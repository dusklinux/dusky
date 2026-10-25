---
title: "Reference XML — Arch (Intel Virtio-GL Accel, Full)"
tags:
  - kvm
  - arch
  - xml
  - reference
---

# Reference XML — Arch (Intel Virtio-GL Accel, Full)

> [!note] Linux guest review pending
> This page retains older Linux/Boxes or example-XML instructions. It can
> conflict with the current Hyprland/Wayland-only baseline, including older
> clipboard/display recipes. Use [[+ MOC KVM]] for the reviewed
> shared host setup; this page is not yet the current Linux guest guide.

> [!info] Scope
> Full domain dump for an Arch guest on Intel iGPU with `virtio`+`virgl` (not VFIO). Mirrors `virt-manager --connect qemu:///system` export then modernized (`ovmf` JSON firmware, `host-passthrough`, `memballoon virtio` retained here as accel labs often keep balloon; passthrough variants use `none`). For passthrough see [[Host PC  Preparation for GPU isolation]].

> [!warning] Original had deep backingStore chain (`vol.177…`) — example below collapses to single `vda` for readability. Your local `vol.*` chain is an artifact of snapshot history; `virsh blockcommit` collapses it if desired.

```xml
<domain type="kvm">
  <name>archlinux</name>
  <uuid>6c5386d1-872e-41d7-bafc-24759542a5e6</uuid>
  <metadata><libosinfo:libosinfo xmlns:libosinfo="http://libosinfo.org/xmlns/libvirt/domain/1.0">
    <libosinfo:os id="http://archlinux.org/archlinux/rolling"/>
  </libosinfo:libosinfo></metadata>
  <memory unit="KiB">6168576</memory>
  <currentMemory unit="KiB">6168576</currentMemory>
  <memoryBacking><source type="memfd"/><access mode="shared"/></memoryBacking>
  <vcpu placement="static">6</vcpu>
  <os firmware="efi">
    <type arch="x86_64" machine="pc-q35-10.1">hvm</type>
    <firmware>
      <feature enabled="no" name="enrolled-keys"/>
      <feature enabled="yes" name="secure-boot"/>
    </firmware>
    <loader readonly="yes" secure="yes" type="pflash" format="raw">/usr/share/edk2/x64/OVMF_CODE.secboot.4m.fd</loader>
    <nvram template="/usr/share/edk2/x64/OVMF_VARS.4m.fd" templateFormat="raw" format="raw">/var/lib/libvirt/qemu/nvram/archlinux_VARS.fd</nvram>
    <boot dev="hd"/>
  </os>
  <features><acpi/><apic/><vmport state="off"/><smm state="on"/></features>
  <cpu mode="host-passthrough" check="none" migratable="on"/>
  <clock offset="utc"><timer name="rtc" tickpolicy="catchup"/><timer name="pit" tickpolicy="delay"/><timer name="hpet" present="no"/></clock>
  <on_poweroff>destroy</on_poweroff><on_reboot>restart</on_reboot><on_crash>destroy</on_crash>
  <pm><suspend-to-mem enabled="no"/><suspend-to-disk enabled="no"/></pm>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type="file" device="disk">
      <driver name="qemu" type="qcow2" cache="none" io="io_uring" discard="unmap"/>
      <source file="/mnt/media/Documents/Virtual Machine/arch_base.qcow2"/>
      <target dev="vda" bus="virtio"/>
      <address type="pci" domain="0x0000" bus="0x04" slot="0x00" function="0x0"/>
    </disk>
    <disk type="file" device="cdrom">
      <driver name="qemu" type="raw"/><target dev="sda" bus="sata"/><readonly/>
      <address type="drive" controller="0" bus="0" target="0" unit="0"/>
    </disk>
    <controller type="usb" index="0" model="qemu-xhci" ports="15"><address type="pci" domain="0x0000" bus="0x02" slot="0x00" function="0x0"/></controller>
    <controller type="pci" index="0" model="pcie-root"/>
    <controller type="pci" index="1" model="pcie-root-port"><model name="pcie-root-port"/><target chassis="1" port="0x10"/><address type="pci" domain="0x0000" bus="0x00" slot="0x02" function="0x0" multifunction="on"/></controller>
    <!-- ports 2–14 as needed (example shortened) -->
    <controller type="sata" index="0"><address type="pci" domain="0x0000" bus="0x00" slot="0x1f" function="0x2"/></controller>
    <controller type="virtio-serial" index="0"><address type="pci" domain="0x0000" bus="0x03" slot="0x00" function="0x0"/></controller>
    <interface type="network"><mac address="52:54:00:48:16:46"/><source network="default"/><model type="virtio"/><address type="pci" domain="0x0000" bus="0x01" slot="0x00" function="0x0"/></interface>
    <serial type="pty"><target type="isa-serial" port="0"><model name="isa-serial"/></target></serial>
    <console type="pty"><target type="serial" port="0"/></console>
    <channel type="unix"><target type="virtio" name="org.qemu.guest_agent.0"/><address type="virtio-serial" controller="0" bus="0" port="1"/></channel>
    <channel type="spicevmc"><target type="virtio" name="com.redhat.spice.0"/><address type="virtio-serial" controller="0" bus="0" port="2"/></channel>
    <input type="tablet" bus="usb"><address type="usb" bus="0" port="1"/></input>
    <input type="mouse" bus="ps2"/><input type="keyboard" bus="ps2"/>
    <graphics type="spice"><listen type="none"/><image compression="off"/><gl enable="yes" rendernode="/dev/dri/renderD128"/></graphics>
    <sound model="ich9"><address type="pci" domain="0x0000" bus="0x00" slot="0x1b" function="0x0"/></sound>
    <audio id="1" type="spice"/>
    <video><model type="virtio" heads="1" primary="yes"><acceleration accel3d="yes"/></model><address type="pci" domain="0x0000" bus="0x00" slot="0x01" function="0x0"/></video>
    <redirdev bus="usb" type="spicevmc"><address type="usb" bus="0" port="2"/></redirdev>
    <redirdev bus="usb" type="spicevmc"><address type="usb" bus="0" port="3"/></redirdev>
    <watchdog model="itco" action="reset"/>
    <memballoon model="virtio"><address type="pci" domain="0x0000" bus="0x05" slot="0x00" function="0x0"/></memballoon>
    <rng model="virtio"><backend model="random">/dev/urandom</backend><address type="pci" domain="0x0000" bus="0x06" slot="0x00" function="0x0"/></rng>
  </devices>
</domain>
```

## Key vs 2024 reference

- **Firmware:** was `OVMF_CODE.secboot.fd`; now JSON-driven `.4m.fd` + `smm` (same).
- **Disk:** `cache=none` + `io=io_uring` default (was `threads`); `discard=unmap`.
- **Network:** `bridge` virbr0 → `network default` (libvirt-managed).
- **CPU:** now `host-passthrough` + `smm` (original had no hyperv enlightenments for Linux guest, correct).

Validate: `virt-xml-validate arch.xml && virsh -c qemu:///system define arch.xml`

See sibling: [[arch linux GPU ACCELERATION intel integrated xml]] (snippets), [[+ MOC KVM]].
