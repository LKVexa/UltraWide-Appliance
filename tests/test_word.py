"""Independent arithmetic edge cases at full width and at small widths."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ultrawide.word import DEFAULT_WORD_BITS, MAX_WORD_BITS, Word, WordError


class WordTests(unittest.TestCase):
    def test_highest_bit_is_retained_at_full_width(self):
        top = Word(1 << (DEFAULT_WORD_BITS - 1))
        self.assertEqual(top.value.bit_length(), DEFAULT_WORD_BITS)
        serialized = top.to_bytes()
        self.assertEqual(len(serialized), 131072)
        self.assertEqual(serialized[-1], 128)
        self.assertFalse(any(serialized[:-1]))
        self.assertEqual(top.shr(DEFAULT_WORD_BITS - 1).value, 1)

    def test_full_width_add_wraps_at_highest_bit(self):
        top = Word(1 << (DEFAULT_WORD_BITS - 1))
        self.assertEqual(top.add(top).value, 0)
        self.assertEqual(top.add(Word(1)).value.bit_count(), 2)

    def test_full_width_underflow_fills_the_word(self):
        underflow = Word(0).sub(Word(1))
        self.assertEqual(underflow.value.bit_count(), DEFAULT_WORD_BITS)
        self.assertEqual(underflow.value.bit_length(), DEFAULT_WORD_BITS)
        self.assertTrue(all(byte == 255 for byte in underflow.to_bytes()))

    def test_xor_clears_a_high_bit_without_touching_low_bit(self):
        top = 1 << (DEFAULT_WORD_BITS - 1)
        self.assertEqual(Word(top | 1).xor(Word(top)).value, 1)

    def test_logical_shifts_do_not_wrap_the_count(self):
        self.assertEqual(Word(128, 8).shl(1).value, 0)
        self.assertEqual(Word(128, 8).shr(7).value, 1)
        for count in (8, 9, MAX_WORD_BITS):
            self.assertEqual(Word(255, 8).shl(count).value, 0)
            self.assertEqual(Word(255, 8).shr(count).value, 0)
        self.assertEqual(Word(53, 8).shl(0).value, 53)

    def test_small_width_known_arithmetic(self):
        self.assertEqual(Word(250, 8).add(Word(10, 8)).value, 4)
        self.assertEqual(Word(3, 8).sub(Word(5, 8)).value, 254)
        self.assertEqual(Word(0xAA, 8).xor(Word(0xCC, 8)).value, 0x66)

    def test_non_byte_aligned_width_has_zero_padding(self):
        word = Word(511, 9)
        self.assertEqual(word.to_bytes(), b'\xff\x01')
        self.assertEqual(word.add(Word(1, 9)).value, 0)

    def test_rejects_invalid_width_or_value_types(self):
        for bits in (7, MAX_WORD_BITS + 1, True, 8.0, '8'):
            with self.subTest(bits=bits), self.assertRaises(WordError):
                Word(0, bits)
        for value in (-1, 256, True, 1.0, '1'):
            with self.subTest(value=value), self.assertRaises(WordError):
                Word(value, 8)

    def test_rejects_mixed_widths_and_invalid_shifts(self):
        for method in ('add', 'sub', 'xor'):
            with self.assertRaises(WordError):
                getattr(Word(1, 8), method)(Word(1, 16))
        for count in (-1, MAX_WORD_BITS + 1, True, 1.0):
            with self.subTest(count=count), self.assertRaises(WordError):
                Word(1).shl(count)

    def test_digest_is_deterministic_and_distinguishes_storage_sizes(self):
        self.assertEqual(Word(1, 8).digest(), Word(1, 8).digest())
        self.assertEqual(len(Word(1).digest()), 64)
        self.assertNotEqual(Word(1, 8).digest(), Word(1, 16).digest())

    def test_digest_hashes_bytes_and_summary_preserves_width(self):
        nine = Word(1, 9)
        sixteen = Word(1, 16)
        self.assertEqual(nine.to_bytes(), sixteen.to_bytes())
        self.assertEqual(nine.digest(), sixteen.digest())
        self.assertNotEqual(nine.summary()['bits'], sixteen.summary()['bits'])


if __name__ == '__main__':
    unittest.main()
