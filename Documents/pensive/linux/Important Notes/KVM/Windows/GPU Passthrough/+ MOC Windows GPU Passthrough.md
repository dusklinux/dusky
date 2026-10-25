---
title: "Windows GPU passthrough — ordered extension"
tags:
  - kvm
  - manual
  - windows
reviewed: 2026-10-25
---

# Windows GPU passthrough — ordered extension

[[+ MOC KVM|KVM home]] · [[+ MOC Windows Installation Through Virt Manager|Windows route]]

First establish [[+ MOC Windows Installation Through Virt Manager|a working Windows VM]]. GPU passthrough is an extension, not an alternative host setup.

1. [[GPU Passthrough — Host Isolation and Return|Audit and isolate the GPU]]:
   host display plan, all slot functions, IOMMU groups and ID collisions.
2. Preserve RAM-backed disks/state before the host reboot. Rebuild/verify the
   actual boot images and entry, then reboot at a planned boundary.
3. Verify selected functions use vfio-pci and the host display still works.
4. With the VM off, attach its PCI functions while retaining setup VGA/SPICE.
5. Install the current vendor driver for the exact GPU/Windows release and
   inspect PnP health. A Basic Display adapter alone is not acceleration.
6. Configure [[Looking Glass]] with a paired IDD/client and sufficient shared
   memory. Test after a normal desktop sign-in.
7. [[Verification — Windows VM|Check rendering, repeated clean VM starts and logs]].
8. For return to Linux, remove hostdev assignments as needed, stage native driver
   return, verify boot images/options and reboot. Inspect video **and audio**.

Fixed RAM is the recommended default for VFIO. Ballooning is an explicit choice,
but host-memory reclamation was tested without PCI passthrough only.
The single observed DMA shutdown fault remains an open stack finding, not a
reason to add guessed IOMMU/reset workarounds or claim the stack fault-free.
