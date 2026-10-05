"""Ultra-wide virtual appliance; BASIC machine, LCTL-WIDE, and BRIM tooling.

The original UWA/0.1 demonstration API remains available for older examples.
The source-faithful BASIC machine is exported separately by ``ultrawide.shs``.
"""

from .word import DEFAULT_WORD_BITS, MAX_WORD_BITS, Word, WordError

__all__ = ['DEFAULT_WORD_BITS', 'MAX_WORD_BITS', 'Word', 'WordError']
__version__ = '0.2.0'
