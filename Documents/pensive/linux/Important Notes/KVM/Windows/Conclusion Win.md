---
title: "Conclusion — Debloated Windows Image"
tags:
  - kvm
  - windows
  - iso
---

# Conclusion — Debloated Windows Image

> [!tip] Use a custom, Defender-stripped ISO
> Stock Win11 pulls in Defender/antimalware, telemetry, preinstalled apps and frequent background scans — measurable cost inside a VM. For labs/gaming passthrough, prefer a **de-bloated LTSC/IoT** or community “lite” image with:
> - **Windows Defender / Antimalware removed** (or service set to `Disabled` — verify `Get-MpComputerStatus` is inert)
> - Startup apps / Store / telemetry trimmed
> - Updates routed via **Windows Update MiniTool** / `O&O ShutUp10++` (not auto).
>
> Trade: you own update/antivirus responsibility. Keep virtio drivers (`NetKVM`, `viostor`, `vioinput`), `QEMU Guest Agent`, `spice-agent`, `WinFsp` intact — those are required for passthrough/shared-folder/clipboard to work.

## Post-install hardening (quick)

```powershell
# keep only needed services
Get-Service SysMain,DiagTrack | Set-Service -StartupType Disabled 2>$null
# Tune Visual Effects: "Adjust for best performance" (see [[Adjust the Visual Effects in Windows 11]])
# Network private: Set-NetConnectionProfile -NetworkCategory Private (if using virtio NAT)
```

Further perfs: web-search “optimize Windows 11 VM” — most win comes from the de-bloat + correct [[Configure the Storage]] (`cache=none,io=io_uring|native,discard=unmap`) and correct [[Enable Hyper-V Enlightenments]]/pinning.

See: [[+ MOC Windows Installation Through Virt Manager]] (full roadmap), [[Windows Configurations for Passthrough]] (Looking Glass IDD / OpenSSH).
