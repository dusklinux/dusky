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

[[+ MOC KVM|KVM home]]

Prepare [[+ MOC KVM|the host]] first. Follow these shared explanations, then the
Windows or Linux guest route. Shared does not mean all guests use identical
settings: devices must have drivers and the workload determines resource sizes.

Follow the reading order below. Existing filenames retain their earlier numbers
for stable links; those numbers are not script phases or a second installation order.

| Order | Shared guide | Windows 11 | Linux distinction |
|---|---|---|---|
| 1 | [[01 Wizard — Create VM]] | Select Windows 11 profile and media | Select the actual distribution |
| 2 | [[02 Chipset & Firmware — Q35 + UEFI]] | UEFI, TPM 2.0; verify Secure Boot state separately | Distribution firmware requirements vary |
| 3 | [[03 Storage — Virtio Bus, Cache, io_uring, Discard]] | VirtIO needs viostor/vioscsi; SATA is an explicit option | Typical kernels include VirtIO drivers |
| 4 | [[04 Network — Virtio NIC (NAT vs Bridge)]] | VirtIO needs NetKVM; e1000e is an explicit option | Typical kernels include virtio_net |
| 5 | [[05 CPU — Host-Passthrough & Topology]] | Add supported [[Hyper-V Enlightenments]] | Do not copy the Windows feature block blindly |
| 6 | [[07 Memory — Fixed and Ballooning]] | Default 8 GiB; optional driver-backed balloon | Guest-driver support differs |
| 7, after OS install | [[06 Guest Integration — Agent, Clipboard & Input]] | VirtIO-Win tools, QGA, SPICE or IDD | Native Wayland integration is a distinct path |
| 8, optional | [[09 Shared Folders — VirtIO-FS]] | WinFsp + viofs service | Kernel mount/systemd automount |
| 9 | [[08 Backup and Restore — Disks, Firmware and TPM]] | Preserve disk, NVRAM, TPM together | Preserve the corresponding guest state |

**Installation boundary:** review rows 1–6 before starting the installer, and
add the required agent channels during that hardware review. Then follow
[[Install a Windows Virtual Machine on KVM|Windows installation]] to install the
OS and drivers. Return to row 7 to verify guest integration, select row 8 if
wanted, and retain a consistent backup once the selected features work.
[[+ MOC Windows Installation Through Virt Manager]] ties these stages together.
Linux-specific desktop work is deferred to its own pass; current source entry
points are `linux/deploy_vm.py` and `linux/guest_integration.py`, not the old
Boxes/XWayland clipboard recipes. macOS uses a separate workflow.

Script paths: `30_kvm_vm_deploy.py --windows` dispatches to
`windows/deploy_vm.py`; without that option it dispatches to Linux. Function
names such as `build_command` now live in the OS-specific implementation.
Use [[Windows 11 XML — Baseline]] for a complete validated Windows example;
individual guides explain each section without duplicating whole domains.
