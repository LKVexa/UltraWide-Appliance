"""Run a built preview ISO with installed QEMU, no network and no host disks."""
import argparse
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', type=Path, default=ROOT / 'dist/UltraWide-Appliance-preview-x86_64.iso')
    parser.add_argument('--memory', type=int, default=2048, help='Guest RAM in MiB (512-32768)')
    parser.add_argument('--cpus', type=int, default=2, help='Guest virtual CPUs (1-16)')
    args = parser.parse_args(argv)
    if not 512 <= args.memory <= 32768 or not 1 <= args.cpus <= 16:
        parser.error('Memory or CPU allocation is outside the declared preview bounds')
    if not args.iso.is_file():
        parser.error('The ISO is missing; build it first')
    executable = shutil.which('qemu-system-x86_64')
    if not executable:
        parser.error('Install QEMU separately and make qemu-system-x86_64 available on PATH')
    command = [executable, '-machine', 'q35', '-accel', 'tcg', '-cpu', 'max',
               '-m', str(args.memory), '-smp', str(args.cpus), '-display', 'none',
               '-serial', 'stdio', '-monitor', 'none', '-no-reboot', '-nic', 'none',
               '-boot', 'd', '-cdrom', str(args.iso.resolve())]
    print('Starting a console-only preview. Guest state is volatile; no host disks or network are attached.', flush=True)
    return subprocess.call(command)


if __name__ == '__main__':
    raise SystemExit(main())
