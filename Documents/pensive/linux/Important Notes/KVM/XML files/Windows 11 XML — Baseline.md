---
title: "Windows 11 XML — annotated baseline"
tags:
  - kvm
  - manual
reviewed: 2026-10-25
---

# Windows 11 XML — annotated baseline

This is a **schema-checked teaching template**, not an exported definition of
this laptop. Replace image paths/name/resources before defining it. Let libvirt
generate UUID, MAC, PCI addresses and firmware paths. The example omits a
physical GPU, Looking Glass and folder sharing so those options can be added
independently after the baseline boots.

Create/provision the qcow2 and stage both ISOs first. Here four vCPUs and eight
GiB are explicit examples; adapt them using [[07 Memory — Fixed and Ballooning]]
and [[05 CPU — Host-Passthrough & Topology]]. VirtIO Windows drivers are required.

```xml
<domain type='kvm'>
  <name>win11-manual-example</name>
  <memory unit='MiB'>8192</memory>
  <currentMemory unit='MiB'>8192</currentMemory>
  <vcpu placement='static'>4</vcpu>
  <os firmware='efi'>
    <type arch='x86_64' machine='q35'>hvm</type>
    <boot dev='cdrom'/>
    <boot dev='hd'/>
  </os>
  <features>
    <acpi/>
    <apic/>
    <smm state='on'/>
    <hyperv mode='custom'>
      <relaxed state='on'/>
      <vapic state='on'/>
      <spinlocks state='on' retries='8191'/>
      <vpindex state='on'/>
      <synic state='on'/>
      <stimer state='on'/>
      <frequencies state='on'/>
      <reenlightenment state='on'/>
      <tlbflush state='on'/>
      <ipi state='on'/>
    </hyperv>
  </features>
  <cpu mode='host-passthrough' migratable='off'>
    <topology sockets='1' cores='4' threads='1'/>
  </cpu>
  <clock offset='localtime'>
    <timer name='hypervclock' present='yes'/>
  </clock>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>
  <devices>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2' cache='none' io='io_uring' discard='unmap'/>
      <source file='/var/lib/libvirt/images/win11-manual-example.qcow2'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='/var/lib/libvirt/images/windows-install.iso'/>
      <target dev='sda' bus='sata'/>
      <readonly/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='/var/lib/libvirt/images/virtio-win.iso'/>
      <target dev='sdb' bus='sata'/>
      <readonly/>
    </disk>
    <controller type='usb' model='qemu-xhci'/>
    <interface type='network'>
      <source network='default'/>
      <model type='virtio'/>
    </interface>
    <channel type='unix'>
      <target type='virtio' name='org.qemu.guest_agent.0'/>
    </channel>
    <channel type='spicevmc'>
      <target type='virtio' name='com.redhat.spice.0'/>
    </channel>
    <input type='tablet' bus='usb'/>
    <graphics type='spice' autoport='yes'>
      <listen type='address' address='127.0.0.1'/>
    </graphics>
    <video><model type='vga'/></video>
    <sound model='ich9'><audio id='1'/></sound>
    <audio id='1' type='spice'/>
    <tpm model='tpm-crb'><backend type='emulator' version='2.0'/></tpm>
    <memballoon model='none'/>
  </devices>
</domain>
```

`on_poweroff='destroy'` describes cleanup after a guest poweroff; it is not a
command to force-kill Windows. The VGA/SPICE device is intentionally retained
for installation and recovery. Modern Windows display-driver acceleration is
not inferred from that emulated setup device.

For the SATA/e1000e route, change the OS disk to an unused SATA drive target and
the NIC model to e1000e; stage the correct boot drivers before changing an
already-installed guest. For optional features, merge the snippets from
[[Looking Glass]], [[09 Shared Folders — VirtIO-FS]] or
[[GPU Passthrough — Host Isolation and Return]].

Validate your saved XML, then define only when its resources are ready:

```sh
virt-xml-validate win11-manual.xml domain
virsh -c qemu:///system define win11-manual.xml --validate
virsh -c qemu:///system dumpxml win11-manual-example --inactive
virsh -c qemu:///system start win11-manual-example
```

Schema validation checks structure; it cannot prove image accessibility,
firmware/key state, driver compatibility, actual boot or workload correctness.
After install eject the OS ISO and choose disk boot as appropriate.
See [[02 Chipset & Firmware — Q35 + UEFI]] for firmware auto-selection versus
actual Secure Boot enforcement; do not copy resolved NVRAM files from another VM.
