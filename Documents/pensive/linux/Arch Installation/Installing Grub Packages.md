 Part 1: GRUB Package Installation

This is the first step, where you install the necessary software packages for the GRUB bootloader using `pacman`.

### 1. Install Core and Optional Packages

Install the essential packages for a UEFI system. The command below includes `grub` itself and `efibootmgr` to interact with the UEFI firmware.

```bash
pacman -S --needed grub efibootmgr
```

`grub-btrfs` is optional snapshot integration; Btrfs itself does not require it. Snapshot discovery and booting depend on a compatible snapshot layout, configuration and any required initramfs support. Consult the package documentation and test recovery separately.

### 2. Install OS Prober (for Dual-Booting)

If you are setting up a dual-boot system (e.g., with Windows), you can install `os-prober` if you want GRUB to discover other operating systems automatically. This package allows GRUB to detect other operating systems on your machine.

```bash
pacman -S --needed os-prober
```

> [!NOTE]
> If you are only installing Arch Linux, you can skip this step.

***