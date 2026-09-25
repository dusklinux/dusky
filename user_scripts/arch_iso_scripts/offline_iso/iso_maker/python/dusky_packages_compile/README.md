# Local packages built into the ISO

The ISO generator automatically builds every immediate subdirectory containing
`recipe.toml` and `PKGBUILD`. Each recipe produces one generic x86-64 pacman
package. The package is added to the ISO's offline repository. After the base
system is installed in offline mode, `131_chroot_aur_packages.sh` installs each
exact archive through the `/offline_repo` bind mount. The online recovery
profile uses online package sources and skips these ISO-built archives; the
userspace selector setup builds a native binary if no package is installed.
There is no package-name list to edit elsewhere.

To add a package, copy `TEMPLATE` to a new directory, rename the two `.example`
files, and fill in the package name, source path, tools, build command, runtime
dependencies, and install path. `source` is relative to the dotfiles checkout
that the generator injects into `/etc/skel`; commit and push the recipe and source
before building an ISO. The generator checks that local recipes match the
injected Git checkout, then copies the source into a user-owned build
directory and passes its path as `DUSKY_PACKAGE_SOURCE` to `makepkg`.

The factory's makepkg configuration uses generic x86-64 compiler flags even if
the ISO builder's own makepkg configuration uses `-march=native`. Recipes should
also set an explicit generic target for compilers they invoke. Bump `pkgver` or
`pkgrel` whenever the packaged program changes so pacman can identify updates.

Keep generated binaries and package archives out of Git. A package installed
from the ISO remains installed after the offline repository is removed, but
later binary updates require a new package source or another update mechanism.
