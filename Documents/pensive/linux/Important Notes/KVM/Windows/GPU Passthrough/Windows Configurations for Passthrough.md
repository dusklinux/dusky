---
title: "Windows integration — choose the components you need"
tags:
  - kvm
  - manual
  - windows
reviewed: 2026-10-25
---

# Windows integration — choose the components you need

After installing Windows/guest tools, use a separate guide for each component:

| Component | Guide |
|---|---|
| Physical GPU vendor driver | [[GPU Passthrough — Host Isolation and Return]] and [[Verification — Windows VM]] |
| Matching IDD and Linux client | [[Looking Glass]] |
| Remote desktop | [[Remote Desktop — FreeRDP on Wayland]] |
| Optional shell/file copying | [[OpenSSH Server — Windows]] |
| Shared directory | [[Setting up Shared Directory Between Guest_win11 and Host]] |
| Memory mode | [[07 Memory — Fixed and Ballooning]] |

`windows/guest/00_bootstrap.ps1` asks about LG, RDP, SSH and guest tools when run
without arguments. It respects explicit switches for automation. Its README
lists offline runtime/installer inputs; a Python script is not a substitute
for the actual Windows driver installer. The standalone folder setup is separate.

No third-party display driver, devcon install, legacy Host service, fixed NVIDIA
version, debloat utility or forced display-adapter disable is mandatory for the
paired IDD route. Preserve account credentials and the usable setup console.
