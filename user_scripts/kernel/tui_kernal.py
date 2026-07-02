#!/usr/bin/env python3
from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "systemd_boot"                       
TARGET_FILE = "/boot/loader/entries/arch-linux.conf"           
APP_TITLE = "Kernel Parameter Editor"         
REQUIRE_ROOT = True

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "batch"                        
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"

ENABLE_USER_PRESETS = True                   
USER_PRESETS_TAB = "Presets"

TAB_NOTICES = {
    0: {"level": "warning", "message": "Changes to the root partition or LUKS parameters can render the system unbootable. Proceed with caution."},
    4: {"level": "info", "message": "Execute these actions after modifying kernel parameters to ensure your system boots with the latest configuration."},
}

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = [
    "Boot & Root",
    "Performance",
    "Hardware & Graphics",
    "Security & Debug",
    "Bootloader & Init",
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
            extended_help="**Read-Write Mount**\n\nMounts the root device initially as read-write. This is required by some init systems."
        ),
        ConfigItem(
            label="Mount Read-Only",
            key="ro",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Root Filesystem",
            extended_help="**Read-Only Mount**\n\nMounts the root device initially as read-only. The init system will remount it read-write later."
        ),
        ConfigItem(
            label="LUKS Crypt Device",
            key="rd.luks.name",
            scope="DEFAULT",
            type_="string",
            default="unset",
            group="Encryption",
            extended_help="**Dracut LUKS Definition**\n\nMaps a LUKS UUID to a mapped device name (e.g., `be1ac50d-...=cryptroot`)."
        ),
        ConfigItem(
            label="LUKS Options",
            key="rd.luks.options",
            scope="DEFAULT",
            type_="string",
            default="unset",
            group="Encryption",
            extended_help="**Dracut LUKS Options**\n\nComma-separated list of options for LUKS (e.g., `discard` to enable TRIM)."
        ),
        ConfigItem(
            label="FSCK Mode",
            key="fsck.mode",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "auto", "skip", "force"],
            default="unset",
            group="Boot Process",
            extended_help="**File System Check**\n\nControls when `fsck` is executed on root file systems at boot time.\n\n- `skip`: Skips checking the root file system entirely (speeds up boot but risks mounting a dirty filesystem)."
        ),
        ConfigItem(
            label="Splash Screen",
            key="splash",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Boot Process",
            extended_help="**Boot Splash**\n\nEnables the boot splash screen (e.g., Plymouth or Plymouth-free bootsplash) to hide kernel initialization logs."
        )
    ],

    # -------------------------------------------------------------------------
    # TAB 1: PERFORMANCE
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Memory Limit",
            key="mem",
            scope="DEFAULT",
            type_="string",
            options=["unset", "6G", "8G", "12G", "16G", "24G", "32G", "48G", "64G"],
            default="unset",
            group="Memory",
            extended_help="**RAM Allocation Limit**\n\nForces the kernel to use a specific maximum amount of memory (e.g., `16G`). This is useful for testing low-memory conditions or reserving hardware RAM. You can select a preset or type your own custom value."
        ),
        ConfigItem(
            label="Mitigations",
            key="mitigations",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "auto", "off"],
            default="unset",
            group="CPU",
            extended_help="**CPU Vulnerability Mitigations**\n\nControls optional mitigations for CPU side-channel vulnerabilities (like Spectre and Meltdown).\n\n- `auto`: System default (usually enabled).\n- `off`: Disables all optional CPU mitigations, which can significantly improve system performance at the expense of local security.\n- `unset`: Relies on the kernel's compile-time defaults."
        ),
        ConfigItem(
            label="Intel P-State",
            key="intel_pstate",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "disable", "passive", "active", "force"],
            default="unset",
            group="CPU",
            extended_help="**Intel Frequency Scaling**\n\nConfigures the hardware P-State scaling driver for Intel processors.\n\n- `disable`: Disables intel_pstate and falls back to acpi-cpufreq.\n- `passive`: Uses the passive governor to allow user-space tools more control over frequency."
        ),
        ConfigItem(
            label="AMD P-State",
            key="amd_pstate",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "active", "passive", "guided", "disable"],
            default="unset",
            group="CPU",
            extended_help="**AMD Frequency Scaling**\n\nConfigures the precision boost state scaling driver for modern AMD Ryzen processors.\n\n- `active`: Fully hardware-controlled autonomous scaling (Recommended for Zen 2+).\n- `guided`: OS hints mixed with hardware control."
        ),
        ConfigItem(
            label="ZSwap Enabled",
            key="zswap.enabled",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "0", "1"],
            default="unset",
            group="Memory",
            extended_help="**ZSwap Compression**\n\nZSwap intercepts memory pages that are being swapped out and attempts to compress them into a dynamically sized RAM-based pool.\n\n- `1`: Enables ZSwap, significantly improving responsiveness during heavy memory pressure.\n- `0`: Explicitly disables ZSwap."
        ),
        ConfigItem(
            label="Trans. Hugepages",
            key="transparent_hugepage",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "always", "madvise", "never"],
            default="unset",
            group="Memory",
            extended_help="**Transparent Hugepages (THP)**\n\nAllows the kernel to dynamically allocate memory in larger block sizes (hugepages) to reduce Translation Lookaside Buffer (TLB) overhead.\n\n- `always`: Enabled globally for all processes.\n- `madvise`: Only enabled for applications that explicitly request it.\n- `never`: Completely disables THP."
        ),
        ConfigItem(
            label="NUMA Balancing",
            key="numa_balancing",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "enable", "disable"],
            default="unset",
            group="Memory",
            extended_help="**Automatic NUMA Balancing**\n\nOptimizes thread and memory placement on multi-node NUMA architectures (such as dual-socket boards or large Threadripper systems).\n\n- `enable`: Automatically moves memory to the local node of the CPU executing the thread."
        ),
        ConfigItem(
            label="Thread IRQs",
            key="threadirqs",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Kernel",
            extended_help="**Threaded Interrupts**\n\nForces hardware interrupt handlers to run inside kernel threads instead of hard IRQ context. This can significantly improve real-time responsiveness and audio latency at the cost of a slight increase in overall CPU overhead."
        ),
        ConfigItem(
            label="Init on Alloc",
            key="init_on_alloc",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "0", "1"],
            default="unset",
            group="Memory",
            extended_help="**Zero Memory on Allocation**\n\n- `0`: Disables zeroing of memory upon allocation, improving performance and reducing overhead.\n- `1`: Enables zeroing of memory for security."
        ),
        ConfigItem(
            label="Init on Free",
            key="init_on_free",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "0", "1"],
            default="unset",
            group="Memory",
            extended_help="**Zero Memory on Free**\n\n- `0`: Disables zeroing of memory upon free, improving performance.\n- `1`: Enables zeroing of memory for security."
        ),
        ConfigItem(
            label="SLUB Debug",
            key="slub_debug",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "0", "1"],
            default="unset",
            group="Kernel",
            extended_help="**SLUB Allocator Debugging**\n\n- `0`: Explicitly disables all SLUB debugging. This saves kernel memory footprint and CPU overhead.\n- `1`: Enables SLUB debugging."
        ),
        ConfigItem(
            label="Disable IPv6",
            key="ipv6.disable",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "0", "1"],
            default="unset",
            group="Network",
            extended_help="**IPv6 Support**\n\n- `1`: Disables the entire IPv6 stack, which saves kernel heap memory and reduces the attack surface if IPv6 is not needed.\n- `0`: Leaves IPv6 enabled."
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
            extended_help="**NVIDIA Direct Rendering Manager**\n\nRequired for Wayland compositors (like Hyprland or Sway) to function correctly on proprietary NVIDIA drivers.\n\n- `1`: Enables kernel modesetting (Required for Wayland)."
        ),
        ConfigItem(
            label="AMDGPU PP Feature Mask",
            key="amdgpu.ppfeaturemask",
            scope="DEFAULT",
            type_="string",
            default="unset",
            group="Graphics",
            extended_help="**AMD GPU Powerplay Mask**\n\nUsed to unlock overclocking and undervolting capabilities on AMD graphics cards (e.g., `0xffffffff`)."
        ),
        ConfigItem(
            label="Intel GuC/HuC",
            key="i915.enable_guc",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "2", "3"],
            default="unset",
            group="Graphics",
            extended_help="**Intel Graphics Microcontrollers**\n\nEnables advanced video encoding and power management on modern Intel GPUs.\n\n- `2`: Enable HuC only.\n- `3`: Enable both GuC and HuC."
        ),
        ConfigItem(
            label="Intel IOMMU",
            key="intel_iommu",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "on", "off", "igfx_off"],
            default="unset",
            group="IOMMU",
            extended_help="**Intel IOMMU / VT-d**\n\nControls the Intel Input/Output Memory Management Unit.\n\n- `on`: Enables VT-d to allow advanced features like VFIO PCIe Passthrough for virtual machines."
        ),
        ConfigItem(
            label="AMD IOMMU",
            key="amd_iommu",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "off", "fullflush", "force_isolation"],
            default="unset",
            group="IOMMU",
            extended_help="**AMD IOMMU / AMD-Vi**\n\nControls the AMD IOMMU implementation.\n\n- `force_isolation`: Forces strict device isolation for VFIO."
        ),
        ConfigItem(
            label="IOMMU Mode",
            key="iommu",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "pt", "off", "force"],
            default="unset",
            group="IOMMU",
            extended_help="**Generic IOMMU Subsystem**\n\n- `pt`: Passthrough mode. Devices use an identity-mapped translation by default, which improves DMA performance for host devices while still allowing VM passthrough."
        ),
        ConfigItem(
            label="PCIE ASPM",
            key="pcie_aspm",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "default", "force", "off"],
            default="unset",
            group="Power Management",
            extended_help="**Active State Power Management**\n\nPCIe power saving configuration.\n\n- `force`: Forces ASPM on even if the BIOS says it's unsupported.\n- `off`: Disables ASPM entirely to prevent latency spikes or hardware crashes."
        ),
        ConfigItem(
            label="USB Autosuspend",
            key="usbcore.autosuspend",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "-1", "1"],
            default="unset",
            group="Power Management",
            extended_help="**USB Core Autosuspend**\n\nControls the delay (in seconds) before an idle USB device is suspended.\n\n- `-1`: Completely disables USB autosuspend, which can fix issues with external audio DACs, mice, or keyboards disconnecting randomly."
        ),
        ConfigItem(
            label="Cursor Default",
            key="vt.global_cursor_default",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "0", "1"],
            default="unset",
            group="Console",
            extended_help="**TTY Global Cursor**\n\n- `0`: Hides the blinking cursor on the raw TTY console during boot.\n- `1`: Shows the blinking cursor."
        ),
        ConfigItem(
            label="Console Blank (s)",
            key="consoleblank",
            scope="DEFAULT",
            type_="picker",
            options=["unset", "0", "60", "300", "600", "900", "1800", "3600"],
            default="unset",
            group="Console",
            extended_help="**TTY Screen Blanking**\n\nTime in seconds before a virtual TTY console will blank its screen to prevent burn-in.\n\n- `0`: Disables screen blanking entirely.\n- `600`: Defaults to 10 minutes."
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
            extended_help="**AppArmor MAC**\n\nMandatory Access Control module.\n\n- `1`: Enables the AppArmor security module.\n- `0`: Disables it."
        ),
        ConfigItem(
            label="SELinux",
            key="selinux",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "0", "1"],
            default="unset",
            group="Security",
            extended_help="**SELinux MAC**\n\nSecurity-Enhanced Linux module.\n\n- `1`: Enables SELinux.\n- `0`: Disables SELinux completely at boot."
        ),
        ConfigItem(
            label="Audit Subsystem",
            key="audit",
            scope="DEFAULT",
            type_="cycle",
            options=["unset", "0", "1"],
            default="unset",
            group="Security",
            extended_help="**Kernel Audit Subsystem**\n\n- `1`: Enables the kernel auditing subsystem used by tools like auditd.\n- `0`: Disables auditing to slightly reduce overhead and log spam if you don't use it."
        ),
        ConfigItem(
            label="Quiet Boot",
            key="quiet",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Logging",
            extended_help="**Quiet Mode**\n\nSuppresses the vast majority of normal kernel initialization messages during the boot process, resulting in a cleaner, faster-scrolling screen or a seamless splash screen."
        ),
        ConfigItem(
            label="Kernel Log Level",
            key="loglevel",
            scope="DEFAULT",
            type_="picker",
            options=["unset", "0", "1", "2", "3", "4", "5", "6", "7"],
            default="unset",
            group="Logging",
            extended_help="**Console Loglevel**\n\nDefines the severity threshold for printing messages to the console.\n\n- `0`: KERN_EMERG (Only emergencies)\n- `3`: KERN_ERR (Errors and worse, normal desktop standard)\n- `7`: KERN_DEBUG (Extremely verbose)"
        ),
        ConfigItem(
            label="Udev Log Level",
            key="rd.udev.log_level",
            scope="DEFAULT",
            type_="picker",
            options=["unset", "0", "3", "4", "7"],
            default="unset",
            group="Logging",
            extended_help="**Dracut/Udev Loglevel**\n\nLimits the verbosity of udev events during the initial ramdisk boot phase (e.g., `3` for errors only)."
        ),
        ConfigItem(
            label="Ignore Loglevel",
            key="ignore_loglevel",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Logging",
            extended_help="**Force Verbose Logs**\n\nForces the kernel to print all messages to the console regardless of the `loglevel` setting. Useful for deep debugging of driver initialization failures."
        ),
        ConfigItem(
            label="Always Enable SysRq",
            key="sysrq_always_enabled",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Recovery",
            extended_help="**Magic SysRq Key**\n\nEnables all functions of the Magic SysRq key combinations (Alt+SysRq+<Key>), allowing you to gracefully recover, reboot (REISUB), or dump state from a totally frozen system."
        ),
        ConfigItem(
            label="Disable Watchdog",
            key="nowatchdog",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Recovery",
            extended_help="**NMI Watchdog**\n\nThe Non-Maskable Interrupt (NMI) watchdog detects hardware hang states.\n\nEnabling this flag (`nowatchdog`) disables the watchdog entirely, which can slightly reduce system interrupts and improve power efficiency on consumer systems."
        ),
        ConfigItem(
            label="Panic Timeout (s)",
            key="panic",
            scope="DEFAULT",
            type_="picker",
            options=["unset", "-1", "0", "10", "30", "60"],
            default="unset",
            group="Recovery",
            extended_help="**Reboot on Panic**\n\nSets the timeout in seconds before automatically rebooting the system after a kernel panic.\n\n- `0`: Wait forever (halt).\n- `-1`: Reboot immediately."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 4: BOOTLOADER & INITRAMFS (Systemd-Boot / mkinitcpio)
    # -------------------------------------------------------------------------
    4: [
        ConfigItem(
            label="Regenerate Initramfs",
            key="action_mkinitcpio",
            scope="DEFAULT",
            type_="action",
            default="mkinitcpio -P > /dev/null",
            group="System Generation",
            confirm_message="Are you sure you want to regenerate the initramfs for all configured kernels? (mkinitcpio -P)",
            extended_help="**mkinitcpio -P**\n\nRebuilds the initial ramdisk environment for all installed preset kernels. This is absolutely essential after changing root filesystem types, LUKS encryption parameters, or early-boot drivers."
        ),
        ConfigItem(
            label="Update Systemd-Boot",
            key="action_bootctl_update",
            scope="DEFAULT",
            type_="action",
            default="bootctl update -q",
            group="Bootloader Configuration",
            confirm_message="Are you sure you want to update systemd-boot in the ESP? (bootctl update)",
            extended_help="**bootctl update**\n\nUpdates all installed versions of systemd-boot in the EFI system partition (ESP) if the available version is newer. It also ensures the boot loader is appended to the firmware's boot list."
        ),
        ConfigItem(
            label="Install Systemd-Boot",
            key="action_bootctl_install",
            scope="DEFAULT",
            type_="action",
            default="bootctl install -q",
            group="Bootloader Configuration",
            confirm_message="Are you sure you want to INSTALL systemd-boot? This will overwrite the primary EFI bootloader. (bootctl install)",
            extended_help="**bootctl install**\n\nInstalls systemd-boot into the EFI system partition and adds it to the top of the firmware's boot loader list. Only run this if systemd-boot is not yet installed."
        ),
        ConfigItem(
            label="Refresh Random Seed",
            key="action_bootctl_seed",
            scope="DEFAULT",
            type_="action",
            default="bootctl random-seed -q",
            group="Bootloader Configuration",
            extended_help="**bootctl random-seed**\n\nRefreshes the random seed in the ESP and EFI variables, ensuring proper early-boot entropy."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 5: PRESETS
    # -------------------------------------------------------------------------
    5: []
}
