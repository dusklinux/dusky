---
title: "Storage Performance — Disk Bus / Cache / io_uring (Windows Delta)"
tags:
  - kvm
  - storage
  - virtio
  - windows
  - qemu
aliases:
  - Windows Storage Stub
---

# Storage Performance — Disk Bus / Cache / io_uring (Windows)

> [!tip] Merged — canonical source
> **Shared bus/cache/io_uring/discard/queues + CLI lives in [[KVM Setup/VM Creation/03 Storage — Virtio Bus, Cache, io_uring, Discard]].** Follow that note for virt-manager **Disk bus → `VirtIO` / `VirtIO SCSI`**, **Cache `none` → `io_uring`/`native` → `unmap`**, and IOThread Virtqueue Mapping (QEMU 9.0+ blk / 10.0+ scsi). This stub keeps **only Windows-specific driver-load** delta.

## Windows delta

| Canonical | Windows note |
|---|---|
| `VirtIO` / `VirtIO SCSI` + `cache=none` + `io=io_uring` + `discard=unmap` | **identical** — set before first boot; pipeline `30_*:build_command --disk …bus=virtio,driver.cache=none,driver.io=io_uring,driver.discard=unmap`. (Use `driver.cache=writeback` for RAM-backed pools like `/mnt/zram1` to avoid O_DIRECT bypass penalties per canonical [[03 Storage — Virtio Bus, Cache, io_uring, Discard]]). |
| Queue count auto = vCPU, IOThreads binding | **identical** — see canonical Step 4 XML; Red Hat 4–8 iothreads, pin away from vCPUs |
| `qcow2` vs `raw` | **identical** — `qcow2` `cluster_size=64k,lazy_refcounts=on` thin; `raw` faster but no snapshots |

**Windows-only step — storage driver at install (no Linux equivalent):**

At **Where do you want to install Windows?** (empty list):

1. **Load driver → Browse → CD Drive (E:)** (`virtio-win.iso` 2nd CDROM from [[Mount the VirtIO-Win ISO Image]])
2. **`viostor → w10` or `w11` → `amd64` → OK**
3. **Red Hat VirtIO SCSI controller → Next** → drive appears → select → Next

Without this, `virtio` disk stays invisible (Windows has no in-kernel virtio). After install, `virtio-win-guest-tools.exe` keeps `viostor`/`vioinput` installed.

> [!info] Linux route — no driver load
> Arch/Fedora kernels ship `virtio_blk` built-in — disk `vda` appears immediately; no 2nd ISO, no `Load driver`. Canonical note marks this explicitly.

## Verify

```bash
virsh -c qemu:///system dumpxml win11 | grep -A2 "<driver name='qemu'"
# cache='none' io='io_uring' discard='unmap'
qemu-img info /var/lib/libvirt/images/win11.qcow2 | grep -E 'virtual size|cluster_size'
# inside Windows: Device Manager → Storage controllers → Red Hat VirtIO SCSI pass-through controller
```

See: canonical [[KVM Setup/VM Creation/03 Storage — Virtio Bus, Cache, io_uring, Discard]], [[Install a Windows Virtual Machine on KVM]] (§1 `viostor`), [[Mount the VirtIO-Win ISO Image]], [[Resize aka extend storage after os is already installed]] (grow).
