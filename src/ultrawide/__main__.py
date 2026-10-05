"""Run the generic arithmetic scaffold without installing dependencies."""

import argparse
import json
from pathlib import Path
from .program import MAX_STEPS, emit, execute, read_program, demo_program, self_test
from .word import DEFAULT_WORD_BITS


def main(argv=None):
    parser = argparse.ArgumentParser(description='Generic software ultra-wide-word scaffold; custom UWA/0.1 subset')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('self-test', help='Check full-width arithmetic, source roundtrip and replay')
    demo = commands.add_parser('demo', help='Execute the high-bit arithmetic example')
    demo.add_argument('--bits', type=int, default=DEFAULT_WORD_BITS)
    demo.add_argument('--write-source', type=Path, help='Also save a new sealed example source file')
    run = commands.add_parser('run', help='Execute a sealed custom UWA/0.1 program')
    run.add_argument('path', type=Path)
    run.add_argument('--max-steps', type=int, default=MAX_STEPS)
    run.add_argument('--include-trace', action='store_true', help='Include the bounded step trace in output')
    args = parser.parse_args(argv)
    try:
        if args.command == 'self-test':
            result = self_test()
        else:
            program = demo_program(args.bits) if args.command == 'demo' else read_program(args.path)
            result = execute(program, max_steps=getattr(args, 'max_steps', MAX_STEPS))
            if args.command == 'demo' and args.write_source:
                with args.write_source.open('x', encoding='utf-8', newline='\n') as stream:
                    stream.write(emit(program))
            if not getattr(args, 'include_trace', False):
                result.pop('trace', None)
        print(json.dumps(result, sort_keys=True, ensure_ascii=True, allow_nan=False))
        return 0
    except (ValueError, OSError, UnicodeError) as exc:
        print(json.dumps({'status': 'ERROR', 'message': str(exc)}, ensure_ascii=True))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
