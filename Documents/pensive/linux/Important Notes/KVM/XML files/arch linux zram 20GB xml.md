---
title: "Reference XML — Arch Linux (ZRAM-Ephemeral, 6 GiB)"
tags:
  - kvm
  - arch
  - xml
  - reference
---

# Reference XML — Arch Linux (Ephemeral, 6 GiB)

> [!note] Linux guest review pending
> This page retains older Linux/Boxes or example-XML instructions. It can
> conflict with the current Hyprland/Wayland-only baseline, including older
> clipboard/display recipes. Use [[+ MOC KVM]] for the reviewed
> shared host setup; this page is not yet the current Linux guest guide.

> [!danger] Ephemeral — contents die on reboot
> Image at **`/mnt/zram1/archlinux.qcow2`** (RAM-backed per [[Symbolic link to zram for image file]]). For persistent Arch use `/var/lib/libvirt/images`. This XML is a throwaway-lab example (Arch rolling).

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
  <!-- optional pinning: see 35_cpu_pinning_generator.py -->
  <os firmware="efi">
    <type arch="x86_64" machine="q35">hvm</type>
    <firmware>
      <feature enabled="no" name="enrolled-keys"/>
      <feature enabled="yes" name="secure-boot"/>
    </firmware>
    <loader readonly="yes" secure="yes" type="pflash" format="raw">/usr/share/edk2/x64/OVMF_CODE.secboot.4m.fd</loader>
    <nvram template="/usr/share/edk2/x64/OVMF_VARS.4m.fd" templateFormat="raw" format="raw">/var/lib/libvirt/qemu/nvram/archlinux_VARS.fd</nvram>
    <boot dev="hd"/>
  </os>
  <features><acpi/><apic/><vmport state="off"/><smm state="on"/></features>
  <cpu mode="host-passthrough" check="none" migratable="off"/>
  <clock offset="utc"><timer name="rtc" tickpolicy="catchup"/><timer name="pit" tickpolicy="delay"/><timer name="hpet" present="no"/></clock>
  <pm><suspend-to-mem enabled="no"/><suspend-to-disk enabled="no"/></pm>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type="file" device="disk">
      <driver name="qemu" type="qcow2" cache="none" io="io_uring" discard="unmap"/>
      <source file="/mnt/zram1/archlinux.qcow2"/>
      <target dev="vda" bus="virtio"/>
      <address type="pci" domain="0x0000" bus="0x04" slot="0x00" function="0x0"/>
    </disk>
    <disk type="file" device="cdrom">
      <driver name="qemu" type="raw"/>
      <source file="/mnt/zram1/archlinux.iso"/>
      <target dev="sda" bus="sata"/><readonly/>
      <address type="drive" controller="0" bus="0" target="0" unit="0"/>
    </disk>
    <controller type="usb" model="qemu-xhci" ports="15"/>
    <controller type="pci" model="pcie-root"/><controller type="pci" model="pcie-root-port"/><controller type="pci" model="pcie-root-port"/><controller type="pci" model="pcie-root-port"/><controller type="pci" model="pcie-root-port"/><controller type="pci" model="pcie-root-port"/><controller type="pci" model="pcie-root-port"/><controller type="pci" model="pcie-root-port"/><controller type="pci" model="pcie-root-port"/><controller type="pci" model="pcie-root-port"/><controller type="pci" model="pcie-root-port"/><controller type="pci" model="pcie-root-port"/><controller type="pci" model="pcie-root-port"/><controller type="pci" model="pcie-root-port"/>
    <interface type="network"><source network="default"/><model type="virtio"/></interface>
    <channel type="unix"><source mode="bind"/><target type="virtio" name="org.qemu.guest_agent.0"/></channel>
    <channel type="spicevmc"><target type="virtio" name="com.redhat.spice.0"/></channel>
    <input type="tablet" bus="usb"/>
    <graphics type="spice" port="-1" tlsPort="-1" autoport="yes"><image compression="off"/></graphics>
    <sound model="ich9"/><video><model type="virtio"/></video>
    <redirdev bus="usb" type="spicevmc"/><redirdev bus="usb" type="spicevmc"/>
    <memballoon model="none"/>
    <rng model="virtio"><backend model="random">/dev/urandom</backend></rng>
  </devices>
</domain>
```

## Modernization vs original

- **Network:** `bridge:virbr0` → `network:default` (NAT). Lab bridge: `bridge name='br0'` if you actually built `br0` via `20_*`.
- **Storage:** `cache=none discard=unmap` idempotently; `io=io_uring` (≥QEMU 10.2) — matches `30_*:build_command`.
- **MemoryBacking:** `memfd shared` added for virtiofs/clipboard future-proofing (was present but now explicit `unit`).
- **Firmware:** added JSON-driven OVMF (original omitted `loader`/`nvram`).

> [!info] Historical collapsed context
> Original filename “20GB” referenced a 20G `qcow2` capacity; current shows 6G guest RAM (`6168576 KiB`) and thin `qcow2`. Capacity = `qemu-img resize` post-create if needed.

Use: `virsh -c qemu:///system define arch-zram.xml; virsh start archlinux`
