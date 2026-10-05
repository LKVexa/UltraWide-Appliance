# UWA/0.1 program format

UWA/0.1 is a small custom arithmetic demonstration format with eight explicit columns. It is not a compatibility layer for an existing ISA or programming language. Programs run in eight zero-initialized software registers, R0 through R7, at one declared unsigned word width. Execution is straight-line and ends in HALT.

## Source structure

Sources use ASCII syntax, stored as UTF-8, with LF, CRLF or CR line endings. There are no comments, blank lines or quoted fields. The structure is:

```text
UWA/0.1
@word-bits 8
ID|OP|OUT|A|B|IMM|WIDTH|NOTE
I0000|MOVI|R0|_|_|255|8|initial-value
I0001|MOVI|R1|_|_|1|8|one
I0002|ADD|R2|R0|R1|_|8|wrap-to-zero
I0003|HALT|_|_|_|_|8|_
@end
@sha256 <64 lowercase hexadecimal characters>
```

The displayed checksum placeholder is illustrative; generate a valid source using the API below. IDs must be sequential I0000, I0001, and so on. Every WIDTH cell must equal the declared width. Unused operands must be `_`. NOTE is non-executable annotation limited to 1–64 ASCII letters, digits, underscores, periods, colons or hyphens.

| Column | Meaning |
|---|---|
| ID | Sequential instruction identifier |
| OP | One of the eight operations below |
| OUT | Destination register, or `_` |
| A | First source register, or `_` |
| B | Second source register, or `_` |
| IMM | Exact unsigned immediate, or `_` |
| WIDTH | Declared word width repeated explicitly |
| NOTE | Bounded annotation, or `_` |

## Operations

| OP | Required operands | Result |
|---|---|---|
| NOP | None | Leave registers unchanged |
| MOVI | OUT, IMM | Copy immediate to destination |
| ADD | OUT, A, B | A + B modulo 2^WIDTH |
| SUB | OUT, A, B | A − B modulo 2^WIDTH |
| XOR | OUT, A, B | Bitwise exclusive OR |
| SHL | OUT, A, IMM | Logical left shift, discarding bits above the width |
| SHR | OUT, A, IMM | Logical right shift, filling high bits with zero |
| HALT | None | Finish; must appear exactly once as the final instruction |

Registers may alias: `ADD` can read and replace the same register. Shifts by at least WIDTH produce zero; counts are not reduced modulo WIDTH. Every immediate, including a shift count, must fit the program word. Thus an 8-bit program can encode shift counts only through 255. The standalone `Word.shl` and `Word.shr` Python methods accept counts through 1,048,576 regardless of the word's width.

## Literals and bounds

Immediate forms are unsigned decimal (at most 4,096 digits), `0x` hexadecimal, `bit:N` for exactly bit N, and `mask` for all bits set. Each resolved value must fit the declared width. Bit indices start at zero. Hexadecimal literals have at most ceil(WIDTH/4) digits. There is no evaluation of expressions or imported code.

- Word width: any integer from 8 through 1,048,576; default 1,048,576.
- Registers: exactly eight, initially zero.
- Program length and execution budget: 1–64 instructions, including HALT.
- Source size: at most 20 MiB; at most 69 physical lines including the seal, or 68 lines before sealing; at most 262,400 characters per line. Physical line counts are checked before a line list is allocated. Cell splitting is bounded to avoid allocating a list for every delimiter in malformed input.
- Shift count: at most 1,048,576, also constrained by the immediate's word width.
- No branches, memory access, host commands, network operations or operating-system services.

## Seals and evidence

The source seal is SHA-256 over all preceding source lines after CRLF/CR normalization to LF, with one final LF. It detects changed text; it does not identify an author or prove that a program is safe. The parser checks both the checksum and operand/resource rules. The `seal()` helper only applies text bounds and a checksum; call `parse()` to validate a manually constructed sealed program.

Execution reports contain a hash of canonical source produced by `emit()`, a deterministic trace hash, and small summaries of final register values. A word digest hashes its fixed-length little-endian storage bytes, with unused high padding bits zero. It does **not** include the bit width: `Word(1, 9)` and `Word(1, 16)` have the same two stored bytes and digest. For typed identity, compare both the report's `bits` and `sha256` fields.

## Generate and update programs

With `src` on PYTHONPATH:

```python
from pathlib import Path
from ultrawide.program import Instruction, Program, emit, execute, parse

program = Program(8, (
    Instruction('MOVI', out=0, immediate=255),
    Instruction('MOVI', out=1, immediate=1),
    Instruction('ADD', out=2, a=0, b=1),
    Instruction('HALT'),
))
source = emit(program)  # Validates operands and generates the source seal.
assert parse(source) == program
Path('example.uwa').write_text(source, encoding='utf-8')
report = execute(program)
```

To revise a program, create updated `Instruction`/`Program` data and call `emit()` again. For hand-edited source, remove its old `@sha256` line, pass the remaining text to `seal()`, then call `parse()` before saving or executing. For example:

```python
from ultrawide.program import parse, seal

edited_body = source.rsplit('@sha256 ', 1)[0].replace('|mask|8|', '|254|8|', 1)
updated_source = seal(edited_body)
updated_program = parse(updated_source)
```

The supplied `examples/high-bit.uwa` exercises the highest bit at the full default width. Generate another example with `python -m ultrawide demo --write-source example.uwa`; the CLI refuses to overwrite an existing output file.
