---
title: "Install Windows 11 — disk, NIC and guest drivers"
tags:
  - kvm
  - manual
  - windows
reviewed: 2026-10-25
---

# Install Windows 11 — disk, NIC and guest drivers

[[+ MOC KVM|KVM home]] · [[+ MOC Windows Installation Through Virt Manager|Windows route]]

Complete [[+ MOC Windows Installation Through Virt Manager|virtual hardware]]
first. Boot the Windows ISO in the retained VGA/SPICE console. The Windows
installer should see the chosen firmware/TPM and meet its resource requirements.
A VirtIO boot disk can be invisible until its matching driver is loaded; a SATA
inbox-driver disk does not require that step.

## Storage driver during Setup

At the installation disk picker, choose **Load driver**, browse the attached
VirtIO-Win DVD, and select the correct Windows version/architecture directory:

| Chosen virtual controller | Driver directory |
|---|---|
| VirtIO block disk (`bus='virtio'`) | `viostor` |
| VirtIO SCSI controller with SCSI disk | `vioscsi` |
| Emulated SATA | Windows inbox AHCI driver |

For an x64 Windows 11 guest, media commonly uses `w11/amd64` below the driver
name. Inspect the media; do not select vioscsi just because its display label
contains “SCSI” while your device is VirtIO block. After loading the matching
driver, confirm the intended disk and capacity before installing.

## Networking during Setup/OOBE

VirtIO networking needs NetKVM. If Setup offers driver browsing, use the media's
matching `NetKVM` directory. A setup console can also stage the matching INF with
`pnputil /add-driver PATH_TO_INF /install`; inspect `pnputil /?` and replace the
path with the actual DVD drive/architecture. Keep the INF, catalog and driver
files together. An e1000e inbox NIC is another explicit installation choice.
Do not assume current Windows OOBE can always complete without network/account
access; edition/build requirements vary.

## First desktop: guest tools

Open the VirtIO DVD in File Explorer, run `virtio-win-guest-tools.exe` as
Administrator, select the required components and follow its restart request.
It can install/update drivers and guest agents; confirm the selected components
rather than assuming every optional driver was installed. Do not use repeated
blind driver uninstall/reinstall cycles for a missing cursor.

Check Device Manager for the actual disk/NIC devices and QEMU Guest Agent/SPICE
services. From Linux:

```sh
virsh -c qemu:///system qemu-agent-command win11 '{"execute":"guest-ping"}'
virsh -c qemu:///system domifaddr win11 --source agent
```

If ballooning was selected, verify its driver and BalloonService. For folder
sharing install WinFsp/viofs separately as needed. Looking Glass IDD has its own
paired installer; do not assume a VirtIO video driver alone supplies it.

Eject the Windows installer when no longer needed and boot the installed disk.
Preserve the driver media for future offline use. A historical tools screenshot:
![[Pasted image 20250726223648.png]]

First verify the baseline OS/devices/QGA with [[Verification — Windows VM]].
Then return to [[+ MOC Windows Installation Through Virt Manager#4. Pick desktop and management access|desktop and management choices]],
and repeat the relevant checks after adding each selected feature.
Sources: [VirtIO-Win driver installation](https://github.com/virtio-win/kvm-guest-drivers-windows/wiki/Driver-installation),
[Microsoft PnPUtil](https://learn.microsoft.com/en-us/windows-hardware/drivers/devtest/pnputil-command-syntax).
