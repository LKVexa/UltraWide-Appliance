"""Bounded, sealed UWA/0.1 programs.

Eight explicit columns make instruction data easy to inspect. This small custom
format supports straight-line demonstration programs, not existing BASIC/LCTL
programs, arbitrary Python, host commands, memory access, networking or an OS.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from .word import DEFAULT_WORD_BITS, MAX_WORD_BITS, MIN_WORD_BITS, Word, WordError, require_integer

MAGIC = 'UWA/0.1'
COLUMNS = 'ID|OP|OUT|A|B|IMM|WIDTH|NOTE'
REGISTER_COUNT = 8
MAX_INSTRUCTIONS = 64
MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_LINE_CHARS = (MAX_WORD_BITS + 3) // 4 + 256
MAX_STEPS = 64
OPS = frozenset({'NOP', 'MOVI', 'ADD', 'SUB', 'XOR', 'SHL', 'SHR', 'HALT'})


class ProgramError(ValueError):
    """Source data, a source seal or an execution bound is invalid."""


@dataclass(frozen=True, slots=True)
class Instruction:
    """All immediates fit the program width, including bounded shift counts.

    The standalone Word API permits larger shift counts than a narrow program
    can encode. For example, an 8-bit UWA immediate is at most 255.
    """
    opcode: str
    out: int | None = None
    a: int | None = None
    b: int | None = None
    immediate: int | None = None
    note: str = '_'


@dataclass(frozen=True, slots=True)
class Program:
    bits: int
    instructions: tuple[Instruction, ...]


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def _register(value):
    if value == '_':
        return None
    if not re.fullmatch(r'R[0-7]', value):
        raise ProgramError('Registers must be R0..R7 or _ where unused')
    return int(value[1:])


def _literal(value, bits):
    if value == '_':
        return None
    if value == 'mask':
        return (1 << bits) - 1
    if re.fullmatch(r'bit:[0-9]{1,7}', value):
        index = int(value[4:])
        if index >= bits:
            raise ProgramError('Bit literal index is outside the word')
        return 1 << index
    if re.fullmatch(r'0x[0-9a-fA-F]+', value):
        if len(value) - 2 > (bits + 3) // 4:
            raise ProgramError('Hexadecimal literal exceeds word width')
        number = int(value, 16)
    elif re.fullmatch(r'[0-9]{1,4096}', value):
        # Decimal input is deliberately capped below Python's default safety limit.
        number = int(value, 10)
    else:
        raise ProgramError('Use unsigned decimal, 0x hexadecimal, bit:N, mask or _')
    if number.bit_length() > bits:
        raise ProgramError('Literal does not fit the word width')
    return number


def validate(program):
    if not isinstance(program, Program):
        raise ProgramError('Expected Program data')
    require_integer(program.bits, MIN_WORD_BITS, MAX_WORD_BITS, 'Word width')
    if not isinstance(program.instructions, tuple) or not 1 <= len(program.instructions) <= MAX_INSTRUCTIONS:
        raise ProgramError(f'A program must contain 1..{MAX_INSTRUCTIONS} instructions')
    for index, ins in enumerate(program.instructions):
        if not isinstance(ins, Instruction) or not isinstance(ins.opcode, str) or ins.opcode not in OPS:
            raise ProgramError('Unknown instruction')
        if not isinstance(ins.note, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,64}', ins.note):
            raise ProgramError('Notes must be 1..64 letters, digits, underscores, dots, colons or hyphens')
        for value in (ins.out, ins.a, ins.b):
            if value is not None:
                require_integer(value, 0, REGISTER_COUNT - 1, 'Register')
        if ins.immediate is not None:
            if type(ins.immediate) is not int or ins.immediate < 0 or ins.immediate.bit_length() > program.bits:
                raise ProgramError('Immediate must be an unsigned integer fitting the word')
        supplied = (ins.out is not None, ins.a is not None, ins.b is not None, ins.immediate is not None)
        expected = {
            'NOP': (False, False, False, False), 'HALT': (False, False, False, False),
            'MOVI': (True, False, False, True),
            'ADD': (True, True, True, False), 'SUB': (True, True, True, False), 'XOR': (True, True, True, False),
            'SHL': (True, True, False, True), 'SHR': (True, True, False, True),
        }[ins.opcode]
        if supplied != expected:
            raise ProgramError(f'{ins.opcode} has missing or unexpected operands')
        if ins.opcode in ('SHL', 'SHR'):
            require_integer(ins.immediate, 0, MAX_WORD_BITS, 'Shift count')
        if ins.opcode == 'HALT' and index != len(program.instructions) - 1:
            raise ProgramError('HALT is allowed only as the final instruction')
    if program.instructions[-1].opcode != 'HALT':
        raise ProgramError('The final instruction must be HALT')
    return program


def _bounded_lines(source, maximum_lines):
    """Check physical line count before allocating a list of source lines."""
    if not isinstance(source, str) or len(source) > MAX_SOURCE_BYTES:
        raise ProgramError('Invalid source text or source too large')
    # UWA's grammar is ASCII. CRLF and CR are accepted only as line endings.
    if not source.isascii() or any(character in source for character in '\v\f\x1c\x1d\x1e'):
        raise ProgramError('Source must use ASCII syntax and LF, CRLF or CR line endings')
    source = source.replace('\r\n', '\n').replace('\r', '\n')
    terminal_lf = source.endswith('\n')
    line_count = source.count('\n') + (0 if terminal_lf else 1)
    if line_count > maximum_lines:
        raise ProgramError('Physical source line limit exceeded')
    # splitlines() also recognizes extra control separators; use only bounded LF.
    lines = source.split('\n', maximum_lines)
    if terminal_lf:
        lines.pop()
    if any(len(line) > MAX_LINE_CHARS for line in lines):
        raise ProgramError('Physical source line length limit exceeded')
    return lines


def seal(body):
    """Seal bounded normalized text; the checksum does not identify a signer."""
    lines = _bounded_lines(body, MAX_INSTRUCTIONS + 4)
    if any(line.startswith('@sha256') for line in lines):
        raise ProgramError('Remove the old seal before sealing source')
    body = '\n'.join(lines).rstrip('\n') + '\n'
    encoded = body.encode('utf-8')
    if len(encoded) + 73 > MAX_SOURCE_BYTES:
        raise ProgramError('Source too large')
    return body + '@sha256 ' + hashlib.sha256(encoded).hexdigest() + '\n'


def _print_literal(value, bits):
    if value is None:
        return '_'
    if value == (1 << bits) - 1:
        return 'mask'
    if value > 0 and value & (value - 1) == 0:
        return 'bit:' + str(value.bit_length() - 1)
    return hex(value)


def emit(program):
    validate(program)
    rows = [MAGIC, f'@word-bits {program.bits}', COLUMNS]
    for index, ins in enumerate(program.instructions):
        reg = lambda value: '_' if value is None else f'R{value}'
        rows.append('|'.join([f'I{index:04d}', ins.opcode, reg(ins.out), reg(ins.a), reg(ins.b),
                              _print_literal(ins.immediate, program.bits), str(program.bits), ins.note]))
    rows.append('@end')
    return seal('\n'.join(rows))


def parse(source):
    lines = _bounded_lines(source, MAX_INSTRUCTIONS + 5)
    if len(lines) < 6 or lines[0] != MAGIC:
        raise ProgramError('Expected custom ' + MAGIC + ' source')
    if not re.fullmatch(r'@sha256 [0-9a-f]{64}', lines[-1]):
        raise ProgramError('Source must end with a SHA-256 seal')
    body = '\n'.join(lines[:-1]) + '\n'
    if hashlib.sha256(body.encode('utf-8')).hexdigest() != lines[-1][8:]:
        raise ProgramError('Source seal mismatch')
    width = re.fullmatch(r'@word-bits ([0-9]{1,7})', lines[1])
    if not width or lines[2] != COLUMNS or lines[-2] != '@end':
        raise ProgramError('Expected word width, column header and @end')
    bits = require_integer(int(width[1]), MIN_WORD_BITS, MAX_WORD_BITS, 'Word width')
    rows = lines[3:-2]
    if not 1 <= len(rows) <= MAX_INSTRUCTIONS:
        raise ProgramError('Instruction count out of bounds')
    instructions = []
    for index, row in enumerate(rows):
        # One extra cell is sufficient to reject an oversized tuple, without
        # allocating a list for every delimiter in an adversarial source row.
        cells = row.split('|', 8)
        if len(cells) != 8:
            raise ProgramError('Every instruction must have exactly eight cells')
        identity, opcode, out, a, b, immediate, declared_bits, note = cells
        if identity != f'I{index:04d}' or declared_bits != str(bits):
            raise ProgramError('Instruction IDs must be sequential and widths must match')
        instructions.append(Instruction(opcode, _register(out), _register(a), _register(b), _literal(immediate, bits), note))
    return validate(Program(bits, tuple(instructions)))


def read_program(path):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_SOURCE_BYTES + 1)
    if len(data) > MAX_SOURCE_BYTES:
        raise ProgramError('Source file exceeds size limit')
    return parse(data.decode('utf-8'))


def execute(program, max_steps=MAX_STEPS, check_cancelled=None):
    """Execute only the stated arithmetic subset; no host actions or dynamic eval."""
    validate(program)
    require_integer(max_steps, 1, MAX_STEPS, 'Maximum steps')
    check = check_cancelled or (lambda: None)
    registers = [Word(0, program.bits) for _ in range(REGISTER_COUNT)]
    trace = []
    for index, ins in enumerate(program.instructions):
        check()
        if index >= max_steps:
            raise ProgramError('Instruction budget exhausted')
        if ins.opcode == 'MOVI':
            registers[ins.out] = Word(ins.immediate, program.bits)
        elif ins.opcode in ('ADD', 'SUB', 'XOR'):
            left, right = registers[ins.a], registers[ins.b]
            registers[ins.out] = getattr(left, ins.opcode.lower())(right)
        elif ins.opcode in ('SHL', 'SHR'):
            registers[ins.out] = getattr(registers[ins.a], ins.opcode.lower())(ins.immediate)
        trace.append({'step': index, 'op': ins.opcode, 'out': ins.out,
                      'out_sha256': registers[ins.out].digest() if ins.out is not None else None})
    check()
    return {'schema': 'ultrawide.execution.v1', 'language': MAGIC, 'word_bits': program.bits,
            'steps': len(trace), 'halted': True, 'registers': [word.summary() for word in registers],
            'program_sha256': hashlib.sha256(emit(program).encode('utf-8')).hexdigest(),
            'trace_sha256': hashlib.sha256(_canonical_json(trace)).hexdigest(), 'trace': trace}


def demo_program(bits=DEFAULT_WORD_BITS):
    require_integer(bits, MIN_WORD_BITS, MAX_WORD_BITS, 'Word width')
    return Program(bits, (
        Instruction('MOVI', out=0, immediate=1 << (bits - 1), note='highest-bit'),
        Instruction('MOVI', out=1, immediate=1, note='one'),
        Instruction('ADD', out=2, a=0, b=0, note='wrap-to-zero'),
        Instruction('SUB', out=3, a=2, b=1, note='wrap-to-mask'),
        Instruction('XOR', out=4, a=3, b=0, note='clear-highest-bit'),
        Instruction('SHR', out=5, a=0, immediate=bits - 1, note='move-highest-bit-to-lowest'),
        Instruction('HALT'),
    ))


def self_test():
    program = demo_program()
    encoded = emit(program)
    decoded = parse(encoded)
    if decoded != program:
        raise ProgramError('Source roundtrip failed')
    result = execute(decoded)
    width = program.bits
    expected = (1 << (width - 1), 1, 0, (1 << width) - 1, (1 << (width - 1)) - 1, 1, 0, 0)
    if [r['sha256'] for r in result['registers']] != [Word(v, width).digest() for v in expected]:
        raise ProgramError('Full-width arithmetic demonstration failed')
    if result != execute(decoded):
        raise ProgramError('Deterministic replay failed')
    return {'status': 'PASS', 'word_bits': width, 'language': MAGIC,
            'checks': ['source-roundtrip', 'highest-bit', 'wrapping-add', 'wrapping-subtract', 'xor', 'logical-right-shift', 'deterministic-replay'],
            'steps': result['steps'], 'program_sha256': result['program_sha256'], 'trace_sha256': result['trace_sha256'],
            'scope': 'generic software-word arithmetic scaffold; no existing ISA compatibility claim'}
