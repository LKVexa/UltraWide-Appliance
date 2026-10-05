"""Repository integrity, quota, path and publication boundaries without large files."""

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ultrawide import repository
from ultrawide.repository import Repository, RepositoryBusyError, RepositoryError


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'objects'
        self.store = Repository(self.root)

    def test_roundtrip_empty_and_binary_objects_with_verified_metadata(self):
        values = (b'', b'\x00\xff\x10arbitrary bytes')
        identities = [self.store.put(value) for value in values]
        self.assertEqual([self.store.get(digest) for digest in identities], list(values))
        self.assertEqual(self.store.list(), sorted(
            [{'sha256': hashlib.sha256(value).hexdigest(), 'bytes': len(value)} for value in values],
            key=lambda row: row['sha256']))

    def test_duplicate_put_does_not_replace_file(self):
        digest = self.store.put(b'unchanged')
        path = self.root / digest
        before = path.stat()
        self.assertEqual(self.store.put(b'unchanged'), digest)
        after = path.stat()
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
        self.assertEqual(len(self.store.list()), 1)

    def test_tampered_object_is_not_returned_or_silently_repaired(self):
        digest = self.store.put(b'original')
        path = self.root / digest
        path.write_bytes(b'corrupt!')
        for operation in (lambda: self.store.get(digest), self.store.list,
                          lambda: self.store.put(b'original')):
            with self.assertRaisesRegex(RepositoryError, 'verification'):
                operation()
        self.assertEqual(path.read_bytes(), b'corrupt!')

    def test_invalid_ids_and_unrelated_entries_fail_closed(self):
        for identity in ('../outside', '/etc/passwd', 'A' * 64, '0' * 63, None):
            with self.subTest(identity=identity), self.assertRaises(RepositoryError):
                self.store.get(identity)
        with self.assertRaisesRegex(RepositoryError, 'does not exist'):
            self.store.get('0' * 64)
        (self.root / 'unrelated.txt').write_text('preserve this', encoding='utf-8')
        with self.assertRaises(RepositoryError):
            self.store.put(b'new')
        self.assertEqual((self.root / 'unrelated.txt').read_text(), 'preserve this')

    def test_quotas_apply_to_unique_objects_and_duplicate_puts_remain_valid(self):
        with patch.object(repository, 'MAX_OBJECT_BYTES', 4), \
             patch.object(repository, 'MAX_TOTAL_BYTES', 6), \
             patch.object(repository, 'MAX_OBJECTS', 2):
            first = self.store.put(b'1234')
            with self.assertRaises(RepositoryError):
                self.store.put(b'12345')
            with self.assertRaises(RepositoryError):
                self.store.put(b'abc')
            self.store.put(b'ab')
            self.assertEqual(self.store.put(b'1234'), first)
            with self.assertRaises(RepositoryError):
                self.store.put(b'')
            self.assertEqual(len(self.store.list()), 2)

    def test_externally_added_objects_cannot_bypass_store_quotas(self):
        for value in (b'first', b'second'):
            (self.root / hashlib.sha256(value).hexdigest()).write_bytes(value)
        for limit, value in (('MAX_OBJECTS', 1), ('MAX_TOTAL_BYTES', 10), ('MAX_OBJECT_BYTES', 4)):
            with self.subTest(limit=limit), patch.object(repository, limit, value), self.assertRaises(RepositoryError):
                self.store.list()

    def test_lock_is_exclusive_and_existing_lock_is_never_removed(self):
        lock = self.root / repository.LOCK_NAME
        lock.write_bytes(b'existing owner')
        for operation in (self.store.list, lambda: self.store.put(b'new')):
            with self.assertRaises(RepositoryBusyError):
                operation()
        self.assertEqual(lock.read_bytes(), b'existing owner')

    def test_failed_publication_removes_own_temporary_file_but_preserves_destination(self):
        value = b'completed bytes'
        digest = hashlib.sha256(value).hexdigest()
        def competing_file(source, destination, **kwargs):
            self.assertEqual(Path(source).read_bytes(), value)
            Path(destination).write_bytes(b'other writer')
            raise FileExistsError('competing writer')
        with patch.object(repository.os, 'link', side_effect=competing_file), self.assertRaises(RepositoryError):
            self.store.put(value)
        self.assertEqual((self.root / digest).read_bytes(), b'other writer')
        self.assertEqual([path.name for path in self.root.iterdir()], [digest])

    def test_publication_failure_does_not_leave_partial_object(self):
        with patch.object(repository.os, 'link', side_effect=OSError('unsupported filesystem')), self.assertRaises(OSError):
            self.store.put(b'new')
        self.assertEqual(list(self.root.iterdir()), [])

    def test_non_bytes_and_traversal_roots_are_rejected(self):
        for value in ('text', bytearray(b'bytes'), None):
            with self.subTest(value=value), self.assertRaises(RepositoryError):
                self.store.put(value)
        with self.assertRaises(RepositoryError):
            Repository(self.root / '..' / 'other')

    def test_symbolic_link_root_and_object_are_rejected_when_supported(self):
        outside = Path(self.temporary.name) / 'outside'
        outside.mkdir()
        linked = Path(self.temporary.name) / 'linked'
        try:
            linked.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('Host does not permit creating symbolic links')
        with self.assertRaises(RepositoryError):
            Repository(linked / 'child')
        content = outside / 'content'
        content.write_bytes(b'protected')
        digest = hashlib.sha256(b'protected').hexdigest()
        (self.root / digest).symlink_to(content)
        with self.assertRaises(RepositoryError):
            self.store.get(digest)
        self.assertEqual(content.read_bytes(), b'protected')

    def test_hard_linked_object_is_rejected(self):
        outside = Path(self.temporary.name) / 'outside'
        outside.write_bytes(b'protected')
        digest = hashlib.sha256(b'protected').hexdigest()
        try:
            os.link(outside, self.root / digest)
        except OSError:
            self.skipTest('Host filesystem does not support hard links')
        with self.assertRaisesRegex(RepositoryError, 'one link'):
            self.store.get(digest)


if __name__ == '__main__':
    unittest.main()
