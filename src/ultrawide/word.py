"""Exact unsigned words; overflow wraps modulo 2**bits.

These are software integers, not native CPU registers. Shifts are logical:
shifting by at least the word width returns zero, without reducing the count
modulo the width.
"""

from dataclasses import dataclass
import hashlib

DEFAULT_WORD_BITS = 1_048_576
MIN_WORD_BITS = 8
MAX_WORD_BITS = 1_048_576


class WordError(ValueError):
    """An operand or configured bound is invalid."""


def require_integer(value, minimum, maximum, label):
    """Reject booleans as well as out-of-range or non-integer values."""
    if type(value) is not int or not minimum <= value <= maximum:
        raise WordError(f'{label} must be an integer in {minimum}..{maximum}')
    return value


@dataclass(frozen=True, slots=True)
class Word:
    """A canonical unsigned value with an explicit, bounded width."""

    value: int
    bits: int = DEFAULT_WORD_BITS

    def __post_init__(self):
        require_integer(self.bits, MIN_WORD_BITS, MAX_WORD_BITS, 'Word width')
        if type(self.value) is not int or self.value < 0 or self.value.bit_length() > self.bits:
            raise WordError('Word value must be an unsigned integer that fits its width')

    @property
    def mask(self):
        return (1 << self.bits) - 1

    def _peer(self, other):
        if not isinstance(other, Word) or other.bits != self.bits:
            raise WordError('Both operands must be words with the same width')
        return other.value

    def add(self, other):
        return Word((self.value + self._peer(other)) & self.mask, self.bits)

    def sub(self, other):
        return Word((self.value - self._peer(other)) & self.mask, self.bits)

    def xor(self, other):
        return Word(self.value ^ self._peer(other), self.bits)

    def shl(self, count):
        require_integer(count, 0, MAX_WORD_BITS, 'Shift count')
        if count >= self.bits:
            return Word(0, self.bits)
        return Word((self.value << count) & self.mask, self.bits)

    def shr(self, count):
        require_integer(count, 0, MAX_WORD_BITS, 'Shift count')
        return Word(0 if count >= self.bits else self.value >> count, self.bits)

    def to_bytes(self):
        """Little-endian, fixed-size representation; unused top bits are zero."""
        return self.value.to_bytes((self.bits + 7) // 8, 'little')

    def digest(self):
        """Hash the stored bytes, not a typed identity including the bit width.

        Widths with the same byte length can have identical encodings and hashes.
        Use ``summary()['bits']`` together with its hash when comparing typed words.
        """
        return hashlib.sha256(self.to_bytes()).hexdigest()

    def summary(self):
        """Small JSON-safe evidence without converting the full word to decimal."""
        return {'bits': self.bits, 'bit_length': self.value.bit_length(),
                'low64_hex': f'{self.value & ((1 << 64) - 1):016x}', 'sha256': self.digest()}
