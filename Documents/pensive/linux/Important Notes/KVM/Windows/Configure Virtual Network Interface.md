---
title: "Windows NIC driver — VirtIO or inbox e1000e"
tags:
  - kvm
  - manual
  - windows
aliases:
  - Windows NIC Stub
reviewed: 2026-10-25
---

# Windows NIC driver — VirtIO or inbox e1000e

Select [[04 Network — Virtio NIC (NAT vs Bridge)|the shared network topology]]
independently of its device model. The Windows deployment asks for VirtIO
(prepared NetKVM driver) or e1000e (Windows inbox driver).

For VirtIO, install the matching NetKVM driver from the attached VirtIO-Win media,
during Setup when network is required or through guest tools/Device Manager
later. Confirm Device Manager reports a healthy adapter, then inspect `ipconfig`
or `Get-NetIPAddress`. See [[Install a Windows Virtual Machine on KVM]].
A changed NIC can get a different address/profile. Discover it rather than
assuming a cached IP is still correct. Guest service/firewall configuration is
separate from the NIC model: [[Remote Desktop — FreeRDP on Wayland]].
