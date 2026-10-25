---
title: "IOMMU boot parameters — systemd-boot and mkinitcpio"
tags:
  - kvm
  - manual
aliases:
  - IOMMU Boot Parameters
reviewed: 2026-10-25
---

# IOMMU boot parameters — systemd-boot and mkinitcpio

The filename is retained for old links; this setup uses **systemd-boot**, not
GRUB. Host boot parameters are separate from the guest's OVMF firmware.
Do not edit every boot entry or replace a complete options line with an example.

```sh
bootctl status
bootctl list --json=short
cat /proc/cmdline
```

Identify the intended entry and its actual source. Type #1 entries have an
`options` line in the displayed entry file. Type #2 entries are UKIs whose
embedded command line must be rebuilt from the configured source. Inspect
`/etc/kernel/cmdline`, any effective cmdline fragments, and mkinitcpio presets.
Creating two overlapping sources can duplicate parameters.

For a Type #1 entry, edit its existing options line, preserving root UUID,
encryption, filesystem, resume and other unrelated settings. For a UKI, change
its effective source and rebuild through the configured mkinitcpio preset.
`bootctl update` updates the bootloader binary; it does not rebuild a UKI or
repair an embedded command line.

| CPU/platform | Usual passthrough parameters |
|---|---|
| Intel | `intel_iommu=on`; this workflow also selects `iommu=pt` |
| AMD | AMD-Vi normally follows firmware; this workflow selects `iommu=pt` |

`amd_iommu=on` is not a supported on-switch. `amd_iommu=force_enable` is a specific
firmware workaround, not a universal AMD default. `iommu=pt` selects passthrough
mappings for ordinary host devices; it does not enable CPU virtualization or
make an unsuitable IOMMU group independently assignable.

VFIO IDs can be supplied by modprobe configuration or a kernel parameter.
Reliable early binding comes from **load order and the correct configuration
being available in initramfs**, not a claim that the kernel command-line form
is inherently broken. The Phase 3 script and newer toggle intentionally differ
in whether they mirror IDs on the command line. Do not mix them with an
unrelated manual configuration or duplicate lists casually.

Continue with [[GPU Passthrough — Host Isolation and Return]]. Sources:
[kernel parameter reference](https://docs.kernel.org/admin-guide/kernel-parameters.html),
[systemd-boot entries](https://www.freedesktop.org/software/systemd/man/latest/systemd-boot.html),
[mkinitcpio configuration](https://man.archlinux.org/man/mkinitcpio.conf.5).
