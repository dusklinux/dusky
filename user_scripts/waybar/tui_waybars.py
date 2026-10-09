#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: WAYBAR CONFIGURATION SCHEMA & SCRIPTING CLI
===============================================================================
This file serves a dual purpose:
1. It is the visual layout schema consumed by the Dusky TUI.
2. It is a standalone scripting CLI using the same Waybar engine.
===============================================================================
"""
import os
import sys
from pathlib import Path
lazy import argparse
lazy from python.frontend.core_types import ConfigItem
lazy from python.engines.waybar_engine import WaybarEngine, discover_themes

# Find the sibling installation from this script, independent of the caller's cwd.
_DUSKY_TUI_ROOT = Path(__file__).resolve().parents[1] / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "waybar"
TARGET_FILE = str(Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config").expanduser() / "waybar")
APP_TITLE = "Dusky Waybars"
DEFAULT_MODE = "auto"
THEME_FILE = str(Path(TARGET_FILE).parent / "matugen/generated/dusky_tui.json")

ENABLE_USER_PRESETS = False
USER_PRESETS_TAB = None

TABS = ["Gallery"]

# =============================================================================
# DYNAMIC THEME DISCOVERY
# =============================================================================
config_root = Path(TARGET_FILE).expanduser().resolve()

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dusky Waybar Manager — choose a theme or open the TUI.",
        allow_abbrev=False,
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("-n", "--next", "--toggle", dest="toggle", action="store_true",
                         help="Apply the next theme in alphabetical order")
    actions.add_argument("-p", "--prev", "--previous", "--back_toggle", dest="back_toggle", action="store_true",
                         help="Apply the previous theme in alphabetical order")
    actions.add_argument("--toggle-pos", action="store_true", help="Toggle the first bar's position (top/bottom, left/right)")
    actions.add_argument("--heal", action="store_true", help="Restore configuration links from the saved theme")
    actions.add_argument("--first", action="store_true", help="Apply the first theme alphabetically")
    actions.add_argument("--apply", "--set", "-s", metavar="THEME", help="Apply a theme by exact name or 1-based number")
    args = parser.parse_args(argv)

    if not any(value is not None and value is not False for value in vars(args).values()):
        main_script = _DUSKY_TUI_ROOT / "python/main/main.py"
        try:
            if not main_script.is_file():
                raise FileNotFoundError(f"TUI entry point not found: {main_script}")
            os.execv(sys.executable, [sys.executable, str(main_script), str(Path(__file__).resolve())])
        except OSError as exc:
            print(f"[-] {exc}", file=sys.stderr)
            return 1

    key, value, kind = "", "true", "bool"
    if args.toggle:
        key = "toggle_forward"
    elif args.back_toggle:
        key = "toggle_backward"
    elif args.toggle_pos:
        key = "action_invert_pos"
    elif args.heal:
        key = "action_heal_state"
    elif args.first:
        key, value, kind = "waybar", "1", "int"
    elif args.apply is not None:
        key, value, kind = "active_theme_name", args.apply, "string"
    try:
        engine = WaybarEngine(TARGET_FILE)
        success, message, _ = engine.write_value(key, "DEFAULT", value, kind)
    except (ImportError, OSError) as exc:
        print(f"[-] {exc}", file=sys.stderr)
        return 1
    print(f"[OK] {message}" if success else f"[-] {message}", file=sys.stdout if success else sys.stderr)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())


THEMES = [directory.name for directory in discover_themes(config_root)]

# =============================================================================
# TUI SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    0: [
        ConfigItem(
            label="Toggle Waybar Position",
            key="action_invert_pos",
            scope="DEFAULT",
            type_="bool",
            default=False,
            options=["trigger"],
            group="Layout",
            extended_help="**Toggle Position**\n\nToggle the first bar between top and bottom, or left and right. The selected theme must declare its position explicitly; additional bars and module settings stay unchanged."
        ),
        ConfigItem(
            label="Restore Configuration Links",
            key="action_heal_state",
            scope="DEFAULT",
            type_="bool",
            default=False,
            options=["trigger"],
            group="Layout",
            extended_help="**Heal Broken Configuration**\n\nRestore the saved theme by name, or by its saved index if the folder was renamed. Rebuild the configuration and style links, then restart Waybar."
        ),
        ConfigItem(
            label="Theme Number",
            key="waybar",
            scope="DEFAULT",
            type_="int",
            default=1,
            min_val=1,
            max_val=len(THEMES) if THEMES else 1,
            step=1,
            group="Themes",
            extended_help="**System Theme Tracker**\n\nChoose a theme by its 1-based number in the alphabetically sorted list below."
        ),
        ConfigItem(
            label="Available Themes (Live Preview)",
            key="active_theme_folder",
            scope="DEFAULT",
            type_="menu",
            default=None,
            is_parent=True,
            expanded=True,
            group="Themes",
            extended_help="**Waybar Themes**\n\nArrow down and hit Enter on any theme to instantly apply and preview it. The list acts as a strict radio-button selection."
        )
    ]
}

# --- Inject dynamic menu items contiguous to the parent folder ---
dynamic_theme_items = []
for i, name in enumerate(THEMES):
    dynamic_theme_items.append(
        ConfigItem(
            label=name,
            key=f"__waybar_theme_{name}",
            scope="DEFAULT",
            type_="preset",
            default=None,
            parent_ref="active_theme_folder",
            group="Themes",
            preset_payload={
                "waybar": i + 1
            },
            extended_help=f"**Apply {name}**\n\nHit Enter to instantly apply this layout. The configuration links are updated and Waybar is restarted."
        )
    )

SCHEMA[0].extend(dynamic_theme_items)
