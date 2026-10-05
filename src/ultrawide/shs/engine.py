"""Sealed eight-column classical ISA extension; not stock LCTL-C or a Python translator.

Execution authority is the byte-for-byte recovered BASIC Python reference.
The wrapper adds explicit budgets, cancellation, exact full-width decimal
rendering, and fail-closed handling of malformed host state and Unicode scalars.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import struct
import math
import time
from . import reference

MAGIC = 'LCTLC-WIDE/0.1'
COLUMNS = 'ID|LANE|OP|OUT|CTRL|IN|ARG|META'
MAX_INSTRUCTIONS = 256
MAX_SOURCE_BYTES = 68 * 1024 * 1024
MAX_SOURCE_LINES = 4096
MAX_LINE_CHARS = reference.WORD_BITS // 4 + 512
MAX_HEADER_BYTES = 65536
MAX_HEADER_DEPTH = 64
MAX_IMAGE_BYTES = len(reference.MAGIC) + 4 + MAX_HEADER_BYTES + MAX_INSTRUCTIONS * reference.INSTRUCTION_BYTES + 32
MAX_MEMORY_BYTES = 16 * 1024 * 1024
MAX_STEPS = 10000
MAX_OUTPUT_CHARS = 1024 * 1024
MAX_TIMEOUT_SECONDS = 120.0
REFERENCE_SHA256 = 'f7af5b056131fa3227da090670f547189f19c905d66c038e1cd54e59d7de4b0b'


class WideError(ValueError):
    pass


@dataclass(frozen=True)
class Program:
    instructions: tuple[reference.Instruction, ...]
    header_bytes: bytes


def bounds():
    return {'instructions': MAX_INSTRUCTIONS, 'source_bytes': MAX_SOURCE_BYTES,
            'source_lines': MAX_SOURCE_LINES, 'line_characters': MAX_LINE_CHARS,
            'header_bytes': MAX_HEADER_BYTES, 'header_depth': MAX_HEADER_DEPTH, 'image_bytes': MAX_IMAGE_BYTES,
            'memory_bytes': MAX_MEMORY_BYTES, 'steps': MAX_STEPS,
            'output_characters': MAX_OUTPUT_CHARS, 'timeout_seconds': MAX_TIMEOUT_SECONDS,
            'word_bits': reference.WORD_BITS, 'registers': reference.REGISTER_COUNT,
            'capability_slots': reference.CAPABILITY_COUNT, 'active_widths': list(reference.WIDTHS),
            'arithmetic_modes': list(reference.MODES), 'opcodes': sorted(reference.OPCODES)}


def _bounded_int(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise WideError(f'{label} must be an integer in {minimum}..{maximum}')
    return value


def read_bounded(path, limit):
    with Path(path).open('rb') as stream:
        blob = stream.read(limit + 1)
    if len(blob) > limit:
        raise WideError(f'Input exceeds the {limit}-byte limit')
    return blob


def _bounded_lines(source):
    if not isinstance(source, str) or len(source) > MAX_SOURCE_BYTES:
        raise WideError('Source exceeds limit or is not text')
    if not source.isascii() or any(c in source for c in '\v\f\x1c\x1d\x1e'):
        raise WideError('Source requires ASCII syntax and LF/CRLF/CR line endings')
    source = source.replace('\r\n', '\n').replace('\r', '\n')
    terminal_lf = source.endswith('\n')
    if source.count('\n') + (not terminal_lf) > MAX_SOURCE_LINES:
        raise WideError('Physical source line limit exceeded')
    lines = source.split('\n', MAX_SOURCE_LINES)
    if terminal_lf:
        lines.pop()
    if any(len(line) > MAX_LINE_CHARS for line in lines):
        raise WideError('Physical source line length limit exceeded')
    return lines


def _capabilities(values):
    if isinstance(values, (str, bytes)):
        raise WideError('Capabilities must be a bounded iterable of names')
    result = []
    for value in values:
        if len(result) >= 128 or not isinstance(value, str) or not 1 <= len(value) <= 256:
            raise WideError('Invalid capability names or capability limit exceeded')
        result.append(value)
    return result


def _check_header_depth(raw):
    """Bound JSON nesting independently of the host Python recursion limit."""
    depth = 0
    quoted = escaped = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > MAX_HEADER_DEPTH:
                raise WideError('Executable JSON header nesting limit exceeded')
        elif byte in (93, 125):
            depth -= 1


def _image_parts(blob):
    if not isinstance(blob, bytes) or len(blob) > MAX_IMAGE_BYTES:
        raise WideError('Executable exceeds image limit or is not bytes')
    start = len(reference.MAGIC) + 4
    if len(blob) < start + 32 or not blob.startswith(reference.MAGIC):
        raise WideError('Expected a BASIC B1048576E executable, not a Windows PE installer')
    size = struct.unpack('<I', blob[len(reference.MAGIC):start])[0]
    if not 1 <= size <= MAX_HEADER_BYTES or start + size + 32 > len(blob):
        raise WideError('Invalid or oversized executable header')
    code_size = len(blob) - start - size - 32
    if code_size % reference.INSTRUCTION_BYTES or not 1 <= code_size // reference.INSTRUCTION_BYTES <= MAX_INSTRUCTIONS:
        raise WideError(f'Image must contain 1..{MAX_INSTRUCTIONS} complete instructions')
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise WideError('Duplicate JSON header key: ' + key)
            result[key] = value
        return result
    def nonfinite(value):
        raise WideError('Non-finite JSON header value: ' + value)
    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise WideError('Non-finite JSON header number')
        return result
    _check_header_depth(blob[start:start + size])
    try:
        header = json.loads(blob[start:start + size], object_pairs_hook=unique_object, parse_constant=nonfinite,
                            parse_float=finite_float)
    except (ValueError, RecursionError, UnicodeError) as exc:
        raise WideError('Invalid executable header JSON: ' + str(exc)) from exc
    if not isinstance(header, dict) or header.get('architecture_version') != reference.ARCHITECTURE_VERSION:
        raise WideError('Expected BASIC architecture version ' + reference.ARCHITECTURE_VERSION)
    _bounded_int(header.get('entry_instruction'), 0, code_size // reference.INSTRUCTION_BYTES - 1, 'Entry')
    for key in ('format_version', 'profile_base', 'profile_exponent', 'word_bits', 'instruction_bytes', 'code_size'):
        if type(header.get(key)) is not int:
            raise WideError('Header ' + key + ' must be an integer')
    checked = reference.verify_executable(blob)
    caps = checked['header'].get('capabilities', [])
    if not isinstance(caps, list):
        raise WideError('Invalid capabilities header')
    _capabilities(caps)
    return checked, blob[start:start + size]


def from_executable(blob):
    checked, header = _image_parts(blob)
    return Program(tuple(reference.disassemble(checked['code'])), header)


def from_instructions(instructions, entry=0, capabilities=(), debug=None):
    items = []
    for ins in instructions:
        if len(items) >= MAX_INSTRUCTIONS:
            raise WideError('Instruction limit exceeded')
        _validate_instruction(ins)
        items.append(ins)
    if not items:
        raise WideError('At least one instruction is required')
    _bounded_int(entry, 0, len(items) - 1, 'Entry')
    capabilities = _capabilities(capabilities)
    blob = reference.build_executable(reference.assemble(items), entry_instruction=entry,
                                      capabilities=capabilities, debug=debug)
    return from_executable(blob)


def compile_program(program):
    if not isinstance(program, Program) or not isinstance(program.instructions, tuple) or not 1 <= len(program.instructions) <= MAX_INSTRUCTIONS:
        raise WideError('Invalid program or instruction count')
    if not isinstance(program.header_bytes, bytes) or len(program.header_bytes) > MAX_HEADER_BYTES:
        raise WideError('Invalid header')
    for ins in program.instructions:
        _validate_instruction(ins)
    code = reference.assemble(program.instructions)
    prefix = reference.MAGIC + struct.pack('<I', len(program.header_bytes)) + program.header_bytes + code
    blob = prefix + hashlib.sha256(prefix).digest()
    _image_parts(blob)
    return blob


def _validate_instruction(ins):
    if not isinstance(ins, reference.Instruction):
        raise WideError('Expected reference.Instruction values')
    if any(type(getattr(ins, name)) is not int for name in ('rd', 'ra', 'rb', 'width', 'immediate')):
        raise WideError('Instruction register, width and immediate fields must be exact integers')
    if not isinstance(ins.name, str) or not isinstance(ins.mode, str):
        raise WideError('Instruction opcode and mode must be strings')
    ins.encode()


def seal(body):
    lines = _bounded_lines(body)
    if any(line.startswith('@sha256') for line in lines):
        raise WideError('Remove an existing seal before resealing')
    body = '\n'.join(lines).rstrip('\n') + '\n'
    if len(body) + 73 > MAX_SOURCE_BYTES or len(lines) + 1 > MAX_SOURCE_LINES:
        raise WideError('Sealed source limit exceeded')
    return body + '@sha256 ' + hashlib.sha256(body.encode('utf-8')).hexdigest() + '\n'


def emit(program):
    compile_program(program)
    lines = [MAGIC, '# Classical extension; execution backend: recovered BASIC Python reference.',
             '@header ' + base64.b64encode(program.header_bytes).decode('ascii'), COLUMNS]
    for index, ins in enumerate(program.instructions):
        lines.append(f'I{index:04d}|cpu|{ins.name}|R{ins.rd}|R{ins.rb}|R{ins.ra}|0x{ins.immediate:x}|width={ins.width};mode={ins.mode}')
    return seal('\n'.join(lines) + '\n@end\n')


def _register(text):
    if not re.fullmatch(r'R(?:[0-9]|1[0-5])', text):
        raise WideError('Register must be R0..R15')
    return int(text[1:])


def _immediate(text):
    if not re.fullmatch(r'0x[0-9a-fA-F]{1,262144}', text):
        raise WideError('Immediate must be exact unsigned hexadecimal, at most 1,048,576 bits')
    return int(text, 16)


def parse(source):
    lines = _bounded_lines(source)
    if not lines or lines[0] != MAGIC:
        raise WideError('Expected ' + MAGIC + '; stock LCTL-C is a different language')
    if len(lines) < 6 or not re.fullmatch(r'@sha256 [0-9a-f]{64}', lines[-1]):
        raise WideError('Missing source SHA-256 seal')
    body = '\n'.join(lines[:-1]) + '\n'
    if hashlib.sha256(body.encode('utf-8')).hexdigest() != lines[-1][8:]:
        raise WideError('Source seal mismatch')
    content = [line for line in lines[1:-1] if line and not line.startswith('#')]
    if len(content) < 4 or not content[0].startswith('@header ') or content[1] != COLUMNS or content[-1] != '@end':
        raise WideError('Expected header, eight-column declaration, instructions and @end')
    if len(content[0][8:]) > ((MAX_HEADER_BYTES + 2) // 3) * 4:
        raise WideError('Encoded executable header limit exceeded')
    try:
        header = base64.b64decode(content[0][8:], validate=True)
    except Exception as exc:
        raise WideError('Header must be strict base64') from exc
    if not 1 <= len(header) <= MAX_HEADER_BYTES:
        raise WideError('Executable header limit exceeded')
    rows = content[2:-1]
    if not 1 <= len(rows) <= MAX_INSTRUCTIONS:
        raise WideError('Instruction limit exceeded')
    instructions = []
    for index, row in enumerate(rows):
        cells = row.split('|', 8)
        if len(cells) != 8:
            raise WideError('Instruction must have exactly eight cells')
        identity, lane, opcode, out, ctrl, incoming, arg, meta = cells
        if identity != f'I{index:04d}' or lane != 'cpu':
            raise WideError('Rows require sequential I0000 IDs and cpu lane')
        if opcode not in reference.OPCODES:
            raise WideError('Unsupported opcode: ' + opcode)
        match = re.fullmatch(r'width=([0-9]{1,7});mode=(wrapping|checked|saturating|trapping)', meta)
        if not match:
            raise WideError('META must be width=N;mode=MODE')
        ins = reference.Instruction(opcode, _register(out), _register(incoming), _register(ctrl),
                                    int(match[1]), match[2], _immediate(arg))
        ins.encode()
        instructions.append(ins)
    program = Program(tuple(instructions), header)
    compile_program(program)
    return program


def from_assembly(source):
    lines = _bounded_lines(source)
    counts = {'NOP': (0, 0), 'HALT': (0, 1), 'MOVI': (2, 3), 'MOV': (2, 3),
              'NOT': (2, 4), 'CMP': (2, 3), 'JMP': (1, 1), 'JZ': (1, 1), 'JNZ': (1, 1),
              'PUSH': (1, 1), 'POP': (1, 1), 'SVC': (1, 2), 'LOAD': (3, 4), 'STORE': (3, 4)}
    counts.update({name: (3, 5) for name in ('ADD', 'SUB', 'MUL', 'DIVU', 'MODU', 'AND', 'OR', 'XOR', 'SHL', 'SHR')})
    count = 0
    for raw in lines:
        text = raw.split(';', 1)[0].strip()
        if not text:
            continue
        if text.startswith('.'):
            fields = text.split(None, 1)
            if len(fields) != 2 or fields[0].lower() not in ('.capability', '.width', '.mode') or len(fields[1]) > 256:
                raise WideError('Invalid or oversized assembly directive')
            continue
        if text.endswith(':'):
            if not 1 <= len(text[:-1].strip()) <= 128:
                raise WideError('Invalid or oversized assembly label')
            continue
        count += 1
        tokens = [t for t in re.split(r'[\s,]+', text, maxsplit=6) if t]
        arity = counts.get(tokens[0].upper())
        if arity is None or not arity[0] <= len(tokens) - 1 <= arity[1]:
            raise WideError('Invalid assembly operation or operand count')
    if not 1 <= count <= MAX_INSTRUCTIONS:
        raise WideError('Assembly instruction count limit exceeded')
    from .assembly import parse_assembly
    try:
        ins, caps, labels = parse_assembly(source)
    except (IndexError, KeyError, OverflowError) as exc:
        raise WideError('Malformed assembly operands') from exc
    except reference.Basic1048576Error as exc:
        raise WideError('Assembly rejected: ' + str(exc)[:512]) from exc
    return from_instructions(ins, entry=labels.get('_start', 0), capabilities=caps, debug={'labels': labels})


def load_program(path):
    blob = read_bounded(path, MAX_SOURCE_BYTES)
    return _program_from_blob(blob)


def _program_from_blob(blob):
    if blob.startswith(reference.MAGIC):
        return from_executable(blob)
    text = blob.decode('utf-8')
    if text.startswith(MAGIC):
        return parse(text)
    return from_assembly(text)


def _decimal(value, check=lambda: None):
    """Exact decimal conversion without changing Python's global digit limit."""
    negative = value < 0
    value = abs(value)
    chunks = []
    radix = 10 ** 1000
    while value >= radix:
        check()
        value, part = divmod(value, radix)
        chunks.append(str(part).zfill(1000))
    return ('-' if negative else '') + str(value) + ''.join(reversed(chunks))


class BoundedVM(reference.Basic1048576VM):
    def __init__(self, memory_bytes=1048576, capabilities=(), cancel=None, max_output_chars=MAX_OUTPUT_CHARS,
                 timeout_seconds=30.0):
        _bounded_int(memory_bytes, reference.WORD_BYTES, MAX_MEMORY_BYTES, 'Memory bytes')
        _bounded_int(max_output_chars, 1, MAX_OUTPUT_CHARS, 'Output character limit')
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise WideError('Wall-time limit must be finite and in (0,120] seconds')
        if cancel is not None and not callable(cancel):
            raise WideError('Cancellation callback must be callable')
        super().__init__(memory_bytes=memory_bytes, capabilities=_capabilities(capabilities))
        self.cancel = cancel or (lambda: None)
        self.max_output_chars = max_output_chars
        self._output_count = 0
        self.timeout_seconds = float(timeout_seconds)
        self._deadline = None
        self._used = False

    def _check(self):
        if self.cancel():
            raise reference.VMFault('SHS-LIMIT: execution cancelled')
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise reference.VMFault('SHS-LIMIT: wall-time deadline exceeded')

    def _cap(self, index):
        cap = super()._cap(index)
        if (type(cap.base) is not int or type(cap.length) is not int or cap.base < 0 or cap.length < 0
                or cap.base > len(self.memory) or cap.length > len(self.memory) - cap.base):
            raise reference.VMFault('SHS-CAP: descriptor outside physical memory')
        return cap

    def _push(self, value):
        if type(self.state.sp) is not int or not 0 <= self.state.sp <= len(self.memory):
            raise reference.VMFault('SHS-STACK: invalid stack pointer')
        return super()._push(value)

    def _pop(self):
        if type(self.state.sp) is not int or not 0 <= self.state.sp <= len(self.memory):
            raise reference.VMFault('SHS-STACK: invalid stack pointer')
        return super()._pop()

    def _append_output(self, text):
        if self._output_count + len(text) > self.max_output_chars:
            raise reference.VMFault('LCTL-WIDE-LIMIT: output character limit exceeded')
        self.stdout.append(text)
        self._output_count += len(text)

    def _execute(self, ins):
        self._check()
        if ins.name == 'SVC' and ins.immediate in (1, 2, 3, 4):
            value = self.state.registers[ins.ra]
            if ins.immediate in (1, 2):
                text = _decimal(reference.signed(value) if ins.immediate == 2 else value, self._check)
            elif ins.immediate == 3:
                if self._output_count + 2 + reference.WORD_BYTES * 2 > self.max_output_chars:
                    raise reference.VMFault('LCTL-WIDE-LIMIT: output character limit exceeded')
                text = reference.full_hex(value)
            else:
                if value > 0x10FFFF:
                    raise reference.VMFault('B1048576-SVC: invalid Unicode scalar')
                if 0xD800 <= value <= 0xDFFF:
                    raise reference.VMFault('SHS-SVC: surrogate is not a Unicode scalar')
                text = chr(value)
            self._append_output(text)
        else:
            if ins.name == 'SVC' and ins.immediate not in (1, 2, 3, 4, 5):
                service = str(ins.immediate) if ins.immediate < (1 << 64) else '0x' + format(ins.immediate, 'x')[:32] + '...'
                raise reference.VMFault('B1048576-SVC: unsupported service ' + service)
            super()._execute(ins)

    def run(self, executable, max_steps=4096):
        _bounded_int(max_steps, 1, MAX_STEPS, 'Maximum steps')
        if self._used:
            raise WideError('A bounded VM instance can execute only one image')
        self._used = True
        self._deadline = time.monotonic() + self.timeout_seconds
        self._check()
        blob = read_bounded(executable, MAX_IMAGE_BYTES) if isinstance(executable, (str, Path)) else executable
        _image_parts(blob)
        self._check()
        result = super().run(blob, max_steps=max_steps)
        result['state'].pop('registers_preview', None)
        result['memory_sha256'] = hashlib.sha256(self.memory).hexdigest()
        result['evidence_marks'] = list(self.evidence_marks)
        result['language'] = MAGIC
        result['execution_backend'] = 'recovered BASIC-1048576 Python reference; bounded wrapper'
        result['reference_sha256'] = REFERENCE_SHA256
        result['schema'] = 'ultrawide.shs.execution.v1'
        result['limits'] = {'max_steps': max_steps, 'memory_bytes': len(self.memory),
                            'output_characters': self.max_output_chars, 'timeout_seconds': self.timeout_seconds}
        self._check()
        return result


def run(program, *, max_steps=4096, memory_bytes=1048576, capabilities=('console.write',), cancel=None,
        max_output_chars=MAX_OUTPUT_CHARS, timeout_seconds=30.0):
    vm = BoundedVM(memory_bytes=memory_bytes, capabilities=capabilities, cancel=cancel,
                   max_output_chars=max_output_chars, timeout_seconds=timeout_seconds)
    return vm.run(compile_program(program), max_steps=max_steps)


def run_file(path, max_steps=4096, memory_bytes=1048576, cancel=None, timeout_seconds=30.0):
    source = read_bounded(path, MAX_SOURCE_BYTES)
    program = _program_from_blob(source)
    # console.write grants only the reference's in-memory output buffer, never host I/O.
    result = run(program, memory_bytes=memory_bytes, cancel=cancel, max_steps=max_steps, timeout_seconds=timeout_seconds)
    result['source_sha256'] = hashlib.sha256(source).hexdigest()
    return result


def demo_program():
    i = reference.Instruction
    return from_instructions([
        i('MOVI', rd=0, immediate=1 << (reference.WORD_BITS - 1)),
        i('MOVI', rd=1, immediate=1),
        i('ADD', rd=2, ra=0, rb=0),
        i('STORE', rd=0, rb=0, immediate=0),
        i('LOAD', rd=3, rb=0, immediate=0),
        i('HALT')], debug={'purpose': 'top-bit carry and full-width memory roundtrip'})


def self_test(cancel=None):
    check = cancel or (lambda: None)
    check()
    actual = hashlib.sha256(Path(reference.__file__).read_bytes()).hexdigest()
    if actual != REFERENCE_SHA256:
        raise WideError('Recovered reference file SHA-256 mismatch')
    program = demo_program()
    image = compile_program(program)
    text = emit(program)
    rebuilt = compile_program(parse(text))
    if rebuilt != image:
        raise WideError('Lossless executable roundtrip failed')
    vm = BoundedVM(cancel=check)
    result = vm.run(rebuilt)
    top = 1 << (reference.WORD_BITS - 1)
    if vm.state.registers[0] != top or vm.state.registers[2] != 0 or vm.state.registers[3] != top:
        raise WideError('Full-width register or memory test failed')
    carry = result['trace'][2]['flags']
    if carry != {'Z': True, 'N': False, 'C': True, 'V': True}:
        raise WideError('Top-bit overflow flags failed')
    rejected = 0
    for invalid in [text.replace('ADD', 'ADZ', 1), text.replace('0x1', '0x2', 1), text + 'trailing\n']:
        try:
            parse(invalid)
        except (WideError, reference.Basic1048576Error):
            rejected += 1
    if rejected != 3:
        raise WideError('Tamper test failed')
    return {'status': 'PASS', 'language': MAGIC, 'word_bits': reference.WORD_BITS,
            'registers': reference.REGISTER_COUNT, 'checks_passed': 7,
            'scope': 'reference identity; exact source/image roundtrip; top-bit carry; memory; three tamper cases',
            'reference_sha256': actual, 'executable_sha256': hashlib.sha256(image).hexdigest(),
            'source_sha256': hashlib.sha256(text.encode()).hexdigest(), 'trace_sha256': result['trace_sha256'],
            'steps': result['steps'], 'translation_scope': 'Recovered full ISA virtualization; hosted runtime, not native compiler self-hosting'}
