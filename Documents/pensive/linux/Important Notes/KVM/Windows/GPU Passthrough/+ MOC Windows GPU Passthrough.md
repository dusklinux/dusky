# 🗺️ Windows KVM & GPU Passthrough: Master Roadmap

> [!info] Note on Windows Versions
> The installation steps for **Windows 10** and **Windows 11** are virtually identical. The only key differences for Windows 11 are:
> * **RAM Requirement:** Minimum 4GB+ recommended.
> * **Storage:** Slightly higher storage 52GB (minumum) space requirements.

## 🚀 Installation Order
Follow these notes in the exact order below. Do not skip steps.

1. [[Virt_Manager Packages for Windows]]
2. [[KVM preperation and Optimization]]
3. [[Host PC  Preparation for GPU isolation]]
4. [[VirtIO win driver iso]]
5. [[+ MOC Windows Installation Through Virt Manager]]
6. [[Windows Configurations for Passthrough]]
7. [[Looking Glass]]

---

## ✅ System Verification

> [!danger] CRITICAL CHECK
> You must verify that your hardware virtualization is active before proceeding.
> 
> Open your terminal and run:
> ```bash
> virt-host-validate
> ```
> 
> **What to look for:**
> * You must see `PASS` for **QEMU** and **KVM** hardware virtualization.
> * *Note: You may see warnings for IOMMU at this stage. This is normal if you haven't completed the [[Host PC  Preparation for GPU isolation]] step yet.*