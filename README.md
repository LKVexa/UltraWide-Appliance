# UltraWide Appliance

**Product preview · generic source scaffold · 1,048,576-bit software words**

UltraWide Appliance is a small starting point for an independently bootable
Linux appliance that runs a bounded ultra-wide-word demonstration. It includes
a Python runtime, examples, tests, and a source-only appliance build kit.
The hardware remains ordinary x86-64; Python performs the wide arithmetic
in software.

The default virtual word is **1,048,576 bits (128 KiB)**. This preview uses
eight registers and a small straight-line instruction set. Its eight-column
`UWA/0.1` program format is an independent scaffold inspired by columned
execution descriptions. It is not the supplied LCTL compiler or a compatible
replacement for the complete BASIC instruction set. Linux and the Python
runtime are not rewritten in LCTL, and this is not compiler self-hosting.
See [the program format](docs/FORMAT.md) for the columns, instructions,
resource bounds, and source-digest rules.

## Try the runtime

Use Python 3.12 or newer. The runtime uses only the Python standard library.
From a checkout of this repository:

```sh
git clone https://github.com/LKVexa/UltraWide-Appliance.git
cd UltraWide-Appliance
PYTHONPATH=src python3 -m ultrawide self-test
PYTHONPATH=src python3 -m ultrawide demo
PYTHONPATH=src python3 -m ultrawide run examples/high-bit.uwa
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

PowerShell:

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
py -3 -m ultrawide self-test
py -3 -m ultrawide demo
py -3 -m ultrawide run examples/high-bit.uwa
py -3 -m unittest discover -s tests -v
```

The demo sets the highest bit of a default-width word, exercises wraparound,
and returns compact JSON evidence. It does not print a megabit-sized number.
To generate another sealed example, use
`python -m ultrawide demo --bits 256 --write-source example-256.uwa` with
`PYTHONPATH` configured as above. An existing file is never overwritten.

Optional package installation in a virtual environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
ultrawide self-test
```

## Build a Linux appliance

See [the build guide](docs/BUILD.md) for pinned inputs, ISO generation,
console startup, and QEMU usage. Building fetches upstream operating-system
components into a local cache. No installer, ISO, APK archive, QEMU binary,
or model weight is included in this repository.

The generic appliance is a console preview. Add your own application through
the documented optional application hook after reviewing its behavior and
license. Guest network access is disabled in the default launcher. Build
scripts operate on files and do not partition or format host drives.

## What is included

| Path | Purpose |
| --- | --- |
| `src/ultrawide/` | Fixed-width words, custom program parser, execution, and CLI |
| `examples/` | Small sealed source examples |
| `tests/` | Arithmetic, parsing, execution, and error-boundary checks |
| `appliance/` | Guest boot configuration and generic application boundary |
| `scripts/` | Build, verification, and VM helpers |
| `config/` | Pinned upstream build inputs |
| `docs/BUILD.md` | Build instructions and qualification limits |

## Preview boundaries

- Arithmetic wraps modulo `2^bits`; shifts are logical and bounded.
- Programs have a finite instruction/step budget and no filesystem,
  network, native-code, or arbitrary Python execution instructions.
- The source digest detects changes; it is not a digital signature or
  proof of author identity. Resource bounds are not an isolation boundary
  for running arbitrary untrusted applications.
- The original application, recovered BASIC source, supplied language
  corpora, neural weights, and previous appliance test logs are excluded.
- Verification of the earlier full appliance does not qualify this new
  scaffold. See [verification status](docs/VERIFICATION.md) for what has
  actually been checked here.

## License and feedback

[Product Preview Tester License 1.0](LICENSE) permits evaluation, testing,
internal experiments, and preview forks under its stated terms. Production
deployment, customer-facing services, and commercial distribution require
separate written permission. This is a source-available preview with a
custom license, not an OSI-approved open-source release.

Third-party components retain their own licenses; see
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). Report reproducible issues
through [GitHub Issues](https://github.com/LKVexa/UltraWide-Appliance/issues).
The [contribution guide](CONTRIBUTING.md) describes useful test reports.
