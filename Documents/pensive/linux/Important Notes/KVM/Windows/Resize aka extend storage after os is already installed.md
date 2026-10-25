---
title: "Grow a Windows virtual disk and its volume"
tags:
  - kvm
  - manual
  - windows
reviewed: 2026-10-25
---

# Grow a Windows virtual disk and its volume

[[+ MOC KVM|KVM home]] · [[+ MOC Windows Installation Through Virt Manager|Windows route]]

Image capacity and the partition/filesystem inside it are different layers.
This is a **grow** procedure, not a shrink recipe. First preserve a consistent
backup: [[08 Backup and Restore — Disks, Firmware and TPM]].

## 1. Discover the stopped guest's actual disk

```sh
virsh -c qemu:///system domstate win11
virsh -c qemu:///system domblklist win11 --inactive --details
```

Require **shut off** before using qemu-img to modify its image. If it has a
backing chain/snapshots, understand that chain first; changing the wrong layer
is not a growth procedure. For a standalone qcow2, replacing the path below:

```sh
qemu-img info --backing-chain /path/to/win11.qcow2
qemu-img resize -f qcow2 /path/to/win11.qcow2 +20G
qemu-img info /path/to/win11.qcow2
```

This increases virtual capacity. It does not guarantee physical host free space
when Windows eventually fills it, and it does not extend C: automatically.

## 2. Start Windows and inspect the layout

Open Disk Management, rescan if necessary and identify the correct disk/volume.
If unallocated space is immediately after C:, choose Extend Volume. The supported
PowerShell route is `Get-PartitionSupportedSize` / `Resize-Partition` for the
actual volume, after inspecting its supported maximum.

If a recovery partition lies between C: and the new space, Windows cannot extend
C: across it using the simple contiguous-space operation. Do not paste a fixed
`disk 0` / partition number or delete Recovery blindly. Options are to use the
space as a separate data volume, perform a planned backup-assisted repartition,
or follow the Windows build's documented WinRE relocation procedure. Preserve
WinRE and encrypted-volume recovery information. Moving partitions with another
OS's live tool is not automatically the safer operation, especially for encrypted
or active Windows filesystems.

Verify the resulting partition/filesystem size and that Windows recovery/boot
still works. No live guest disk was resized during this documentation review.
Sources: [QEMU image resize](https://www.qemu.org/docs/master/tools/qemu-img.html),
[Microsoft volume extension requirements](https://learn.microsoft.com/en-us/windows-server/storage/disk-management/extend-a-basic-volume).
