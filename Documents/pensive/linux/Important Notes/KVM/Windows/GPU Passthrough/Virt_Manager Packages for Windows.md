---
title: "KVM Packages for Windows Guest (Arch)"
tags:
  - kvm
  - arch
  - packages
---

# KVM Packages for Windows Guest (Arch · Kernel 7.3+)

> [!abstract] Goal
> Hypervisor stack for Windows guest with passthrough. Replaces stale `qemu-full`/`bridge-utils`/`iptables-nft` lists.

## Install

```bash
sudo pacman -S --needed \
  qemu-desktop libvirt virt-install virt-manager virt-viewer \
  dnsmasq iproute2 openbsd-netcat edk2-ovmf swtpm nftables libosinfo pciutils
```

> [!warning] 2026 firewall shift
> `nftables` is native. Old `iptables-nft` shim renamed → plain `iptables`. libvirt `network.conf` → `firewall_backend="nftables"` (no shim).

### Package roles

| Package | Role |
|---|---|
| `qemu-desktop` | Full virt superset (spice/gtk/virtio-gpu) — **not** `qemu-full` (foreign archs) |
| `libvirt` | Modular daemons `virtqemud`/`virtnetworkd`… |
| `virt-manager`/`virt-viewer`/`virt-install` | GUI + viewer + `virt-install --osinfo` |
| `dnsmasq` | NAT DHCP/DNS |
| `iproute2` | modern bridge/route (replaces `bridge-utils`) |
| `edk2-ovmf` | UEFI firmware JSON + `OVMF_CODE.secboot.4m.fd` |
| `swtpm` | TPM 2.0 for `win11` |
| `nftables` | filtering backend |
| `libosinfo` | OS profile DB |

## Per-user access + sockets (not `libvirtd`)

> [!danger] Don’t edit `libvirtd.conf` for this — monolith deprecated.

```bash
sudo usermod -aG libvirt,kvm,input "$(id -un)"   # log out/in
# perms fallback (inert under socket): /etc/libvirt/virtqemud.conf → unix_sock_group="libvirt" / unix_sock_rw_perms="0770"
sudo systemctl enable --now virtqemud.socket virtnetworkd.socket  # minimal; pipeline enables full fleet + libvirt-guests
```

## Validate

```bash
virt-host-validate   # PASS QEMU/KVM; IOMMU WARN OK pre-VFIO
virsh --version; qemu-system-x86_64 --version
```

See: [[+ MOC Windows GPU Passthrough]], `05_virtio_iso.py`, `10_virt_modular_daemon.py`.
