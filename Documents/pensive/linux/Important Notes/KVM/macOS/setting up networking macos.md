---
title: "macOS — QEMU Networking (User, Tap, Bridge)"
tags:
  - kvm
  - macos
  - qemu
  - networking
  - arch
  - legacy
---

# macOS — QEMU Networking

> [!note] Separate workflow — not reviewed in the Windows pass
> This page retains older macOS-specific instructions. Its packages, firmware,
> device examples and commands have not been validated by the Windows/shared
> review. Start at [[+ MOC KVM]] for the reviewed scope.

> [!info] Context (Aug 2026)
> `OSX-KVM` boot scripts directly invoke `qemu-system-x86_64` (not libvirt). This note maps QEMU network backends; libvirt-managed `virbr0` (`default` NAT) and `br0` paths from [[Activating Network and Setting it to Autostart]] / [[Network Bridging for LAN access]] apply if you import the macOS VM into libvirt via `macOS-libvirt-*.xml`.

## User Mode (`slirp`) — easiest, no host prep

QEMU default (`-netdev user`). Outbound-only (`10.0.2.15`), no inbound.

```bash
# in boot-macOS.sh
-netdev user,id=net0 -device e1000-82545em,netdev=net0,id=net0,mac=52:54:00:c9:18:27
# or virtio: -device virtio-net-pci / vmxnet3 (macOS 10.11+)
```

Adapters macOS accepts:

- `e1000-82545em` — widest compatibility (any macOS)
- `vmxnet3`, `virtio-net-pci` — paravirt, faster, needs 10.11+

Docs: <http://wiki.qemu.org/Documentation/Networking>

### SSH via user mode (port forward)

```bash
# macOS guest: System Preferences → Sharing → Remote Login (SSH) → On
# boot script:
-netdev user,id=net0,hostfwd=tcp::10022-:22 -device e1000-82545em,netdev=net0,id=net0,mac=52:54:00:c9:18:27
# host:
ssh -p 10022 user@localhost   # same for VNC forwards
```

```bash
printf '52:54:00:AB:%02X:%02X\n' $((RANDOM%256)) $((RANDOM%256))   # QEMU mac
```

## Tap via libvirt `virbr0` — libvirt NAT, host↔guest

If `virt-manager` created `virbr0` (`default` NAT, `dnsmasq`+`nftables`):

```bash
sudo ip tuntap add dev tap0 mode tap
sudo ip link set tap0 up promisc on
sudo ip link set dev tap0 master virbr0
sudo ip link set dev virbr0 up
# boot script:
-netdev tap,id=net0,ifname=tap0,script=no,downscript=no -device e1000-82545em,netdev=net0,id=net0,mac=52:54:00:c9:18:27
```

If `virbr0` missing:

```bash
sudo virsh -c qemu:///system net-start default
sudo virsh -c qemu:///system net-autostart default
```

> [!warning] Legacy — Ubuntu `apt` / `uml-utilities`
> Original did `sudo apt-get install uml-utilities virt-manager` — on Arch: `sudo pacman -S --needed qemu-desktop libvirt virt-manager dnsmasq nftables`.

### `rc.local` helper

```bash
# /etc/rc.local (enable via systemd unit if on Arch — see [[all notes macos]] rc.local section)
#!/usr/bin/env bash
sudo ip tuntap add dev tap0 mode tap
sudo ip link set tap0 up promisc on
sudo ip link set dev virbr0 up
sudo ip link set dev tap0 master virbr0
```

## Bridge helper (`qemu-bridge-helper`) — LAN-visible bridge

For guests that need real LAN IP (e.g. iservices troubleshooting <https://dortania.github.io/OpenCore-Post-Install/universal/iservices.html>):

```bash
-netdev bridge,id=net0,br=virbr0,"helper=/usr/lib/qemu/qemu-bridge-helper"
# or br0 after 2023 bridge path:
-netdev bridge,id=net0,br=br0,"helper=/usr/lib/qemu/qemu-bridge-helper" -device virtio-net-pci,netdev=net0,id=net0,mac=00:16:CB:00:11:34
# fix helper perms if needed:
sudo chmod u+s /usr/lib/qemu/qemu-bridge-helper  # setuid — understand risk
```

## Bridged (2023 `br0` — Arch `nmcli` variant)

Modern `br0` via NetworkManager (see [[Network Bridging for LAN access]] Option 3):

```bash
sudo mkdir -p /etc/qemu && sudo cp bridge.conf /etc/qemu
sudo chmod u+s /usr/lib/qemu/qemu-bridge-helper
sudo ip link add name br0 type bridge
sudo ip link set dev br0 up
sudo ip link set enx00e04c680a67 master br0 && sudo dhclient br0
brctl show   # or: bridge link / ip link
# Use: -netdev bridge,id=net0,br=br0,"helper=/usr/lib/qemu/qemu-bridge-helper"
```

> [!tip] Choose
> - Disposable macOS lab → **user mode + hostfwd**
> - Import into libvirt (`virsh define macOS.xml`) → **virbr0 NAT** (host↔guest `192.168.122.x`)
> - LAN-visible → `br0` bridge (Ethernet only; Wi-Fi bridges need `macvtap`)

See: [[all notes macos]], [[+ MOC macOS]].
