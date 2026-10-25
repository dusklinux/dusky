---
title: "VM storage — persistent disks and RAM-backed experiments"
tags:
  - kvm
  - manual
aliases:
  - Relocating Storage to ZRAM
reviewed: 2026-10-25
---

# VM storage — persistent disks and RAM-backed experiments

A libvirt storage pool is an inventory of a host directory. It is not the guest
RAM allocation and does not make storage persistent. A qcow2 has a **virtual
capacity**, **allocated host space**, and possibly **backing images**; these are
three different things.

Choose a real directory on your intended filesystem rather than replacing
`/var/lib/libvirt/images` with a symlink. Persistent examples include that default
or a mounted media drive. `/mnt/zram1` is a local convention, not proof of zram:
on the tested host it is **tmpfs**. Zram block devices are compressed RAM devices;
tmpfs is a memory filesystem. Both lose data on host reboot.

```sh
findmnt --target /mnt/zram1
findmnt --target /var/lib/libvirt/images
df -h /mnt/zram1
free -h
```

For an external/encrypted disk, mount and unlock it before creating the directory
or starting the VM. Otherwise a missing mount can leave you writing to the host
root filesystem at the same pathname.

## Create a directory pool

Choose the path and an unused pool name. These example assignments are explicit:

```sh
vm_pool_dir=/var/lib/libvirt/images
vm_pool_name=manual-images
sudo install -d -m 0711 -- "$vm_pool_dir"
# Apply access for the actual operator/QEMU account first; see the ACL guide.
virsh -c qemu:///system pool-list --all
virsh -c qemu:///system pool-define-as "$vm_pool_name" dir --target "$vm_pool_dir"
virsh -c qemu:///system pool-start "$vm_pool_name"
virsh -c qemu:///system pool-autostart "$vm_pool_name"
virsh -c qemu:///system pool-refresh "$vm_pool_name"
```

Reuse a pool already covering the directory instead of defining duplicates.
`install -d` is for a directory you are provisioning; preserve the existing
permissions of an established mount root. Autostarting a pool does not unlock
an encrypted drive or guarantee its mount ordering.

## Budget RAM-backed disks explicitly

A sparse 80 GiB qcow2 need not immediately consume 80 GiB, but it can grow.
Budget actual/possible image growth **plus guest RAM plus host/app overhead**.
Compression and swap do not guarantee capacity or latency. The Linux deployment
helper has a particular conservative heuristic; it is not a universal filesystem
requirement or a measured performance rule for every VM.

Disk-cache settings belong to [[03 Storage — Virtio Bus, Cache, io_uring, Discard]].
Do not infer an I/O speedup merely from a RAM-backed pathname.

Before host reboot, preserve a stopped guest's complete disk chain and its
firmware/TPM state: [[08 Backup and Restore — Disks, Firmware and TPM]].
Next: [[Set ACL on the Image Directory]].
