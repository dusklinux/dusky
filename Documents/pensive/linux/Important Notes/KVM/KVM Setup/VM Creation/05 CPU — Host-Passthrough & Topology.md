---
title: "CPU mode, topology and optional pinning"
tags:
  - kvm
  - manual
aliases:
  - CPU Unified
reviewed: 2026-10-25
---

# CPU mode, topology and optional pinning

`host-passthrough` exposes the host CPU's available features to the guest. It is
a useful single-host default, not a promise that an arbitrary VM can move to
unlike hardware. `host-model`/named models have migration use cases; there is no
justified universal “10–30% slower” figure for them.

In virt-manager, select the host CPU configuration and inspect the XML. Example
for **four vCPUs**, using one socket, four cores and one thread per core:

```xml
<vcpu placement='static'>4</vcpu>
<cpu mode='host-passthrough' migratable='off'>
  <topology sockets='1' cores='4' threads='1'/>
</cpu>
```

The topology product must equal the configured vCPU count. A logical guest
thread is not an exclusively reserved host thread. `migratable='off'` does not
make the VM portable; `check='none'` does not repair incompatible CPUs. Cache
passthrough is an optional CPU-cache representation, not a measured speedup.

Start with a vCPU count the host can support while leaving room for its desktop
and QEMU's work. The Windows script defaults to at most six host logical threads,
using fewer on smaller hosts. A hybrid CPU's logical CPU numbers do not reveal
P/E core type or SMT relationships by themselves.

## Pinning only when useful

```sh
lscpu -e=CPU,CORE,SOCKET,NODE,ONLINE
virsh -c qemu:///system vcpupin win11
virsh -c qemu:///system emulatorpin win11
```

Inspect sibling/core/capacity topology and measure latency before pinning.
`35_cpu_pinning_generator.py` can generate a proposal for the current host.
Read it before applying; a pin map from an i7 laptop is not suitable for every
AMD, desktop or small host. Preserve enough host CPUs for the compositor,
emulator and I/O work. More vCPUs or tighter pinning can reduce performance.

Windows-specific paravirtual interfaces are in [[Hyper-V Enlightenments]].
They do not require enabling nested Hyper-V or hiding KVM. Linux guests have
their own paravirtual support and should not inherit an unrelated Windows
feature block. CPU virtualization, PCI passthrough and Looking Glass are three
separate concepts.

Source: [libvirt CPU XML](https://libvirt.org/formatdomain.html#cpu-model-and-topology).
