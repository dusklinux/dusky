---
title: "GNOME Boxes Windows — separate session workflow"
tags:
  - kvm
  - manual
reviewed: 2026-10-25
---

# GNOME Boxes Windows — separate session workflow

[[+ MOC KVM|KVM home]] · [[Reference Index — Older Names and Separate Workflows|Separate workflows]]

Native GNOME Boxes commonly uses the per-user `qemu:///session` inventory. Check
it explicitly before editing a VM in virt-manager. It is a separate alternative,
not the system-libvirt Windows/VFIO workflow documented by this pass.

```sh
virsh -c qemu:///session list --all
virt-manager --connect qemu:///session
```

Windows SPICE tools/agent can provide supported clipboard/display integration
when the corresponding channel and viewer exist. VirtIO-Win tools and driver
requirements still depend on the devices actually configured; see
[[Install a Windows Virtual Machine on KVM]]. Do not infer healthy display
acceleration from a QXL driver name.

Boxes folder sharing through SPICE WebDAV is a different mechanism from
VirtIO-FS. Its guest daemon/channel/client support must all match. The tested
folder route in this session is system-libvirt VirtIO-FS:
[[Setting up Shared Directory Between Guest_win11 and Host]]. A channel alone
does not install a WebDAV service or share a directory.

For a complete current Windows manual route use
[[+ MOC Windows Installation Through Virt Manager]]. Broader Boxes/Linux guest
clipboard notes await the Linux pass and are not a Wayland/XWayland baseline
recommendation here. Source: [SPICE manual](https://www.spice-space.org/spice-user-manual.html).
