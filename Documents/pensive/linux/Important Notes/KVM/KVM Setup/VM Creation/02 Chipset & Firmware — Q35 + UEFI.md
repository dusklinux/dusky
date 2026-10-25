---
title: "Chipset and firmware — Q35, UEFI and NVRAM"
tags:
  - kvm
  - manual
aliases:
  - Q35 Unified
reviewed: 2026-10-25
---

# Chipset and firmware — Q35, UEFI and NVRAM

[[+ MOC KVM|KVM home]] · [[00 Index — VM Creation (Unified)|Virtual hardware]]

Q35 is this workflow's modern x86 PCIe machine model. Use it for new domains;
changing an established guest's chipset can change its device layout and boot
behavior. Preserve its existing versioned machine model during ordinary updates
unless deliberately testing a change. Q35 is not a universal architectural
requirement of every possible KVM guest.

Use guest UEFI from edk2-ovmf. In virt-manager choose the appropriate UEFI
firmware; libvirt discovers installed firmware descriptors. An automatically
selected definition starts with:

```xml
<os firmware='efi'>
  <type arch='x86_64' machine='q35'>hvm</type>
  <boot dev='hd'/>
</os>
```

A complete newly defined domain may be normalized to specific loader, NVRAM and
machine paths. Do not copy those resolved paths, UUIDs or PCI addresses from
another computer. Inspect local support with:

```sh
virsh -c qemu:///system domcapabilities --virttype kvm --arch x86_64 --machine q35
virsh -c qemu:///system dumpxml win11 --inactive
```

## Windows 11 additions

Use emulated TPM 2.0: [[Enable Trusted Platform Module (TPM)]]. The current
Windows deployment adds SMM for its x86 Q35/UEFI configuration; SMM is not a
standalone Microsoft TPM requirement or a setting to copy to every Linux VM.

**Secure Boot capability, enrolled keys and actual enforcement are different.**
An OVMF filename containing `secboot`, or XML advertising `secure-boot`, does not
prove Windows booted with Secure Boot enabled. Libvirt's `enrolled-keys='no'`
explicitly does not request a firmware template with pre-enrolled keys.
Verify Windows with `msinfo32` / `Confirm-SecureBootUEFI` when that state matters.
To request an enrolled-key Secure Boot configuration for a new VM, use supported
firmware features from local domain capabilities; do not overwrite an existing
VM's NVRAM merely to change the advertised XML flag.

The host's UEFI Secure Boot and the guest's firmware state are separate. Do not
disable host Secure Boot as a generic Windows guest setup step.

## Preserve firmware state

NVRAM stores persistent guest firmware variables. Preserve it with the domain,
disks and TPM: [[08 Backup and Restore — Disks, Firmware and TPM]]. Snapshot
support depends on the actual QEMU/libvirt/device combination; shutting the VM
off alone does not turn every NVRAM/TPM configuration into a supported internal
snapshot workflow. A consistent cold backup is the documented recovery route.

Sources: [libvirt firmware selection](https://libvirt.org/formatdomain.html#operating-system-booting),
[Windows 11 requirements](https://learn.microsoft.com/en-us/windows/whats-new/windows-11-requirements).

---

Previous: [[01 Wizard — Create VM]]. Continue: [[03 Storage — Virtio Bus, Cache, io_uring, Discard]].
