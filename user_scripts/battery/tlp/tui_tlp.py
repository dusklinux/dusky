#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: TLP POWER MANAGEMENT MASTER SCHEMA (VERSION 1.10)
===============================================================================
Target: ~/tlp.conf (Testing)
Engine: ini
"""

from python.frontend.core_types import ConfigItem

ENGINE_TYPE = "ini"
TARGET_FILE = "/etc/tlp.conf"
REQUIRE_ROOT = True

APP_TITLE = "TLP Configurator"
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json" 

ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Profiles"

GLOBAL_POPUP = {
    "title": "Kernel Power Management",
    "message": "Caution: Forcing conflicting CPU governors or extreme power states may cause instability. Type 'nil' into any text field to restore its hardware default.",
    "level": "warning",
    "require_confirm": False,
    "cancel_quits": False
}

TABS = [
    "General",
    "Processor",
    "Storage",
    "Graphics",
    "Radio",
    "USB",
    "PCIe",
    "Audio",
    "Battery",
    "Profiles"
]

SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: GENERAL
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Apply Changes", key="action_tlp_start", scope="DEFAULT", type_="action", default="tlp start", group="Daemon",
            extended_help="**Reload TLP**\n\nExecutes `tlp start` to immediately apply all saved configuration changes to the active system without needing a reboot."
        ),
        ConfigItem(
            label="TLP Daemon", key="TLP_ENABLE", scope="DEFAULT", type_="cycle", default="1", options=["0", "1"], hints=["Disabled", "Enabled"], group="Daemon",
            extended_help="**Master TLP Switch**\n\nSet to 0 to temporarily disable TLP without uninstalling the package. Reboot required after disabling."
        ),
        ConfigItem(
            label="Disable Dflts", key="TLP_DISABLE_DEFAULTS", scope="DEFAULT", type_="cycle", default="nil", options=["nil", "0", "1"], group="Daemon",
            extended_help="**Disable Intrinsic Defaults**\n\nSet to 1 to deactivate almost all TLP defaults. TLP will only apply settings that have been explicitly activated (not commented out)."
        ),
        ConfigItem(
            label="Warn Level", key="TLP_WARN_LEVEL", scope="DEFAULT", type_="cycle", default="3", options=["0", "1", "2", "3"], group="Daemon",
            extended_help="**Warning Output**\n\nControls how warnings are issued:\n0 = Disabled\n1 = Background tasks report to syslog\n2 = Shell commands report to terminal\n3 = Both"
        ),
        ConfigItem(label="Log Colors", key="TLP_MSG_COLORS", scope="DEFAULT", type_="string", default="91 93 1 92", group="Daemon"),

        ConfigItem(
            label="Auto Switch", key="TLP_AUTO_SWITCH", scope="DEFAULT", type_="cycle", default="2", options=["0", "1", "2"], hints=["Never", "Always", "Smart"], group="Switching",
            extended_help="**Automatic Profile Switching**\n\n0 = Disabled (Never switch)\n1 = Auto (Always switch on AC/BAT events)\n2 = Smart (Skips switch if you manually changed the profile away from the default)."
        ),
        ConfigItem(label="AC Profile", key="TLP_PROFILE_AC", scope="DEFAULT", type_="cycle", default="BAL", options=["PRF", "BAL", "SAV"], group="Switching"),
        ConfigItem(label="BAT Profile", key="TLP_PROFILE_BAT", scope="DEFAULT", type_="cycle", default="SAV", options=["PRF", "BAL", "SAV"], group="Switching"),
        ConfigItem(label="Dflt Profile", key="TLP_PROFILE_DEFAULT", scope="DEFAULT", type_="cycle", default="nil", options=["nil", "PRF", "BAL", "SAV"], group="Switching"),
        ConfigItem(label="Ignore PS", key="TLP_PS_IGNORE", scope="DEFAULT", type_="string", default="nil", group="Switching"),

        ConfigItem(
            label="Platform Profile", key="menu_platform", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Platform",
            extended_help="**ACPI Platform Profile**\n\nControls system operating characteristics around thermal limits and fan speed. 'performance' maximizes speed, 'low-power' maximizes battery.\n\n*Check available choices for your PC:*\n`cat /sys/firmware/acpi/platform_profile_choices`"
        ),
        ConfigItem(label="AC", key="PLATFORM_PROFILE_ON_AC", scope="DEFAULT", type_="picker", default="performance", options=["nil", "performance", "balanced", "low-power", "quiet", "cool"], parent_ref="menu_platform"),
        ConfigItem(label="Battery", key="PLATFORM_PROFILE_ON_BAT", scope="DEFAULT", type_="picker", default="balanced", options=["nil", "performance", "balanced", "low-power", "quiet", "cool"], parent_ref="menu_platform"),
        ConfigItem(label="Power Saver", key="PLATFORM_PROFILE_ON_SAV", scope="DEFAULT", type_="picker", default="low-power", options=["nil", "performance", "balanced", "low-power", "quiet", "cool"], parent_ref="menu_platform"),

        ConfigItem(
            label="Suspend Mode", key="menu_sleep", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Suspend",
            extended_help="**System Suspend Mode**\n\n`s2idle`: Idle standby (light-weight software sleep. CPU in deep idle, but system not fully powered down. Very fast resume).\n`deep`: Suspend to RAM (higher savings, slightly longer resume).\n\n*Note:* Some laptops (like certain Asus models) have bugs where deep sleep causes the device to hard reset after waking up. Use `s2idle` if experiencing instability.\n\n*Check supported modes:*\n`cat /sys/power/mem_sleep`"
        ),
        ConfigItem(label="AC", key="MEM_SLEEP_ON_AC", scope="DEFAULT", type_="picker", default="nil", options=["nil", "s2idle", "deep"], parent_ref="menu_sleep"),
        ConfigItem(label="Battery", key="MEM_SLEEP_ON_BAT", scope="DEFAULT", type_="picker", default="nil", options=["nil", "s2idle", "deep"], parent_ref="menu_sleep"),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: PROCESSOR (CPU)
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Scaling Driver", key="menu_driver", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Driver",
            extended_help="**Scaling Driver Mode**\n\nLeaving this to `active` is highly recommended. It allows the CPU to manage its own frequency depending on workload, as opposed to offloading it to a generic kernel setting. The CPU's own governor is much more optimized and tuned for your specific silicon.\n\n*Check active driver:*\n`cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_driver`"
        ),
        ConfigItem(label="AC", key="CPU_DRIVER_OPMODE_ON_AC", scope="DEFAULT", type_="picker", default="nil", options=["nil", "active", "passive", "guided"], parent_ref="menu_driver"),
        ConfigItem(label="Battery", key="CPU_DRIVER_OPMODE_ON_BAT", scope="DEFAULT", type_="picker", default="nil", options=["nil", "active", "passive", "guided"], parent_ref="menu_driver"),
        ConfigItem(label="Power Saver", key="CPU_DRIVER_OPMODE_ON_SAV", scope="DEFAULT", type_="picker", default="nil", options=["nil", "active", "passive", "guided"], parent_ref="menu_driver"),

        ConfigItem(
            label="Governor", key="menu_gov", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Governor",
            extended_help="**Scaling Governor**\n\nIntel CPUs generally have two optimal settings: `performance` and `powersave`. Powersave will be more aggressive at scaling down frequency but will still hit the highest frequencies when needed. DO NOT USE CUSTOM CPU SCALING FREQUENCIES with active drivers.\n\n*Check available governors:*\n`cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_available_governors`"
        ),
        ConfigItem(label="AC", key="CPU_SCALING_GOVERNOR_ON_AC", scope="DEFAULT", type_="picker", default="nil", options=["nil", "performance", "powersave", "conservative", "ondemand", "userspace", "schedutil"], parent_ref="menu_gov"),
        ConfigItem(label="Battery", key="CPU_SCALING_GOVERNOR_ON_BAT", scope="DEFAULT", type_="picker", default="nil", options=["nil", "performance", "powersave", "conservative", "ondemand", "userspace", "schedutil"], parent_ref="menu_gov"),
        ConfigItem(label="Power Saver", key="CPU_SCALING_GOVERNOR_ON_SAV", scope="DEFAULT", type_="picker", default="nil", options=["nil", "performance", "powersave", "conservative", "ondemand", "userspace", "schedutil"], parent_ref="menu_gov"),

        ConfigItem(
            label="Energy Policy", key="menu_policy", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Policy",
            extended_help="**Energy Performance Policy (EPP/EPB)**\n\nValues in order of increasing power saving: performance, balance_performance, default, balance_power, power.\nRequires Intel 6th gen+ or AMD Zen 2+.\n\n*Check available options:*\n`cat /sys/devices/system/cpu/cpu*/cpufreq/energy_performance_available_preferences`"
        ),
        ConfigItem(label="AC", key="CPU_ENERGY_PERF_POLICY_ON_AC", scope="DEFAULT", type_="picker", default="balance_performance", options=["nil", "performance", "balance_performance", "default", "balance_power", "power"], parent_ref="menu_policy"),
        ConfigItem(label="Battery", key="CPU_ENERGY_PERF_POLICY_ON_BAT", scope="DEFAULT", type_="picker", default="balance_power", options=["nil", "performance", "balance_performance", "default", "balance_power", "power"], parent_ref="menu_policy"),
        ConfigItem(label="Power Saver", key="CPU_ENERGY_PERF_POLICY_ON_SAV", scope="DEFAULT", type_="picker", default="power", options=["nil", "performance", "balance_performance", "default", "balance_power", "power"], parent_ref="menu_policy"),

        ConfigItem(
            label="Min Freq (MHz)", key="menu_minfreq", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Frequency",
            extended_help="**Minimum Hardware Frequency**\n\nInput a precise MHz value based on your hardware. Not recommended for `intel_pstate` active mode. Type `nil` to return to hardware default."
        ),
        ConfigItem(label="AC", key="CPU_SCALING_MIN_FREQ_ON_AC", scope="DEFAULT", type_="string", default="nil", parent_ref="menu_minfreq"),
        ConfigItem(label="Battery", key="CPU_SCALING_MIN_FREQ_ON_BAT", scope="DEFAULT", type_="string", default="nil", parent_ref="menu_minfreq"),
        ConfigItem(label="Power Saver", key="CPU_SCALING_MIN_FREQ_ON_SAV", scope="DEFAULT", type_="string", default="nil", parent_ref="menu_minfreq"),

        ConfigItem(
            label="Max Freq (MHz)", key="menu_maxfreq", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Frequency",
            extended_help="**Maximum Hardware Frequency**\n\nInput a precise MHz value. Note that lowering the max frequency on battery may NOT conserve power depending on workloads."
        ),
        ConfigItem(label="AC", key="CPU_SCALING_MAX_FREQ_ON_AC", scope="DEFAULT", type_="string", default="nil", parent_ref="menu_maxfreq"),
        ConfigItem(label="Battery", key="CPU_SCALING_MAX_FREQ_ON_BAT", scope="DEFAULT", type_="string", default="nil", parent_ref="menu_maxfreq"),
        ConfigItem(label="Power Saver", key="CPU_SCALING_MAX_FREQ_ON_SAV", scope="DEFAULT", type_="string", default="nil", parent_ref="menu_maxfreq"),

        ConfigItem(
            label="Min P-State %", key="menu_minperf", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="PState",
            extended_help="**Intel P-State Limits**\n\nMaps directly to `min_perf_pct` in `/sys/devices/system/cpu/intel_pstate/`. You generally want this at 0 so the CPU can drop into its deepest idle states."
        ),
        ConfigItem(label="AC", key="CPU_MIN_PERF_ON_AC", scope="DEFAULT", type_="string", default="0", parent_ref="menu_minperf"),
        ConfigItem(label="Battery", key="CPU_MIN_PERF_ON_BAT", scope="DEFAULT", type_="string", default="0", parent_ref="menu_minperf"),
        ConfigItem(label="Power Saver", key="CPU_MIN_PERF_ON_SAV", scope="DEFAULT", type_="string", default="0", parent_ref="menu_minperf"),

        ConfigItem(
            label="Max P-State %", key="menu_maxperf", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="PState",
            extended_help="**Intel P-State Limits**\n\nMaps directly to `max_perf_pct`. Setting AC to 100 allows full turbo. On battery, capping to 30-70% heavily restricts the CPU to save power. If you find your laptop too slow unplugged, simply raise the BAT value."
        ),
        ConfigItem(label="AC", key="CPU_MAX_PERF_ON_AC", scope="DEFAULT", type_="string", default="100", parent_ref="menu_maxperf"),
        ConfigItem(label="Battery", key="CPU_MAX_PERF_ON_BAT", scope="DEFAULT", type_="string", default="80", parent_ref="menu_maxperf"),
        ConfigItem(label="Power Saver", key="CPU_MAX_PERF_ON_SAV", scope="DEFAULT", type_="string", default="60", parent_ref="menu_maxperf"),

        ConfigItem(
            label="Turbo Boost", key="menu_boost", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Boost",
            extended_help="**Hardware Boost Allowed**\n\nAllows the maximum frequency to raise beyond base clock if thermal budget allows. 0=Disabled, 1=Allowed."
        ),
        ConfigItem(label="AC", key="CPU_BOOST_ON_AC", scope="DEFAULT", type_="cycle", default="nil", options=["nil", "0", "1"], parent_ref="menu_boost"),
        ConfigItem(label="Battery", key="CPU_BOOST_ON_BAT", scope="DEFAULT", type_="cycle", default="nil", options=["nil", "0", "1"], parent_ref="menu_boost"),
        ConfigItem(label="Power Saver", key="CPU_BOOST_ON_SAV", scope="DEFAULT", type_="cycle", default="nil", options=["nil", "0", "1"], parent_ref="menu_boost"),

        ConfigItem(
            label="Dynamic Boost", key="menu_dyn", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Boost",
            extended_help="**HWP Dynamic Boost**\n\nA feature that temporarily raises the CPU’s minimum performance level when a thread that’s been waiting on I/O (disk, network) becomes runnable. When on AC, it provides a latency/performance kick. On battery, disabling it prevents sacrificing extra power for brief kicks."
        ),
        ConfigItem(label="AC", key="CPU_HWP_DYN_BOOST_ON_AC", scope="DEFAULT", type_="cycle", default="nil", options=["nil", "0", "1"], parent_ref="menu_dyn"),
        ConfigItem(label="Battery", key="CPU_HWP_DYN_BOOST_ON_BAT", scope="DEFAULT", type_="cycle", default="nil", options=["nil", "0", "1"], parent_ref="menu_dyn"),
        ConfigItem(label="Power Saver", key="CPU_HWP_DYN_BOOST_ON_SAV", scope="DEFAULT", type_="cycle", default="nil", options=["nil", "0", "1"], parent_ref="menu_dyn"),

        ConfigItem(label="NMI Watchdog", key="NMI_WATCHDOG", scope="DEFAULT", type_="cycle", default="0", options=["0", "1"], group="Kernel"),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: STORAGE (Disk & NVMe/SATA)
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Disk Targets", key="DISK_DEVICES", scope="DEFAULT", type_="string", default="nvme0n1 sda", group="Disks",
            extended_help="**Target Block Devices**\n\nEnsure you add or change this if you install more drives later. It is highly robust to use hardware IDs instead of `nvme0n1`.\n\n*Find your disk IDs using:*\n`sudo tlp diskid`"
        ),
        ConfigItem(label="APM Denylist", key="DISK_APM_CLASS_DENYLIST", scope="DEFAULT", type_="string", default="usb ieee1394", group="Disks"),
        
        ConfigItem(
            label="I/O Scheduler", key="DISK_IOSCHED", scope="DEFAULT", type_="string", default="keep", group="Disks",
            extended_help="**Block I/O Scheduler**\n\nSetting this to `none` is often best for NVMe/SSDs because it lets the hardware controller handle I/O independently and doesn't require the Linux kernel to engage, saving CPU cycles and reducing overhead.\n\n*Check supported options for your drive:*\n`cat /sys/block/nvme0n1/queue/scheduler`"
        ),
        ConfigItem(label="SATA Denylist", key="SATA_LINKPWR_DENYLIST", scope="DEFAULT", type_="string", default="nil", group="Disks"),

        ConfigItem(label="Idle Sync (sec)", key="menu_idle", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Timeouts"),
        ConfigItem(label="AC", key="DISK_IDLE_SECS_ON_AC", scope="DEFAULT", type_="string", default="0", parent_ref="menu_idle"),
        ConfigItem(label="Battery", key="DISK_IDLE_SECS_ON_BAT", scope="DEFAULT", type_="string", default="2", parent_ref="menu_idle"),

        ConfigItem(label="Dirty Page (sec)", key="menu_lost", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Timeouts"),
        ConfigItem(label="AC", key="MAX_LOST_WORK_SECS_ON_AC", scope="DEFAULT", type_="string", default="15", parent_ref="menu_lost"),
        ConfigItem(label="Battery", key="MAX_LOST_WORK_SECS_ON_BAT", scope="DEFAULT", type_="string", default="60", parent_ref="menu_lost"),

        ConfigItem(
            label="APM Level", key="menu_apm", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Power",
            extended_help="**Advanced Power Management**\n\nThis is largely a legacy feature for spinning HDDs, not modern SSDs. 1 to 254. 255 disables APM. \n\n*Check if your spinning drive supports APM:*\n`sudo hdparm -I /dev/sda | grep \"Advanced power management\"`"
        ),
        ConfigItem(label="AC", key="DISK_APM_LEVEL_ON_AC", scope="DEFAULT", type_="string", default="254 254", parent_ref="menu_apm"),
        ConfigItem(label="Battery", key="DISK_APM_LEVEL_ON_BAT", scope="DEFAULT", type_="string", default="128 128", parent_ref="menu_apm"),

        ConfigItem(
            label="Spindown", key="menu_spin", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Power",
            extended_help="**Hard Disk Spindown**\n\n0: spindown disabled. 1..240: 5s to 20min (in units of 5s). See `man hdparm` for advanced intervals."
        ),
        ConfigItem(label="AC", key="DISK_SPINDOWN_TIMEOUT_ON_AC", scope="DEFAULT", type_="string", default="0 0", parent_ref="menu_spin"),
        ConfigItem(label="Battery", key="DISK_SPINDOWN_TIMEOUT_ON_BAT", scope="DEFAULT", type_="string", default="0 0", parent_ref="menu_spin"),

        ConfigItem(
            label="SATA ALPM", key="menu_sata", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="SATA",
            extended_help="**AHCI Link Power Management**\n\nControls SATA disk link state. `med_power_with_dipm` is strongly recommended for balance."
        ),
        ConfigItem(label="AC", key="SATA_LINKPWR_ON_AC", scope="DEFAULT", type_="picker", default="med_power_with_dipm", options=["nil", "min_power", "med_power_with_dipm", "medium_power", "max_performance"], parent_ref="menu_sata"),
        ConfigItem(label="Battery", key="SATA_LINKPWR_ON_BAT", scope="DEFAULT", type_="picker", default="med_power_with_dipm", options=["nil", "min_power", "med_power_with_dipm", "medium_power", "max_performance"], parent_ref="menu_sata"),

        ConfigItem(
            label="AHCI Runtime PM", key="menu_ahci", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="AHCI",
            extended_help="**Runtime Power Management**\n\nAllows the system to power down the controller that manages power saving for other block devices. When 'auto', the controller goes into power saving mode when all block drives are idle."
        ),
        ConfigItem(label="AC", key="AHCI_RUNTIME_PM_ON_AC", scope="DEFAULT", type_="cycle", default="on", options=["on", "auto"], parent_ref="menu_ahci"),
        ConfigItem(label="Battery", key="AHCI_RUNTIME_PM_ON_BAT", scope="DEFAULT", type_="cycle", default="auto", options=["on", "auto"], parent_ref="menu_ahci"),
        ConfigItem(label="Timeout (sec)", key="AHCI_RUNTIME_PM_TIMEOUT", scope="DEFAULT", type_="string", default="15", parent_ref="menu_ahci"),

        ConfigItem(label="Optical Bay", key="menu_bay", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Optical"),
        ConfigItem(label="AC", key="BAY_POWEROFF_ON_AC", scope="DEFAULT", type_="cycle", default="0", options=["0", "1"], parent_ref="menu_bay"),
        ConfigItem(label="Battery", key="BAY_POWEROFF_ON_BAT", scope="DEFAULT", type_="cycle", default="0", options=["0", "1"], parent_ref="menu_bay"),
        ConfigItem(label="Bay Device", key="BAY_DEVICE", scope="DEFAULT", type_="string", default="sr0", parent_ref="menu_bay"),
    ],

    # -------------------------------------------------------------------------
    # TAB 3: GRAPHICS (GPU & Display)
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="Intel Power", key="menu_intel", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Intel",
            extended_help="**Intel GPU Profile**\n\nSupported by the `xe` driver (kernel 6.18+). Choose between `base` or `power_saving`."
        ),
        ConfigItem(label="AC", key="INTEL_GPU_POWER_PROFILE_ON_AC", scope="DEFAULT", type_="cycle", default="nil", options=["nil", "base", "power_saving"], parent_ref="menu_intel"),
        ConfigItem(label="Battery", key="INTEL_GPU_POWER_PROFILE_ON_BAT", scope="DEFAULT", type_="cycle", default="nil", options=["nil", "base", "power_saving"], parent_ref="menu_intel"),
        ConfigItem(label="Power Saver", key="INTEL_GPU_POWER_PROFILE_ON_SAV", scope="DEFAULT", type_="cycle", default="nil", options=["nil", "base", "power_saving"], parent_ref="menu_intel"),

        ConfigItem(
            label="Intel Min Freq", key="menu_intmin", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Intel",
            extended_help="**Hardware iGPU Frequencies**\n\nUse `sudo tlp-stat -g` to check your iGPU's min, max, and boost capabilities before applying custom values."
        ),
        ConfigItem(label="AC", key="INTEL_GPU_MIN_FREQ_ON_AC", scope="DEFAULT", type_="string", default="nil", parent_ref="menu_intmin"),
        ConfigItem(label="Battery", key="INTEL_GPU_MIN_FREQ_ON_BAT", scope="DEFAULT", type_="string", default="nil", parent_ref="menu_intmin"),
        ConfigItem(label="Power Saver", key="INTEL_GPU_MIN_FREQ_ON_SAV", scope="DEFAULT", type_="string", default="nil", parent_ref="menu_intmin"),

        ConfigItem(label="Intel Max Freq", key="menu_intmax", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Intel"),
        ConfigItem(label="AC", key="INTEL_GPU_MAX_FREQ_ON_AC", scope="DEFAULT", type_="string", default="nil", parent_ref="menu_intmax"),
        ConfigItem(label="Battery", key="INTEL_GPU_MAX_FREQ_ON_BAT", scope="DEFAULT", type_="string", default="nil", parent_ref="menu_intmax"),
        ConfigItem(label="Power Saver", key="INTEL_GPU_MAX_FREQ_ON_SAV", scope="DEFAULT", type_="string", default="nil", parent_ref="menu_intmax"),

        ConfigItem(label="Intel Boost Freq", key="menu_intboost", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Intel"),
        ConfigItem(label="AC", key="INTEL_GPU_BOOST_FREQ_ON_AC", scope="DEFAULT", type_="string", default="nil", parent_ref="menu_intboost"),
        ConfigItem(label="Battery", key="INTEL_GPU_BOOST_FREQ_ON_BAT", scope="DEFAULT", type_="string", default="nil", parent_ref="menu_intboost"),
        ConfigItem(label="Power Saver", key="INTEL_GPU_BOOST_FREQ_ON_SAV", scope="DEFAULT", type_="string", default="nil", parent_ref="menu_intboost"),

        ConfigItem(
            label="Radeon DPM", key="menu_radeon", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="AMD",
            extended_help="**Dynamic Power Management**\n\nControls DPM for AMD GPUs. 'auto' dynamically selects the optimal profile. 'high' locks clocks to highest state (beware overheating)."
        ),
        ConfigItem(label="AC", key="RADEON_DPM_PERF_LEVEL_ON_AC", scope="DEFAULT", type_="picker", default="auto", options=["nil", "high", "auto", "low"], parent_ref="menu_radeon"),
        ConfigItem(label="Battery", key="RADEON_DPM_PERF_LEVEL_ON_BAT", scope="DEFAULT", type_="picker", default="auto", options=["nil", "high", "auto", "low"], parent_ref="menu_radeon"),
        ConfigItem(label="Power Saver", key="RADEON_DPM_PERF_LEVEL_ON_SAV", scope="DEFAULT", type_="picker", default="low", options=["nil", "high", "auto", "low"], parent_ref="menu_radeon"),

        ConfigItem(
            label="ABM Mod Level", key="menu_abm", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Backlight",
            extended_help="**Adaptive Backlight Modulation**\n\n0: Off. 1..4: Controls maximum brightness reduction allowed by ABM. 4 represents the most aggressive power saving. Savings are made at the expense of color balance."
        ),
        ConfigItem(label="AC", key="AMDGPU_ABM_LEVEL_ON_AC", scope="DEFAULT", type_="cycle", default="0", options=["nil", "0", "1", "2", "3", "4"], parent_ref="menu_abm"),
        ConfigItem(label="Battery", key="AMDGPU_ABM_LEVEL_ON_BAT", scope="DEFAULT", type_="cycle", default="1", options=["nil", "0", "1", "2", "3", "4"], parent_ref="menu_abm"),
        ConfigItem(label="Power Saver", key="AMDGPU_ABM_LEVEL_ON_SAV", scope="DEFAULT", type_="cycle", default="3", options=["nil", "0", "1", "2", "3", "4"], parent_ref="menu_abm"),
    ],

    # -------------------------------------------------------------------------
    # TAB 4: RADIO (Networking & Devices)
    # -------------------------------------------------------------------------
    4: [
        ConfigItem(
            label="Wi-Fi Power", key="menu_wifi", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="WLAN",
            extended_help="**Wireless Power Management**\n\nSaves quite a bit of power when idling, but may cause occasional Wi-Fi connectivity/latency issues. Generally a worthwhile tradeoff on battery."
        ),
        ConfigItem(label="AC", key="WIFI_PWR_ON_AC", scope="DEFAULT", type_="cycle", default="off", options=["on", "off"], parent_ref="menu_wifi"),
        ConfigItem(label="Battery", key="WIFI_PWR_ON_BAT", scope="DEFAULT", type_="cycle", default="on", options=["on", "off"], parent_ref="menu_wifi"),

        ConfigItem(label="Disable WOL", key="WOL_DISABLE", scope="DEFAULT", type_="cycle", default="Y", options=["Y", "N"], group="Ethernet"),

        ConfigItem(label="Disable on Boot", key="DEVICES_TO_DISABLE_ON_STARTUP", scope="DEFAULT", type_="string", default="bluetooth nfc wifi wwan", group="Startup"),
        ConfigItem(label="Enable on Boot", key="DEVICES_TO_ENABLE_ON_STARTUP", scope="DEFAULT", type_="string", default="wifi", group="Startup"),

        ConfigItem(label="Enable on AC", key="DEVICES_TO_ENABLE_ON_AC", scope="DEFAULT", type_="string", default="bluetooth nfc wifi wwan", group="Triggers"),
        ConfigItem(label="Disable on BAT", key="DEVICES_TO_DISABLE_ON_BAT", scope="DEFAULT", type_="string", default="bluetooth nfc wifi wwan", group="Triggers"),
        ConfigItem(label="Disable Idle BAT", key="DEVICES_TO_DISABLE_ON_BAT_NOT_IN_USE", scope="DEFAULT", type_="string", default="bluetooth nfc wifi wwan", group="Triggers"),

        ConfigItem(label="Disable on LAN", key="DEVICES_TO_DISABLE_ON_LAN_CONNECT", scope="DEFAULT", type_="string", default="wifi wwan", group="Wizard"),
        ConfigItem(label="Enable on LAN Dis", key="DEVICES_TO_ENABLE_ON_LAN_DISCONNECT", scope="DEFAULT", type_="string", default="wifi wwan", group="Wizard"),
        ConfigItem(label="Disable on WiFi", key="DEVICES_TO_DISABLE_ON_WIFI_CONNECT", scope="DEFAULT", type_="string", default="wwan", group="Wizard"),
        ConfigItem(label="Enable on WiFi Dis", key="DEVICES_TO_ENABLE_ON_WIFI_DISCONNECT", scope="DEFAULT", type_="string", default="nil", group="Wizard"),
        ConfigItem(label="Disable on WWAN", key="DEVICES_TO_DISABLE_ON_WWAN_CONNECT", scope="DEFAULT", type_="string", default="wifi", group="Wizard"),
        ConfigItem(label="Enable on WWAN Dis", key="DEVICES_TO_ENABLE_ON_WWAN_DISCONNECT", scope="DEFAULT", type_="string", default="nil", group="Wizard"),
        
        ConfigItem(label="Enable on Dock", key="DEVICES_TO_ENABLE_ON_DOCK", scope="DEFAULT", type_="string", default="nil", group="Docks"),
        ConfigItem(label="Disable on Dock", key="DEVICES_TO_DISABLE_ON_DOCK", scope="DEFAULT", type_="string", default="nil", group="Docks"),
        ConfigItem(label="Enable on Undock", key="DEVICES_TO_ENABLE_ON_UNDOCK", scope="DEFAULT", type_="string", default="wifi", group="Docks"),
        ConfigItem(label="Disable on Undock", key="DEVICES_TO_DISABLE_ON_UNDOCK", scope="DEFAULT", type_="string", default="nil", group="Docks"),
    ],

    # -------------------------------------------------------------------------
    # TAB 5: USB
    # -------------------------------------------------------------------------
    5: [
        ConfigItem(
            label="USB Suspend", key="USB_AUTOSUSPEND", scope="DEFAULT", type_="cycle", default="1", options=["0", "1"], group="Global",
            extended_help="**USB Autosuspend**\n\nControls autosuspend for USB devices on boot and when plugged in. 1 = enabled."
        ),
        ConfigItem(label="USB Denylist", key="USB_DENYLIST", scope="DEFAULT", type_="string", default="nil", group="Global"),
        ConfigItem(label="USB Allowlist", key="USB_ALLOWLIST", scope="DEFAULT", type_="string", default="nil", group="Global"),

        ConfigItem(label="Exclude Audio", key="USB_EXCLUDE_AUDIO", scope="DEFAULT", type_="cycle", default="1", options=["0", "1"], group="Exclusions"),
        ConfigItem(label="Exclude BT", key="USB_EXCLUDE_BTUSB", scope="DEFAULT", type_="cycle", default="0", options=["0", "1"], group="Exclusions"),
        ConfigItem(label="Exclude Phone", key="USB_EXCLUDE_PHONE", scope="DEFAULT", type_="cycle", default="0", options=["0", "1"], group="Exclusions"),
        ConfigItem(label="Exclude Printer", key="USB_EXCLUDE_PRINTER", scope="DEFAULT", type_="cycle", default="1", options=["0", "1"], group="Exclusions"),
        ConfigItem(label="Exclude WWAN", key="USB_EXCLUDE_WWAN", scope="DEFAULT", type_="cycle", default="0", options=["0", "1"], group="Exclusions"),
    ],

    # -------------------------------------------------------------------------
    # TAB 6: PCIe
    # -------------------------------------------------------------------------
    6: [
        ConfigItem(
            label="PCIe ASPM", key="menu_pcie", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="ASPM",
            extended_help="**Active State Power Management**\n\nControls power states for PCIe links (L0, L0s, L1).\n`default`: Respects BIOS/UEFI configured policy (Safest).\n`performance`: Disables ASPM entirely.\n`powersave`/`powersupersave`: Aggressively forces deeper L1 sleep states. Change with caution as aggressive settings may cause instability.\n\n*Output the currently set option:*\n`cat /sys/module/pcie_aspm/parameters/policy`"
        ),
        ConfigItem(label="AC", key="PCIE_ASPM_ON_AC", scope="DEFAULT", type_="picker", default="default", options=["nil", "default", "performance", "powersave", "powersupersave"], parent_ref="menu_pcie"),
        ConfigItem(label="Battery", key="PCIE_ASPM_ON_BAT", scope="DEFAULT", type_="picker", default="default", options=["nil", "default", "performance", "powersave", "powersupersave"], parent_ref="menu_pcie"),
        ConfigItem(label="Power Saver", key="PCIE_ASPM_ON_SAV", scope="DEFAULT", type_="picker", default="default", options=["nil", "default", "performance", "powersave", "powersupersave"], parent_ref="menu_pcie"),

        ConfigItem(
            label="Runtime PM", key="menu_rpm", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Runtime",
            extended_help="**Runtime Power Management**\n\nWhile ASPM manages the power state of the physical data link, RPM manages the power state of the device at the end of the link. RPM allows the kernel to put an entire device into a low-power D-state (e.g., D3cold) when it is idle."
        ),
        ConfigItem(label="AC", key="RUNTIME_PM_ON_AC", scope="DEFAULT", type_="cycle", default="on", options=["on", "auto"], parent_ref="menu_rpm"),
        ConfigItem(label="Battery", key="RUNTIME_PM_ON_BAT", scope="DEFAULT", type_="cycle", default="auto", options=["on", "auto"], parent_ref="menu_rpm"),

        ConfigItem(label="PM Denylist", key="RUNTIME_PM_DENYLIST", scope="DEFAULT", type_="string", default="nil", group="Overrides"),
        ConfigItem(label="Driver Denylist", key="RUNTIME_PM_DRIVER_DENYLIST", scope="DEFAULT", type_="string", default="amdgpu mei_me nouveau nvidia xhci_hcd", group="Overrides"),
        ConfigItem(label="Force Enable", key="RUNTIME_PM_ENABLE", scope="DEFAULT", type_="string", default="nil", group="Overrides"),
        ConfigItem(label="Force Disable", key="RUNTIME_PM_DISABLE", scope="DEFAULT", type_="string", default="nil", group="Overrides"),
    ],

    # -------------------------------------------------------------------------
    # TAB 7: AUDIO
    # -------------------------------------------------------------------------
    7: [
        ConfigItem(
            label="Power Save (sec)", key="menu_audio", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Timeout",
            extended_help="**Audio Power Saving Timeout**\n\nHow long to wait in seconds until the audio codec is put to sleep after no audio is playing. Change this to 10 if you hear audio crackling or pops."
        ),
        ConfigItem(label="AC", key="SOUND_POWER_SAVE_ON_AC", scope="DEFAULT", type_="string", default="1", parent_ref="menu_audio"),
        ConfigItem(label="Battery", key="SOUND_POWER_SAVE_ON_BAT", scope="DEFAULT", type_="string", default="1", parent_ref="menu_audio"),
        
        ConfigItem(
            label="Controller Save", key="SOUND_POWER_SAVE_CONTROLLER", scope="DEFAULT", type_="cycle", default="Y", options=["Y", "N"], group="Hardware",
            extended_help="**Controller Sleep**\n\nThe audio subsystem has both a controller and a codec. While the setting above puts the codec to sleep, this setting allows the audio controller on the PCIe bus to be powered down as well."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 8: BATTERY
    # -------------------------------------------------------------------------
    8: [
        ConfigItem(
            label="Main (BAT0)", key="menu_bat0", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Thresholds",
            extended_help="**Charge Thresholds**\n\nControls when charging starts and stops to preserve battery longevity. \n\n*Note:* Support varies wildly by vendor. Some laptops (e.g., ASUS, Lenovo non-ThinkPad, LG, Samsung) only support fixed stop thresholds (e.g., 60% or 80%). If your hardware lacks support for start thresholds, simply type `0` into the Start field. Consult `tlp-stat -b` for your hardware's exact capabilities."
        ),
        ConfigItem(label="Start %", key="START_CHARGE_THRESH_BAT0", scope="DEFAULT", type_="string", default="75", parent_ref="menu_bat0"),
        ConfigItem(label="Stop %", key="STOP_CHARGE_THRESH_BAT0", scope="DEFAULT", type_="string", default="80", parent_ref="menu_bat0"),

        ConfigItem(
            label="Aux (BAT1)", key="menu_bat1", scope="DEFAULT", type_="menu", default=None, is_parent=True, group="Thresholds",
            extended_help="**Secondary Battery Thresholds**\n\nOnly applicable to laptops with a secondary or UltraBay battery. If unused, leave as `nil`."
        ),
        ConfigItem(label="Start %", key="START_CHARGE_THRESH_BAT1", scope="DEFAULT", type_="string", default="75", parent_ref="menu_bat1"),
        ConfigItem(label="Stop %", key="STOP_CHARGE_THRESH_BAT1", scope="DEFAULT", type_="string", default="80", parent_ref="menu_bat1"),
        
        ConfigItem(label="Restore Unplug", key="RESTORE_THRESHOLDS_ON_BAT", scope="DEFAULT", type_="cycle", default="0", options=["0", "1"], group="Safety"),
    ],

    # -------------------------------------------------------------------------
    # TAB 9: PROFILES
    # -------------------------------------------------------------------------
    9: [
        ConfigItem(
            label="Max Battery",
            key="preset_max_battery",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Presets",
            preset_payload={
                "TLP_PROFILE_AC": "SAV",
                "TLP_PROFILE_BAT": "SAV",
                "CPU_SCALING_GOVERNOR_ON_AC": "powersave",
                "CPU_SCALING_GOVERNOR_ON_BAT": "powersave",
                "PLATFORM_PROFILE_ON_AC": "low-power",
                "PLATFORM_PROFILE_ON_BAT": "low-power",
                "CPU_BOOST_ON_AC": "0",
                "CPU_BOOST_ON_BAT": "0"
            },
            extended_help="Forces intense power-saving constraints even while connected to the wall to heavily prioritize thermal reduction and battery preservation."
        ),
        ConfigItem(
            label="Max Performance",
            key="preset_max_performance",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Presets",
            preset_payload={
                "TLP_PROFILE_AC": "PRF",
                "TLP_PROFILE_BAT": "PRF",
                "CPU_SCALING_GOVERNOR_ON_AC": "performance",
                "CPU_SCALING_GOVERNOR_ON_BAT": "performance",
                "PLATFORM_PROFILE_ON_AC": "performance",
                "PLATFORM_PROFILE_ON_BAT": "performance",
                "CPU_BOOST_ON_AC": "1",
                "CPU_BOOST_ON_BAT": "1"
            },
            extended_help="Strips away power constraints and prioritizes raw computing power and screen brightness."
        ),
        ConfigItem(
            label="Factory Reset",
            key="preset_factory_reset",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Danger",
            confirm_message="Are you sure you want to strip all local changes and comment out active parameters? This reverts to the hardware defaults.",
            preset_payload={
                "__ALL_DEFAULTS__": True
            }
        ),
    ]
}
