

# MOC SPEEDRUN: Windows GPU Passthrough

## Prerequisites

1. ```
    sudo pacman --needed -S qemu-full libvirt virt-install virt-manager virt-viewer dnsmasq iproute2 openbsd-netcat edk2-ovmf swtpm nftables iptables libosinfo
    ```
    
2. ```
    sudo usermod -aG libvirt,kvm,input "$(id -un)"
    ```
    
    > [!NOTE]
    > 
    > _(Note: You **must** log out and log back in, or reboot, for this group change to take effect). , DO IT LATER_
    

## Libvirt Configuration

3. Open the modular configuration file:
    
    ```
    sudo nvim /etc/libvirt/virtqemud.conf
    ```
    
    Scroll to the bottom and paste the following block to explicitly grant socket permissions to your group:
    
    ```
    unix_sock_group = "libvirt"
    unix_sock_rw_perms = "0770"
    ```
    

## Host Configuration

4. ```
    lscpu | grep -i virtualization
    ```
    
    > [!CHECK] Expected Output
    > 
    > You should see `VT-x` (for Intel) or `AMD-V` (for AMD).
    
5. We need to ensure your running Arch Kernel 7.1 was compiled with the modern virtualization stack.
    
    ```
    zgrep -E "CONFIG_KVM=|CONFIG_VFIO_PCI=|CONFIG_IOMMUFD=" /proc/config.gz
    ```
    
    > [!EXAMPLE] Understanding the Results
    > 
    > - **`=y`**: Built directly into the kernel (Always active).
    >     
    > - **`=m`**: Loadable Module (Arch default, loaded dynamically by QEMU/libvirt).
    >     
    
6. Verify IOMMU Groups & ACS Isolation (The Crucial Test)
    
    If your IOMMU is working and ACS is functioning, the kernel will physically separate your PCIe devices into distinct numbered groups.
    
    Run this bash script to map out your hardware:
    
    ```
    for d in /sys/kernel/iommu_groups/*/devices/*; do 
      n=${d#*/iommu_groups/*}; n=${n%%/*}
      printf 'IOMMU Group %s ' "$n"
      lspci -nns "${d##*/}"
    done
    ```
    
    > [!INFO] How to Read Your IOMMU Map
    > 
    > Look through the output for the GPU you want to pass through.
    > 
    > **Success:** Your target GPU and its associated Audio Controller are alone in their own isolated `IOMMU Group`.
    > 
    > **Failure:** Your GPU is grouped with essential host devices (like your main NVMe drive). You would need an ACS Override Patch.
    
    for my hardware its this
    
    they're in group 15, the same group!
    
    ```
    IOMMU Group 15 01:00.0 VGA compatible controller [0300]: NVIDIA Corporation GA107M [GeForce RTX 3050 Ti Mobile] [10de:25a0] (rev a1)
    IOMMU Group 15 01:00.1 Audio device [0403]: NVIDIA Corporation GA107 High Definition Audio Controller [10de:2291] (rev a1)
    ```
    
    take a note of the ids for both
    
    ```
    10de:2291, 10de:25a0
    ```
    

## Driver Isolation

7. Open your main Arch boot entry file.
    
    ```
    sudo nvim /boot/loader/entries/arch-linux.conf
    ```
    
    **Append Kernel Parameters**
    
    Append the following arguments to the `options` line (the same line containing `root=...` and `rw`):
    
    _(Ensure `vfio-pci.ids=` matches the exact IDs you pulled in step 7 with the script)_
    
    ```
    intel_iommu=on iommu=pt vfio-pci.ids=10de:25a0,10de:2291
    ```
    
    > [!NOTE]- Parameter Breakdown
    > 
    > - `intel_iommu=on`: Explicitly enables Intel's IOMMU driver (VT-d), essential for mapping guest memory to physical hardware. _(If using an AMD CPU, change to `amd_iommu=on`)_.
    >     
    > - `iommu=pt`: Sets "Passthrough" mode for the host. The host OS bypasses IOMMU translation for its own devices, yielding better host performance.
    >     
    > - `vfio-pci.ids=xxxx:xxxx,yyyy:yyyy`: Tells the kernel to bind these IDs to `vfio-pci` as early as possible.
    >     
    > - `module_blacklist=...`: An aggressive **in-kernel** parameter. It strictly forbids the kernel module loader from touching the native NVIDIA drivers during early boot, preventing race conditions.
    >     
    > 
    > ```
    > module_blacklist=nouveau,nvidia,nvidia_drm,nvidia_modeset,nvidia_uvm
    > ```
    

## Initramfs Configuration

8. The `initramfs` is the temporary file system loaded into RAM before the root partition mounts. To ensure `vfio-pci` grabs the GPU before the display manager starts, its modules must be baked into this image.
    
    Edit mkinitcpio.conf
    
    ```
    sudo nvim /etc/mkinitcpio.conf
    ```
    
    ### 3.2 Update MODULES
    
    Add the core VFIO framework to the `MODULES` array, ahead of any native GPU driver. Current Arch Wiki guidance lists `vfio_pci` before `vfio` and `vfio_iommu_type1` — keep that order; `mkinitcpio` resolves the real module dependency graph through `modprobe` regardless of how you order the array, so this is a documented convention, not a load-bearing detail.
    
    ```
    MODULES=(i915 btrfs vfio_pci vfio vfio_iommu_type1)
    ```
    
    _(AMD iGPU users: substitute `amdgpu` for `i915`.)_
    
    > [!WARNING]- Why `i915` (or `amdgpu`) is in that list
    > 
    > Since kernel 6.0, framebuffer/console output freezes the moment the VFIO modules load and stays frozen until a real GPU driver takes over. If you use full-disk encryption, this can hide your LUKS password prompt entirely, making the system look hung when it isn't. Loading your iGPU's own driver ahead of the VFIO chain keeps the console usable. This has zero effect on the NVIDIA isolation logic — `i915`/`amdgpu` only match Intel/AMD vendor IDs, never the NVIDIA device you're isolating.
    
    > [!INFO]- You will not see `vfio_virqfd` in this list, and that's correct
    > 
    > Older guides (and plenty of search results you'll find) tell you to add a fourth module, `vfio_virqfd`, to this array. As of kernel 6.2, that functionality was folded directly into the base `vfio` module — there is no standalone `vfio_virqfd` module on kernel 7.x to load. If you copy a MODULES line from an older tutorial that includes it, delete it; `modprobe` will simply fail to find it.
    > 
    > Separately: `vfio`, `vfio_pci`, and `vfio_iommu_type1` ship as genuine loadable modules (`=m`) in Arch's official kernel package, not built into the kernel core. You should _not_ see "module not found" warnings for any of the three listed above when you regenerate the initramfs in Phase 5 — if you do, something else is wrong (mismatched kernel/headers, a custom kernel config, etc.), so don't dismiss it as expected noise.
    

## VFIO Configuration

9. Create VFIO Config
    
    ```
    sudo nvim /etc/modprobe.d/vfio.conf
    ```
    
    Add Configuration
    
    Paste the following rules:
    
    ```
    # Explicitly assign IDs to vfio-pci
    options vfio-pci ids=10de:25a0,10de:2291
    
    # Enforce strict load order: vfio-pci must load BEFORE any native drivers
    softdep nvidia pre: vfio-pci
    softdep nvidia_drm pre: vfio-pci
    softdep nvidia_modeset pre: vfio-pci
    softdep nvidia_uvm pre: vfio-pci
    softdep nouveau pre: vfio-pci
    
    # Disable automatic loading of native drivers via udev
    blacklist nouveau
    blacklist nvidia
    blacklist nvidia_drm
    blacklist nvidia_modeset
    blacklist nvidia_uvm
    ```
    
    ```
    sudo mkinitcpio -P
    ```
    
10. make sure you're in hybrid mode and not mux/dgpu mode. or you'll reboot into a black screen! do so with ghelper for linux or something, or bios if your laptop supports it.
    
11. Kernel cmdline — Add to /boot/loader/entries/arch.conf:
    
    ```
    intel_iommu=on iommu=pt vfio-pci.ids=10de:25a0,10de:2291 module_blacklist=nvidia,nvidia_modeset,nvidia_uvm,nvidia_drm,nouveau
    ```
    

## Module and Daemon Management

12. Verify Existing Modules (Modern Autoload)
    
    In modern Arch Linux (Kernel 7.1+), the `systemd-udevd` service automatically detects your CPU capabilities during boot and loads the correct KVM modules.
    
    Let's verify that this automatic process succeeded. Open your terminal and check the loaded modules:
    
    ```
    lsmod | grep -iE kvm
    ```
    
    ### Understanding the Output
    
    > [!SUCCESS] Scenario A: Modules are Loaded
    > 
    > If the system successfully detected your hardware, you will see output similar to this:
    > 
    > ```
    > kvm_intel             401408  0
    > kvm                  1204224  1 kvm_intel
    > irqbypass             16384   1 kvm
    > ```
    > 
    > _(Note: AMD users will see `kvm_amd` instead of `kvm_intel`)_.
    > 
    > **Action:** If you see this output, you are done! The modern kernel handled it. You can skip the rest of this note.
    
    > [!FAILURE] Scenario B: No Output
    > 
    > If the command returns nothing (a blank line), the modules are not loaded. This usually means **Virtualization is disabled in your BIOS/UEFI**, or you are running a custom kernel without KVM support. Double-check your firmware settings. If firmware is correct, proceed to Step 2 to force-load them.
    
    If `udev` failed to load the modules but your BIOS is configured correctly, we can load them manually into the current session.
    
    Run the command corresponding to your CPU manufacturer:
    
    **For Intel Processors:**
    
    ```
    sudo modprobe kvm_intel
    ```
    
    **For AMD Processors:**
    
    ```
    sudo modprobe kvm_amd
    ```
    
    Verify again with `lsmod | grep kvm`. If it worked, proceed to Step 3 to make it permanent.
    
13. Stop monolothic daemon if active . Stop, disable, and mask the service and ALL legacy sockets
    
    ```
    sudo systemctl stop libvirtd.service libvirtd.socket libvirtd-ro.socket libvirtd-admin.socket libvirtd-tcp.socket libvirtd-tls.socket
    sudo systemctl disable libvirtd.service libvirtd.socket libvirtd-ro.socket libvirtd-admin.socket libvirtd-tcp.socket libvirtd-tls.socket
    sudo systemctl mask libvirtd.service libvirtd.socket libvirtd-ro.socket libvirtd-admin.socket libvirtd-tcp.socket libvirtd-tls.socket
    ```
    
    **First, Enable all sockets to persist across reboots:**
    
    ```
    # Enable the primary modular sockets (including alternative hypervisors)
    for drv in qemu interface network nodedev nwfilter secret storage proxy lxc ch vbox; do \
      sudo systemctl enable virt${drv}d.socket virt${drv}d-ro.socket virt${drv}d-admin.socket; \
    done
    
    # Enable the logging and locking sockets
    for drv in log lock; do \
      sudo systemctl enable virt${drv}d.socket virt${drv}d-admin.socket; \
    done
    ```
    
    **Second, Start the sockets for the current session:**
    
    ```
    # Start the primary modular sockets
    for drv in qemu interface network nodedev nwfilter secret storage proxy lxc ch vbox; do \
      sudo systemctl start virt${drv}d.socket virt${drv}d-ro.socket virt${drv}d-admin.socket; \
    done
    
    # Start the logging and locking sockets
    for drv in log lock; do \
      sudo systemctl start virt${drv}d.socket virt${drv}d-admin.socket; \
    done
    ```
    

## Step 3: Enable Graceful VM Shutdowns (Data Safety)

To prevent your virtual machines from being brutally hard-killed (which corrupts data) when you shut down or reboot your host computer, you must enable the `libvirt-guests` service. This tells systemd to gracefully pause or shut down your VMs when the host turns off.

```
sudo systemctl enable --now libvirt-guests.service
```

> [!IMPORTANT]
> 
> reboot at this point and then run this

## Verification

14. ```
    virt-host-validate
    ```
    
    Once your system has rebooted, you can verify that your new modular setup is working flawlessly.
    
    **. Check if the sockets are listening:**
    
    ```
    systemctl list-sockets | grep virt
    ```
    
    > [!SUCCESS] Expected Output
    > 
    > You should see a long list showing sockets like `virtqemud.socket`, `virtnetworkd.socket`, etc., sitting in the `LISTEN` state. This means the doorways are open and waiting.
    
    Prove the daemons are sleeping:
    
    ```
    systemctl status virtqemud.service
    ```
    
    > [!SUCCESS] Expected Output
    > 
    > It should say `Active: inactive (dead)`.
    > 
    > _This is exactly what we want!_ It proves the daemon is using 0MB of RAM. The moment you open your virtual machine manager or run a `virsh` command, systemd will instantly flip this to `active (running)`.
    
    **Verify Boot Parameters**
    
    Ensure your bootloader (GRUB or systemd-boot) has the correct IOMMU parameters injected at boot.
    
    ```
    cat /proc/cmdline
    ```
    
    > [!TIP] Required Flags
    > 
    > Ensure your boot line includes `iommu=pt`. For Intel systems, explicitly adding `intel_iommu=on` is highly recommended even if the kernel defaults it to on.
    
    **Check Driver Binding**
    
    Query the PCI tree specifically for your GPU's Vendor ID.
    
    ```
    lspci -nnk -d 10de:25a0
    ```
    
    **Expected Output:**
    
    ```
    Kernel driver in use: vfio-pci
    Kernel modules: nouveau, nvidia_drm, nvidia
    ```
    
    _(If the output reads `Kernel driver in use: nvidia`, a race condition occurred and the host grabbed it. Re-check that `modconf` is before `kms` in `mkinitcpio.conf`, and confirm Phase 4's file actually made it into the image: `sudo lsinitcpio /boot/initramfs-linux.img | grep vfio`.)_
    
    **If nothing has bound to it yet** (rare — usually only true on a first attempt with no native driver installed):
    
    ```
    sudo modprobe vfio-pci
    ```
    
    **Check dmesg for VFIO Initialization**
    
    Verify that `vfio-pci` successfully claimed the device and initialized the IOMMU groups during the boot process.
    
    ```
    sudo dmesg | grep -i vfio
    ```
    
    > [!NOTE]- Expected Output
    > 
    > ```
    > [ 6106.148012] VFIO - User Level meta-driver version: 0.3
    > [ 6106.156096] vfio-pci 0000:01:00.0: vgaarb: VGA decodes changed: olddecodes=io+mem,decodes=io+mem:owns=none
    > [ 6106.156248] vfio_pci: add [10de:25a0[ffff:ffff]] class 0x000000/00000000
    > [ 6106.156251] vfio_pci: add [10de:2291[ffff:ffff]] class 0x000000/00000000
    > [ 6124.455667] vfio-pci 0000:01:00.0: enabling device (0000 -> 0003)
    > [ 6124.455753] vfio-pci 0000:01:00.0: resetting
    > ```
    > 
    > The `[ffff:ffff]` wildcard is 4 hex digits, not 8 — it reflects the real 16-bit width of the subvendor/subdevice fields being matched generically, since you didn't specify a subvendor/subdevice qualifier. Exact timestamps and the presence of the `vgaarb` line will vary by hardware; what matters is the `vfio_pci: add` lines showing your exact IDs and the `enabling device` line.
    

## Networking

15. Networking
    
    [[Network Bridging for LAN access]]