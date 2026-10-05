"""Run a built preview ISO with installed QEMU, no network and no host disks."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

from qualify_boot import firmware_paths

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', type=Path, default=ROOT / 'dist/UltraWide-Appliance-preview-x86_64.iso')
    parser.add_argument('--memory', type=int, default=2048, help='Guest RAM in MiB (512-32768)')
    parser.add_argument('--cpus', type=int, default=2, help='Guest virtual CPUs (1-16)')
    parser.add_argument('--uefi', action='store_true', help='Use QEMU edk2 instead of legacy BIOS')
    parser.add_argument('--firmware-dir', type=Path, help='QEMU share directory; required with --uefi')
    args = parser.parse_args(argv)
    if not 512 <= args.memory <= 32768 or not 1 <= args.cpus <= 16:
        parser.error('Memory or CPU allocation is outside the declared preview bounds')
    if not args.iso.is_file():
        parser.error('The ISO is missing; build it first')
    if args.uefi and not args.firmware_dir:
        parser.error('--uefi requires --firmware-dir with QEMU edk2 files')
    executable = shutil.which('qemu-system-x86_64')
    if not executable:
        parser.error('Install QEMU separately and make qemu-system-x86_64 available on PATH')
    command = [executable, '-machine', 'q35', '-accel', 'tcg', '-cpu', 'max',
               '-m', str(args.memory), '-smp', str(args.cpus), '-display', 'none',
               '-chardev', 'stdio,id=console,mux=on,signal=off', '-serial', 'chardev:console',
               '-monitor', 'none', '-no-reboot', '-nic', 'none',
               '-boot', 'd', '-cdrom', str(args.iso.resolve())]
    if args.firmware_dir:
        command += ['-L', str(args.firmware_dir.resolve())]
    print('Unprivileged console, volatile state, no host disks or network. Ctrl-A then X exits QEMU.', flush=True)
    with tempfile.TemporaryDirectory(prefix='ultrawide-firmware-') as directory:
        if args.uefi:
            code, template = firmware_paths(args.firmware_dir)
            variables = Path(directory) / 'vars.fd'
            shutil.copyfile(template, variables)
            command += ['-drive', 'if=pflash,format=raw,readonly=on,file=' + str(code.resolve()),
                        '-drive', 'if=pflash,format=raw,file=' + str(variables)]
        return subprocess.call(command)


if __name__ == '__main__':
    raise SystemExit(main())
