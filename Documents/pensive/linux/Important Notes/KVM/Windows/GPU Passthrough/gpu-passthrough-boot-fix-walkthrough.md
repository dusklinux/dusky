---
title: "Recover a host display after VFIO boot configuration"
tags:
  - kvm
  - manual
  - windows
reviewed: 2026-10-25
---

# Recover a host display after VFIO boot configuration

[[+ MOC KVM|KVM home]] · [[+ MOC Windows Installation Through Virt Manager|Windows route]] · [[+ MOC Windows GPU Passthrough|GPU route]]

A black host display after assigning its GPU can mean the native host driver no
longer owns it. This is different from a black Windows setup console after the
Looking Glass helper changed the guest's active monitor. Diagnose which machine
and display is black before modifying anything.

## A working host shell is available

Use a spare GPU, a working TTY or an already configured SSH session. Stop any
VM using the device before changing its configuration. Follow the return section
of [[GPU Passthrough — Host Isolation and Return]]. For toggle-managed files,
`--unbind` stages the saved baseline; it does not perform a live GPU reset.
Verify image rebuilds/options before the planned reboot.

## Rescue media when normal boot is unusable

Boot suitable Arch rescue media. Discover actual devices and mounts:

```sh
lsblk -f
```

Unlock encrypted root if applicable. Identify the installed root subvolume and
mount it at `/mnt`; do not assume `@`, a disk number or a UUID from an example.
Read `/mnt/etc/fstab` and mount the installation's ESP, separate `/boot`, `/home`
and relevant subvolumes **at their actual locations** before rebuilding. An
unmounted ESP can make a rebuild write to an ordinary directory that firmware
never reads. Do not hide mount failures with `|| true` and continue.

```sh
cat /mnt/etc/fstab
findmnt -R /mnt
sudo arch-chroot /mnt
```

Inside the installation, inspect the particular files that introduced isolation:
modprobe rules, mkinitcpio drop-ins, the selected Type #1 entry or UKI command-line
source, and the toggle journal if used. Back them up before editing.

For a manual recipe, move aside only its own VFIO modprobe/module drop-ins and
remove only its selected IDs/graphics-blacklist tokens from the effective boot
options. Preserve root/encryption/other device options and the existing hook
chain. Do not run regexes that chop a whole `MODULES=(...)` array or wipe all boot
entries. Keep native GPU modules installed for the selected kernel.

```sh
mkinitcpio -P
```

Check the generated images and boot entry/UKI sources. `bootctl update` does not
replace that rebuild. Do not use the toggle's live-hardware status as proof of
future boot binding while inside rescue: sysfs belongs to the rescue kernel.
Preserve the old journal; after a manual recovery, reconcile it with the saved
baseline before asking the toggle to manage files again.

Exit the chroot, unmount the installed system cleanly and reboot only after
successful verification. If the native module still cannot load, inspect the
actual kernel/module package pairing and logs. No automatic nomodeset, GRUB,
dracut, single-GPU hot-detach or blanket blacklist removal is part of this
systemd-boot/mkinitcpio recovery route.
