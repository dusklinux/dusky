# Pacman keyring in the live environment

Pacman uses a keyring to verify package signatures. The live environment may already initialize its keyring; check its status and the ISO's instructions before changing it. An offline ISO may also have its own repository and signing keys, so follow its procedure rather than assuming the official Arch keys cover every package.

When a keyring needs initialization, the local `pacman-key --help` documents these commands:

```bash
pacman-key --init
pacman-key --populate archlinux
```

`--populate` loads keyring material already present in `/usr/share/pacman/keyrings`; it does not itself download the Arch keys. The relevant `archlinux-keyring` package and a usable clock must be present. A stale keyring may need updating from a trusted source; an offline installation cannot assume network access. Errors in key initialization or signature verification must be resolved before installing packages.

`pacstrap` normally copies the live keyring into the target, while `pacstrap -K` initializes a fresh target keyring. See [[Install Kernel and Base Packages]] and [ArchWiki: offline installation](https://wiki.archlinux.org/title/Offline_installation).
