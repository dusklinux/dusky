---
title: "KVM Services — Legacy Monolith (Deprecated)"
tags:
  - kvm
  - libvirt
  - systemd
  - legacy
---

# KVM Services — Legacy Monolith

> [!warning] Legacy — do **not** use on Aug 2026 Arch
> This note documents the *old* monolithic `libvirtd.service` path. Modern Arch (libvirt 12.6+, systemd 262+) uses **modular, socket-activated** daemons. Keep this note only as historical context; the replacement is [[libvert Modular daemon enable]] (`10_virt_modular_daemon.py`).

## What the monolith did (and why it was replaced)

| Service | Role | Modern replacement |
|---|---|---|
| `libvirtd` | Single daemon handling networks, storage, VMs | `virtqemud`, `virtnetworkd`, `virtstoraged`, `virtnodedevd`, `virtinterfaced`, … |
| `virtlogd` | Log handling isolated from `libvirtd` | Still present via `virtlogd.socket` but socket-activated, not always-on |

**Why the switch:** the monolithic daemon ran 24/7 and held memory even when idle. Modular sockets idle at **0 RSS** and wake per-driver on demand (see `10_virt_modular_daemon.py:enforce_socket_activation`). Arch's upstream now masks `libvirtd` and ships modular units.

## Legacy enable (retained for reference — **do not run**)

```bash
# HISTORICAL — masked by the pipeline
sudo systemctl enable --now libvirtd.service virtlogd.service
```

## What to run instead

```bash
# 1. Eradicate monolith (Phase 2)
sudo systemctl stop libvirtd.service libvirtd.socket libvirtd-ro.socket libvirtd-admin.socket libvirtd-tcp.socket libvirtd-tls.socket
sudo systemctl disable libvirtd.service libvirtd.socket libvirtd-ro.socket libvirtd-admin.socket libvirtd-tcp.socket libvirtd-tls.socket
sudo systemctl mask libvirtd.service libvirtd.socket libvirtd-ro.socket libvirtd-admin.socket libvirtd-tcp.socket libvirtd-tls.socket
sudo systemctl daemon-reload

# 2. Arm modular sockets (the only units enabled)
for drv in qemu interface network nodedev nwfilter secret storage proxy lxc ch vbox; do
  sudo systemctl enable --now virt${drv}d.socket virt${drv}d-ro.socket virt${drv}d-admin.socket
done
for drv in log lock; do
  sudo systemctl enable --now virt${drv}d.socket virt${drv}d-admin.socket
done
sudo systemctl enable --now libvirt-guests.service
```

Verify:

```bash
systemctl list-sockets | grep virt   # LISTEN
systemctl status virtqemud.service   # inactive (dead) = correct → 0 MB idle
virsh -c qemu:///system version      # waking test; then auto-idles
```

> [!info] Why `virtqemud.conf` permissions are inert
> Quoting `libvirtd.conf` verbatim: *“This setting is not required or honoured if using systemd socket activation.”* `unix_sock_group`/`unix_sock_rw_perms` are **ignored** when a `.socket` unit is listening. Socket owner/group/mode come from `[Socket] SocketGroup=`/`SocketMode=` in `/etc/systemd/system/virtqemud.socket.d/10-arsonix.conf` (`10_virt_modular_daemon.py:DROPIN_RW`).

See: [[libvert Modular daemon enable]], [[All Libvert Daemons]], [[check libvert modular daemon availability]].
