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
from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "waybar"                     
TARGET_FILE = "~/.config/waybar/config.jsonc" 
APP_TITLE = "Waybar Master Control"               
DEFAULT_MODE = "auto"                      
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"

ENABLE_USER_PRESETS = False
USER_PRESETS_TAB = None

TABS = ["Theme Engine"]

# =============================================================================
# DYNAMIC THEME DISCOVERY
# =============================================================================
# CRITICAL FIX: Call .parent BEFORE .absolute(). 
# Calling .resolve() first follows the symlink and traps us inside the active theme's folder!
config_root = Path(TARGET_FILE).expanduser().parent.absolute()
theme_paths = sorted(config_root.glob("*/config.jsonc"))
THEMES = [t.parent.name for t in theme_paths]

# =============================================================================
# TUI SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    0: [
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
for name in THEMES:
    dynamic_theme_items.append(
        ConfigItem(
            label=name,
            key=f"__waybar_theme_{name}",
            scope="DEFAULT",
            type_="bool",
            default=False,
            parent_ref="active_theme_folder",
            group="Themes",
            extended_help=f"**Activate {name}**\n\nHit Enter to instantly apply this layout. It will automatically symlink and restart Waybar."
        )
    )

SCHEMA[0] = [SCHEMA[0][0]] + dynamic_theme_items

# --- Inject Layout & Healing Actions ---
SCHEMA[0].extend([
    ConfigItem(
        label="Invert Waybar Screen Position",
        key="action_invert_pos",
        scope="DEFAULT",
        type_="action",
        # Elegantly routes the UI action back through this exact script's CLI!
        default=f"python {shlex.quote(__file__)} --toggle-pos",
        group="Layout",
        extended_help="**Toggle Position**\n\nInstantly inverts the current screen position (Top becomes Bottom, Left becomes Right). Equivalent to pressing Spacebar in the old bash script."
    ),
    ConfigItem(
        label="Force State Restore (Heal Symlinks)",
        key="action_heal_state",
        scope="DEFAULT",
        type_="action",
        default=f"python {shlex.quote(__file__)} --heal",
        group="Layout",
        extended_help="**Heal Broken Configuration**\n\nIf your Waybar symlinks break, this action rebuilds the exact symlink paths needed and restarts Waybar automatically."
    )
])


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
    
    parser.add_argument("--toggle", action="store_true", help="Switch to the next Waybar theme chronologically")
    parser.add_argument("--back_toggle", action="store_true", help="Switch to the previous Waybar theme chronologically")
    parser.add_argument("--toggle-pos", action="store_true", help="Invert current Waybar position (Top↔Bottom, Left↔Right)")
    parser.add_argument("--heal", action="store_true", help="Force state restore / heal broken symlinks")
    parser.add_argument("-h", "--help", action="help", default=argparse.SUPPRESS, help="Show this help message and exit")
    
    args = parser.parse_args()
    
    # Behavior 1: If executed with no arguments, act like the bash script and launch the TUI
    if not any(vars(args).values()):
        main_script = Path("~/user_scripts/dusky_tui/main/main.py").expanduser().resolve()
        if main_script.exists():
            os.execvp(sys.executable, [sys.executable, str(main_script), __file__])
        else:
            print("[-] Error: Could not locate dusky_tui main.py to launch TUI.")
            sys.exit(1)
            
    # Behavior 2: If executed with flags, act as a headless mutator script
    dusky_root = Path("~/user_scripts/dusky_tui").expanduser().resolve()
    if str(dusky_root) not in sys.path:
        sys.path.insert(0, str(dusky_root))
        
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
        changes.append(("toggle_position", "DEFAULT", "true", "bool"))
    elif args.heal:
        changes.append(("restore_state", "DEFAULT", "true", "bool"))
        
    if changes:
        success, msg, _ = engine.write_batch(changes)
        if success:
            print(f"[OK] {msg}")
            sys.exit(0)
        else:
            print(f"[-] Failed: {msg}")
            sys.exit(1)
