"""Create a fresh diskless preview ISO from pinned public Alpine inputs.

Only the virtual runtime, examples, appliance hooks and project notices enter
the overlay. This script does not download anything or modify attached disks.
Use --check for a source-overlay preflight without downloading/building an ISO.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from fetch_inputs import DEFAULT_CACHE, ROOT, load_lock, sha256, verify_inputs

OUTPUT_NAME = 'UltraWide-Appliance-preview-x86_64.iso'
SERVICES = {
    'sysinit': ('devfs', 'dmesg', 'mdev', 'modloop'),
    'boot': ('modules', 'sysctl', 'hostname', 'bootmisc', 'syslog'),
    'default': ('local',),
    'shutdown': ('killprocs', 'mount-ro'),
}


def source_files(root=ROOT, include_hook=False):
    """Explicit source roots avoid accidentally packaging caches or private data."""
    selected = []
    for folder, suffixes in (('src/ultrawide', {'.py'}),
                             ('examples', {'.uwa', '.lctlw', '.lctlb', '.b1048576asm', '.brimg', '.brir', '.brw', '.b1048576', '.uwabundle'})):
        base = root / folder
        if not base.is_dir() or base.is_symlink():
            raise ValueError('Required regular source directory is missing: ' + folder)
        for path in sorted(base.rglob('*')):
            if path.is_symlink():
                raise ValueError('Source symlinks are not admitted: ' + str(path))
            if path.is_file() and path.suffix in suffixes and '__pycache__' not in path.parts:
                selected.append((path, 'opt/ultrawide/' + path.relative_to(root).as_posix(), 0o644))
    for name in ('LICENSE', 'README.md', 'THIRD-PARTY-NOTICES.md',
                 'docs/licenses/SUPPLIED-PREVIEW-NOTICE.md', 'src/ultrawide/shs/PROVENANCE.json',
                 'docs/BUILD.md', 'docs/BRIM.md', 'docs/SHS-AUDIT.md', 'docs/SHS-FORMAT.md',
                 'docs/WORD-CHAIN.md', 'docs/REPOSITORY.md', 'docs/FORMAT.md', 'docs/images/brim-word.png'):
        path = root / name
        if path.is_file():
            if path.is_symlink():
                raise ValueError('Project notice must be a regular file: ' + name)
            selected.append((path, 'opt/ultrawide/' + name, 0o644))
    for source, target, mode in (
        ('appliance/boot.start', 'etc/local.d/ultrawide.start', 0o755),
        ('appliance/ultrawide', 'usr/local/bin/ultrawide', 0o755),
        ('appliance/console-login', 'usr/local/bin/ultrawide-login', 0o755),
    ):
        path = root / source
        if path.is_symlink() or not path.is_file():
            raise ValueError('Required appliance script is missing: ' + source)
        selected.append((path, target, mode))
    if include_hook:
        hook = root / 'appliance/application.start'
        if hook.is_symlink() or not hook.is_file():
            raise ValueError('--include-application-hook requires appliance/application.start')
        selected.append((hook, 'opt/ultrawide/appliance/application.start', 0o755))
    if not (root / 'src/ultrawide/__main__.py').is_file():
        raise ValueError('The generic ultrawide CLI entry point is missing')
    return selected


def make_overlay(lock, cache=None, *, root=ROOT, include_hook=False):
    """Return deterministic overlay bytes and source identities; no host writes."""
    epoch = lock['source_date_epoch']
    if type(epoch) is not int or epoch < 0:
        raise ValueError('Invalid source_date_epoch')
    raw = io.BytesIO()
    inventory = []
    with gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=epoch) as compressed:
        with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as archive:
            def add(name, data, mode=0o644):
                if name.startswith('/') or '..' in PurePosixPath(name).parts:
                    raise ValueError('Noncanonical overlay path')
                body = data.encode('utf-8') if isinstance(data, str) else data
                item = tarfile.TarInfo(name)
                item.size = len(body)
                item.mode = mode
                item.mtime = epoch
                item.uid = item.gid = 0
                archive.addfile(item, io.BytesIO(body))
                inventory.append({'path': name, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()})

            add('etc/hostname', 'ultrawide-preview\n')
            # Python is installed later from explicit signed local files. It
            # must not be requested from unavailable mutable repository indexes.
            add('etc/apk/world', 'alpine-base\n')
            add('etc/motd', 'UltraWide Appliance 0.2 Product Preview\nBASIC-1048576 virtual machine, LCTL-WIDE and BRIM tooling.\nGuest state is volatile. Console runs as unprivileged ultrawide.\n')
            add('etc/inittab', '::sysinit:/sbin/openrc sysinit\n::sysinit:/sbin/openrc boot\n::wait:/sbin/openrc default\n'
                'tty1::respawn:/sbin/getty -n -l /usr/local/bin/ultrawide-login 38400 tty1\n'
                'ttyS0::respawn:/sbin/getty -n -l /usr/local/bin/ultrawide-login 115200 ttyS0 vt100\n'
                '::ctrlaltdel:/sbin/reboot\n::shutdown:/sbin/openrc shutdown\n')
            add('etc/profile.d/ultrawide.sh', 'export PYTHONPATH=/opt/ultrawide/src\nexport PYTHONDONTWRITEBYTECODE=1\n')
            for level, services in SERVICES.items():
                for service in services:
                    item = tarfile.TarInfo(f'etc/runlevels/{level}/{service}')
                    item.type = tarfile.SYMTYPE
                    item.linkname = '/etc/init.d/' + service
                    item.mode = 0o777
                    item.mtime = epoch
                    archive.addfile(item)
            for path, target, mode in source_files(root, include_hook):
                add(target, path.read_bytes(), mode)
            if cache is not None:
                for row in lock['packages']:
                    add('opt/ultrawide/packages/' + row['filename'], (Path(cache) / row['filename']).read_bytes())
            add('opt/ultrawide/source-inventory.json', json.dumps(inventory, indent=2) + '\n')
    return raw.getvalue(), inventory


def check_overlay(blob, *, includes_packages):
    expected_links = {
        f'etc/runlevels/{level}/{service}': '/etc/init.d/' + service
        for level, services in SERVICES.items() for service in services
    }
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz') as archive:
        entries = archive.getmembers()
        names = [entry.name for entry in entries]
        if len(names) != len(set(names)):
            raise ValueError('Duplicate overlay members')
        for entry in entries:
            if (entry.name.startswith('/') or '..' in PurePosixPath(entry.name).parts
                    or PurePosixPath(entry.name).as_posix() != entry.name):
                raise ValueError('Unsafe overlay member')
            if entry.issym() and expected_links.get(entry.name) != entry.linkname:
                raise ValueError('Unexpected overlay symlink')
            if not (entry.isfile() or entry.issym()):
                raise ValueError('Unexpected overlay member type')
        required = {'etc/local.d/ultrawide.start', 'usr/local/bin/ultrawide', 'opt/ultrawide/src/ultrawide/__main__.py'}
        if not required <= set(names):
            raise ValueError('Overlay is missing a boot/runtime entry point')
        return {'status': 'PASS', 'members': len(entries), 'overlay_sha256': hashlib.sha256(blob).hexdigest(),
                'packages_included': includes_packages, 'iso_built': False, 'boot_tested': False}


def build_iso(cache, output, *, include_hook=False):
    try:
        import pycdlib
    except ImportError as exc:
        raise RuntimeError('Install the hashed scripts/requirements-build.txt dependencies first') from exc
    lock = load_lock()
    verified = verify_inputs(cache, lock)
    overlay, inventory = make_overlay(lock, cache, include_hook=include_hook)
    overlay_check = check_overlay(overlay, includes_packages=True)
    output = Path(output)
    if any(path.exists() or path.is_symlink() for path in (
        output, output.with_suffix('.iso.sha256'), output.with_suffix('.build.json')
    )):
        raise FileExistsError('Choose a new output path; existing ISO files or build receipts are never replaced')
    output.parent.mkdir(parents=True, exist_ok=True)
    original = pycdlib.PyCdlib()
    image = pycdlib.PyCdlib()
    original.open(str(Path(cache) / lock['base_iso']['filename']))
    image.new(interchange_level=4, rock_ridge='1.09', joliet=3, vol_ident='UWA_PREVIEW')
    buffers = []
    directories = {'/'}
    try:
        def directory(path):
            if path in directories:
                return
            directory(path.rsplit('/', 1)[0] or '/')
            image.add_directory(iso_path=path.upper(), rr_name=path.rsplit('/', 1)[1], joliet_path=path)
            directories.add(path)

        def add(path, data):
            directory(path.rsplit('/', 1)[0] or '/')
            buffer = io.BytesIO(data)
            buffers.append(buffer)
            image.add_fp(buffer, len(data), iso_path=path.upper() + ';1', rr_name=path.rsplit('/', 1)[1], joliet_path=path)

        replaced = {'/boot/syslinux/boot.cat', '/boot/syslinux/syslinux.cfg', '/boot/grub/grub.cfg'}
        for base, _, files in original.walk(rr_path='/'):
            for name in files:
                path = base.rstrip('/') + '/' + name
                if path in replaced:
                    continue
                data = io.BytesIO()
                original.get_file_from_iso_fp(data, rr_path=path)
                add(path, data.getvalue())
        arguments = 'modules=loop,squashfs,sd-mod,usb-storage console=tty0 console=ttyS0,115200 quiet'
        add('/boot/syslinux/syslinux.cfg', ('SERIAL 0 115200\nTIMEOUT 10\nPROMPT 0\nDEFAULT ultrawide\nLABEL ultrawide\nKERNEL /boot/vmlinuz-virt\nINITRD /boot/initramfs-virt\nAPPEND ' + arguments + '\n').encode())
        add('/boot/grub/grub.cfg', ('set timeout=1\nmenuentry "UltraWide Appliance Preview" {\n linux /boot/vmlinuz-virt ' + arguments + '\n initrd /boot/initramfs-virt\n}\n').encode())
        add('/ultrawide.apkovl.tar.gz', overlay)
        image.add_eltorito('/BOOT/SYSLINUX/ISOLINUX.BIN;1', bootcatfile='/BOOT/SYSLINUX/BOOT.CAT;1', rr_bootcatname='boot.cat', joliet_bootcatfile='/boot/syslinux/boot.cat', boot_load_size=4, boot_info_table=True)
        image.add_eltorito('/BOOT/GRUB/EFI.IMG;1', platform_id=0xef, efi=True)
        with output.open('xb') as stream:
            image.write_fp(stream)
    finally:
        image.close()
        original.close()
    digest = sha256(output)
    report = {'schema': 'ultrawide.iso-build.v1', 'status': 'ISO_BUILT_BOOT_UNVERIFIED',
              'file': output.name, 'bytes': output.stat().st_size, 'sha256': digest,
              'inputs': verified, 'overlay': overlay_check, 'application_hook_included': include_hook,
              'boot_tested': False, 'source_files': len(inventory),
              'language': 'LCTLC-WIDE/0.1 classical extension; BASIC-1048576 architecture 0.2.0; BRIM/BRPV image binding',
              'console_account': 'ultrawide (unprivileged)', 'root_password_locked': True}
    with output.with_suffix('.iso.sha256').open('x', encoding='utf-8') as stream:
        stream.write(digest + '  ' + output.name + '\n')
    with output.with_suffix('.build.json').open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(report, indent=2) + '\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Validate a source-only overlay; no downloads or ISO creation')
    parser.add_argument('--cache', type=Path, default=DEFAULT_CACHE)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / OUTPUT_NAME)
    parser.add_argument('--include-application-hook', action='store_true')
    args = parser.parse_args(argv)
    if args.check:
        blob, _ = make_overlay(load_lock(), include_hook=args.include_application_hook)
        result = check_overlay(blob, includes_packages=False)
        result['scope'] = 'Source-only archive structure; package installation and boot are unverified'
    else:
        result = build_iso(args.cache, args.output, include_hook=args.include_application_hook)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
