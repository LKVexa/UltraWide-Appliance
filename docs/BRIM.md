# BRIM images, LCTL source and ultra-wide execution

This preview implements the observed **BR-480 ISA 4.1 / image ABI 1 / BRPV 1.1**
binary format. It can create, check and execute genuine BRIM images for the
pure control and arithmetic subset using **1,048,576-bit unsigned registers**.
Encoded immediate operands remain unsigned 64-bit values, as required by that
format. A shift instruction can construct the highest bit of a full word.

Two formats must remain distinct. The recovered BASIC-1048576 machine uses
`B1048576E` executables and different instruction/flag semantics. Its lossless
LCTL transport is `LCTLC-WIDE/0.1`. A BRIM image is not a renamed BASIC image.
The separate word-chain format packs a complete small BRIM program, its source
and its BRIR into each 128 KiB word; it does not split an instruction operand
across program images. See the word-chain documentation for that container.

## Observed format and implementation boundary

The supplied BRIM reference reader and sample policy images establish these
fields; the four supplied language corpora and recovered BASIC source contain
no BRIM format definition.

| Component | Observed encoding |
| --- | --- |
| Header | 80 bytes, `BRIM`, ISA 4.1, ABI 1, image ABI 1 |
| Geometry | Word exponent 20, 16 registers, 16 capability registers, 32 opcodes, 4 arithmetic modes |
| Instruction | 16 bytes: opcode, mode, destination, two inputs, capability byte, 16 reserved bits, little-endian 64-bit immediate |
| Code | 1–256 instructions |
| Data | 0–4096 bytes |
| Provenance | 96-byte `BRPV` record: authority ABI fields, step budget, derived capabilities, source and BRIR SHA-256, image version and feature word |
| Integrity | SHA-256 of code, data and provenance in the header |

The reader validates exact lengths, geometry, versions, reserved instruction
fields, operand bounds, derived capabilities, features, control targets,
reachability and consistent data/call stack depths. Every instruction must
have a graph path to `HALT` or `TRAP`. That graph property does not prove that
every run terminates: execution also enforces a step budget and deadline.

Unsigned flag combinations 4 and 5 are accepted. Signed-image flag 2 and BRTM
trust trailers are rejected. This implementation supplies **integrity checks,
not signer authentication, secure boot, release authorization or trust-chain
verification**. It does not embed or impersonate a release signing key.

Header bytes 64–79 are an opaque 16-byte identification field in the observed
reader. They are retained and not interpreted as authority; the writer emits
zeros. The payload digest does not authenticate these or other header fields.
Structural checks also reject a conditional instruction in the final slot,
whose not-taken path would otherwise leave the image.

The parser recognizes all 32 encoded opcodes, but execution admits only:

```text
NOP MOVI MOV JMP JZ JNZ HALT
ADD SUB MUL DIVU MODU AND OR XOR NOT SHL SHR CMP
```

Data segments, memory/stack/service operations and other capability classes
are rejected before execution. Those operations remain available where
implemented by the separate full BASIC-1048576 machine; they are not silently
emulated as different BRIM instructions.

## WIDE arithmetic and state

Each of the 16 registers holds an unsigned integer from zero through
`2**1048576 - 1`. Inputs and handoff values may occupy the entire word. `MOVI`
loads its u64 immediate. `SHL` and `SHR` use the instruction's immediate shift
count, which must be smaller than the word width. `NOT` complements the full
word. Division and remainder by zero fault.

| Mode | Out-of-range ADD/SUB/MUL/SHL result |
| --- | --- |
| WRAP | Reduce modulo the word size and record overflow |
| CHECKED | Reduce modulo the word size and record overflow |
| SATURATE | Clamp to zero or the largest word and record overflow |
| TRAPPING | Raise an arithmetic fault |

BRIM `CHECKED` here follows the observed policy adapter's WIDE rules. It must
not be confused with BASIC-1048576 `checked`, which raises a fault. The
candidate adapter previously rejected intermediates above 256 bits because of
its own host resource profile. This implementation removes that particular
restriction and bounds values at the actual WIDE width. Native production
BR-480 execution has not been used to certify these full-width extensions.

Comparison bits are `ZERO=1`, `LESS=2`, `GREATER=4`; only `CMP` changes them.
Arithmetic preserves these comparison bits. `CARRY=8` and `OVERFLOW=16` are
updated by ADD, SUB, MUL, SHL and SHR. For subtraction, carry means no borrow.
`JZ` and `JNZ` inspect the comparison-zero bit. Every image starts with zero
flags, including when registers are handed off from another image.

At most 4096 instructions execute per image. The default cooperative deadline
is 30 seconds; a host may request a shorter deadline and step budget. A cancel
callback runs before each instruction. Cancellation and deadlines cannot
interrupt a Python arithmetic operation already in progress.

## Explicit LCTL-BRIM/1 compiler

`LCTL-BRIM/1` is a new, deliberately small BRIM-target dialect. It is separate
from the corpora's quantum `LCTLC/1.0`, the candidate's native classical
`LCTLC/1.1`, the SHS `LCTLC-WIDE/0.1` extension, and `UWA/0.1`. The compiler
rejects those other headers. It does not claim to implement their language
authorities or translate incompatible flag, lane or shift semantics.

This complete image creates the highest bit of R0:

```text
LCTL-BRIM/1
ID|LANE|OP|OUT|CTRL|IN|ARG|META
I0000|main|MOVI|R0|C0|R0>R0|u64:1|mode=WRAP;width=WIDE
I0001|main|SHL|R0|C0|R0>R0|u64:1048575|mode=WRAP;width=WIDE
I0002|main|HALT|R0|C0|R0>R0|u64:0|mode=WRAP;width=WIDE
```

Rows use contiguous zero-based `I0000` IDs, the `main` lane and capability C0.
OUT and IN always spell the encoded registers, even for fields unused by an
opcode. ARG is canonical unsigned decimal `u64:...`; META is exactly a mode
and `width=WIDE`. The source must have LF endings and a final newline, at most
256 instructions, 512 characters per row and 64 KiB total. Narrow widths,
unknown fields, oversized immediates and unsupported operations fail closed.

The compiler emits actual BRIM instruction bytes and `BRIR/1.1` provenance,
including the exact source-byte digest. BRIR names the source language
`LCTL-BRIM/1`; this does not imply approval by the separate native compiler.

Python example:

```python
from ultrawide import brim

first_source, next_source = brim.demo_lctl()
first = brim.compile_lctl(first_source, max_steps=16)
second = brim.compile_lctl(next_source, max_steps=16)
brim.verify_brim(first.image, source=first_source.encode(), brir=first.brir)
one = brim.run_brim(first.image)
two = brim.run_brim(second.image, registers=dict(enumerate(one['registers'])))
assert one['registers'][0] == 1 << 1048575
assert two['registers'][1] == 0
assert two['flags'] == 24  # carry and overflow from highest-bit addition
```

`run_brim` returns full Python integers for register handoff. JSON interfaces
should omit `registers` and display `register_sha256` and
`register_bit_length`; serializing megabit values as decimal is unnecessary.
Hashes use the complete 131072-byte little-endian word, including leading zero
storage bytes. The six checked example artifacts are
`examples/brim-top-bit.{lctlb,brimg,brir}` and
`examples/brim-wrap-next.{lctlb,brimg,brir}`.

## Lossless full-SHS binding bundles

For full SHS programs that cannot fit or cannot be lowered into BR-480,
`build_bundle(executable, ...)` creates the separately named
`ULTRAWIDE-SHS-BRIM-BUNDLE/1` ZIP format. Its five fixed members are:

- `program.b104e`: exact original BASIC-1048576 executable bytes.
- `program.lctlc`: reversible LCTLC-WIDE source, including original header data.
- `control.brimg`: a genuine BRIM HALT control image whose data segment binds
  the exact executable/source identities, budget and textual provenance.
- `control.brir`: the BRIR describing that control image.
- `manifest.json`: exact size and SHA-256 of the four members above.

The control image has the explicit role **binding-control**. The SHS payload
runs in the full SHS VM, not in that HALT instruction. `verify_bundle` checks
all file hashes, BRPV bindings, schema/limits and exact byte equality of
`compile_program(parse(source))` with the original executable. `run_bundle`
performs verification again and then invokes the bounded SHS VM. Capabilities
come from the caller; declaring them inside an image does not grant them.

Member names are fixed and are never extracted to a filesystem. Duplicate or
unknown names, traversal, links, special files, encryption, unsupported ZIP
compression, ZIP64 extensions, comments and trailing bytes are rejected.
Reads are bounded by actual decompressed bytes as well as declared sizes:
33 MiB executable, 68 MiB source, 16 KiB manifest, 64 KiB BRIR and 8368-byte
BRIM image. The archive itself is bounded at 104 MiB; aggregate decompressed
size is separately checked. This permits highly compressible wide immediates
without relying on an arbitrary compression-ratio heuristic.

Provenance consists of at most 12 bounded textual claims. It is asserted
metadata, not verified authorship. The example `shs-demo.uwabundle` contains
the public arithmetic/memory demo; `shs-binding.brimg` and `.brir` are its
control image, and `shs-brim-bundle-demo.lctlw` is its full SHS source.

## Validation scope

The implementation has dedicated malformed-image, arithmetic, compiler,
resource-bound, cancellation, archive and lossless-bundle tests. A separate
read-only interoperability check parsed all 12 supplied candidate images and
matched their existing bounded executor on 36 input cases. The supplied
independent BRIM verifier accepted both newly created highest-bit/addition
images and their BRIR files. These checks establish observed format and
shared-domain behavior; they are not signed-image certification or evidence
of a full native BR-480 service/memory runtime.
