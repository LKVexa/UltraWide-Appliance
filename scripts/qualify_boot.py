"""Boot one ISO in isolated QEMU and record observed guest qualification.

No disk, shared folder, or network adapter is attached. Optional UEFI variable
storage is a fresh temporary file. A successful ISO build alone is not a pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def firmware_paths(directory):
    directory = Path(directory)
    code = directory / 'edk2-x86_64-code.fd'
    variables = directory / 'edk2-i386-vars.fd'
    for item in (code, variables):
        if item.is_symlink() or not item.is_file():
            raise ValueError('Expected QEMU edk2 firmware file: ' + str(item))
    return code, variables


def qualify(iso, executable, output, *, firmware='bios', firmware_dir=None, timeout=180):
    iso, output = Path(iso).resolve(), Path(output)
    if not iso.is_file() or not 30 <= timeout <= 900:
        raise ValueError('Missing ISO or timeout outside 30..900 seconds')
    if firmware not in ('bios', 'uefi'):
        raise ValueError('Firmware must be bios or uefi')
    if output.exists():
        raise FileExistsError('Choose a new qualification directory')
    if firmware == 'uefi':
        code, template = firmware_paths(firmware_dir)
    output.mkdir(parents=True)
    serial_path = output / 'serial.log'
    receipt = {
        'schema': 'ultrawide.boot-qualification.v2', 'firmware': firmware,
        'iso_name': iso.name, 'iso_bytes': iso.stat().st_size,
        'guest_memory_mib': 2048, 'virtual_cpus': 2, 'network_interface': False,
        'host_disks_attached': False, 'shared_folders': False,
        'status': 'FAIL', 'checks': {},
    }
    with iso.open('rb') as stream:
        receipt['iso_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
    port = free_port()
    command = [str(executable), '-machine', 'q35', '-accel', 'tcg', '-cpu', 'max',
               '-m', '2048', '-smp', '2', '-display', 'none', '-monitor', 'none',
               '-chardev', f'socket,id=serial0,host=127.0.0.1,port={port},server=on,wait=off,logfile={serial_path.resolve()}',
               '-serial', 'chardev:serial0', '-no-reboot', '-nic', 'none',
               '-boot', 'd', '-cdrom', str(iso)]
    if firmware_dir:
        command += ['-L', str(Path(firmware_dir).resolve())]
    with tempfile.TemporaryDirectory(prefix='ultrawide-firmware-') as temp:
        if firmware == 'uefi':
            variables = Path(temp) / 'vars.fd'
            shutil.copyfile(template, variables)
            command += ['-drive', f'if=pflash,format=raw,readonly=on,file={code.resolve()}',
                        '-drive', f'if=pflash,format=raw,file={variables}']
            receipt['firmware_code_sha256'] = hashlib.sha256(code.read_bytes()).hexdigest()
        started = time.monotonic()
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        with (output / 'qemu.stdout').open('wb') as stdout, (output / 'qemu.stderr').open('wb') as stderr:
            child = subprocess.Popen(command, stdout=stdout, stderr=stderr, creationflags=flags)
            try:
                while time.monotonic() - started < timeout:
                    if serial_path.exists() and serial_path.stat().st_size > 8 * 1024 * 1024:
                        raise ValueError('Guest serial output exceeds qualification limit')
                    content = serial_path.read_text('utf-8', errors='replace') if serial_path.exists() else ''
                    if 'ULTRAWIDE_BOOT_SELF_TEST_END' in content:
                        region = content.split('ULTRAWIDE_BOOT_SELF_TEST_BEGIN', 1)[1].split('ULTRAWIDE_BOOT_SELF_TEST_END', 1)[0]
                        result = next(json.loads(line.strip()) for line in region.splitlines() if line.strip().startswith('{'))
                        receipt['self_test'] = result
                        receipt['checks']['runtime_self_test'] = result.get('status') == 'PASS'
                        receipt['checks']['wide_word'] = result.get('word_bits') == 1048576
                        expected_firmware = 'UEFI64' if firmware == 'uefi' else 'BIOS'
                        receipt['checks']['firmware'] = 'ULTRAWIDE_FIRMWARE=' + expected_firmware in content
                        receipt['checks']['volatile_state'] = 'ULTRAWIDE_STATE_MODE=volatile_RAM' in content
                        receipt['kernel'] = content.split('ULTRAWIDE_GUEST_UNAME=', 1)[1].splitlines()[0].strip()
                        # Commands run through the actual automatically selected console account.
                        with socket.create_connection(('127.0.0.1', port), timeout=5) as console:
                            console.sendall(b'\n')
                            time.sleep(1)
                            console.sendall(b"printf '\\nUWA_UID=%s\\n' \"$(id -u)\"; test ! -r /etc/shadow && echo UWA_SHADOW_BLOCKED; echo UWA_CONSOLE_PROBE_DONE\n")
                            for _ in range(15):
                                time.sleep(1)
                                content = serial_path.read_text('utf-8', errors='replace').replace('\r', '')
                                if '\nUWA_CONSOLE_PROBE_DONE\n' in content:
                                    uid_lines = [line[8:] for line in content.splitlines() if line.startswith('UWA_UID=')]
                                    receipt['console_uid'] = int(uid_lines[-1]) if uid_lines else -1
                                    receipt['checks']['unprivileged_console'] = receipt['console_uid'] >= 1000
                                    receipt['checks']['shadow_unreadable'] = '\nUWA_SHADOW_BLOCKED\n' in content
                                    break
                            else:
                                receipt['checks']['console_probe'] = False
                        receipt['status'] = 'PASS' if all(receipt['checks'].values()) else 'FAIL'
                        break
                    if child.poll() is not None:
                        receipt['error'] = 'QEMU exited before the guest completed its self-test'
                        break
                    time.sleep(1)
                else:
                    receipt['error'] = 'Guest qualification timeout'
            except Exception as exc:
                receipt['error'] = type(exc).__name__ + ': ' + str(exc)
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=10)
                receipt['qemu_stopped'] = child.poll() is not None
                receipt['elapsed_seconds'] = round(time.monotonic() - started, 2)
    (output / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--qemu', default=shutil.which('qemu-system-x86_64'))
    parser.add_argument('--firmware', choices=('bios', 'uefi'), default='bios')
    parser.add_argument('--firmware-dir', type=Path, help='QEMU share directory (required for UEFI)')
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args(argv)
    if not args.qemu or (args.firmware == 'uefi' and not args.firmware_dir):
        parser.error('QEMU is required; UEFI also requires --firmware-dir')
    result = qualify(args.iso, args.qemu, args.output, firmware=args.firmware,
                     firmware_dir=args.firmware_dir, timeout=args.timeout)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
