---
title: "MOC — KVM Host Bring-Up (Aug 2026)"
tags:
  - kvm
  - arch
  - vfio
  - libvirt
  - moc
aliases:
  - KVM Master Checklist
---

# MOC — KVM Host Bring-Up (Aug 2026)

> [!info] Canonical pipeline
> This checklist mirrors the **Arsonix pipeline** under `~/user_scripts/dusky_vm/passthrough/` — each entry maps to a script phase. Prefer the scripts for idempotent execution; use these notes to *learn* what each phase does.

## Phase 0 — Firmware

- [ ] Enable in UEFI: **VT-x / SVM**, **VT-d / AMD-Vi**, **Above 4G Decoding**, **ReBAR**, **ACS** if present, disable `Secure Boot` only if using unsigned OVMF (`edk2-ovmf` ships signed `OVMF_CODE.secboot.4m.fd` — leave SB on for `win11` osinfo if you want)
- [ ] [[Verify VT-x and Kernel Modules and IOMMU]] — `lscpu`, `zgrep CONFIG_KVM`, `/proc/cmdline`, IOMMU group map

## Phase 1 — Hypervisor staging

- [ ] [[KVM Packages]] → `05_virtio_iso.py` — `qemu-desktop`/`libvirt`/`virt-manager`/`edk2-ovmf`/`swtpm`/`nftables`/`libosinfo`, VirtIO ISO
- [ ] [[KVM Loading Kernel Module]] — `lsmod | grep kvm`, `/etc/modules-load.d/kvm.conf` (rarely needed; `udev` autoloads)
- [ ] [[KVM Group Add]] + [[Give the User System-Wide Permission]] — `libvirt,kvm,input` groups, `LIBVIRT_DEFAULT_URI=qemu:///system` via `~/.config/environment.d/libvirt.conf`
- [ ] [[Symbolic link to zram for image file]] / [[Set ACL on the Image Directory]] → `07_storage_setup.py` — pool dir + POSIX ACL (`u:operator:rwx` + `d:u:operator:rwx`, `--x` on parents), `mount_facts` volatile check, `qemu.conf` root:root note

## Phase 2 — Modular daemons

- [ ] [[check libvert modular daemon availability]] + [[libvert Modular daemon enable]] → `10_virt_modular_daemon.py` — eradicate `libvirtd`, enforce socket activation, drop-ins `SocketGroup=libvirt`/`SocketMode=0660` (conf keys are inert), `virtqemud.socket` active, daemons idle = 0 RSS
- [ ] [[All Libvert Daemons]] / [[KVM Services]] — reference; do **not** `enable libvirtd.service`
- [ ] [[Activating Network and Setting it to Autostart]] + [[Network Bridging for LAN access]] → `20_networking_nmcli.py` — `default` NAT or `br0` via `nmcli`, `nftables` backend, `dnsmasq`

## Phase 3 — VFIO isolation (reboot boundary)

- [ ] [[Grub Kernal Parameters]] + [[KVM Prepare dGPU passthrough]] → `15_gpu_probing_kernal_param_mkinit.py` — `bootctl` Type1/Type2, `intel_iommu=on`/`iommu=pt` (no `amd_iommu=on`), `vfio-pci ids=` only in `/etc/modprobe.d/arsonix-vfio.conf`, softdep vs blacklist, `mkinitcpio` `modconf` before `kms`
- [ ] [[Host PC  Preparation for GPU isolation]] — IOMMU group + ID-collision audits, `boot_vga` warning, `lsinitcpio | grep vfio`

## Phase 4 — Guest stack (unified)

- [ ] **Unified VM creation** [[KVM Setup/VM Creation/00 Index — VM Creation (Unified)]] → `30_kvm_vm_deploy.py` — `virt-install --osinfo` with `host-passthrough`/`q35`/`virtio`/`io_uring`/`cache=none`/`discard=unmap`
  - [ ] [[KVM Setup/VM Creation/01 Wizard — Create VM]] — wizard + osinfo (`win11` vs `archlinux`, `qemu-img create -o cluster_size=64k,lazy_refcounts=on`)
  - [ ] [[KVM Setup/VM Creation/02 Chipset & Firmware — Q35 + UEFI]] — Q35 + OVMF `OVMF_CODE.secboot.4m.fd` + `smm` (Windows `smm=on`+TPM)
  - [ ] [[KVM Setup/VM Creation/03 Storage — Virtio Bus, Cache, io_uring, Discard]] — `virtio-blk/scsi`, `none`, `io_uring`/`native`+iothreads, `unmap` (Windows `viostor` load vs Linux in-kernel)
  - [ ] [[KVM Setup/VM Creation/04 Network — Virtio NIC (NAT vs Bridge)]] — `virtio` + `default` NAT vs `br0` bridge (Windows `NetKVM` vs Linux in-kernel)
  - [ ] [[KVM Setup/VM Creation/05 CPU — Host-Passthrough & Topology]] — `host-passthrough` + `cache.mode=passthrough` + Hyper-V enlightenments (Windows) vs plain (Linux)
  - [ ] [[KVM Setup/VM Creation/06 Guest Integration — Agent, Clipboard & Input]] — `spicevmc` + `org.qemu.guest_agent.0`, tablet optional
- [ ] Windows path [[+ MOC Windows Installation Through Virt Manager]] → after canonical, adds [[Enable Trusted Platform Module (TPM)]] (`tpm-crb` 2.0), [[Enable Hyper-V Enlightenments]] / [[Hyper-V Enlightenments]] / [[Hypervisor Features]], [[Install a Windows Virtual Machine on KVM]] (`viostor`/`NetKVM`), `virtio-win-guest-tools`
- [ ] Linux path [[gnome-boxes/linux]] + [[gnome-boxes]] — for Boxes `qemu:///session` SPICE plumbing ( `spice-vdagent` user service, Xwayland, wl-clipboard bridge); for `qemu:///system` Linux use canonical above (no TPM/Hyper-V)
- [ ] [[Looking Glass]] → `25_looking_glass.py` + `60_configure_client_ini.py` — `looking-glass-idd-setup.exe` IDD driver, `/dev/shm/looking-glass` `0660 operator:kvm`, `O_EXCL|O_NOFOLLOW`, `posix_fallocate`, `<shmem>` ivshmem-plain, client config (`[lgmp] shmDevice`, `autoResize=no`, `setGuestRes=yes`)
- [ ] Optional: [[Optimize the Host with TuneD]] — `tuned` vs `TLP` conflict

> [!warning] Legacy entries kept as warnings
> `KVM Services` (`libvirtd.service`), `Grub Kernal Parameters` (GRUB), `Give the User System-Wide Permission_old` ( `disk`/`input` groups), `Windows/Configure *.md` (now redirect stubs → `VM Creation/` canonical), `gnome-boxes/linux` old duplicate Q35 sections (now single `VM Creation/` source) remain in vault only inside `> [!warning] Legacy` callouts.

## Verify

```bash
virt-host-validate | grep -E 'QEMU|KVM'   # QEMU/KVM should PASS
virsh -c qemu:///system list --all
virsh -c qemu:///system dumpxml win11 2>/dev/null | grep -E 'machine=.q35|firmware=.efi|bus=.virtio|host-passthrough'
```

## Cross-links

- [[Source Tutorial Link]] — upstream sysguides primer (pre-modular era)
- State: `/var/lib/arsonix/state.json` (durable, not `/tmp`) — survives reboot, see `05_virtio_iso.py:state_merge`
- References: [[arch GPU ACCELERATION reference xml]] / [[arch linux GPU ACCELERATION intel integrated xml]] / [[arch linux zram 20GB xml]] / [[windows 10 virt xml file]] — full domain XML + snippets

