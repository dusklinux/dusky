---
title: "VM RAM — capacity, fixed allocation and ballooning"
tags:
  - kvm
  - manual
reviewed: 2026-10-25
---

# VM RAM — capacity, fixed allocation and ballooning

[[+ MOC KVM|KVM home]] · [[00 Index — VM Creation (Unified)|Virtual hardware]]

Choose RAM in GiB according to the workload and host budget. The Windows script
always offers **8 GiB by default**, accepts custom positive whole amounts such
as 4 or 50, and separately asks whether to enable ballooning (**No by default**).
The amount is not silently clamped to the host. Windows 11's documented RAM
floor is 4 GB; the rest of the system's requirements still apply.

An 8 GiB host cannot comfortably supply an 8 GiB guest plus Linux, QEMU and
applications. RAM-backed guest disks/files also consume host memory. Selecting
more than physical capacity is overcommit, not free capacity; it can lead to
swap pressure or OOM. The script displays host RAM and warns about low headroom.

## Understand the three values

- **Capacity:** configured guest memory; raising it can require a powered-off edit.
- **Balloon target:** how much of that capacity the guest may currently use.
- **Physical host use:** pages actually resident/backed, plus VM overhead.

QEMU can allocate backing pages on demand, but Windows can touch most of them.
Freeing a Windows application does not automatically return those pages to
Linux. RSS also includes overhead/shared mappings; a single number is not a
complete host RAM accounting model.

Fixed example, eight GiB:

```xml
<memory unit='MiB'>8192</memory>
<currentMemory unit='MiB'>8192</currentMemory>
<!-- inside devices -->
<memballoon model='none'/>
```

Optional VirtIO balloon device, replacing the `none` device:

```xml
<memballoon model='virtio'>
  <stats period='5'/>
</memballoon>
```

Windows needs the VirtIO Balloon driver and BalloonService, even with SATA and
e1000e. Typical Linux guests use their virtio_balloon driver. Device support and
free-page-reporting support are not interchangeable; do not assume a setting
advertised by QEMU is implemented by the chosen Windows driver.

## Manual live adjustment

For a running balloon-enabled guest with eight GiB capacity:

```sh
virsh -c qemu:///system dommemstat win11
virsh -c qemu:///system setmem win11 6G --live
virsh -c qemu:///system dommemstat win11
virsh -c qemu:///system setmem win11 8G --live
```

An accepted command is not enough: watch the actual target and physical host
use. Guest statistics can take an interval to update. `--live` alone leaves the
next-boot target unchanged; `--config` changes persistent intent when supported.
Targets cannot exceed configured capacity. To change a fixed eight GiB VM to
50 GiB, power it off, edit its memory/currentMemory values consistently, then
start it on a host able to support the request.

Ballooning does not install an automatic host/guest demand controller. Reclaiming
too aggressively can evict useful caches, force paging and cause stutter.
VFIO DMA mappings can prevent ordinary balloon reclamation; fixed mode is the
recommended default for GPU passthrough. VirtIO-FS shared memory alone did not
prevent balloon reclamation in this test. Looking Glass configuration now
preserves an explicit balloon choice instead of overwriting it.

## Observed test

Without PCI hostdevs, targets 8 → 6 → 4 → 8 → 6 → 8 GiB succeeded with VirtIO-FS.
At four GiB, QEMU RSS fell by about four GiB and host shared-memory use fell
similarly. A 512 MiB application buffer retained its complete SHA-256 hash
across shrinking/growing. The VM was returned to fixed eight GiB.
A physical eight GiB host, a running 50 GiB guest and balloon reclamation with
VFIO were not tested. No application-performance percentage was established.

Source: [libvirt memory balloon device](https://libvirt.org/formatdomain.html#memory-balloon-device).

---

Previous: [[05 CPU — Host-Passthrough & Topology]]. Continue: [[+ MOC Windows Installation Through Virt Manager#3. Install Windows and guest tools|Install Windows after completing hardware review]].
