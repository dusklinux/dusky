# systemd-boot for a UEFI installation

This example matches the simple manual layout: a FAT ESP mounted at `/boot`, and an **unencrypted** Btrfs root subvolume named `@`. It is distinct from the offline installer, which can create additional subvolumes and support other layouts. Commands below run **inside the installed system's chroot**, as root. For encrypted root, the kernel command line and initramfs hooks must also describe how to unlock it; this example is not sufficient.

systemd-boot reads EFI executables, kernels and initramfs images from the ESP or an XBOOTLDR partition. Here `/boot` is the ESP, so mount it before installing the kernel or generating images. Confirm the live environment was booted in UEFI mode; see [[Verify Boot Mode]].

## 1. Check the mount and kernel files

```bash
findmnt --mountpoint /boot
ls -lh /boot/vmlinuz-linux-zen /boot/initramfs-linux-zen*.img
```

The first command should identify the intended FAT ESP. The second example assumes the `linux-zen` kernel and mkinitcpio's conventional image names. Check the actual filenames and adjust the entry below if using another kernel or preset. The ESP needs enough free space for the installed kernels, initramfs images and boot loader; check with `df -h /boot`.

## 2. Install the boot loader

```bash
bootctl --esp-path=/boot --variables=yes install
```

On this system `bootctl` and the EFI binary belong to `systemd`. Verify the package and binary on the actual ISO/target because package layouts may change. The explicit `--variables=yes` requests firmware-variable updates even when the chroot is detected as a container. Without it, the default `auto` can skip those updates. In the installed systemd 262, `--graceful` is implicitly enabled in a chroot, so an exit status alone does not prove firmware variables were written. Inspect the output, verify the files on the ESP, and inspect the firmware boot entries with `efibootmgr -v` (install `efibootmgr` in the target if needed). `bootctl status` provides additional information. If variable access or entry creation fails, investigate before rebooting; the fallback EFI path may be usable, but firmware behavior varies.

## 3. Create a boot entry

Find the **filesystem UUID** of the Btrfs root partition, replacing the placeholder with the partition used in [[Formatting Partitions]]:

```bash
blkid -s UUID -o value /dev/root_partition
```

Create `/boot/loader/entries/arch.conf`, replacing the placeholder with that UUID:

```ini
title   Arch Linux (linux-zen)
linux   /vmlinuz-linux-zen
initrd  /initramfs-linux-zen.img
options root=UUID=ROOT-FILESYSTEM-UUID rootflags=subvol=@ rw
```

`root=` identifies the filesystem; `rootflags=subvol=@` selects its root subvolume. The paths are relative to the ESP, not to the root filesystem. Do not use the ESP UUID for `root=`. A separately mounted `/home` is handled by fstab. If your initramfs configuration embeds CPU microcode with mkinitcpio's `microcode` hook, the one initrd line above is sufficient; see [[Configuring the Initial RAM File System]]. If you instead use a separate microcode image, follow the current ArchWiki microcode instructions and list that image before the main initramfs.

For a fallback entry, copy this file to a second `.conf` file, change its title and point `initrd` at the fallback image, if that image exists. Check `bootctl list` and verify the entry contents and file paths before rebooting.

## 4. Menu and updates

Optionally create `/boot/loader/loader.conf`:

```ini
default arch.conf
timeout 3
```

The firmware-saved default or timeout can override these settings. To update an installed systemd-boot during later system updates, enable the packaged update service:

```bash
systemctl enable systemd-boot-update.service
```

Consult [systemd-boot on ArchWiki](https://wiki.archlinux.org/title/Systemd-boot) and `man bootctl`, `man loader.conf`, and `man systemd-boot` for supported layouts and options.
