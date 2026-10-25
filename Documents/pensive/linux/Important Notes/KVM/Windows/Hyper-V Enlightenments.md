---
title: "Windows Hyper-V enlightenments on KVM"
tags:
  - kvm
  - manual
  - windows
reviewed: 2026-10-25
---

# Windows Hyper-V enlightenments on KVM

These are paravirtual interfaces that help Windows cooperate with KVM. They do
not require installing the Hyper-V role inside Windows or enabling nested
virtualization. The current deployment uses a conservative custom set plus a
Hyper-V clock. Add features within the existing `<features>` element:

```xml
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
```

Within the existing clock block, the deployment requests:

```xml
<timer name='hypervclock' present='yes'/>
```

Do not duplicate `<clock>` or `<features>`. Let libvirt/virt-install normalize
and validate the complete domain; [[Windows 11 XML — Baseline]] gives one example.
No unmeasured percentage speedup is promised by enabling a flag.

| Group | Purpose / dependency to understand |
|---|---|
| relaxed / vapic / spinlocks | Guest scheduling/interrupt/spin behavior |
| vpindex | Virtual processor identifiers used by other interfaces |
| synic / stimer / Hyper-V time | Synthetic interrupt/timer path with interdependent support |
| frequencies / reenlightenment | Timing information/change notifications; workload/migration context matters |
| tlbflush / ipi | Paravirtual address-space invalidation and interprocessor calls |

This table is an orientation, not a replacement for the installed QEMU feature
rules. Check host/domain capabilities before adding optional features.
`runtime` and `stimer/direct` are other supported interfaces on suitable stacks,
but are not in the current script's exact baseline. `evmcs` is for Intel nested
virtualization; `avic` has vendor/host dependencies. Neither is a cross-hardware
checkbox to paste into every Windows VM. Do not mix all flags from all examples.

There is no general requirement to spoof a vendor string or hide KVM for current
NVIDIA drivers, and those settings do not guarantee anti-cheat compatibility.
The script does not add the old `kvm.hidden`/Error 43 workaround. For timing,
prefer Windows defaults over forced debugging options: [[Disable useplatformclock]].

Source: [QEMU Hyper-V documentation](https://www.qemu.org/docs/master/system/i386/hyperv.html),
[libvirt feature XML](https://libvirt.org/formatdomain.html#hypervisor-features).
