---
title: "Reference XML — Windows 11 (Q35, OVMF, TPM, Virtio)"
tags:
  - kvm
  - libvirt
  - xml
  - windows
  - reference
---

# Reference XML — Windows 11 (Q35, OVMF, TPM, Virtio)

> [!info] Purpose
> De-bloated Windows 11 lab XML (base before pinning/LG). Matches `30_kvm_vm_deploy.py:build_command` (`--osinfo win11 --machine q35 --cpu host-passthrough … --memballoon none …`). Use `virsh define` or `virsh edit` after provisioning disk.

> [!warning] Legacy paths scrubbed
> Original had `virbr0` as `bridge` (NAT vs bridge confusion) + `qxl` video + emulated `e1000e` + missing TPM. Updated to `virtio` where appropriate; keep `qxl` only if not using `virtio-gpu`/`Looking Glass`.

```xml
<domain type="kvm">
  <name>win11</name>
  <uuid>5fb07cd7-762b-4d64-97ef-e22a8f32b1fa</uuid>
  <metadata>
    <libosinfo:libosinfo xmlns:libosinfo="http://libosinfo.org/xmlns/libvirt/domain/1.0">
      <libosinfo:os id="http://microsoft.com/win/11"/>
    </libosinfo:libosinfo>
  </metadata>
  <memory unit="KiB">8388608</memory>      <!-- 8 GiB -->
  <currentMemory unit="KiB">8388608</currentMemory>
  <memoryBacking><source type="memfd"/><access mode="shared"/></memoryBacking> <!-- for virtiofs/LG shared mem -->
  <vcpu placement="static">6</vcpu>
  <!-- cputune generated via 35_cpu_pinning_generator.py; example: -->
  <!-- <cputune><vcpupin vcpu='0' cpuset='0'/>…<emulatorpin cpuset='12-13'/></cputune> -->
  <os firmware="efi">
    <type arch="x86_64" machine="q35">hvm</type>
    <firmware>
      <feature enabled="no" name="enrolled-keys"/>
      <feature enabled="yes" name="secure-boot"/>
    </firmware>
    <loader readonly="yes" secure="yes" type="pflash" format="raw">/usr/share/edk2/x64/OVMF_CODE.secboot.4m.fd</loader>
    <nvram template="/usr/share/edk2/x64/OVMF_VARS.4m.fd" templateFormat="raw" format="raw">/var/lib/libvirt/qemu/nvram/win11_VARS.fd</nvram>
    <boot dev="hd"/>
  </os>
  <features>
    <acpi/><apic/>
    <hyperv mode="custom">
      <relaxed state="on"/><vapic state="on"/><spinlocks state="on" retries="8191"/>
      <vpindex state="on"/><runtime state="on"/><synic state="on"/><stimer state="on"><direct state="on"/></stimer>
      <reset state="on"/><vendor_id state="on" value="Microsoft Hv"/>
      <frequencies state="on"/><reenlightenment state="on"/><tlbflush state="on"/><ipi state="on"/><evmcs state="on"/>
    </hyperv>
    <kvm><hidden state="on"/></kvm>
    <vmport state="off"/><ioapic driver="kvm"/><smm state="on"/>
  </features>
  <cpu mode="host-passthrough" check="none" migratable="off">
    <topology sockets="1" dies="1" cores="3" threads="2"/><cache mode="passthrough"/>
  </cpu>
  <clock offset="localtime">
    <timer name="rtc" tickpolicy="catchup"/><timer name="pit" tickpolicy="delay"/>
    <timer name="hpet" present="no"/><timer name="hypervclock" present="yes"/>
  </clock>
  <pm><suspend-to-mem enabled="no"/><suspend-to-disk enabled="no"/></pm>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type="file" device="disk">
      <driver name="qemu" type="qcow2" cache="none" io="io_uring" discard="unmap"/>
      <source file="/var/lib/libvirt/images/win11.qcow2"/>
      <target dev="vda" bus="virtio"/>
      <address type="pci" domain="0x0000" bus="0x04" slot="0x00" function="0x0"/>
    </disk>
    <!-- Windows ISO (remove after install) -->
    <disk type="file" device="cdrom">
      <driver name="qemu" type="raw"/>
      <source file="/var/lib/libvirt/images/win11.iso"/>
      <target dev="sda" bus="sata"/><readonly/>
    </disk>
    <!-- VirtIO drivers -->
    <disk type="file" device="cdrom">
      <driver name="qemu" type="raw"/>
      <source file="/var/lib/libvirt/images/virtio-win.iso"/>
      <target dev="sdb" bus="sata"/><readonly/>
    </disk>
    <controller type="usb" model="qemu-xhci" ports="15"/>
    <controller type="pci" model="pcie-root"/>
    <controller type="pci" model="pcie-root-port"/><controller type="pci" model="pcie-root-port"/>
    <controller type="pci" model="pcie-root-port"/><controller type="pci" model="pcie-root-port"/>
    <filesystem type="mount"><source dir="/mnt/zram1/share"/><target dir="host_zram"/><driver type="virtiofs"/></filesystem>
    <interface type="network">
      <source network="default"/><model type="virtio"/>
    </interface>
    <channel type="spicevmc"><target type="virtio" name="com.redhat.spice.0"/></channel>
    <channel type="unix"><source mode="bind"/><target type="virtio" name="org.qemu.guest_agent.0"/></channel>
    <tpm model="tpm-crb"><backend type="emulator" version="2.0"/></tpm>
    <console type="pty"/><redirdev bus="usb" type="spicevmc"/><redirdev bus="usb" type="spicevmc"/>
    <input type="tablet" bus="usb"/> <!-- or virtio with LG -->
    <graphics type="spice" port="-1" tlsPort="-1" autoport="yes"><image compression="off"/></graphics>
    <sound model="ich9"/><video><model type="qxl"/></video> <!-- or virtio with accel3d, or none + <shmem> for LG -->
    <!-- passthrough hostdevs injected via 30_kvm_vm_deploy: flag hostdev pci_0000_01_00_0 etc. -->
    <!-- native LG shmem (Aug 2026): <shmem name='looking-glass'><model type='ivshmem-plain'/><size unit='M'>64</size></shmem> -->
    <memballoon model="none"/>
    <rng model="virtio"><backend model="random">/dev/urandom</backend></rng>
  </devices>
</domain>
```

## Notes vs original

- **Firmware:** hard-coded `fd` → JSON-driven `OVMF_CODE.secboot.4m.fd` + `OVMF_VARS` per-domain (`edk2-ovmf` 2026).
- **Network:** `bridge:virbr0` (wrong) → `network:default` + `model:virtio` (NAT). Bridge users: `bridge name='br0'` per `20_*`.
- **Storage:** `cache=none` + `io=io_uring` + `discard=unmap` (QEMU 11.1); `virbr0` `bridge` interface vs `network`.
- **TPM:** added `tpm-crb` `2.0` + `smm` (osinfo `win11` requires).
- **CPU:** `host-passthrough` topology (`cores×threads = vcpu`).
- **MemoryBacking:** `memfd` shared for virtiofs/LG.
- **Passthrough:** `hostdev managed='yes'` via `nodedev` `pci_…` (from `15_*` state), not raw `domain/bus/slot` alone.

Validate: `virt-xml-validate <file>; virsh define <file>; virsh dumpxml win11 | grep -E 'tpm|shmem|hostdev'`
