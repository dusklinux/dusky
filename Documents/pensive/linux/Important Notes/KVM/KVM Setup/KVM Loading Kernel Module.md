---
title: "KVM Kernel Modules"
tags:
  - kvm
  - kernel
  - arch
---

# KVM Kernel Modules — Autoload vs Manual

> [!abstract] What are we doing?
> Verify the kernel is allowed to act as a hypervisor. On Arch (Kernel 7.3+) `systemd-udevd` autoloads the correct `kvm` flavour at boot; manual work is rarely needed.

## 1. Check autoload (expected path)

```bash
lsmod | grep -iE kvm
```

> [!success] Expected (Intel)
> ```
> kvm_intel   401408  0
> kvm        1204224  1 kvm_intel
> irqbypass    16384  1 kvm
> ```
> AMD: `kvm_amd` + `kvm`. `irqbypass` is normal.

If present → **done**. No file to create.

> [!failure] No output
> Means `vmx`/`svm` not exposed to kernel. Re-check UEFI: **VT-x/SVM + VT-d/AMD-Vi** enabled, *not* mitigated by `kvm-intel.nested=0`. On custom kernels ensure `CONFIG_KVM=y/m`, `CONFIG_KVM_INTEL`/`CONFIG_KVM_AMD`.

## 2. Manual load (current boot only)

```bash
# Intel
sudo modprobe kvm_intel
# AMD
sudo modprobe kvm_amd

lsmod | grep kvm   # verify
ls -l /dev/kvm     # crw-rw-rw- 1 root kvm
```

## 3. Persistent autoload (only if udev truly fails)

```bash
echo "kvm_intel" | sudo tee /etc/modules-load.d/kvm.conf  # or kvm_amd
cat /etc/modules-load.d/kvm.conf
systemctl reboot
```

> [!info] What this file does
> `systemd-modules-load.service` reads `/etc/modules-load.d/*.conf` before the graphical session and calls `modprobe`. The pipeline's `05_virtio_iso.py:verify_kvm_capability` checks `/dev/kvm` + `flags: vmx/svm` + `/sys/class/iommu` before proceeding — if those gates pass, this note is already satisfied.

## 4. Quick validate

```bash
ls -l /dev/kvm
virt-host-validate | grep -i kvm
```

Related: [[Verify VT-x and Kernel Modules and IOMMU]] (IOMMUFD/ACS deeper), [[KVM Packages]].
