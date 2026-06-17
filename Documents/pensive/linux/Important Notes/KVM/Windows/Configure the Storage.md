# Optimizing Virtual Disk Performance

For near-bare-metal speeds on modern NVMe drives, your storage configuration comes down to four largely independent levers: disk bus, caching behavior, the async I/O API, and how I/O work gets distributed across host threads. None of these are exclusive to one specific kernel or QEMU release — `io_uring` has existed since Linux 5.1 (2019), and virtio multiqueue is older still — so this note focuses on what's genuinely changed recently rather than re-certifying settings that have been stable for years.

Verified against current upstream sources as of **June 17, 2026**: Linux kernel **7.1** (released June 14, 2026 — three days old at time of writing) and QEMU **11.0** (released April 2026).


## ⚡ Quick Glance: The Optimal Settings

_Match your VM settings to this list, then read the linked step if you want the reasoning:_

- **Disk Bus:** `virtio-blk` or `virtio` for outright single-disk performance, or `VirtIO SCSI` (ideally "SCSI single" with a dedicated IOThread) for many disks or full SCSI features — see Step 2
- **Storage Format:** `raw` (max speed) or `qcow2` (snapshots / thin provisioning)
- **Cache mode:** `none`
- **IO mode / IO API:** `io_uring` for a simple single-IOThread setup, or `native` if pairing with IOThread Virtqueue Mapping — see Step 3
- **Discard mode:** `unmap`
- **Queues:** leave unset to auto-match your vCPU count, then layer on IOThread Virtqueue Mapping for real scaling — see Step 4

### Step 1: Select the Storage Device

1. Open the virtual machine details view (the lightbulb icon).
2. From the left sidebar panel, locate and select your primary disk (e.g., **SATA Disk 1**).

### Step 2: Set the Optimal Disk Bus

1. In the right-hand details pane, locate the **Disk bus** dropdown.
2. For the highest possible single-disk IOPS/throughput, select `VirtIO` (this is `virtio-blk`). For many disks, SCSI passthrough, or persistent reservations, select `VirtIO SCSI` instead.

> [!info] virtio-blk vs VirtIO SCSI — there's no universal winner QEMU's own device configuration guidance is blunt about this: prefer `virtio-blk` in performance-critical use cases, thanks to its thinner software stack, and prefer `virtio-scsi` once you need more than roughly 28 disks on one controller or full SCSI functionality. That guidance hasn't moved.
> 
> What's changed is that `virtio-scsi` closed most of its historical multiqueue disadvantage in QEMU 10.0, when it gained **IOThread Virtqueue Mapping** — the same scaling feature `virtio-blk` got a release earlier, in QEMU 9.0 (see Step 4). It's also why major QEMU-based platforms, Proxmox among them, default to recommending "VirtIO SCSI single" with a dedicated IOThread per disk: the scalability and manageability usually outweigh `virtio-blk`'s raw-IOPS edge for typical workloads. If you're chasing the single highest IOPS number on one disk and don't need SCSI features, `virtio-blk` is still the documented choice. For nearly everything else, `VirtIO SCSI` remains a perfectly reasonable default.
> 
> One correction to the original reasoning: both controllers support discard/TRIM in current QEMU, so that's no longer a reason to pick `virtio-scsi` over `virtio-blk`. "Flawless" guest-side support is also a stretch — it depends on the guest having a reasonably current virtio driver, particularly on Windows.

### Step 3: Configure Advanced Performance Options

1. Expand the **Advanced options** dropdown menu.
2. Set **Cache mode** to `none`.
3. Set **IO mode** (or IO API) to `io_uring` — or `native`, see the callout below.
4. Set **Discard mode** to `unmap`.

> [!important] The I/O performance triad — two of three now come with fine print
> 
> - **`none` (Cache):** Bypasses the host's page cache via `O_DIRECT`. The VM talks directly to the storage controller, eliminating double-caching and the CPU cost of an extra copy. This hasn't changed and isn't likely to.
> - **`io_uring` (IO Mode):** Still the lowest-overhead async I/O API QEMU has on Linux, and a solid choice for NVMe. Two things worth knowing first: QEMU's `io_uring` backend had a genuine request-ordering bug (it surfaced during backups of BTRFS-backed disks) that was only fixed by a rework landing in **QEMU 10.2** (December 2025) — stay on 10.2 or later (current stable is 11.0). And on the kernel side, `io_uring` has a documented history of concentrated kernel-exploit activity — Google reported that around 60% of its 2022 kernel-exploit bounty submissions targeted it — which is why some hardened distributions disable it by default via the `kernel.io_uring_disabled` sysctl. Check that setting on locked-down hosts before assuming `io_uring` is even available. Linux 7.0 added BPF-based filtering for `io_uring` specifically so admins get a middle ground instead of all-or-nothing.
> - **`unmap` (Discard):** Passes TRIM from the guest straight through to the host's physical SSD. Accurate as described, and behaves identically on `virtio-blk` and `virtio-scsi` in current QEMU.
> 
> One more wrinkle: if you plan to use **IOThread Virtqueue Mapping** (Step 4) to spread I/O across multiple host threads, Red Hat's engineering documentation for that feature is written and benchmarked against `io='native'`, and explicitly advises against pairing it with `io='threads'`. `io_uring` isn't the documented pairing either. Practically: `io_uring` with a single IOThread is a great, simple, fast setup; `native` plus IOThread Virtqueue Mapping is the better-documented path once many vCPUs are hammering one disk.

### Step 4: Maximize NVMe Parallelism (Queues & IOThreads)

Modern NVMe hardware supports thousands of parallel queues. Two separate things decide whether a VM can actually use them: how many virtqueues the guest opens, and how many host threads are available to service those queues. "Set queues to match vCPUs" only addresses the first half.

1. **Queue count:** in current QEMU/libvirt, an unspecified queue count on a `virtio-blk` device already defaults to matching the vCPU count automatically — you generally don't need to set this by hand anymore.
2. **IOThreads:** by default, a single QEMU IOThread (or the main loop) still services every virtqueue on a disk, so a high queue count alone doesn't guarantee parallel host-side processing. Since QEMU 9.0 (`virtio-blk`) and QEMU 10.0 (`virtio-scsi`), **IOThread Virtqueue Mapping** lets you bind specific virtqueues to specific IOThreads so multiple host CPU cores genuinely share the work.

> [!tip] Configuring IOThread Virtqueue Mapping Define multiple IOThreads at the domain level, then list them under the disk so virtqueues are spread across them round-robin. A 4-vCPU guest using 2 IOThreads for a `virtio-blk` disk:
> 
> ```xml
> <domain>
>   ...
>   <vcpu>4</vcpu>
>   <iothreads>2</iothreads>
>   ...
>   <devices>
>     <disk type='file' device='disk'>
>       <driver name='qemu' type='raw' cache='none' io='native' discard='unmap'/>
>       <iothreads>
>         <iothread id='1'/>
>         <iothread id='2'/>
>       </iothreads>
>       ...
>     </disk>
>   </devices>
> </domain>
> ```
> 
> For `virtio-scsi`, the equivalent mapping goes on the SCSI controller element rather than per-disk, since one controller serves multiple LUNs — check `man virsh` or your installed libvirt's own documentation for the exact attribute syntax, since this is newer and worth confirming against your specific version rather than copying blind.
> 
> Red Hat's own benchmarking-based recommendations: use 4–8 IOThreads, let them share devices rather than dedicating one per disk unless you know a disk is especially hot, and pin IOThreads away from your vCPUs with `<iothreadpin>` if you have spare host cores. This needs the domain XML directly (`virsh edit <vm-name>`, or the XML tab in virt-manager) — it isn't a simple dropdown as of virt-manager 5.1.

> [!warning] Version floor `iothread-vq-mapping` needs QEMU 9.0+ (`virtio-blk`) or 10.0+ (`virtio-scsi`), plus libvirt 10+. Anything currently shipping in 2026 comfortably clears that bar. On an older stack, falling back to "set queues to match vCPUs and stop" is still correct — just less effective than it could be.

### Step 5: Consider Your Storage Format

If you are passing a disk image file rather than a physical PCIe device:

1. Ensure the **Storage format** is set to `raw`.

> [!warning] Raw vs QCOW2 While `qcow2` is incredibly convenient for snapshots and thin provisioning, it inherently carries a metadata overhead penalty. For absolute maximum performance, a `raw` image file (or directly passing a physical LVM/ZFS block device) is measurably faster because it requires zero block-translation overhead by the hypervisor. This hasn't changed.

> [!note] Going further than virtio Everything above still puts QEMU's virtio layer between guest and device. If you want the actual zero-overhead ceiling and can accept the trade-offs — no live migration, no snapshots, the device becomes unavailable to the host or any other VM — passing the physical NVMe controller through to the guest via VFIO (`vfio-pci`) skips virtio entirely. That's a more disruptive decision than anything else in this note, worth treating separately rather than as a setting to flip.

### Step 6: Save Changes

1. Click the **Apply** button at the bottom right of the window to commit these configurations.

**Next Step:** Proceed to the next hardware configuration note.
