---
title: "KVM — manual setup and learning route"
tags:
  - kvm
  - manual
aliases:
  - KVM Master Checklist
reviewed: 2026-10-25
---

# KVM — manual setup and learning route

This is a manual learning route for an Arch host running Hyprland on Wayland.
Read the explanation, make one change, then run its verification command.
The scripts under `~/user_scripts/dusky_vm/passthrough` are implementations to
compare against, not prerequisites for following the manual instructions.

## The route

1. Follow [[#Host preparation|Host preparation]] below: packages, KVM, system libvirt, storage
   and networking. This work is shared by Windows and Linux guests.
2. [[00 Index — VM Creation (Unified)|Choose virtual hardware]]: firmware,
   disk/NIC models, CPU, RAM, console and guest-agent channels.
3. [[+ MOC Windows Installation Through Virt Manager|Install and integrate Windows 11]].
4. Optionally add [[+ MOC Windows GPU Passthrough|GPU passthrough]],
   [[Looking Glass]], [[Remote Desktop — FreeRDP on Wayland|RDP]],
   [[OpenSSH Server — Windows|SSH]] or [[09 Shared Folders — VirtIO-FS|a shared folder]].
5. [[Verification — Windows VM|Verify the selected features]], then retain a
   [[08 Backup and Restore — Disks, Firmware and TPM|consistent backup]]. Before
   changing an existing VM, make that backup first.

Linux installation and guest desktop integration have a separate follow-up pass.
The shared host/hardware guides already distinguish the OS requirements.
Existing `gnome-boxes/` and Linux XML pages are older material, not the current
Wayland guest-integration instructions. macOS remains a separate, unreviewed
workflow. Neither is required to complete the Windows route.

## How to use these notes in Obsidian

The numbered route is the installation order. Read each page's explanation,
choose the applicable branch, apply that change, then verify its result. Optional
features are independent choices; skip their configuration when not selected.
The short navigation line under each title returns to this MOC and the relevant
section. **Continue** links follow the main route; **Related** links explain a
concept without adding a required installation step.

Use Obsidian's Backlinks panel to see which notes reference the current page;
these incoming links are generated from the actual links, not a second list to
maintain by hand. The Outline panel navigates long guides. Heading links open
the relevant section directly. Older filenames remain valid, while their page
titles and links identify the current guide. No community plugin is required.
See [[Reference Index — Older Names and Separate Workflows]] for those older
entry points and the clearly separated Linux/macOS material. Sources and script
locations are in [[Source Tutorial Link|Sources and maintenance map]].

Obsidian reference: [internal and heading links](https://help.obsidian.md/links),
[Backlinks](https://help.obsidian.md/plugins/backlinks).

## How a change reaches the guest

Keep four layers separate when learning or troubleshooting:

| Layer | What changes here | What to check |
|---|---|---|
| Host resources | Mounted disks, permissions, networks, isolated PCI devices | The resource exists and is accessible before VM start |
| Persistent VM definition | The hardware libvirt should create on its next start | Inactive XML and the chosen device/controller model |
| Running VM | The QEMU process and hardware currently presented to Windows | Running XML, guest device enumeration and driver health |
| Guest/session | Drivers, services, signed-in user, active monitor and applications | The specific service and real desktop/file/workload behavior |

A Windows restart stays inside the existing QEMU process; it does not recreate
hardware from an edited persistent definition. These guides use a full graceful
shutdown followed by a VM start for hardware changes. Some devices support live
updates, but each needs its own documented procedure. A host reboot is a third
operation: it also resets host drivers and clears RAM-backed filesystems.

For a running persistent VM, `virsh -c qemu:///system dumpxml win11` shows the
running definition; adding `--inactive` shows the next-start definition. They
can differ. A newly added channel still needs its guest driver/service, and a
running service still does not prove a usable signed-in desktop. Diagnose the
first failed layer rather than repeatedly reinstalling unrelated components.

## Choose the options before creating the guest

| Decision | Choices to make |
|---|---|
| Disk location | Persistent storage, or a deliberate RAM-backed experiment |
| Network topology | NAT for host access/internet, or a wired bridge for LAN visibility |
| Disk/NIC devices | VirtIO with prepared Windows drivers, or SATA/e1000e inbox devices |
| Resources | Custom RAM/vCPUs that fit the host; Windows script defaults to fixed 8 GiB |
| Ballooning | Optional, off by default; evaluate separately from configured capacity |
| Desktop access | Setup console, Looking Glass, RDP, or selected combinations |
| Physical GPU | Optional VFIO assignment; keep a host display and guest recovery path |
| File/command access | Optional VirtIO-FS folder and/or Windows SSH server |

Pick the Windows edition accordingly: Home can use the local console/Looking
Glass path but cannot host the supported Remote Desktop service described here.
Prepare the installers for the choices you make before going offline.

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

Examples use VM name `win11`, storage directory `/var/lib/libvirt/images` and
NAT network `default`. A pool name is separate from its directory path. Replace
examples with the values you chose. PCI addresses, account IDs,
interface names, disk devices and UUIDs must be discovered on your machine.
Run Linux shell blocks on the host. PowerShell blocks run inside Windows, with
Administrator access where stated; current-user preferences use the intended
Windows account.
Snippets belong inside an existing XML element as stated; do not create duplicate
`<features>`, `<cpu>` or `<devices>` sections.

Replace uppercase placeholders before running a command. Stop on an error and
resolve it before continuing; a command accepting an option is not proof the
requested change succeeded. Code blocks marked `xml`, `ini`, `javascript` or
`fstab` are file contents, not commands to paste into a terminal.

The planned ISO floor is Linux 7.3+, systemd 262+, Python 3.15+, current
Hyprland/Wayland and Bash 5.3.20+. Final ISO package versions still need checking.
The live checks used kernel 7.3.0-rc7; a release candidate is not certification
of that final floor. See [[Verification — Windows VM]] for observed results,
including the unresolved isolated DMA fault from GPU testing.

Before a host reboot, preserve any needed RAM-backed disk. A guest reboot and a
host reboot are different: guest restart keeps host tmpfs, host restart clears it.
Before changing devices, power the guest **off and start it again**; an in-guest
restart keeps the same QEMU hardware model.

## Host preparation

1. [[Verify VT-x and Kernel Modules and IOMMU|Check firmware and KVM]]. Enable
   CPU virtualization. On a fresh host, install the next step’s tools before
   running the command-line validation. Add IOMMU checks only for PCI assignment.
2. [[KVM Packages|Install or stage the package set]] and installation media.
3. [[libvert Modular daemon enable|Configure modular libvirt sockets]].
4. [[Give the User System-Wide Permission|Verify regular-user system access]].
5. [[Symbolic link to zram for image file|Choose storage]] and
   [[Set ACL on the Image Directory|verify directory access]]. Daemon setup now
   precedes storage in the Windows profile so QEMU's real default account can
   be discovered from libvirt, rather than guessed from commented examples.
6. [[Activating Network and Setting it to Autostart|Start the NAT network]], or
   use [[Host Networking — NAT and Wired Bridge|a wired bridge]] if LAN visibility
   is needed. NAT works on ordinary Wi-Fi uplinks.
7. [[00 Index — VM Creation (Unified)|Create the guest]]. For Windows continue
   with [[+ MOC Windows Installation Through Virt Manager]].

Optional branches: [[GPU Passthrough — Host Isolation and Return|VFIO]],
[[Optimize the Host with TuneD|host tuning]],
[[08 Backup and Restore — Disks, Firmware and TPM|backup]].

## Script equivalents

From `~/user_scripts/dusky_vm/passthrough`:

```sh
python3 000_dusky_vm.py --windows --dry-run
python3 000_dusky_vm.py --windows --reset-and-run --setup-only --manual
python3 000_dusky_vm.py --windows --deploy-only
```

Reset receipts for a genuinely fresh host, especially after restoring an old
home directory. For an interrupted setup on the same host, resume without
resetting. Host setup and deployment are separate so rerunning setup need not
create a second VM. The Windows profile offers GPU isolation, Looking Glass and
RDP as choices; none is required for a plain setup console.

The Linux profile shares package/daemon/network preparation but has its own
`linux/deploy_vm.py` and optional `linux/guest_integration.py`. It is not the
Windows interview with a different OS name. Do not install Windows drivers or
copy Windows Hyper-V settings into Linux by rote.

## Final host checks

```sh
virt-host-validate qemu
virsh -c qemu:///system version
virsh -c qemu:///system list --all
virsh -c qemu:///system net-list --all
virsh -c qemu:///system pool-list --all
```

Read each validation result. An IOMMU warning matters for passthrough, not an
ordinary virtual disk/NIC guest. A listening socket can have an inactive daemon
until first use; an active daemon while clients or VMs exist is also normal.
