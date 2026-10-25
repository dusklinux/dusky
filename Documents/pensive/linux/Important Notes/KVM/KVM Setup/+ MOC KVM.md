---
title: "KVM host setup — shared Windows and Linux steps"
tags:
  - kvm
  - manual
aliases:
  - KVM Master Checklist
reviewed: 2026-10-25
---

# KVM host setup — shared Windows and Linux steps

Start at [[00 Start Here — KVM Manual]]. Prepare the host once, then follow the
OS-specific installation guide. Basic virtualization does not require VFIO,
GPU isolation, an IOMMU or a special performance profile.

## Manual order

1. [[Verify VT-x and Kernel Modules and IOMMU|Check firmware and KVM]]. Enable
   CPU virtualization; add IOMMU checks only if assigning physical PCI devices.
2. [[KVM Packages|Install or stage the package set]] and installation media.
3. [[libvert Modular daemon enable|Configure modular libvirt sockets]].
4. [[Give the User System-Wide Permission|Verify regular-user system access]].
5. [[Symbolic link to zram for image file|Choose storage]] and
   [[Set ACL on the Image Directory|verify directory access]]. Daemon setup now
   precedes storage in the Windows profile so QEMU's real default account can
   be discovered from libvirt, rather than guessed from commented examples.
6. [[Activating Network and Setting it to Autostart|Start the NAT network]], or
   use [[Host Networking — NAT and Wired Bridge|a wired bridge]] if LAN visibility
   is needed. NAT works on ordinary Wi-Fi uplinks.
7. [[00 Index — VM Creation (Unified)|Create the guest]]. For Windows continue
   with [[+ MOC Windows Installation Through Virt Manager]].

Optional branches: [[GPU Passthrough — Host Isolation and Return|VFIO]],
[[Optimize the Host with TuneD|host tuning]],
[[08 Backup and Restore — Disks, Firmware and TPM|backup]].

## Script equivalents

From `~/user_scripts/dusky_vm/passthrough`:

```sh
python3 000_dusky_vm.py --windows --dry-run
python3 000_dusky_vm.py --windows --reset-and-run --setup-only --manual
python3 000_dusky_vm.py --windows --deploy-only
```

Reset receipts for a genuinely fresh host, especially after restoring an old
home directory. For an interrupted setup on the same host, resume without
resetting. Host setup and deployment are separate so rerunning setup need not
create a second VM. The Windows profile offers GPU isolation, Looking Glass and
RDP as choices; none is required for a plain setup console.

The Linux profile shares package/daemon/network preparation but has its own
`linux/deploy_vm.py` and optional `linux/guest_integration.py`. It is not the
Windows interview with a different OS name. Do not install Windows drivers or
copy Windows Hyper-V settings into Linux by rote.

## Final host checks

```sh
virt-host-validate qemu
virsh -c qemu:///system version
virsh -c qemu:///system list --all
virsh -c qemu:///system net-list --all
virsh -c qemu:///system pool-list --all
```

Read each validation result. An IOMMU warning matters for passthrough, not an
ordinary virtual disk/NIC guest. A listening socket can have an inactive daemon
until first use; an active daemon while clients or VMs exist is also normal.
