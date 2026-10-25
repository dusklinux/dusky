---
title: "Master Roadmap — Windows GPU Passthrough"
tags:
  - kvm
  - vfio
  - passthrough
  - arch
  - roadmap
---

# Master Roadmap — Windows GPU Passthrough (Aug 2026)

> [!info] One note per phase. Order matters — do not skip. Stack: **Arch · Kernel 7.3+ · systemd 262 · QEMU 11.1 · libvirt 12.6+ · nftables · systemd-boot**.
> Windows 10 vs 11 diff: RAM 4G vs 8G, disk 40G vs 64G, 11 **requires** `TPM 2.0 + UEFI Secure Boot` (osinfo `win11` enforces). Shared Q35/virtio steps deduped to [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]] — this MOC points there (no duplicate bus/cache/virtio explanation).

## Install order

1. [[Virt_Manager Packages for Windows]] → `05_virtio_iso.py` — `qemu-desktop`, `edk2-ovmf`, `swtpm`, `nftables`, `libosinfo` (or [[KVM Packages]] host-generic)
2. [[KVM preperation and Optimization]] — host orientation + `virt-host-validate` (also see [[+ MOC KVM]] Phase 0–2)
3. [[Host PC  Preparation for GPU isolation]] → `15_gpu_probing_kernal_param_mkinit.py` — IOMMU, slot (= all functions), ACS, kernel cmdline, `mkinitcpio`, `modprobe.d` single source, `lsinitcpio` check
4. [[VirtIO win driver iso]] → `05_virtio_iso.py:stage_virtio` — `virtio-win.iso` pool (or [[Mount the VirtIO-Win ISO Image]])
5. [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]] → [[+ MOC Windows Installation Through Virt Manager]] (virt-manager path) **or** `30_kvm_vm_deploy.py` (scripted) — win guest create (**shared Q35/OVMF/virtio/CPU via canonical**; then Windows deltas [[Enable Trusted Platform Module (TPM)]] + [[Enable Hyper-V Enlightenments]] + `viostor`/`NetKVM` in [[Install a Windows Virtual Machine on KVM]])
6. [[Windows Configurations for Passthrough]] — Looking Glass IDD driver (`looking-glass-idd-setup.exe`), `freerdp` (`wlfreerdp` / `xfreerdp3`), `OpenSSH`, `WinFsp`/`VirtIO-FS` (needs canonical [[KVM Setup/VM Creation/06 Guest Integration — Agent, Clipboard & Input|06]] `memoryBacking memfd shared`)
7. [[Looking Glass]] → `25_looking_glass.py` + `60_configure_client_ini.py` — `/dev/shm/looking-glass` + native `<shmem>` ([[KVM Setup/VM Creation/05 CPU — Host-Passthrough & Topology|05 CPU]] topology + [[KVM Setup/VM Creation/02 Chipset & Firmware — Q35 + UEFI|02]] `smm`)

---

## Verify gates (run after each phase)

```bash
virt-host-validate          # before VFIO: QEMU/KVM PASS; IOMMU WARN OK pre-Phase 3
for d in /sys/kernel/iommu_groups/*/devices/*; do printf 'IOMMU Group %s ' "${d#*/iommu_groups/*}"; printf '%s' "${d##*/}"; echo " $(lspci -nns "${d##*/}")"; done | grep -E '10de|8086|1002'
cat /proc/cmdline | tr ' ' '\n' | grep -E 'iommu|vfio'
lspci -nnk -d 10de:25a0      # → vfio-pci after reboot
virsh -c qemu:///system list --all ; virsh -c qemu:///system net-list --all
# after VM create:
virsh -c qemu:///system dumpxml win11 | grep -E 'machine=.q35|firmware=.efi|bus=.virtio|host-passthrough|tpm|hyperv|shmem'
```

> [!danger] Before touching VFIO
> ```bash
> virt-host-validate   # must PASS QEMU/KVM
> ```
> IOMMU warnings at this stage are **expected** — they turn PASS only after [[Host PC  Preparation for GPU isolation]] + reboot.

Speed-run: [[+ MOC SPEEDRUN Windows GPU PASSTHROUGH]] (condensed, assumes you know above; also deduped to canonical `VM Creation/` for shared steps).

See: [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]] (shared VM creation truth), [[+ MOC Windows Installation Through Virt Manager]] (ordered Windows path), [[+ MOC KVM]] (host bring-up), [[Network Bridging for LAN access]] (if LAN-visible after).
