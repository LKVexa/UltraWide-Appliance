# Audit of the supplied system and the initial preview

This audit uses **SHS** as the user's name for the supplied system. The inspected
installer calls its product **BASIC 1048576 Universe — JA-20**. No separate SHS
source package was supplied. The audit distinguishes that installer, the
candidate's separate BRIM adapter, and this repository's initial UWA/0.1 preview.
It is a requirements baseline, not a claim that every original subsystem has
already been ported. Consult the current runtime and verification documentation
for the implemented revision.

## Evidence and inspection limits

The original `BASIC-1048576-Universe-JA20-4.0.0-x64.exe` was read and hashed,
without executing its installer. A fresh SHA-256 check reproduced:

```text
ca6ffd619878ca6747e4490b9f41f0b2fc07600c75997e7c0aabf4b54ebf395d
```

It is a Windows AMD64 Go executable with an appended ZIP containing a
content-addressed store and logical path map. The earlier extraction recorded
978 archive entries, 1,055 logical files, and 501,931,814 logical bytes. It
selected 905 small source, documentation, configuration, and example files
(4,051,020 bytes), checking selected contents against the package map. Large
corpus archives, native binaries, and other excluded payloads were not treated
as inspected source. This audit reviews the recovered runtime, assembler,
object/linker, shell, suite CLI, architecture contracts, packaging boundaries,
and license records. It does not certify every record in the bundled corpora.

Source identifiers below are **paths inside the supplied package**, not files
redistributed by this document. `basic_os/` abbreviates
`modules/BASIC_1048576_UNIVERSE/basic_os/`.

| Source | SHA-256 |
| --- | --- |
| `basic_os/src/basic_os/basic1048576.py` | `f7af5b056131fa3227da090670f547189f19c905d66c038e1cd54e59d7de4b0b` |
| `basic_os/src/basic_os/basic1048576_asm.py` | `0a417c67cac2024ecd07cad473c415222e1fc00dfecb93bed7e64ee175399631` |
| `basic_os/src/basic_os/basic1048576_object.py` | `5be5e16bbfb1a7d53e4cb33319a756c5c2f7365ae276faae2ca377773c3db96e` |
| `basic_os/src/basic_os/cli.py` | `d86bfca01d2586bedbf2c0f723986733f2731ca67b7f757b0757bad3b78b8835` |
| `scripts/ja_suite.py` | `d67fd0536be5a113823d1204a15edc2ecb417d0637af397fbe32674c5a0b38bc` |
| `program/program-scope.yaml` | `796f5b092bf206bd8d26a8df8d0c0ec53c23845f8e458d77df87aca5231ecf12` |
| `spec/kernel/shared-ja-kernel.yaml` | `18a2320580c8c62dcefd50706e6a2a077d23928d6223d16b64f614a833a118fe` |
| `LICENSES/canonical/Preview_Tester_License_Agreement_en-US.md` | `7d8c7cf01576bac0835de3c6ea792fff6687f9e0250386f7c5e3f7b1bf36af63` |

## Gap and architecture matrix

| Area | Recovered implementation or explicit status | Initial UWA/0.1 preview | Required treatment |
| --- | --- | --- | --- |
| Word machine | BASIC-1048576 v0.2.0; 16 registers, 18 lane widths, 24 instructions, 4 arithmetic modes | Eight registers; eight operations; arbitrary widths 8..1048576; wrapping arithmetic | Preserve an explicitly named BASIC compatibility machine separately from UWA |
| Control flow and state | Branches, flags, program counter, memory, stack, services, trace and snapshot API | Straight-line arithmetic ending in HALT | Translate complete state effects and faults; test branch and stack paths |
| Executable images | `B1048576E` images with JSON metadata and SHA-256; large fixed instructions | Sealed text only | Verify, inspect, translate and reconstruct original image bytes with bounded input |
| Objects and assembly | Python assembler, object format and limited linker | Not present | State supported scope; do not claim complete relocation/linker behavior |
| Shell | Python `build`, `run`, `verify`, `inspect` | `self-test`, `demo`, `run` for UWA | Expose actual image and translation workflows with explicit verbs |
| HERMIT / PODIUM | Repository and job metadata specifications; no corresponding storage/job engine found in recovered code | Not present | Implement a clearly labeled independent content-addressed preview store and receipts; do not call it full HERMIT compatibility |
| JA suite / corpora | Registry/control validation, SQLite catalog search, record retrieval and archive extraction | Excluded | Treat catalog/corpus ingestion as a separate, authorized subsystem; do not embed private datasets |
| Shared language kernel | Source/CST/AST/type/effect/capability/R12/MCRT/MSSLB contracts and schemas; executable kernel explicitly pending | Independent custom format | Do not equate schema presence with a working compiler or self-hosted language system |
| Coupling mechanics | Executable Python classification, fixed-step RK4, R12 records and MCRT replay evidence | Excluded | Separate optional domain runtime, not an ISA instruction or complete semantic kernel |
| Desktop / native compatibility | Windows launcher opens local HTML; separate hash-allowlisted PE32+ AMD64 process launcher | Linux console and Python demonstration | Windows application compatibility requires its own qualified host; it is not supplied by wide arithmetic |
| Boot / hardware | Original explicitly hosted on Windows x64, no bare-metal claim | Independent Linux BIOS/UEFI preview boot tested | Linux supplies boot, drivers and Python; neither Linux nor Python is translated into LCTL |
| BRIM | Separate candidate adapter, absent from recovered installer identifiers | Not present | Implement verified BRIM handling with explicit semantics and binding to any BASIC payload |

The original suite's own `registry/suite-status.json` reports incomplete
program/release gates, unsigned release status, unverified Windows lifecycle,
and no production safety certification. `program/program-scope.yaml` explicitly
marks parsers as not started, the semantic kernel as a contract baseline,
backends as scaffolded, and the package manager as a profile baseline. Porting
the delivered behavior cannot honestly turn those plans into completed systems.

## Exact BASIC machine behavior

The executable source, rather than stale descriptive files, is authoritative
for compatibility. Full words are **1,048,576 bits / 131,072 bytes**, little
endian in instruction immediates, memory, and word hashing. There are 16 general
registers, 16 capability-table slots, and PC, SP, FP, Z/N/C/V flags, halted state,
and a 32-bit exit code. Supported lane widths are powers of two from 8 through
1,048,576 inclusive: **18 widths**. A narrow write replaces the register with
the masked value; it does not retain upper lanes. These widths describe a
selected low-bit lane, not SIMD execution across all lanes.

An instruction occupies **131,104 bytes**: 32 control bytes followed by one
131,072-byte unsigned immediate. Control bytes 0..5 hold opcode, destination,
source A, source B, width index, and arithmetic-mode index. Bytes 6..31 must be
zero. All three register fields must be 0..15 even when an operation ignores
one. The immediate always permits the full word range, independently of lane
width. A hardened typed API should reject booleans and non-integers explicitly.

| Instruction and opcode | Implemented effect |
| --- | --- |
| `NOP 00` | No state change other than the run loop's PC/trace |
| `MOVI 01`, `MOV 02` | Mask immediate or source A to selected width; write destination and set arithmetic flags |
| `ADD 10`, `SUB 11`, `MUL 12` | Mask both source registers to width, compute unsigned result, apply mode, write and set flags |
| `DIVU 13`, `MODU 14` | Unsigned quotient/remainder; zero divisor faults |
| `AND 15`, `OR 16`, `XOR 17`, `NOT 18` | Width-masked bitwise operation; write and set flags |
| `SHL 19`, `SHR 1A` | Shift masked source A by **masked source B modulo lane width**; SHL applies arithmetic mode to discarded high bits |
| `CMP 20` | Set Z=(A==B), N=(A<B), C=(A>=B), V=false using masked unsigned operands; no register write |
| `JMP 21`, `JZ 22`, `JNZ 23` | Set PC to the full immediate when unconditional/flag condition is true; out-of-range target faults on the next fetch |
| `LOAD 30` | Read width/8 bytes using capability indexed by source B and immediate offset; little endian; write destination and set flags |
| `STORE 31` | Store low width bits of the **destination register field**, using capability source B and immediate offset; preserve flags |
| `PUSH 32` | Push full-width source A, regardless of instruction width; preserve flags |
| `POP 33` | Pop a full word into destination, regardless of width; preserve flags |
| `SVC 50` | Invoke immediate-selected service on source A; preserve arithmetic flags |
| `HALT FF` | Halt with immediate masked to 32 bits as exit code; preserve flags |

Arithmetic modes are wrapping=0, checked=1, saturating=2, trapping=3.
Wrapping reduces modulo 2^width; saturation clamps unsigned underflow to zero
and overflow to the maximum. Checked and trapping both raise faults on an
out-of-range result, with different fault identifiers. Successful arithmetic
sets Z from zero and N from the selected sign bit; **both C and V equal the
unsigned overflow/underflow indicator**. This differs from conventional CPU
carry and signed-overflow rules. MOV/LOAD clear C/V. A fault occurs before a
successful register/flag update, while the run loop has already incremented PC.

Memory defaults to 16 MiB in the VM constructor; the original CLI defaults to
64 MiB. The constructor permits 131,072..536,870,912 bytes. C0 initially covers
the whole byte array with read/write permissions; C1..C15 are absent. Capability
checks enforce revocation, permission, and offset bounds. No guest instruction
creates or revokes capabilities. A generation field exists but is not checked.
Stack operations use the byte array directly, move SP by 131,072, and do not
consult the capability table. SP and FP initially equal memory size; FP is not
otherwise changed by the ISA. A robust host must also validate externally
provided capability base/length against actual memory, not rely on Python's
permissive slice behavior.

Services are: 1 unsigned decimal; 2 full-word signed two's-complement decimal;
3 fixed full-word hexadecimal; 4 character; 5 SHA-256 evidence mark. Width does
not narrow these services. Full hexadecimal has 262,144 digits plus `0x`.
Evidence marks are kept on the VM instance but are not included in the original
run result. Required capability **strings in image metadata** gate admission;
the original SVC dispatcher does not independently require console/evidence
capability strings. The run loop defaults to one million steps and accumulates
trace rows in memory. Its public result includes hashes for all registers and
full hexadecimal previews for R0/R1. A Python snapshot is evidence, not a
restorable boot image or a serialized complete machine checkpoint.

## Images, assembler, and original defects

An executable is `B1048576E` plus NUL, a little-endian uint32 JSON-header length,
header bytes, aligned code, and a trailing SHA-256 of everything before the
digest. The canonical builder records architecture/version, profile base=2 and
exponent=20, word bits, endianness, instruction size, entry instruction,
capability names, code length/hash, and debug data. SHA-256 detects corruption;
it does not authenticate an author. Preserving original header bytes is required
for byte-identical reconstruction of a valid noncanonical JSON serialization.

Objects use `B1048576O` plus NUL with analogous metadata/integrity and symbol
indices. The original linker concatenates objects, rebases symbol indices,
unions capability strings, and chooses an entry symbol. It does **not** relocate
already encoded local branch immediates; its relocation list is empty. A
multi-object program can therefore branch to the wrong object. This behavior
must not be advertised as a complete relocatable linker.

The assembler accepts registers R0..R15, capability operands C0..C15, integer
literals, labels, semicolon comments, `.capability`, `.width`, and `.mode`.
Negative literals are normalized to the full word. Its first pass collects
directives, and its second pass applies the **final default width/mode to all
instructions**, so directives are not lexical changes at their source position.
Some surplus operands are ignored, while missing operands may raise an
unstructured indexing exception. A new strict language front end should state
its rules explicitly instead of silently inheriting malformed-input behavior.

Other original issues requiring explicit hardening or compatibility notes:

- Executable verification does not require `architecture_version`, permits
  JSON duplicate keys/non-finite extensions through default decoding, and uses
  integer tests that can admit booleans. Header, source, instruction count,
  output and trace sizes are not independently bounded there.
- Decimal printing can hit Python's integer-to-string digit limit for valid
  full words. Character service rejects values above U+10FFFF but admits
  surrogate code points; later UTF-8 output hashing can fail.
- Reusing a VM instance retains registers, memory, flags, output and halted
  state; `run()` only resets PC. Fresh executions need a fresh instance unless
  stateful continuation is an explicit API feature.
- `REGISTER_MODEL.json` still says 65,536-bit registers and only 14 widths;
  `INSTRUCTION_SET.json` says 8,224 instruction bytes; ABI/debug text and the
  HTML dashboard retain 8,192-byte words or 16,384-digit hex. These are stale
  BASIC-65536 values, contradicted by executable source and its tests.

## Wider suite, shell, and ownership boundaries

`basic_os/src/basic_os/cli.py` actually provides four subcommands: build, run,
verify, inspect. `basic_os/SHELL/SHELL_SPECIFICATION.md` describes additional
architecture, JA20 compilation, migration, compatibility, payload, and split
verification commands; no matching integrated shell dispatcher was found.
HERMIT, PODIUM, and LANDON architecture documents describe desired repository,
job, and IDE records. They should guide new APIs without being presented as
existing executable subsystems.

There are real adjacent implementations: `scripts/ja_suite.py` validates
registries and program contracts, searches a SQLite corpus catalog, retrieves
records from ZIPs, and extracts registered archives. `scripts/coupling_cli.py`
and `runtime/coupling/` implement package validation, geometric classification,
fixed-step RK4 scenarios, and hashed R12/MCRT replay ledgers. They depend on
their own data, schemas, and PyYAML; they are not BASIC machine services.
Unbounded simulation parameters or archive extraction must not be exposed as
trusted guest operations without separate limits and validation.

The native Windows launcher checks a local airgap-policy file and opens a
static dashboard. The compatibility host validates an approved-directory path,
SHA-256 allowlist, and PE32+ AMD64 header, then launches the executable natively.
It does not emulate Windows APIs or x86-64 instructions. Clearing proxy
environment variables does not enforce network denial; its execution-time
limit field is read but not enforced. These are original implementation gaps,
not capabilities supplied by the Linux preview.

SOPHIA, CHARLOTTE, and LANDON are semantic, structural, and implementation
responsibility roles in the supplied ownership contracts. Those roles and
approval records do not establish legal ownership of every bundled component.
The supplied canonical preview agreement identifies its publisher and explicitly
marks itself a draft awaiting legal review; its tester terms restrict public
distribution and production use. That document is distinct from this
repository's current [preview license](../LICENSE). Publication or adaptation
of supplied code requires the rights holder's authorization and retained
provenance; a new repository license does not automatically relicense upstream
or third-party material. The audit records the supplied text, not a legal
determination of title or enforceability. License-gate completion and production
approval remain separate from a successful VM test.

## BRIM and LCTL are separate compatibility boundaries

The candidate's `src/modules/lctl_agents/brim_ref.py` has SHA-256
`333b8bcf2c876efb6aec1373f24aa21da83e26036fecedd5bdcb99f10cae3056`.
Its BRIM parser describes BR-480 ISA 4.1 / ABI 1. **BRIM also declares
1,048,576-bit words and 16 registers**; its 16-byte instruction size must not be
mistaken for a 16-bit word width. It encodes 32 opcode identifiers, four modes,
and a 64-bit immediate. Its image uses an 80-byte header, at most 256
instructions, at most 4,096 data bytes, and a 96-byte BRPV provenance extension.
A separate secure parser handles a 416-byte BRTM signature-chain envelope.

The candidate's pure-policy runtime admits only 18 CONTROL/ARITH operations,
unsigned 64-bit inputs, values bounded to 256 bits, and at most 4,096 steps.
It explicitly identifies an external native compiler as the LCTL language
authority; it does not itself compile general LCTL. Its arithmetic preserves
CMP comparison flags, and its shifts use immediates. BASIC arithmetic updates
Z/N/C/V and BASIC shifts use register B modulo width. BRIM's extra instructions
include CALL, RET, YIELD, TRAP, CAPQ, CHECKPOINT, LDX and STX. The formats,
capability systems, flag rules, immediates, and control verification differ.

Consequently, renaming a BASIC executable or embedding its bytes under a BRIM
filename is not translation. A truthful integration must identify whether it
executes BRIM directly, translates a supported subset with proved semantics,
or packages a BASIC image alongside a BRIM control/binding artifact. The latter
requires separate hashes, dispatch rules, capability policy, and explicit
failure for unsupported operations. Neither a BRIM hash nor a locally generated
seal makes an unsigned preview a native trusted/signature-verified release.

No BRIM identifiers were found in the recovered installer's inspected source.
The four supplied language corpora were separately inspected by the language
workstream; they do not by themselves establish a BASIC-to-BRIM compiler.
A corpus-inspired LCTL wide extension must declare its own version and
supported syntax; it must not be labeled as the supplied general compiler.

The expanded preview implements an additional, explicit **BRWWORD/1** format.
Each 131,072-byte word contains one complete BRIM image, its exact LCTL-BRIM/1
source and BRIR, a 256-byte header, and verified zero padding. A program cannot
spill across words. The chain binds order, count, metadata and contents with
hashes. Its execution passes all 16 full-width registers between complete
programs while resetting PC and flags for each image. This is a new bounded
transport/execution extension, not an original SHS or stock BRIM image format.
It executes the preview's pure BRIM subset; the separate BASIC-compatible
machine still owns BASIC memory, stack, service and flag semantics. Treat the
new format's current test and guest receipts as its evidence, not the earlier
UWA-only boot receipts. The independent object store is described in
[REPOSITORY.md](REPOSITORY.md).

## Implementation acceptance requirements

1. Keep UWA demonstrations, BASIC-compatible wide execution, BRIM execution,
   and the LCTL extension explicit and distinct. Preserve all 24 BASIC
   operations, 18 widths, four modes, state transitions, faults, and word hashes.
2. Differentially compare original and translated programs, including the
   highest full-width bit, overflow/underflow, branch flags, aliasing, memory
   permissions/revocation/bounds, full-word stack operations, and all services.
   Record intentional hardening differences separately from semantic parity.
3. Fail closed on malformed images and source before large allocation. Bound
   physical lines, field splits, JSON/header bytes, instruction count, memory,
   steps, decimal output, trace, bundles, and archive paths. Check cancellation
   and report structured faults. Do not evaluate host commands or Python from
   image/source data.
4. Bind exact source, executable, BRIM/control artifact, and execution receipt
   hashes. Distinguish integrity, provenance, and signature authentication.
   Archive explicitly through a bounded content-addressed store; never infer
   permission to ingest a user's corpus, credentials, or unrelated files.
5. Test the actual Linux image containing the final runtime, including BIOS
   and UEFI where claimed. A booted Python interpreter is a hosted virtual
   system, not a compiler compiling itself or a translated Linux kernel.
6. Maintain a gap register for the suite compiler, corpus catalog, coupling
   runtime, Windows application host, IDE, package manager, migration, and
   release/signature gates. Unsupported original plans must remain visible.
