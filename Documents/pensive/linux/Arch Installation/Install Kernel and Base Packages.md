# Install the base system

Run these commands from the live environment after mounting the target root at `/mnt` and the ESP at `/mnt/boot`. [[Mounting Partitions]] describes the simple Btrfs layout. A working repository configuration, available packages and a usable pacman keyring are prerequisites; your offline ISO supplies these differently from an online installation.

```bash
pacstrap /mnt base linux-zen linux-firmware mkinitcpio btrfs-progs neovim
```

`base` supplies the minimal base system; `linux-zen` supplies the kernel and its preset; `linux-firmware` supplies firmware; `mkinitcpio` explicitly selects the initramfs generator assumed by the later steps; `btrfs-progs` supplies Btrfs user-space tools; `neovim` is an optional editor. Choose an appropriate kernel and firmware for the target. Install `linux-zen-headers` only when building external modules for that kernel, and `base-devel` when building packages. Network management software must be installed and configured separately for the new system.

For early CPU microcode, add the appropriate `intel-ucode` or `amd-ucode` package for the target CPU. Then check the mkinitcpio `microcode` hook and rebuild the images as described in [[Configuring the Initial RAM File System]].

For an online install the official guide uses `pacstrap -K` to initialize a fresh target keyring. The locally installed `pacstrap -h` documents that `-K` implies `-G`, so it does not copy the host keyring. For an offline ISO, use the repository and keyring procedure supplied with that ISO; do not assume a newly initialized keyring can validate its offline packages. `pacstrap` normally copies the live environment's keyring and mirror list. Inspect its output and resolve errors before continuing to [[Generate the fstab File]].

Alternative official kernels include `linux` and `linux-lts`; match the kernel, optional headers, initramfs and boot entry names. Repository contents and versions must be checked on the actual ISO. Reference: [pacstrap(8)](https://man.archlinux.org/man/pacstrap.8).
