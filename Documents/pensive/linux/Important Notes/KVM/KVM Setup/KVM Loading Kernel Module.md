---
title: "KVM kernel modules"
tags:
  - kvm
  - manual
reviewed: 2026-10-25
---

# KVM kernel modules

KVM support must be built into or loadable by the running kernel. Modules
normally load through device discovery; persistent modules-load entries are not
necessary when that already works.

```sh
ls -l /dev/kvm
lsmod
lscpu
```

If support is modular and missing, load **the host CPU's vendor module**:

```sh
# Intel host only:
sudo modprobe kvm_intel
# AMD host only:
sudo modprobe kvm_amd
```

Run the applicable command, not both. Read its error instead of assuming missing
output means virtualization is disabled. Check firmware virtualization, kernel
configuration and `journalctl -k -b`. `/proc/config.gz` exists only when the kernel
provides it. Do not expect a fixed `/dev/kvm` permission string across systems.

Only if an otherwise working module genuinely needs explicit boot loading,
put its name in `/etc/modules-load.d/kvm.conf`. That file contains a module
name such as `kvm_intel`, not modprobe options. No host reboot is required just
to test `modprobe` now.

Verify `virt-host-validate qemu`, then [[libvert Modular daemon enable]].
