#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: HYPRIDLE CONFIGURATION SCHEMA
===============================================================================
"""
import sys
from pathlib import Path

# Inject the dusky_tui root into sys.path for direct execution
_DUSKY_TUI_ROOT = Path(__file__).resolve().parent.parent / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING (REQUIRED)
# =============================================================================
ENGINE_TYPE = "hyprlang"
TARGET_FILE = "~/.config/hypr/hypridle.conf"
APP_TITLE = "Dusky Hypridle"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Profiles" # Routes user-created presets to the 3rd tab

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = ["Power States", "Profiles"]

# =============================================================================
# 4. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: POWER STATES
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Auto Lock (s)",
            key="timeout",
            scope="listener:3",
            type_="int",
            min_val=30, max_val=2000000000, step=30,
            default=300,
            group="Security",
            extended_help="**Auto Lock Session**\n\nTime in seconds before the screen automatically locks.\n\n**Note**: You can press `Enter` and manually type `2000000000` to effectively disable this (Never)."
        ),
        ConfigItem(
            label="Screen Off DPMS (s)",
            key="timeout",
            scope="listener:4",
            type_="int",
            min_val=30, max_val=2000000000, step=30,
            default=330,
            group="Power",
            extended_help="**Monitor Power Off**\n\nTime in seconds before the monitors are powered off (DPMS). This is critical for saving battery.\n\n**Note**: You can press `Enter` and manually type `2000000000` to effectively disable this (Never)."
        ),
        ConfigItem(
            label="System Suspend (s)",
            key="timeout",
            scope="listener:5",
            type_="int",
            min_val=60, max_val=2000000000, step=60,
            default=600,
            group="Power",
            extended_help="**System Suspend**\n\nTime in seconds before the system fully suspends to RAM.\n\n**Note**: You can press `Enter` and manually type `2000000000` to effectively disable this (Never)."
        ),
        ConfigItem(
            label="Keyboard Backlight Dim (s)",
            key="timeout",
            scope="listener:1",
            type_="int",
            min_val=10, max_val=2000000000, step=10,
            default=140,
            group="Hardware",
            extended_help="**Keyboard Backlight Timeout**\n\nTime in seconds before keyboard backlight dims to zero.\n\n**Note**: You can press `Enter` and manually type `2000000000` to effectively disable this (Never)."
        ),
        ConfigItem(
            label="Screen Dim Warning (s)",
            key="timeout",
            scope="listener:2",
            type_="int",
            min_val=10, max_val=2000000000, step=10,
            default=150,
            group="Hardware",
            extended_help="**Screen Dim Warning**\n\nTime in seconds before the screen dims as a visual warning right before sleep/lock.\n\n**Note**: You can press `Enter` and manually type `2000000000` to effectively disable this (Never)."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: PROFILES & PRESETS
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Apply 'Maximum Battery Saver' Profile",
            key="preset_battery",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Profiles",
            preset_payload={
                "listener:1.timeout": 60,
                "listener:2.timeout": 90,
                "listener:3.timeout": 120,
                "listener:4.timeout": 150,
                "listener:5.timeout": 300
            },
            extended_help="**Maximum Battery Saver**\n\nHighly aggressive power saving. Dims screens and suspends the system very quickly when idle."
        ),
        ConfigItem(
            label="Apply 'Presentation / Media' Profile",
            key="preset_presentation",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Profiles",
            preset_payload={
                "listener:1.timeout": 2000000000,
                "listener:2.timeout": 2000000000,
                "listener:3.timeout": 2000000000,
                "listener:4.timeout": 2000000000,
                "listener:5.timeout": 2000000000
            },
            extended_help="**Presentation Mode**\n\nEffectively disables all idle timeouts. Useful when watching movies, presenting, or running long background tasks without audio playing."
        ),
        ConfigItem(
            label="Factory Reset Everything",
            key="preset_factory_reset",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Reset",
            preset_payload={
                "__ALL_DEFAULTS__": True
            },
            extended_help="**Factory Reset**\n\nReverts all idle timeouts back to their original configuration."
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
