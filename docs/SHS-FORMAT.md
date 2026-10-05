# BASIC-compatible execution and LCTLC-WIDE/0.1

The `ultrawide.shs` module executes the recovered BASIC-1048576 instruction set
through a bounded Python host. Its **LCTLC-WIDE/0.1** representation preserves
every instruction field and the original executable header. It is an explicit
classical extension inspired by columned LCTL, not stock LCTL-C, the native
LCTL compiler, BRIM, or the smaller UWA/0.1 example language.

The exact recovered `reference.py` is retained with SHA-256
`f7af5b056131fa3227da090670f547189f19c905d66c038e1cd54e59d7de4b0b`.
`engine.py` applies resource limits and input validation around it.
`PROVENANCE.json` identifies the source and intentional changes. The
[original-system audit](SHS-AUDIT.md) explains the complete machine semantics,
the broader suite's gaps, and discrepancies in the original documentation.

## Eight-column representation

A source has this structure; the header and seal placeholders below are
illustrative, so this displayed fragment is not a runnable file:

```text
LCTLC-WIDE/0.1
# Optional full-line comment
@header <base64 of the exact executable JSON header bytes>
ID|LANE|OP|OUT|CTRL|IN|ARG|META
I0000|cpu|MOVI|R0|R0|R0|0x41|width=1048576;mode=wrapping
I0001|cpu|SVC|R0|R0|R0|0x4|width=1048576;mode=wrapping
I0002|cpu|HALT|R0|R0|R0|0x0|width=1048576;mode=wrapping
@end
@sha256 <64 lowercase hexadecimal characters>
```

| Column | Exact mapping |
| --- | --- |
| ID | Sequential `I0000`, `I0001`, and so on |
| LANE | Literal `cpu` |
| OP | One of the recovered 24 uppercase opcode names |
| OUT | Encoded destination register `rd`, written `R0`..`R15` |
| CTRL | Encoded source-B register `rb`, written `R0`..`R15` |
| IN | Encoded source-A register `ra`, written `R0`..`R15` |
| ARG | Unsigned `0x` hexadecimal immediate, at most 262,144 digits |
| META | Exact `width=N;mode=MODE` fields |

All fields are retained, including fields an opcode ignores. `_` placeholders
are not part of this extension. For LOAD/STORE, the number in CTRL selects a
capability slot; the text still uses `R` because it preserves the encoded
register-index field. For SHL/SHR, CTRL is a shift-count register. It is not a
generic quantum-control or arbitrary capability-expression column.

Widths are the 18 powers of two from 8 through 1,048,576. Modes are `wrapping`,
`checked`, `saturating`, and `trapping`. The immediate always fits the full
1,048,576-bit word, even for a narrow instruction. Narrow writes clear upper
bits. Arithmetic, flags, branches, memory and stack follow the source-driven
semantics in the audit; in particular BASIC shifts use register B modulo width.

Syntax is ASCII. LF, CRLF, and CR line endings are normalized to LF. Blank
lines and comments starting with `#` are permitted after the magic line, within
the overall bounds. The final seal hashes every preceding normalized line,
including comments, with one final LF. It detects changes and does not
authenticate an author.

The base64 header preserves exact JSON bytes, including valid whitespace, so
accepted executable images can make a byte-identical image → text → image
round trip. The JSON contains the instruction-code hash and size. **Resealing
hand-edited instructions alone does not update that header.** Create a new
Program with `from_instructions()` or use validated assembly to regenerate
consistent metadata, then call `emit()`. This avoids silently accepting a stale
code hash as a new executable.

## Generate and run source

With `src` on PYTHONPATH:

```python
from pathlib import Path
from ultrawide import shs

program = shs.from_instructions([
    shs.Instruction('MOVI', rd=0, immediate=65),
    shs.Instruction('SVC', ra=0, immediate=4),
    shs.Instruction('HALT'),
], capabilities=('console.write',))
source = shs.emit(program)
assert shs.compile_program(shs.parse(source)) == shs.compile_program(program)
with Path('hello.lctlw').open('x', encoding='utf-8', newline='\n') as stream:
    stream.write(source)
result = shs.run(program)
assert result['stdout'] == 'A'
```

The command line preserves the original UWA commands and provides separate SHS
verbs:

```sh
python -m ultrawide shs self-test
python -m ultrawide shs inspect examples/shs-control-memory-stack.lctlw
python -m ultrawide shs run examples/shs-control-memory-stack.lctlw
python -m ultrawide shs compile hello.lctlw hello.b1048576
python -m ultrawide shs translate hello.b1048576 translated.lctlw
```

`inspect`, `run`, `translate`, and `compile` admit a BASIC executable,
LCTLC-WIDE text, or supported recovered assembly, recognizing content rather
than trusting a filename extension. A Windows installer is not a guest
executable. `translate` writes the columned form; `compile` writes the BASIC
executable. Output files must be new. Malformed input returns a bounded JSON
error and a nonzero CLI status; valid guest execution reports its guest exit
code in JSON. The wrapper CLI succeeds when verification/execution completes.

`shs run` accepts `--max-steps`, `--memory-bytes`, `--timeout-seconds`, and
`--include-trace`. Without the last option, output includes compact register
hashes, flags, PC/SP/FP, memory hash, bounded service output, evidence marks,
execution/trace hashes, and limits, but omits the trace list and giant register
hexadecimal previews. The Python API returns the bounded trace. Separate
`brim` and `chain` commands have different formats and execution semantics.

## Actual host limits

| Resource | Bound / default |
| --- | --- |
| Instructions | 1..256 |
| Source input | At most 68 MiB |
| Physical source lines | At most 4,096 |
| Characters per physical line | At most 262,656 |
| Decoded executable header | 1..65,536 bytes; JSON nesting at most 64 |
| Executable input | 256 fixed instructions plus bounded header, framing and hash; 33,628,206 bytes maximum |
| Memory | 131,072..16,777,216 bytes; default 1,048,576 |
| Steps | 1..10,000; default 4,096 |
| Service output | At most 1,048,576 characters; Python API can lower the limit |
| Wall time | Finite positive limit through 120 seconds; default 30 |
| Named capabilities | At most 128 names, each 1..256 characters; checked at image admission |

The limits bound the hosted implementation; they do not enlarge the original
ISA. Timeout and cancellation are cooperative, checked at instruction
boundaries and during decimal rendering. They are not a hard real-time
preemption guarantee for a single Python arithmetic operation. A SHS Python
cancellation callback can return true or raise an exception to interrupt execution.

## Intentional hardening and compatibility limits

- Header decoding rejects duplicate keys, non-finite values, incompatible
  architecture versions, excessive depth, and booleans in integer fields.
  Sources are bounded before line and cell lists are allocated.
- Capability descriptors must remain inside physical guest memory; externally
  corrupted stack pointers fail rather than using Python slice edge behavior.
- Unicode surrogates fail as explicit VM faults. Full-width decimal output
  uses exact chunked conversion without changing Python's global digit limit.
  Output that exceeds its limit is refused before appending.
- Bounded VM instances are single-use. Create a new VM for a fresh run; there
  is no implicit resume from an earlier halted machine.
- Source and image hashes are integrity evidence, not signatures. Named
  capability strings retain the original admission semantics; SVC does not
  acquire a new per-service authorization protocol. PUSH/POP retain direct
  guest-memory stack behavior.
- Assembly operand counts are validated. The recovered assembler's final
  `.width`/`.mode` defaults still apply to all its instructions; this historical
  behavior is retained explicitly. The original object linker and its missing
  multi-object relocations are not included as a compatibility claim.
- Execution is hosted by Python on Linux or another supported host. The
  language does not compile itself, translate the Linux kernel, run arbitrary
  host Python, or provide Windows application emulation.
