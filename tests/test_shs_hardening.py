"""Adversarial boundaries and an independently stated arithmetic conformance model."""
from pathlib import Path
import hashlib
import json
import sys
import tracemalloc
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ultrawide.shs import engine, reference


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.program = engine.from_instructions([reference.Instruction('HALT')])
        self.source = engine.emit(self.program)
        self.body = self.source.rsplit('@sha256 ', 1)[0]

    def test_reference_bytes_match_recovered_authority(self):
        self.assertEqual(hashlib.sha256(Path(reference.__file__).read_bytes()).hexdigest(), engine.REFERENCE_SHA256)

    def test_line_count_is_checked_before_source_list_allocation(self):
        source = 'x\n' * 500000
        for action in (engine.parse, engine.seal, engine.from_assembly):
            with self.subTest(action=action.__name__):
                tracemalloc.start()
                try:
                    with self.assertRaisesRegex(engine.WideError, 'line limit'):
                        action(source)
                    _, peak = tracemalloc.get_traced_memory()
                finally:
                    tracemalloc.stop()
                self.assertLess(peak, 3 * len(source))

    def test_control_line_separators_are_refused(self):
        for control in ('\v', '\f', '\x1c', '\x1d', '\x1e', '\u0085', '\u2028'):
            for action in (engine.parse, engine.seal, engine.from_assembly):
                with self.subTest(control=repr(control), action=action.__name__), self.assertRaises(ValueError):
                    action('LCTLC-WIDE/0.1' + control + 'x')

    def test_cell_splitting_is_bounded(self):
        rows = self.body.splitlines()
        row_index = next(i for i, line in enumerate(rows) if line.startswith('I0000|'))
        rows[row_index] = '|' * 200000
        text = engine.seal('\n'.join(rows))
        tracemalloc.start()
        try:
            with self.assertRaisesRegex(ValueError, 'exactly eight cells'):
                engine.parse(text)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 6 * len(text))

    def test_assembly_token_and_directive_bounds(self):
        bad = ['ADD ' + 'R0,' * 50000, 'HALT 0 1', 'NOP R0', 'MOVI R0',
               '.capability ' + 'a' * 257 + '\nHALT', '.unknown foo\nHALT',
               'a' * 129 + ':\nHALT']
        for source in bad:
            with self.subTest(prefix=source[:40]), self.assertRaises(ValueError):
                engine.from_assembly(source)

    def test_legacy_assembly_uses_final_width_mode_defaults(self):
        # Match the recovered frontend's actual two-pass behavior, not an assumed lexical scope.
        p = engine.from_assembly('.width 8\nMOVI R0, 1\n.width 16\n.mode saturating\nHALT')
        self.assertEqual({i.width for i in p.instructions}, {16})
        self.assertEqual({i.mode for i in p.instructions}, {'saturating'})

    def test_header_nonfinite_json_deep_nesting_and_capability_types(self):
        base = json.loads(self.program.header_bytes)
        headers = []
        for value in ('NaN', 'Infinity', '-Infinity', '1e99999', '-1e99999'):
            headers.append(b'{"extra":' + value.encode() + b',' + self.program.header_bytes[1:])
        headers.append(b'{"extra":' + b'[' * 1500 + b'0' + b']' * 1500 + b',' + self.program.header_bytes[1:])
        for caps in ('console.write', [True], ['a' * 257], ['a'] * 129, ['']):
            header = dict(base, capabilities=caps)
            headers.append(reference.canonical_json(header))
        for hb in headers:
            with self.subTest(header_prefix=hb[:30]), self.assertRaises(ValueError):
                engine.compile_program(engine.Program(self.program.instructions, hb))

    def test_capability_descriptor_cannot_escape_allocated_memory(self):
        for base, length in ((-1, 8), (0, reference.WORD_BYTES + 1), (reference.WORD_BYTES, 1), (True, 1)):
            for opcode in ('LOAD', 'STORE'):
                p = engine.from_instructions([reference.Instruction(opcode, rb=1, width=8), reference.Instruction('HALT')])
                vm = engine.BoundedVM(memory_bytes=reference.WORD_BYTES)
                vm.cap_table[1] = reference.Capability(base, length, frozenset({'read', 'write'}))
                before = bytes(vm.memory)
                with self.subTest(base=base, length=length, op=opcode), self.assertRaisesRegex(reference.VMFault, 'physical memory'):
                    vm.run(engine.compile_program(p))
                self.assertEqual(bytes(vm.memory), before)

    def test_stack_pointer_cannot_change_python_slice_extent(self):
        for sp in (-1, reference.WORD_BYTES + 1, True):
            for opcode in ('PUSH', 'POP'):
                vm = engine.BoundedVM(memory_bytes=reference.WORD_BYTES)
                vm.state.sp = sp
                p = engine.from_instructions([reference.Instruction(opcode), reference.Instruction('HALT')])
                with self.subTest(sp=sp, op=opcode), self.assertRaisesRegex(reference.VMFault, 'stack pointer'):
                    vm.run(engine.compile_program(p))
                self.assertEqual(len(vm.memory), reference.WORD_BYTES)

    def test_rejected_output_is_not_partially_appended(self):
        p = engine.from_instructions([reference.Instruction('MOVI', immediate=123), reference.Instruction('SVC', immediate=1), reference.Instruction('HALT')])
        vm = engine.BoundedVM(max_output_chars=2)
        with self.assertRaisesRegex(reference.VMFault, 'output character limit'):
            vm.run(engine.compile_program(p))
        self.assertEqual(vm.stdout, [])

    def test_surrogate_character_is_a_defined_vm_fault(self):
        p = engine.from_instructions([reference.Instruction('MOVI', immediate=0xD800), reference.Instruction('SVC', immediate=4), reference.Instruction('HALT')])
        vm = engine.BoundedVM()
        with self.assertRaisesRegex(reference.VMFault, 'surrogate'):
            vm.run(engine.compile_program(p))
        self.assertEqual(vm.stdout, [])

    def test_huge_unknown_service_has_a_bounded_error(self):
        p = engine.from_instructions([reference.Instruction('SVC', immediate=1 << (reference.WORD_BITS - 1))])
        with self.assertRaises(reference.VMFault) as caught:
            engine.run(p)
        self.assertLess(len(str(caught.exception)), 120)

    def test_deadline_is_cooperative_between_instructions(self):
        p = engine.from_instructions([reference.Instruction('MOVI', immediate=7), reference.Instruction('HALT')])
        vm = engine.BoundedVM(timeout_seconds=30)
        with mock.patch.object(engine.time, 'monotonic', side_effect=[0, 0, 0, 0, 31]):
            with self.assertRaisesRegex(reference.VMFault, 'deadline'):
                vm.run(engine.compile_program(p))
        self.assertEqual(vm.state.registers[0], 7)
        self.assertEqual(len(vm.trace), 1)

    def test_timeout_argument_is_finite_and_bounded(self):
        for value in (0, -1, 121, True, float('nan'), float('inf'), '30'):
            with self.subTest(timeout=str(value)), self.assertRaises(ValueError):
                engine.BoundedVM(timeout_seconds=value)

    def test_boolean_cancellation_stops_before_execution(self):
        vm = engine.BoundedVM(cancel=lambda: True)
        with self.assertRaisesRegex(reference.VMFault, 'cancelled'):
            vm.run(engine.compile_program(self.program))
        self.assertEqual(vm.trace, [])

    def test_false_cancellation_allows_execution(self):
        result = engine.run(self.program, cancel=lambda: False)
        self.assertEqual(result['steps'], 1)

    def test_vm_is_single_use_to_keep_state_and_trace_budget_bounded(self):
        vm = engine.BoundedVM()
        blob = engine.compile_program(self.program)
        vm.run(blob)
        with self.assertRaisesRegex(ValueError, 'only one image'):
            vm.run(blob)

    def test_run_api_schema_and_no_full_register_previews(self):
        result = engine.run(self.program)
        self.assertEqual(result['schema'], 'ultrawide.shs.execution.v1')
        self.assertEqual(len(result['state']['register_sha256']), 16)
        self.assertNotIn('registers_preview', result['state'])
        self.assertEqual(result['limits']['timeout_seconds'], 30.0)

    def test_packaged_control_memory_stack_example(self):
        path = Path(__file__).resolve().parents[1] / 'examples/shs-control-memory-stack.lctlw'
        program = engine.load_program(path)
        vm = engine.BoundedVM(memory_bytes=2 * reference.WORD_BYTES, capabilities=('console.write',))
        result = vm.run(engine.compile_program(program))
        self.assertEqual(result['stdout'], '5\n')
        self.assertEqual(result['steps'], 26)
        self.assertEqual(vm.state.sp, 2 * reference.WORD_BYTES)
        self.assertEqual([vm.state.registers[i] for i in (0, 3, 4)], [5, 5, 5])
        self.assertEqual(int.from_bytes(vm.memory[:reference.WORD_BYTES], 'little'), 5)
        self.assertEqual(int.from_bytes(vm.memory[reference.WORD_BYTES:], 'little'), 5)


class ArithmeticModelTests(unittest.TestCase):
    def test_overflow_underflow_modes_against_integer_interval_model(self):
        # This model is independent of the recovered apply_mode/_execute code.
        # It checks 18 widths × 4 modes × three overflowing arithmetic operations.
        for width in reference.WIDTHS:
            bound = 1 << width
            for mode in ('wrapping', 'checked', 'saturating', 'trapping'):
                for op, a, b, mathematical in (('ADD', bound - 1, 1, bound),
                                               ('SUB', 0, 1, -1), ('MUL', bound // 2, 2, bound)):
                    with self.subTest(width=width, mode=mode, op=op):
                        p = engine.from_instructions([
                            reference.Instruction('MOVI', rd=0, immediate=a),
                            reference.Instruction('MOVI', rd=1, immediate=b),
                            reference.Instruction(op, rd=2, ra=0, rb=1, width=width, mode=mode),
                            reference.Instruction('HALT')])
                        vm = engine.BoundedVM(memory_bytes=reference.WORD_BYTES)
                        if mode in ('checked', 'trapping'):
                            with self.assertRaises(reference.VMFault):
                                vm.run(engine.compile_program(p))
                            self.assertEqual(vm.state.registers[2], 0)
                            self.assertEqual(vm.state.pc, 3)
                        else:
                            vm.run(engine.compile_program(p))
                            expected = mathematical % bound if mode == 'wrapping' else min(max(mathematical, 0), bound - 1)
                            self.assertEqual(vm.state.registers[2], expected)
                            self.assertEqual(vm.state.zero, expected == 0)
                            self.assertEqual(vm.state.negative, bool(expected & (bound // 2)))
                            self.assertTrue(vm.state.carry)
                            self.assertTrue(vm.state.overflow)


if __name__ == '__main__':
    unittest.main()
