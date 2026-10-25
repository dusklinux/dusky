---
title: "Windows disk controller and driver choice"
tags:
  - kvm
  - manual
  - windows
aliases:
  - Windows Storage Stub
reviewed: 2026-10-25
---

# Windows disk controller and driver choice

[[+ MOC KVM|KVM home]] · [[+ MOC Windows Installation Through Virt Manager|Windows route]]

Configure image format, cache/I/O and discard using
[[03 Storage — Virtio Bus, Cache, io_uring, Discard]]. The Windows-specific part is
matching the controller to its driver:

| Disk hardware | Driver to stage/load |
|---|---|
| VirtIO block disk | viostor |
| VirtIO SCSI controller / SCSI disk | vioscsi |
| SATA AHCI | Inbox Windows AHCI |

Load the matching media version/architecture at the Setup disk picker:
[[Install a Windows Virtual Machine on KVM]]. An installed guest needs the new
controller's boot driver ready before switching its OS disk bus. Device labels
can contain “SCSI”; do not use that label alone to choose an unrelated driver.
Growth is covered by [[Resize aka extend storage after os is already installed]].
