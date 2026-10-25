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

## Optional: create the RAM-backed mount on a fresh host

If `/mnt/zram1` is already mounted, keep that existing setup. The pathname alone
does not create tmpfs or a zram device. For a new, empty mount directory, this
manual tmpfs example uses a limit of 25% of physical RAM:

```sh
sudo mkdir -p /mnt/zram1
sudo mount -t tmpfs -o size=25%,mode=1777,nosuid,nodev tmpfs /mnt/zram1
findmnt --mountpoint /mnt/zram1
```

Do not mount over existing files or a live VM's source. `size=25%` is an example
capacity limit, not a reservation or a recommended VM disk size on every host.
Tmpfs grows with its contents and can use configured swap. Budget it together
with guest RAM and host applications; review the RAM budget below.

For the same mount after host boot, add one entry to `/etc/fstab` after checking
that no existing fstab entry or mount service already manages that path:

```fstab
tmpfs /mnt/zram1 tmpfs rw,nosuid,nodev,mode=1777,size=25% 0 0
```

Use `sudo systemctl daemon-reload` after editing fstab. Mount it before starting
VMs that depend on it. This recreates the filesystem, not its previous contents
or custom child directories. Persistent VM disks can stay on media while this
mount is used only for shared files.

Reference: [kernel tmpfs options](https://docs.kernel.org/filesystems/tmpfs.html).

## Create a directory pool

Choose the path and an unused pool name. These example assignments are explicit:

```sh
vm_pool_dir=/var/lib/libvirt/images
vm_pool_name=manual-images
# Create only a missing directory; do not reset an existing directory's mode.
if [ ! -d "$vm_pool_dir" ]; then
  sudo install -d -m 0711 -- "$vm_pool_dir"
fi
```

Now apply [[Set ACL on the Image Directory|the directory and parent ACLs]] for
the discovered operator/QEMU accounts. Return here after verifying that access.
Check whether a pool already covers this path:

```sh
virsh -c qemu:///system pool-list --all
# Inspect likely existing pools before defining another:
# virsh -c qemu:///system pool-dumpxml EXISTING_POOL_NAME
```

Only for a new pool name/path relationship, using the variables above:

```sh
virsh -c qemu:///system pool-define-as "$vm_pool_name" dir --target "$vm_pool_dir"
virsh -c qemu:///system pool-start "$vm_pool_name"
virsh -c qemu:///system pool-autostart "$vm_pool_name"
virsh -c qemu:///system pool-refresh "$vm_pool_name"
```

Reuse a pool already covering the directory instead of defining duplicates.
Autostarting a pool does not unlock
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
With directory access and the pool ready, continue with
[[Activating Network and Setting it to Autostart]].
