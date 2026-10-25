---
title: "VirtIO-Win ISO — prepare and attach driver media"
tags:
  - kvm
  - manual
  - windows
reviewed: 2026-10-25
---

# VirtIO-Win ISO — prepare and attach driver media

[[+ MOC KVM|KVM home]] · [[+ MOC Windows Installation Through Virt Manager|Windows route]]

Windows needs drivers for the selected VirtIO devices. The disk bus determines
whether it needs viostor or vioscsi; NetKVM handles VirtIO networking. Guest tools
also provide management/integration components. SATA/e1000e can use inbox
Windows drivers, but do not supply QGA/clipboard services by themselves.

Obtain VirtIO-Win media from the [project's distribution instructions](https://github.com/virtio-win/virtio-win-pkg-scripts/blob/master/README.md).
Stage it on persistent media for an offline reinstall. If the AUR package is
already installed, discover its actual ISO location:

```sh
pacman -Qlq virtio-win
```

`05_virtio_iso.py` can reuse a staged ISO/package or ask for a local path/download.
Do not treat a printed local SHA-256 as authentication against an independent
publisher checksum, or a minimum-size check as proof the complete ISO is good.
Do not write a new download over a mounted/in-use ISO.

With the VM powered off, virt-manager → **Add Hardware → Storage → CD-ROM**:
select the ISO and use SATA. Keep the OS installer and driver ISO on separate
CD-ROM devices. Windows drive letters are assigned by the guest; do not assume
its VirtIO DVD is always E:.

```sh
virsh -c qemu:///system domblklist win11 --inactive --details
```

Browse the DVD in Windows or Windows Setup. Confirm the required driver paths
exist for that media version and guest architecture. Keep the media available
until installation/guest tools are complete, then eject/remove media deliberately.
An unavailable ISO path can prevent a later VM start just like a missing disk.

Continue with [[Install a Windows Virtual Machine on KVM]].
