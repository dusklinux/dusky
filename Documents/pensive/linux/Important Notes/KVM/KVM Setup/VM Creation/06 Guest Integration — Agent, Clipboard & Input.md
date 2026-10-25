---
title: "Guest integration — management, desktop and input paths"
tags:
  - kvm
  - manual
aliases:
  - Guest Integration Unified
reviewed: 2026-10-25
---

# Guest integration — management, desktop and input paths

Install a guest component for the job it actually performs. Adding a channel
alone does not install its driver or userspace service.

| Path | Role | What it does not prove |
|---|---|---|
| QEMU guest agent (QGA) | Shutdown, network/filesystem information and supported management commands | Visible desktop, clipboard or login |
| SPICE agent | Clipboard and desktop integration with an attached SPICE client | GPU acceleration or a shared folder |
| Looking Glass IDD | Windows display/direct input and supported clipboard transport | RDP account access or file sharing |
| VirtIO-FS | Shared host files | Display/clipboard transport |
| SSH | Optional command/file access | Visible interactive desktop |

## Shared XML channels

Inside `<devices>`:

```xml
<channel type='unix'>
  <target type='virtio' name='org.qemu.guest_agent.0'/>
</channel>
<channel type='spicevmc'>
  <target type='virtio' name='com.redhat.spice.0'/>
</channel>
```

Keep a SPICE graphics device when using spicevmc channels. Let libvirt allocate
its socket paths and virtio-serial addresses. Power the guest off/start it to
apply new hardware. After installing the agent:

```sh
virsh -c qemu:///system qemu-agent-command win11 '{"execute":"guest-ping"}'
virsh -c qemu:///system domifaddr win11 --source agent
virsh -c qemu:///system shutdown win11 --mode agent
```

A successful ping is `{"return":{}}`. Shutdown is a separate command that
changes guest state; do not run it as an incidental connectivity probe. Without
QGA, an ACPI shutdown request is another graceful route if the guest handles it.
Forced destroy is not a normal restart method.

## Windows versus Linux

Windows installs QGA/SPICE and selected VirtIO drivers from the VirtIO-Win tools:
[[Install a Windows Virtual Machine on KVM]], [[Add QEMU Guest Agent Channel]].
Its interactive IDD helper starts after sign-in: [[Looking Glass]].

Linux QGA is separate from its graphical-session clipboard agent. The current
Linux scripts provide an SSH/Wayland text route and a separate native Wayland
SPICE-agent build route. Their dependencies, MIME support and build inputs are
not identical. Do not install Xorg/XWayland packages or xclip bridges for this
Hyprland-only baseline. Linux guest details will be consolidated in the next
pass; source reference is `docs/linux/NATIVE_GUEST_INTEGRATION.md`.

## Input and recovery

Keep the setup tablet/PS2 input until the desktop works. Absolute tablet input
can improve console usability; removing it is optional and has no measured
universal latency benefit here. Current IDD direct input does not need an extra
VirtIO mouse package. Looking Glass's escape key is configured as `KEY_F6`, not
a guessed numeric evdev/X11 keycode. Keep a working setup/recovery input path.

An RDP session can move/lock the console session; it is not a neutral test of
the same Looking Glass desktop. Test visible output after a normal console
sign-in. See [[Verification — Windows VM]].
