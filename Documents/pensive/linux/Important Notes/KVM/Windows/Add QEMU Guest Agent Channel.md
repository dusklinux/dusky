---
title: "Windows QEMU guest agent — install and check"
tags:
  - kvm
  - manual
  - windows
aliases:
  - Windows Agent Stub
reviewed: 2026-10-25
---

# Windows QEMU guest agent — install and check

The shared channel XML and management/clipboard distinction are in
[[06 Guest Integration — Agent, Clipboard & Input]]. Add
`org.qemu.guest_agent.0`, then power the VM off/start it to apply the device.
Install QEMU Guest Agent and its required serial driver through VirtIO-Win tools.

Inside Windows, inspect the installed service in `services.msc`; configure/start
it if that component was selected. A channel with no driver/service cannot
answer management requests. From the Linux host:

```sh
virsh -c qemu:///system qemu-agent-command win11 '{"execute":"guest-ping"}'
virsh -c qemu:///system domifaddr win11 --source agent
```

A successful ping permits management without an RDP/SSH account password. It does
not sign the user into Windows, bypass the sign-in screen, prove desktop pixels,
or replace the SPICE/IDD user-session clipboard path. A Windows Hello PIN is
not an RDP/SSH password. Keep this boundary when diagnosing a black display.
