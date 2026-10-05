"""Pack complete BRIM programs inside linked 1,048,576-bit words.

BRWWORD/1 is a transport and execution-chain extension, not a new BR-480 ISA.
Every physical word is exactly 131072 bytes and contains one complete image,
its source and BRIR, a 256-byte header, and checked zero padding. No image is
fragmented. Hashes establish integrity, not an author or signature authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import struct
import time

from . import brim

MAGIC = b'BRWWORD\0'
FORMAT = 'BRWWORD/1'
WORD_BITS = 1048576
WORD_BYTES = WORD_BITS // 8
HEADER_BYTES = 256
MAX_WORDS = 64
MAX_CHAIN_BYTES = MAX_WORDS * WORD_BYTES
MAX_CHAIN_STEPS = 10000
MAX_TIMEOUT_SECONDS = 120.0
DOMAIN = b'UltraWide:BRWWORD:manifest:v1\0'
ZERO_HASH = bytes(32)


class WordChainError(ValueError):
    pass


def _integer(value, minimum, maximum, name):
    if type(value) is not int or not minimum <= value <= maximum:
        raise WordChainError(f'{name} must be an integer in {minimum}..{maximum}')
    return value


def _hash(data):
    return hashlib.sha256(data).digest()


@dataclass(frozen=True)
class PackedProgram:
    image: bytes
    source: bytes
    brir: bytes


@dataclass(frozen=True)
class WordChain:
    programs: tuple[PackedProgram, ...]
    max_steps: int
    chain_id: str
    chain_sha256: str
    word_sha256: tuple[str, ...]

    def summary(self):
        return {
            'status': 'VERIFIED', 'format': FORMAT, 'word_bits': WORD_BITS,
            'word_bytes': WORD_BYTES, 'words': len(self.programs),
            'bytes': len(self.programs) * WORD_BYTES, 'max_steps': self.max_steps,
            'chain_id': self.chain_id, 'chain_sha256': self.chain_sha256,
            'authentication': 'unsigned; hashes establish integrity only',
            'runtime_profile': 'BR-480 unsigned full-width pure arithmetic and control',
            'records': [
                {'index': index, 'word_sha256': self.word_sha256[index],
                 'image_sha256': _hash(item.image).hex(), 'image_bytes': len(item.image),
                 'source_sha256': _hash(item.source).hex(), 'source_bytes': len(item.source),
                 'brir_sha256': _hash(item.brir).hex(), 'brir_bytes': len(item.brir),
                 'padding_bytes': WORD_BYTES - HEADER_BYTES - len(item.image) - len(item.source) - len(item.brir)}
                for index, item in enumerate(self.programs)
            ],
        }


def _validate_program(item):
    if not isinstance(item, PackedProgram):
        raise WordChainError('Expected PackedProgram(image, source, brir)')
    if any(not isinstance(value, bytes) or not value for value in (item.image, item.source, item.brir)):
        raise WordChainError('Image, source and BRIR must be nonempty bytes')
    if HEADER_BYTES + len(item.image) + len(item.source) + len(item.brir) > WORD_BYTES:
        raise WordChainError('Complete image, source and BRIR do not fit one ultra-wide word; fragmentation is not allowed')
    image = brim.verify_brim(item.image, source=item.source, brir=item.brir)
    try:
        rebuilt = brim.compile_lctl(item.source.decode('utf-8'), image_version=image.image_version,
                                     max_steps=image.max_steps)
    except (ValueError, UnicodeError) as exc:
        raise WordChainError('Packed program source must compile under LCTL-BRIM/1') from exc
    if rebuilt.image != item.image or rebuilt.brir != item.brir:
        raise WordChainError('Packed image/BRIR do not match actual compilation of their source')
    return image


def _manifest(programs, steps):
    digest = hashlib.sha256(DOMAIN + struct.pack('<II', len(programs), steps))
    for item in programs:
        for part in (item.image, item.source, item.brir):
            digest.update(struct.pack('<I', len(part)))
            digest.update(_hash(part))
    return digest.digest()


def pack_words(programs, *, max_steps=MAX_CHAIN_STEPS):
    """Pack checked complete images; a program cannot spill into the next word."""
    _integer(max_steps, 1, MAX_CHAIN_STEPS, 'Total step budget')
    items = []
    for item in programs:
        if len(items) >= MAX_WORDS:
            raise WordChainError('At most 64 program words may be chained')
        _validate_program(item)
        items.append(item)
    if not items:
        raise WordChainError('At least one complete program image is required')
    identity = _manifest(items, max_steps)
    words = []
    previous = ZERO_HASH
    for index, item in enumerate(items):
        payload = item.image + item.source + item.brir
        header = bytearray(HEADER_BYTES)
        header[:8] = MAGIC
        struct.pack_into('<HHIIIIIII', header, 8, 1, HEADER_BYTES, WORD_BITS,
                         index, len(items), len(item.image), len(item.source), len(item.brir), max_steps)
        header[40:72] = identity
        header[72:104] = previous
        header[104:136] = _hash(item.image)
        header[136:168] = _hash(item.source)
        header[168:200] = _hash(item.brir)
        header[200:232] = _hash(payload)
        word = bytes(header) + payload + bytes(WORD_BYTES - HEADER_BYTES - len(payload))
        words.append(word)
        previous = _hash(word)
    return b''.join(words)


def compile_chain(sources, *, max_steps=MAX_CHAIN_STEPS):
    """Compile supported columned LCTL programs and pack one program per word."""
    items = []
    for source in sources:
        if len(items) >= MAX_WORDS:
            raise WordChainError('At most 64 LCTL programs may be chained')
        if not isinstance(source, str) or len(source) > WORD_BYTES - HEADER_BYTES:
            raise WordChainError('Source cannot fit in one program word')
        data = source.encode('utf-8')
        if len(data) > WORD_BYTES - HEADER_BYTES:
            raise WordChainError('Encoded source cannot fit in one program word')
        artifact = brim.compile_lctl(source)
        items.append(PackedProgram(artifact.image, data, artifact.brir))
    return pack_words(items, max_steps=max_steps)


def verify_chain(blob):
    """Check the complete chain before returning any admitted program."""
    if not isinstance(blob, bytes) or not WORD_BYTES <= len(blob) <= MAX_CHAIN_BYTES or len(blob) % WORD_BYTES:
        raise WordChainError('Chain must contain 1..64 complete 131072-byte words')
    count = len(blob) // WORD_BYTES
    programs, hashes = [], []
    previous = ZERO_HASH
    identity = None
    total_budget = None
    for index in range(count):
        word = blob[index * WORD_BYTES:(index + 1) * WORD_BYTES]
        if word[:8] != MAGIC:
            raise WordChainError('Word magic mismatch')
        version, header_size, bits, stored_index, stored_count, image_size, source_size, brir_size, steps = struct.unpack_from('<HHIIIIIII', word, 8)
        if (version, header_size, bits, stored_index, stored_count) != (1, HEADER_BYTES, WORD_BITS, index, count):
            raise WordChainError('Word geometry, version, ordering or count mismatch')
        _integer(steps, 1, MAX_CHAIN_STEPS, 'Total step budget')
        payload_size = image_size + source_size + brir_size
        if min(image_size, source_size, brir_size) < 1 or payload_size > WORD_BYTES - HEADER_BYTES:
            raise WordChainError('Invalid word segment lengths')
        if any(word[232:HEADER_BYTES]) or any(word[HEADER_BYTES + payload_size:]):
            raise WordChainError('Reserved bytes and unused word padding must be zero')
        if word[72:104] != previous:
            raise WordChainError('Previous-word hash mismatch')
        if identity is None:
            identity, total_budget = word[40:72], steps
        elif identity != word[40:72] or total_budget != steps:
            raise WordChainError('Word chain identity or budget mismatch')
        end_image = HEADER_BYTES + image_size
        end_source = end_image + source_size
        item = PackedProgram(word[HEADER_BYTES:end_image], word[end_image:end_source],
                             word[end_source:end_source + brir_size])
        for part, offset in ((item.image, 104), (item.source, 136), (item.brir, 168)):
            if _hash(part) != word[offset:offset + 32]:
                raise WordChainError('Word segment digest mismatch')
        if _hash(word[HEADER_BYTES:HEADER_BYTES + payload_size]) != word[200:232]:
            raise WordChainError('Word payload digest mismatch')
        _validate_program(item)
        programs.append(item)
        previous = _hash(word)
        hashes.append(previous.hex())
    if _manifest(programs, total_budget) != identity:
        raise WordChainError('Whole-chain manifest mismatch')
    return WordChain(tuple(programs), total_budget, identity.hex(), _hash(blob).hex(), tuple(hashes))


def _state_digest(registers):
    digest = hashlib.sha256(b'BRWWORD:register-state:v1\0')
    for value in registers:
        digest.update(value.to_bytes(WORD_BYTES, 'little'))
    return digest.hexdigest()


def as_operands(blob):
    """Represent each complete program word as one exact unsigned VM operand."""
    verify_chain(blob)
    return tuple(int.from_bytes(blob[index:index + WORD_BYTES], 'little')
                 for index in range(0, len(blob), WORD_BYTES))


def from_operands(operands):
    """Restore fixed-width program words, including their leading zero padding."""
    words = []
    for operand in operands:
        if len(words) >= MAX_WORDS:
            raise WordChainError('At most 64 word operands are allowed')
        if type(operand) is not int or operand < 0 or operand.bit_length() > WORD_BITS:
            raise WordChainError('Program operand must be an unsigned 1048576-bit word')
        words.append(operand.to_bytes(WORD_BYTES, 'little'))
    blob = b''.join(words)
    verify_chain(blob)
    return blob


def run_chain(blob, *, registers=None, max_steps=None, timeout_seconds=30.0, cancel=None):
    """Execute word programs in order, handing all 16 full-width registers on.

    HALT ends the current image. The next word starts at PC=0 with fresh flags
    and the previous image's registers. Every image is admitted before any runs.
    Cancellation/deadline are cooperative at instruction boundaries.
    """
    if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS):
        raise WordChainError('Timeout must be finite and in (0,120] seconds')
    if cancel is not None and not callable(cancel):
        raise WordChainError('Cancellation must be callable')
    deadline = time.monotonic() + timeout_seconds

    def check():
        if cancel is not None and cancel():
            raise WordChainError('Word-chain execution cancelled')
        if time.monotonic() >= deadline:
            raise WordChainError('Word-chain wall-time budget exhausted')

    check()
    chain = verify_chain(blob)
    remaining = chain.max_steps
    if max_steps is not None:
        remaining = min(remaining, _integer(max_steps, 1, MAX_CHAIN_STEPS, 'Caller step budget'))
    admitted_budget = remaining
    initial = [0] * 16
    if registers is not None:
        if not isinstance(registers, dict) or len(registers) > 16:
            raise WordChainError('Initial registers must be an index/value mapping')
        for index, value in registers.items():
            _integer(index, 0, 15, 'Register index')
            if type(value) is not int or value < 0 or value.bit_length() > WORD_BITS:
                raise WordChainError('Register operand must be an unsigned full-width word')
            initial[index] = value
    # Structural image validity does not grant execution of host services.
    for item in chain.programs:
        checked = brim.parse_brim(item.image)
        if checked.data or checked.requested_caps & ~3 or any(i.op not in brim.PURE_OPS for i in checked.instructions):
            raise WordChainError('Every image must use the pure BRIM execution profile')
    check()
    current = initial
    receipts = []
    for index, item in enumerate(chain.programs):
        check()
        if remaining <= 0:
            raise WordChainError('Total word-chain step budget exhausted')
        before = _state_digest(current)
        result = brim.run_brim(item.image, registers=dict(enumerate(current)),
                               max_steps=min(remaining, brim.MAX_STEPS), cancel=check,
                               timeout_seconds=timeout_seconds)
        current = result['registers']
        remaining -= result['steps']
        receipts.append({'word': index, 'word_sha256': chain.word_sha256[index],
                         'image_sha256': _hash(item.image).hex(), 'source_sha256': _hash(item.source).hex(),
                         'input_registers_sha256': before, 'output_registers_sha256': _state_digest(current),
                         'steps': result['steps'], 'flags': result['flags'], 'trace_sha256': result['trace_sha256']})
    check()
    return {'status': 'HALTED', 'format': FORMAT, 'word_bits': WORD_BITS, 'word_bytes': WORD_BYTES,
            'words': len(chain.programs), 'chain_id': chain.chain_id, 'chain_sha256': chain.chain_sha256,
            'steps': admitted_budget - remaining, 'max_steps': admitted_budget, 'timeout_seconds': timeout_seconds,
            'registers': [{'index': i, 'bit_length': value.bit_length(), 'low64_hex': f'{value & ((1 << 64) - 1):016x}',
                           'sha256': _hash(value.to_bytes(WORD_BYTES, 'little')).hex()}
                          for i, value in enumerate(current)],
            'register_state_sha256': _state_digest(current), 'images': receipts,
            'handoff': 'all sixteen registers; PC and flags reset between images'}


def self_test():
    """Exercise complete image packing, full-width register handoff and tampering."""
    sources = brim.demo_lctl()
    blob = compile_chain(sources)
    a = brim.compile_lctl(sources[0])
    parsed = verify_chain(blob)
    result = run_chain(blob)
    top = 1 << (WORD_BITS - 1)
    if (len(blob) != 2 * WORD_BYTES or parsed.programs[0].image != a.image
            or from_operands(as_operands(blob)) != blob
            or result['registers'][0]['sha256'] != _hash(top.to_bytes(WORD_BYTES, 'little')).hex()
            or result['registers'][1]['bit_length'] != 0
            or result['images'][0]['output_registers_sha256'] != result['images'][1]['input_registers_sha256']):
        raise WordChainError('Complete-image packing or full-width handoff failed')
    rejected = 0
    for bad in (blob[WORD_BYTES:] + blob[:WORD_BYTES], blob[:-1], blob[:HEADER_BYTES] + bytes([blob[HEADER_BYTES] ^ 1]) + blob[HEADER_BYTES + 1:]):
        try:
            verify_chain(bad)
        except (ValueError, brim.BrimError):
            rejected += 1
    if rejected != 3:
        raise WordChainError('Word-chain tamper test failed')
    return {'status': 'PASS', 'format': FORMAT, 'word_bits': WORD_BITS, 'word_bytes': WORD_BYTES,
            'words': 2, 'checks_passed': 8, 'chain_sha256': parsed.chain_sha256,
            'scope': 'LCTL compilation and complete-image packing; operand roundtrip; high-bit BRIM execution; register handoff; wrapping arithmetic; three corruptions'}
