"""Recovered full wide-word ISA with a bounded LCTLC-WIDE execution extension.

This explicitly versioned extension is separate from stock quantum LCTL and
from the smaller UWA arithmetic demonstration. ``reference.py`` preserves the
recovered interpreter's exact bytes; hardening is implemented in ``engine``.
"""

from .reference import Instruction, Capability, Basic1048576Error, VMFault
from .engine import (Program, WideError, BoundedVM, bounds, from_instructions,
                     from_executable, from_assembly, emit, parse, seal,
                     compile_program, load_program, run, run_file, demo_program, self_test)

__all__ = ['Instruction', 'Capability', 'Basic1048576Error', 'VMFault', 'Program', 'WideError',
           'BoundedVM', 'bounds', 'from_instructions', 'from_executable', 'from_assembly',
           'emit', 'parse', 'seal', 'compile_program', 'load_program', 'run', 'run_file',
           'demo_program', 'self_test']
