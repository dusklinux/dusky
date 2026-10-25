---
title: "Hyper-V Enlightenments — Fast Path (Aug 2026)"
tags:
  - kvm
  - windows
  - hyperv
  - qemu
  - cpu-pinning
---

# Hyper-V Enlightenments — Fast Path

> [!info] Context
> KVM emulates Hyper-V enlightenments so Windows behaves as if on Hyper-V — huge win for clocks, IPIs, TLB. `30_kvm_vm_deploy.py:HYPERV` lists the canonical 10: `relaxed,vapic,spinlocks,vpindex,synic,stimer,frequencies,reenlightenment,tlbflush,ipi`. This note is the **pinning + enlightenment + clock** preset (12700H-tuned in the old revision; generic below). See [[Hyper-V Enlightenments]] (upstream docs) + [[Hypervisor Features]] for full semantics.

## Preset — paste-safe XML (integrated)

> [!warning] Machine-specific warning (carried from original)
> The original XML was tuned for i7-12700H (6P+8E). **Do not blindly paste `cputune` on other CPUs.** Use `35_cpu_pinning_generator.py` to generate pinning for your topology, then merge the enlightenment/clock blocks below.

### Full XML skeleton (4 vCPU example, adapt vCPU/cputune)

```xml
<domain type='kvm'>
  <name>win11</name>
  <uuid>7df8f4a3-5848-4943-96ec-22578d3bd13b</uuid>
  <metadata><libosinfo:libosinfo xmlns:libosinfo="http://libosinfo.org/xmlns/libvirt/domain/1.0">
    <libosinfo:os id="http://microsoft.com/win/11"/></libosinfo:libosinfo></metadata>
  <memory unit='KiB'>8388608</memory>
  <currentMemory unit='KiB'>8388608</currentMemory>
  <vcpu placement='static'>4</vcpu>
  <cputune>
    <vcpupin vcpu='0' cpuset='0'/><vcpupin vcpu='1' cpuset='1'/>
    <vcpupin vcpu='2' cpuset='2'/><vcpupin vcpu='3' cpuset='3'/>
    <emulatorpin cpuset='12-19'/>
  </cputune>
  <os firmware='efi'><type arch='x86_64' machine='q35'>hvm</type><boot dev='hd'/></os>
  <features>
    <acpi/><apic/>
    <hyperv mode='custom'>
      <relaxed state='on'/><vapic state='on'/><spinlocks state='on' retries='8191'/>
      <vpindex state='on'/><runtime state='on'/><synic state='on'/>
      <stimer state='on'><direct state='on'/></stimer><reset state='on'/>
      <vendor_id state='on' value='Microsoft Hv'/>
      <frequencies state='on'/><reenlightenment state='on'/><tlbflush state='on'/><ipi state='on'/><evmcs state='on'/>
    </hyperv>
    <kvm><hidden state='on'/></kvm>
    <vmport state='off'/><ioapic driver='kvm'/>
  </features>
  <cpu mode='host-passthrough' check='none'><topology sockets='1' dies='1' cores='2' threads='2'/><cache mode='passthrough'/></cpu>
  <clock offset='localtime'>
    <timer name='rtc' tickpolicy='catchup'/><timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/><timer name='hypervclock' present='yes'/>
  </clock>
  <pm><suspend-to-mem enabled='no'/><suspend-to-disk enabled='no'/></pm>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type='file' device='disk'><driver name='qemu' type='qcow2' cache='none' io='io_uring' discard='unmap'/><source file='/var/lib/libvirt/images/win11.qcow2'/><target dev='vda' bus='virtio'/></disk>
    <disk type='file' device='cdrom'><driver name='qemu' type='raw'/><source file='/var/lib/libvirt/images/win11.iso'/><target dev='sda' bus='sata'/><readonly/></disk>
    <disk type='file' device='cdrom'><driver name='qemu' type='raw'/><source file='/var/lib/libvirt/images/virtio-win.iso'/><target dev='sdb' bus='sata'/><readonly/></disk>
    <interface type='network'><source network='default'/><model type='virtio'/></interface>
    <controller type='usb' model='qemu-xhci' ports='15'/>
    <controller type='pci' model='pcie-root'/><controller type='pci' model='pcie-root-port'/><controller type='pci' model='pcie-root-port'/><controller type='pci' model='pcie-root-port'/><controller type='pci' model='pcie-root-port'/><controller type='pci' model='pcie-root-port'/><controller type='pci' model='pcie-root-port'/><controller type='pci' model='pcie-root-port'/><controller type='pci' model='pcie-root-port'/><controller type='pci' model='pcie-root-port'/><controller type='pci' model='pcie-root-port'/><controller type='pci' model='pcie-root-port'/><controller type='pci' model='pcie-root-port'/>
    <console type='pty'/>
    <channel type='spicevmc'><target type='virtio' name='com.redhat.spice.0'/></channel>
    <channel type='unix'><source mode='bind'/><target type='virtio' name='org.qemu.guest_agent.0'/></channel>
    <input type='tablet' bus='usb'/><tpm model='tpm-crb'><backend type='emulator'/></tpm>
    <graphics type='spice' autoport='yes'><image compression='off'/><gl enable='no'/></graphics><sound model='ich9'/><video><model type='qxl'/></video>
    <redirdev bus='usb' type='spicevmc'/><redirdev bus='usb' type='spicevmc'/>
    <memballoon model='none'/>
    <shmem name='looking-glass'><model type='ivshmem-plain'/><size unit='M'>64</size></shmem>
  </devices>
</domain>
```

> [!tip] What was tuned vs upstream default
> - `avic` removed → conflicts `evmcs`
> - `vendor_id='Microsoft Hv'` → anti-cheat/NVIDIA `Error 43` mitigation
> - `kvm.hidden=on` → same NVIDIA reason
> - `evmcs=on` → VBS/Hyper-V perf (Intel nested; Intel only)
> - `cputune`: P-cores for vCPUs, E-cores for emulator (see `35_cpu_pinning_generator.py` for auto-gen)
> - `devices`: `virtio` bus (`cache=none`, `io=io_uring`, `discard=unmap` for disk; `cache=writeback` if on RAM-backed zram/tmpfs) + `virtio` NIC + `<memballoon model='none'/>` (avoids DMA latency/jitter) + `<shmem>` for Looking Glass

## Minimal patches (if you already have a VM)

### 1. CPU & pinning

> [!tip] Automated via `35_cpu_pinning_generator.py` — generates `vcpupin` + `emulatorpin` + `iothreadpin` + topology for your host (`lscpu` + `nproc`). Hand-type only if you understand P/E/SMT layout. Adapt example: 4 vCPU uses 2 P-cores (4 threads); 8 vCPU uses 4 P-cores.

**Option A — 4 vCPU (2 P-cores, 4 threads):**
```xml
<vcpu placement='static'>4</vcpu>
<cputune>
  <vcpupin vcpu='0' cpuset='0'/><vcpupin vcpu='1' cpuset='1'/>
  <vcpupin vcpu='2' cpuset='2'/><vcpupin vcpu='3' cpuset='3'/>
  <emulatorpin cpuset='12-19'/>
</cputune>
```

**Option B — 8 vCPU (4 P-cores, 8 threads — recommended for 8-core VMs):**
```xml
<vcpu placement='static'>8</vcpu>
<cputune>
  <vcpupin vcpu='0' cpuset='0'/><vcpupin vcpu='1' cpuset='1'/>
  <vcpupin vcpu='2' cpuset='2'/><vcpupin vcpu='3' cpuset='3'/>
  <vcpupin vcpu='4' cpuset='4'/><vcpupin vcpu='5' cpuset='5'/>
  <vcpupin vcpu='6' cpuset='6'/><vcpupin vcpu='7' cpuset='7'/>
  <emulatorpin cpuset='12-19'/>
</cputune>
```
Generate yours: `/home/dusk/user_scripts/dusky_vm/passthrough/35_cpu_pinning_generator.py`

### 2. Topology

```xml
<cpu mode='host-passthrough' check='none'><topology sockets='1' dies='1' cores='2' threads='2'/><cache mode='passthrough'/></cpu>
```

### 3. Clock

```xml
<clock offset='localtime'>
  <timer name='rtc' tickpolicy='catchup'/><timer name='pit' tickpolicy='delay'/>
  <timer name='hpet' present='no'/><timer name='hypervclock' present='yes'/>
</clock>
```

### 4. Hyper-V block

```xml
<features><hyperv mode='custom'>
  <relaxed state='on'/><vapic state='on'/><spinlocks state='on' retries='8191'/>
  <vpindex state='on'/><runtime state='on'/><synic state='on'/>
  <stimer state='on'><direct state='on'/></stimer><reset state='on'/>
  <vendor_id state='on' value='Microsoft Hv'/><frequencies state='on'/><reenlightenment state='on'/><tlbflush state='on'/><ipi state='on'/><evmcs state='on'/>
</hyperv></features>
```

→ **Apply**. Next: [[Configure the Storage]] / [[Looking Glass]] (if VFIO).

See: `30_kvm_vm_deploy.py`, `35_cpu_pinning_generator.py`.
