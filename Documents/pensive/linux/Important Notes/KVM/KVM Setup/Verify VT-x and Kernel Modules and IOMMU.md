---
title: "Verify CPU virtualization, KVM and optional IOMMU"
tags:
  - kvm
  - manual
reviewed: 2026-10-25
---

# Verify CPU virtualization, KVM and optional IOMMU

[[+ MOC KVM|KVM home]]

CPU virtualization and PCI-device isolation are separate features. A plain
Windows/Linux VM needs KVM; VFIO PCI passthrough also needs an enabled IOMMU and
usable device groups. IOMMUFD is a QEMU/kernel interface, not a firmware switch.

Firmware settings can be checked before installing packages. The host-side
commands below require [[KVM Packages|the package set]]; on a fresh host, install
those tools first, then return here to validate KVM. A command not found is a
missing tool, not proof that the CPU lacks virtualization.

## KVM first

In firmware enable Intel VT-x or AMD SVM. Then inspect:

```sh
lscpu
ls -l /dev/kvm
lsmod
virt-host-validate qemu
```

`lsmod` omits built-in kernel support. A missing module listing alone does not
prove KVM is unavailable. If needed, follow [[KVM Loading Kernel Module]].
Nested virtualization controls whether a guest can run its own hypervisor;
it is not a prerequisite for a first-level Windows desktop VM.

## Only for PCI/GPU passthrough

Enable Intel VT-d or AMD-Vi in firmware. Above 4G Decoding can be needed for
large PCI resource mappings. ReBAR support depends on the GPU/firmware/guest
combination; it is not a universal prerequisite or guaranteed speed improvement.
SR-IOV matters only when using a device's virtual functions.

```sh
cat /proc/cmdline
sudo journalctl -k -b --grep='DMAR|IOMMU|AMD-Vi|remapping'
```

List actual IOMMU groups with Python so the command works from Bash or zsh:

```sh
python3 - <<'PY'
from pathlib import Path
import subprocess
for group in sorted(Path('/sys/kernel/iommu_groups').glob('*'), key=lambda p: int(p.name)):
    print('Group', group.name)
    for dev in sorted((group / 'devices').iterdir()):
        subprocess.run(['lspci', '-nnk', '-s', dev.name], check=True)
PY
```

Inspect every function in the selected GPU slot. Other endpoints in the group
can prevent independent assignment. PCI bridges are not equivalent to unrelated
storage/network endpoints. Do not infer isolation from a vendor name or copy
another machine's group number. The workflow does not add ACS overrides.

A kernel supporting IOMMUFD does not prove a VM is using it. Inspect the actual
QEMU command/domain configuration. Do not manually force `vfio_iommu_type1`
into initramfs merely because an older recipe listed it; load `vfio_pci` and
let dependencies follow the installed driver stack.

Optional PCI branch: [[GPU Passthrough — Host Isolation and Return]]. Sources:
[kernel VFIO](https://docs.kernel.org/driver-api/vfio.html),
[kernel IOMMUFD](https://docs.kernel.org/userspace-api/iommufd.html).

---

Previous: [[KVM Packages]]. Continue: [[libvert Modular daemon enable]].
