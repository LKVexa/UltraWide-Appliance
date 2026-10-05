# Local content-addressed repository

`ultrawide.repository.Repository` stores explicitly supplied bytes by SHA-256.
It provides a small workflow component inspired by the original HERMIT storage
description. It is an independent preview format, not a port of a complete
HERMIT repository, package manager, job scheduler, or trust service.

The caller chooses the root directory. Each object is a regular file named by
its 64-character lowercase SHA-256. There is no mutable metadata index. The API
does not execute object contents, unpack archives, scan a user's files, or
fetch dependencies. LCTL source, images, bindings, and JSON receipts can all be
stored as opaque bytes; the relevant format verifier must validate them before
use. A matching hash proves content identity, not authorship or authorization.

```python
from ultrawide.repository import Repository

repository = Repository('state/objects')
identity = repository.put(b'Explicitly selected source or receipt bytes')
assert repository.get(identity) == b'Explicitly selected source or receipt bytes'
records = repository.list()  # [{'sha256': identity, 'bytes': 43}]
```

`put(bytes)` returns the digest and is idempotent for an existing verified object.
It never replaces an existing file. `get(digest)` returns verified bytes.
`list()` returns records sorted by digest, with `sha256` and `bytes` fields,
after verifying every object's size and hash. Empty objects are permitted.
IDs that contain paths, uppercase hex, or anything except exactly 64 lowercase
hexadecimal characters are rejected.

## Storage and publication bounds

- At most **80 MiB per object**, **256 MiB total**, and **64 unique objects**.
- All operations check existing storage; unrelated files, corrupted objects,
  directories, symlinks, reparse points, and multiply linked object files fail
  closed. A root containing parent traversal is rejected.
- Cooperating operations use one exclusive, fail-fast lock. A busy repository
  raises `RepositoryBusyError`; no operation waits indefinitely or steals a lock.
- Writes first create an exclusive temporary file in the root, flush and sync
  its complete bytes, then publish through an atomic, no-replace hard link and
  remove the temporary name. A filesystem without hard-link support rejects
  the operation rather than falling back to overwrite-prone publication.
- A failed write removes only its own temporary file and lock. An existing
  destination is preserved, including one that appears during publication.

The root must be controlled by the application owner. Path and file checks
reduce accidental redirection, but this API is not a filesystem sandbox against
a hostile process running as the same host user. The lock coordinates callers
of this API; it is not an operating-system access-control boundary. Atomic
publication does not promise recovery from every power-loss/filesystem failure.

An interrupted process may leave `.ultrawide-repository.lock` or an incomplete
temporary file. The API refuses further work instead of silently deleting
unrecognized data. Stop all users of that repository, inspect the interrupted
operation, and explicitly recover or remove only confirmed temporary state.
There is no automatic eviction or deletion API. A caller can retain a separate
repository for a new bounded run when the existing store is full.

The boot preview's root filesystem is volatile. Stored objects persist only
where the caller has independently arranged persistent storage; the repository
module does not format, partition, attach, or mount disks.
