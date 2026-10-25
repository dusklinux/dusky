---
title: "MOC — macOS on KVM (OSX-KVM, Arch)"
tags:
  - kvm
  - macos
  - arch
  - qemu
---

# MOC — macOS on KVM (OSX-KVM, Arch)

> [!note] Separate workflow — not reviewed in the Windows pass
> This page retains older macOS-specific instructions. Its packages, firmware,
> device examples and commands have not been validated by the Windows/shared
> review. Start at [[+ MOC KVM]] for the reviewed scope.

> [!info] Upstream
> **OSX-KVM** <https://github.com/kholia/OSX-KVM> — QEMU `x86_64-softmmu` + OpenCore. Arch-specific tweaks below (Arch = `mkinitcpio`/`systemd-boot`, not `apt`/`update-grub`).

## Host deps (Arch)

```bash
sudo pacman -S --needed qemu-desktop libvirt virt-manager virt-viewer git wget guestfs-tools p7zip make tesseract tesseract-data-eng cdrkit vim net-tools screen cdrtools
paru -S --needed dmg2img uml_utilities
```

## Checkout

```bash
cd ~
git clone --depth 1 --recursive https://github.com/kholia/OSX-KVM.git
cd OSX-KVM
```

## KVM tunables + groups

```bash
sudo modprobe kvm; echo 1 | sudo tee /sys/module/kvm/parameters/ignore_msrs
lscpu | grep -E 'VT-x|AMD-V'   # confirm
sudo cp kvm.conf /etc/modprobe.d/kvm.conf   # if repo ships one (e.g. kvm.ignore_msrs=1)
sudo usermod -aG kvm,libvirt,input "$(whoami)"   # re-login
systemctl reboot
```

## Fetch macOS

```bash
./fetch-macOS-v2.py   # pick version → BaseSystem.dmg
# note: Big Sur+ stalls at Country screen — wait, it recovers
dmg2img -i BaseSystem.dmg BaseSystem.img
qemu-img create -f qcow2 mac_hdd_ng.img 256G   # put on fast SSD/NVMe (host FS with ACLs, ext4/xfs/btrfs)
```

## Install (CLI)

```bash
./OpenCore-Boot.sh   # same script for all modern macOS
# inside installer: Disk Utility → partition + APFS → Install macOS
```

## Optional libvirt import

```bash
sed "s/CHANGEME/$USER/g" macOS-libvirt-Catalina.xml > macOS.xml
virt-xml-validate macOS.xml
virsh --connect qemu:///system define macOS.xml
# perms:
sudo setfacl -m u:libvirt-qemu:rx /home/$USER
sudo setfacl -R -m u:libvirt-qemu:rx /home/$USER/OSX-KVM
# start: virt-manager --connect qemu:///system → macOS
```

## Post-install

- **Networking:** [[setting up networking macos]]
- **Resolution:** [[all notes macos]] → `vmware-svga`, `displayplacer`, OpenCore `config.plist:Resolution`, OVMF menu
- **iMessage:** [[all notes macos]] + <https://dortania.github.io/OpenCore-Post-Install/universal/iservices.html>

See also: [[all notes macos]] (GPU/USB passthrough Arch adaptations).
