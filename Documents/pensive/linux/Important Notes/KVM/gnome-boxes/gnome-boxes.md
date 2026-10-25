---
title: "GNOME Boxes — Arch + Hyprland + SPICE Clipboard"
tags:
  - kvm
  - gnome-boxes
  - virt-manager
  - spice
  - wayland
  - arch
---

# GNOME Boxes — `virt-manager` SPICE Clipboard (Arch + Hyprland/UWSM)

> [!summary]
> - **Boxes scope:** `qemu:///session` (per-user, not `qemu:///system`)
> - **View/edit same VMs in virt-manager:** `virt-manager --connect qemu:///session` (`virsh --connect qemu:///session list --all`)
> - **Arch guest (Hyprland):** `spice-vdagent` + `xorg-xwayland` + user agent in session; optional `wl-clipboard`+`xclip` bridge is **guest-side** only
> - **Do not** enable `libvirtd.service` or mass-add groups for Boxes-default use
> - **Do not** symlink `~/.local/share/gnome-boxes/images` → `zram` unless you want **RAM-ephemeral disks**

*This file is intentionally **comprehensive** (670-line original retained in structure) — it is already Aug 2026-current for Boxes.*

> [!abstract] Canonical location
> Native **Arch** `gnome-boxes` package (not Flatpak). For host-level KVM/libvirt pass-through, see [[+ MOC KVM]] (`qemu:///system`).

## Scope

- **Host:** Arch, Wayland, Hyprland via **UWSM**, optional `virt-manager`
- **Guest example:** Arch + Hyprland
- **Theme:** accessing Boxes VMs from `virt-manager` + SPICE clipboard reliably

> [!note] Not Flatpak — sandbox/filesystem differ.

## Core architecture — `qemu:///session` vs `qemu:///system`

- Boxes → **`qemu:///session`** (user instance, not visible in default system connection)
- So normal `virt-manager` looks empty → use: `virt-manager --connect qemu:///session` (`-c qemu:///session`)

```bash
virsh --connect qemu:///session list --all
```

## Packages

### Host

```bash
sudo pacman -S --needed gnome-boxes virt-manager            # minimal
sudo pacman -S --needed edk2-ovmf swtpm                     # optional (UEFI/TPM)
# no extra host spice/wl-clipboard/xclip needed for clipboard
```

### Guest (Arch + Hyprland)

```bash
sudo pacman -S --needed spice-vdagent xorg-xwayland
sudo pacman -S --needed wl-clipboard xclip   # optional bridge (guest-side)
```

| Pkg | Where | Role |
|---|---|---|
| `gnome-boxes` | host | frontend |
| `virt-manager` | host | edit same `session` VMs |
| `spice-vdagent` | guest | clipboard/cursor/agent |
| `xorg-xwayland` | guest | clipboard path for Hyprland (`spice-vdagent` → Xwayland) |
| `wl-clipboard` / `xclip` | guest | optional Wayland↔X11 text bridge |

## Host setup

1. Launch `Boxes` once → creates `~/.local/share/gnome-boxes/`, `~/.config/libvirt/qemu/`.
2. Confirm scope: `virsh --connect qemu:///session list --all` shows VM.

> [!warning] `qemu:///system` ≠ `qemu:///session` — editing wrong scope edits wrong VMs.

## SPICE clipboard — required chain

1. VM uses **SPICE** (not VNC)  2. VM has **SPICE agent channel** (`com.redhat.spice.0`)  3. Guest `spice-vdagentd.service` running  4. Guest user `spice-vdagent` running  5. **Xwayland enabled** on Hyprland  6. SPICE client attached (Boxes/virt-manager viewer)

### Channel check

```bash
virsh --connect qemu:///session dumpxml "VM_NAME" | grep -A3 "com.redhat.spice.0"
# should show <channel type='spicevmc'>
# add via virt-manager: Add Hardware → Channel → SPICE agent
```

### Graphics backend

```bash
virsh --connect qemu:///session dumpxml "VM_NAME" | grep -A2 "<graphics type='spice'"
# if VNC, clipboard via spice cannot work
```

## Guest — Arch + Hyprland

### 1. Daemon

```bash
sudo systemctl enable --now spice-vdagentd.service
```

### 2. User agent (must be in user session, not root)

**systemd-user (UWSM-aware, preferred):**

```ini
# ~/.config/systemd/user/spice-vdagent-user.service
[Unit]
Description=SPICE user agent
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart=/usr/bin/spice-vdagent
Restart=on-failure
RestartSec=2

[Install]
WantedBy=graphical-session.target
```
```bash
systemctl --user daemon-reload; systemctl --user enable --now spice-vdagent-user.service
```

**Hyprland `exec-once` (simpler):**

```ini
exec-once = /usr/bin/spice-vdagent
# UWSM-tracked:
exec-once = uwsm app -- /usr/bin/spice-vdagent
```

> [!warning] Run `spice-vdagent` in guest, not host.

## Hyprland clipboard behavior (Xwayland bridge)

Hyprland `spice-vdagent` still uses X11/Xwayland path → needs `xorg-xwayland` + running `spice-vdagent`. If **host→guest works but guest Wayland app → host fails**, add guest-side bridge:

### systemd-user bridge

```ini
# ~/.config/systemd/user/spice-wayland-clipboard-bridge.service
[Unit]
Description=Bridge Wayland clipboard to X11 for SPICE
PartOf=graphical-session.target
After=graphical-session.target spice-vdagent-user.service

[Service]
ExecStart=/usr/bin/wl-paste --type text --watch /usr/bin/xclip -selection clipboard
Restart=on-failure
RestartSec=2

[Install]
WantedBy=graphical-session.target
```
```bash
systemctl --user daemon-reload; systemctl --user enable --now spice-wayland-clipboard-bridge.service
```

`exec-once = /usr/bin/wl-paste --type text --watch /usr/bin/xclip -selection clipboard` also works.

> Text-only bridge — not image/binary.

## Debug (guest-side)

```bash
wl-paste --type text | xclip -selection clipboard         # one-shot
wl-paste --type text --watch xclip -selection clipboard   # persistent (current shell)
pkill -x spice-vdagent; spice-vdagent &
pkill -x wl-paste; wl-paste --type text --watch xclip -selection clipboard &
sudo systemctl restart spice-vdagentd.service
```

### Verify

```bash
ls -l /dev/virtio-ports/com.redhat.spice.0
sudo modprobe virtio_console   # if missing
systemctl status spice-vdagentd.service
pgrep -af spice-vdagent
pgrep -af Xwayland
printf 'DISPLAY=%s WAYLAND=%s\n' "${DISPLAY-}" "${WAYLAND_DISPLAY-}"
```

## Boxes storage anti-pattern

> [!warning] Don't
> ```bash
> mkdir -p /mnt/zram1/boxes_vm/
> rm -rf ~/.local/share/gnome-boxes/images
> ln -nfs /mnt/zram1/boxes_vm ~/.local/share/gnome-boxes/images  # RAM-ephemeral VMs!
> ```

If you want temp RAM backing only for installer scratch:

```bash
mkdir -p "$XDG_RUNTIME_DIR/gnome-boxes-tmp"
TMPDIR="$XDG_RUNTIME_DIR/gnome-boxes-tmp" gnome-boxes
# or: mkdir -p /mnt/zram1/gnome-boxes-tmp; TMPDIR=/mnt/zram1/gnome-boxes-tmp gnome-boxes
```

## System libvirt — only if you intentionally want `qemu:///system`

Boxes-default → skip. System → `libvirt` group, socket activation (`10_virt_modular_daemon.py`), **separate** inventory from Boxes.

> Old `libvirtd.service` reflex (`libvirt,kvm` for Boxes user-session) is not recommended.

## Logs

```bash
virsh --connect qemu:///session list --all
journalctl --user -b --grep='gnome-boxes|virt-manager'
ls ~/.cache/libvirt/qemu/log/
journalctl -b -u spice-vdagentd.service
journalctl --user -b --grep='spice-vdagent|wl-paste|xclip'
```

Checklist: scope visible? `grep -A2 "<graphics type='spice'"`? `grep -A3 "com.redhat.spice.0"`? `systemctl status spice-vdagentd`? `pgrep spice-vdagent`? `pgrep Xwayland`? SPICE viewer attached?

See: [[SSHing into vm]] (NAT vs user-mode), [[linux]] (guest agents).
