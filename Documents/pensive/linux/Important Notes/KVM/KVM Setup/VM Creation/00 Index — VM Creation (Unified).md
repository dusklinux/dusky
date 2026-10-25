---
title: "VM Creation — Unified Index (Aug 2026)"
tags:
  - kvm
  - virt-manager
  - arch
  - qemu
  - libvirt
  - moc
aliases:
  - VM Creation Canonical
  - Unified VM Creation
---

# VM Creation — Unified Index (Aug 2026)

> [!abstract] Single source of truth
> All **shared** virt-manager steps — wizard, Q35/OVMF, storage, network, CPU, guest integration — live **here** (`KVM Setup/VM Creation/`). `Windows/` and `gnome-boxes/` notes do **not** duplicate them; they are redirect stubs with only OS-specific deltas. This mirrors `30_kvm_vm_deploy.py:build_command` (`--machine q35`, `host-passthrough`, `virtio`, `cache=none`, `io=io_uring`, `discard=unmap`, `network=default,model=virtio`).

> [!info] How to use
> - **Windows VM** → follow canonical steps 01–06, then apply **Windows delta** sections inline (TPM, Hyper-V, VirtIO drivers). See [[+ MOC Windows Installation Through Virt Manager]] for ordered path.
> - **Linux VM (virt-manager, `qemu:///system`)** → same canonical steps 01–06, skip Windows deltas, use Linux guest-agent route. See [[gnome-boxes/linux]] for Boxes `qemu:///session` SPICE variant (guest `spice-vdagent`, not `virtio-win`).
> - **macOS** → keep separate [[+ MOC macOS]] / [[all notes macos]] (OSX-KVM + OpenCore, not `virt-install` flow).

## Pipeline → notes → XML

| Pipeline | Note | XML knob |
|---|---|---|
| `05_virtio_iso.py` | [[KVM Packages]], [[KVM Group Add]] | — |
| `07_storage_setup.py` | [[Set ACL on the Image Directory]] / [[Symbolic link to zram for image file]] | pool `dir` `rwx` + `default:rwx` |
| `15_gpu_probing_kernal_param_mkinit.py` | [[Host PC  Preparation for GPU isolation]] | `vfio-pci.ids=` in `modprobe.d` |
| `30_kvm_vm_deploy.py` | **this folder** | `--osinfo` `q35` `host-passthrough` `virtio` `io_uring` `unmap` `tpm-crb` `hyperv.*` |

## Steps (follow in order)

| # | Canonical note | Shared? | Windows delta | Linux delta |
|---|---|---|---|---|
| 01 | [[KVM Setup/VM Creation/01 Wizard — Create VM\|01 Wizard]] | ✅ | `win11` osinfo, 8 GiB / 64 GiB, `virtio-win.iso` 2nd CDROM | `archlinux` / `linux2024` osinfo, 4 GiB / 20–40 GiB, **no** 2nd ISO (virtio in-kernel) |
| 02 | [[KVM Setup/VM Creation/02 Chipset & Firmware — Q35 + UEFI\|02 Chipset & Firmware]] | ✅ | `smm=on` + TPM 2.0 ([[Enable Trusted Platform Module (TPM)]]) mandatory for `win11` | `smm` optional; Secure Boot optional (no TPM) |
| 03 | [[KVM Setup/VM Creation/03 Storage — Virtio Bus, Cache, io_uring, Discard\|03 Storage]] | ✅ | load `viostor` at install ([[Install a Windows Virtual Machine on KVM]]) | **no driver load** — kernel virtio-blk in box |
| 04 | [[KVM Setup/VM Creation/04 Network — Virtio NIC (NAT vs Bridge)\|04 Network]] | ✅ | load `NetKVM` if needed at OOBE, else `virtio-win-guest-tools` | **no driver load** — virtio-net in-kernel |
| 05 | [[KVM Setup/VM Creation/05 CPU — Host-Passthrough & Topology\|05 CPU]] | ✅ | + Hyper-V enlightenments ([[Enable Hyper-V Enlightenments]]) | plain `host-passthrough`; no Hyper-V |
| 06 | [[KVM Setup/VM Creation/06 Guest Integration — Agent, Clipboard & Input\|06 Guest Integration]] | ✅ | `qemu-guest-agent` via `virtio-win-guest-tools` + `spice-agent` + optional tablet removal | `spice-vdagent` + `qemu-guest-agent` via `pacman` (see [[gnome-boxes/linux]] + [[gnome-boxes]]) |

## Quick CLI (scripted, same as manual)

```bash
# Windows (win11, 8 GiB, 64 GiB qcow2, TPM, Hyper-V, virtio, io_uring)
virt-install --connect qemu:///system --osinfo win11 --machine q35 --boot uefi \
  --vcpus 4 --memory 8192 --cpu host-passthrough,migratable=off,cache.mode=passthrough \
  --disk path=/var/lib/libvirt/images/win11.qcow2,format=qcow2,bus=virtio,driver.cache=none,driver.io=io_uring,driver.discard=unmap \
  --network network=default,model=virtio --graphics spice,listen=none --video virtio \
  --tpm backend.type=emulator,backend.version=2.0,model=tpm-crb --features hyperv.relaxed.state=on,hyperv.vapic.state=on,hyperv.spinlocks.state=on \
  --clock hypervclock.present=yes --channel spicevmc --channel unix,target.type=virtio,target.name=org.qemu.guest_agent.0

# Arch Linux (archlinux, 4 GiB, 40 GiB, no TPM, no Hyper-V)
virt-install --connect qemu:///system --osinfo archlinux --machine q35 --boot uefi \
  --vcpus 4 --memory 4096 --cpu host-passthrough,migratable=off,cache.mode=passthrough \
  --disk path=/var/lib/libvirt/images/archlinux.qcow2,format=qcow2,bus=virtio,driver.cache=none,driver.io=io_uring,driver.discard=unmap \
  --network network=default,model=virtio --graphics spice,listen=none --video virtio --channel spicevmc \
  --channel unix,target.type=virtio,target.name=org.qemu.guest_agent.0
```

## Legacy / dedup

> [!warning] Legacy — where old docs lived
> - `Windows/Configure Chipset and Firmware.md` / `Configure the Storage.md` / `Configure Virtual Network Interface.md` / `Enable CPU Host-Passthrough.md` / `Configure Default Virtual Hardware Using the Wizard.md` — **now redirect stubs** → pointer to this folder + only delta inline. Keeps filenames for `[[wikilink]]` stability; body is `> [!tip] Merged into [[…]]`.
> - `gnome-boxes/linux.md` — still the Boxes `qemu:///session` guide (clipboard plumbing), but virt-manager Linux path now points here. No duplicate Q35/virtio explanation.

## Verify

```bash
virsh -c qemu:///system list --all
virsh -c qemu:///system dominfo win11 2>/dev/null | grep -E 'CPU|Memory'
virsh -c qemu:///system dumpxml win11 | grep -E "machine=.q35|firmware=.efi|bus=.virtio|host-passthrough|tpm|hyperv"
virt-host-validate | grep -E 'QEMU|KVM'
```

See: [[+ MOC KVM]] (host bring-up), [[+ MOC Windows Installation Through Virt Manager]] (ordered Windows path), [[+ MOC Windows GPU Passthrough]] (VFIO layer), [[+ MOC macOS]] (separate).
