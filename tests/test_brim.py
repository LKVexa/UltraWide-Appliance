import hashlib
import io
import json
import struct
import unittest
from unittest.mock import patch
import zipfile

from ultrawide import brim


I = brim.BrimInstruction


def image(*instructions, **kwargs):
    return brim.create_brim(instructions, source=b'test source\n', **kwargs).image


def rehash(blob):
    blob = bytearray(blob)
    blob[32:64] = hashlib.sha256(blob[80:]).digest()
    return bytes(blob)


class BrimFormatTests(unittest.TestCase):
    def test_native_binary_geometry_and_roundtrip(self):
        artifact = brim.create_brim([I('MOVI', rd=3, imm=(1 << 64) - 1), I('HALT')], source=b'source')
        actual = brim.verify_brim(artifact.image, source=b'source', brir=artifact.brir)
        self.assertEqual(artifact.image[:16], b'BRIM\x04\x01\x01\x05\x01\x14\x10\x10\x20\x04\x50\x00')
        self.assertEqual(len(artifact.image), 80 + 2 * 16 + 96)
        self.assertEqual(actual.instructions[0].imm, (1 << 64) - 1)
        self.assertEqual(actual.source_sha256, hashlib.sha256(b'source').hexdigest())
        self.assertEqual(artifact.brir.splitlines()[0], b'BRIR/1.1')
        self.assertFalse(actual.summary()['signed'])

    def test_actual_u64_immediate_bound_is_not_widened_silently(self):
        for invalid in (-1, 1 << 64, True):
            with self.subTest(value=invalid), self.assertRaises(brim.BrimError):
                image(I('MOVI', imm=invalid), I('HALT'))

    def test_unsigned_hash_binding_and_trailing_data(self):
        original = image(I('HALT'))
        corrupt = bytearray(original)
        corrupt[-1] ^= 1
        for blob in (original[:-1], original + b'x', bytes(corrupt), b'B1048576E\0' + original):
            with self.assertRaises(brim.BrimError):
                brim.parse_brim(blob)

    def test_signed_images_and_trust_trailers_are_rejected(self):
        original = bytearray(image(I('HALT')))
        original[7] |= 2
        with self.assertRaisesRegex(brim.BrimError, 'cryptographic'):
            brim.parse_brim(bytes(original))
        with self.assertRaises(brim.BrimError):
            brim.parse_brim(image(I('HALT')) + b'BRTM' + bytes(412))

    def test_all_geometry_fields_are_checked(self):
        for offset in (*range(4, 7), *range(8, 16)):
            blob = bytearray(image(I('HALT')))
            blob[offset] ^= 0x40
            with self.subTest(offset=offset), self.assertRaises(brim.BrimError):
                brim.parse_brim(bytes(blob))

    def test_instruction_reserved_bits_and_registers(self):
        for offset, value in ((80, 255), (81, 4), (82, 16), (85, 1), (86, 1)):
            blob = bytearray(image(I('HALT')))
            blob[offset] = value
            with self.subTest(offset=offset), self.assertRaises(brim.BrimError):
                brim.parse_brim(rehash(blob))

    def test_provenance_authority_and_derived_fields(self):
        for relative in (0, 4, 8, 12, 20, 88, 92):
            blob = bytearray(image(I('HALT')))
            blob[len(blob) - 96 + relative] ^= 0x80
            with self.subTest(relative=relative), self.assertRaises(brim.BrimError):
                brim.parse_brim(rehash(blob))

    def test_source_and_brir_mismatch(self):
        artifact = brim.create_brim([I('HALT')], source=b'original')
        with self.assertRaises(brim.BrimError):
            brim.verify_brim(artifact.image, source=b'changed')
        with self.assertRaises(brim.BrimError):
            brim.verify_brim(artifact.image, brir=artifact.brir + b'\n')

    def test_cfg_rejects_unreachable_nonterminating_bad_jump_and_stack(self):
        cases = ((I('HALT'), I('HALT')), (I('JMP', imm=0),),
                 (I('JMP', imm=100), I('HALT')), (I('POP'), I('HALT')),
                 (I('RET'), I('HALT')), (I('NOP'),),
                 (I('JNZ', imm=2), I('HALT'), I('JZ', imm=1)))
        for case in cases:
            with self.subTest(case=case), self.assertRaises(brim.BrimError):
                image(*case)

    def test_segment_and_host_budget_bounds(self):
        for kwargs in ({'data': bytes(4097)}, {'max_steps': 0}, {'max_steps': 4097}, {'image_version': True}):
            with self.assertRaises(brim.BrimError):
                image(I('HALT'), **kwargs)
        with self.assertRaises(brim.BrimError):
            image(*([I('NOP')] * 256 + [I('HALT')]))


class BrimExecutionTests(unittest.TestCase):
    def test_highest_bit_then_register_handoff_wrapping_add(self):
        first, second = [brim.compile_lctl(x) for x in brim.demo_lctl()]
        a = brim.run_brim(first.image)
        b = brim.run_brim(second.image, registers=dict(enumerate(a['registers'])))
        self.assertEqual(a['registers'][0], 1 << (brim.WORD_BITS - 1))
        self.assertEqual(b['registers'][1], 0)
        self.assertEqual(b['flags'], 24)
        self.assertEqual(a['register_sha256'][0], hashlib.sha256(a['registers'][0].to_bytes(brim.WORD_BYTES, 'little')).hexdigest())

    def test_full_width_input_not_and_logical_shift(self):
        blob = image(I('NOT', rd=1, ra=0), I('SHR', rd=2, ra=0, rb=3, imm=brim.WORD_BITS - 1), I('HALT'))
        out = brim.run_brim(blob, registers={0: 1 << (brim.WORD_BITS - 1), 3: 8})
        self.assertEqual(out['registers'][1], brim.WORD_MASK ^ (1 << (brim.WORD_BITS - 1)))
        self.assertEqual(out['registers'][2], 1)

    def test_four_modes_for_underflow(self):
        for mode in range(4):
            blob = image(I('SUB', mode=mode, rd=2, ra=0, rb=1), I('HALT'))
            if mode == 3:
                with self.assertRaisesRegex(brim.BrimError, 'trap'):
                    brim.run_brim(blob, registers={1: 1})
            else:
                result = brim.run_brim(blob, registers={1: 1})
                self.assertEqual(result['registers'][2], 0 if mode == 2 else brim.WORD_MASK)
                self.assertEqual(result['flags'], 16)

    def test_four_modes_for_overflow(self):
        for mode in range(4):
            blob = image(I('ADD', mode=mode, rd=2, ra=0, rb=1), I('HALT'))
            if mode == 3:
                with self.assertRaises(brim.BrimError):
                    brim.run_brim(blob, registers={0: brim.WORD_MASK, 1: 1})
            else:
                result = brim.run_brim(blob, registers={0: brim.WORD_MASK, 1: 1})
                self.assertEqual(result['registers'][2], brim.WORD_MASK if mode == 2 else 0)
                self.assertEqual(result['flags'], 24)

    def test_comparison_flags_survive_move_and_arithmetic(self):
        blob = image(I('CMP'), I('MOVI', rd=1, imm=9), I('ADD', rd=2, ra=1, rb=1),
                     I('JZ', imm=5), I('HALT'), I('HALT'))
        out = brim.run_brim(blob)
        self.assertEqual(out['trace'][-1]['pc'], 5)
        self.assertEqual(out['flags'] & 7, 1)

    def test_multiply_divide_remainder_and_bitwise(self):
        blob = image(I('MUL', rd=2, ra=0, rb=1), I('DIVU', rd=3, ra=2, rb=1),
                     I('MODU', rd=4, ra=2, rb=1), I('AND', rd=5, ra=0, rb=1),
                     I('OR', rd=6, ra=0, rb=1), I('XOR', rd=7, ra=0, rb=1), I('HALT'))
        out = brim.run_brim(blob, registers={0: 11, 1: 7})
        self.assertEqual(out['registers'][2:8], [77, 11, 0, 3, 15, 12])

    def test_unsupported_capabilities_rejected_before_callback(self):
        touched = []
        blob = image(I('LOAD'), I('HALT'))
        with self.assertRaises(brim.BrimError):
            brim.run_brim(blob, cancel=lambda: touched.append(True))
        self.assertEqual(touched, [])
        with self.assertRaises(brim.BrimError):
            brim.run_brim(image(I('HALT'), data=b'not a pure policy'))

    def test_zero_division_cancel_and_deadline(self):
        with self.assertRaises(brim.BrimError):
            brim.run_brim(image(I('DIVU'), I('HALT')))
        with self.assertRaisesRegex(brim.BrimError, 'cancelled'):
            brim.run_brim(image(I('HALT')), cancel=lambda: True)
        with patch('ultrawide.brim.time.monotonic', side_effect=[0, 2]), self.assertRaisesRegex(brim.BrimError, 'deadline'):
            brim.run_brim(image(I('HALT')), timeout_seconds=1)

    def test_runtime_budget_handles_cfg_with_a_terminal_path(self):
        blob = image(I('CMP'), I('JZ', imm=0), I('HALT'), max_steps=8)
        with self.assertRaisesRegex(brim.BrimError, 'budget'):
            brim.run_brim(blob, max_steps=3)

    def test_input_types_and_deadline_bounds(self):
        blob = image(I('HALT'))
        for registers in ({True: 1}, {0: True}, {0: -1}, {0: 1 << brim.WORD_BITS}, {16: 1}, [0]):
            with self.assertRaises(brim.BrimError):
                brim.run_brim(blob, registers=registers)
        for deadline in (0, -1, True, float('inf'), float('nan'), 301):
            with self.assertRaises(brim.BrimError):
                brim.run_brim(blob, timeout_seconds=deadline)
        with self.assertRaises(brim.BrimError):
            brim.run_brim(blob, cancel=3)


class BrimLctlTests(unittest.TestCase):
    def test_checked_eight_column_compilation(self):
        source = brim.demo_lctl()[0]
        ins = brim.parse_lctl(source)
        artifact = brim.compile_lctl(source, max_steps=8)
        self.assertEqual(brim.emit_lctl(ins), source)
        self.assertEqual(brim.verify_brim(artifact.image, source=source.encode()).instructions, ins)
        self.assertIn(b'language=LCTL-BRIM/1', artifact.brir)

    def test_other_dialects_are_not_silently_reinterpreted(self):
        for prefix in ('LCTLC/1.0', 'LCTLC/1.1', 'LCTLC-WIDE/1.0', 'UWA/0.1'):
            with self.assertRaises(brim.BrimError):
                brim.compile_lctl(brim.demo_lctl()[0].replace('LCTL-BRIM/1', prefix))

    def test_malformed_or_narrow_rows_fail(self):
        source = brim.demo_lctl()[0]
        corruptions = (source.replace('I0000', 'I0001'), source.replace('width=WIDE', 'width=64'),
                       source.replace('C0', 'C1'), source.replace('u64:1|', 'u64:18446744073709551616|'),
                       source.replace('main', 'other'), source.replace('\n', '\r\n'), source + '\n',
                       source.replace('|MOVI|', '|SVC|'))
        for bad in corruptions:
            with self.assertRaises(brim.BrimError):
                brim.compile_lctl(bad)


class BrimBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ultrawide.shs import Instruction, from_instructions, compile_program
        cls.executable = compile_program(from_instructions([Instruction('MOVI', rd=0, immediate=1 << (brim.WORD_BITS - 1)), Instruction('HALT')], debug={'preserved': 'metadata'}))
        cls.bundle = brim.build_bundle(cls.executable, provenance={'purpose': 'public-test'}, max_steps=16)

    @staticmethod
    def rewrite(blob, transform):
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            files = {x.filename: archive.read(x) for x in archive.infolist()}
        transform(files)
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        return output.getvalue()

    def test_lossless_source_binary_and_metadata(self):
        verified = brim.verify_bundle(self.bundle)
        self.assertEqual(verified.executable, self.executable)
        self.assertEqual(verified.provenance, {'purpose': 'public-test'})
        self.assertEqual(verified.control.instructions, (I('HALT'),))
        self.assertEqual(verified.summary()['brim_role'], 'binding-control')
        self.assertEqual(brim.build_bundle(self.executable, provenance={'purpose': 'public-test'}, max_steps=16), self.bundle)

    def test_verified_bundle_executes_actual_full_width_payload(self):
        result = brim.run_bundle(self.bundle)
        self.assertEqual(result['execution']['steps'], 2)
        self.assertEqual(result['bundle']['max_steps'], 16)
        self.assertEqual(result['bundle']['executable_sha256'], hashlib.sha256(self.executable).hexdigest())

    def test_hash_mismatch_and_unknown_member(self):
        for mutate in (lambda f: f.__setitem__('program.b104e', f['program.b104e'] + b'x'),
                       lambda f: f.__setitem__('extra', b'x'),
                       lambda f: f.__setitem__('../program.lctlc', f.pop('program.lctlc'))):
            with self.assertRaises(brim.BrimError):
                brim.verify_bundle(self.rewrite(self.bundle, mutate))

    def test_bad_manifest_duplicate_json_keys_and_nonfinite(self):
        for content in (b'{"format":1,"format":2}', b'{"format":NaN}', b'{"format":1e999}', b'[]'):
            with self.assertRaises(brim.BrimError):
                brim.verify_bundle(self.rewrite(self.bundle, lambda f: f.__setitem__('manifest.json', content)))

    def test_json_depth_and_integer_bounds(self):
        for content in (b'{"nested":' + b'[' * 65 + b'0' + b']' * 65 + b'}',
                        b'{"number":' + b'9' * 21 + b'}'):
            with self.assertRaises(brim.BrimError):
                brim._json(content, 16384)
        self.assertEqual(brim._json(b'{"quoted":"[[[[\\\"{\\\""}', 100)['quoted'], '[[[["{"')

    def test_member_and_total_decompression_are_bounded(self):
        with patch.dict(brim.MEMBERS, {'program.b104e': 64}), self.assertRaises(brim.BrimError):
            brim.verify_bundle(self.bundle)
        with patch('ultrawide.brim.MAX_TOTAL', 128), self.assertRaises(brim.BrimError):
            brim.verify_bundle(self.bundle)

    def test_prefix_trailer_and_truncation_rejected(self):
        for blob in (b'prefix' + self.bundle, self.bundle + b'trailer', self.bundle[:-1]):
            with self.assertRaises(brim.BrimError):
                brim.verify_bundle(blob)

    def test_provenance_limits(self):
        for claims in ({'bad key': 'x'}, {'key': 'x' * 257}, {'key': 'line\nbreak'}, {'key': 1}):
            with self.assertRaises(brim.BrimError):
                brim.build_bundle(self.executable, provenance=claims)


if __name__ == '__main__':
    unittest.main()
