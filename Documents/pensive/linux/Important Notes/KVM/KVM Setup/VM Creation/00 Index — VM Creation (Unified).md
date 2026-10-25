---
title: "VM virtual hardware — shared index"
tags:
  - kvm
  - manual
aliases:
  - VM Creation Canonical
  - Unified VM Creation
reviewed: 2026-10-25
---

# VM virtual hardware — shared index

Prepare [[+ MOC KVM|the host]] first. Follow these shared explanations, then the
Windows or Linux guest route. Shared does not mean all guests use identical
settings: devices must have drivers and the workload determines resource sizes.

| Order | Shared guide | Windows 11 | Linux distinction |
|---|---|---|---|
| 1 | [[01 Wizard — Create VM]] | Select Windows 11 profile and media | Select the actual distribution |
| 2 | [[02 Chipset & Firmware — Q35 + UEFI]] | UEFI, TPM 2.0; verify Secure Boot state separately | Distribution firmware requirements vary |
| 3 | [[03 Storage — Virtio Bus, Cache, io_uring, Discard]] | VirtIO needs viostor/vioscsi; SATA is an explicit option | Typical kernels include VirtIO drivers |
| 4 | [[04 Network — Virtio NIC (NAT vs Bridge)]] | VirtIO needs NetKVM; e1000e is an explicit option | Typical kernels include virtio_net |
| 5 | [[05 CPU — Host-Passthrough & Topology]] | Add supported [[Hyper-V Enlightenments]] | Do not copy the Windows feature block blindly |
| 6 | [[07 Memory — Fixed and Ballooning]] | Default 8 GiB; optional driver-backed balloon | Guest-driver support differs |
| 7 | [[06 Guest Integration — Agent, Clipboard & Input]] | VirtIO-Win tools, QGA, SPICE or IDD | Native Wayland integration is a distinct path |
| 8 | [[09 Shared Folders — VirtIO-FS]] | WinFsp + viofs service | Kernel mount/systemd automount |
| 9 | [[08 Backup and Restore — Disks, Firmware and TPM]] | Preserve disk, NVRAM, TPM together | Preserve the corresponding guest state |

Continue Windows installation at [[+ MOC Windows Installation Through Virt Manager]].
Linux-specific desktop work is deferred to its own pass; current source entry
points are `linux/deploy_vm.py` and `linux/guest_integration.py`, not the old
Boxes/XWayland clipboard recipes. macOS uses a separate workflow.

Script paths: `30_kvm_vm_deploy.py --windows` dispatches to
`windows/deploy_vm.py`; without that option it dispatches to Linux. Function
names such as `build_command` now live in the OS-specific implementation.
Use [[Windows 11 XML — Baseline]] for a complete validated Windows example;
individual guides explain each section without duplicating whole domains.
