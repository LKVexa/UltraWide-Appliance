# Preview verification

These checks concern this generic scaffold, performed on 4 October 2026
(America/Los_Angeles). Results from the earlier application-specific appliance
are not used as evidence for this repository.

## Completed checks

- The 30 runtime unit tests passed on Python 3.12.14. They cover full-width
  arithmetic, wraparound, shifts, parsing, source tampering, finite execution,
  cancellation, and allocation bounds for malformed source.
- Ten additional build-boundary tests passed for source filtering, optional
  hooks, unsafe archive members, invalid input locks, and tampered caches.
  The full standard-library suite contains **40 passing tests**.
- The default 1,048,576-bit CLI self-test passed its seven checks, including
  source round-trip and deterministic replay.
- The Python distribution built successfully as a wheel without runtime
  dependencies.
- The source-only overlay passed structural validation. It admits explicit
  source roots and keeps the optional application hook out by default.
- A newly generated x86-64 ISO booted in an isolated QEMU BIOS guest. The
  guest reported Linux 6.18.52-0-virt, installed signed local Python packages
  without networking, and printed a passing full-width self-test between
  the two boot self-test markers. No host disks or folders were attached.
- The same image booted under UEFI and passed the same seven checks. The
  guest's `/sys/firmware/efi/fw_platform_size` reported `64`. Both guests
  were stopped after verification.

The BIOS- and UEFI-tested image was 82,919,424 bytes with SHA-256:

```text
a5e8f81a38d78558c1a52955ae8d2816d3c7cb958a53234212b86f565e514300
```

That image was used for validation and is not a release asset in this
source-only repository. Rebuilding can produce a different ISO checksum
because the whole ISO is not promised to be bit-for-bit reproducible.
Each builder invocation writes a new receipt and correctly leaves
`boot_tested: false` until that particular output is actually tested.

The sanitized [BIOS receipt](evidence/boot-bios.json) maps the tested runtime
sources to their SHA-256 hashes. The [UEFI receipt](evidence/boot-uefi.json)
records the independent firmware confirmation. The archive validator was
hardened after image construction; the guest payload sources were unchanged
and checked byte-for-byte against the tested image. Raw logs are not published.

Known UEFI preview behavior: the inherited Alpine EFI loader first searches
for the original Alpine volume label, then falls back to the optical disc
and starts the generic GRUB configuration successfully. The observed startup
message does not prevent the verified UEFI boot.

The checked-in demonstration produces these stable program and trace hashes:

```text
program_sha256  8fdfb297b43428e3bf90d88f5572eca494dd80aa13341ab8ac5d6bb3c004dba3
trace_sha256    d7bd9f6ea37f2669adf5fa999ec3b3113493eab932938bb44828f3d588f0cddc
```

## Re-run and interpret the checks

Use the README's unit-test and self-test commands, then follow the build
guide to generate and boot your own image. The GitHub Actions workflow runs
the runtime checks on Python 3.12 and 3.14; its actual run status is shown
in the repository's Actions tab. A configured workflow is not itself a
claim of a successful remote run or a VM boot test.

Physical hardware, Secure Boot, production hardening, persistent storage,
neural inference, arbitrary application integration, and compatibility with
the complete BASIC or LCTL languages are outside this preview's qualification.
Guest state is intentionally volatile. The local root console has no password
and is intended only for the isolated, network-free development VM described
in the build guide.
