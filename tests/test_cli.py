"""CLI workflows, compact full-width output, and exclusive output files."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ultrawide import brim, shs
from ultrawide.__main__ import main


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def invoke(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main([str(arg) for arg in args])
        return code, json.loads(output.getvalue())

    def test_existing_uwa_demo_remains_available(self):
        code, result = self.invoke('demo', '--bits', 8)
        self.assertEqual(code, 0)
        self.assertEqual(result['word_bits'], 8)
        self.assertTrue(result['halted'])

    def test_invalid_arguments_and_missing_inputs_return_json_errors(self):
        for args in (('shs', 'run'), ('shs', 'run', 'absent.image'),
                     ('demo', '--bits', 'not-an-integer'), ('unknown-command',)):
            with self.subTest(args=args):
                code, result = self.invoke(*args)
                self.assertEqual(code, 1)
                self.assertEqual(result['status'], 'ERROR')
                self.assertLessEqual(len(result['message']), 2048)

    def test_shs_image_translation_compile_and_execution_roundtrip(self):
        original = shs.compile_program(shs.from_instructions([
            shs.Instruction('MOVI', rd=0, immediate=1 << 1048575), shs.Instruction('HALT')]))
        image, source, rebuilt = (self.root / name for name in ('original.image', 'source.lctlw', 'rebuilt.image'))
        image.write_bytes(original)
        self.assertEqual(self.invoke('shs', 'translate', image, source)[0], 0)
        self.assertEqual(self.invoke('shs', 'compile', source, rebuilt)[0], 0)
        self.assertEqual(rebuilt.read_bytes(), original)
        code, report = self.invoke('shs', 'inspect', source)
        self.assertEqual(code, 0)
        self.assertEqual(report['instruction_count'], 2)
        code, report = self.invoke('shs', 'run', rebuilt)
        self.assertEqual(code, 0)
        self.assertEqual(len(report['state']['register_sha256']), 16)
        self.assertNotIn('trace', report)
        self.assertNotIn('registers_preview', report['state'])

    def test_shs_invalid_budget_or_output_collision_is_not_success(self):
        image = self.root / 'input.image'
        image.write_bytes(shs.compile_program(shs.from_instructions([shs.Instruction('HALT')])))
        output = self.root / 'preserve.lctlw'
        output.write_bytes(b'preserve')
        self.assertEqual(self.invoke('shs', 'translate', image, output)[0], 1)
        self.assertEqual(output.read_bytes(), b'preserve')
        self.assertEqual(self.invoke('shs', 'run', image, '--max-steps', '0')[0], 1)
        self.assertEqual(self.invoke('shs', 'run', image, '--timeout-seconds', 'nan')[0], 1)

    def test_repository_put_list_get_and_no_overwrite(self):
        source = self.root / 'opaque'
        source.write_bytes(b'\x00\xff\x01')
        store = self.root / 'objects'
        code, result = self.invoke('repository', '--root', store, 'put', source)
        self.assertEqual(code, 0)
        identity = result['sha256']
        code, result = self.invoke('repository', '--root', store, 'list')
        self.assertEqual(result['objects'], [{'bytes': 3, 'sha256': identity}])
        output = self.root / 'copied'
        self.assertEqual(self.invoke('repository', '--root', store, 'get', identity, output)[0], 0)
        self.assertEqual(output.read_bytes(), source.read_bytes())
        self.assertEqual(self.invoke('repository', '--root', store, 'get', identity, output)[0], 1)

    def test_brim_compile_verify_and_high_bit_run_are_compact(self):
        source = self.root / 'high.lctlb'
        text = brim.emit_lctl([brim.BrimInstruction('MOVI', rd=0, imm=1),
                               brim.BrimInstruction('SHL', rd=0, ra=0, imm=1048575),
                               brim.BrimInstruction('HALT')])
        source.write_text(text, encoding='utf-8', newline='\n')
        image = self.root / 'high.brimg'
        self.assertEqual(self.invoke('brim', 'compile', source, image)[0], 0)
        self.assertTrue(image.with_suffix('.brir').is_file())
        self.assertEqual(self.invoke('brim', 'verify', image, '--source', source,
                                     '--brir', image.with_suffix('.brir'))[0], 0)
        code, report = self.invoke('brim', 'run', image)
        self.assertEqual(code, 0)
        self.assertEqual(report['register_bit_length'][0], 1048576)
        self.assertNotIn('registers', report)
        self.assertNotIn('trace', report)
        self.assertLess(len(json.dumps(report)), 8192)

    def test_brim_sidecar_collision_preserves_both_paths(self):
        source, image = self.root / 'simple.lctlb', self.root / 'simple.brimg'
        source.write_text(brim.emit_lctl([brim.BrimInstruction('HALT')]), encoding='utf-8', newline='\n')
        sidecar = image.with_suffix('.brir')
        sidecar.write_bytes(b'preserve')
        self.assertEqual(self.invoke('brim', 'compile', source, image)[0], 1)
        self.assertFalse(image.exists())
        self.assertEqual(sidecar.read_bytes(), b'preserve')

    def test_chain_pack_verify_run_and_unpack_exact_complete_images(self):
        from ultrawide import brim_wide
        sources = []
        for index, text in enumerate(brim.demo_lctl()):
            path = self.root / f'input-{index}.lctlb'
            path.write_text(text, encoding='utf-8', newline='\n')
            sources.append(path)
        packed = self.root / 'programs.brw'
        code, report = self.invoke('chain', 'pack', packed, *sources)
        self.assertEqual(code, 0, report)
        self.assertEqual(packed.stat().st_size, 2 * brim_wide.WORD_BYTES)
        code, report = self.invoke('chain', 'verify', packed)
        self.assertEqual(code, 0, report)
        self.assertEqual(report['words'], 2)
        code, report = self.invoke('chain', 'run', packed)
        self.assertEqual(code, 0, report)
        self.assertEqual(report['registers'][0]['bit_length'], 1048576)
        self.assertEqual(report['registers'][1]['bit_length'], 0)
        self.assertLess(len(json.dumps(report)), 16384)
        output = self.root / 'unpacked'
        code, report = self.invoke('chain', 'unpack', packed, output)
        self.assertEqual(code, 0, report)
        self.assertEqual(len(list(output.iterdir())), 8)
        for index, source in enumerate(sources):
            compiled = brim.compile_lctl(source.read_text())
            self.assertEqual((output / f'{index:04d}.brimg').read_bytes(), compiled.image)
            self.assertEqual((output / f'{index:04d}.brir').read_bytes(), compiled.brir)
            self.assertEqual((output / f'{index:04d}.lctlb').read_bytes(), source.read_bytes())
            self.assertEqual((output / f'{index:04d}.brword').stat().st_size, brim_wide.WORD_BYTES)
        self.assertEqual(self.invoke('chain', 'unpack', packed, output)[0], 1)

    def test_chain_invalid_data_creates_no_output_directory(self):
        packed = self.root / 'corrupt.brw'
        packed.write_bytes(b'not a chain')
        target = self.root / 'unpacked'
        self.assertEqual(self.invoke('chain', 'unpack', packed, target)[0], 1)
        self.assertFalse(target.exists())

    def test_combined_self_test_requires_all_three_profiles(self):
        code, report = self.invoke('self-test')
        self.assertEqual(code, 0, report)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['word_bits'], 1048576)
        self.assertEqual(set(report['profiles']), {'uwa', 'shs', 'brim_word_chain'})
        self.assertTrue(all(value['status'] == 'PASS' for value in report['profiles'].values()))


if __name__ == '__main__':
    unittest.main()
