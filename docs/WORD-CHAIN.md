# Complete BRIM images packed into ultra-wide words

`BRWWORD/1` implements one complete small program image per 1,048,576-bit word.
A chain file (`.brw`) is a contiguous sequence of **131,072-byte words**, with
no external prefix or trailer. Each word contains a complete actual BR-480
BRIM image, its columned LCTL source, its BRIR representation, and checked
metadata. Images never spill into another word. An oversized image/source/IR
combination is rejected rather than truncated or implicitly split.

This is a new explicitly versioned container and execution convention. The
contained `.brimg` is a BRIM image; the whole `.brw` is not a native BR-480
image and must be unpacked/verified by this adapter. See [BRIM.md](BRIM.md)
for the implemented BRIM instructions and the `LCTL-BRIM/1` language boundary.

## Compile and run two linked program words

```sh
export PYTHONPATH=src
python -m ultrawide chain pack two-programs.brw examples/brim-top-bit.lctlb examples/brim-wrap-next.lctlb
python -m ultrawide chain verify two-programs.brw
python -m ultrawide chain run two-programs.brw
```

The first program creates the highest bit in R0 using `MOVI` and `SHL`; the
second receives that full-width register and doubles it into R1. The wrapping
result is zero with the BRIM carry/overflow indicators set. Execution receipts
bind every word/image/source and the input/output register-state hashes.
The checked-in `examples/brim-word-chain.brw` is exactly 262,144 bytes: two
program words. It can be regenerated from the two readable LCTL sources.

Every image begins at PC 0 with fresh comparison/arithmetic flags. All sixteen
full-width registers pass to the next image. `HALT` ends that image, then the
next word runs. The chain does not carry a host process, stack, memory device,
filesystem handle, service authority, or hidden machine state between images.
Register carryover is an explicit container convention, not a change to BASIC
branch semantics or a claim that unrelated BRIM hosts implement this chain.

## Words are exact operands

```python
from ultrawide import brim_wide

operands = brim_wide.as_operands(chain_bytes)
assert len(operands) == 2
assert all(0 <= value < (1 << 1048576) for value in operands)
assert brim_wide.from_operands(operands) == chain_bytes
```

Each integer is the **whole program word**, interpreted little endian. It can
be held in an ultra-wide register or stored as one full-width operand without
losing the image. Restoration always writes exactly 131,072 bytes, preserving
zero padding that ordinary variable-length integer encoding would discard.
Packed program words are data until explicitly verified and dispatched by the
chain runner; arithmetic on the encoded word normally invalidates its hashes.

Initial execution operands are separate from program-word encoding. The Python
runner accepts `registers={0: value}` with any unsigned full-width value. These
operands feed the first program and then travel through the explicit register
handoff. JSON receipts show hashes, bit lengths, and low 64 bits instead of
attempting to print 315,653-digit integers.

## Byte layout

All integer fields below are unsigned little endian. The header is 256 bytes.

| Offset | Bytes | Field |
| --- | ---: | --- |
| 0 | 8 | Magic `BRWWORD` followed by NUL |
| 8 | 2 | Version = 1 |
| 10 | 2 | Header bytes = 256 |
| 12 | 4 | Word bits = 1,048,576 |
| 16 | 4 | Word index, sequential from zero |
| 20 | 4 | Total word count, 1..64 |
| 24 | 4 | Complete BRIM image byte count |
| 28 | 4 | Source byte count |
| 32 | 4 | BRIR byte count |
| 36 | 4 | Total chain step budget, 1..10,000 |
| 40 | 32 | Whole-chain manifest identity |
| 72 | 32 | SHA-256 of previous complete word; all zero for index 0 |
| 104 | 32 | Image SHA-256 |
| 136 | 32 | Source SHA-256 |
| 168 | 32 | BRIR SHA-256 |
| 200 | 32 | SHA-256 of concatenated image + source + BRIR |
| 232 | 24 | Reserved, all zero |
| 256 | variable | Complete image, then source, then BRIR |
| after payload | remainder | All-zero padding through byte 131,071 |

The chain manifest is SHA-256 of the ASCII domain separator
`UltraWide:BRWWORD:manifest:v1` plus NUL, then uint32 count and step budget,
then, for every word in order, each of image/source/BRIR as uint32 byte count
followed by its raw 32-byte SHA-256. The identity and budget are repeated and
checked in every word. Previous-word links hash the entire fixed-size word,
including its header and padding.

## Admission and hardening

The verifier checks the entire chain before any image executes: exact length,
geometry, version, indices/count, reserved bytes, padding, previous-word links,
all segment digests, complete BRIM/BRPV validity, source/BRIR binding, and the
whole-chain manifest. Every source is recompiled with its declared image
version and step budget, and the resulting image and BRIR must match exactly.
A hash claiming that an unrelated source produced an image is insufficient.
This chain version admits `LCTL-BRIM/1` source; other compiler dialects require
a separate verified adapter. The execution preflight also rejects every image outside
the supported pure control/arithmetic profile before running the first image.

Limits are 64 words / 8 MiB per chain, 256 instructions per BRIM image, 4,096
steps per image, and 10,000 total steps. A caller may lower but cannot raise
the encoded budgets. The default wall-time limit is 30 seconds, with a maximum
of 120; cancellation and deadlines are checked between instructions. They do
not preempt a single Python big-integer operation. There is no host command,
network, file access, dynamic import, or native-code instruction in this profile.

Hashes detect corruption, reordering and incomplete chains. They are not
signatures: someone who edits a program and recomputes all hashes has created
a different unsigned chain. BRIM signature flags and BRTM authentication are
not silently treated as verified. These images are product-preview artifacts,
not Secure Boot or native BR-480 trust-chain releases.

The separate BASIC image format has 131,104-byte instructions, each already
larger than a word. Full BASIC instruction images therefore use the lossless
`LCTLC-WIDE/0.1` workflow or explicitly named SHS binding bundles; they cannot
be squeezed into this one-complete-small-program-per-word format without
changing their encoding and semantics.
