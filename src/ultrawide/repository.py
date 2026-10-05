"""Bounded local content-addressed storage, independent of the original HERMIT.

The root is caller-owned storage, not a sandbox against an adversarial host.
Cooperating callers serialize operations with an exclusive, fail-fast lock.
"""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import stat
import uuid

MAX_OBJECT_BYTES = 80 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_OBJECTS = 64
LOCK_NAME = '.ultrawide-repository.lock'
_DIGEST = re.compile(r'[0-9a-f]{64}')


class RepositoryError(ValueError):
    """Invalid repository data, unsafe storage path, or exceeded storage bound."""


class RepositoryBusyError(RepositoryError):
    """Another operation or an interrupted operation owns the repository lock."""


def _identity(info):
    return info.st_dev, info.st_ino


def _reject_redirect(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise RepositoryError('Repository paths must not contain symbolic links or reparse points')
    return info


def _check_ancestors(path):
    for part in reversed((path, *path.parents)):
        if part.exists() or part.is_symlink():
            if not stat.S_ISDIR(_reject_redirect(part).st_mode):
                raise RepositoryError('Repository root and ancestors must be directories')


def _digest_name(value):
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise RepositoryError('Object ID must be exactly 64 lowercase hexadecimal characters')
    return value


class Repository:
    """Store opaque bytes by SHA-256; no execution or automatic archive extraction.

    ``list()`` returns sorted ``{'sha256': str, 'bytes': int}`` records after
    verifying every object's content. Empty objects are permitted. Existing
    objects are immutable and duplicate puts return the existing identity.
    """

    def __init__(self, root):
        path = Path(root)
        if '..' in path.parts:
            raise RepositoryError('Parent traversal is not permitted in a repository root')
        self.root = path.absolute()
        _check_ancestors(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        _check_ancestors(self.root)
        self._root_identity = _identity(self.root.lstat())

    def _check_root(self):
        _check_ancestors(self.root)
        if _identity(self.root.lstat()) != self._root_identity:
            raise RepositoryError('Repository root changed after it was opened')

    @contextmanager
    def _locked(self):
        self._check_root()
        path = self.root / LOCK_NAME
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0)
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise RepositoryBusyError('Repository is locked; inspect interrupted work before retrying') from exc
        identity = _identity(os.fstat(fd))
        try:
            self._check_root()
            yield
        finally:
            os.close(fd)
            # Never delete a replacement lock supplied by another actor.
            if path.exists() and _identity(path.lstat()) == identity:
                path.unlink()

    def _read(self, digest, *, keep_bytes):
        path = self.root / digest
        before = _reject_redirect(path)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise RepositoryError('Objects must be regular files with one link')
        if before.st_size > MAX_OBJECT_BYTES:
            raise RepositoryError('Object exceeds the per-object size limit')
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
        parts, total, actual = [], 0, hashlib.sha256()
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if _identity(opened) != _identity(before) or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise RepositoryError('Object changed while it was opened')
            while block := stream.read(min(1024 * 1024, MAX_OBJECT_BYTES - total + 1)):
                total += len(block)
                if total > MAX_OBJECT_BYTES:
                    raise RepositoryError('Object exceeds the per-object size limit')
                actual.update(block)
                if keep_bytes:
                    parts.append(block)
            after = os.fstat(stream.fileno())
        if total != before.st_size or after.st_size != total or actual.hexdigest() != digest:
            raise RepositoryError('Stored object failed its size or SHA-256 verification')
        return b''.join(parts) if keep_bytes else total

    def _inventory(self):
        records, total = [], 0
        with os.scandir(self.root) as entries:
            for entry in entries:
                if entry.name == LOCK_NAME:
                    continue
                digest = _digest_name(entry.name)
                if len(records) >= MAX_OBJECTS:
                    raise RepositoryError('Repository exceeds the object count limit')
                size = self._read(digest, keep_bytes=False)
                total += size
                if total > MAX_TOTAL_BYTES:
                    raise RepositoryError('Repository exceeds the total size limit')
                records.append({'sha256': digest, 'bytes': size})
        return sorted(records, key=lambda row: row['sha256'])

    def list(self):
        with self._locked():
            return self._inventory()

    def get(self, digest):
        digest = _digest_name(digest)
        with self._locked():
            # Validate storage quotas and reject unrelated/unsafe entries first.
            records = self._inventory()
            if not any(row['sha256'] == digest for row in records):
                raise RepositoryError('Object does not exist in this repository')
            return self._read(digest, keep_bytes=True)

    def put(self, data):
        if type(data) is not bytes:
            raise RepositoryError('Object content must be bytes')
        if len(data) > MAX_OBJECT_BYTES:
            raise RepositoryError('Object exceeds the per-object size limit')
        digest = hashlib.sha256(data).hexdigest()
        with self._locked():
            records = self._inventory()
            if any(row['sha256'] == digest for row in records):
                return digest
            if len(records) >= MAX_OBJECTS or sum(row['bytes'] for row in records) + len(data) > MAX_TOTAL_BYTES:
                raise RepositoryError('Adding this object would exceed repository storage limits')
            temporary = self.root / ('.object-' + uuid.uuid4().hex + '.tmp')
            identity = None
            try:
                with temporary.open('xb') as stream:
                    identity = _identity(os.fstat(stream.fileno()))
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._check_root()
                # link() atomically publishes a completed file and never replaces
                # an existing destination. Filesystems without hard links fail.
                os.link(temporary, self.root / digest, follow_symlinks=False)
            except FileExistsError as exc:
                raise RepositoryError('Object destination appeared during publication; no file was replaced') from exc
            finally:
                if identity is not None and temporary.exists() and _identity(temporary.lstat()) == identity:
                    temporary.unlink()
        return digest
