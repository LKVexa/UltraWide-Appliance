"""Whole-program word packing, register handoff, integrity and resource limits."""
import hashlib
import struct
import unittest
from unittest.mock import patch

from ultrawide import brim, brim_wide as wide


class ProgramWordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = brim.demo_lctl()
        cls.blob = wide.compile_chain(cls.sources)

    def test_each_word_contains_one_complete_image_and_its_source(self):
        self.assertEqual(len(self.blob), 2 * 131072)
        chain = wide.verify_chain(self.blob)
        for index, program in enumerate(chain.programs):
            expected = brim.compile_lctl(self.sources[index])
            self.assertEqual(program.image, expected.image)
            self.assertEqual(program.source, self.sources[index].encode())
            self.assertEqual(program.brir, expected.brir)
            start = index * wide.WORD_BYTES + wide.HEADER_BYTES
            self.assertEqual(self.blob[start:start + len(program.image)], expected.image)
        self.assertEqual(chain.summary()['words'], 2)

    def test_full_width_operands_roundtrip_complete_program_words(self):
        operands = wide.as_operands(self.blob)
        self.assertTrue(all(0 <= value < (1 << wide.WORD_BITS) for value in operands))
        self.assertEqual(wide.from_operands(operands), self.blob)
        for bad in (True, -1, 1 << wide.WORD_BITS, 1.0):
            with self.subTest(value_type=type(bad).__name__), self.assertRaises(ValueError):
                wide.from_operands([bad])

    def test_actual_high_bit_is_handed_to_next_image_without_narrowing(self):
        result = wide.run_chain(self.blob)
        self.assertEqual(result['steps'], 5)
        self.assertEqual(result['registers'][0]['bit_length'], 1048576)
        self.assertEqual(result['registers'][1]['bit_length'], 0)
        self.assertEqual(result['registers'][1]['sha256'], hashlib.sha256(bytes(131072)).hexdigest())
        self.assertEqual(result['images'][0]['output_registers_sha256'], result['images'][1]['input_registers_sha256'])
        self.assertEqual(result['images'][1]['flags'] & 24, 24)

    def test_chain_can_receive_a_full_width_caller_operand(self):
        blob = wide.compile_chain([self.sources[1]])
        result = wide.run_chain(blob, registers={0: 1 << (wide.WORD_BITS - 1)})
        self.assertEqual(result['registers'][1]['bit_length'], 0)
        self.assertEqual(result['registers'][0]['bit_length'], wide.WORD_BITS)

    def test_packing_is_deterministic(self):
        self.assertEqual(wide.compile_chain(self.sources), self.blob)

    def test_no_partial_missing_extra_or_reordered_words(self):
        size = wide.WORD_BYTES
        cases = (b'', self.blob[:-1], self.blob + b'\0', self.blob[:size],
                 self.blob[size:] + self.blob[:size], self.blob[:size] * 2)
        for case in cases:
            with self.subTest(bytes=len(case)), self.assertRaises(ValueError):
                wide.verify_chain(case)

    def test_corruption_in_every_header_region_source_image_ir_and_padding(self):
        # Every byte range is either interpreted and constrained or hash-bound.
        first = wide.verify_chain(self.blob).programs[0]
        offsets = (0, 8, 10, 12, 16, 20, 24, 28, 32, 36, 40, 72, 104, 136, 168, 200, 232,
                   wide.HEADER_BYTES, wide.HEADER_BYTES + len(first.image),
                   wide.HEADER_BYTES + len(first.image) + len(first.source), wide.WORD_BYTES - 1)
        for offset in offsets:
            changed = bytearray(self.blob)
            changed[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                wide.verify_chain(bytes(changed))

    def test_second_word_cannot_change_the_chain_budget(self):
        changed = bytearray(self.blob)
        struct.pack_into('<I', changed, wide.WORD_BYTES + 36, 9999)
        with self.assertRaisesRegex(ValueError, 'budget mismatch'):
            wide.verify_chain(bytes(changed))

    def test_complete_source_and_image_that_do_not_fit_are_rejected(self):
        source = b'x' * wide.WORD_BYTES
        artifact = brim.create_brim([brim.BrimInstruction('HALT')], source=source)
        with self.assertRaisesRegex(ValueError, 'fragmentation'):
            wide.pack_words([wide.PackedProgram(artifact.image, source, artifact.brir)])
        with self.assertRaises(ValueError):
            wide.compile_chain(['x' * wide.WORD_BYTES])

    def test_word_count_and_step_parameters_are_bounded(self):
        item = wide.verify_chain(self.blob).programs[0]
        with self.assertRaises(ValueError):
            wide.pack_words([item] * 65)
        with self.assertRaises(ValueError):
            wide.pack_words([])
        for bad in (0, True, 10001, 1.5):
            with self.subTest(budget=bad), self.assertRaises(ValueError):
                wide.pack_words([item], max_steps=bad)

    def test_total_steps_apply_across_images_and_cannot_be_raised_by_caller(self):
        item = wide.verify_chain(self.blob).programs
        blob = wide.pack_words(item, max_steps=4)
        with self.assertRaisesRegex(ValueError, 'budget'):
            wide.run_chain(blob, max_steps=10000)
        with self.assertRaisesRegex(ValueError, 'budget'):
            wide.run_chain(self.blob, max_steps=4)

    def test_all_images_are_verified_before_any_execution(self):
        changed = bytearray(self.blob)
        changed[wide.WORD_BYTES + wide.HEADER_BYTES] ^= 1
        with patch.object(brim, 'run_brim') as runner:
            with self.assertRaises(ValueError):
                wide.run_chain(bytes(changed))
            runner.assert_not_called()

    def test_unsupported_source_capabilities_cannot_enter_a_chain(self):
        image = brim.create_brim([brim.BrimInstruction('SVC'), brim.BrimInstruction('HALT')], source=b'unsupported service')
        second = wide.PackedProgram(image.image, b'unsupported service', image.brir)
        first = wide.verify_chain(self.blob).programs[0]
        with patch.object(brim, 'run_brim') as runner:
            with self.assertRaisesRegex(ValueError, 'compile under LCTL-BRIM'):
                wide.pack_words([first, second])
            runner.assert_not_called()

    def test_source_hash_alone_does_not_prove_actual_compilation(self):
        source = brim.emit_lctl([brim.BrimInstruction('MOVI', imm=1), brim.BrimInstruction('HALT')]).encode()
        # A structurally valid image can lie about which instructions its source produced.
        false = brim.create_brim([brim.BrimInstruction('MOVI', imm=2), brim.BrimInstruction('HALT')],
                                 source=source, source_language='LCTL-BRIM/1')
        brim.verify_brim(false.image, source=source, brir=false.brir)
        with self.assertRaisesRegex(ValueError, 'actual compilation'):
            wide.pack_words([wide.PackedProgram(false.image, source, false.brir)])

    def test_cancellation_and_deadline_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            wide.run_chain(self.blob, cancel=lambda: True)
        with patch.object(wide.time, 'monotonic', side_effect=[0, 31]):
            with self.assertRaisesRegex(ValueError, 'wall-time'):
                wide.run_chain(self.blob)
        for timeout in (False, 0, -1, float('nan'), float('inf'), 121):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                wide.run_chain(self.blob, timeout_seconds=timeout)

    def test_caller_wall_time_policy_reaches_each_image(self):
        with patch.object(brim, 'run_brim', wraps=brim.run_brim) as runner:
            wide.run_chain(self.blob, timeout_seconds=60)
            self.assertEqual(len(runner.call_args_list), 2)
            self.assertTrue(all(call.kwargs['timeout_seconds'] == 60 for call in runner.call_args_list))

    def test_self_test_executes_real_compiled_images(self):
        result = wide.self_test()
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['checks_passed'], 8)


if __name__ == '__main__':
    unittest.main()
