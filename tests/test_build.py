"""Build boundaries exercised with temporary sources; no downloads or ISO tools."""

import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_iso import check_overlay, make_overlay, source_files
from fetch_inputs import load_lock, verify_file


def write_fixture(root, name, data='fixture\n'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding='utf-8')
    return path


def source_fixture(root):
    for name in ('src/ultrawide/__main__.py', 'src/ultrawide/word.py',
                 'examples/example.uwa', 'appliance/boot.start', 'appliance/ultrawide',
                        'appliance/console-login', 'LICENSE', 'README.md', 'THIRD-PARTY-NOTICES.md'):
        write_fixture(root, name)


def input_fixture():
    prefix = 'https://dl-cdn.alpinelinux.org/alpine/v3.24/'
    return {
        'schema': 'ultrawide.build-inputs.v1', 'architecture': 'x86_64',
        'alpine_branch': 'v3.24', 'source_date_epoch': 1,
        'base_iso': {'filename': 'fixture.iso', 'size': 1, 'sha256': 'a' * 64,
                     'url': prefix + 'releases/x86_64/fixture.iso'},
        'packages': [{'filename': 'fixture.apk', 'size': 1, 'sha256': 'b' * 64,
                      'repo': 'main', 'url': prefix + 'main/x86_64/fixture.apk'}],
    }


def archive_fixture(extra):
    """Make a tiny archive that has required entry points plus the tested member."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w:gz') as archive:
        for name in ('etc/local.d/ultrawide.start', 'usr/local/bin/ultrawide',
                     'opt/ultrawide/src/ultrawide/__main__.py'):
            archive.addfile(tarfile.TarInfo(name), io.BytesIO())
        archive.addfile(extra, io.BytesIO() if extra.isfile() else None)
    return raw.getvalue()


class BuildBoundaryTests(unittest.TestCase):
    def test_source_selection_excludes_unrelated_files_caches_and_default_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_fixture(root)
            excluded = ('src/ultrawide/__pycache__/cached.py', 'src/ultrawide/unrelated.bin',
                        'examples/unrelated.iso', '.cache/download.py', 'src/other/tool.py',
                        'appliance/application.start', 'appliance/application.start.example')
            for name in excluded:
                write_fixture(root, name, 'must-not-be-packaged\n')
            selected = source_files(root)
            paths = {path.relative_to(root).as_posix() for path, _, _ in selected}
            self.assertTrue({'src/ultrawide/__main__.py', 'examples/example.uwa',
                             'THIRD-PARTY-NOTICES.md'} <= paths)
            self.assertTrue(paths.isdisjoint(excluded))
            blob, _ = make_overlay(input_fixture(), root=root)
            with tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz') as archive:
                for member in archive.getmembers():
                    if member.isfile():
                        self.assertNotIn(b'must-not-be-packaged', archive.extractfile(member).read())

    def test_explicit_hook_request_rejects_missing_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_fixture(root)
            with self.assertRaisesRegex(ValueError, 'application.start'):
                source_files(root, include_hook=True)

    def test_present_hook_is_admitted_only_with_explicit_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_fixture(root)
            hook = write_fixture(root, 'appliance/application.start', '#!/bin/sh\nexit 0\n')
            self.assertFalse(any(path == hook for path, _, _ in source_files(root)))
            admitted = [row for row in source_files(root, include_hook=True) if row[0] == hook]
            self.assertEqual(admitted, [(hook, 'opt/ultrawide/appliance/application.start', 0o755)])

    def test_source_overlay_check_does_not_claim_iso_or_boot_qualification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_fixture(root)
            blob, _ = make_overlay(input_fixture(), root=root)
            report = check_overlay(blob, includes_packages=False)
            self.assertEqual(report['status'], 'PASS')
            self.assertFalse(report['packages_included'])
            self.assertFalse(report['iso_built'])
            self.assertFalse(report['boot_tested'])

    def test_overlay_rejects_absolute_traversal_and_duplicate_members(self):
        for name in ('/tmp/escape', 'opt/../../tmp/escape', 'usr/local/bin/ultrawide'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                check_overlay(archive_fixture(tarfile.TarInfo(name)), includes_packages=False)

    def test_overlay_rejects_unsafe_links_and_special_members(self):
        cases = [
            ('opt/ultrawide/link', tarfile.SYMTYPE, '/etc/passwd'),
            ('etc/runlevels/default/escape', tarfile.SYMTYPE, '/etc/init.d/../../tmp/escape'),
            ('opt/ultrawide/link', tarfile.LNKTYPE, 'etc/hostname'),
            ('opt/ultrawide/device', tarfile.CHRTYPE, ''),
        ]
        for name, kind, target in cases:
            member = tarfile.TarInfo(name)
            member.type, member.linkname = kind, target
            with self.subTest(name=name, kind=kind), self.assertRaises(ValueError):
                check_overlay(archive_fixture(member), includes_packages=False)

    def test_overlay_rejects_missing_runtime_entry_points(self):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w:gz'):
            pass
        with self.assertRaisesRegex(ValueError, 'entry point'):
            check_overlay(raw.getvalue(), includes_packages=False)

    def test_lock_accepts_valid_pins_and_rejects_url_filename_and_digest_changes(self):
        valid = input_fixture()
        mutations = [
            ('url', 'http://dl-cdn.alpinelinux.org/alpine/v3.24/main/x86_64/fixture.apk'),
            ('url', valid['packages'][0]['url'] + '?redirect=1'),
            ('url', valid['packages'][0]['url'].replace('dl-cdn.alpinelinux.org', 'example.invalid')),
            ('filename', '../fixture.apk'), ('filename', 'folder\\fixture.apk'),
            ('sha256', '0' * 63), ('sha256', 'z' * 64), ('size', True),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'inputs.json'
            path.write_text(json.dumps(valid), encoding='utf-8')
            self.assertEqual(load_lock(path), valid)
            for field, value in mutations:
                changed = copy.deepcopy(valid)
                changed['packages'][0][field] = value
                path.write_text(json.dumps(changed), encoding='utf-8')
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    load_lock(path)

    def test_lock_rejects_duplicate_names_including_base_package_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'inputs.json'
            for collide_with_base in (False, True):
                changed = input_fixture()
                if collide_with_base:
                    changed['packages'][0]['filename'] = changed['base_iso']['filename']
                else:
                    changed['packages'].append(copy.deepcopy(changed['packages'][0]))
                path.write_text(json.dumps(changed), encoding='utf-8')
                with self.subTest(base=collide_with_base), self.assertRaisesRegex(ValueError, 'duplicate'):
                    load_lock(path)

    def test_cached_input_requires_both_exact_size_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.apk'
            data = b'original'
            path.write_bytes(data)
            row = {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            verify_file(path, row)
            for changed in (b'changed!', data + b'!'):
                path.write_bytes(changed)
                with self.subTest(size=len(changed)), self.assertRaisesRegex(ValueError, 'mismatch'):
                    verify_file(path, row)


if __name__ == '__main__':
    unittest.main()
