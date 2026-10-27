# Configure mkinitcpio

The initramfs is an early userspace image loaded before the real root filesystem is mounted. mkinitcpio builds it from the installed kernel, configuration and hooks. Kernel package installation normally generates images automatically; rebuild them after changing hooks or adding CPU microcode.

These commands run inside the installed system's chroot. Inspect `/etc/mkinitcpio.conf` and the kernel presets in `/etc/mkinitcpio.d/` before changing them:

```bash
cat /etc/mkinitcpio.conf
ls /etc/mkinitcpio.d/
mkinitcpio -H microcode
```

## Simple unencrypted Btrfs root

For the simple layout in [[Btrfs Subvolume Creation]], the installed default hooks and kernel normally suffice. Do not add `/usr/bin/btrfs` to `BINARIES` merely because root is Btrfs. Do not replace an existing `MODULES` or `HOOKS` array blindly: its content may support other hardware or storage. If a needed driver is missing from the image, investigate and explicitly add the relevant module when appropriate.

Install the target CPU's `intel-ucode` or `amd-ucode` package and ensure the `microcode` hook is in the active `HOOKS` array. The installed mkinitcpio hook embeds early microcode in a prepended, uncompressed image. When `autodetect` precedes `microcode`, it selects microcode for the CPU building the image; take this into account when preparing a system for different hardware. A separate microcode `initrd` entry is unnecessary when the hook embeds it.

## Encrypted root

An encrypted installation requires a matching early-unlock hook **and** kernel command line. The `encrypt` hook belongs to the busybox/udev approach; `sd-encrypt` belongs to the systemd approach. Do not mix instructions for the two. Ordering and parameters depend on the chosen design, so use the current [ArchWiki dm-crypt system configuration](https://wiki.archlinux.org/title/Dm-crypt/System_configuration). The unencrypted boot entry in [[Boot Loader systemd boot]] does not unlock a LUKS volume.

After any relevant changes, run `mkinitcpio -P` and inspect its output and resulting files; see [[Generating the Initramfs]].
