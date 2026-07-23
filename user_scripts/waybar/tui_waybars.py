#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: WAYBAR CONFIGURATION SCHEMA & SCRIPTING CLI
===============================================================================
This file serves a dual purpose:
1. It is the visual layout schema consumed by the Dusky TUI (`main.py waybar_schema`).
2. It is a standalone executable scripting tool duplicating `dusky_waybars.sh`.
===============================================================================
"""
import sys
import os
import shlex
from pathlib import Path

# --- RESOLVE PATH BEFORE IMPORTS FOR STANDALONE CLI ---
_cwd = Path.cwd()
if (_cwd / "python" / "frontend").exists() and str(_cwd) not in sys.path:
    sys.path.insert(0, str(_cwd))
else:
    _fallback = Path("~/user_scripts/dusky_tui").expanduser().resolve()
    if str(_fallback) not in sys.path:
        sys.path.insert(0, str(_fallback))

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

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "waybar"                     
TARGET_FILE = "~/.config/waybar" 
APP_TITLE = "Dusky Waybars"               
DEFAULT_MODE = "auto"                      
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"

ENABLE_USER_PRESETS = False
USER_PRESETS_TAB = None

TABS = ["Gallary"]

# =============================================================================
# DYNAMIC THEME DISCOVERY
# =============================================================================
config_root = Path(TARGET_FILE).expanduser().resolve()
theme_paths = sorted(config_root.glob("*/config.jsonc"))
THEMES = [t.parent.name for t in theme_paths]

_CLI_PATH = shlex.quote(str(Path(__file__).resolve()))
_EXECUTABLE = sys.executable

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
            extended_help="**Toggle Position**\n\nInstantly inverts the current screen position (Top becomes Bottom, Left becomes Right). Equivalent to pressing Spacebar in the old bash script."
        ),
        ConfigItem(
            label="Heal Broken Symlinks",
            key="action_heal_state",
            scope="DEFAULT",
            type_="bool",
            default=False,
            options=["trigger"], 
            group="Layout",
            extended_help="**Heal Broken Configuration**\n\nIf your Waybar symlinks break, this action rebuilds the exact symlink paths needed and restarts Waybar automatically based on your chronologically saved index."
        ),
        ConfigItem(
            label="Active Theme Target",
            key="waybar",
            scope="DEFAULT",
            type_="int",
            default=1,
            min_val=1,
            max_val=len(THEMES) if THEMES else 1,
            step=1,
            group="Themes",
            extended_help="**System Theme Tracker**\n\nThis strictly tracks the currently applied Waybar theme chronologically. You can adjust the number here to directly switch themes."
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
            extended_help=f"**Apply {name}**\n\nHit Enter to instantly apply this layout. It will automatically symlink and restart Waybar."
        )
    )

SCHEMA[0].extend(dynamic_theme_items)

# =============================================================================
# 3. STANDALONE CLI MODE (Replaces dusky_waybars.sh)
# =============================================================================
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Dusky Waybar Manager - Scripting CLI Tool",
        formatter_class=argparse.RawTextHelpFormatter,
        add_help=False
    )
    
    parser.add_argument("-n", "--next", "--toggle", dest="toggle", action="store_true", help="Switch to the next Waybar theme chronologically")
    parser.add_argument("-p", "--prev", "--previous", "--back_toggle", dest="back_toggle", action="store_true", help="Switch to the previous Waybar theme chronologically")
    parser.add_argument("--toggle-pos", action="store_true", help="Invert current Waybar position (Top↔Bottom, Left↔Right)")
    parser.add_argument("--heal", action="store_true", help="Force state restore / heal broken symlinks")
    parser.add_argument("--first", action="store_true", help="Apply the first Waybar theme alphabetically")
    parser.add_argument("--apply", "--set", "-s", dest="apply", type=str, metavar="THEME", help="Apply a specific Waybar theme by name or chronological number")
    parser.add_argument("-h", "--help", action="help", default=argparse.SUPPRESS, help="Show this help message and exit")
    
    args = parser.parse_args()
    
    # Behavior 1: If executed with no arguments, act like the bash script and launch the TUI
    if not any(vars(args).values()):
        main_script = Path("~/user_scripts/dusky_tui/python/main/main.py").expanduser().resolve()
        if not main_script.exists():
            main_script = Path("~/user_scripts/dusky_tui/main/main.py").expanduser().resolve()

        if main_script.exists():
            os.execvp(sys.executable, [sys.executable, str(main_script), __file__])
        else:
            print("[-] Error: Could not locate dusky_tui main.py to launch TUI.")
            sys.exit(1)
            
    # Behavior 2: If executed with flags, act as a headless mutator script
    try:
        from python.engines.waybar_engine import WaybarEngine
    except ImportError:
        print("[-] Error: Could not import WaybarEngine. Ensure dusky_tui is installed correctly.")
        sys.exit(1)
        
    engine = WaybarEngine(TARGET_FILE)
    changes = []
    
    if args.toggle:
        changes.append(("toggle_forward", "DEFAULT", "true", "bool"))
    elif args.back_toggle:
        changes.append(("toggle_backward", "DEFAULT", "true", "bool"))
    elif args.toggle_pos:
        changes.append(("action_invert_pos", "DEFAULT", "true", "bool"))
    elif args.heal:
        changes.append(("action_heal_state", "DEFAULT", "true", "bool"))
    elif args.first:
        if THEMES:
            changes.append(("active_theme_name", "DEFAULT", THEMES[0], "string"))
        else:
            print("[-] Error: No Waybar themes found.")
            sys.exit(1)
    elif args.apply:
        changes.append(("active_theme_name", "DEFAULT", args.apply, "string"))
        
    if changes:
        success, msg, _ = engine.write_batch(changes)
        if success:
            print(f"[OK] {msg}")
            sys.exit(0)
        else:
            print(f"[-] Failed: {msg}")
            sys.exit(1)
