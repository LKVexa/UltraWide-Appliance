"""Fetch exact public Alpine inputs, checking size and SHA-256 before use.

No packages are installed on the build host. No private indexes or archives
are required. Changed/missing upstream releases fail closed; pins never refresh
automatically. Python 3.12 or newer is required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'config/alpine-lock.json'
DEFAULT_CACHE = ROOT / '.cache/alpine'


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_lock(path=LOCK):
    document = json.loads(Path(path).read_text('utf-8'))
    if document.get('schema') != 'ultrawide.build-inputs.v1' or document.get('architecture') != 'x86_64':
        raise ValueError('Unsupported build-input lock')
    branch = document.get('alpine_branch')
    if not re.fullmatch(r'v[0-9]+\.[0-9]+', branch or ''):
        raise ValueError('Invalid Alpine branch')
    packages = document.get('packages')
    if not isinstance(packages, list) or not 1 <= len(packages) <= 128:
        raise ValueError('Invalid package closure')
    rows = [document['base_iso'], *packages]
    names = set()
    for index, row in enumerate(rows):
        name = row.get('filename', '')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9+_.-]*', name) or name in names:
            raise ValueError('Invalid or duplicate input filename')
        names.add(name)
        if not re.fullmatch(r'[0-9a-f]{64}', row.get('sha256', '')):
            raise ValueError('Invalid SHA-256: ' + name)
        if type(row.get('size')) is not int or not 1 <= row['size'] <= 1024**3:
            raise ValueError('Invalid input size: ' + name)
        segment = 'releases/x86_64' if index == 0 else row.get('repo', '') + '/x86_64'
        if index and row.get('repo') != 'main':
            raise ValueError('This minimal closure admits only Alpine main packages')
        expected = f'https://dl-cdn.alpinelinux.org/alpine/{branch}/{segment}/{name}'
        if row.get('url') != expected:
            raise ValueError('Unexpected input URL: ' + name)
    return document


def verify_file(path, row):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Required regular input file is missing: ' + str(path))
    if path.stat().st_size != row['size'] or sha256(path) != row['sha256']:
        raise ValueError('Input size or SHA-256 mismatch: ' + str(path))


def verify_inputs(cache, lock=None):
    lock = load_lock() if lock is None else lock
    rows = [lock['base_iso'], *lock['packages']]
    for row in rows:
        verify_file(Path(cache) / row['filename'], row)
    return {'status': 'PASS', 'verified_files': len(rows), 'verified_bytes': sum(row['size'] for row in rows)}


def download(cache, row):
    cache = Path(cache)
    target = cache / row['filename']
    if target.exists() or target.is_symlink():
        verify_file(target, row)
        return 'already verified'
    request = urllib.request.Request(row['url'], headers={'User-Agent': 'UltraWide-Appliance-preview-build'})
    temporary = None
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            final = urllib.parse.urlsplit(response.url)
            if final.scheme != 'https' or final.hostname != 'dl-cdn.alpinelinux.org':
                raise ValueError('Unexpected download redirect')
            declared = response.headers.get('Content-Length')
            if declared is not None and int(declared) != row['size']:
                raise ValueError('Unexpected Content-Length: ' + row['filename'])
            digest = hashlib.sha256()
            total = 0
            with tempfile.NamedTemporaryFile(dir=cache, prefix='input-', suffix='.part', delete=False) as stream:
                temporary = Path(stream.name)
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if total > row['size']:
                        raise ValueError('Download exceeds pinned size')
                    stream.write(block)
                    digest.update(block)
            if total != row['size'] or digest.hexdigest() != row['sha256']:
                raise ValueError('Downloaded size/SHA-256 mismatch: ' + row['filename'])
        if target.exists() or target.is_symlink():
            verify_file(target, row)
            temporary.unlink()
        else:
            temporary.rename(target)
        temporary = None
        return 'downloaded and verified'
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=DEFAULT_CACHE)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args(argv)
    lock = load_lock()
    if not args.verify_only:
        args.cache.mkdir(parents=True, exist_ok=True)
        for row in [lock['base_iso'], *lock['packages']]:
            print(row['filename'] + ': ' + download(args.cache, row), flush=True)
    print(json.dumps(verify_inputs(args.cache, lock), sort_keys=True))


if __name__ == '__main__':
    main()
