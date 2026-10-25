---
title: "macOS KVM — All Notes (Sonoma, Resolution, Passthrough)"
tags:
  - kvm
  - macos
  - vfio
  - arch
---

# macOS KVM — All Notes

[[+ MOC KVM|KVM home]] · [[Reference Index — Older Names and Separate Workflows|Separate workflows]]

> [!note] Separate workflow — not reviewed in the Windows pass
> This page retains older macOS-specific instructions. Its packages, firmware,
> device examples and commands have not been validated by the Windows/shared
> review. Start at [[+ MOC KVM]] for the reviewed scope.

> [!info] Base repo: **OSX-KVM** (<https://github.com/kholia/OSX-KVM>) + Dortania. Notes collected here are **host-side Arch** adaptations (Arch uses `pacman`/`systemd-boot`/`mkinitcpio`, not `apt`/`update-grub`).

## Sonoma support

- Change CPU model `Penryn` → `Haswell-noTSX` in `OpenCore-Boot.sh`. Works also on AMD `Ryzen 9 5900HS`.

## App Store — “device could not be verified”

- `en0` must be the wired Ethernet. `ifconfig | grep en0`
- If not `en0`: System Preferences → Network → delete all devices → Apply → host terminal (or guest?) — original fix:

```bash
sudo rm /Library/Preferences/SystemConfiguration/NetworkInterfaces.plist  # inside macOS guest
reboot
# inside macOS: retry App Store
```

Via `Glnk2012` (tonymacx86). Also tweak `smbios.plist` + validate via <https://dortania.github.io/OpenCore-Post-Install/universal/iservices.html>.

## Resolution — Ventura black-screen after picking option

> Works → switch to `vmware-svga`.
> Broken: macOS Ventura Settings shows 3 options; picking any gives black with sliver.

Blind reset (inside guest Terminal):

```bash
sudo rm /Library/Preferences/com.apple.windowserver.plist
rm ~/Library/Preferences/ByHost/com.apple.windowserver*
sudo reboot
```

`displayplacer "id:FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF mode:10"` not helpful here.

### OpenCore resolution

```diff
--- a/OpenCore/config.plist
+++ b/OpenCore/config.plist
@@ -692,7 +692,7 @@
                         <key>Resolution</key>
-                       <string>Max</string>
+                       <string>1920x1080</string>
```

- Set **OVMF** resolution to match (ESC at OVMF logo → Device Manager → OVMF Platform Configuration → Change Preferred Resolution for Next Boot → Commit) — default 1024×768.
- After `config.plist` edit: regenerate `OpenCore.qcow2` per `OSX-KVM/OpenCore/README.md`.

## GPU passthrough — AMD example (Apr 2024, Ubuntu 22.04 + i5-6500 + RX 6600)

> [!warning] Arch adaptation — translate `apt` → `pacman` / GRUB → systemd-boot

Original (Ubuntu) — keep as reference then Arch-map:

```bash
cat /etc/modprobe.d/blacklist.conf
blacklist amdgpu
blacklist radeon

lspci -nnk | grep AMD
# 01:00.0 [1002:67df] VGA  01:00.1 [1002:aaf0] Audio
```

**Ubuntu**

```ini
# /etc/default/grub → GRUB_CMDLINE_LINUX_DEFAULT
iommu=pt intel_iommu=on vfio-pci.ids=1002:67df,1002:aaf0 kvm.ignore_msrs=1 video=vesafb:off,efifb:off
```

**Arch (systemd-boot) equivalent — what you actually do on this host**

```bash
sudo nvim /boot/loader/entries/arch.conf   # Type1  OR  /etc/kernel/cmdline (Type2)
# append to options:
intel_iommu=on iommu=pt vfio-pci.ids=1002:67df,1002:aaf0 kvm.ignore_msrs=1
# AMD host: iommu=pt only (+ force_enable if broken IVRS)

sudo nvim /etc/modprobe.d/vfio.conf
options vfio-pci ids=1002:67df,1002:aaf0 disable_vga=1
softdep radeon pre: vfio-pci
softdep amdgpu pre: vfio-pci
softdep nouveau pre: vfio-pci
softdep drm pre: vfio-pci

sudo nvim /etc/mkinitcpio.conf  # or /etc/mkinitcpio.conf.d/99-vfio.conf
MODULES=(… vfio_pci vfio vfio_iommu_type1)
HOOKS=(… modconf kms …)   # modconf before kms
```

Ubuntu rebuild:

```bash
sudo update-grub2 && sudo update-initramfs -k all -u
```

Arch rebuild:

```bash
sudo mkinitcpio -P && sudo reboot
# verify:
sudo dmesg | grep -i iommu
sudo dmesg | grep vfio
lspci -nkk -d 1002:67df   # Kernel driver in use: vfio-pci
```

- BIOS: **Primary Display → IGFX** (iGPU) — prerequisite for Intel host isolation.
- Arch Wiki: <https://wiki.archlinux.org/title/PCI_passthrough_via_OVMF>

Expected:

```
DMAR: IOMMU enabled
iommu: Default domain type: Passthrough (set via kernel command line)
vfio-pci … vgaarb: changed VGA decodes
vfio_pci: add [1002:67df[ffffffff:ffffffff]]
```

Fix perms if using direct QEMU without libvirt:

```bash
sudo cp vfio-kvm.rules /etc/udev/rules.d/vfio-kvm.rules
sudo udevadm control --reload && sudo udevadm trigger
```

Memlock (legacy, for VFIO directly via QEMU):

```
# /etc/security/limits.conf
@kvm     soft memlock unlimited
@kvm     hard memlock unlimited
@libvirt soft memlock unlimited
@libvirt hard memlock unlimited
```

- See `boot-passthrough.sh` + `scripts/list_iommu_groups.sh` in OSX-KVM.

## USB passthrough (PCIe USB controller)

```bash
lspci -nnk | grep -i usb   # e.g. 1b21:1242 ASMedia ASM1142
# add 1b21:1242 to vfio-pci ids= list + rebuild initramfs/reboot
scripts/lsgroup.sh         # find group
scripts/vfio-group.sh 13
# boot script: -device vfio-pci,host=03:00.0,bus=pcie.0
```

## Other peripherals & tweaks

- **Sound:** <https://github.com/chris1111/VoodooHDA-OC> (10.12–11.2, not with AppleALC); prefer HDA/USB passthrough or HDMI.
- **USB sound:** cheap QHM623 class works without controller passthrough.
- **Build QEMU:** <http://wiki.qemu-project.org/Hosts/Linux>, `--enable-trace-backend=simple --target-list=x86_64-softmmu,aarch64-softmmu --audio-drv-list=pa`
- **iDevice passthrough:** USB OTA <https://github.com/corellium/usbfluxd> / <https://github.com/Silfalion/Iphone_docker_osx_passthrough>
- **AES-NI:** `-cpu Penryn,kvm=off,vendor=GenuineIntel,+aes` → guest `sysctl -a | grep machdep.cpu.features`
- **AVX/AVX2:** `boot-clover.sh` `MY_OPTIONS` already exposes; add `+avx2` etc.
- **Hypervisor.framework / nested:** `sysctl kern.hv_support` → 1; ensure `kvm_intel` loaded + `+vmx,rdtscp` or `+vmx` with `Skylake-Client`
- **virtio-blk:** Mojave+ → `-device virtio-blk-pci,drive=MacHDD` (vs `ide-hd`)
- **ACLs libvirt/qemu:** `sudo setfacl -m u:libvirt-qemu:rx /home/$USER; sudo setfacl -R -m u:libvirt-qemu:rx /home/$USER/OSX-KVM`
- **Dracut vs Arch:** Arch uses `mkinitcpio`, not `dracut`/`update-initramfs`.
- **.pkg extracts:** `7z x example.pkg; gunzip -c …/Payload | cpio -i` (xar unmaintained)
- **gtk init failed:** `display=none` (already in `boot-passthrough.sh`)
- **ISO not detected:** `ScanPolicy=0` in OpenCore `config.plist` (<https://dortania.github.io/OpenCore-Install-Guide/troubleshooting/troubleshooting.html#can-t-see-macos-partitions>)
- **Physical disk passthrough:** `ls -la /dev/disk/by-id/ → -drive id=NVMe…,file=/dev/disk/by-id/… → -device ide-hd,bus=sata.4,drive=NVMe…` (or pass NVMe controller via VFIO)
- **Autostart:** `REPO_PATH` → `/etc/rc.local` (systemd `rc-local.service` on Arch)
- **SSH:** `sudo pacman -S openssh` vs `apt install openssh-server`; `systemctl enable --now sshd.socket`
- **AMD GPU reset bug:** <https://www.nicksherlock.com/2020/11/working-around-the-amd-gpu-reset-bug-on-proxmox/>

## Related snippets

- **TuneD vs TLP**, **ACLs**, **VT-x** in [[+ MOC KVM]]
- Networking helper: [[setting up networking macos]]
