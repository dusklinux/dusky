Hyper-V Enlightenments allow KVM to emulate the Microsoft Hyper-V hypervisor. This improves the performance of the Windows 11 virtual machine.

For more information, check out the pages [[Hyper-V Enlightenments]] and [[Hypervisor Features]]

Click the XML tab and replace the hyprv section with this. 

> [!danger] This is ONLY for 12700H cpu, If you have another cpu, leave your's as is, the default is usually automatically optimized for the cpu in use

replace the entire xml file with this. 
### Key Optimizations Applied:

1. **Hybrid Architecture Pinning:**
    
    - **vCPUs 0-3** are pinned rigidly to **Physical P-Cores 0 and 1** (Host CPUs 0, 1, 2, 3). This ensures your game threads share L1/L2 cache and **never** touch an E-core.
        
    - **Emulator Pinning:** The QEMU "overhead" threads (disk I/O, network traffic) are banished to **E-Cores 12-19**. This leaves your P-Cores 100% free for the GPU and Game logic.
        
2. **Hyper-V Cleanup:**
    
    - Removed `avic` (conflicts with `evmcs`).
        
    - Added `vendor_id value='Microsoft Hv'` (helps with Nvidia drivers and anti-cheat).
        
    - Enabled `evmcs` (Critical for Win 11 VBS/Hyper-V performance).
        
3. **NVIDIA Prep:**
    
    - Added `<kvm><hidden state='on'/></kvm>`. This is often required to prevent NVIDIA "Error 43" on consumer cards passed to VMs.
        
4. **Topology:** Explicitly defined as 1 Socket, 2 Cores, 2 Threads to match the P-core hardware layout.
    

### The XML File
```xml
<domain type='kvm'>
  <name>win11</name>
  <uuid>7df8f4a3-5848-4943-96ec-22578d3bd13b</uuid>
  <metadata>
    <libosinfo:libosinfo xmlns:libosinfo="http://libosinfo.org/xmlns/libvirt/domain/1.0">
      <libosinfo:os id="http://microsoft.com/win/11"/>
    </libosinfo:libosinfo>
  </metadata>
  
  <memory unit='KiB'>8388608</memory>
  <currentMemory unit='KiB'>8388608</currentMemory>
  
  <vcpu placement='static'>4</vcpu>
  
  <cputune>
    <vcpupin vcpu='0' cpuset='0'/>
    <vcpupin vcpu='1' cpuset='1'/>
    <vcpupin vcpu='2' cpuset='2'/>
    <vcpupin vcpu='3' cpuset='3'/>
    <emulatorpin cpuset='12-19'/>
  </cputune>

  <os firmware='efi'>
    <type arch='x86_64' machine='q35'>hvm</type>
    <boot dev='hd'/>
  </os>
  
  <features>
    <acpi/>
    <apic/>
    <hyperv mode='custom'>
      <relaxed state='on'/>
      <vapic state='on'/>
      <spinlocks state='on' retries='8191'/>
      <vpindex state='on'/>
      <runtime state='on'/>
      <synic state='on'/>
      <stimer state='on'>
        <direct state='on'/>
      </stimer>
      <reset state='on'/>
      <vendor_id state='on' value='Microsoft Hv'/>
      <frequencies state='on'/>
      <reenlightenment state='on'/>
      <tlbflush state='on'/>
      <ipi state='on'/>
      <evmcs state='on'/>
    </hyperv>
    <kvm>
      <hidden state='on'/>
    </kvm>
    <vmport state='off'/>
    <ioapic driver='kvm'/>
  </features>

  <cpu mode='host-passthrough' check='none'>
    <topology sockets='1' dies='1' cores='2' threads='2'/>
    <cache mode='passthrough'/>
  </cpu>
  
  <clock offset='localtime'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/>
    <timer name='hypervclock' present='yes'/>
  </clock>
  
  <pm>
    <suspend-to-mem enabled='no'/>
    <suspend-to-disk enabled='no'/>
  </pm>
  
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='/mnt/zram1/win11.qcow2'/>
      <target dev='sda' bus='sata'/>
    </disk>
    
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='/mnt/zram1/25h2_lite.iso'/>
      <target dev='sdb' bus='sata'/>
      <readonly/>
    </disk>

    <interface type='bridge'>
      <mac address='52:54:00:53:da:cc'/>
      <source bridge='virbr0'/> <model type='e1000e'/>
    </interface>

    <controller type='usb' model='qemu-xhci' ports='15'/>
    <controller type='pci' model='pcie-root'/>
    <controller type='pci' model='pcie-root-port'/>
    <controller type='pci' model='pcie-root-port'/>
    <controller type='pci' model='pcie-root-port'/>
    <controller type='pci' model='pcie-root-port'/>
    <controller type='pci' model='pcie-root-port'/>
    <controller type='pci' model='pcie-root-port'/>
    <controller type='pci' model='pcie-root-port'/>
    <controller type='pci' model='pcie-root-port'/>
    <controller type='pci' model='pcie-root-port'/>
    <controller type='pci' model='pcie-root-port'/>
    <controller type='pci' model='pcie-root-port'/>
    <controller type='pci' model='pcie-root-port'/>
    <controller type='pci' model='pcie-root-port'/>
    <controller type='pci' model='pcie-root-port'/>

    <console type='pty'/>
    <channel type='spicevmc'>
      <target type='virtio' name='com.redhat.spice.0'/>
    </channel>
    
    <input type='tablet' bus='usb'/>
    <tpm model='tpm-crb'>
      <backend type='emulator'/>
    </tpm>
    
    <graphics type='spice' autoport='yes'>
      <image compression='off'/>
      <gl enable='no'/>
    </graphics>
    <sound model='ich9'/>
    <video>
      <model type='qxl'/>
    </video>
    
    <redirdev bus='usb' type='spicevmc'/>
    <redirdev bus='usb' type='spicevmc'/>
  </devices>
</domain>
```



#### 1. CPU & Pinning (The Performance Foundation)

> [!TIP] Automated Pinning Configuration Script
> You can automatically generate and apply these pinning settings for your specific host CPU topology (P-cores, E-cores, AMD SMT, etc.) by running:
> ```bash
> /home/new/user_scripts/dusky_vm/passthrough/35_cpu_pinning_generator.py
> ```

Otherwise, paste the following blocks below the `<vcpu>` tag manually:

##### Option A: 4-vCPU Configuration (Uses 2 P-Cores, 4 Threads)
```xml
<vcpu placement='static'>4</vcpu>
<cputune>
  <vcpupin vcpu='0' cpuset='0'/>
  <vcpupin vcpu='1' cpuset='1'/>
  <vcpupin vcpu='2' cpuset='2'/>
  <vcpupin vcpu='3' cpuset='3'/>
  <emulatorpin cpuset='12-19'/>
</cputune>
```

##### Option B: 8-vCPU Configuration (Uses 4 P-Cores, 8 Threads — Recommended for 8-Core/8-thread VMs)
```xml
<vcpu placement='static'>8</vcpu>
<cputune>
  <vcpupin vcpu='0' cpuset='0'/>
  <vcpupin vcpu='1' cpuset='1'/>
  <vcpupin vcpu='2' cpuset='2'/>
  <vcpupin vcpu='3' cpuset='3'/>
  <vcpupin vcpu='4' cpuset='4'/>
  <vcpupin vcpu='5' cpuset='5'/>
  <vcpupin vcpu='6' cpuset='6'/>
  <vcpupin vcpu='7' cpuset='7'/>
  <emulatorpin cpuset='12-19'/>
</cputune>
```

#### 2. CPU Topology (Required for Windows Scheduler)

_Find the `<cpu>` section and ensure the topology line matches:_

```xml
<cpu mode='host-passthrough' check='none'>
  <topology sockets='1' dies='1' cores='2' threads='2'/>
  <cache mode='passthrough'/>
</cpu>
```


#### 3. The Clock (Low Latency)

_Replace your existing `<clock>` section with this. This fixes the HPET timer lag._

```xml
<clock offset='localtime'>
  <timer name='rtc' tickpolicy='catchup'/>
  <timer name='pit' tickpolicy='delay'/>
  <timer name='hpet' present='no'/>
  <timer name='hypervclock' present='yes'/>
</clock>
```


#### 4. The Hyper-V Enlightenments (Optimized)

_Replace `<hyperv>` with this. Note: I removed `avic` to prevent conflicts with `evmcs` and set the vendor_id to "Microsoft Hv" for better compatibility._

```xml
<features>
  <hyperv mode='custom'>
    <relaxed state='on'/>
    <vapic state='on'/>
    <spinlocks state='on' retries='8191'/>
    <vpindex state='on'/>
    <runtime state='on'/>
    <synic state='on'/>
    <stimer state='on'>
      <direct state='on'/>
    </stimer>
    <reset state='on'/>
    <vendor_id state='on' value='Microsoft Hv'/> 
    <frequencies state='on'/>
    <reenlightenment state='on'/>
    <tlbflush state='on'/>
    <ipi state='on'/>
    <evmcs state='on'/> 
  </hyperv>
  </features>
```

`Apply`