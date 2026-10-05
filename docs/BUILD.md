# Build the generic Linux preview

This source scaffold builds a new x86-64 Alpine optical boot image containing
the independent `ultrawide` Python package and its console interface. It uses
the custom **UWA/0.1** subset; it is not an LCTL compiler or a compatibility
implementation of another virtual machine. No private application, model,
corpus, recovered interpreter, emulator binary, APK, repository-index snapshot
or prebuilt ISO is included in this repository.

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

The local maintenance console accepts `root` with no password. This is a
deliberately isolated development image, not a hardened multi-user deployment.
At the console, try:

```sh
ultrawide self-test
ultrawide demo
ultrawide run /opt/ultrawide/examples/high-bit.uwa
poweroff
```

All guest state is volatile and disappears at shutdown. The boot scripts do
not partition, format or attach disks. The provided launcher selects legacy
BIOS. Local BIOS and 64-bit UEFI smoke tests are recorded in
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

Only `src/ultrawide/*.py` (including subdirectories), `examples/*.uwa`, the two
known appliance scripts and existing project README, license and third-party
notices enter the default source overlay. Cache directories, test logs and unrelated files are
not swept into the image. User source symlinks are rejected.
