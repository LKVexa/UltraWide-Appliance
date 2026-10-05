# Third-party components

This repository contains original preview scaffold source. It does not bundle
the earlier application candidate, recovered BASIC implementation, language corpora,
model weights, operating-system binaries, package archives, or QEMU.

The build and launch procedures obtain or use these independent components:

| Component | Role | License/source information |
| --- | --- | --- |
| Alpine Linux and its packages | Guest operating system and Python runtime | [Alpine package database](https://pkgs.alpinelinux.org/) and [source packages](https://gitlab.alpinelinux.org/alpine/aports) |
| Linux | Guest kernel | [Linux licensing rules](https://www.kernel.org/doc/html/latest/process/license-rules.html) |
| Python | Runtime and build scripts | [Python license](https://docs.python.org/3/license.html) |
| pycdlib | Optional ISO authoring dependency | [Upstream source and license](https://github.com/clalancette/pycdlib) |
| QEMU | Optional virtual-machine launcher | [QEMU license](https://www.qemu.org/docs/master/about/license.html) |
| setuptools | Optional Python package installation | [Upstream source and license](https://github.com/pypa/setuptools) |

Each remains subject to its own terms. The Product Preview Tester License
applies to this repository's original work and does not relicense any of
these dependencies. The source scaffold and a built appliance image are
different distributions: before redistributing an image, include the
applicable notices and satisfy the source and other obligations of its
included packages. The build manifest records exact input versions and
hashes; it is not a substitute for those license obligations.
