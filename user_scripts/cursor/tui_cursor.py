#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: CURSOR CONFIGURATION SCHEMA
===============================================================================
Target: ~/.config/dusky/settings/cursor.conf
Engine: env (KEY=VALUE store read by cursor/color/dusky_cursor.py)

Single page. Theme is always Dusky. Edit values, then press footer
Apply to rebuild the theme and push every layer (compositor, gsettings,
Lua env, icon fallback, GTK). Empty colors mean "follow the matugen theme".
===============================================================================
"""

import sys
from pathlib import Path

_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "env"
TARGET_FILE = "~/.config/dusky/settings/cursor.conf"
APP_TITLE = "Cursor Settings"

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = False
APPLY_COMMAND = "python3 ~/user_scripts/cursor/color/dusky_cursor.py --apply"
ROW_GATE = [
    {
        "watch_key": "THEME",
        "allow": ["Dusky"],
        "gated": ["BASE", "BORDER", "ACCENT", "WATCH_BG", "action_reset_colors"],
        "hide": True,
        "message": "Base and border colors only work with the Dusky theme. Other themes apply as-is.",
    },
    {
        "watch_key": "THEME",
        "allow": ["Bibata-Modern-Classic"],
        "gated": ["action_restore"],
        "hide": True,
        "message": None,
    },
]

TAB_NOTICES = {
    0: {"level": "info", "position": "top",
        "message": "Edit values, then press footer **Apply**. Empty color = follow the matugen theme."},
}

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = [
    "Cursor",
]

COLOR_PRESETS = [
    "#ffffff", "#000000", "#ff5555", "#faba72", "#f8e369", "#5ff08a",
    "#5fd8f0", "#5f8ff0", "#b48cf2", "#f06cb0", "#83d5c6",
]

# =============================================================================
# 4. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # Single page: Theme / Colors / Actions sections
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Cursor Theme",
            key="THEME",
            scope="DEFAULT",
            type_="string",
            default="Dusky",
            options=["Adwaita", "Bibata-Modern-Classic", "Dusky"],
            group="Theme",
            extended_help="**Cursor Theme**\n\nDusky is rebuilt from your colors. Stock themes apply as-is and switch the color rows off.",
        ),
        ConfigItem(
            label="Cursor Size",
            key="SIZE",
            scope="DEFAULT",
            type_="int",
            default=18,
            min_val=8,
            max_val=64,
            step=1,
            group="Theme",
            extended_help="**Cursor Size**\n\nPointer size in pixels, 8 to 64. Snaps to the theme's real bitmaps on apply.",
        ),
        ConfigItem(
            label="Base Fill",
            key="BASE",
            scope="DEFAULT",
            type_="color",
            default="",
            options=COLOR_PRESETS,
            group="Colors",
            extended_help="**Base Fill**\n\nThe dark middle of the pointer. Empty follows the matugen accent.",
        ),
        ConfigItem(
            label="Border Outline",
            key="BORDER",
            scope="DEFAULT",
            type_="color",
            default="",
            options=COLOR_PRESETS,
            group="Colors",
            extended_help="**Border Outline**\n\nThe light edge of the pointer. Empty follows the matugen accent.",
        ),
        ConfigItem(
            label="Accent Override",
            key="ACCENT",
            scope="DEFAULT",
            type_="color",
            default="",
            options=COLOR_PRESETS,
            group="Colors",
            extended_help="**Accent Override**\n\nReplaces the matugen accent for border and derived fill. Empty uses matugen.",
            tooltip="Accent Override: one-knob recolor. Sets the border edge and the derived middle fill, pinned against matugen switches.",
        ),
        ConfigItem(
            label="Spinner Background",
            key="WATCH_BG",
            scope="DEFAULT",
            type_="color",
            default="",
            options=COLOR_PRESETS,
            group="Colors",
            extended_help="**Spinner Background**\n\nFill for wait/loading pointers. Empty uses the matugen background.",
        ),
        ConfigItem(
            label="Reset to Defaults",
            key="action_reset_colors",
            scope="DEFAULT",
            type_="action",
            default="python3 ~/user_scripts/cursor/color/dusky_cursor.py --reset-colors",
            group="Actions",
            confirm_message="Reset theme to Dusky, size to 18, clear custom colors, and rebuild?",
            extended_help="**Reset to Defaults**\n\nThe single reset. Restores stock Dusky at size 18 from the matugen accent and applies it.",
        ),
        ConfigItem(
            label="Restore Bibata",
            key="action_restore",
            scope="DEFAULT",
            type_="action",
            default="python3 ~/user_scripts/cursor/color/dusky_cursor.py --restore",
            group="Actions",
            confirm_message="Revert every layer to the stock Bibata theme?",
            extended_help="**Restore Bibata**\n\nPoints all layers back at the stock source theme. The Dusky build stays installed.",
        ),
    ],
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
