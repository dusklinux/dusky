---
title: "KVM manual — start here"
tags:
  - kvm
  - manual
reviewed: 2026-10-25
---

# KVM manual — start here

This is a manual learning route for an Arch host running Hyprland on Wayland.
Read the explanation, make one change, then run its verification command.
The scripts under `~/user_scripts/dusky_vm/passthrough` are implementations to
compare against, not prerequisites for following the manual instructions.

## The route

1. [[+ MOC KVM|Prepare the Linux host]]: packages, KVM, system libvirt, storage
   and networking. This work is shared by Windows and Linux guests.
2. [[00 Index — VM Creation (Unified)|Choose virtual hardware]]: firmware,
   disk/NIC models, CPU, RAM, console and guest-agent channels.
3. [[+ MOC Windows Installation Through Virt Manager|Install and integrate Windows 11]].
4. Optionally add [[GPU Passthrough — Host Isolation and Return|GPU passthrough]],
   [[Looking Glass]], [[Remote Desktop — FreeRDP on Wayland|RDP]],
   [[OpenSSH Server — Windows|SSH]] or [[09 Shared Folders — VirtIO-FS|a shared folder]].
5. [[08 Backup and Restore — Disks, Firmware and TPM|Back up and recover]], then
   [[Verification — Windows VM|verify the result]].

Linux installation and guest desktop integration have a separate follow-up pass.
The shared host/hardware guides already distinguish the OS requirements.
Existing `gnome-boxes/` and Linux XML pages are older material, not the current
Wayland guest-integration instructions. macOS remains a separate, unreviewed
workflow. Neither is required to complete the Windows route.

## Understand the parts

| Part | Responsibility |
|---|---|
| KVM | Kernel support for hardware-assisted CPU virtualization |
| QEMU | VM hardware model and guest process |
| libvirt | VM definitions, lifecycle, networks, storage and device assignment |
| virt-manager / virt-viewer | Configuration GUI / setup console |
| VirtIO | Efficient virtual disk, network and other devices; guest drivers required |
| QGA | QEMU guest agent: management commands, separate from desktop display |
| SPICE | Setup display and optional clipboard/audio with guest tools |
| VFIO | Give a physical PCI device to a guest; optional |
| Looking Glass | Windows display/input transport; not a network or file share |
| VirtIO-FS | Shared host directory; unrelated to Looking Glass's frame buffer |

`qemu:///system` and `qemu:///session` have separate VM inventories. These guides
use **system** explicitly. The privileged management daemon and the account
running each QEMU process are different concepts.

## Conventions and limits

Examples use VM name `win11`, pool `/var/lib/libvirt/images` and NAT network
`default`. Replace those with the values you chose. PCI addresses, account IDs,
interface names, disk devices and UUIDs must be discovered on your machine.
Run Linux blocks on the host unless marked **Windows Administrator PowerShell**.
Snippets belong inside an existing XML element as stated; do not create duplicate
`<features>`, `<cpu>` or `<devices>` sections.

The planned ISO floor is Linux 7.3+, systemd 262+, Python 3.15+, current
Hyprland/Wayland and Bash 5.3.20+. Final ISO package versions still need checking.
The live checks used kernel 7.3.0-rc7; a release candidate is not certification
of that final floor. See [[Verification — Windows VM]] for observed results,
including the unresolved isolated DMA fault from GPU testing.

Before a host reboot, preserve any needed RAM-backed disk. A guest reboot and a
host reboot are different: guest restart keeps host tmpfs, host restart clears it.
Before changing devices, power the guest **off and start it again**; an in-guest
restart keeps the same QEMU hardware model.
