---
title: "Windows performance — establish a baseline, then measure"
tags:
  - kvm
  - manual
  - windows
reviewed: 2026-10-25
---

# Windows performance — establish a baseline, then measure

[[+ MOC KVM|KVM home]] · [[+ MOC Windows Installation Through Virt Manager|Windows route]]

A usable Windows VM does not require blanket “debloat”. Begin with correct
storage/NIC drivers, sensible CPU/RAM, healthy GPU drivers if assigned, and a
working desktop transport. Record idle CPU/RAM, boot time and the workload that
actually matters before changing one setting at a time.

| Optional topic | Reason to consider it |
|---|---|
| [[Disable Unnecessary Startup Programs]] | Remove unwanted user-app startup work |
| [[Adjust the Visual Effects in Windows 11]] | Preference or a demonstrated software-rendering cost |
| [[Disable SysMain]] | Diagnose measured I/O/background behavior; not a universal VM default |
| [[Disable Unnecessary Scheduled Tasks]] | Inspect/reschedule a proven conflicting task |
| [[Disable useplatformclock]] | Undo an earlier forced debug setting, not a mandatory timer tweak |
| [[Disable Windows Web Search]] | Search preference with build/edition limitations |
| [[Optional Enable Hardware Security on Windows]] | Understand optional nested/VBS implications, not a default change |

Do not remove guest agents, the IDD helper or filesystem services while trimming
startup apps. `discard=unmap` does not replace guest retrim. A fixed CPU pin map,
large vCPU count, hugepages or a power profile is not automatically faster.
For RAM constraints use [[07 Memory — Fixed and Ballooning]] with actual host
measurements; ballooning is not an application-speed optimization.

The scripts do not automatically disable Defender, Windows Update, SysMain,
maintenance tasks or the pagefile. The old recommendations to prefer stripped
third-party ISOs or assume a stock image cannot perform were unmeasured and
could remove components the workflow needs. Keep a known working install and
compare changes against it. See [[Verification — Windows VM]].
