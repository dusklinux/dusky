# Local packages built into the ISO

The ISO generator automatically builds every immediate subdirectory containing
`recipe.toml` and `PKGBUILD`. Each recipe produces one generic `x86_64` or architecture-independent `any` pacman
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
directory and passes its path as `DUSKY_PACKAGE_SOURCE` to `makepkg`. All recipe
files, including auxiliary files, must match the injected checkout.

After a successful build and offline dependency check, the generator publishes
the archive into the configured AUR repository and records its input fingerprint
in `custom_builds.json`. Subsequent ISO builds reuse the archive when the source
tree, recipe tree (including file permissions), and factory build settings match.
Reuse verifies the archive against the repository SHA256; missing or corrupt
archives rebuild automatically. Unchanged builds skip compilation, indexing the
archive with `repo-add`, and persistent repository publication. Source timestamps,
checkout paths, and unrelated dotfiles commits do not invalidate the cache.
Existing archives without fingerprints need one initial rebuild.

Use `--rebuild-local` to rebuild every local recipe deliberately, for example
after a toolchain or library ABI update, or when a recipe downloads mutable
remote sources. Host package versions and remote content are not cache inputs;
keep remote inputs pinned and bump the recipe when they change. Recipes remain
independent and should declare all inputs in their source tree or recipe tree.

The factory's makepkg configuration uses generic x86-64 compiler flags even if
the ISO builder's own makepkg configuration uses `-march=native`. Recipes should
also set an explicit generic target for compilers they invoke. Bump `pkgver` or
`pkgrel` whenever the packaged program changes so pacman can identify updates.

Keep generated binaries and package archives out of Git. A package installed
from the ISO remains installed after the offline repository is removed, but
later binary updates require a new package source or another update mechanism.

`dusky-network` uses the network meter's shared `setup.py --generic` builder:
frozen/offline Cargo, executable tmpfs, and explicit baseline x86-64 flags. The
package includes its exact binary/source manifest and disables makepkg stripping
because the builder already strips the executable. User setup step 154 reuses a
matching package without a compiler, or builds natively after source edits.
