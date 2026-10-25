---
title: "Optional VBS and nested virtualization — separate from baseline"
tags:
  - kvm
  - manual
  - windows
reviewed: 2026-10-25
---

# Optional VBS and nested virtualization — separate from baseline

This page explains an existing optional topic; it does not change the VM's
security configuration. UEFI, TPM readiness, Secure Boot enforcement, Hyper-V
enlightenments and Windows Memory Integrity/VBS are different mechanisms.
Ordinary Windows-on-KVM enlightenments do not mean the guest is running Hyper-V.

If deliberately enabling a feature requiring a nested hypervisor, verify host
nested support, compatible CPU features and the Windows feature's current VM
requirements. Exposing VMX on Intel or SVM on AMD is not by itself proof VBS
will run. Do not copy Intel evmcs onto AMD or claim one CPU XML snippet meets
all Windows VBS requirements.

In Windows inspect `msinfo32` and Windows Security → Device security → Core
isolation. Keep the requested settings and measure workload behavior; nested
virtualization can change performance and interact with memory/device support.
No universal FPS penalty or mandatory enable/disable recommendation is established
here. Do not confuse Kernel DMA Protection with proof VBS is active.

Reference: [Microsoft Memory Integrity deployment](https://learn.microsoft.com/en-us/windows/security/hardware-security/enable-virtualization-based-protection-of-code-integrity),
[QEMU Hyper-V features](https://www.qemu.org/docs/master/system/i386/hyperv.html).
