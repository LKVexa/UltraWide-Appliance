"""Small, independent ultra-wide integer scaffold using only Python's standard library.

UWA/0.1 is a custom demonstration format. It is not compatible with an existing
operating-system ISA or a general-purpose language runtime.
"""

from .word import DEFAULT_WORD_BITS, MAX_WORD_BITS, Word, WordError

__all__ = ['DEFAULT_WORD_BITS', 'MAX_WORD_BITS', 'Word', 'WordError']
__version__ = '0.1.0'
