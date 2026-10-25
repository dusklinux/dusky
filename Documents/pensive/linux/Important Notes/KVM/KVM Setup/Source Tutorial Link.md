---
title: "Sources and maintenance map"
tags:
  - kvm
  - manual
reviewed: 2026-10-25
---

# Sources and maintenance map

The older sysguides KVM tutorial explains the origin of some notes:
<https://sysguides.com/install-kvm-on-linux>. It is background, not the authority
for this host's package/daemon versions. Local notes can be wrong too; compare
them against installed help, schema, logs, scripts and primary references.

## Primary references by topic

- [libvirt daemon architecture](https://libvirt.org/daemons.html)
- [libvirt authentication](https://libvirt.org/auth.html)
- [libvirt domain XML](https://libvirt.org/formatdomain.html)
- [libvirt network XML](https://libvirt.org/formatnetwork.html)
- [libvirt capabilities](https://libvirt.org/formatcaps.html)
- [QEMU image utility](https://www.qemu.org/docs/master/tools/qemu-img.html)
- [QEMU Hyper-V interfaces](https://www.qemu.org/docs/master/system/i386/hyperv.html)
- [kernel VFIO](https://docs.kernel.org/driver-api/vfio.html)
- [mkinitcpio manual](https://man.archlinux.org/man/mkinitcpio.conf.5)
- [systemd-boot](https://www.freedesktop.org/software/systemd/man/latest/systemd-boot.html)
- [VirtIO-Win documentation](https://virtio-win.github.io/Knowledge-Base/)
- [Looking Glass downloads/docs](https://looking-glass.io/downloads)
- [FreeRDP upstream](https://github.com/FreeRDP/FreeRDP)
- [Windows 11 requirements](https://learn.microsoft.com/en-us/windows/whats-new/windows-11-requirements)

## Script map

The dispatcher is `30_kvm_vm_deploy.py`; Windows logic is in
`windows/deploy_vm.py`, Linux in `linux/deploy_vm.py`. Looking Glass host setup
and configuration are `windows/25_looking_glass.py` and
`windows/60_configure_client_ini.py`. `windows/50_win.py` dispatches management,
RDP, folder sharing and diagnostics. Guest helpers live under `windows/guest/`.
Shared root scripts stage packages, libvirt, storage, networking and VFIO.

For the paired IDD/FreeRDP source review, preserved source trees are under the
persistent live-test artifacts. Source-tree IDD documentation is a reference for
that exact build, not proof every stable release has the same options.

Record versions as observations, not future promises. Verify final ISO package
versions/build features before treating an ordinary rolling update as validated.
