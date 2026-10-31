#!/usr/bin/env python3

import os
import sys
from pathlib import Path

_dusky_root = Path.home() / "user_scripts" / "dusky_tui"
if str(_dusky_root) not in sys.path:
    sys.path.insert(0, str(_dusky_root))

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
_config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
ENGINE_TYPE = "autostart"
TARGET_FILE = str(_config_home / "hypr/edit_here/source/autostart.lua")
APP_TITLE = "Autostart & Services"

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"
THEME_FILE = str(_config_home / "matugen/generated/dusky_tui.json")
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Profiles"

TAB_NOTICES = {
    0: {
        "level": "info",
        "position": "bottom",
        "message": "\uf05a **Note:** Changes require restart/relogin to take effect."
    },
    1: {
        "level": "info",
        "position": "bottom",
        "message": "\uf05a **Note:** Changes require restart/relogin to take effect."
    },
    2: [
        {
            "level": "warning",
            "position": "top",
            "message": "\uf071 **DISCLAIMER:** This tab configures core system autostart settings (`~/.config/hypr/source/autostart.lua`). Do not modify these options unless you know what you are doing!"
        },
        {
            "level": "info",
            "position": "bottom",
            "message": "\uf05a **Note:** Changes require restart/relogin to take effect."
        }
    ]
}

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = [
    "System",
    "User",
    "Dusky",
    "Profiles"
]

DUSKY_SYSTEM_TARGET = str(_config_home / "hypr/source/autostart.lua")
DUSKY_DISCLAIMER = "Core system autostart configuration. Do not modify unless you know what you are doing."

# =============================================================================
# 4. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: System Configuration (AST mapped natively to hl.config)
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Enable XWayland Subsystem",
            key="enabled",
            scope="xwayland",
            type_="bool",
            default=True,
            group="Compatibility",
            extended_help="**XWayland Support**\n\nToggles the XWayland translation layer globally.\n\n- **ON**: Better compatibility for older X11 applications.\n- **OFF**: Disables the layer to save 20-30 MB of RAM, but strictly prevents non-Wayland applications from functioning."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: User Autostart (User overrides in ~/.config/hypr/edit_here/source/autostart.lua)
    # -------------------------------------------------------------------------
    1: [
        # --- Interface & Desktop ---
        ConfigItem(
            label="Wallpaper Engine (awww)",
            key="awww_daemon",
            scope="autostart",
            type_="bool",
            default=True,
            group="Interface & Desktop",
            extended_help="**Wallpaper Daemon**\n\nAutomatically launches `awww-daemon` on login to render desktop wallpapers."
        ),
        ConfigItem(
            label="Status Bar (Waybar)",
            key="waybar",
            scope="autostart",
            type_="bool",
            default=True,
            group="Interface & Desktop",
            extended_help="**Waybar Status Bar**\n\nAutomatically launches the Waybar panel on startup."
        ),
        ConfigItem(
            label="Network Tray Applet",
            key="nm_applet",
            scope="autostart",
            type_="bool",
            default=False,
            group="Interface & Desktop",
            extended_help="**Network Tray Applet**\n\nLaunches the NetworkManager tray applet automatically."
        ),
        ConfigItem(
            label="Wallpaper Audio Visualizer",
            key="audio_visualizer",
            scope="autostart",
            type_="bool",
            default=False,
            group="Interface & Desktop",
            extended_help="**Audio Visualizer Layer**\n\nLaunches background audio visualizer on boot."
        ),
        ConfigItem(
            label="Wayclick",
            key="wayclick",
            scope="autostart",
            type_="bool",
            default=False,
            group="Interface & Desktop",
            extended_help="**Wayclick**\n\nAutomatically launches Wayclick on startup."
        ),

        # --- Daemons & Services ---
        ConfigItem(
            label="Dusky Audio Studio & Voice DSP",
            key="dusky_audio",
            scope="autostart",
            type_="bool",
            default=False,
            group="Daemons & Services",
            extended_help="**Dusky Audio Studio & Voice DSP Engine**\n\nAutomatically launches Dusky Audio Studio on login, restoring real-time PipeWire vocoder, noise cancellation, spatial DSP, and parametric EQ settings."
        ),
        ConfigItem(
            label="LocalSend (LAN File Transfer)",
            key="localsend",
            scope="autostart",
            type_="bool",
            default=False,
            group="Daemons & Services",
            extended_help="**LocalSend Tray Daemon**\n\nEfficient AirDrop alternative (LAN only). Runs as `localsend --hidden` tray daemon — idle ~0% CPU / ~30 MB RAM, scales only during active transfer. Uses 224.0.0.167:53317/udp multicast discovery + 53317/tcp HTTPS. Native `paru -S localsend` preferred, Flatpak `org.localsend.localsend_app` fallback auto-detected. Thunar right-click *Send via LocalSend* works without this; this toggle only controls background receive/tray on login (opt-in, off by default). If tray missing after reboot, try without `--hidden`."
        ),
        ConfigItem(
            label="Gnome Keyring",
            key="gnome_keyring",
            scope="autostart",
            type_="bool",
            default=False,
            group="Daemons & Services",
            extended_help="**Gnome Keyring**\n\nLaunches the Gnome Keyring secrets daemon for storing application credentials."
        ),
        ConfigItem(
            label="Hypridle Manager",
            key="hypridle",
            scope="autostart",
            type_="bool",
            default=False,
            group="Daemons & Services",
            extended_help="**Hypridle Daemon**\n\nLaunches Hyprland's idle management service for screen locking and dimming."
        ),
        ConfigItem(
            label="Layout Notifier",
            key="layout_notify",
            scope="autostart",
            type_="bool",
            default=False,
            group="Daemons & Services",
            extended_help="**Layout Notifier**\n\nRuns keyboard layout notification script on startup."
        ),
        ConfigItem(
            label="Root XHost Access",
            key="xhost_root",
            scope="autostart",
            type_="bool",
            default=False,
            group="Daemons & Services",
            extended_help="**Root XHost Access**\n\nGrants root access to the display server (needed for GUI administrative tools)."
        ),
        ConfigItem(
            label="Hyprpm Plugin Reload",
            key="hyprpm_reload",
            scope="autostart",
            type_="bool",
            default=False,
            group="Daemons & Services",
            extended_help="**Hyprpm Reload**\n\nReloads Hyprland plugins automatically upon startup."
        ),

        # --- Clipboard Services ---
        ConfigItem(
            label="Cliphist Text Listener",
            key="cliphist_text",
            scope="autostart",
            type_="bool",
            default=False,
            group="Clipboard Services",
            extended_help="**Cliphist Text**\n\nStarts text clipboard history listener on login."
        ),
        ConfigItem(
            label="Cliphist Image Listener",
            key="cliphist_image",
            scope="autostart",
            type_="bool",
            default=False,
            group="Clipboard Services",
            extended_help="**Cliphist Image**\n\nStarts image clipboard history listener on login."
        ),
        ConfigItem(
            label="Cliphist DB Text Listener",
            key="cliphist_db_text",
            scope="autostart",
            type_="bool",
            default=False,
            group="Clipboard Services",
            extended_help="**Cliphist Custom DB Text**\n\nStarts text clipboard listener using custom database environment."
        ),
        ConfigItem(
            label="Cliphist DB Image Listener",
            key="cliphist_db_image",
            scope="autostart",
            type_="bool",
            default=False,
            group="Clipboard Services",
            extended_help="**Cliphist Custom DB Image**\n\nStarts image clipboard listener using custom database environment."
        ),
        ConfigItem(
            label="Clipboard Persistence",
            key="clip_persist",
            scope="autostart",
            type_="bool",
            default=False,
            group="Clipboard Services",
            extended_help="**Clipboard Persistence**\n\nEnsures copied selection remains active even if source app exits."
        ),

        # --- Environment Integration ---
        ConfigItem(
            label="Import Systemd Env",
            key="systemd_env",
            scope="autostart",
            type_="bool",
            default=False,
            group="Environment",
            extended_help="**Systemd Environment**\n\nImports current environment variables into systemd user instance."
        ),
        ConfigItem(
            label="Update DBus Env",
            key="dbus_env",
            scope="autostart",
            type_="bool",
            default=False,
            group="Environment",
            extended_help="**DBus Environment**\n\nUpdates DBus activation environment with systemd variables."
        ),

        # --- Dusky Glance Collapsible Menu ---
        ConfigItem(
            label="Dusky Glance",
            key="menu_dusky_glance",
            scope="DEFAULT",
            type_="menu",
            default=None,
            is_parent=True,
            expanded=False,
            group="Dashboards",
            extended_help="**Dusky Glance Dashboards**\n\nToggle autostart for Rofi system monitoring glance overlays on login."
        ),
        ConfigItem(
            label="CPU Usage",
            key="glance_cpu",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**CPU Glance Autostart**\n\nLaunches Rofi CPU monitoring overlay at startup."
        ),
        ConfigItem(
            label="CPU Power Draw",
            key="glance_cpu_power",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**CPU Power Glance Autostart**\n\nLaunches CPU power consumption overlay at startup."
        ),
        ConfigItem(
            label="Memory (RAM)",
            key="glance_ram",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**RAM Glance Autostart**\n\nLaunches RAM usage overlay at startup."
        ),
        ConfigItem(
            label="RAM Available (Headroom)",
            key="glance_ram_available",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**RAM Available Glance Autostart**\n\nLaunches available memory headroom overlay at startup."
        ),
        ConfigItem(
            label="RAM Free (Unallocated)",
            key="glance_ram_free",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**RAM Free Glance Autostart**\n\nLaunches unallocated memory overlay at startup."
        ),
        ConfigItem(
            label="RAM Temperature",
            key="glance_ram_temp",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**RAM Temp Glance Autostart**\n\nLaunches memory temperature overlay at startup."
        ),
        ConfigItem(
            label="ZRAM Usage",
            key="glance_zram",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**ZRAM Glance Autostart**\n\nLaunches ZRAM compression overlay at startup."
        ),
        ConfigItem(
            label="Temperatures",
            key="glance_temp",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Temperature Glance Autostart**\n\nLaunches CPU/GPU thermal overlay at startup."
        ),
        ConfigItem(
            label="Battery Status",
            key="glance_battery",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Battery Glance Autostart**\n\nLaunches battery status overlay at startup."
        ),
        ConfigItem(
            label="Battery Percent",
            key="glance_battery_percent",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Battery Percent Glance Autostart**\n\nLaunches battery percentage overlay at startup."
        ),
        ConfigItem(
            label="Battery Power Draw",
            key="glance_battery_watts",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Battery Power Draw Autostart**\n\nLaunches battery power draw overlay at startup."
        ),
        ConfigItem(
            label="Battery Time Remaining",
            key="glance_battery_time",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Battery Time Remaining Autostart**\n\nLaunches battery time remaining overlay at startup."
        ),
        ConfigItem(
            label="GPU Power Draw",
            key="glance_gpu_power",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**GPU Power Draw Autostart**\n\nLaunches GPU power draw overlay at startup."
        ),
        ConfigItem(
            label="GPU Usage",
            key="glance_gpu_usage",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**GPU Usage Autostart**\n\nLaunches GPU utilization overlay at startup."
        ),
        ConfigItem(
            label="GPU Memory",
            key="glance_gpu_mem",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**GPU Memory Autostart**\n\nLaunches GPU VRAM usage overlay at startup."
        ),
        ConfigItem(
            label="Network Bandwidth",
            key="glance_network",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Network Glance Autostart**\n\nLaunches network bandwidth overlay at startup."
        ),
        ConfigItem(
            label="Network Download Speed",
            key="glance_network_down",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Network Download Glance Autostart**\n\nLaunches live download speed overlay at startup."
        ),
        ConfigItem(
            label="Network Upload Speed",
            key="glance_network_up",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Network Upload Glance Autostart**\n\nLaunches live upload speed overlay at startup."
        ),
        ConfigItem(
            label="Network Combined Speed",
            key="glance_network_combined",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Network Combined Glance Autostart**\n\nLaunches combined network speed overlay at startup."
        ),
        ConfigItem(
            label="Session Downloaded Data",
            key="glance_network_down_session",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Session Download Glance Autostart**\n\nLaunches session downloaded data overlay at startup."
        ),
        ConfigItem(
            label="Session Uploaded Data",
            key="glance_network_up_session",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Session Upload Glance Autostart**\n\nLaunches session uploaded data overlay at startup."
        ),
        ConfigItem(
            label="Session Total Traffic",
            key="glance_network_session",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Session Total Traffic Glance Autostart**\n\nLaunches session total traffic overlay at startup."
        ),
        ConfigItem(
            label="Boot Total Downloaded",
            key="glance_network_boot_down",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Boot Total Download Glance Autostart**\n\nLaunches boot total downloaded data overlay at startup."
        ),
        ConfigItem(
            label="Boot Total Uploaded",
            key="glance_network_boot_up",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Boot Total Upload Glance Autostart**\n\nLaunches boot total uploaded data overlay at startup."
        ),
        ConfigItem(
            label="Boot Total Traffic",
            key="glance_network_boot",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Boot Total Traffic Glance Autostart**\n\nLaunches boot total traffic overlay at startup."
        ),
        ConfigItem(
            label="System Uptime",
            key="glance_uptime",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Uptime Glance Autostart**\n\nLaunches system uptime overlay at startup."
        ),
        ConfigItem(
            label="Workspace Overview",
            key="glance_workspace",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Workspace Glance Autostart**\n\nLaunches workspace overview overlay at startup."
        ),
        ConfigItem(
            label="Disk Usage",
            key="glance_disk",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Disk Glance Autostart**\n\nLaunches disk usage overlay at startup."
        ),
        ConfigItem(
            label="Disk Read Activity",
            key="glance_disk_read",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Disk Read Autostart**\n\nLaunches disk read activity overlay at startup."
        ),
        ConfigItem(
            label="Disk Write Activity",
            key="glance_disk_write",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Disk Write Autostart**\n\nLaunches disk write activity overlay at startup."
        ),
        ConfigItem(
            label="Disk Temperature",
            key="glance_disk_temp",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Disk Temp Autostart**\n\nLaunches disk temperature overlay at startup."
        ),
        ConfigItem(
            label="Clock & Calendar",
            key="glance_clock",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Clock Glance Autostart**\n\nLaunches clock and calendar overlay at startup."
        ),
        ConfigItem(
            label="Compact Clock",
            key="glance_clock_short",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Compact Clock Glance Autostart**\n\nLaunches minimal clock overlay at startup."
        ),
        ConfigItem(
            label="Live Stopwatch",
            key="glance_stopwatch",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Stopwatch Glance Autostart**\n\nLaunches live stopwatch counter at startup."
        ),
        ConfigItem(
            label="Countdown Timer (15m)",
            key="glance_timer",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**Timer Glance Autostart**\n\nLaunches 15-minute countdown timer overlay at startup."
        ),
        ConfigItem(
            label="GPU HUD Overlay",
            key="glance_hud",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**GPU HUD Autostart**\n\nLaunches GPU HUD overlay at startup."
        ),
        ConfigItem(
            label="World Clock (New York)",
            key="glance_world_ny",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**World Clock NY Autostart**\n\nLaunches New York world clock overlay at startup."
        ),
        ConfigItem(
            label="World Clock (Tokyo)",
            key="glance_world_tokyo",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**World Clock Tokyo Autostart**\n\nLaunches Tokyo world clock overlay at startup."
        ),
        ConfigItem(
            label="World Clock (London)",
            key="glance_world_london",
            scope="autostart",
            type_="bool",
            default=False,
            parent_ref="menu_dusky_glance",
            group="Dashboards",
            extended_help="**World Clock London Autostart**\n\nLaunches London world clock overlay at startup."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: Dusky Base Configuration (Targets ~/.config/hypr/source/autostart.lua)
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Import Systemd Env",
            key="systemd_env",
            scope="autostart",
            type_="bool",
            default=True,
            group="Core System Services",
            target_file_override=DUSKY_SYSTEM_TARGET,
            warning_msg=DUSKY_DISCLAIMER,
            extended_help="**Systemd Environment Sync**\n\nImports Wayland/Desktop environment variables into systemd user instance at startup."
        ),
        ConfigItem(
            label="Update DBus Env",
            key="dbus_env",
            scope="autostart",
            type_="bool",
            default=True,
            group="Core System Services",
            target_file_override=DUSKY_SYSTEM_TARGET,
            warning_msg=DUSKY_DISCLAIMER,
            extended_help="**DBus Activation Environment**\n\nUpdates DBus activation environment with systemd variables."
        ),
        ConfigItem(
            label="Hyprland Session Target Start",
            key="session_target",
            scope="autostart",
            type_="bool",
            default=True,
            group="Core System Services",
            target_file_override=DUSKY_SYSTEM_TARGET,
            warning_msg=DUSKY_DISCLAIMER,
            extended_help="**Hyprland Session Target**\n\nStarts `hyprland-session.target` systemd user service on graphical boot."
        ),
        ConfigItem(
            label="Compositor OOM Protection (choom)",
            key="choom_oom",
            scope="autostart",
            type_="bool",
            default=True,
            group="Core System Services",
            target_file_override=DUSKY_SYSTEM_TARGET,
            warning_msg=DUSKY_DISCLAIMER,
            extended_help="**OOM Killer Protection**\n\nAdjusts Hyprland compositor OOM score to prevent system force-kills during high memory pressure."
        ),
        ConfigItem(
            label="Clipboard Persistence Daemon",
            key="clip_persist",
            scope="autostart",
            type_="bool",
            default=True,
            group="Core System Services",
            target_file_override=DUSKY_SYSTEM_TARGET,
            warning_msg=DUSKY_DISCLAIMER,
            extended_help="**wl-clip-persist Daemon**\n\nPersists clipboard selections across application closures."
        ),
        ConfigItem(
            label="Cliphist Text Watcher",
            key="cliphist_db_text",
            scope="autostart",
            type_="bool",
            default=True,
            group="Core System Services",
            target_file_override=DUSKY_SYSTEM_TARGET,
            warning_msg=DUSKY_DISCLAIMER,
            extended_help="**Cliphist Text Store Watcher**\n\nWatches text clipboard selections and writes to cliphist database."
        ),
        ConfigItem(
            label="Cliphist Image Watcher",
            key="cliphist_db_image",
            scope="autostart",
            type_="bool",
            default=True,
            group="Core System Services",
            target_file_override=DUSKY_SYSTEM_TARGET,
            warning_msg=DUSKY_DISCLAIMER,
            extended_help="**Cliphist Image Store Watcher**\n\nWatches image clipboard selections and writes to cliphist database."
        ),
        ConfigItem(
            label="Session Shutdown Target Stop",
            key="shutdown_target",
            scope="autostart",
            type_="bool",
            default=True,
            group="Core System Services",
            target_file_override=DUSKY_SYSTEM_TARGET,
            warning_msg=DUSKY_DISCLAIMER,
            extended_help="**Session Shutdown Target**\n\nStops `hyprland-session.target` automatically when Hyprland shuts down."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 3: Profiles (System Presets)
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="Deploy Lightweight Mode",
            key="preset_lightweight_mode",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Optimization",
            preset_partial=True,
            preset_payload={
                "xwayland.enabled": False,
                "autostart.audio_visualizer": False
            },
            extended_help="**Lightweight Preset**\n\nOptimizes RAM usage by aggressively disabling XWayland and non-essential background layers."
        ),
        ConfigItem(
            label="Restore Standard Defaults",
            key="preset_restore_defaults",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Optimization",
            preset_payload={
                "xwayland.enabled": True,
                "autostart.waybar": True,
                "autostart.awww_daemon": True
            },
            extended_help="**Standard Defaults**\n\nRe-enables standard desktop services and XWayland compatibility layer."
        ),
    ]
}

# =============================================================================
# DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    import sys, subprocess
    from pathlib import Path

    script_path = Path(__file__).resolve()
    main_router = Path.home() / "user_scripts" / "dusky_tui" / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
