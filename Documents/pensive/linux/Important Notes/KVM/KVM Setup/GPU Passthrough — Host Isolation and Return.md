---
title: "GPU passthrough — host isolation and return"
tags:
  - kvm
  - manual
reviewed: 2026-10-25
---

# GPU passthrough — host isolation and return

VFIO gives a physical PCI device to QEMU instead of its ordinary Linux driver.
It is optional: a setup console, RDP and the current Looking Glass IDD's software
path can work without a passed-through GPU. The procedure below is a **boot-time
transition**, not a universal live GPU detach/reset procedure.

## 1. Plan a host display and preserve the VM

Keep a usable host GPU/display path. A laptop's internal panel, MUX mode and
external outputs may be wired differently; “every laptop should use Optimus” is
not a hardware-independent rule. Check the actual display connections before
isolating a boot/only GPU. Preserve RAM-backed VM data before any host reboot:
[[08 Backup and Restore — Disks, Firmware and TPM]].

Check firmware/IOMMU using [[Verify VT-x and Kernel Modules and IOMMU]]. Then:

```sh
lspci -nnk
```

Record the selected GPU's full domain:bus:slot.function address and **all** its
slot functions, including audio/USB/UCSI where present. Inspect every endpoint
in the IOMMU group. Do not pass unrelated host storage/network endpoints merely
to satisfy a crowded group.

`vendor:device` IDs are not unique PCI addresses. If another retained device has
the same IDs, the simple ID-based recipe below can claim it too. Stop and design
per-device early binding rather than blindly adding duplicate IDs. The supplied
toggle rejects that ambiguity; it does not invent an ACS override.

## 2. Boot parameters and early loading

Follow [[Grub Kernal Parameters|the systemd-boot command-line guide]], preserving
all root/encryption/storage options. Make a backup of the effective initramfs
configuration and selected boot entry. Use your existing mkinitcpio hook chain;
**do not replace it** with a short example that omits your disk-unlock hooks.

A manual drop-in can prepend the required PCI driver while preserving modules:

```bash
# /etc/mkinitcpio.conf.d/90-manual-vfio.conf
MODULES=(vfio_pci "${MODULES[@]}")
```

Ensure effective HOOKS include `modconf` before `kms`. Preserve all other required
hooks and inspect later drop-ins for overrides. Dependencies load through the
installed module stack; do not copy obsolete `vfio_virqfd` or unconditionally
force a legacy IOMMU backend into the module list.

Save `/etc/modprobe.d/manual-vfio.conf` with the IDs you actually discovered:

```ini
# Replace these placeholders; they are NOT literal configuration values:
options vfio-pci ids=GPU_VENDOR:GPU_DEVICE,AUDIO_VENDOR:AUDIO_DEVICE
```

Add the other selected functions if present. For a host driver that could race
VFIO, add its actual soft dependency, e.g. `softdep amdgpu pre: vfio-pci` when
amdgpu is relevant. Blacklist a vendor's graphics drivers only if **no retained
host GPU needs them**. Do not blacklist shared audio/USB infrastructure such as
snd_hda_intel or xhci_pci. A same-vendor iGPU/dGPU setup often needs the host
vendor driver to remain available.

Manual files above are distinct from script-managed `arsonix-vfio.conf` files.
Choose one owner for the configuration; do not stack both recipes and assume
all scripts will adopt arbitrary foreign files.

## 3. Build, inspect, then reboot

```sh
sudo mkinitcpio -P
bootctl list --json=short
# Inspect each actual preset image, replacing the pathname:
lsinitcpio --nocolor --list /path/to/actual-initramfs.img
```

Require a successful rebuild and verify the chosen image includes vfio_pci and
the intended modprobe file. Check the selected entry/UKI uses that image and the
intended options. A build failure is not permission to reboot into a partial
configuration. Preserve the known-good entry/image for recovery.

After a planned host reboot, inspect `lspci -nnk`: every selected function must
use vfio-pci, while the host display still uses its native driver. Check kernel
logs for IOMMU, DMA and reset faults. Early boot status can precede a native
video/audio driver loading; recheck after initialization before diagnosing a
persistent failure.

## 4. Attach the isolated GPU

With Windows powered off, add the selected slot's PCI functions in virt-manager.
Keep the emulated setup display and QGA/SPICE channels. A managed hostdev lets
libvirt manage its host binding during VM start/stop, but does not guarantee
reset support or safe hot-detach of a GPU used by the host desktop.

Install the current vendor driver appropriate to that exact guest GPU and OS.
Do not pin the test machine's NVIDIA driver version into every future install.
Check guest PnP health, actual rendering, repeated clean shutdown/start reuse,
visible Looking Glass output and kernel logs: [[Verification — Windows VM]].

## 5. Return to Linux

Shut the guest down. If that VM should run without the physical GPU, remove its
PCI hostdev assignments; otherwise a later start can detach the GPU again.
Undo only the manual files/options you introduced, retaining unrelated claims
and host settings. Remove the matching VFIO IDs/graphics blacklists, restore
native module availability, rebuild/verify the effective images and reboot.
Then check both video and audio functions' native drivers.

## Scripted equivalent and interrupted operations

```sh
cd ~/user_scripts/dusky_vm/passthrough
python3 nvidia_vfio_bind_unbind.py --bind --dry-run
python3 nvidia_vfio_bind_unbind.py --bind
# After the authorized host reboot:
sudo python3 nvidia_vfio_bind_unbind.py --status
# To stage the reverse transition:
python3 nvidia_vfio_bind_unbind.py --unbind
```

The historical filename now supports discovered Intel/AMD/NVIDIA slots. It
stages boot changes, never automatically reboots or live-unbinds the GPU, and
keeps `/var/lib/arsonix/vfio-toggle.json` for recovery. Rerun the same action
after a failed write/build; a converged run avoids rewrites/rebuilds. Do not mix
active GPU-disable configuration with VFIO. Read its `--help` and `VFIO_TOGGLE.md`.
Status proves configuration/binding, not fault-free rendering or reset behavior.

If the host display is unavailable: [[gpu-passthrough-boot-fix-walkthrough]].
Sources: [kernel VFIO](https://docs.kernel.org/driver-api/vfio.html),
[libvirt PCI host devices](https://libvirt.org/formatdomain.html#host-device-assignment).
