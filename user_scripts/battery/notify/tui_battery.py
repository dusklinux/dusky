#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: BATTERY MONITOR CONFIGURATION SCHEMA
===============================================================================
"""
import sys
from pathlib import Path

# Inject the dusky_tui root into sys.path for direct execution
_DUSKY_TUI_ROOT = Path(__file__).resolve().parent.parent.parent / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

import sys
from pathlib import Path

_dusky_root = Path.home() / "user_scripts" / "dusky_tui"
if str(_dusky_root) not in sys.path:
    sys.path.insert(0, str(_dusky_root))

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING (REQUIRED)
# =============================================================================
ENGINE_TYPE = "shell_fallback"
TARGET_FILE = "~/user_scripts/battery/notify/battery_notify.sh"
APP_TITLE = "Dusky Battery"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Presets"

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = ["Thresholds", "Daemon & Timings", "Sounds & Alerts", "Presets"]

# =============================================================================
# 4. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: THRESHOLDS
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Restart Service",
            key="action_restart_service",
            scope="DEFAULT",
            type_="action",
            default="systemctl --user restart dusky_battery.service",
            group="Control",
            extended_help="**Restart Monitor Service**\n\nRestarts the systemd user service `dusky_battery` to apply all changes made to the script immediately."
        ),
        ConfigItem(
            label="Full Threshold (%)",
            key="BATTERY_FULL_THRESHOLD",
            scope="DEFAULT",
            type_="int",
            min_val=50, max_val=100, step=1,
            default=100,
            group="Thresholds",
            extended_help="**Fully Charged Threshold**\n\nPercentage at which the battery is considered full. A notification is triggered once the battery reaches this level."
        ),
        ConfigItem(
            label="Low Threshold (%)",
            key="BATTERY_LOW_THRESHOLD",
            scope="DEFAULT",
            type_="int",
            min_val=10, max_val=40, step=1,
            default=20,
            group="Thresholds",
            extended_help="**Low Battery Alert Level**\n\nPercentage threshold below which the low battery warning is active."
        ),
        ConfigItem(
            label="Critical Threshold (%)",
            key="BATTERY_CRITICAL_THRESHOLD",
            scope="DEFAULT",
            type_="int",
            min_val=2, max_val=20, step=1,
            default=10,
            group="Thresholds",
            extended_help="**Critical Shutdown Level**\n\nPercentage threshold at which the critical shutdown is triggered. The service will prompt a warning before auto-suspending the system."
        ),
        ConfigItem(
            label="Unplug Alert Level (%)",
            key="BATTERY_UNPLUG_THRESHOLD",
            scope="DEFAULT",
            type_="int",
            min_val=50, max_val=100, step=1,
            default=100,
            group="Thresholds",
            extended_help="**Unplug Warning Level**\n\nOnly triggers an unplug alert if the charger is disconnected while the battery is at or above this percentage."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: DAEMON & TIMINGS
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Auto-Suspend on Critical",
            key="DO_SUSPEND",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Power Management",
            extended_help="**Auto Suspend Enable**\n\nIf enabled, the system will suspend to RAM when the battery level drops below the critical threshold."
        ),
        ConfigItem(
            label="Suspend Grace Period (s)",
            key="SUSPEND_GRACE_SEC",
            scope="DEFAULT",
            type_="int",
            min_val=5, max_val=600, step=5,
            default=60,
            group="Power Management",
            extended_help="**Grace Countdown Period**\n\nSeconds to wait and show warning prompts before initiating auto-suspend upon reaching critical battery capacity."
        ),
        ConfigItem(
            label="Safety Backup Poll (s)",
            key="SAFETY_POLL_INTERVAL",
            scope="DEFAULT",
            type_="int",
            min_val=5, max_val=600, step=5,
            default=60,
            group="Timings",
            extended_help="**Safety Backup Interval**\n\nBackup polling interval in seconds. Triggers a state update query in case the UPower DBus monitor event stream goes silent."
        ),
        ConfigItem(
            label="Full Alert Repeat (min)",
            key="REPEAT_FULL_MIN",
            scope="DEFAULT",
            type_="int",
            min_val=1, max_val=1440, step=1,
            default=999,
            group="Timings",
            extended_help="**Full Alert Loop**\n\nTime in minutes to wait before repeating the full battery notification. 999 means effectively once."
        ),
        ConfigItem(
            label="Low Alert Repeat (min)",
            key="REPEAT_LOW_MIN",
            scope="DEFAULT",
            type_="int",
            min_val=1, max_val=60, step=1,
            default=3,
            group="Timings",
            extended_help="**Low Alert Repeat Interval**\n\nMinutes to wait before re-triggering the low battery warning if the level remains low."
        ),
        ConfigItem(
            label="Critical Repeat (min)",
            key="REPEAT_CRITICAL_MIN",
            scope="DEFAULT",
            type_="int",
            min_val=1, max_val=60, step=1,
            default=1,
            group="Timings",
            extended_help="**Critical Alert Loop**\n\nMinutes to wait before re-sending the critical warning notice."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: SOUNDS & ALERTS
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Critical Message",
            key="MSG_CRITICAL",
            scope="DEFAULT",
            type_="string",
            default="Suspending system!",
            group="Texts",
            extended_help="**Shutdown Warning Body**\n\nText message printed on screen inside the critical warning notification."
        ),
        ConfigItem(
            label="Low Battery Sound",
            key="SOUND_LOW",
            scope="DEFAULT",
            type_="string",
            default="/usr/share/sounds/freedesktop/stereo/complete.oga",
            group="Sounds",
            extended_help="**Low Battery Sound Path**\n\nAbsolute file path to the audio file played when the low warning triggers."
        ),
        ConfigItem(
            label="Critical Sound",
            key="SOUND_CRITICAL",
            scope="DEFAULT",
            type_="string",
            default="/usr/share/sounds/freedesktop/stereo/suspend-error.oga",
            group="Sounds",
            extended_help="**Critical Shutdown Sound Path**\n\nAbsolute file path to the audio file played when the critical shutdown warning fires."
        ),
        ConfigItem(
            label="Plug Sound",
            key="SOUND_PLUG",
            scope="DEFAULT",
            type_="string",
            default="/usr/share/sounds/freedesktop/stereo/device-added.oga",
            group="Sounds",
            extended_help="**Power Plugged Sound Path**\n\nAbsolute file path to the audio file played when the charger is connected."
        ),
        ConfigItem(
            label="Unplug Sound",
            key="SOUND_UNPLUG",
            scope="DEFAULT",
            type_="string",
            default="/usr/share/sounds/freedesktop/stereo/device-removed.oga",
            group="Sounds",
            extended_help="**Power Unplugged Sound Path**\n\nAbsolute file path to the audio file played when the charger is disconnected."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 3: PRESETS
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="Apply 'Balanced Defaults'",
            key="preset_balanced",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Profiles",
            preset_payload={
                "BATTERY_FULL_THRESHOLD": 100,
                "BATTERY_LOW_THRESHOLD": 20,
                "BATTERY_CRITICAL_THRESHOLD": 10,
                "SUSPEND_GRACE_SEC": 60,
                "DO_SUSPEND": True
            },
            extended_help="**Balanced Configuration**\n\nSets standard thresholds: warning at 20%, critical suspend at 10% with a 60-second warning grace period."
        ),
        ConfigItem(
            label="Apply 'Aggressive Power Saving'",
            key="preset_saving",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Profiles",
            preset_payload={
                "BATTERY_FULL_THRESHOLD": 95,
                "BATTERY_LOW_THRESHOLD": 25,
                "BATTERY_CRITICAL_THRESHOLD": 12,
                "SUSPEND_GRACE_SEC": 30,
                "DO_SUSPEND": True
            },
            extended_help="**Aggressive Power Management**\n\nRaises warnings earlier: warning at 25%, critical suspend at 12% with a shorter 30-second warning countdown."
        ),
        ConfigItem(
            label="Factory Reset Defaults",
            key="preset_factory_reset",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Reset",
            preset_payload={
                "__ALL_DEFAULTS__": True
            },
            extended_help="**Nuclear Factory Reset**\n\nReverts all variables inside the script back to their initial coded default states."
        ),
    ]
}

# =============================================================================
# DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    import subprocess
    main_script = _DUSKY_TUI_ROOT / "python" / "main" / "main.py"
    if main_script.exists():
        subprocess.run([sys.executable, str(main_script), str(Path(__file__).resolve())])
    else:
        print(f"[-] Error: Could not find router at {main_script}")
