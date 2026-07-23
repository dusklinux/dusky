#!/usr/bin/env python3
import sys
sys.dont_write_bytecode = True
import json
from pathlib import Path

tui_root = Path(__file__).resolve().parents[2] / "dusky_tui"
if str(tui_root) not in sys.path:
    sys.path.insert(0, str(tui_root))

import sys
from pathlib import Path

_dusky_root = Path.home() / "user_scripts" / "dusky_tui"
if str(_dusky_root) not in sys.path:
    sys.path.insert(0, str(_dusky_root))

import sys
from pathlib import Path

_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

# Load bios defaults from the baseline tracking state cache
STATE_FILE = Path("/dev/shm/dusky_rapl_state.json")
bios_defaults = {}
if STATE_FILE.exists():
    try:
        with open(STATE_FILE) as f:
            boot_data = json.load(f).get("boot", {})
            bios_defaults = {
                "pl1": boot_data.get("constraint_0_power_limit_uw", 90_000_000) // 1_000_000,
                "pl2": boot_data.get("constraint_1_power_limit_uw", 115_000_000) // 1_000_000,
                "pl4": boot_data.get("constraint_2_power_limit_uw", 215_000_000) // 1_000_000,
                "pl1_time": round(boot_data.get("constraint_0_time_window_us", 28_000_000) / 1_000_000, 2),
                "pl2_time": round(boot_data.get("constraint_1_time_window_us", 2000) / 1_000_000, 4)
            }
    except Exception:
        pass

# Fallback values
if not bios_defaults:
    bios_defaults = {
        "pl1": 90,
        "pl2": 115,
        "pl4": 215,
        "pl1_time": 28.00,
        "pl2_time": 0.0020
    }

ENGINE_TYPE = "pkg_throttle"
TARGET_FILE = "/sys/class/powercap"
APP_TITLE = "Dusky Power Limit Manager"
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
REQUIRE_ROOT = True

TABS = [
    "Power Limits",
    "Time Windows",
    "Presets"
]

USER_PRESETS_TAB = "Presets"

TAB_NOTICES = {
    0: {
        "level": "warning",
        "position": "bottom",
        "message": "Setting PL4 too low can trigger a failsafe hardware lock (minimum clock throttle) to protect voltage regulators. Keep PL4 at its BIOS default unless you explicitly need to clamp peak currents."
    }
}

SCHEMA = {
    0: [
        ConfigItem(
            label="PL1 (Long-Term Limit)",
            key="pl1",
            type_="int",
            default=bios_defaults["pl1"],
            min_val=3,
            max_val=1000,
            step=1,
            extended_help="Sustained long-term CPU package power limit envelope (in Watts). Applies under continuous high workloads."
        ),
        ConfigItem(
            label="PL2 (Short-Term Limit)",
            key="pl2",
            type_="int",
            default=bios_defaults["pl2"],
            min_val=3,
            max_val=1000,
            step=1,
            extended_help="Maximum transient boost power envelope (in Watts). Only sustained for the duration of the PL2 time window."
        ),
        ConfigItem(
            label="PL4 (Peak Limit)",
            key="pl4",
            type_="int",
            default=bios_defaults["pl4"],
            min_val=3,
            max_val=1000,
            step=5,
            extended_help="Absolute physical hardware power spike clamp (in Watts). Prevents PSU protection triggers on rapid power transitions."
        )
    ],
    1: [
        ConfigItem(
            label="PL1 Time Window",
            key="pl1_time",
            type_="float",
            default=bios_defaults["pl1_time"],
            min_val=0.01,
            max_val=150.0,
            step=0.5,
            extended_help="Rolling averaging window (in seconds) for long-term PL1 enforcement."
        ),
        ConfigItem(
            label="PL2 Time Window",
            key="pl2_time",
            type_="float",
            default=bios_defaults["pl2_time"],
            min_val=0.0001,
            max_val=2.0,
            step=0.0005,
            extended_help="Maximum duration envelope (in seconds) that the CPU package is permitted to boost up to PL2 power limits before scaling down."
        )
    ]
}

if __name__ == "__main__":
    import sys
    import subprocess
    from pathlib import Path

    if len(sys.argv) > 1 and sys.argv[1] == "--restore":
        from python.engines.pkg_throttle import PkgThrottleEngine
        engine = PkgThrottleEngine()
        if engine.restore_state():
            print("[OK] Successfully restored persistent CPU power limits.")
            sys.exit(0)
        else:
            print("[*] No persistent power limits state found to restore (or failed to restore).")
            sys.exit(0)

    main_py = Path(__file__).resolve().parents[2] / "dusky_tui" / "python" / "main" / "main.py"

    cmd = [sys.executable, str(main_py), str(Path(__file__).resolve()), *sys.argv[1:]]
    try:
        res = subprocess.run(cmd)
        sys.exit(res.returncode)
    except Exception as e:
        print(f"[-] Error delegating to dusky_tui: {e}")
        sys.exit(1)
