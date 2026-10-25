---
title: "GPU Isolation — VFIO Binding (Arch, Kernel 7.3+)"
tags:
  - kvm
  - vfio
  - arch
  - kernel
  - systemd-boot
  - mkinitcpio
aliases:
  - Host PC Preparation for GPU isolation
---

# GPU Isolation — VFIO Binding (Arch, Kernel 7.3+)

> [!info] Audited
> `15_gpu_probing_kernal_param_mkinit.py` (sysfs truth, IOMMU group + ID-collision audits, BLS Type1/Type2, `mkinitcpio` `modconf<kms`, softdep vs blacklist). Binding via `vfio-pci` predates 6.x; rock-solid on 7.3+. Canonical state: `/var/lib/arsonix/state.json`.

> [!warning] MUX switch (laptop)
> BIOS → **Hybrid / Optimus** (not Discrete/NVIDIA-only) or you blind the host (no iGPU to render `sddm`/`gdm`). Use G-Helper on ASUS.

## Phase 1 — Identify

```bash
lspci -nn | grep -E "NVIDIA|VGA|Audio"
# 01:00.0 [10de:25a0] VGA — RTX 3050 Ti Mobile
# 01:00.1 [10de:2291] Audio  (both functions = one slot; also check 0c03 xHCI / 0c80 UCSI on some cards)
```

### IOMMU groups (truth)

```bash
#!/usr/bin/env bash
shopt -s nullglob 2>/dev/null || setopt NULL_GLOB
for g in /sys/kernel/iommu_groups/*; do echo "Group ${g##*/}:"; for d in $g/devices/*; do echo -e "\t$(lspci -nns ${d##*/})"; done; done
# expect: Group 15: 01:00.0 [10de:25a0] + 01:00.1 [10de:2291] only (bridges OK)
```

> [!tip] Crowded group?
> Move slot or board with real ACS. **ACS-override** (`linux-vfio` AUR) fakes isolation — last resort, weakens DMA isolation.

## Phase 2 — Bootloader (systemd-boot)

```bash
sudo nvim /boot/loader/entries/arch.conf   # Type1 snippet; Type2 UKI → /etc/kernel/cmdline or /etc/cmdline.d/99-arsonix-vfio.conf
# append to existing `options` (keep root= rw quiet …):
intel_iommu=on iommu=pt
# AMD: iommu=pt only — amd_iommu=on is ignored (see 15_*:desired_params); only amd_iommu=force_enable for broken IVRS
# do NOT add vfio-pci.ids= here — committed via modprobe.d (§Phase 4) baked into initramfs
# optional: module_blacklist=nouveau,nvidia,nvidia_drm,nvidia_modeset,nvidia_uvm (only when discrete vendor not shared)
sudo mkinitcpio -P || sudo kernel-install add-all
```

> [!important] One source
> Type2 pipeline enforces a **single** UKI source (`/etc/kernel/cmdline` vs `/etc/cmdline.d/*.conf`) and de-pollutes duplicates — prevents concatenated `vfio-pci.ids=`.

## Phase 3 — Initramfs

```bash
sudo nvim /etc/mkinitcpio.conf
MODULES=(i915 btrfs vfio_pci vfio vfio_iommu_type1)  # AMD iGPU → amdgpu
HOOKS=(base systemd autodetect microcode modconf kms keyboard sd-vconsole block filesystems fsck)
# microcode now via `microcode` hook (not separate initrd line); remove stale initrd /intel-ucode.img line
# modconf MUST precede kms so blacklist/softdep lands before DRM probe
# optional drop-in: /etc/mkinitcpio.conf.d/99-arsonix-vfio.conf → MODULES+=(vfio_pci vfio vfio_iommu_type1)
```

> [!info] `vfio_virqfd` gone since 6.2 — folded into `vfio`.

## Phase 4 — Modprobe (the real claim)

```bash
sudo nvim /etc/modprobe.d/arsonix-vfio.conf
```
```ini
# Single source of truth — every boot
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
# never blacklist shared snd_hda_intel/xhci_pci/i2c_designware — 15_*:NEVER_BLACKLIST
```

> [!danger] Dual-AMD (iGPU + dGPU same `amdgpu`)
> Do not `blacklist amdgpu` — blinds host. Rely on `vfio-pci ids=` + `softdep amdgpu pre: vfio-pci`.

Pipeline audits: `audit_groups` (ACS) + `audit_id_collisions` (`vendor:device` is a *class* — if twin cards share IDs, `ids=` would steal both; use `driver_override` address-bind instead).

## Phase 5 — Rebuild

```bash
sudo mkinitcpio -P
systemctl reboot
```

## Phase 6 — Verify

```bash
lspci -nnk -d 10de:25a0  # Kernel driver in use: vfio-pci
sudo dmesg | grep -i vfio
# vfio_pci: add [10de:25a0[ffff:ffff]] … ffff:ffff = 4 hex wildcard (subvendor/subdevice)
sudo dmesg | grep -i -e DMAR -e IOMMU
sudo lsinitcpio /boot/initramfs-linux.img | grep vfio
```

## Phase 7 — Manual unbind (if native driver won)

```bash
# rare — e.g. fallback image sans modconf, or dynamic switch-back-to-host workflow
echo "0000:01:00.0" | sudo tee /sys/bus/pci/devices/0000:01:00.0/driver/unbind
echo "vfio-pci" | sudo tee /sys/bus/pci/devices/0000:01:00.0/driver_override
echo "0000:01:00.0" | sudo tee /sys/bus/pci/drivers_probe
# repeat for .1 audio function if still bound
```

## Appendix

- **Revert:** delete `arsonix-vfio.conf`, remove `vfio-pci.ids`/`module_blacklist` from loader entry, `mkinitcpio -P`, reboot.
- **Dynamic workflow:** skip blacklist/cmdline, keep native driver, bind via `driver_override` right before `virsh start` and unbind after.
- **Looking ahead:** host binding stops here; QEMU side now prefers `iommufd` (`vfio_iommu_type1` container is legacy but still supported).

Sources: Arch Wiki PCI passthrough via OVMF, kernel.org IOMMUFD, `driver_override` sysfs.
