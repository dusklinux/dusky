---
title: "GNOME Boxes — Windows Guest Tools (SPICE)"
tags:
  - kvm
  - gnome-boxes
  - windows
  - spice
---

# GNOME Boxes — Windows Guest (SPICE Tools)

> [!info] Scope
> Boxes `qemu:///session` Windows VM — SPICE for display + clipboard (no VFIO here). For `qemu:///system` Windows + passthrough see [[+ MOC Windows GPU Passthrough]].

## spice-guest-tools (display, agent, qxl)

Install **spice-guest-tools** inside **Windows guest** (not host):

- Download: <https://www.spice-space.org/download.html> → **Windows binaries → Windows SPICE Guest Tools** (`spice-guest-tools-latest.exe`) — includes `qxl` video, `spice-agent` (copy/paste, auto-resize), optional `virtio` serial drivers.
- Install → reboot.

Verify in guest: `services.msc` → **RDP/SPICE agents** running; `Device Manager → Display adapters` → **Red Hat QXL**.

> [!tip] CLI x64 path issue?
> Boxes default `qxl` driver may show `Standard VGA` pre-tools → after install it becomes `Red Hat QXL`.

## WebDAV (guest↔host file sharing, separate from clipboard)

Clipboard (`spice-vdagent` / `spice-guest-tools`) ≠ Shared Folders.

For **folder sharing** via SPICE WebDAV need **Spice WebDAV daemon** inside Windows guest:

- <https://www.spice-space.org/download/windows/spice-webdavd/> → install → plus **SPICE WebDAV channel** in VM XML (Boxes usually omits; add via `virt-manager --connect qemu:///session` → Add Hardware → Channel → `org.spice-space.webdav.0` per SPICE manual: <https://www.spice-space.org/spice-user-manual.html#_folder_sharing>):

```xml
<channel type='spiceport'>
  <source channel='org.spice-space.webdav.0'/>
  <target type='virtio' name='org.spice-space.webdav.0'/>
</channel>
```

> [!note] virtiofs vs WebDAV
> `virtiofs` (`host_zram` shared folder for `qemu:///system` VMs) is different stack (see [[Setting up Shared Directory Between Guest_win11 and Host]]). Boxes `session` VMs typically use **WebDAV**, not `virtiofs`, for folder sharing.

See: [[gnome-boxes]] (scope + clipboard plumbing), [[SSHing into vm]].
