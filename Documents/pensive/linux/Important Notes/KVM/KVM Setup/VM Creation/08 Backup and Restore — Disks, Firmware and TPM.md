---
title: "VM backup and restore — disks, firmware and TPM"
tags:
  - kvm
  - manual
reviewed: 2026-10-25
---

# VM backup and restore — disks, firmware and TPM

[[+ MOC KVM|KVM home]] · [[00 Index — VM Creation (Unified)|Virtual hardware]]

A working VM is more than its qcow2. Preserve its domain definition, every disk
and backing image, NVRAM, TPM state and any required host-device/network setup.
A shared host directory is separate data and is not part of a VM snapshot.

## Cold backup — easiest to reason about

1. Ask Windows/Linux to shut down normally; verify `domstate` is **shut off**.
2. Identify actual image paths and chains. Do not guess them from the pool name.
3. Copy the disks and VM state to mounted persistent storage.
4. Validate the backup and retain the original until restoration boots correctly.

```sh
virsh -c qemu:///system domstate win11
virsh -c qemu:///system domblklist win11 --inactive --details
virsh -c qemu:///system dumpxml win11 --inactive > win11.xml
# Only after the guest is fully stopped:
qemu-img info --backing-chain /path/to/win11.qcow2
```

For a standalone qcow2, a sparse copy can use:

```sh
cp --reflink=auto --sparse=always -- /source/win11.qcow2 /mounted/backup/win11.qcow2
qemu-img check -f qcow2 /mounted/backup/win11.qcow2
sha256sum /source/win11.qcow2 /mounted/backup/win11.qcow2
```

Use new destination names; do not overwrite your only known-good copy. A
reflink on the same storage is not protection against losing that storage.
If a backing chain exists, preserve the whole chain and its path relationships,
or deliberately flatten it with an offline `qemu-img convert`; record the
changed layout. A qcow2 check validates image structure, not every guest file.

## Definition, firmware and TPM

`backup_restore/backup_restore_virt_xml_config.py` backs up libvirt configuration,
domains, firmware and TPM state. It explicitly excludes guest disks and
managed-save RAM. Read its `--help`; for example:

```sh
sudo python3 ~/user_scripts/dusky_vm/backup_restore/backup_restore_virt_xml_config.py --backup /mounted/backup/libvirt-state --non-interactive
sudo python3 ~/user_scripts/dusky_vm/backup_restore/backup_restore_virt_xml_config.py --verify /mounted/backup/libvirt-state
```

The tool coordinates management daemons for the state capture. For a manual
backup, inspect `<nvram>` and TPM backend state in the domain/installed libvirt
layout; copy their state consistently while stopped and preserve ownership,
mode and ACLs. Do not assume every build uses one identical TPM pathname.
Retain Windows recovery information for encrypted volumes before changing
TPM/firmware/boot measurements. Do not generate a fresh TPM and call it a restore.

## Restore and relocation

Mount/unlock destination storage, restore disk chains and state, then edit the
saved domain's disk sources if paths changed. Validate XML before defining it:

```sh
virt-xml-validate win11-restored.xml domain
virsh -c qemu:///system define win11-restored.xml --validate
```

Avoid duplicate UUID/name conflicts with a still-registered original. Recreate
needed named networks/pools, check file access, and start the restored VM.
Verify the OS, QGA, applications, shared folders and any encrypted volumes.
For a host-driver test, remove GPU hostdev assignments deliberately; a managed
hostdev can detach its device from Linux when the VM starts.

A guest reboot preserves host tmpfs; a **host reboot does not**. The live RAM-to-
media preservation passed image checks and complete-file hashes before copying
was considered safe to use. See [[Verification — Windows VM]].

Sources: [QEMU offline image operations](https://www.qemu.org/docs/master/tools/qemu-img.html),
[libvirt domain state](https://libvirt.org/formatdomain.html).
