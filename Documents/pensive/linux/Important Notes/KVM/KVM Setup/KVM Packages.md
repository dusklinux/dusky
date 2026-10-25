---
title: "KVM host packages and offline inputs"
tags:
  - kvm
  - manual
aliases:
  - Hypervisor Packages
reviewed: 2026-10-25
---

# KVM host packages and offline inputs

The shared package list is `05_virtio_iso.py:REPO_PACKAGES`. Understand the roles
before adding optional components. On a connected Arch installation, update the
system consistently before installing from its repositories; do not perform a
partial upgrade with `pacman -Sy` followed by selective upgrades.

```sh
sudo pacman -Syu
sudo pacman -S --needed qemu-desktop libvirt virt-install virt-manager virt-viewer \
  dnsmasq edk2-ovmf swtpm nftables libosinfo pciutils dmidecode \
  virtiofsd acl openssh wl-clipboard python-rich
```

| Component | Why it is here |
|---|---|
| qemu-desktop | Desktop x86 VM device backends; qemu-full is unnecessary unless its additional emulators are wanted |
| libvirt | Management daemons and `virsh` |
| virt-install / virt-manager / virt-viewer | CLI definition generator, GUI editor, setup viewer |
| edk2-ovmf / swtpm | Guest UEFI firmware / emulated TPM |
| dnsmasq / nftables | Libvirt-managed NAT DHCP/DNS / packet filtering |
| virtiofsd / acl | Optional host-folder backend / directory permissions |
| openssh / wl-clipboard | Optional Linux guest management/native Wayland text integration |
| pciutils / dmidecode | Hardware discovery |
| python-rich | Script UI; manual configuration does not depend on it |

`qemu-img` is a real Arch package providing the image utility; it is pulled in
by this QEMU installation. Verify ownership with `pacman -Qo /usr/bin/qemu-img`
instead of treating the package name as obsolete. `osinfo-db` supplies OS data
through the installed dependency set; inspect `virt-install --osinfo list`.

For Windows prepare the Windows ISO, VirtIO-Win ISO, and, if selected, WinFsp,
OpenSSH offline inputs and a **paired** Looking Glass client/IDD installer.
See [[Mount the VirtIO-Win ISO Image]] and [[Looking Glass]]. A stable legacy LG
package name alone does not prove compatibility with the development IDD.

For an offline install, make a consistent package repository/cache and all
selected guest installers available beforehand. `pacman -U /path/to/package…`
installs supplied archives but does not invent missing dependencies. Keep the
prepared package set consistent with the target ISO; the ISO has not shipped,
so its final versions remain to be checked. Source builds additionally need
prepared compiler dependencies and source/submodule inputs.

```sh
pacman -Q qemu-desktop qemu-img libvirt virt-install virt-manager edk2-ovmf swtpm virtiofsd
virsh --version
qemu-system-x86_64 --version
python3 -VV
```

If a temporary custom Python is first in PATH, distribution GUI bindings may
still belong to `/usr/bin/python3`. Launch the packaged GUI with its interpreter:
`/usr/bin/python3 /usr/bin/virt-manager --connect qemu:///system`. The setup
scripts themselves use Python 3.15+. Do not copy a runtime-directory UID or a
Wayland socket name from another login session.

Next: [[libvert Modular daemon enable]]. Reference:
[Arch package database](https://archlinux.org/packages/),
[libvirt daemon architecture](https://libvirt.org/daemons.html).
