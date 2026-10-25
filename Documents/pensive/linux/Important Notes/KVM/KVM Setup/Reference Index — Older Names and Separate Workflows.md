---
title: "KVM reference index — older names and separate workflows"
tags:
  - kvm
  - manual
reviewed: 2026-10-25
---

# KVM reference index — older names and separate workflows

[[+ MOC KVM|KVM home]]

Use [[+ MOC KVM]] for installation order. This index makes supporting notes and
older bookmarks discoverable without turning them into extra required steps.
Many old filenames now point to a single maintained procedure; their continued
existence does not mean there are two competing setup methods. Display text
below uses each page's descriptive title, while the original filename remains
available in Obsidian's file explorer and link preview.

Linux/Boxes guest guides and macOS notes are listed separately because their
contents have not received the Windows/shared technical review. Their status
callouts take precedence over older dates or claims in the retained text. Read
them as reference material, not as a continuation of the Windows checklist.

## Host references and earlier entry points

- [[Source Tutorial Link|Sources and maintenance map]]
- [[All Libvert Daemons|Libvirt daemon roles]]
- [[Give the User System-Wide Permission_old|Older libvirt permission recipe — replaced]]
- [[KVM Group Add|KVM group membership]]
- [[KVM Prepare dGPU passthrough|GPU passthrough — shared procedure]]
- [[KVM Services|KVM services — current entry point]]
- [[check libvert modular daemon availability|Check installed modular libvirt units]]

## Windows supporting notes and earlier names

- [[Conclusion Win|Windows VM completion checklist]]
- [[Configure Chipset and Firmware|Windows chipset and firmware — shared procedure]]
- [[Configure Default Virtual Hardware Using the Wizard|Windows wizard — shared procedure]]
- [[Configure Windows Virtual Hardware|Windows hardware editor — entry point]]
- [[Configure the Storage|Windows disk controller and driver choice]]
- [[Enable CPU Host-Passthrough|Windows CPU setup — shared procedure]]
- [[Enable Hyper-V Enlightenments|Configure Windows enlightenments — canonical reference]]
- [[Hypervisor Features|Hypervisor features — Windows scope]]
- [[Remove the USB Tablet Device|Tablet input — optional, keep recovery usable]]
- [[windows 10 virt xml file|Older Windows XML reference — replaced]]

## GPU/display supporting notes and earlier names

- [[Host PC  Preparation for GPU isolation|GPU host preparation — shared guide]]
- [[+ MOC SPEEDRUN Windows GPU PASSTHROUGH|GPU passthrough — compact navigation]]
- [[+ MOC Windows GPU Passthrough|Windows GPU passthrough — ordered extension]]
- [[GPU vfio_pci isolation windows|VFIO isolation — shared guide]]
- [[KVM preperation and Optimization|Windows passthrough host prerequisites]]
- [[Looking Glass_old|Older Looking Glass configuration — replaced]]
- [[Network Bridging for LAN access|LAN networking — shared host procedure]]
- [[The RDP method to disable display driver|Older RDP display-disable workaround — replaced]]
- [[VirtIO win driver iso|VirtIO-Win driver media — canonical guide]]
- [[Virt_Manager Packages for Windows|Windows VM host packages — shared list]]
- [[Windows Configurations for Passthrough|Windows integration — choose the components you need]]

## Other Windows front ends

- [[Windows|GNOME Boxes Windows — separate session workflow]]

## Linux guest material — review pending

- [[SSHing into vm|SSH into QEMU/KVM Guests — Arch (Boxes vs System, Aug 2026)]]
- [[gnome-boxes|GNOME Boxes — `virt-manager` SPICE Clipboard (Arch + Hyprland/UWSM)]]
- [[linux|GNOME Boxes — Arch Guest Agents (QXL/SPICE)]]
- [[arch GPU ACCELERATION reference xml|Reference XML — Arch (Intel Virtio-GL Accel, Full)]]
- [[arch linux GPU ACCELERATION intel integrated xml|Snippet — Intel iGPU Guest Accel (Virtio + SPICE GL)]]
- [[arch linux zram 20GB xml|Reference XML — Arch Linux (Ephemeral, 6 GiB)]]

## macOS — separate, unreviewed workflow

- [[+ MOC macOS|MOC — macOS on KVM (OSX-KVM, Arch)]]
- [[all notes macos|macOS KVM — All Notes]]
- [[setting up networking macos|macOS — QEMU Networking]]

## Previous start page

- [[00 Start Here — KVM Manual|KVM manual — entry point moved]]
