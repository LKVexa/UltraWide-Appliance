# Source provenance and third-party components

The 0.2 preview includes the recovered BASIC-1048576 Python reference and an
adapted assembler supplied with BASIC 1048576 Universe JA-20 4.0.0. The reference
is byte-for-byte preserved; the assembler's relative import is redirected.
New bounded execution, LCTL transport, BRIM/word-chain, repository and build
code lives separately. [SHS-AUDIT.md](docs/SHS-AUDIT.md) records the source
inventory and original system limits; [PROVENANCE.json](src/ultrawide/shs/PROVENANCE.json)
records exact source identity and intentional wrapper changes.

The supplied publisher is LK/Vexa a Linear Finance Lab. Its original package
included a draft preview notice, retained at
[SUPPLIED-PREVIEW-NOTICE.md](docs/licenses/SUPPLIED-PREVIEW-NOTICE.md) as source
provenance, including its original draft/review status. The repository's
[Product Preview Tester License](LICENSE) states the terms of this requested
public preview to the extent the Licensor has rights to the included work.
Neither that license nor this notice grants rights to unrelated archives,
third-party code, or separately owned materials.

The BRIM implementation was developed against the supplied independent
BR-480 reader and candidate policy runtime. Exact source identities and
compatibility boundaries are recorded in the audit and [BRIM.md](docs/BRIM.md).
The candidate's binary policy collection, signing keys, native compiler,
application data, models and corpus archives are not redistributed here.

The build and launch procedures obtain or use these independent components:

| Component | Role | License/source information |
| --- | --- | --- |
| Alpine Linux and its packages | Guest operating system and Python dependencies | [Package database](https://pkgs.alpinelinux.org/) and [source packages](https://gitlab.alpinelinux.org/alpine/aports) |
| Linux | Guest kernel | [Licensing rules](https://www.kernel.org/doc/html/latest/process/license-rules.html) |
| Python | Runtime and build scripts | [Python license](https://docs.python.org/3/license.html) |
| pycdlib | Optional ISO authoring | [Source and license](https://github.com/clalancette/pycdlib) |
| QEMU | Optional virtual machine | [QEMU license](https://www.qemu.org/docs/master/about/license.html) |
| setuptools | Optional package installation | [Source and license](https://github.com/pypa/setuptools) |

Each remains subject to its own terms. The preview license does not relicense
these dependencies. A source checkout and a built Linux image are different
distributions: redistribution of an image must retain applicable notices and
satisfy the included packages' source and other obligations. Input hashes and
package license identifiers are recorded in `config/alpine-lock.json`; that
manifest is not a substitute for the underlying license terms.
