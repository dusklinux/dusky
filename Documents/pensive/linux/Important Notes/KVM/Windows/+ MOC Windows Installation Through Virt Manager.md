---
title: "Windows on KVM — Master Path (virt-manager)"
tags:
  - kvm
  - windows
  - virt-manager
  - arch
  - moc
aliases:
  - Windows VM Roadmap
---

# Windows on KVM — Master Path (virt-manager)

> [!summary] Goal
> Linear, ordered path to a fast, de-bloated Windows 10/11 VM on Arch (QEMU 11.1, libvirt 12.6, systemd-boot, nftables). Each step links to a focused note. Canonical automation: `30_kvm_vm_deploy.py` (`--osinfo`, `q35`, `host-passthrough`, `virtio`, `tpm-crb`). **Shared steps live in [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]] — this MOC adds Windows ordering + deltas.**

## 1. Virtual hardware (before first boot) — canonical + Windows deltas

> [!tip] Single source of truth
> `Windows/Configure *.md` are now **redirect stubs** → pointer to [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]] + only Windows delta inline. Follow the canonical note, then note the delta here where flagged. Prevents duplication with Linux ([[gnome-boxes/linux]] + canonical Linux route).

- **1.1** [[KVM Setup/VM Creation/01 Wizard — Create VM]] (canonical) ↔ stub [[Configure Default Virtual Hardware Using the Wizard]] — `virt-manager` wizard, `--osinfo win11` (not `--os-variant`), 4G/64G thin `qcow2` (`cluster_size=64k,lazy_refcounts=on`, `cache=none,io=io_uring|native,discard=unmap`)
- **1.2** [[KVM Setup/VM Creation/02 Chipset & Firmware — Q35 + UEFI]] ↔ stub [[Configure Chipset and Firmware]] — `Q35`, `UEFI x86_64 (OVMF)` (`OVMF_CODE.secboot.4m.fd`), `smm.state=on`, snapshot note (**Windows delta: `smm=on` mandatory for TPM; see [[Enable Trusted Platform Module (TPM)]]**)
- **1.3** [[KVM Setup/VM Creation/05 CPU — Host-Passthrough & Topology]] + [[Enable Hyper-V Enlightenments]] / [[Hyper-V Enlightenments]] / [[Hypervisor Features]] ↔ stub [[Enable CPU Host-Passthrough]] — `hyperv mode='custom'` enlightenments, `kvm hidden`, `vmport off`, `ioapic driver='kvm'`, clock `hypervclock` (**Windows-only**; Linux skips Hyper-V)
- **1.4** [[KVM Setup/VM Creation/05 CPU — Host-Passthrough & Topology]] ↔ stub [[Enable CPU Host-Passthrough]] — `host-passthrough` + `cache.mode=passthrough`, topology `sockets×cores×threads = vCPU`
- **1.5** [[KVM Setup/VM Creation/03 Storage — Virtio Bus, Cache, io_uring, Discard]] ↔ stub [[Configure the Storage]] — `VirtIO` vs `VirtIO SCSI`, `io_uring` (QEMU ≥10.2) vs `native`+`iothreads`, `unmap` (**Windows delta: load `viostor` at install from `virtio-win.iso`**)
- **1.6** [[Mount the VirtIO-Win ISO Image]] — attach `virtio-win.iso` (`/var/lib/libvirt/images` symlink or `/usr/share/virtio-win`) (**Windows-only**; Linux virtio in-kernel)
- **1.7** [[KVM Setup/VM Creation/04 Network — Virtio NIC (NAT vs Bridge)]] ↔ stub [[Configure Virtual Network Interface]] + [[Network Bridging for LAN access]] — `virtio` model, `network='default'` (NAT) or `br0` (**Windows delta: `NetKVM` if needed at OOBE**)
- **1.8** [[KVM Setup/VM Creation/06 Guest Integration — Agent, Clipboard & Input]] ↔ stub [[Remove the USB Tablet Device]] — optional latency trade
- **1.9** [[KVM Setup/VM Creation/06 Guest Integration — Agent, Clipboard & Input]] ↔ stub [[Add QEMU Guest Agent Channel]] — `spicevmc` + `org.qemu.guest_agent.0` (`virsh shutdown --mode agent`)
- **1.10** [[Enable Trusted Platform Module (TPM)]] — `swtpm` `tpm-crb` `2.0` + `smm` (required for `win11` osinfo; skip on patched LTSC) (**Windows-only**; [[KVM Setup/VM Creation/02 Chipset & Firmware — Q35 + UEFI]] `> [!info] Windows route`)

> [!info] Linux contrast (same canonical, different delta)
> Linux skips **1.6** (`virtio-win.iso`), **1.10** (TPM), **1.3** (Hyper-V), uses `archlinux` osinfo, 4 GiB, no `viostor`/`NetKVM`. See [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]] Windows vs Linux table + [[gnome-boxes/linux]].

## 2. Install

- [[Install a Windows Virtual Machine on KVM]] — load `viostor` (disk) + `NetKVM` (net) from `virtio-win.iso` → `virtio-win-guest-tools` (**Windows-only** load; Linux needs no load)

## 3. Optional security (skip for gaming)

- [[Optional Enable Hardware Security on Windows]] — Core Isolation / Memory Integrity → requires `vmx`/`svm` nested feature + `host-passthrough` (perf penalty)

## 4. Debloat & tune

- [[Optimize Windows Performance]] hub → [[Disable SysMain]] · [[Disable Windows Web Search]] · [[Disable useplatformclock]] · [[Disable Unnecessary Scheduled Tasks]] · [[Disable Unnecessary Startup Programs]] · [[Adjust the Visual Effects in Windows 11]]
- For passthrough hosts also: [[Windows Configurations for Passthrough]] (Looking Glass IDD driver + OpenSSH)

## 5. Integration

- [[Setting up Shared Directory Between Guest_win11 and Host]] — `virtiofs` (`host_zram`, `Enable shared memory` + `WinFsp` + `VirtIO-FS Service` `Automatic`) — needs [[KVM Setup/VM Creation/06 Guest Integration — Agent, Clipboard & Input|06]] `memoryBacking memfd shared`
- [[Network Bridging for LAN access]] Option 3 (`br0`) if LAN-visible needed (Ethernet only)

## 6. Wrap

- [[Conclusion Win]] — custom de-bloated ISO tip (Defender-removed)
- [[Resize aka extend storage after os is already installed]] — `qemu-img resize` + DiskMgmt `Extend Volume` / `diskpart` / GParted

> [!tip] Scripted alternative
> `30_kvm_vm_deploy.py` builds the same baseline via `virt-install --osinfo win11 --machine q35 … --disk cache=none,io=io_uring … --network network=default,model=virtio --tpm … --features hyperv.* --clock hypervclock … --graphics spice,listen=none --video virtio`, then injects `<shmem>` for Looking Glass. Prefer it for reproducible labs. Linux variant: `--osinfo archlinux` (no `--tpm`, no `hyperv.*`).

```bash
virt-install --connect qemu:///system --osinfo win11 --machine q35 --boot uefi \
  --vcpus 4 --memory 8192 --cpu host-passthrough,migratable=off \
  --disk path=/var/lib/libvirt/images/win11.qcow2,format=qcow2,bus=virtio,driver.cache=none,driver.io=io_uring,driver.discard=unmap \
  --network network=default,model=virtio --tpm backend.type=emulator,backend.version=2.0,model=tpm-crb
```

See: [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]] (canonical shared), [[+ MOC Windows GPU Passthrough]] for VFIO variant, [[+ MOC KVM]] for host bring-up.
