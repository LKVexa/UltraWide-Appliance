"""Explicit, bounded command-line workflows for the preview's distinct profiles."""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
from .program import MAX_STEPS, emit, execute, read_program, demo_program, self_test as uwa_self_test
from .word import DEFAULT_WORD_BITS


class CommandError(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CommandError(message)


def _read(path, limit):
    with Path(path).open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise CommandError(f'Input exceeds the {limit}-byte limit')
    return data


def _write_outputs(outputs):
    """Reserve all requested files exclusively; never replace user content."""
    paths = [Path(path) for path, _ in outputs]
    identities = [os.path.normcase(str(path.absolute())) for path in paths]
    if len(set(identities)) != len(paths):
        raise CommandError('Output paths must be distinct')
    if any(path.exists() or path.is_symlink() for path in paths):
        raise FileExistsError('Choose new output paths; existing files are never replaced')
    opened = []
    try:
        for path in paths:
            stream = path.open('xb')
            info = os.fstat(stream.fileno())
            opened.append((path, stream, (info.st_dev, info.st_ino)))
        for (_, stream, _), (_, data) in zip(opened, outputs):
            stream.write(data)
            stream.flush()
        for _, stream, _ in opened:
            stream.close()
    except BaseException:
        for path, stream, identity in opened:
            stream.close()
            if path.exists():
                current = path.lstat()
                if (current.st_dev, current.st_ino) == identity:
                    path.unlink()
        raise
    return [{'file': path.name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            for path, (_, data) in zip(paths, outputs)]


def _compact(result, include_trace=False):
    result = dict(result)
    if not include_trace:
        result.pop('trace', None)
    registers = result.get('registers')
    if isinstance(registers, (list, tuple)) and any(type(value) is int for value in registers):
        # BRIM's Python API includes exact integers; JSON uses their hashes and
        # bit lengths to avoid converting megabit values into decimal text.
        result.pop('registers')
    return result


def _parser():
    parser = _Parser(description='Wide-word preview: UWA, BASIC-compatible SHS, BRIM, and explicit storage')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('self-test', help='Check UWA, SHS, and packed BRIM word-chain profiles')
    demo = commands.add_parser('demo', help='Execute the original UWA arithmetic example')
    demo.add_argument('--bits', type=int, default=DEFAULT_WORD_BITS)
    demo.add_argument('--write-source', type=Path)
    run = commands.add_parser('run', help='Execute UWA/0.1 source')
    run.add_argument('path', type=Path)
    run.add_argument('--max-steps', type=int, default=MAX_STEPS)
    run.add_argument('--include-trace', action='store_true')

    shs = commands.add_parser('shs', help='BASIC-compatible execution and the LCTLC-WIDE extension')
    actions = shs.add_subparsers(dest='action', required=True)
    actions.add_parser('self-test')
    inspect = actions.add_parser('inspect')
    inspect.add_argument('path', type=Path)
    for name in ('translate', 'compile'):
        action = actions.add_parser(name)
        action.add_argument('input', type=Path)
        action.add_argument('output', type=Path)
    action = actions.add_parser('run')
    action.add_argument('path', type=Path)
    action.add_argument('--max-steps', type=int, default=4096)
    action.add_argument('--memory-bytes', type=int, default=1048576)
    action.add_argument('--timeout-seconds', type=float, default=30)
    action.add_argument('--include-trace', action='store_true')

    repository = commands.add_parser('repository', help='Explicit immutable object storage')
    repository.add_argument('--root', required=True, type=Path)
    actions = repository.add_subparsers(dest='action', required=True)
    action = actions.add_parser('put')
    action.add_argument('path', type=Path)
    action = actions.add_parser('get')
    action.add_argument('digest')
    action.add_argument('output', type=Path)
    actions.add_parser('list')

    brim = commands.add_parser('brim', help='Compile/verify/run the explicit LCTL-BRIM/1 subset')
    actions = brim.add_subparsers(dest='action', required=True)
    action = actions.add_parser('compile')
    action.add_argument('input', type=Path)
    action.add_argument('output', type=Path)
    action.add_argument('--brir-output', type=Path)
    action.add_argument('--max-steps', type=int, default=4096)
    for name in ('verify', 'run'):
        action = actions.add_parser(name)
        action.add_argument('path', type=Path)
        action.add_argument('--source', type=Path)
        action.add_argument('--brir', type=Path)
        if name == 'run':
            action.add_argument('--max-steps', type=int)
            action.add_argument('--timeout-seconds', type=float, default=30)
            action.add_argument('--include-trace', action='store_true')

    chain = commands.add_parser('chain', help='Pack and execute verified BRIM word chains')
    actions = chain.add_subparsers(dest='action', required=True)
    action = actions.add_parser('pack')
    action.add_argument('output', type=Path)
    action.add_argument('sources', nargs='+', type=Path)
    action.add_argument('--max-steps', type=int, default=10000)
    for name in ('verify', 'run'):
        action = actions.add_parser(name)
        action.add_argument('path', type=Path)
        if name == 'run':
            action.add_argument('--max-steps', type=int)
            action.add_argument('--timeout-seconds', type=float, default=30)
    action = actions.add_parser('unpack')
    action.add_argument('path', type=Path)
    action.add_argument('directory', type=Path)
    return parser


def _dispatch(args):
    if args.command == 'self-test':
        from . import shs, brim_wide
        checks = {'uwa': uwa_self_test(), 'shs': shs.self_test(), 'brim_word_chain': brim_wide.self_test()}
        if any(check.get('status') != 'PASS' for check in checks.values()):
            raise CommandError('A required preview self-test did not pass')
        return {'status': 'PASS', 'schema': 'ultrawide.self-test.v2',
                'word_bits': DEFAULT_WORD_BITS, 'profiles': checks}
    if args.command in ('demo', 'run'):
        program = demo_program(args.bits) if args.command == 'demo' else read_program(args.path)
        result = execute(program, max_steps=getattr(args, 'max_steps', MAX_STEPS))
        if args.command == 'demo' and args.write_source:
            _write_outputs([(args.write_source, emit(program).encode('utf-8'))])
        return _compact(result, getattr(args, 'include_trace', False))
    if args.command == 'shs':
        from . import shs
        if args.action == 'self-test':
            return shs.self_test()
        if args.action == 'run':
            return _compact(shs.run_file(args.path, max_steps=args.max_steps,
                            memory_bytes=args.memory_bytes, timeout_seconds=args.timeout_seconds), args.include_trace)
        program = shs.load_program(args.path if args.action == 'inspect' else args.input)
        image = shs.compile_program(program)
        if args.action == 'inspect':
            header = json.loads(program.header_bytes)
            return {'status': 'VERIFIED', 'schema': 'ultrawide.shs.inspection.v1',
                    'architecture': header['architecture'], 'architecture_version': header['architecture_version'],
                    'word_bits': header['word_bits'], 'instruction_count': len(program.instructions),
                    'entry_instruction': header['entry_instruction'], 'capabilities': header.get('capabilities', []),
                    'opcodes': dict(sorted(Counter(ins.name for ins in program.instructions).items())),
                    'image_bytes': len(image), 'image_sha256': hashlib.sha256(image).hexdigest(), 'bounds': shs.bounds()}
        data = shs.emit(program).encode('utf-8') if args.action == 'translate' else image
        return {'status': 'PASS', 'action': args.action, 'outputs': _write_outputs([(args.output, data)]),
                'image_sha256': hashlib.sha256(image).hexdigest()}
    if args.command == 'repository':
        from .repository import Repository, MAX_OBJECT_BYTES
        store = Repository(args.root)
        if args.action == 'put':
            data = _read(args.path, MAX_OBJECT_BYTES)
            return {'status': 'STORED', 'sha256': store.put(data), 'bytes': len(data)}
        if args.action == 'get':
            data = store.get(args.digest)
            return {'status': 'PASS', 'outputs': _write_outputs([(args.output, data)])}
        return {'status': 'PASS', 'objects': store.list()}
    if args.command == 'brim':
        from . import brim
        if args.action == 'compile':
            artifact = brim.compile_lctl(_read(args.input, 65536).decode('utf-8'), max_steps=args.max_steps)
            ir_path = args.brir_output or args.output.with_suffix('.brir')
            outputs = _write_outputs([(args.output, artifact.image), (ir_path, artifact.brir)])
            return {'status': 'PASS', 'outputs': outputs, 'image': brim.verify_brim(artifact.image).summary()}
        blob = _read(args.path, brim.MAX_IMAGE)
        source = _read(args.source, 65536) if args.source else None
        ir = _read(args.brir, 65536) if args.brir else None
        verified = brim.verify_brim(blob, source=source, brir=ir)
        if args.action == 'verify':
            return {'status': 'VERIFIED', **verified.summary()}
        return _compact(brim.run_brim(blob, max_steps=args.max_steps,
                        timeout_seconds=args.timeout_seconds), args.include_trace)
    if args.command == 'chain':
        from . import brim_wide
        if args.action == 'pack':
            if len(args.sources) > 64:
                raise CommandError('A chain may contain at most 64 source programs')
            sources = [_read(path, 65536).decode('utf-8') for path in args.sources]
            blob = brim_wide.compile_chain(sources, max_steps=args.max_steps)
            return {'status': 'PASS', 'outputs': _write_outputs([(args.output, blob)]),
                    'chain': brim_wide.verify_chain(blob).summary()}
        blob = _read(args.path, brim_wide.MAX_CHAIN_BYTES)
        if args.action == 'verify':
            return {'status': 'VERIFIED', **brim_wide.verify_chain(blob).summary()}
        if args.action == 'unpack':
            chain = brim_wide.verify_chain(blob)
            args.directory.mkdir(exist_ok=False)
            try:
                outputs = []
                for index, item in enumerate(chain.programs):
                    for extension, data in (('brimg', item.image), ('lctlb', item.source), ('brir', item.brir),
                                             ('brword', blob[index * brim_wide.WORD_BYTES:(index + 1) * brim_wide.WORD_BYTES])):
                        outputs.append((args.directory / f'{index:04d}.{extension}', data))
                written = _write_outputs(outputs)
            except BaseException:
                # Only remove our empty directory; never remove unexpected data.
                try:
                    args.directory.rmdir()
                except OSError:
                    pass
                raise
            return {'status': 'PASS', 'words': len(chain.programs), 'outputs': written,
                    'chain_sha256': chain.chain_sha256}
        return _compact(brim_wide.run_chain(blob, max_steps=args.max_steps,
                                           timeout_seconds=args.timeout_seconds))
    raise CommandError('Unsupported command')


def main(argv=None):
    try:
        result = _dispatch(_parser().parse_args(argv))
        print(json.dumps(result, sort_keys=True, ensure_ascii=True, allow_nan=False))
        return 0
    except KeyboardInterrupt:
        print(json.dumps({'status': 'CANCELLED', 'message': 'Execution interrupted'}))
        return 130
    except Exception as exc:
        # Report source/image faults without host stack traces or huge integers.
        print(json.dumps({'status': 'ERROR', 'error_type': type(exc).__name__, 'message': str(exc)[:2048]},
                         ensure_ascii=True, allow_nan=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
