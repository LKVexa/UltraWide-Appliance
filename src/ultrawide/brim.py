"""Checked BR-480 images and explicit executable SHS/BRIM binding bundles.

BR-480 ISA 4.1 and BASIC-1048576 are distinct instruction sets. A bundle's
BRIM control image binds its lossless SHS payload; it does not execute that
payload as BR-480 instructions. All images accepted here are unsigned.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import io
import json
import math
from pathlib import Path
import re
import stat
import struct
import time
import zipfile

HEADER_BYTES = 80
INSTRUCTION_BYTES = 16
PROVENANCE_BYTES = 96
MAX_CODE = 256
MAX_DATA = 4096
MAX_IMAGE = HEADER_BYTES + MAX_CODE * INSTRUCTION_BYTES + MAX_DATA + PROVENANCE_BYTES
MAX_STEPS = 4096
WORD_BITS = 1048576
WORD_BYTES = WORD_BITS // 8
WORD_MASK = (1 << WORD_BITS) - 1
MAX_SOURCE = 68 * 1024 * 1024
MAX_EXECUTABLE = 33 * 1024 * 1024
MAX_BUNDLE = 104 * 1024 * 1024
MAX_TOTAL = MAX_SOURCE + MAX_EXECUTABLE + 128 * 1024
OPS = ('NOP', 'MOVI', 'MOV', 'JMP', 'JZ', 'JNZ', 'HALT', 'ADD', 'SUB', 'MUL',
       'DIVU', 'MODU', 'AND', 'OR', 'XOR', 'NOT', 'SHL', 'SHR', 'CMP', 'LOAD',
       'STORE', 'PUSH', 'POP', 'SVC', 'CALL', 'RET', 'YIELD', 'TRAP', 'CAPQ',
       'CHECKPOINT', 'LDX', 'STX')
MODES = ('WRAP', 'CHECKED', 'SATURATE', 'TRAPPING')
PURE_OPS = frozenset(OPS[:19])
SERVICE_CAPS = (16, 16, 16, 16, 48, 48, 144, 16, 80, 16, 16, 16, 48, 48, 16, 48, 16, 144, 16, 16)
MEMBERS = {'manifest.json': 16384, 'program.b104e': MAX_EXECUTABLE,
           'program.lctlc': MAX_SOURCE, 'control.brimg': MAX_IMAGE, 'control.brir': 65536}
BUNDLE_FORMAT = 'ULTRAWIDE-SHS-BRIM-BUNDLE/1'
BINDING_FORMAT = 'ULTRAWIDE-SHS-BRIM-BINDING/1'


class BrimError(ValueError):
    """Malformed, unsupported or resource-bounded image operation."""


def _fail(message):
    raise BrimError(message)


def _integer(value, minimum, maximum, name):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail('Invalid ' + name)
    return value


def _bytes(value, maximum, name):
    if not isinstance(value, bytes) or len(value) > maximum:
        _fail('Invalid or oversized ' + name)
    return value


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def _json(data, maximum):
    _bytes(data, maximum, 'JSON')
    try:
        text = data.decode('utf-8')
    except UnicodeError as exc:
        raise BrimError('Invalid JSON UTF-8') from exc
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '[{':
            depth += 1
            if depth > 64:
                _fail('JSON nesting exceeds 64')
        elif char in ']}':
            depth -= 1
    def integer(raw):
        if len(raw) > 20:
            _fail('Oversized JSON integer')
        return int(raw)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _fail('Duplicate JSON key')
            result[key] = value
        return result
    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_int=integer,
                           parse_float=lambda _: _fail('Floating JSON values are not part of the bundle schema'),
                           parse_constant=lambda _: _fail('Nonfinite JSON constant'))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise BrimError('Invalid bounded JSON') from exc
    if not isinstance(value, dict):
        _fail('Expected a JSON object')
    return value


@dataclass(frozen=True)
class BrimInstruction:
    op: str
    mode: int = 0
    rd: int = 0
    ra: int = 0
    rb: int = 0
    imm: int = 0


@dataclass(frozen=True)
class BrimImage:
    blob: bytes
    instructions: tuple[BrimInstruction, ...]
    data: bytes
    image_version: int
    requested_caps: int
    max_steps: int
    source_sha256: str
    brir_sha256: str
    feature_word: int

    def summary(self):
        return {'dialect': 'BR-480/ISA4.1/BRPV1.1', 'signed': False,
                'authentication': 'not provided', 'image_sha256': _sha(self.blob),
                'image_version': self.image_version, 'code_count': len(self.instructions),
                'data_bytes': len(self.data), 'requested_caps': self.requested_caps,
                'max_steps': self.max_steps, 'source_sha256': self.source_sha256,
                'brir_sha256': self.brir_sha256}


@dataclass(frozen=True)
class BrimArtifact:
    image: bytes
    brir: bytes


def _cap(ins):
    op = OPS.index(ins.op)
    if op in {0, 1, 2, 3, 4, 5, 6, 24, 25, 26, 27, 28}:
        return 1
    if 7 <= op <= 18:
        return 2
    if op in {19, 20, 30, 31}:
        return 4
    if op in {21, 22}:
        return 8
    if op == 29:
        return 32
    return SERVICE_CAPS[ins.imm]


def _analyze(instructions):
    if not 1 <= len(instructions) <= MAX_CODE:
        _fail('BRIM code count outside 1..256')
    successors = []
    needed = 0
    features = 0x38
    for index, ins in enumerate(instructions):
        if not isinstance(ins, BrimInstruction) or ins.op not in OPS:
            _fail('Invalid BRIM instruction')
        for name in ('rd', 'ra', 'rb'):
            _integer(getattr(ins, name), 0, 15, 'register')
        _integer(ins.mode, 0, 3, 'arithmetic mode')
        _integer(ins.imm, 0, (1 << 64) - 1, '64-bit immediate')
        if ins.op in {'JMP', 'JZ', 'JNZ', 'CALL', 'RET'} and ins.imm >= len(instructions):
            _fail('BRIM control target outside code')
        if ins.op in {'LOAD', 'STORE'} and (ins.imm > MAX_DATA - 8 or ins.imm & 7):
            _fail('BRIM direct memory operand')
        if ins.op in {'LDX', 'STX'} and ins.imm > 0xffffffff:
            _fail('BRIM indexed memory operand')
        if ins.op in {'SHL', 'SHR'} and ins.imm >= 1048576:
            _fail('BRIM shift count')
        if ins.op == 'SVC' and ins.imm >= len(SERVICE_CAPS):
            _fail('BRIM service ID')
        if ins.op == 'CAPQ' and ins.imm >= 16:
            _fail('BRIM capability query')
        if ins.op == 'RET' and not any(x.op == 'CALL' and i + 1 == ins.imm for i, x in enumerate(instructions)):
            _fail('BRIM RET target has no matching call site')
        needed |= _cap(ins)
        if ins.op in {'CALL', 'RET'}:
            features |= 1
        if ins.op in {'LDX', 'STX'}:
            features |= 2
        if ins.op in {'YIELD', 'TRAP', 'CAPQ', 'CHECKPOINT'}:
            features |= 4
        if ins.op in {'HALT', 'TRAP'}:
            succ = []
        elif ins.op in {'JMP', 'CALL', 'RET'}:
            succ = [ins.imm]
        elif ins.op in {'JZ', 'JNZ'}:
            if index + 1 == len(instructions):
                _fail('BRIM conditional fallthrough outside code')
            succ = [ins.imm, index + 1]
        elif index + 1 < len(instructions):
            succ = [index + 1]
        else:
            _fail('BRIM fallthrough outside code')
        successors.append(succ)
    depths = {0: (0, 0)}
    queue = deque([0])
    while queue:
        index = queue.popleft()
        ins = instructions[index]
        data_depth, call_depth = depths[index]
        data_depth += (ins.op == 'PUSH') - (ins.op == 'POP')
        call_depth += (ins.op == 'CALL') - (ins.op == 'RET')
        if not (0 <= data_depth <= 256 and 0 <= call_depth <= 64):
            _fail('BRIM stack bounds')
        for target in successors[index]:
            resulting = (data_depth, call_depth)
            if target in depths and depths[target] != resulting:
                _fail('BRIM stack depth mismatch at merge')
            if target not in depths:
                depths[target] = resulting
                queue.append(target)
    if len(depths) != len(instructions):
        _fail('BRIM unreachable code')
    terminating = {i for i, x in enumerate(instructions) if x.op in {'HALT', 'TRAP'}}
    for _ in instructions:
        expanded = terminating | {i for i, targets in enumerate(successors) if any(t in terminating for t in targets)}
        if expanded == terminating:
            break
        terminating = expanded
    if len(terminating) != len(instructions):
        _fail('BRIM control flow has no path to a terminal')
    return needed, features | 0x01010000, successors, depths


def parse_brim(blob: bytes) -> BrimImage:
    _bytes(blob, MAX_IMAGE, 'BRIM image')
    if len(blob) < HEADER_BYTES or blob[:4] != b'BRIM':
        _fail('BRIM magic or truncated header')
    if blob[4:7] != bytes((4, 1, 1)) or blob[8:14] != bytes((1, 20, 16, 16, 32, 4)):
        _fail('Unsupported BRIM ISA, ABI or geometry')
    if blob[7] & 2:
        _fail('Signed BRIM requires a cryptographic trust verifier; unsupported here')
    if blob[7] not in (4, 5) or struct.unpack_from('<H', blob, 14)[0] != HEADER_BYTES:
        _fail('BRIM flags or header size')
    version, requested, extension, count, data_size = struct.unpack_from('<IIIHH', blob, 16)
    if not version or not requested or requested & ~255 or extension != PROVENANCE_BYTES:
        _fail('BRIM authority fields')
    if not 1 <= count <= MAX_CODE or data_size > MAX_DATA:
        _fail('BRIM segment bounds')
    expected = HEADER_BYTES + count * INSTRUCTION_BYTES + data_size + PROVENANCE_BYTES
    if len(blob) != expected:
        _fail('BRIM exact length; trailers including BRTM are unsupported')
    if hashlib.sha256(blob[HEADER_BYTES:]).digest() != blob[32:64]:
        _fail('BRIM payload digest mismatch')
    instructions = []
    for offset in range(HEADER_BYTES, HEADER_BYTES + count * INSTRUCTION_BYTES, INSTRUCTION_BYTES):
        op, mode, rd, ra, rb, cap, reserved, immediate = struct.unpack_from('<BBBBBBHQ', blob, offset)
        if op >= len(OPS) or cap or reserved:
            _fail('BRIM instruction opcode or reserved fields')
        instructions.append(BrimInstruction(OPS[op], mode, rd, ra, rb, immediate))
    needed, features, _, _ = _analyze(instructions)
    provenance = blob[-PROVENANCE_BYTES:]
    if provenance[:8] != b'BRPV\x01\x01\x01\x04':
        _fail('BRPV magic or version')
    compiler_abi, authority_abi, steps, derived = struct.unpack_from('<IIII', provenance, 8)
    if compiler_abi != 0x40700 or authority_abi != 0x40700 or not steps:
        _fail('BRPV authority ABI or step budget')
    pversion, pfeatures = struct.unpack_from('<II', provenance, 88)
    if pversion != version or requested != needed or derived != needed or pfeatures != features:
        _fail('BRPV image, capability or feature binding')
    return BrimImage(blob, tuple(instructions), blob[HEADER_BYTES + count * INSTRUCTION_BYTES:-PROVENANCE_BYTES],
                     version, requested, steps, provenance[24:56].hex(), provenance[56:88].hex(), features)


def _brir(instructions, version, steps, language):
    needed, features, successors, depths = _analyze(instructions)
    if not isinstance(language, str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,80}', language):
        _fail('Invalid BRIR source-language identifier')
    lines = ['BRIR/1.1', f'unit=ultrawide.image|language={language}|isa=4.1|abi=1.0|features=0x{features & 0xffff:x}|image={version}|max_steps={steps}|declared=0x{needed:02x}|derived=0x{needed:02x}']
    for i, ins in enumerate(instructions):
        succ = ','.join(str(x) for x in successors[i]) or ins.op
        depth, calls = depths[i]
        source_line = i + 3 if language == 'LCTL-BRIM/1' else 0
        lines.append(f'{i}|A{i+1:03d}|{ins.op}|{MODES[ins.mode]}|R{ins.rd}|R{ins.ra}|R{ins.rb}|C0|{ins.imm}|need=0x{_cap(ins):02x}|succ={succ}|line={source_line}|stack={depth}|call={calls}')
    return ('\n'.join(lines) + '\n').encode('utf-8')


def create_brim(instructions, *, source: bytes, data: bytes = b'', image_version=1,
                max_steps=MAX_STEPS, source_language='BRIM-INSTRUCTIONS/1') -> BrimArtifact:
    """Encode actual BR-480 instructions and matching BRPV/BRIR; no compilation claim."""
    _bytes(source, MAX_SOURCE, 'source')
    _bytes(data, MAX_DATA, 'data segment')
    _integer(image_version, 1, 0xffffffff, 'image version')
    _integer(max_steps, 1, MAX_STEPS, 'host step budget')
    admitted = []
    for ins in instructions:
        if len(admitted) == MAX_CODE:
            _fail('Too many BRIM instructions')
        admitted.append(ins)
    needed, features, _, _ = _analyze(admitted)
    brir = _brir(admitted, image_version, max_steps, source_language)
    code = b''.join(struct.pack('<BBBBBBHQ', OPS.index(i.op), i.mode, i.rd, i.ra, i.rb, 0, 0, i.imm) for i in admitted)
    provenance = (b'BRPV\x01\x01\x01\x04' + struct.pack('<IIII', 0x40700, 0x40700, max_steps, needed)
                  + hashlib.sha256(source).digest() + hashlib.sha256(brir).digest()
                  + struct.pack('<II', image_version, features))
    payload = code + data + provenance
    header = bytearray(HEADER_BYTES)
    header[:14] = b'BRIM' + bytes((4, 1, 1, 5, 1, 20, 16, 16, 32, 4))
    struct.pack_into('<HIIIHH', header, 14, HEADER_BYTES, image_version, needed, PROVENANCE_BYTES, len(admitted), len(data))
    header[32:64] = hashlib.sha256(payload).digest()
    image = bytes(header) + payload
    verify_brim(image, source=source, brir=brir)
    return BrimArtifact(image, brir)


def verify_brim(blob, *, source=None, brir=None):
    image = parse_brim(blob)
    if source is not None and _sha(_bytes(source, MAX_SOURCE, 'source')) != image.source_sha256:
        _fail('BRIM source binding mismatch')
    if brir is not None and _sha(_bytes(brir, 65536, 'BRIR')) != image.brir_sha256:
        _fail('BRIM BRIR binding mismatch')
    return image


def run_brim(blob: bytes, *, registers=None, max_steps=None, cancel=None, timeout_seconds=30):
    """Run pure BR-480 CONTROL/ARITH with full 1,048,576-bit unsigned words.

    Registers may be handed off between images; flags start at zero per image.
    CMP owns comparison flags. BRIM CHECKED wraps and records overflow;
    TRAPPING faults. SHL/SHR use an immediate shift count. Full integer registers
    are returned for host handoff; JSON-facing callers should use their hashes.
    """
    image = parse_brim(blob)
    if cancel is not None and not callable(cancel):
        _fail('BRIM cancellation callback must be callable')
    if image.requested_caps & ~3 or image.data or any(i.op not in PURE_OPS for i in image.instructions):
        _fail('BRIM execution admits only pure CONTROL/ARITH without data')
    budget = _integer(image.max_steps, 1, MAX_STEPS, 'BRIM execution budget')
    if max_steps is not None:
        budget = min(budget, _integer(max_steps, 1, MAX_STEPS, 'host step budget'))
    if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 300:
        _fail('BRIM deadline must be positive and at most 300 seconds')
    deadline = time.monotonic() + timeout_seconds
    regs = [0] * 16
    if registers is not None:
        if not isinstance(registers, dict) or len(registers) > 16:
            _fail('Register inputs must be a bounded index/value mapping')
        for index, value in registers.items():
            regs[_integer(index, 0, 15, 'input register')] = _integer(value, 0, WORD_MASK, 'unsigned WIDE input')
    flags = pc = 0
    trace = []
    for step in range(1, budget + 1):
        if cancel is not None and cancel():
            _fail('BRIM execution cancelled')
        if time.monotonic() >= deadline:
            _fail('BRIM execution deadline exceeded')
        if not 0 <= pc < len(image.instructions):
            _fail('BRIM program counter outside image')
        ins = image.instructions[pc]
        op, a, b, value, following = ins.op, regs[ins.ra], regs[ins.rb], None, pc + 1
        if op == 'MOVI': value = ins.imm
        elif op == 'MOV': value = a
        elif op == 'ADD': value = a + b
        elif op == 'SUB': value = a - b
        elif op == 'MUL': value = a * b
        elif op in {'DIVU', 'MODU'}:
            if not b: _fail('BRIM division by zero')
            value = a // b if op == 'DIVU' else a % b
        elif op == 'AND': value = a & b
        elif op == 'OR': value = a | b
        elif op == 'XOR': value = a ^ b
        elif op == 'NOT': value = WORD_MASK ^ a
        elif op in {'SHL', 'SHR'}:
            value = a << ins.imm if op == 'SHL' else a >> ins.imm
        elif op == 'CMP': flags = (flags & ~7) | (1 if a == b else 2 if a < b else 4)
        elif op == 'JMP': following = ins.imm
        elif op == 'JZ' and flags & 1: following = ins.imm
        elif op == 'JNZ' and not flags & 1: following = ins.imm
        if op in {'ADD', 'SUB', 'MUL', 'SHL', 'SHR'}:
            underflow = value < 0
            overflow = value.bit_length() > WORD_BITS
            flags &= ~24
            if underflow or overflow: flags |= 16
            if (op == 'SUB' and not underflow) or (op != 'SUB' and overflow): flags |= 8
            if underflow or overflow:
                if ins.mode == 3: _fail('BRIM arithmetic trap')
                if ins.mode == 2: value = 0 if underflow else WORD_MASK
                else: value &= WORD_MASK
        if value is not None:
            if value < 0 or value.bit_length() > WORD_BITS:
                _fail('BRIM intermediate exceeds WIDE word bound')
            regs[ins.rd] = value
        trace.append({'step': step, 'pc': pc, 'op': op, 'next_pc': following, 'flags': flags})
        if op == 'HALT':
            return {'status': 'HALTED', 'profile': 'BR-480 pure CONTROL/ARITH WIDE', 'word_bits': WORD_BITS,
                    'registers': regs, 'flags': flags, 'steps': step, 'trace': trace,
                    'register_sha256': [_sha(x.to_bytes(WORD_BYTES, 'little')) for x in regs],
                    'register_bit_length': [x.bit_length() for x in regs],
                    'trace_sha256': _sha(_canonical(trace)), 'image_sha256': _sha(blob)}
        pc = following
    _fail('BRIM execution step budget exhausted')


LCTL_BRIM_HEADER = 'LCTL-BRIM/1\nID|LANE|OP|OUT|CTRL|IN|ARG|META\n'


def emit_lctl(instructions) -> str:
    """Emit an explicit BRIM-target eight-column dialect, not a legacy compiler."""
    admitted = []
    for ins in instructions:
        if len(admitted) == MAX_CODE:
            _fail('Too many LCTL-BRIM instructions')
        admitted.append(ins)
    _analyze(admitted)
    if any(i.op not in PURE_OPS for i in admitted):
        _fail('LCTL-BRIM/1 compiler admits only the pure WIDE execution subset')
    rows = [f'I{n:04d}|main|{i.op}|R{i.rd}|C0|R{i.ra}>R{i.rb}|u64:{i.imm}|mode={MODES[i.mode]};width=WIDE'
            for n, i in enumerate(admitted)]
    return LCTL_BRIM_HEADER + '\n'.join(rows) + '\n'


def parse_lctl(text: str) -> tuple[BrimInstruction, ...]:
    """Parse only LCTL-BRIM/1; reject other LCTL dialects instead of reinterpreting."""
    if not isinstance(text, str) or len(text) > 65536 or '\r' in text or not text.startswith(LCTL_BRIM_HEADER) or not text.endswith('\n'):
        _fail('Expected bounded canonical LCTL-BRIM/1 with LF line endings')
    body = text[len(LCTL_BRIM_HEADER):-1]
    if body.count('\n') >= MAX_CODE:
        _fail('LCTL-BRIM code count exceeds 256')
    instructions = []
    for index, line in enumerate(body.split('\n')):
        if len(line) > 512:
            _fail('LCTL-BRIM row exceeds limit')
        cells = line.split('|', 8)
        if len(cells) != 8:
            _fail('LCTL-BRIM requires eight columns')
        ident, lane, op, out, cap, inputs, arg, meta = cells
        if ident != f'I{index:04d}' or lane != 'main' or cap != 'C0' or op not in PURE_OPS:
            _fail('LCTL-BRIM ID, lane, capability or opcode')
        reg = re.fullmatch(r'R([0-9]|1[0-5])', out)
        operands = re.fullmatch(r'R([0-9]|1[0-5])>R([0-9]|1[0-5])', inputs)
        immediate = re.fullmatch(r'u64:(0|[1-9][0-9]{0,19})', arg)
        mode = re.fullmatch(r'mode=(WRAP|CHECKED|SATURATE|TRAPPING);width=WIDE', meta)
        if not all((reg, operands, immediate, mode)):
            _fail('LCTL-BRIM operands, arithmetic mode or WIDE geometry')
        instructions.append(BrimInstruction(op, MODES.index(mode[1]), int(reg[1]), int(operands[1]), int(operands[2]), int(immediate[1])))
    _analyze(instructions)
    if emit_lctl(instructions) != text:
        _fail('Noncanonical LCTL-BRIM source')
    return tuple(instructions)


def compile_lctl(text: str, *, image_version=1, max_steps=MAX_STEPS) -> BrimArtifact:
    """Compile the explicit LCTL-BRIM/1 pure-WIDE subset into actual BR-480 bytes."""
    instructions = parse_lctl(text)
    return create_brim(instructions, source=text.encode('utf-8'), image_version=image_version,
                       max_steps=max_steps, source_language='LCTL-BRIM/1')


def demo_lctl() -> tuple[str, str]:
    """Two complete programs: create the highest bit, then wrap its sum in R1."""
    return (emit_lctl((BrimInstruction('MOVI', rd=0, imm=1),
                       BrimInstruction('SHL', rd=0, ra=0, imm=WORD_BITS - 1),
                       BrimInstruction('HALT'))),
            emit_lctl((BrimInstruction('ADD', rd=1, ra=0, rb=0), BrimInstruction('HALT'))))


def self_test(cancel=None):
    first, second = demo_lctl()
    artifacts = [compile_lctl(source, max_steps=16) for source in (first, second)]
    for source, artifact in zip((first, second), artifacts):
        verify_brim(artifact.image, source=source.encode('utf-8'), brir=artifact.brir)
    one = run_brim(artifacts[0].image, cancel=cancel)
    two = run_brim(artifacts[1].image, registers=dict(enumerate(one['registers'])), cancel=cancel)
    if one['registers'][0] != 1 << (WORD_BITS - 1) or two['registers'][1] != 0 or two['flags'] != 24:
        _fail('BRIM full-width self-test failed')
    return {'status': 'PASS', 'dialect': 'BR-480 ISA4.1 / LCTL-BRIM/1', 'word_bits': WORD_BITS,
            'checks': ['LCTL compile', 'BRPV source/BRIR bindings', 'highest-bit immediate shift',
                       'full-width register handoff', 'wrapping add', 'carry/overflow flags'],
            'image_sha256': [_sha(x.image) for x in artifacts],
            'final_register_sha256': two['register_sha256'], 'final_flags': two['flags']}


def _claims(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 12:
        _fail('Provenance must contain at most 12 textual claims')
    for key, content in value.items():
        if not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9_.-]{0,47}', key):
            _fail('Invalid provenance key')
        if not isinstance(content, str) or len(content.encode('utf-8')) > 256 or any(ord(c) < 32 for c in content):
            _fail('Invalid provenance claim')
    return dict(value)


@dataclass(frozen=True)
class VerifiedBundle:
    executable: bytes
    lctl: str
    control: BrimImage
    provenance: dict
    sha256: str

    def summary(self):
        return {'format': BUNDLE_FORMAT, 'status': 'VERIFIED', 'bundle_sha256': self.sha256,
                'execution': 'BASIC-1048576', 'brim_role': 'binding-control',
                'executable_sha256': _sha(self.executable), 'lctl_sha256': _sha(self.lctl.encode('utf-8')),
                'max_steps': self.control.max_steps, 'authentication': 'not provided',
                'provenance': dict(self.provenance)}


def build_bundle(executable: bytes, *, lctl=None, provenance=None, max_steps=MAX_STEPS) -> bytes:
    """Package exact native SHS bytes with reversible LCTL and genuine BRIM binding."""
    from .shs import from_executable, emit, parse, compile_program
    _bytes(executable, MAX_EXECUTABLE, 'SHS executable')
    _integer(max_steps, 1, MAX_STEPS, 'bundle step budget')
    program = from_executable(executable)
    text = emit(program) if lctl is None else lctl
    if not isinstance(text, str) or len(text) > MAX_SOURCE:
        _fail('Invalid or oversized LCTL source')
    source = _bytes(text.encode('utf-8'), MAX_SOURCE, 'LCTL source')
    if compile_program(parse(text)) != executable:
        _fail('LCTL does not reproduce the exact SHS executable')
    claims = _claims(provenance)
    binding = {'format': BINDING_FORMAT, 'execution': 'BASIC-1048576',
               'brim_role': 'binding-control', 'lctl_sha256': _sha(source),
               'executable_sha256': _sha(executable), 'max_steps': max_steps, 'provenance': claims}
    artifact = create_brim([BrimInstruction('HALT')], source=source, data=_canonical(binding),
                           max_steps=max_steps, source_language='ULTRAWIDE-SHS-BINDING/1')
    files = {'program.b104e': executable, 'program.lctlc': source,
             'control.brimg': artifact.image, 'control.brir': artifact.brir}
    manifest = {'format': BUNDLE_FORMAT, 'members': {name: {'bytes': len(data), 'sha256': _sha(data)} for name, data in files.items()}}
    files['manifest.json'] = _canonical(manifest)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(files):
            item = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.create_system = 3
            item.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(item, files[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
    blob = output.getvalue()
    _bytes(blob, MAX_BUNDLE, 'bundle')
    verify_bundle(blob)
    return blob


def _unpack(blob):
    _bytes(blob, MAX_BUNDLE, 'bundle')
    # Canonical single-disk ZIP only; no prefix/trailer, ZIP64 or comment carrier.
    if not blob.startswith(b'PK\x03\x04') or len(blob) < 22 or blob[-22:-18] != b'PK\x05\x06':
        _fail('Bundle must be an ordinary ZIP without prefix, trailer or comment')
    _, disk, central_disk, disk_count, total_count, central_size, central_offset, comment_size = struct.unpack('<4sHHHHIIH', blob[-22:])
    if disk or central_disk or comment_size or disk_count != 5 or total_count != 5 or central_offset + central_size != len(blob) - 22:
        _fail('Bundle ZIP directory bounds')
    files = {}
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            entries = archive.infolist()
            if len(entries) != len(MEMBERS) or {i.filename for i in entries} != set(MEMBERS):
                _fail('Bundle requires exactly five unique fixed member names')
            for item in entries:
                if item.flag_bits & 1 or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    _fail('Encrypted or unsupported compressed bundle member')
                mode = item.external_attr >> 16
                if item.is_dir() or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)) or item.extra or item.comment:
                    _fail('Bundle members must be ordinary files without ZIP extensions')
                limit = MEMBERS[item.filename]
                if not 0 <= item.file_size <= limit or not 0 <= item.compress_size <= MAX_BUNDLE:
                    _fail('Bundle declared member size exceeds limit')
                total += item.file_size
                if total > MAX_TOTAL:
                    _fail('Bundle total decompressed size exceeds limit')
                with archive.open(item) as stream:
                    data = stream.read(limit + 1)
                    if len(data) != item.file_size or len(data) > limit or stream.read(1):
                        _fail('Bundle decompressed member size mismatch')
                files[item.filename] = data
    except (OSError, ValueError, RuntimeError, EOFError, zipfile.BadZipFile) as exc:
        raise BrimError('Invalid bounded ZIP bundle: ' + str(exc)) from exc
    return files


def verify_bundle(blob: bytes) -> VerifiedBundle:
    from .shs import parse, compile_program
    files = _unpack(blob)
    manifest = _json(files['manifest.json'], MEMBERS['manifest.json'])
    expected_names = set(MEMBERS) - {'manifest.json'}
    if set(manifest) != {'format', 'members'} or manifest['format'] != BUNDLE_FORMAT or not isinstance(manifest['members'], dict) or set(manifest['members']) != expected_names:
        _fail('Invalid bundle manifest schema')
    for name in expected_names:
        record = manifest['members'][name]
        if (not isinstance(record, dict) or set(record) != {'bytes', 'sha256'}
                or type(record['bytes']) is not int or not isinstance(record['sha256'], str)
                or record != {'bytes': len(files[name]), 'sha256': _sha(files[name])}):
            _fail('Bundle member hash or size mismatch: ' + name)
    control = verify_brim(files['control.brimg'], source=files['program.lctlc'], brir=files['control.brir'])
    if control.instructions != (BrimInstruction('HALT'),) or control.requested_caps != 1:
        _fail('Bundle control image must contain only its binding HALT')
    _integer(control.max_steps, 1, MAX_STEPS, 'bundle step budget')
    if files['control.brir'] != _brir(control.instructions, control.image_version, control.max_steps, 'ULTRAWIDE-SHS-BINDING/1'):
        _fail('Bundle BRIR does not describe the control image')
    binding = _json(control.data, MAX_DATA)
    keys = {'format', 'execution', 'brim_role', 'lctl_sha256', 'executable_sha256', 'max_steps', 'provenance'}
    if set(binding) != keys or binding['format'] != BINDING_FORMAT or binding['execution'] != 'BASIC-1048576' or binding['brim_role'] != 'binding-control':
        _fail('Invalid SHS/BRIM binding schema')
    if binding['lctl_sha256'] != _sha(files['program.lctlc']) or binding['executable_sha256'] != _sha(files['program.b104e']):
        _fail('BRIM binding does not identify these exact SHS payloads')
    if type(binding['max_steps']) is not int or binding['max_steps'] != control.max_steps:
        _fail('Bundle budget binding mismatch')
    claims = _claims(binding['provenance'])
    try:
        text = files['program.lctlc'].decode('utf-8')
        if compile_program(parse(text)) != files['program.b104e']:
            _fail('Bundle LCTL does not reproduce its exact SHS executable')
    except (UnicodeError, ValueError) as exc:
        raise BrimError('Bundle SHS/LCTL verification failed: ' + str(exc)) from exc
    return VerifiedBundle(files['program.b104e'], text, control, claims, _sha(blob))


def read_bundle(path) -> VerifiedBundle:
    with Path(path).open('rb') as stream:
        blob = stream.read(MAX_BUNDLE + 1)
    return verify_bundle(blob)


def run_bundle(blob: bytes, *, max_steps=None, memory_bytes=1048576, capabilities=(), cancel=None,
               timeout_seconds=30, max_output_chars=1048576):
    """Verify every binding, then execute the full SHS payload with caller authority."""
    from .shs import BoundedVM
    bundle = verify_bundle(blob)
    budget = bundle.control.max_steps
    if max_steps is not None:
        budget = min(budget, _integer(max_steps, 1, MAX_STEPS, 'host step budget'))
    vm = BoundedVM(memory_bytes=memory_bytes, capabilities=capabilities, cancel=cancel,
                   timeout_seconds=timeout_seconds, max_output_chars=max_output_chars)
    result = vm.run(bundle.executable, max_steps=budget)
    return {'bundle': bundle.summary(), 'execution': result}
