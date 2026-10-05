"""Source integrity, operand validation, execution limits and deterministic replay."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import tracemalloc
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ultrawide import Word
from ultrawide.__main__ import main
from ultrawide.program import (COLUMNS, MAGIC, MAX_INSTRUCTIONS, MAX_SOURCE_BYTES, Instruction,
                               Program, ProgramError, demo_program, emit, execute, parse,
                               read_program, seal, self_test, validate)


def source_with_rows(rows, bits=8):
    return seal('\n'.join([MAGIC, f'@word-bits {bits}', COLUMNS, *rows, '@end']))


class ProgramTests(unittest.TestCase):
    def test_full_width_example_roundtrip_and_values(self):
        program = demo_program()
        self.assertEqual(parse(emit(program)), program)
        result = execute(program)
        self.assertEqual(result['word_bits'], 1048576)
        self.assertTrue(result['halted'])
        self.assertEqual(result['steps'], 7)
        self.assertEqual(result['registers'][0]['bit_length'], 1048576)
        self.assertEqual(result['registers'][2]['bit_length'], 0)
        self.assertEqual(result['registers'][3]['sha256'], Word((1 << 1048576) - 1).digest())
        self.assertEqual(result['registers'][5]['low64_hex'], '0000000000000001')

    def test_execution_is_deterministic(self):
        self.assertEqual(execute(demo_program()), execute(demo_program()))
        self.assertEqual(self_test()['status'], 'PASS')

    def test_source_tampering_and_trailing_data_are_rejected(self):
        text = emit(demo_program(8))
        for changed in (text.replace('MOVI', 'NOPE', 1), text + 'extra\n', text.rsplit('@sha256', 1)[0]):
            with self.assertRaises(ProgramError):
                parse(changed)

    def test_crlf_normalization_preserves_sealed_program(self):
        text = emit(demo_program(8))
        self.assertEqual(parse(text), parse(text.replace('\n', '\r\n')))

    def test_parser_rejects_unsupported_or_malformed_rows_even_when_resealed(self):
        bad_rows = [
            'I0000|HOST|_|_|_|_|8|_',
            'I0000|MOVI|R8|_|_|1|8|_',
            'I0000|MOVI|R0|_|_|-1|8|_',
            'I0000|MOVI|R0|_|_|256|8|_',
            'I0000|MOVI|R0|_|_|bit:8|8|_',
            'I0000|MOVI|R0|_|_|0x100|8|_',
            'I0001|MOVI|R0|_|_|1|8|_',
            'I0000|MOVI|R0|_|_|1|16|_',
            'I0000|ADD|R0|R1|_|_|8|_',
            'I0000|MOVI|R0|R1|_|1|8|_',
            'I0000|NOP|_|_|_|_|8|extra|cell',
            'I0000|NOP|_|_|_|_|8|bad note',
            'I0000|MOVI|R0|_|_|__import__(os)|8|_',
        ]
        for row in bad_rows:
            with self.subTest(row=row), self.assertRaises(ValueError):
                parse(source_with_rows([row, 'I0001|HALT|_|_|_|_|8|_']))

    def test_literal_forms_are_exact(self):
        program = parse(source_with_rows([
            'I0000|MOVI|R0|_|_|mask|8|_',
            'I0001|MOVI|R1|_|_|bit:7|8|_',
            'I0002|MOVI|R2|_|_|0xAB|8|_',
            'I0003|MOVI|R3|_|_|171|8|_',
            'I0004|HALT|_|_|_|_|8|_',
        ]))
        self.assertEqual(tuple(i.immediate for i in program.instructions[:-1]), (255, 128, 171, 171))

    def test_full_width_hex_immediate_fits_and_roundtrips(self):
        value = (1 << 1048575) | 42
        program = Program(1048576, (Instruction('MOVI', out=0, immediate=value), Instruction('HALT')))
        encoded = emit(program)
        self.assertIn('0x8', encoded)
        self.assertEqual(parse(encoded), program)

    def test_decimal_and_source_size_are_bounded(self):
        huge = '9' * 4097
        with self.assertRaises(ProgramError):
            parse(source_with_rows([f'I0000|MOVI|R0|_|_|{huge}|1048576|_',
                                    'I0001|HALT|_|_|_|_|1048576|_'], bits=1048576))
        with self.assertRaises(ProgramError):
            parse('x' * (MAX_SOURCE_BYTES + 1))

    def test_many_short_lines_are_rejected_without_list_amplification(self):
        source = MAGIC + '\n' + 'x\n' * 500000
        for action in (parse, seal):
            with self.subTest(action=action.__name__):
                tracemalloc.start()
                try:
                    with self.assertRaisesRegex(ProgramError, 'Physical source line limit'):
                        action(source)
                    _, peak = tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
                self.assertLess(peak, 3 * len(source), 'Source rejection allocated an oversized line list')

    def test_non_lf_splitlines_separators_cannot_bypass_line_bound(self):
        for separator in ('\v', '\f', '\x1c', '\x1d', '\x1e', '\u0085', '\u2028'):
            text = MAGIC + separator + 'x' * 1000
            for action in (parse, seal):
                with self.subTest(separator=repr(separator), action=action.__name__), self.assertRaises(ProgramError):
                    action(text)

    def test_many_cell_delimiters_do_not_allocate_a_large_cell_list(self):
        # Below the physical line length bound, but far beyond eight cells.
        row = '|' * 200000
        text = source_with_rows([row, 'I0001|HALT|_|_|_|_|8|_'])
        tracemalloc.start()
        try:
            with self.assertRaisesRegex(ProgramError, 'exactly eight cells'):
                parse(text)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 6 * len(text), 'Cell parsing amplified delimiter count into allocations')

    def test_single_line_length_is_bounded_during_parse_and_seal(self):
        source = '|' * 1000000
        for action in (parse, seal):
            with self.subTest(action=action.__name__), self.assertRaisesRegex(ProgramError, 'line length'):
                action(source)

    def test_program_shift_immediate_must_fit_its_word(self):
        valid = Program(8, (Instruction('MOVI', out=0, immediate=1),
                            Instruction('SHL', out=1, a=0, immediate=255), Instruction('HALT')))
        self.assertEqual(execute(valid)['registers'][1]['bit_length'], 0)
        invalid = Program(8, (Instruction('SHL', out=1, a=0, immediate=256), Instruction('HALT')))
        with self.assertRaisesRegex(ProgramError, 'Immediate'):
            validate(invalid)
        self.assertEqual(Word(1, 8).shl(256).value, 0)

    def test_program_must_be_bounded_and_halt_once_at_end(self):
        for instructions in ((), (Instruction('NOP'),), (Instruction('HALT'), Instruction('HALT')),
                             (Instruction('NOP'),) * MAX_INSTRUCTIONS + (Instruction('HALT'),)):
            with self.assertRaises(ProgramError):
                validate(Program(8, instructions))

    def test_typed_api_rejects_ambiguous_or_invalid_fields(self):
        cases = [Instruction('MOVI', out=True, immediate=1), Instruction('MOVI', out=0, immediate=True),
                 Instruction('MOVI', out=0, immediate=-1), Instruction(['ADD']),
                 Instruction('SHL', out=0, a=0, immediate=1048577), Instruction('NOP', note='')]
        for ins in cases:
            with self.subTest(op=str(ins.opcode)), self.assertRaises(ValueError):
                validate(Program(1048576, (ins, Instruction('HALT'))))

    def test_instruction_budget_and_cancellation(self):
        with self.assertRaises(ProgramError):
            execute(demo_program(8), max_steps=1)
        calls = []
        def cancel():
            calls.append(True)
            raise InterruptedError('cancelled')
        with self.assertRaises(InterruptedError):
            execute(demo_program(8), check_cancelled=cancel)
        self.assertEqual(len(calls), 1)

    def test_aliasing_operands_and_zero_fill_shifts(self):
        program = Program(8, (
            Instruction('MOVI', out=0, immediate=255),
            Instruction('MOVI', out=1, immediate=1),
            Instruction('ADD', out=0, a=0, b=1),
            Instruction('SHL', out=1, a=1, immediate=8),
            Instruction('HALT'),
        ))
        result = execute(program)
        self.assertEqual(result['registers'][0]['bit_length'], 0)
        self.assertEqual(result['registers'][1]['bit_length'], 0)

    def test_file_read_and_cli_report_are_bounded_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'example.uwa'
            path.write_text(emit(demo_program(8)), encoding='utf-8')
            self.assertEqual(read_program(path), demo_program(8))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(['run', str(path)])
            self.assertEqual(code, 0)
            report = json.loads(output.getvalue())
            self.assertNotIn('trace', report)
            self.assertEqual(report['steps'], 7)

    def test_cli_error_and_exclusive_source_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'existing.uwa'
            path.write_text('keep-me', encoding='utf-8')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(['demo', '--bits', '8', '--write-source', str(path)])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue())['status'], 'ERROR')
            self.assertEqual(path.read_text(encoding='utf-8'), 'keep-me')


if __name__ == '__main__':
    unittest.main()
