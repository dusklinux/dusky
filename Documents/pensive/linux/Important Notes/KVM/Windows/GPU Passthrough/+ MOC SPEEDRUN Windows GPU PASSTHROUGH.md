---
title: "SPEEDRUN — GPU Passthrough (Condensed)"
tags:
  - kvm
  - vfio
  - arch
  - speedrun
---

# SPEEDRUN — GPU Passthrough (Condensed, Aug 2026)

> [!warning] Fast lane — no explanations. For learning use [[+ MOC Windows GPU Passthrough]] + [[Host PC  Preparation for GPU isolation]]. Stateful phases survive reboot via `/var/lib/arsonix/state.json` (not `/tmp`). Shared `Q35/virtio/TPM/CPU` steps deduped — see [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]] for full wizard; this note keeps only host VFIO + socket + shm.

## Prereqs

```bash
sudo pacman -S --needed qemu-desktop libvirt virt-install virt-manager virt-viewer dnsmasq iproute2 openbsd-netcat edk2-ovmf swtpm nftables libosinfo pciutils
sudo usermod -aG libvirt,kvm,input "$(id -un)"   # re-login!
# libvirt sockets (not libvirtd) — see [[libvert Modular daemon enable]] §3 for real perms
# /etc/libvirt/virtqemud.conf fallback (INERT under systemd socket activation):
# unix_sock_group = "libvirt"
# unix_sock_rw_perms = "0770"
# Real perms: /etc/systemd/system/virtqemud.socket.d/10-arsonix.conf → [Socket] SocketGroup=libvirt SocketMode=0660
```

## Host validation

```bash
lscpu | grep -i virtualization     # VT-x / AMD-V
zgrep -E "CONFIG_KVM=|CONFIG_VFIO_PCI=|CONFIG_IOMMUFD=" /proc/config.gz
for d in /sys/kernel/iommu_groups/*/devices/*; do n=${d#*/iommu_groups/*}; n=${n%%/*}; printf 'Group %s ' "$n"; lspci -nns "${d##*/}"; done | grep -E 'Group 15|NVIDIA'
# → Group 15 01:00.0 [10de:25a0] VGA + 01:00.1 [10de:2291] Audio  (example, ASUS RTX 3050 Ti Mobile)
```

## Boot entry — systemd-boot (not GRUB)

```bash
sudo nvim /boot/loader/entries/arch.conf   # Type1; Type2 → /etc/kernel/cmdline
# append to options line (keep root= rw …):
intel_iommu=on iommu=pt   # AMD: iommu=pt only (amd_iommu=on invalid)
# VFIO claim lives in modprobe.d below — not here (see Host Preparation §Phase 4 + [[KVM Setup/VM Creation/02 Chipset & Firmware — Q35 + UEFI|canonical 02]])
```

## Initramfs

```bash
sudo nvim /etc/mkinitcpio.conf
MODULES=(i915 btrfs vfio_pci vfio vfio_iommu_type1)  # AMD iGPU → amdgpu instead of i915
HOOKS=(base systemd autodetect microcode modconf kms keyboard sd-vconsole block filesystems fsck)
# modconf MUST be before kms; microcode hook (not separate initrd) on 7.1
```

## VFIO modprobe (single source; vfio_virqfd deleted since 6.2)

```bash
sudo nvim /etc/modprobe.d/arsonix-vfio.conf
options vfio-pci ids=10de:25a0,10de:2291
softdep nvidia pre: vfio-pci
softdep nvidia_drm pre: vfio-pci
softdep nvidia_modeset pre: vfio-pci
softdep nvidia_uvm pre: vfio-pci
softdep nouveau pre: vfio-pci
blacklist nouveau
blacklist nvidia
blacklist nvidia_drm
blacklist nvidia_modeset
blacklist nvidia_uvm
sudo mkinitcpio -P
# hybrid laptop only: ensure Hybrid/Optimus in BIOS/G-Helper — Discrete = black screen
sudo reboot
```

## Daemons (modular)

```bash
sudo systemctl stop libvirtd.service libvirtd.socket libvirtd-ro.socket libvirtd-admin.socket libvirtd-tcp.socket libvirtd-tls.socket 2>/dev/null || true
sudo systemctl disable libvirtd.service libvirtd.socket libvirtd-ro.socket libvirtd-admin.socket libvirtd-tcp.socket libvirtd-tls.socket 2>/dev/null || true
sudo systemctl mask libvirtd.service libvirtd.socket libvirtd-ro.socket libvirtd-admin.socket libvirtd-tcp.socket libvirtd-tls.socket 2>/dev/null || true
for drv in qemu interface network nodedev nwfilter secret storage proxy lxc ch vbox; do sudo systemctl enable virt${drv}d.socket virt${drv}d-ro.socket virt${drv}d-admin.socket; done
for drv in log lock; do sudo systemctl enable virt${drv}d.socket virt${drv}d-admin.socket; done
for drv in qemu interface network nodedev nwfilter secret storage proxy lxc ch vbox; do sudo systemctl start virt${drv}d.socket virt${drv}d-ro.socket virt${drv}d-admin.socket; done
for drv in log lock; do sudo systemctl start virt${drv}d.socket virt${drv}d-admin.socket; done
sudo systemctl enable --now libvirt-guests.service
```

## Verify

```bash
systemctl list-sockets | grep virt          # LISTEN
systemctl status virtqemud.service          # inactive (dead) = 0 MB idle ✓
cat /proc/cmdline | tr ' ' '\n' | grep iommu
lspci -nnk -d 10de:25a0  # → vfio-pci
sudo dmesg | grep -i vfio
sudo lsinitcpio /boot/initramfs-linux.img | grep vfio; virt-host-validate
```

## VM (Windows) — canonical one-liner (instead of repeating wizard)

> [!tip] Do not re-document Q35/virtio/CPU here — run:
> [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]] → `virt-install --osinfo win11 --machine q35 --boot uefi --cpu host-passthrough --disk bus=virtio,cache=none,io=io_uring --network network=default,model=virtio --tpm emulator --features hyperv.*` or follow [[+ MOC Windows Installation Through Virt Manager]] (wizard → Q35/OVMF+smm+TPM, storage virtio+viostor, network virtio+NetKVM, CPU host-passthrough+Hyper-V, agent+tablet).
> For Looking Glass: [[Looking Glass]] + [[KVM Setup/VM Creation/06 Guest Integration — Agent, Clipboard & Input|06]] (SPICE + `org.qemu.guest_agent.0`) then [[The RDP method to disable display driver]] + Looking Glass IDD driver (`looking-glass-idd-setup.exe /S /ivshmem`).

Networking → [[Network Bridging for LAN access]] (Option 2/3) or [[KVM Setup/VM Creation/04 Network — Virtio NIC (NAT vs Bridge)]].

See: [[+ MOC Windows GPU Passthrough]] (full), [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]] (VM creation truth), [[Host PC  Preparation for GPU isolation]] (deep VFIO).
