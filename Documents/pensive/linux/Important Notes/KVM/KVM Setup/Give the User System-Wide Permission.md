---
title: "System libvirt — user access and connection URI"
tags:
  - kvm
  - manual
reviewed: 2026-10-25
---

# System libvirt — user access and connection URI

[[+ MOC KVM|KVM home]]

Use `qemu:///system` for this workflow. The system daemon is privileged, but QEMU
processes can run under a separate service account. `qemu:///session` is a
separate per-user inventory with different networking/device arrangements.
Neither an empty GUI nor a session URI means your system VM was deleted.

## User membership

Run as the desktop account:

```sh
sudo usermod -aG libvirt "$(id -un)"
# Looking Glass's chosen group for its shared-memory file:
sudo usermod -aG kvm "$(id -un)"
```

Refresh the desktop login after adding groups; existing processes retain old
credentials. Compare `id` with `id "$(id -un)"`. The `input` group is relevant only
to a deliberate evdev/input-device workflow, not ordinary SPICE or IDD input.
No `disk` membership is needed for a file-backed VM. The current phase 05 also
adds `input`; do not interpret that script policy as a universal prerequisite.

## Two permission checks

1. The connection must reach its socket: [[libvert Modular daemon enable]].
2. The requested API action must be authorized by polkit.

A Hyprland session can use its configured polkit agent for interactive prompts.
For a deliberate full-management group policy, the script installs a rule
covering `org.libvirt.unix.*` for `libvirt` and `wheel` members. A manual
libvirt-group-only equivalent follows.

Save the following using `sudoedit /etc/polkit-1/rules.d/50-local-libvirt.rules`:

```javascript
// /etc/polkit-1/rules.d/50-local-libvirt.rules
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.libvirt.unix.") === 0 && subject.isInGroup("libvirt")) {
        return polkit.Result.YES;
    }
});
```

This authorizes full system VM management for that group; it is not a QEMU file
ownership setting. Keep this distinction when troubleshooting storage access.

## Explicit connection first

```sh
virsh -c qemu:///system list --all
virt-manager --connect qemu:///system
```

Optionally set `LIBVIRT_DEFAULT_URI=qemu:///system` in the environment actually
used by your desktop launcher. A systemd user-environment drop-in can supply:

```ini
# ~/.config/environment.d/60-libvirt.conf
LIBVIRT_DEFAULT_URI=qemu:///system
```

It affects processes inheriting that user-manager environment; it is not a
promise that every shell/compositor launch path imports it. Explicit `-c` or
`--connect` remains unambiguous. Check `virsh uri` and
`systemctl --user show-environment` after a new login.

Troubleshoot in order: correct URI → listening socket → current group membership
→ polkit agent/rule → individual error. Do not launch the desktop GUI with sudo
as a general repair. Sources: [libvirt authentication](https://libvirt.org/auth.html),
[systemd environment.d](https://www.freedesktop.org/software/systemd/man/latest/environment.d.html).

---

Previous: [[libvert Modular daemon enable]]. Continue: [[Symbolic link to zram for image file]].
