# Verification of the 0.2 preview

This page qualifies the restored BASIC runtime, BRIM tools, program-word chain
and new Linux image. The original 0.1 scaffold's checks remain historical
records; they are not substituted for testing this revision.

## Source and execution checks

The [runtime receipt](evidence/runtime-0.2.json) records **154 test methods:
153 passed and one skipped** on Python 3.12.14 / Windows. The skip is a real
symlink-creation test because the host denies creating symlinks; the repository
still checks links and hard links. There were 1,037 exercised subtests. The
suite was rerun on the canonical staged Git bytes used to build the image.

- 187 explicit ISA vectors cover all 24 BASIC operations, 18 widths and four
  arithmetic modes. Another 216 cases use independent integer-interval
  arithmetic expectations across widths, modes and overflow/underflow paths.
- Original and translated BASIC programs match register hashes, flags, PC,
  memory, stack and faults; accepted image/source roundtrips preserve bytes.
  The recovered reference's SHA-256 remains `f7af5b056131fa3227da090670f547189f19c905d66c038e1cd54e59d7de4b0b`.
- Source/image boundaries, capabilities, stack limits, output, cancellation,
  cooperative deadlines, JSON structure, BRIM control flow, artifact paths,
  quotas, exclusive writes and source-overlay filtering are exercised.
- The supplied independent BRIM reader accepts both newly generated full-width
  demo images. Twelve existing candidate images parse; 36 executions match
  the candidate's shared bounded input domain. This is not a native full-width
  BR-480 or signature-chain certification.
- Word-chain tests validate complete image packing, exact LCTL recompilation,
  source/IR bindings, all header/hash/padding fields, missing/reordered words,
  full-width operand conversion, register handoff, total budgets and admission
  of all images before execution begins.

## Actual Linux boots

The newly built **83,171,328-byte** ISO was tested in isolated QEMU guests:

```text
94fbf67d3038d43776b5169763b8ddcf5a6c683cda1f55447755aa494cad59e5
```

Both [BIOS](evidence/boot-bios-0.2.json) and
[64-bit UEFI](evidence/boot-uefi-0.2.json) boots passed. The guests reported
Linux 6.18.52-0-virt and ran the actual UWA, full-SHS and BRIM-word-chain
self-tests. The console reported UID 1000 and could not read `/etc/shadow`.
Root password login is locked by the checked boot hook. No network adapter,
host disk or shared folder was attached, and both QEMU processes were stopped.

The [overlay receipt](evidence/source-overlay-0.2.json) binds the tested ISO to
the exact published project payload: its overlay was read from the ISO and
each included project file was compared byte-for-byte with canonical source.
Build scripts correctly record `boot_tested: false`; the separate observed
boot receipts establish qualification of this particular output. A new build
requires its own boot checks and may have a different whole-ISO hash.

The inherited Alpine UEFI loader can report a missing original volume label
before selecting the optical fallback. The tested guest continued to its
64-bit UEFI boot successfully. Physical hardware and Secure Boot are untested.

## Downloadable binary examples

| Example | Exact bytes | SHA-256 |
| --- | ---: | --- |
| [One program word](../examples/brim-one-word.brw) | 131,072 | `024cb0c01b22a0bfb26def4d864fd47c05d33d0e753bbfd08a458ef555bdcf7e` |
| [Two linked program words](../examples/brim-word-chain.brw) | 262,144 | `e7ac6963c00ee769ca65ef5899196bd8a914ac5ffb51607087ecdc008aa7ac6b` |

Both exactly match compilation of the checked-in LCTL programs, execute in
three and five steps respectively, and roundtrip as full-width integer
operands without losing padding. The one-word example is independently packed
with count=1; it is not an invalid slice of the two-word chain. Its
[actual-bit image](images/brim-word.png) is rendered from the binary.

## Reproduce and interpret

Run the README's test, SHS and chain commands. [BUILD.md](BUILD.md) provides
image construction and isolated BIOS/UEFI qualification commands. GitHub
Actions runs the source checks on Python 3.12 and 3.14; the Actions result for
the published revision is separate from the local receipts above. A workflow
definition alone does not establish success.

This preview is not qualified for native compiler self-hosting, the complete
JA language/kernel suite, native BR-480 services, Windows application hosting,
VIVIAN/model integration, persistent appliance state or production security.
Its Linux/Python host, explicit language extensions and unsigned images are
documented boundaries, not hidden substitutes for those capabilities.

Historical 0.1 receipts: [BIOS](evidence/boot-bios.json) and
[UEFI](evidence/boot-uefi.json). They concern the earlier UWA-only image.
