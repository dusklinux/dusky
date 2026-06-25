# GPU Isolation and VFIO Binding Guide (Arch Linux)

This guide details the process of **statically isolating** a dedicated NVIDIA GPU on an Arch Linux laptop (with an Intel or AMD iGPU) to prevent the host Linux kernel from touching it. This reserves the GPU exclusively for passthrough to a Virtual Machine (KVM/QEMU).

> [!INFO] Audited for Kernel 7.1 / systemd 260 (June 2026)
> 
> Verified against current Arch Wiki guidance and the Linux 7.0/7.1 changelogs. The binding mechanism here — `vfio-pci`, IOMMU passthrough mode, and module blacklisting — is a long-stable kernel interface that predates the 6.x→7.x version rollover by years and isn't touched by anything in 7.0 or 7.1 (new in-kernel NTFS driver, Rust leaving experimental status, Intel FRED enabled by default, removal of 486-era code). This will keep working unmodified on 7.2 and beyond unless upstream does a ground-up VFIO redesign.

> [!WARNING] CRITICAL: MUX Switch Configuration
> 
> Since this is a laptop with a hardware MUX switch:
> 
> Ensure your BIOS/UEFI is set to **Hybrid Mode (Optimus)** or **iGPU Mode**.
> 
> If you set the MUX to "Discrete/NVIDIA only" and then follow this guide to isolate the NVIDIA card, you will boot into a black screen. The host OS will have no GPU driver available to render the display manager.

## Phase 1: Identification & Preparation

Before modifying the bootloader, we must identify the specific hardware addresses of the discrete GPU and ensure the hardware architecture supports isolated passthrough.

### 1.1 Identify GPU PCI IDs

We need the hex codes (`Vendor:Product`) for the GPU and its associated Audio Controller.

```
lspci -nn | grep -E "NVIDIA|VGA"
```

> [!NOTE]- Example Output
> 
> ```
> 00:02.0 VGA compatible controller [0300]: Intel Corporation Alder Lake-P GT2 [Iris Xe Graphics] [8086:46a6] (rev 0c)
> 01:00.0 VGA compatible controller [0300]: NVIDIA Corporation GA107M [GeForce RTX 3050 Ti Mobile] [10de:25a0] (rev a1)
> 01:00.1 Audio device [0403]: NVIDIA Corporation GA107 High Definition Audio Controller [10de:2291] (rev a1)
> ```

**Analysis of IDs:**

- **3D Controller:** `10de:25a0` (RTX 3050 Ti Mobile)
- **Audio Controller:** `10de:2291` (NVIDIA Audio)
- _Note: Both functions of the card must be isolated._

### 1.2 Check IOMMU Groups (Crucial)

Hardware passthrough requires the GPU to be in its own isolated "IOMMU Group". If it shares a group with vital host components (like the primary NVMe drive or USB controller), isolation will fail without artificially splitting the group.

Run this script to map your IOMMU groups (works in both Bash and Zsh):

```bash
#!/usr/bin/env bash
shopt -s nullglob 2>/dev/null || setopt NULL_GLOB
for g in /sys/kernel/iommu_groups/*; do
    echo "IOMMU Group ${g##*/}:"
    for d in $g/devices/*; do
        echo -e "\t$(lspci -nns ${d##*/})"
    done;
done;
```

> [!NOTE]- What to look for in the output
> 
> Scroll through the output and find the group containing your NVIDIA IDs.
> 
> ```
> IOMMU Group 15:
> 	01:00.0 VGA compatible controller [0300]: NVIDIA Corporation GA107M [GeForce RTX 3050 Ti Mobile] [10de:25a0] (rev a1)
> 	01:00.1 Audio device [0403]: NVIDIA Corporation GA107 High Definition Audio Controller [10de:2291] (rev a1)
> ```
> 
> **Rule of thumb:** Ideally, _only_ the NVIDIA Video and NVIDIA Audio devices should be in that group. If a "PCIe Root Port" or "Bridge" is in the same group, that is structurally normal and completely acceptable.

> [!TIP] If your GPU's group is too crowded
> 
> A handful of consumer boards put the GPU slot in the same IOMMU group as unrelated devices (a SATA controller, other PCIe slots), with no software fix on the standard kernel — the real solution is a different slot or board. The traditional workaround is an **ACS override patch**, which artificially splits IOMMU groups by ignoring the real PCIe Access Control Services topology. It's still actively maintained for current kernels (packaged as `linux-vfio` in the AUR), but it does exactly what its name implies: it overrides isolation the IOMMU was enforcing for a reason, weakening host-to-device DMA isolation. Treat it as a last resort, and prefer a slot or motherboard fix if one exists.

## Phase 2: Bootloader Configuration (Systemd-boot)

We must instruct the Linux Kernel to activate hardware virtualization and reserve the GPU device IDs specifically for the `vfio-pci` driver at the absolute beginning of the boot sequence (Ring 0).

### 2.1 Edit Loader Entry

Open your main Arch boot entry file.

```
sudo nvim /boot/loader/entries/arch-linux.conf
```

### 2.2 Append Kernel Parameters

Append the following arguments to the `options` line (the same line containing `root=...` and `rw`):

_(Ensure `vfio-pci.ids=` matches the exact IDs you pulled in Phase 1)_

```
intel_iommu=on iommu=pt vfio-pci.ids=10de:25a0,10de:2291 module_blacklist=nouveau,nvidia,nvidia_drm,nvidia_modeset,nvidia_uvm
```

**Parameter Breakdown:**

- `intel_iommu=on`: Explicitly enables Intel's IOMMU driver (VT-d), essential for mapping guest memory to physical hardware. _(If using an AMD CPU, change to `amd_iommu=on`)_.
- `iommu=pt`: Sets "Passthrough" mode for the host. The host OS bypasses IOMMU translation for its own devices, yielding better host performance.
- `vfio-pci.ids=xxxx:xxxx,yyyy:yyyy`: Tells the kernel to bind these IDs to `vfio-pci` as early as possible.
- `module_blacklist=...`: An aggressive **in-kernel** parameter. It strictly forbids the kernel module loader from touching the native NVIDIA drivers during early boot, preventing race conditions.

These four parameters aren't new — they're the same ones that have driven static VFIO binding since `vfio-pci` itself landed in the kernel over a decade ago, and nothing in the Linux 7.0 or 7.1 release cycles touched any of them. Read "current" as "still exactly right," not "new in this kernel."

> [!IMPORTANT] Phase 2 alone is not reliable — read Phase 4
> 
> Current Arch documentation carries a specific warning: on some modesetting configurations, an ID supplied only through this kernel command line parameter can be read too late to win the race against early driver probing. The kernel parameter above is still worth setting — it's the earliest possible signal and costs nothing — but the **modprobe.d configuration in Phase 4 is the mechanism actually doing the safety-critical work**, because it gets baked into the initramfs and is applied at the exact moment `vfio_pci` loads. Treat Phase 2 and Phase 4 as both required, not Phase 4 as an optional backup for Phase 2.

> [!DANGER] DUAL-AMD LAPTOP USERS (Read Carefully)
> 
> If your laptop has an AMD CPU with integrated Radeon graphics AND a discrete AMD Radeon GPU:
> 
> **DO NOT** use `module_blacklist=amdgpu,radeon`. Both of your GPUs use the exact same driver. If you blacklist it globally, your host OS will not be able to render the screen. Rely purely on the `vfio-pci.ids=` parameter and the Phase 4 softdeps to grab the discrete card early — `vfio-pci` claims that exact device ID before `amdgpu`'s broader ID table gets a chance to, leaving your iGPU's distinct ID free for `amdgpu` to bind normally.

## Phase 3: Initramfs Configuration

The `initramfs` is the temporary file system loaded into RAM before the root partition mounts. To ensure `vfio-pci` grabs the GPU before the display manager starts, its modules must be baked into this image.

### 3.1 Edit mkinitcpio.conf

```
sudo nvim /etc/mkinitcpio.conf
```

### 3.2 Update MODULES

Add the core VFIO framework to the `MODULES` array, ahead of any native GPU driver. Current Arch Wiki guidance lists `vfio_pci` before `vfio` and `vfio_iommu_type1` — keep that order; `mkinitcpio` resolves the real module dependency graph through `modprobe` regardless of how you order the array, so this is a documented convention, not a load-bearing detail.

```
MODULES=(i915 btrfs vfio_pci vfio vfio_iommu_type1)
```

_(AMD iGPU users: substitute `amdgpu` for `i915`.)_

> [!WARNING] Why `i915` (or `amdgpu`) is in that list
> 
> Since kernel 6.0, framebuffer/console output freezes the moment the VFIO modules load and stays frozen until a real GPU driver takes over. If you use full-disk encryption, this can hide your LUKS password prompt entirely, making the system look hung when it isn't. Loading your iGPU's own driver ahead of the VFIO chain keeps the console usable. This has zero effect on the NVIDIA isolation logic — `i915`/`amdgpu` only match Intel/AMD vendor IDs, never the NVIDIA device you're isolating.

> [!INFO] You will not see `vfio_virqfd` in this list, and that's correct
> 
> Older guides (and plenty of search results you'll find) tell you to add a fourth module, `vfio_virqfd`, to this array. As of kernel 6.2, that functionality was folded directly into the base `vfio` module — there is no standalone `vfio_virqfd` module on kernel 7.x to load. If you copy a MODULES line from an older tutorial that includes it, delete it; `modprobe` will simply fail to find it.
> 
> Separately: `vfio`, `vfio_pci`, and `vfio_iommu_type1` ship as genuine loadable modules (`=m`) in Arch's official kernel package, not built into the kernel core. You should *not* see "module not found" warnings for any of the three listed above when you regenerate the initramfs in Phase 5 — if you do, something else is wrong (mismatched kernel/headers, a custom kernel config, etc.), so don't dismiss it as expected noise.

### 3.3 Verify HOOKS

Ensure your systemd-based initramfs configuration includes `modconf`. This hook ensures the modprobe rules we create in Phase 4 are packaged into the initramfs.

_(Crucial: `modconf` MUST appear before `kms`, so the blacklist/softdep rules are already active before `kms` autodetection decides which graphics modules to pull in.)_
```ini
HOOKS=(base systemd autodetect microcode modconf kms keyboard sd-vconsole block filesystems fsck)
```

or 

```
HOOKS=(systemd autodetect microcode modconf kms keyboard sd-vconsole block filesystems)
```

> [!TIP] Already on the modern microcode hook? Check arch.conf too
> 
> The `microcode` hook above replaced the older setup where CPU microcode was loaded from a *separate* `initrd` line in your loader entry alongside a now-deprecated `ALL_microcode=` preset option. If `/boot/loader/entries/arch.conf` still has a leftover line like `initrd /intel-ucode.img` or `initrd /amd-ucode.img` above your main `initrd` line, it's harmless but redundant now — the `microcode` hook already bakes it into the main image. Worth deleting while you're already in that file for Phase 2.

## Phase 4: Modprobe Rules (Where the Isolation Actually Happens)

Kernel parameters set the earliest possible intent, but as flagged in Phase 2, the `modprobe.d` rules below are what reliably wins the race against native drivers on current kernels — particularly on any configuration doing early modesetting. This isn't just a secondary layer; it's the mechanism you should expect to be doing the real work.

### 4.1 Create VFIO Config

```
sudo nvim /etc/modprobe.d/vfio.conf
```

### 4.2 Add Configuration

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

> [!NOTE]- Optional: `disable_vga=1`
> 
> If your particular hardware ever makes the NVIDIA GPU the primary/boot VGA device (not the case in the standard hybrid-MUX setup this guide assumes, where the iGPU stays primary), you can append `disable_vga=1` to the `options vfio-pci ids=...` line to stop the kernel's VGA arbiter from fighting over legacy VGA decode resources. Leave it off unless you hit that specific symptom.

## Phase 5: Regeneration

We must now rebuild the initramfs image to incorporate the `mkinitcpio.conf` and `modprobe.d` changes.

```
sudo mkinitcpio -P
```

Action: Reboot the system to apply **the new boot sequence.**

```
systemctl reboot
```

## Phase 6: Verification

Upon reboot, verify the isolation succeeded and the host kernel has relinquished the hardware.

### 6.1 Check Driver Binding

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

### 6.2 Check dmesg for VFIO Initialization

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

## Phase 7: Manual Binding (Troubleshooting)

If `vfio-pci` never claimed the device (e.g. you booted a fallback image that skipped `modconf`), what you need depends on whether anything else has already grabbed the GPU.

**If nothing has bound to it yet** (rare — usually only true on a first attempt with no native driver installed):

```
sudo modprobe vfio-pci
```

**If nouveau, nvidia, or another driver already owns it** (the far more common case), loading the module alone won't move it — you need to unbind the device from its current driver, force `vfio-pci` as the only candidate, and re-probe:

```
# Replace 0000:01:00.0 with your GPU's actual PCI bus address from `lspci`
echo "0000:01:00.0" | sudo tee /sys/bus/pci/devices/0000:01:00.0/driver/unbind
echo "vfio-pci" | sudo tee /sys/bus/pci/devices/0000:01:00.0/driver_override
echo "0000:01:00.0" | sudo tee /sys/bus/pci/drivers_probe
```

Repeat for the audio function (`0000:01:00.1`) if it's also still bound elsewhere. This is the same `driver_override` sysfs mechanism the kernel has exposed for exactly this purpose since it was added to the PCI subsystem, and it's what underlies most of the automated vfio-bind scripts you'll find in the wild.

## Appendix: Future Scenarios

### Reverting (Undoing Isolation)

To return the discrete GPU to the Linux host (e.g., for native rendering or CUDA compute):

1. Remove `vfio-pci.ids=...` and `module_blacklist=...` from `/boot/loader/entries/arch.conf`.
2. Delete or comment out the contents of `/etc/modprobe.d/vfio.conf`.
3. Regenerate the initramfs: `sudo mkinitcpio -P`.
4. Reboot the system.

### If You Want the GPU Back on Host Sometimes (Without Editing Files Each Time)

Everything above is the **static** method: the GPU is permanently reserved for `vfio-pci` from the moment the kernel boots, full stop. If you expect to regularly switch the GPU back to the host for things like local CUDA work between VM sessions, current Arch documentation also describes a **dynamic** method: skip the kernel-parameter and blacklist steps, leave the native driver free to load normally, and instead run a small script (built around the same `driver_override` / `unbind` / `drivers_probe` sequence from Phase 7) right before starting the VM, then run it in reverse afterward. It trades a few seconds of scripting at VM start/stop for not needing a reboot to switch which side owns the card — worth knowing about if the static approach in this guide ever feels too rigid for your workflow.

### Looking Ahead, Briefly

Everything in this guide stops at the point where the host kernel hands the device to `vfio-pci` — that's the actual scope of "isolation." When you move on to wiring the device into an actual QEMU VM, be aware the ecosystem is mid-migration: QEMU's older VFIO device backend works through the legacy `vfio_iommu_type1` container API (still loaded here, still fully supported), but newer QEMU versions can instead use the kernel's `iommufd` interface, which is the direction upstream is heading for device-passthrough memory management. Nothing here needs to change for that — it's a detail of how you'll eventually configure the VM side, not the host-side binding this guide covers — but it's worth knowing the name so you're not confused when you see it in QEMU's newer documentation.

---

**Sources consulted during this audit:**

- Arch Wiki — *PCI passthrough via OVMF* (and Talk page)
- Arch Linux News — *mkinitcpio hook migration and early microcode*
- kernel.org — *IOMMUFD* userspace API documentation
- Linux kernel mailing list patches introducing the PCI `driver_override` sysfs mechanism
- kernelnewbies.org and LWN.net summaries of the Linux 7.0 and 7.1 release cycles
- Arch Linux package database (`linux` core package)
- Phoronix and the systemd project's release history, for systemd 260
