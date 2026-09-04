#!/usr/bin/env python3
"""
Systemd-Boot Kernel Manager - Advanced Dusky TUI Schema.
Provides dynamic multi-kernel switching, granular cmdline parameter tuning,
direct renaming for any installed kernel entry, and EFI maintenance actions.
"""
import os
import sys
from pathlib import Path

_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem
from python.engines.systemd_boot import discover_all_kernels_and_entries, clean_kernel_name, slugify_entry_key

# =============================================================================
# 1. DYNAMIC KERNEL & BOOT ENTRY DISCOVERY
# =============================================================================
def discover_kernel_options() -> tuple[list[str], list[str]]:
    """
    Dynamically scans all available kernel packages, boot entries, and mkinitcpio presets.
    Returns:
        (options, hints) aligned for the PickerModal dialog.
    """
    entries_map = discover_all_kernels_and_entries()
    
    stock_options: list[str] = []
    stock_hints: list[str] = []
    
    custom_options: list[str] = []
    custom_hints: list[str] = []
    
    # 1. Stock Arch Linux (always pinned first as default)
    if "Arch Linux" in entries_map:
        stock_options.append("Arch Linux")
        stock_hints.append(entries_map["Arch Linux"].get("hint") or "Stock Arch Linux kernel (/boot/vmlinuz-linux)")
    else:
        stock_options.append("Arch Linux")
        stock_hints.append("Stock Arch Linux kernel (/boot/vmlinuz-linux)")
        
    if "Arch Linux (Fallback)" in entries_map:
        stock_options.append("Arch Linux (Fallback)")
        stock_hints.append(entries_map["Arch Linux (Fallback)"].get("hint") or "Arch Linux fallback initramfs (recovery mode)")

    # 2. Custom kernels (sorted alphabetically)
    for name in sorted(entries_map.keys()):
        if name in ("Arch Linux", "Arch Linux (Fallback)") or name.startswith("@"):
            continue
        meta = entries_map[name]
        hint = meta.get("hint") or meta.get("title") or f"Custom kernel {name}"
        custom_options.append(name)
        custom_hints.append(hint)

    # 3. Special systemd-boot targets
    special_options = ["@saved", "@default", "@latest"]
    special_hints = [
        "Automatically boots the last chosen kernel from the previous boot",
        "Boots the UEFI firmware standard default entry",
        "Automatically boots the newest installed kernel by version"
    ]

    all_options = stock_options + custom_options + special_options
    all_hints = stock_hints + custom_hints + special_hints
    return all_options, all_hints


def build_entry_override_items() -> list[ConfigItem]:
    """Generates direct title renaming items for all discovered boot entries."""
    entries_map = discover_all_kernels_and_entries()
    items: list[ConfigItem] = []
    
    for name in sorted(entries_map.keys(), key=lambda n: (0 if n == "Arch Linux" else 1 if n == "Arch Linux (Fallback)" else 2, n)):
        if name.startswith("@"):
            continue
        slug = slugify_entry_key(name)
        meta = entries_map[name]
        file_hint = meta.get("entry_file") or name
        items.append(
            ConfigItem(
                label=f"Rename: {name}",
                key=slug,
                scope="ENTRY_OVERRIDE",
                type_="string",
                default=name,
                group="Rename Installed Boot Entries",
                extended_help=(
                    f"**Rename `{name}` Boot Menu Title**\n\n"
                    f"Modifies the `title` line in the `{file_hint}` entry file.\n"
                    f"This controls the exact text displayed for this kernel on the systemd-boot menu."
                )
            )
        )
    return items


_INITIAL_KERNEL_OPTIONS, _INITIAL_KERNEL_HINTS = discover_kernel_options()
_INITIAL_TARGET_OPTIONS = ["Auto (Follows Default Kernel)"] + [opt for opt in _INITIAL_KERNEL_OPTIONS if not opt.startswith("@")]


def generate_cpu_isolation_presets() -> tuple[list[str], list[str]]:
    """
    Dynamically generates sensible isolcpus and nohz_full presets based on
    the detected online/present CPU hardware topology.
    Works generically across any CPU architecture (x86_64, aarch64, riscv, etc.)
    and core counts (from 2 cores up to 128+ cores).
    """
    count = os.cpu_count() or 4
    max_idx = count - 1

    isol = ["unset", "0"]
    if count >= 2:
        isol.append("1")
    if count >= 4:
        isol.append("0,1")
        mid = count // 2
        if mid - 1 > 0:
            isol.append(f"0-{mid - 1}")
        if mid < max_idx:
            isol.append(f"{mid}-{max_idx}")
    if count > 2:
        isol.append(f"1-{max_idx}")
    if count >= 8:
        quarter = count // 4
        if count - quarter < max_idx:
            isol.append(f"{count - quarter}-{max_idx}")

    isol.append("domain,0")
    isol.append("nohz,domain,0")
    if count >= 4:
        mid = count // 2
        isol.append(f"managed_irq,domain,0-{mid - 1}")

    dedup_isol = list(dict.fromkeys(isol))

    nohz = ["unset"]
    if count >= 2:
        nohz.append("1")
    if count >= 4:
        mid = count // 2
        if mid - 1 > 1:
            nohz.append(f"1-{mid - 1}")
        if mid < max_idx:
            nohz.append(f"{mid}-{max_idx}")
    if count > 2:
        nohz.append(f"1-{max_idx}")
    if count >= 8:
        quarter = count // 4
        if count - quarter < max_idx:
            nohz.append(f"{count - quarter}-{max_idx}")

    dedup_nohz = list(dict.fromkeys(nohz))
    return dedup_isol, dedup_nohz


_INITIAL_ISOLCPUS_OPTIONS, _INITIAL_NOHZ_FULL_OPTIONS = generate_cpu_isolation_presets()

# =============================================================================
# 2. CORE APPLICATION ROUTING & ENVIRONMENT
# =============================================================================
ENGINE_TYPE = "systemd_boot"                       
TARGET_FILE = "/boot/loader/entries/arch-linux.conf"           
APP_TITLE = "Systemd-Boot Kernel Manager"         
REQUIRE_ROOT = True

DEFAULT_MODE = "batch"                        
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"

ENABLE_USER_PRESETS = True                   
USER_PRESETS_TAB = "Presets"

TAB_NOTICES = {
    0: {"level": "warning", "message": "Modifications to root or LUKS storage parameters can render the system unbootable. Proceed with caution."},
    4: {"level": "info", "message": "Configures global /boot/loader/loader.conf (default kernel selection, timeout, resolution, security)."},
    5: {"level": "info", "message": "Rename any installed kernel entry, inspect active entry metadata, and execute EFI maintenance."},
}

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = [
    "Boot & Root",
    "Performance",
    "Hardware & Graphics",
    "Security & Debug",
    "systemd-boot Loader",
    "Boot Entry Metadata",
    "Presets"
]

# =============================================================================
# 4. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: BOOT & ROOT
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Root Partition",
            key="root",
            scope="DEFAULT",
            type_="string",
            default="unset",
            group="Root Filesystem",
            extended_help="**Root Device**\n\nSpecifies the device to be used as the root file system (e.g., `/dev/sda1`, `UUID=...`, or `/dev/mapper/cryptroot`)."
        ),
        ConfigItem(
            label="Root FS Type",
            key="rootfstype",
            scope="DEFAULT",
            type_="picker",
            options=["unset", "btrfs", "ext4", "xfs", "f2fs", "vfat"],
            hints=["Auto-detect", "Btrfs with subvolumes", "Ext4 filesystem", "XFS filesystem", "Flash-Friendly FS", "FAT/EFI"],
            default="unset",
            group="Root Filesystem",
            extended_help="**Root File System Type**\n\nExplicitly defines the file system type of the root partition, bypassing auto-detection to speed up boot."
        ),
        ConfigItem(
            label="Root Flags",
            key="rootflags",
            scope="DEFAULT",
            type_="string",
            default="unset",
            group="Root Filesystem",
            extended_help="**Root Mount Options**\n\nComma-separated mount options applied to the root filesystem (e.g., `subvol=/@,noatime,compress=zstd:3`)."
        ),
        ConfigItem(
            label="Mount Read-Write",
            key="rw",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Root Filesystem",
            extended_help="**Read-Write Mount**\n\nMounts the root device initially as read-write. Required by systemd init systems."
        ),
        ConfigItem(
            label="Mount Read-Only",
            key="ro",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Root Filesystem",
            extended_help="**Read-Only Mount**\n\nMounts the root device initially as read-only. The init system remounts it read-write later."
        ),
        ConfigItem(
            label="Root Delay (s)",
            key="rootdelay",
            scope="DEFAULT",
            type_="picker",
            options=["unset", "1", "3", "5", "10"],
            hints=["No extra delay", "1 second delay", "3 seconds delay", "5 seconds delay", "10 seconds delay"],
            default="unset",
            group="Root Filesystem",
            extended_help="**Root Device Wait Delay**\n\nPauses boot for N seconds before attempting to mount root, allowing slow USB or NVMe controllers to initialize."
        ),
        ConfigItem(
            label="Root Wait",
            key="rootwait",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Root Filesystem",
            extended_help="**Wait for Root Device Indefinitely**\n\nWaits indefinitely for the root device to appear. Essential for asynchronous NVMe and external storage."
        ),
        ConfigItem(
            label="LUKS Crypt Device",
            key="rd.luks.name",
            scope="DEFAULT",
            type_="string",
            default="unset",
            group="Encryption",
            extended_help="**Dracut / Mkinitcpio LUKS Name**\n\nMaps a LUKS UUID to a mapped device name (e.g., `be1ac50d-...=cryptroot`)."
        ),
        ConfigItem(
            label="LUKS UUID",
            key="rd.luks.uuid",
            scope="DEFAULT",
            type_="string",
            default="unset",
            group="Encryption",
            extended_help="**Dracut / Systemd LUKS UUID**\n\nSpecifies the UUID of the encrypted LUKS root partition to unlock during early boot."
        ),
        ConfigItem(
            label="LUKS Options",
            key="rd.luks.options",
            scope="DEFAULT",
            type_="string",
            default="unset",
            group="Encryption",
            extended_help="**Dracut LUKS Options**\n\nComma-separated list of options for LUKS (e.g., `discard` to enable TRIM support on SSDs)."
        ),
        ConfigItem(
            label="Resume Device",
            key="resume",
            scope="DEFAULT",
            type_="string",
            default="unset",
            group="Hibernation",
            extended_help="**Hibernation Resume Partition**\n\nSpecifies the swap partition or UUID used to resume from hibernation (e.g., `UUID=...`)."
        ),
        ConfigItem(
            label="Resume Offset",
            key="resume_offset",
            scope="DEFAULT",
            type_="string",
            default="unset",
            group="Hibernation",
            extended_help="**Hibernation Swapfile Offset**\n\nPhysical block offset of a swapfile within a Btrfs or Ext4 root partition for hibernation resume."
        ),
        ConfigItem(
            label="FSCK Mode",
            key="fsck.mode",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "auto", "skip", "force"],
            default="unset",
            group="Boot Process",
            extended_help="**File System Check Mode**\n\nControls when `fsck` is executed on root file systems at boot time.\n\n- `auto`: Standard automatic check.\n- `skip`: Skips checking root entirely (speeds up boot).\n- `force`: Always checks root."
        ),
        ConfigItem(
            label="Splash Screen",
            key="splash",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Boot Process",
            extended_help="**Boot Splash**\n\nEnables the graphical boot splash screen (e.g., Plymouth) to provide a smooth transition into your desktop."
        ),
        ConfigItem(
            label="Disable OEM Logo",
            key="bgrt_disable",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Boot Process",
            extended_help="**Disable UEFI BGRT Logo**\n\nDisables the UEFI Boot Graphics Resource Table OEM firmware bitmap display."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: PERFORMANCE
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Mitigations",
            key="mitigations",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "auto", "off"],
            default="unset",
            group="CPU & Scheduler",
            extended_help="**CPU Vulnerability Mitigations**\n\nControls optional mitigations for CPU side-channel vulnerabilities (Spectre, Meltdown, Retbleed).\n\n- `auto`: Standard kernel defaults.\n- `off`: Disables CPU mitigations for maximum gaming and compute performance."
        ),
        ConfigItem(
            label="Intel P-State",
            key="intel_pstate",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "disable", "passive", "active", "force"],
            default="unset",
            group="CPU & Scheduler",
            extended_help="**Intel Frequency Scaling**\n\nConfigures the hardware P-State scaling driver for Intel processors.\n\n- `active`: Fully hardware-controlled autonomous scaling (EPP).\n- `passive`: Allows user-space tools finer control."
        ),
        ConfigItem(
            label="AMD P-State",
            key="amd_pstate",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "active", "passive", "guided", "disable"],
            default="unset",
            group="CPU & Scheduler",
            extended_help="**AMD Frequency Scaling**\n\nConfigures precision boost scaling driver for modern AMD Ryzen processors.\n\n- `active`: Fully hardware-controlled autonomous scaling (Recommended for Zen 2+)."
        ),
        ConfigItem(
            label="Preemption Model",
            key="preempt",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "lazy", "full", "voluntary", "none"],
            default="unset",
            group="CPU & Scheduler",
            extended_help="**Dynamic Kernel Preemption (PREEMPT_DYNAMIC)**\n\nControls runtime preemption in Linux 6.x/7.x.\n\n- `lazy`: (Recommended in Linux 7.0+) Hybrid lazy preemption (`CONFIG_PREEMPT_LAZY`). Real-time tasks preempt immediately while throughput tasks finish time-slices.\n- `full`: Classic full preemption where all tasks preempt immediately.\n- `voluntary`: Balances desktop responsiveness and throughput."
        ),
        ConfigItem(
            label="Split Lock Mitigate",
            key="split_lock_mitigate",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "0", "1"],
            default="unset",
            group="CPU & Scheduler",
            extended_help="**Split Lock Penalty Mitigation**\n\n- `0`: Disables split lock bus penalties, preventing frame drops in gaming and Windows emulators.\n- `1`: Enables kernel split lock mitigation."
        ),
        ConfigItem(
            label="Thread IRQs",
            key="threadirqs",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="CPU & Scheduler",
            extended_help="**Threaded Interrupts**\n\nForces hardware interrupt handlers to execute inside kernel threads, improving real-time audio and input latency."
        ),
        ConfigItem(
            label="Max C-State",
            key="processor.max_cstate",
            scope="DEFAULT",
            type_="picker",
            options=["unset", "1", "2", "3", "4", "5", "6"],
            hints=["Unset (Auto)", "C1 (Lowest latency)", "C2", "C3", "C4", "C5", "C6 (Deepest power save)"],
            default="unset",
            group="CPU & Scheduler",
            extended_help="**Processor Max C-State**\n\nLimits the deepest sleep C-state the CPU can enter. Limiting to C1 minimizes wake-up latency for pro-audio and gaming."
        ),
        ConfigItem(
            label="Isolated CPUs",
            key="isolcpus",
            scope="DEFAULT",
            type_="string",
            options=_INITIAL_ISOLCPUS_OPTIONS,
            default="unset",
            group="CPU & Scheduler",
            extended_help=(
                "**CPU Core Isolation (`isolcpus`)**\n\n"
                "Removes specified CPU cores from the general kernel scheduler load-balancing domain.\n"
                "Isolated cores will never run user-space tasks or background processes by default unless tasks are explicitly assigned to them using `taskset -c <cpus>` or cgroups.\n\n"
                "**Common Configurations & Presets:**\n"
                "- `unset`: Normal scheduling (all cores participate in load balancing).\n"
                "- `0`: Isolates Core 0 (keeps user apps off the bootstrap core; dedicates it to kernel interrupts).\n"
                "- `0,1` or `0-3`: Isolates a specific list or range of cores.\n"
                "- Upper core ranges: Isolates specific high-index cores for dedicated background tasks, audio, or VMs.\n"
                "- `nohz,domain,<cores>`: Disables ticks and scheduling domains on specified cores for ultra-low latency.\n"
                "- `managed_irq,domain,<cores>`: Fully isolates cores from both managed IRQs and scheduling domains.\n\n"
                "*Note:* Hardware-agnostic. Presets adapt dynamically to detected CPU threads, and you can freely type any custom core range or comma-separated list (e.g. `1,3,5`, `2-7`, `0-1,4-5`)."
            )
        ),
        ConfigItem(
            label="Full Tickless Cores",
            key="nohz_full",
            scope="DEFAULT",
            type_="string",
            options=_INITIAL_NOHZ_FULL_OPTIONS,
            default="unset",
            group="CPU & Scheduler",
            extended_help=(
                "**Full Tickless Cores (`nohz_full`)**\n\n"
                "Stops the periodic kernel timer tick on specified cores when only one runnable task is active.\n"
                "Drastically minimizes timer interrupt jitter for real-time computing, gaming, and emulators.\n\n"
                "- `unset`: Normal tickless-idle (`CONFIG_NO_HZ_IDLE`).\n"
                "- `<cores>`: List or range of cores (e.g. `1`, `1-3`, `4-7`, `1-15`).\n\n"
                "*Note:* Core 0 cannot be tickless because the kernel requires at least one core to maintain global timekeeping ticks."
            )
        ),
        ConfigItem(
            label="Memory Limit",
            key="mem",
            scope="DEFAULT",
            type_="string",
            options=["unset", "6G", "8G", "12G", "16G", "24G", "32G", "48G", "64G"],
            default="unset",
            group="Memory & Swapping",
            extended_help="**RAM Allocation Limit**\n\nForces the kernel to use a specific maximum amount of memory (e.g., `16G`). Useful for testing low-memory conditions or reserving hardware RAM."
        ),
        ConfigItem(
            label="ZSwap Enabled",
            key="zswap.enabled",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "0", "1"],
            default="unset",
            group="Memory & Swapping",
            extended_help="**ZSwap Compression**\n\nIntercepts swapped memory pages and compresses them into a dynamic RAM pool.\n\n- `1`: Enables ZSwap, greatly improving responsiveness under heavy memory pressure."
        ),
        ConfigItem(
            label="Trans. Hugepages",
            key="transparent_hugepage",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "always", "madvise", "never"],
            default="unset",
            group="Memory & Swapping",
            extended_help="**Transparent Hugepages (THP)**\n\nAllocates memory in larger blocks to reduce TLB cache misses.\n\n- `always`: Enabled for all processes.\n- `madvise`: Enabled only for applications requesting hugepages (Recommended)."
        ),
        ConfigItem(
            label="NUMA Balancing",
            key="numa_balancing",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "enable", "disable"],
            default="unset",
            group="Memory & Swapping",
            extended_help="**Automatic NUMA Balancing**\n\nOptimizes thread and memory placement on multi-node NUMA architectures."
        ),
        ConfigItem(
            label="Init on Alloc",
            key="init_on_alloc",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "0", "1"],
            default="unset",
            group="Memory & Swapping",
            extended_help="**Zero Memory on Allocation**\n\n- `0`: Disables memory zeroing on alloc for higher throughput.\n- `1`: Enables zeroing for security."
        ),
        ConfigItem(
            label="Init on Free",
            key="init_on_free",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "0", "1"],
            default="unset",
            group="Memory & Swapping",
            extended_help="**Zero Memory on Free**\n\n- `0`: Disables memory zeroing on free for higher throughput.\n- `1`: Enables zeroing for security."
        ),
        ConfigItem(
            label="SLUB Debug",
            key="slub_debug",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "0", "1"],
            default="unset",
            group="Kernel Diagnostics",
            extended_help="**SLUB Allocator Debugging**\n\n- `0`: Disables SLUB debugging, saving kernel memory and CPU cycles.\n- `1`: Enables SLUB allocator debugging."
        ),
        ConfigItem(
            label="Disable IPv6",
            key="ipv6.disable",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "0", "1"],
            default="unset",
            group="Kernel Diagnostics",
            extended_help="**IPv6 Support**\n\n- `1`: Disables the IPv6 network stack.\n- `0`: Leaves IPv6 enabled."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: HARDWARE & GRAPHICS
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Nvidia DRM Modeset",
            key="nvidia-drm.modeset",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "1", "0"],
            default="unset",
            group="Graphics",
            extended_help="**NVIDIA Direct Rendering Manager**\n\nRequired for Wayland compositors (Hyprland, Sway) to function on proprietary NVIDIA drivers.\n\n- `1`: Enables kernel modesetting (Required for Wayland)."
        ),
        ConfigItem(
            label="Nvidia Preserve VRAM",
            key="nvidia.NVreg_PreserveVideoMemoryAllocations",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "1", "0"],
            default="unset",
            group="Graphics",
            extended_help="**NVIDIA Video Memory Allocation Preservation**\n\nPreserves video memory across system suspend and hibernate cycles on NVIDIA GPUs under Wayland."
        ),
        ConfigItem(
            label="AMDGPU PP Feature Mask",
            key="amdgpu.ppfeaturemask",
            scope="DEFAULT",
            type_="string",
            default="unset",
            group="Graphics",
            extended_help="**AMD GPU Powerplay Mask**\n\nUnlocks overclocking and undervolting capabilities on AMD graphics cards (e.g., `0xffffffff`)."
        ),
        ConfigItem(
            label="AMDGPU SG Display",
            key="amdgpu.sg_display",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "0", "1"],
            default="unset",
            group="Graphics",
            extended_help="**AMD Scatter-Gather Display**\n\n- `0`: Disables scatter-gather display to resolve screen flickering on certain APU/GPU combinations."
        ),
        ConfigItem(
            label="AMDGPU Display Core",
            key="amdgpu.dc",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "1", "0"],
            default="unset",
            group="Graphics",
            extended_help="**AMD Display Core (DC)**\n\n- `1`: Enables Display Core driver for modern multi-monitor and FreeSync support."
        ),
        ConfigItem(
            label="Intel GuC/HuC",
            key="i915.enable_guc",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "2", "3"],
            default="unset",
            group="Graphics",
            extended_help="**Intel Graphics Microcontrollers**\n\nEnables advanced video hardware acceleration and power management on Intel GPUs.\n\n- `3`: Enables both GuC and HuC."
        ),
        ConfigItem(
            label="Intel IOMMU",
            key="intel_iommu",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "on", "off", "igfx_off"],
            default="unset",
            group="IOMMU",
            extended_help="**Intel IOMMU / VT-d**\n\n- `on`: Enables VT-d for PCIe virtualization passthrough (VFIO)."
        ),
        ConfigItem(
            label="AMD IOMMU",
            key="amd_iommu",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "on", "off", "fullflush", "force_isolation"],
            default="unset",
            group="IOMMU",
            extended_help="**AMD IOMMU / AMD-Vi**\n\n- `on`: Enables AMD-Vi for PCIe virtualization passthrough (VFIO)."
        ),
        ConfigItem(
            label="IOMMU Mode",
            key="iommu",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "pt", "off", "force"],
            default="unset",
            group="IOMMU",
            extended_help="**Generic IOMMU Passthrough**\n\n- `pt`: Identity-mapped passthrough mode. Optimizes host DMA performance while enabling VM device passthrough."
        ),
        ConfigItem(
            label="PCIE ASPM",
            key="pcie_aspm",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "default", "force", "off"],
            default="unset",
            group="Power Management",
            extended_help="**Active State Power Management**\n\n- `force`: Forces PCIe power savings.\n- `off`: Disables ASPM to eliminate latency spikes."
        ),
        ConfigItem(
            label="USB Autosuspend",
            key="usbcore.autosuspend",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "-1", "1"],
            default="unset",
            group="Power Management",
            extended_help="**USB Core Autosuspend**\n\n- `-1`: Disables USB autosuspend, preventing audio DACs and external mice from disconnecting."
        ),
        ConfigItem(
            label="Cursor Default",
            key="vt.global_cursor_default",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "0", "1"],
            default="unset",
            group="Console",
            extended_help="**TTY Global Cursor**\n\n- `0`: Hides the blinking cursor on raw TTY during boot.\n- `1`: Shows blinking cursor."
        ),
        ConfigItem(
            label="Console Blank (s)",
            key="consoleblank",
            scope="DEFAULT",
            type_="picker",
            options=["unset", "0", "60", "300", "600", "900", "1800", "3600"],
            hints=["Default (10m)", "Disabled (Never blank)", "1 minute", "5 minutes", "10 minutes", "15 minutes", "30 minutes", "1 hour"],
            default="unset",
            group="Console",
            extended_help="**TTY Screen Blanking Timeout**\n\nSeconds of inactivity before virtual TTY blanks screen."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 3: SECURITY & DEBUG
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="AppArmor",
            key="apparmor",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "0", "1"],
            default="unset",
            group="Security",
            extended_help="**AppArmor MAC**\n\nMandatory Access Control security module.\n\n- `1`: Enables AppArmor.\n- `0`: Disables AppArmor."
        ),
        ConfigItem(
            label="SELinux",
            key="selinux",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "0", "1"],
            default="unset",
            group="Security",
            extended_help="**SELinux MAC**\n\nSecurity-Enhanced Linux module.\n\n- `1`: Enables SELinux.\n- `0`: Disables SELinux."
        ),
        ConfigItem(
            label="Audit Subsystem",
            key="audit",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "0", "1"],
            default="unset",
            group="Security",
            extended_help="**Kernel Audit Subsystem**\n\n- `1`: Enables audit log generation.\n- `0`: Disables auditing to reduce log spam and overhead."
        ),
        ConfigItem(
            label="Kernel Lockdown",
            key="lockdown",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "none", "integrity", "confidentiality"],
            default="unset",
            group="Security",
            extended_help="**Kernel Lockdown Mode**\n\nRestricts direct root access to kernel memory and hardware ports."
        ),
        ConfigItem(
            label="Systemd Boot Status",
            key="systemd.show_status",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "auto", "yes", "no", "error"],
            default="unset",
            group="Logging",
            extended_help="**Systemd Early Boot Status**\n\nControls systemd service status printing during startup."
        ),
        ConfigItem(
            label="Quiet Boot",
            key="quiet",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Logging",
            extended_help="**Quiet Mode**\n\nSuppresses kernel initialization logs for a clean boot screen."
        ),
        ConfigItem(
            label="Kernel Log Level",
            key="loglevel",
            scope="DEFAULT",
            type_="picker",
            options=["unset", "0", "1", "2", "3", "4", "5", "6", "7"],
            hints=["Unset (Default)", "0: Emergency only", "1: Alert", "2: Critical", "3: Errors only (Desktop)", "4: Warnings", "5: Notice", "6: Informational", "7: Debug (Verbose)"],
            default="unset",
            group="Logging",
            extended_help="**Console Loglevel**\n\nControls console message verbosity threshold (3 = normal errors only, 7 = full debug)."
        ),
        ConfigItem(
            label="Udev Log Level",
            key="rd.udev.log_level",
            scope="DEFAULT",
            type_="picker",
            options=["unset", "0", "3", "4", "7"],
            hints=["Unset", "0: Silent", "3: Errors only", "4: Warnings", "7: Debug"],
            default="unset",
            group="Logging",
            extended_help="**Dracut/Udev Early Boot Loglevel**\n\nLimits udev event verbosity in initial ramdisk phase."
        ),
        ConfigItem(
            label="Ignore Loglevel",
            key="ignore_loglevel",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Logging",
            extended_help="**Force All Kernel Messages**\n\nForces all kernel printks to console regardless of loglevel. Essential for diagnosing boot hangs."
        ),
        ConfigItem(
            label="Always Enable SysRq",
            key="sysrq_always_enabled",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Recovery",
            extended_help="**Magic SysRq Key**\n\nEnables all Magic SysRq emergency functions (Alt+SysRq+<Key>) to safely sync and reboot frozen systems (REISUB)."
        ),
        ConfigItem(
            label="Disable Watchdog",
            key="nowatchdog",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Recovery",
            extended_help="**NMI Watchdog**\n\nDisables hardware watchdog timer interrupts to improve battery life and reduce latency."
        ),
        ConfigItem(
            label="Panic Timeout (s)",
            key="panic",
            scope="DEFAULT",
            type_="picker",
            options=["unset", "-1", "0", "5", "10", "30", "60"],
            hints=["Unset (Halt)", "Reboot immediately", "Halt forever", "5 seconds", "10 seconds", "30 seconds", "60 seconds"],
            default="unset",
            group="Recovery",
            extended_help="**Reboot on Kernel Panic**\n\nTimeout in seconds before rebooting automatically after a panic."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 4: SYSTEMD-BOOT LOADER (/boot/loader/loader.conf)
    # -------------------------------------------------------------------------
    4: [
        ConfigItem(
            label="Default Entry",
            key="default",
            scope="LOADER",
            type_="picker",
            options=_INITIAL_KERNEL_OPTIONS,
            hints=_INITIAL_KERNEL_HINTS,
            default="Arch Linux",
            group="Default Kernel",
            extended_help="**Default Boot Entry (`default` in loader.conf)**\n\nSelects the default kernel or entry to boot in systemd-boot.\n\n- `Arch Linux`: Boots the standard Arch Linux stock kernel.\n- `Dusky ...`: Boots your custom Dusky kernel profile.\n- `@saved`: Automatically boots the last chosen kernel from the previous boot.\n- `@latest`: Automatically boots the newest installed kernel by version.\n- `@default`: Boots the firmware standard default entry."
        ),
        ConfigItem(
            label="Menu Timeout",
            key="timeout",
            scope="LOADER",
            type_="picker",
            options=["0", "1", "2", "3", "4", "5", "10", "menu-force", "menu-hidden", "menu-disabled"],
            hints=["0s (Instant boot / hold Space to show)", "1 second", "2 seconds", "3 seconds", "4 seconds (Default)", "5 seconds", "10 seconds", "Always display menu without countdown", "Hidden until key press", "Disabled (Never show menu)"],
            default="4",
            group="Menu Display & Timing",
            extended_help="**Boot Menu Timeout (`timeout` in loader.conf)**\n\n- `0`: Instant boot / hidden menu (Hold Space during boot to show).\n- `2`-`5`: Fast countdown in seconds.\n- `menu-force`: Always displays boot menu without timeout countdown.\n- `menu-hidden`: Hides menu unless key is pressed.\n- `menu-disabled`: Disables menu display completely."
        ),
        ConfigItem(
            label="Console Mode",
            key="console-mode",
            scope="LOADER",
            type_="cycle",
            options=["max", "keep", "0", "1", "2", "auto"],
            default="max",
            group="Display Resolution",
            extended_help="**Console Mode (`console-mode` in loader.conf)**\n\nSets UEFI GOP display resolution for systemd-boot.\n\n- `max`: Native maximum display resolution.\n- `keep`: Keeps firmware default mode.\n- `0`: Standard UEFI 80x25 mode.\n- `1`: 80x50 mode.\n- `auto`: Automatic heuristics mode."
        ),
        ConfigItem(
            label="Cmdline Editor",
            key="editor",
            scope="LOADER",
            type_="cycle",
            options=["no", "yes"],
            default="no",
            group="Security & TPM",
            extended_help="**Interactive Kernel Parameter Editor (`editor` in loader.conf)**\n\n- `no`: (Recommended for security) Disables editing kernel parameters from the boot menu prompt.\n- `yes`: Allows editing cmdline at boot time with 'e'."
        ),
        ConfigItem(
            label="Secure Boot Auto Enroll",
            key="secure-boot-enroll",
            scope="LOADER",
            type_="cycle",
            options=["if-safe", "manual", "off", "force"],
            default="if-safe",
            group="Security & TPM",
            extended_help="**Secure Boot Key Enrollment (`secure-boot-enroll` in loader.conf)**\n\nControls automatic enrollment of Secure Boot keys into firmware variables."
        ),
        ConfigItem(
            label="Secure Boot Action",
            key="secure-boot-enroll-action",
            scope="LOADER",
            type_="cycle",
            options=["unset", "reboot", "shutdown"],
            default="unset",
            group="Security & TPM",
            extended_help="**Secure Boot Enrollment Action (`secure-boot-enroll-action` in loader.conf)**\n\nAction to execute after automatic Secure Boot key enrollment completes (reboot or shutdown)."
        ),
        ConfigItem(
            label="Secure Boot Timeout (s)",
            key="secure-boot-enroll-timeout-sec",
            scope="LOADER",
            type_="picker",
            options=["unset", "0", "5", "10", "15", "30", "hidden"],
            hints=["Unset (Default 15s)", "0s (Immediate enrollment)", "5 seconds", "10 seconds", "15 seconds", "30 seconds", "Hidden"],
            default="unset",
            group="Security & TPM",
            extended_help="**Secure Boot Warning Timeout (`secure-boot-enroll-timeout-sec` in loader.conf)**\n\nSeconds to display warning before automatic enrollment starts (0 or hidden for instant)."
        ),
        ConfigItem(
            label="Reboot BitLocker",
            key="reboot-for-bitlocker",
            scope="LOADER",
            type_="cycle",
            options=["no", "yes"],
            default="no",
            group="Security & TPM",
            extended_help="**Reboot for BitLocker (`reboot-for-bitlocker` in loader.conf)**\n\nReboots instead of chainloading directly when launching Windows Boot Manager to preserve TPM PCR measurements."
        ),
        ConfigItem(
            label="Reboot on Error",
            key="reboot-on-error",
            scope="LOADER",
            type_="cycle",
            options=["unset", "auto", "yes", "no"],
            default="unset",
            group="Boot Recovery & Reliability",
            extended_help="**Reboot on Boot Error (`reboot-on-error` in loader.conf)**\n\n- `auto`: (Default) Reboots if boot assessment is active and tries left counter is non-zero.\n- `yes`: Always reboot if boot entry fails to start.\n- `no`: Return control to UEFI firmware interface."
        ),
        ConfigItem(
            label="Systemd-Boot Log Level",
            key="log-level",
            scope="LOADER",
            type_="cycle",
            options=["unset", "info", "emerg", "alert", "crit", "err", "warning", "notice", "debug"],
            default="unset",
            group="Boot Recovery & Reliability",
            extended_help="**Systemd-Boot Log Level (`log-level` in loader.conf)**\n\nConfigures log verbosity used by systemd-boot during early initialization."
        ),
        ConfigItem(
            label="Auto Entries",
            key="auto-entries",
            scope="LOADER",
            type_="cycle",
            options=["1", "0"],
            default="1",
            group="Automatic Discovery",
            extended_help="**Automatic OS Entries (`auto-entries` in loader.conf)**\n\nAutomatically discovers Windows Boot Manager, macOS, and EFI shells."
        ),
        ConfigItem(
            label="Auto Firmware",
            key="auto-firmware",
            scope="LOADER",
            type_="cycle",
            options=["1", "0"],
            default="1",
            group="Automatic Discovery",
            extended_help="**Reboot Into Firmware Entry (`auto-firmware` in loader.conf)**\n\nAutomatically adds the 'Reboot Into Firmware Interface' menu item."
        ),
        ConfigItem(
            label="Beep on Menu",
            key="beep",
            scope="LOADER",
            type_="cycle",
            options=["0", "1"],
            default="0",
            group="Accessibility",
            extended_help="**PC Speaker Beep (`beep` in loader.conf)**\n\nEmits an audible beep when the boot menu is displayed."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 5: BOOT ENTRY METADATA & EFI ACTIONS
    # -------------------------------------------------------------------------
    5: [
        ConfigItem(
            label="Selected Entry to Manage",
            key="target_entry",
            scope="LOADER",
            type_="picker",
            options=_INITIAL_TARGET_OPTIONS,
            hints=["Automatically manages whichever kernel is default/active"] + ["Inspect & configure this specific boot entry" for _ in _INITIAL_TARGET_OPTIONS[1:]],
            default="Auto (Follows Default Kernel)",
            group="Active Entry Selection",
            extended_help=(
                "**Selected Boot Entry to Manage**\n\n"
                "Controls which kernel entry file is active for inspection and cmdline parameter editing.\n\n"
                "- `Auto (Follows Default Kernel)`: Automatically binds to whichever kernel you selected as default in Tab 4.\n"
                "- `<Entry Name>`: Explicitly locks the editor to inspect and tune a specific kernel entry."
            )
        ),
        *build_entry_override_items(),
        ConfigItem(
            label="Active Entry Title",
            key="title",
            scope="ENTRY",
            type_="string",
            default="unset",
            group="Active Entry Metadata",
            extended_help="**Active Entry Title (`title`)**\n\nThe title line inside the currently active boot entry file."
        ),
        ConfigItem(
            label="Active Entry Sort Key",
            key="sort-key",
            scope="ENTRY",
            type_="string",
            default="unset",
            group="Active Entry Metadata",
            extended_help="**Active Entry Sort Key (`sort-key`)**\n\nDefines menu ordering and grouping for the currently active boot entry."
        ),
        ConfigItem(
            label="Active Entry Version",
            key="version",
            scope="ENTRY",
            type_="string",
            default="unset",
            group="Active Entry Metadata",
            extended_help="**Active Entry Version (`version`)**\n\nKernel version string associated with the currently active boot entry."
        ),
        ConfigItem(
            label="Active Architecture",
            key="architecture",
            scope="ENTRY",
            type_="string",
            default="unset",
            group="Active Entry Metadata",
            extended_help="**Active Entry Architecture (`architecture`)**\n\nTarget CPU architecture for this bootloader entry (e.g. `x86-64`)."
        ),
        ConfigItem(
            label="Active Kernel Image",
            key="linux",
            scope="ENTRY",
            type_="string",
            default="unset",
            group="Active Entry Metadata",
            extended_help="**Active Kernel Image Path (`linux`)**\n\nRelative path to the kernel executable on the ESP (e.g. `/vmlinuz-linux`)."
        ),
        ConfigItem(
            label="Active Initramfs Image",
            key="initrd",
            scope="ENTRY",
            type_="string",
            default="unset",
            group="Active Entry Metadata",
            extended_help="**Active Initramfs Image Path (`initrd`)**\n\nRelative path to the initial ramdisk image on the ESP (e.g. `/initramfs-linux.img`)."
        ),
        ConfigItem(
            label="View Bootloader Status",
            key="action_bootctl_status",
            scope="DEFAULT",
            type_="action",
            default="bootctl status",
            group="Diagnostics & Status",
            force_interactive=True,
            extended_help="**bootctl status**\n\nDisplays current UEFI firmware, Secure Boot state, ESP mounts, and active default bootloader configuration."
        ),
        ConfigItem(
            label="View Registered Boot Entries",
            key="action_bootctl_list",
            scope="DEFAULT",
            type_="action",
            default="bootctl list",
            group="Diagnostics & Status",
            force_interactive=True,
            extended_help="**bootctl list**\n\nPrints all registered systemd-boot Type #1 and Type #2 boot entries, kernel command lines, and sort orders."
        ),
        ConfigItem(
            label="Clean Orphaned ESP Files",
            key="action_bootctl_cleanup",
            scope="DEFAULT",
            type_="action",
            default="bootctl cleanup",
            group="Maintenance & Firmware",
            confirm_message="Run bootctl cleanup to remove unreferenced files from the ESP?",
            extended_help="**bootctl cleanup**\n\nScans the EFI System Partition ($BOOT and ESP) and safely removes old kernel binaries and orphaned initrds not referenced by any active .conf boot entry."
        ),
        ConfigItem(
            label="Reboot to UEFI Firmware",
            key="action_bootctl_reboot_fw",
            scope="DEFAULT",
            type_="action",
            default="bootctl reboot-to-firmware true && systemctl reboot",
            group="Maintenance & Firmware",
            confirm_message="Reboot immediately into the UEFI BIOS firmware setup?",
            extended_help="**bootctl reboot-to-firmware true**\n\nSets the EFI variable to enter BIOS/UEFI setup on next reboot, then reboots immediately."
        ),
        ConfigItem(
            label="Regenerate Initramfs",
            key="action_mkinitcpio",
            scope="DEFAULT",
            type_="action",
            default="mkinitcpio -P > /dev/null",
            group="System Generation",
            confirm_message="Are you sure you want to regenerate the initramfs for all configured kernels? (mkinitcpio -P)",
            extended_help="**mkinitcpio -P**\n\nRebuilds the initial ramdisk environment for all installed preset kernels."
        ),
        ConfigItem(
            label="Update Systemd-Boot in ESP",
            key="action_bootctl_update",
            scope="DEFAULT",
            type_="action",
            default="bootctl update -q",
            group="Bootloader Configuration",
            confirm_message="Are you sure you want to update systemd-boot in the ESP? (bootctl update)",
            extended_help="**bootctl update**\n\nUpdates installed systemd-boot binaries in the EFI system partition (ESP) if a newer version is available."
        ),
        ConfigItem(
            label="Install Systemd-Boot",
            key="action_bootctl_install",
            scope="DEFAULT",
            type_="action",
            default="bootctl install -q",
            group="Bootloader Configuration",
            confirm_message="Are you sure you want to INSTALL systemd-boot? (bootctl install)",
            extended_help="**bootctl install**\n\nInstalls systemd-boot into the EFI system partition."
        ),
        ConfigItem(
            label="Refresh Random Seed",
            key="action_bootctl_seed",
            scope="DEFAULT",
            type_="action",
            default="bootctl random-seed -q",
            group="Bootloader Configuration",
            confirm_message="Are you sure you want to refresh the random seed? (bootctl random-seed)",
            extended_help="**bootctl random-seed**\n\nRefreshes the cryptographic random seed in the ESP and EFI variables."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 6: PRESETS
    # -------------------------------------------------------------------------
    6: [
        ConfigItem(
            label="Factory Reset",
            key="preset_factory_reset",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Standard Presets",
            confirm_message="Reset all kernel parameters and bootloader options to defaults?",
            preset_payload={"__ALL_DEFAULTS__": True},
            extended_help="**Factory Reset Preset**\n\nReverts all kernel command-line parameters and loader settings back to clean system defaults."
        ),
        ConfigItem(
            label="Gaming & Low Latency Profile",
            key="preset_performance",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Performance Profiles",
            confirm_message="Apply Maximum Performance & Low-Latency kernel parameters?",
            preset_payload={
                "mitigations": "off",
                "preempt": "lazy",
                "split_lock_mitigate": "0",
                "zswap.enabled": "1",
                "threadirqs": True,
                "quiet": True,
            },
            extended_help="**Gaming & Low Latency Profile**\n\n- `mitigations=off`: Maximizes CPU compute and gaming framerates.\n- `preempt=lazy`: Uses modern lazy preemption for responsiveness.\n- `split_lock_mitigate=0`: Prevents micro-stutters during heavy gaming or emulation.\n- `zswap.enabled=1`: Fast memory compression."
        ),
        ConfigItem(
            label="Maximum Security Profile",
            key="preset_security",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Security Profiles",
            confirm_message="Apply Maximum Security Hardened kernel parameters?",
            preset_payload={
                "mitigations": "auto",
                "apparmor": "1",
                "audit": "1",
                "init_on_alloc": "1",
                "init_on_free": "1",
                "slub_debug": "1",
                "LOADER.editor": "no",
            },
            extended_help="**Maximum Security Profile**\n\n- `mitigations=auto`: Enables all CPU vulnerability hardware mitigations.\n- `apparmor=1`: Enables Mandatory Access Control.\n- `init_on_alloc=1` & `init_on_free=1`: Zeroes memory pages on alloc/free.\n- `editor=no`: Disables interactive command-line editing at boot."
        ),
        ConfigItem(
            label="Silent Fast Boot Profile",
            key="preset_fastboot",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Boot Experience Profiles",
            confirm_message="Apply Silent Fast Boot parameters?",
            preset_payload={
                "quiet": True,
                "loglevel": "3",
                "splash": True,
                "LOADER.timeout": "0",
                "consoleblank": "300",
            },
            extended_help="**Silent Fast Boot Profile**\n\n- `quiet` & `loglevel=3`: Completely silences kernel scrolling logs.\n- `splash`: Shows graphical splash screen.\n- `timeout=0`: Instant boot into default kernel (hold Space during boot if needed)."
        ),
        ConfigItem(
            label="Deep Diagnostics & Debug Profile",
            key="preset_debug",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Diagnostic Profiles",
            confirm_message="Apply Full Verbose Debugging parameters?",
            preset_payload={
                "ignore_loglevel": True,
                "loglevel": "7",
                "rd.udev.log_level": "7",
                "sysrq_always_enabled": True,
                "nowatchdog": True,
                "panic": "0",
                "LOADER.editor": "yes",
            },
            extended_help="**Deep Diagnostics Profile**\n\n- `ignore_loglevel` & `loglevel=7`: Prints every single kernel and driver event to console.\n- `sysrq_always_enabled`: Enables Magic SysRq keys.\n- `panic=0`: Halts on panic so error trace can be photographed."
        ),
    ]
}


# =============================================================================
# 5. DEFERRED BACKGROUND LOAD HANDLER
# =============================================================================
def DEFERRED_LOAD() -> list[int]:
    """
    Background loader invoked by the TUI after first paint.
    Refreshes kernel discovery dynamically to catch newly installed kernels
    and updates Tab 4 (systemd-boot Loader) and Tab 5 (Boot Entry Metadata).
    """
    opts, hints = discover_kernel_options()
    
    # 1. Update Tab 4 (Default Entry picker)
    for item in SCHEMA.get(4, []):
        if item.key == "default" and item.scope == "LOADER":
            item.options = opts
            item.hints = hints
            break

    # 2. Update Tab 5 (Target Entry selector)
    target_opts = ["Auto (Follows Default Kernel)"] + [opt for opt in opts if not opt.startswith("@")]
    target_hints = ["Automatically manages whichever kernel is default/active"] + ["Inspect & configure this specific boot entry" for _ in target_opts[1:]]

    for item in SCHEMA.get(5, []):
        if item.key == "target_entry" and item.scope == "LOADER":
            item.options = target_opts
            item.hints = target_hints
            break

    # 3. Refresh direct renaming items in Tab 5 if new kernels appeared
    current_items = SCHEMA.get(5, [])
    if current_items:
        target_entry_item = current_items[0]
        tail_items = [it for it in current_items if it.scope != "ENTRY_OVERRIDE" and it.key != "target_entry"]
        new_override_items = build_entry_override_items()
        SCHEMA[5] = [target_entry_item] + new_override_items + tail_items

    return [4, 5]


# =============================================================================
# DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    import subprocess
    from pathlib import Path

    script_path = Path(__file__).resolve()
    main_router = _DUSKY_TUI_ROOT / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
