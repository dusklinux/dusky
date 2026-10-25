---
title: "Windows 11 VM — complete manual route"
tags:
  - kvm
  - manual
  - windows
aliases:
  - Windows VM Roadmap
reviewed: 2026-10-25
---

# Windows 11 VM — complete manual route

[[+ MOC KVM|KVM home]]

Use this as the ordered Windows checklist. Shared host/hardware explanations
live in [[+ MOC KVM]] and [[00 Index — VM Creation (Unified)]]. You can complete
this route manually; the optional script equivalents are shown after the steps.

## 1. Prepare the shared host

Finish packages, KVM, modular libvirt, regular-user access, a mounted storage
pool and a named NAT/bridge network. GPU isolation is **optional**, not part of
the minimum Windows installation. Use [[GPU Passthrough — Host Isolation and Return]]
only if assigning a physical card. The complete ordered Windows extension is
[[+ MOC Windows GPU Passthrough]].

## 2. Create and inspect virtual hardware

Follow [[01 Wizard — Create VM]]. Select actual Windows 11 media/profile,
[[02 Chipset & Firmware — Q35 + UEFI|Q35/UEFI]], and
[[Enable Trusted Platform Module (TPM)|TPM 2.0]]. Secure Boot capability is not
proof of enrolled keys/enforcement. Inspect it separately if selected.
Choose [[05 CPU — Host-Passthrough & Topology|CPU topology]] and
[[07 Memory — Fixed and Ballooning|RAM/memory mode]] appropriate to the host.

Select either VirtIO disk/NIC with prepared drivers, or SATA/e1000e inbox devices.
Attach [[Mount the VirtIO-Win ISO Image|VirtIO-Win media]] when needed. Keep a
VGA/SPICE setup console, tablet/PS2 input and the QGA/SPICE channels. Add supported
[[Hyper-V Enlightenments]]; do not copy a giant cross-vendor XML feature list.
[[Windows 11 XML — Baseline]] gives a complete schema-checked example.

## 3. Install Windows and guest tools

Follow [[Install a Windows Virtual Machine on KVM]] for the correct storage
bus driver, network driver, first desktop sign-in and guest tools. Verify
[[Add QEMU Guest Agent Channel|QGA access]] before depending on automated
management. Use [[06 Guest Integration — Agent, Clipboard & Input]] to distinguish management,
desktop and input checks. Basic display/agent setup should work before optional GPU/display
changes. OS edition/build determines Windows Setup's account/network rules;
preparing offline driver media does not waive those requirements.

## 4. Pick desktop and management access

| Need | Guide |
|---|---|
| Setup/recovery display | SPICE console in virt-manager/virt-viewer |
| Looking Glass local desktop | [[Looking Glass]]; paired IDD/client, with or without physical GPU |
| Independent remote desktop | [[Remote Desktop — FreeRDP on Wayland]]; supported Windows edition/account |
| Optional shell/file access | [[OpenSSH Server — Windows]] |
| Shared host directory | [[09 Shared Folders — VirtIO-FS]], then [[Setting up Shared Directory Between Guest_win11 and Host]] |

Keep a setup console device even when IDD's monitor becomes the active desktop.
RDP is not a prerequisite for Looking Glass and manually disabling the emulated
adapter is not its normal setup step.

## 5. Verify, then tune

Use [[Verification — Windows VM]] for actual desktop/input/clipboard/file tests,
service checks and known limitations. Then consider [[Optimize Windows Performance]]
only for a measured need. Do not start by deleting Windows components or applying
blanket service/timer/task changes. Preserve a working baseline using
[[08 Backup and Restore — Disks, Firmware and TPM]]. Grow storage later with
[[Resize aka extend storage after os is already installed]].

## Optional scripted route

Host/profile commands are in [[+ MOC KVM]]. Deployment is
`python3 30_kvm_vm_deploy.py --windows`; it interviews you about access, GPU,
VirtIO/inbox devices, custom RAM and optional ballooning. Preview with
`--dump-only` before defining anything.

Inside Windows, the copied `windows/guest/00_bootstrap.ps1` without arguments asks
which guest components to configure, then requests elevation. Its README
explains offline Python, VirtIO, IDD and optional SSH inputs. To invoke it:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\00_bootstrap.ps1
```

It does not install a Linux host or make unmatched IDD/client versions compatible.
Restart only the guest when an installer requests it, then rerun unfinished
steps. A host reboot needs separate preservation of RAM-backed disks.
