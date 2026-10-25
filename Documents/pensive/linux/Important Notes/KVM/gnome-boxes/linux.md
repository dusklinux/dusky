---
title: "GNOME Boxes — Arch Guest Agents (QXL/SPICE) + Linux VM Route"
tags:
  - kvm
  - gnome-boxes
  - arch
  - spice
aliases:
  - Linux Guest Agents
---

# GNOME Boxes — Arch Guest Agents (QXL/SPICE)

> [!note] Linux guest review pending
> This page retains older Linux/Boxes or example-XML instructions. It can
> conflict with the current Hyprland/Wayland-only baseline, including older
> clipboard/display recipes. Use [[+ MOC KVM]] for the reviewed
> shared host setup; this page is not yet the current Linux guest guide.

> [!tip] Merged — canonical Q35/virtio/CPU lives elsewhere
> **Creating an Arch/Fedora/Ubuntu VM via `virt-manager` (`qemu:///system`, NAT `192.168.122.x`, `host-passthrough`)? Follow [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]] — shared wizard ([[KVM Setup/VM Creation/01 Wizard — Create VM|01 Wizard]]), chipset/Firmware ([[KVM Setup/VM Creation/02 Chipset & Firmware — Q35 + UEFI|02 Chipset]]), storage ([[KVM Setup/VM Creation/03 Storage — Virtio Bus, Cache, io_uring, Discard|03 Storage]]), network ([[KVM Setup/VM Creation/04 Network — Virtio NIC (NAT vs Bridge)|04 Network]]), CPU ([[KVM Setup/VM Creation/05 CPU — Host-Passthrough & Topology|05 CPU]]), guest integration ([[KVM Setup/VM Creation/06 Guest Integration — Agent, Clipboard & Input|06 Integration]]).** This note now keeps **only Boxes `qemu:///session` SPICE plumbing** (no duplicate Q35/virtio explanation).

> [!info] Scope
> Enable inside the **guest** Arch VM that GNOME Boxes launched (which lives in `qemu:///session` by default; see [[gnome-boxes]] for host/guest split). For `qemu:///system` Linux VMs (the canonical path above), the same guest `spice-vdagent` + `qemu-guest-agent` applies — just without the Boxes `session` wrapper.

## Arch guest agents (both `qemu:///session` Boxes and `qemu:///system` canonical)

Inside guest (`pacman` inside VM):

```bash
sudo pacman -S --needed spice-vdagent qemu-guest-agent xf86-video-qxl
sudo systemctl enable --now spice-vdagentd.service
# Hyprland guest: spice-vdagent via exec-once or systemd-user (see [[gnome-boxes]]: UWSM variant)
sudo systemctl enable --now qemu-guest-agent.service 2>/dev/null || sudo systemctl enable --now qemu-guest-agent 2>/dev/null
```

Reboot guest. Host viewer: **View → Scale Display → Auto resize** now works.

> [!tip] Secure vs legacy display
> `qxl` = legacy SPICE. For 3D on Intel iGPU, prefer `video:virtio` + `accel3d=yes` + `spice gl` ([[arch linux GPU ACCELERATION intel integrated xml]]). For headless VFIO, see [[Looking Glass]] (`video none` + `<shmem>`). Virt-manager Linux VMs use `video:virtio` + SPICE `gl.enable=yes` (virgl) by default when not headless.

> [!info] Linux route vs Windows route
> Linux needs **no** `virtio-win.iso` / `viostor` / `NetKVM` / TPM / Hyper-V. Virtio drivers are **in-kernel** (`virtio_blk`, `virtio_net`, `virtio_pci`). Follow canonical [[KVM Setup/VM Creation/03 Storage — Virtio Bus, Cache, io_uring, Discard|03 Storage]] and [[KVM Setup/VM Creation/04 Network — Virtio NIC (NAT vs Bridge)|04 Network]] `> [!info] Linux route` boxes — no driver load step.

See: [[gnome-boxes]] (scope, systemd-user `spice-vdagent-user.service`, Wayland bridge), [[KVM Setup/VM Creation/06 Guest Integration — Agent, Clipboard & Input]] (canonical both OSes), [[SSHing into vm]] (NAT vs `hostfwd`).
