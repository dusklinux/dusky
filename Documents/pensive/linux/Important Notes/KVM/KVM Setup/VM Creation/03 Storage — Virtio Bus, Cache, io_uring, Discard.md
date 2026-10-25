---
title: "Guest storage — format, bus, caching and discard"
tags:
  - kvm
  - manual
aliases:
  - Storage Unified
reviewed: 2026-10-25
---

# Guest storage — format, bus, caching and discard

Start with one simple virtual disk. A guest disk's controller/bus, image format,
cache policy and host filesystem are independent choices. No combination in
this note is a universal performance winner; measure your actual workload.

## Format and creation

For a new image, with an unused destination filename:

```sh
qemu-img create -f qcow2 -o cluster_size=64k,lazy_refcounts=on /path/to/new.qcow2 80G
qemu-img info /path/to/new.qcow2
```

`80G` is virtual capacity. Sparse host allocation grows as the guest writes.
`lazy_refcounts` trades some crash-recovery work for less metadata updating;
it does not preserve data through host RAM loss. Raw images avoid qcow2 metadata,
but external overlays/snapshots remain possible; “raw has no snapshots” is too
broad. Backing chains must be preserved together.

## Bus and Windows drivers

| Device | Windows driver | Linux driver / use |
|---|---|---|
| VirtIO block (`bus='virtio'`) | `viostor` | `virtio_blk`; straightforward single disk |
| VirtIO SCSI controller + SCSI disk | `vioscsi` | `virtio_scsi`; SCSI functions/many disks |
| Emulated SATA | Inbox AHCI | Common inbox driver; explicit compatibility choice |

For a VirtIO block disk, load **viostor**, not vioscsi, during Windows Setup.
Do not change an installed Windows boot disk's bus until its destination driver
is installed and the transition is planned; otherwise Windows may not boot.

## Persistent-file baseline

The Windows deployment uses this disk driver policy:

```xml
<disk type='file' device='disk'>
  <driver name='qemu' type='qcow2' cache='none' io='io_uring' discard='unmap'/>
  <source file='/path/to/new.qcow2'/>
  <target dev='vda' bus='virtio'/>
</disk>
```

`cache='none'` requests direct host I/O, avoiding a second host file-data cache.
It is not “never cache anything”: Windows still caches. `io='io_uring'` chooses
an asynchronous host API. Filesystem support and actual backend behavior matter.
Do not call one API lowest-overhead on every device without measuring it.

The separate Linux deployment deliberately uses a different RAM-storage policy.
Tmpfs-backed images need an appropriate buffered path; do not prescribe O_DIRECT
as a RAM-filesystem speed optimization or claim both deployment scripts choose
identical disk policies. For a manual tmpfs file image, `cache='writeback'` with
`io='threads'` is a buffered starting point, subject to local validation. Retain
normal guest flushes; do not substitute `cache='unsafe'` for performance claims.

## Discard and maintenance

`discard='unmap'` allows guest discard requests to pass through; it does **not**
generate those requests. Guest TRIM/retrim still matters, and the backing chain
and filesystem determine how much host allocation can be released. Windows
ScheduledDefrag includes optimization/retrim work; do not disable it because
`unmap` exists. See [[Disable Unnecessary Scheduled Tasks]].

An IOThread or multiple queues can help a demonstrated bottleneck. They also add
configuration and host scheduling work. Do not assume unspecified queue count
equals vCPU count or paste a machine-specific pinning map. If needed, obtain
valid `<driver><iothreads>…</iothreads></driver>` syntax from the installed libvirt
schema rather than putting a guessed `<iothreads>` child directly under a disk.
The baseline here needs no manual queue mapping.

Inspect running domains with `virsh domblklist win11 --details` and their XML.
Use qemu-img modifications/checks only on stopped, unowned images; do not bypass
QEMU's image locking. Growing a Windows disk:
[[Resize aka extend storage after os is already installed]].
Sources: [QEMU image utility](https://www.qemu.org/docs/master/tools/qemu-img.html),
[QEMU disk/cache options](https://www.qemu.org/docs/master/system/invocation.html),
[libvirt disk XML](https://libvirt.org/formatdomain.html#hard-drives-floppy-disks-cdroms).
