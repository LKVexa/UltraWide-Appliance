# Build the ultra-wide Linux preview

This build kit produces an x86-64 Alpine optical boot image containing the
restored BASIC machine, reversible LCTL-WIDE translation, BRIM compiler/runtime,
complete-program word chains, and explicit object storage. The older UWA demo
remains available. No private application, model, corpus, emulator binary,
APK, repository-index snapshot or prebuilt Linux ISO is included in this
source repository. The recovered BASIC reference and its provenance are included.

The build script can generate BIOS and UEFI boot entries. ISO construction and
source-overlay validation do not prove a successful boot. The script records
`boot_tested: false`; verify your newly built image before relying on it. No
results from a different appliance are acceptance evidence for this scaffold.

## Prerequisites

- Python 3.12 or newer, with `pip` and `venv`, on Linux, macOS or Windows.
- About 1 GiB of free build space; internet access for the explicit input fetch.
- Separately installed QEMU to run the result. No administrator installation or
  host disk changes are performed by these Python scripts.

From the repository root, create an isolated build environment. On POSIX:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes --only-binary=:all: -r scripts/requirements-build.txt
```

On Windows PowerShell, use `.venv\Scripts\python.exe` directly after
`python -m venv .venv`; activation is optional. The build dependency is the
exact hash-pinned [PyCdlib 1.21.0 wheel](https://pypi.org/project/pycdlib/1.21.0/).

## Inspect, fetch and build

```sh
python scripts/build_iso.py --check
python scripts/fetch_inputs.py
python scripts/fetch_inputs.py --verify-only
python scripts/build_iso.py
```

`--check` makes and validates an in-memory source-only overlay; it does not
claim to build or boot Linux. The fetcher writes only to `.cache/alpine` and
verifies the exact size and SHA-256 of every download. The builder operates
offline on that verified cache and refuses to replace an existing ISO or either
of its receipt files.
To retain multiple builds, supply `--output dist/another-preview.iso`.

The three normal outputs are:

- `dist/UltraWide-Appliance-preview-x86_64.iso`
- `dist/UltraWide-Appliance-preview-x86_64.iso.sha256`
- `dist/UltraWide-Appliance-preview-x86_64.build.json`

The build receipt identifies the actual bytes and says **ISO built, boot
unverified**. Overlay timestamps are fixed by the lock's `source_date_epoch`;
the complete ISO is not promised to be bit-for-bit reproducible across tool
versions or build times.

## Input identities and offline package installation

`config/alpine-lock.json` lists an exact official Alpine virt ISO and 19 public
packages: Python's dependency closure plus the matching OpenSSL command package
to keep the base ISO's OpenSSL installation consistent. Its pinned payload is about
83 MB before local tooling. The upstream base ISO retains its original base
packages, signed repository data and trusted signing keys. Additional Python
packages are supplied as explicitly named, verified local APK files.

During boot, the init hook runs `apk add --no-network --no-cache
--force-non-repository` over those local APK files. The last flag acknowledges
that these packages intentionally disappear at shutdown in a diskless guest;
it does not skip signature verification. APK's ordinary signature validation
stays enabled. There is
no `--allow-untrusted` option, refreshed repository index or guest package
download. Packages are installed into the disposable guest RAM filesystem.

The upstream Alpine package endpoints can retire old versions. A missing file
or changed checksum stops the fetch; do not silently replace a pin. Updating
the lock requires reviewing a new release, its complete dependency closure,
new hashes and licenses, then rebuilding and retesting. The repository does
not provide an automatic dependency updater. Alpine and PyCdlib retain their
own licenses; the project preview license does not relicense dependencies.

## Run and verify your build

```sh
python scripts/run_qemu.py
```

The launcher uses installed `qemu-system-x86_64`, software CPU emulation, 2 GiB
RAM, serial-console output, **no network interface**, and **no host disks**.
It does not install QEMU, mount host folders, create a persistent disk, enable
SSH or configure external access. Use `--memory` and `--cpus` to change the
bounded guest allocation. Software emulation can be slow.

Successful startup should print `ULTRAWIDE_GUEST_UNAME`, followed by the actual
`ultrawide self-test` JSON between `ULTRAWIDE_BOOT_SELF_TEST_BEGIN` and
`ULTRAWIDE_BOOT_SELF_TEST_END`. The self-test runs as an unprivileged account.
Record the ISO checksum and the new guest output yourself; do not treat this
expected sequence as a supplied boot receipt.

The console automatically runs as the unprivileged `ultrawide` account. Root's
password is locked. This remains an isolated development image, not a qualified
multi-user deployment. At the console, try:

```sh
ultrawide self-test
ultrawide shs run /opt/ultrawide/examples/shs-control-memory-stack.lctlw
ultrawide chain run /opt/ultrawide/examples/brim-word-chain.brw
```

All guest state is volatile and disappears at shutdown. The boot scripts do
not partition, format or attach disks. The provided launcher selects legacy
BIOS by default. Use `--uefi --firmware-dir /path/to/qemu/share` for QEMU edk2;
temporary variable storage is removed when QEMU exits. Use **Ctrl-A then X**
to stop the console VM. Local BIOS and 64-bit UEFI checks are recorded in
[VERIFICATION.md](VERIFICATION.md); verify new builds and firmware configurations
separately. Physical hardware has not been tested. The image
is not Secure Boot signed and does not contain a graphical desktop or browser.

The inherited Alpine UEFI loader may briefly report that its original volume
label was not found before falling back to the disc's generic GRUB menu. The
tested UEFI guest continued successfully; this preview does not customize the
embedded third-party EFI executable.

## Optional application integration

The default image has no application service or HTTP interface. To add your own
reviewed application, create `appliance/application.start` using
`appliance/application.start.example` as a starting point, and build with:

```sh
python scripts/build_iso.py --include-application-hook --output dist/custom-preview.iso
```

The hook is excluded unless that explicit flag is supplied. It starts as the
unprivileged `ultrawide` account after the core self-test. Application packaging,
dependencies, supervision, persistence, authentication and network access are
the integrator's responsibility. This flag does not enable guest networking.
Keep secrets and private data out of the repository and image source roots.

Only recursive `src/ultrawide/*.py`, its reference provenance record, explicit
example suffixes (`.uwa`, `.lctlw`, `.lctlb`, `.b1048576asm`, `.brimg`, `.brir`,
`.brw`, `.b1048576`, `.uwabundle`), three known appliance scripts and project
license notices enter the source overlay. Cache directories, test logs and
unrelated files are excluded. User source symlinks are rejected.

## Reproduce isolated firmware qualification

```sh
python scripts/qualify_boot.py --iso dist/UltraWide-Appliance-preview-x86_64.iso --output bios-check
python scripts/qualify_boot.py --iso dist/UltraWide-Appliance-preview-x86_64.iso --output uefi-check --firmware uefi --firmware-dir /path/to/qemu/share
```

Pass `--qemu /path/to/qemu-system-x86_64` when QEMU is not on PATH. Each check
requires a new output directory, attaches no host disks, folders or network
interface, records the exact ISO digest and actual guest self-test, confirms
firmware and the unprivileged console UID, and stops its QEMU process. Raw
serial logs remain local; publish only reviewed receipts. These checks do not
test physical hardware, Secure Boot or application integrations.
