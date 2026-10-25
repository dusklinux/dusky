---
title: "Storage Performance — Disk Bus / Cache / io_uring / iothreads (Unified)"
tags:
  - kvm
  - storage
  - virtio
  - qemu
  - arch
aliases:
  - Storage Unified
---

# Storage Performance — Disk Bus / Cache / io_uring / iothreads (Unified)

> [!abstract] Goal
> Near-bare NVMe speed via four levers: **bus**, **cache**, **async API**, **host-thread fanout**. Verified against **Linux 7.3+** + **QEMU 11.1** + **libvirt 12.6+** (Aug 2026). Pipeline `30_kvm_vm_deploy.py:build_command` uses `cache=none,io=io_uring,discard=unmap` for persistent storage, and `cache=writeback` for RAM-backed storage. **Identical for Windows and Linux** — only driver-load moment differs.

## Quick glance

- **Bus:** `virtio-blk` (`virtio`) for peak single-disk IOPS/throughput; `VirtIO SCSI` (`virtio-scsi`, “SCSI single” + dedicated IOThread) for multi-digit disk counts / full SCSI — *not* a “must SCSI” rule
- **Format:** `qcow2` (snapshots/thin) or `raw` (max speed, zero metadata overhead)
- **Cache:** `none` (O_DIRECT, no double cache)
- **IO API:** `io_uring` (simple single-IOThread) or `native` + IOThread Virtqueue Mapping (multi-queue scaling)
- **Discard:** `unmap` (TRIM passthrough)
- **Queues:** leave unset — auto = vCPU count on `virtio-blk` — then layer IOThreads for real parallelism

### Step 1 — Select disk

VM details (lightbulb) → **SATA Disk 1** (the volume you created in [[KVM Setup/VM Creation/01 Wizard — Create VM|01 Wizard]])

### Step 2 — Disk bus

- Right pane → **Disk bus** → `VirtIO` (`virtio-blk`) *or* `VirtIO SCSI`

> [!info] `virtio-blk` vs `virtio-scsi` in 2026
> QEMU docs still say `virtio-blk` has thinner stack → best single-disk IOPS; `virtio-scsi` wins past ~28 LUNs / when you need SCSI reservations. The old multiqueue gap **closed**: `virtio-blk` got IOThread Virtqueue Mapping in **QEMU 9.0**, `virtio-scsi` in **10.0** (Step 4). Proxmox et al default to “VirtIO SCSI single” + IOThread per disk for manageability — fine for most. Neither has exclusive discard advantage now. **Same choice for Windows and Linux.**

### Step 3 — Advanced options

1. **Cache mode** → `none`
2. **IO mode** → `io_uring` (or `native` if doing Step 4 mapping — Red Hat docs benchmark `native` with that feature, not `io_uring`)
3. **Discard mode** → `unmap`

> [!important] Triad fine print
> - **`none` vs `writeback`**:
>   - **Persistent disks (`ext4`/`xfs`/`btrfs`)**: use `cache=none` (`O_DIRECT`, bypassing host page cache, no double-cache, stable).
>   - **RAM-backed disks (`tmpfs`/`zram`)**: use `cache=writeback`! On RAM filesystems, `cache=none` conflicts with or bypasses the memory-backed buffer cache; `writeback` achieves full memory-speed I/O.
> - **`io_uring`**: lowest overhead API since Linux 5.1, but had ordering bug on BTRFS-backed disks fixed in **QEMU 10.2** (Dec 2025) — stay ≥10.2 (current 11.1). Hardened distros may set `kernel.io_uring_disabled` (60% of 2022 exploit bounties hit io_uring) — check `sysctl kernel.io_uring_disabled`. Linux 7.0 added BPF filtering for it.
> - **`unmap`**: TRIM guest→host, identical on both buses in modern QEMU.

Pipeline CLI equivalent (both OSes):

```bash
--disk path=/var/lib/libvirt/images/win11.qcow2,format=qcow2,bus=virtio,driver.cache=none,driver.io=io_uring,driver.discard=unmap
--disk path=/var/lib/libvirt/images/archlinux.qcow2,format=qcow2,bus=virtio,driver.cache=none,driver.io=io_uring,driver.discard=unmap
```

> [!info] Windows route — driver load
> Bus `virtio` is chosen **before first boot**, but Windows has no in-kernel virtio. At install the drive list will be **empty** until you load `viostor` from the 2nd CDROM (`virtio-win.iso`): **Load driver → Browse → `E:\viostor\w11\amd64` → Red Hat VirtIO SCSI controller** (see [[Install a Windows Virtual Machine on KVM]] §1). Without this, `virtio` disk stays invisible. Keep `virtio-win.iso` attached as SATA CDROM ([[Mount the VirtIO-Win ISO Image]]).

> [!info] Linux route — no driver load
> Arch / Fedora / Ubuntu kernels ship `virtio_blk` + `virtio_pci` built-in. No extra ISO, no “Load driver” step — disk appears immediately. After install, verify `lsblk` shows `vda` (virtio) not `sda`.

### Step 4 — NVMe parallelism (Queues + IOThreads)

- **Queue count:** unspecified already auto-matches vCPU count on `virtio-blk` — usually leave blank.
- **IOThreads:** by default one thread services all virtqueues → high queue count not parallel. **IOThread Virtqueue Mapping** (QEMU 9.0+ blk / 10.0+ scsi) binds virtqueues round-robin to multiple host threads.

> [!tip] Mapping example (4 vCPU, 2 iothreads, `virtio-blk`) — same for both OSes
> ```xml
> <domain>
>   <vcpu>4</vcpu>
>   <iothreads>2</iothreads>
>   <devices><disk type='file' device='disk'>
>     <driver name='qemu' type='raw' cache='none' io='native' discard='unmap'/>
>     <iothreads><iothread id='1'/><iothread id='2'/></iothreads>
>   </disk></devices>
> </domain>
> ```
> `virtio-scsi` mapping sits on the **controller** (one ctrl, many LUNs) — check `man virsh` for current attr syntax (new enough to verify per release). Needs `virt-manager` XML tab / `virsh edit`; not a dropdown in virt-manager 5.1. Red Hat: 4–8 iothreads, pin via `<iothreadpin>` away from vCPUs.

> [!warning] Version floor
> Needs QEMU 9.0+ (blk) / 10.0+ (scsi) + libvirt 10+ — met on any 2026 Arch. Older stack: “queues = vCPUs and stop.”

### Step 5 — Format

- For file images: `qcow2` (snapshots) vs `raw` (no block-translation). Passing raw physical NVMe via VFIO skips virtio entirely — peak but no migration/snapshots (see [[Host PC  Preparation for GPU isolation]]).

### Step 6 — Apply

**Apply** → next: [[KVM Setup/VM Creation/04 Network — Virtio NIC (NAT vs Bridge)]].

## Verify

```bash
virsh -c qemu:///system dumpxml win11 | grep -A2 "<driver name='qemu'"
virsh -c qemu:///system dumpxml archlinux | grep -A2 "<driver name='qemu'"
# expect: cache='none' io='io_uring' discard='unmap'
qemu-img info /var/lib/libvirt/images/win11.qcow2 | grep -E 'virtual size|cluster_size'
```

See: [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]], [[Mount the VirtIO-Win ISO Image]] (Windows ISO attach), [[Install a Windows Virtual Machine on KVM]] (viostor load), [[Resize aka extend storage after os is already installed]] (grow), `30_kvm_vm_deploy.py:provision_disk`.
