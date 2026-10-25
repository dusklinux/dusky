---
title: "Windows VM completion checklist"
tags:
  - kvm
  - manual
  - windows
reviewed: 2026-10-25
---

# Windows VM completion checklist

[[+ MOC KVM|KVM home]] · [[+ MOC Windows Installation Through Virt Manager|Windows route]]

A complete install is one whose selected features have been verified, not one
with the largest list of disabled services. Use [[Verification — Windows VM]]
to check OS boot, devices, management, desktop/input, clipboard, sharing and
optional GPU/RDP/SSH paths.

Keep the Windows installation media/build information, offline driver inputs,
paired LG components and a [[08 Backup and Restore — Disks, Firmware and TPM|consistent backup]].
Record performance changes against a working baseline. The earlier recommendation
to prefer Defender-stripped/community “lite” images has been removed: removed
RDP, drivers or services can break this workflow, and no measured universal
performance gain was established. Choose the edition/components you need;
unsupported stripping is not a prerequisite for a working KVM desktop.
