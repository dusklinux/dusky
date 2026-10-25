---
title: "Create a VM — virt-manager wizard"
tags:
  - kvm
  - manual
aliases:
  - VM Wizard Unified
reviewed: 2026-10-25
---

# Create a VM — virt-manager wizard

Connect with `virt-manager --connect qemu:///system` as your desktop user.
If the inventory is empty, check the URI before assuming disks or definitions
are missing. Complete [[+ MOC KVM|host setup]] first.

1. Choose **Create a new virtual machine → Local install media**.
2. Select the OS ISO and the actual OS profile. For Windows 11 use `win11`, not
   `win10` to evade a missing TPM configuration. An OS profile supplies defaults;
   inspect the resulting hardware instead of assuming everything was selected.
3. Choose RAM/vCPUs for the host/workload. The Windows deployment script defaults
   to 8 GiB fixed RAM, asks for a custom amount and offers optional ballooning.
   See [[07 Memory — Fixed and Ballooning]]; 8 GiB on an 8 GiB host leaves no
   adequate physical host headroom. A vCPU is a scheduled virtual processor,
   not an exclusively reserved physical core.
4. Select a storage pool and create a qcow2 volume. The Windows script defaults
   to 80 GiB capacity; Microsoft's documented Windows 11 storage floor is 64 GB.
   Sparse allocation grows with actual writes. Choose a size suitable for updates
   and applications; use [[03 Storage — Virtio Bus, Cache, io_uring, Discard]].
5. Give the VM a distinct name and select **Customize configuration before
   install**. Review firmware, CPU, disk, NIC, console and agents before booting.
6. For VirtIO Windows disk/NICs, attach [[Mount the VirtIO-Win ISO Image|the VirtIO-Win ISO]] as a SATA CD-ROM. SATA/e1000e can use Windows inbox drivers;
   a typical Linux ISO already contains VirtIO drivers.

Enable virt-manager's XML editing preference if using its XML tab. That is a GUI
editing preference, not a hypervisor requirement; `virsh edit` works separately.
After starting installation, follow [[Install a Windows Virtual Machine on KVM]].

Earlier screenshots are retained as historical UI examples:
![[Pasted image 20250726180159.png]]
![[Pasted image 20250727150813.png]]

For CLI learning, use [[Windows 11 XML — Baseline]]. The scripted equivalent is
`python3 30_kvm_vm_deploy.py --windows --dump-only`: it interviews you and previews
XML without provisioning storage. Preview flags matter: raw virt-install XML
printing without its dry-run protections is not a universal no-side-effect mode.
