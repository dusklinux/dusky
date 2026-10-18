#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: HYPRLOCK CONFIGURATION SCHEMA & SCRIPTING CLI
===============================================================================
This file serves a dual purpose:
1. Visual layout schema consumed by the Dusky TUI (`main.py hyprlock.tui_hyprlock`).
2. Standalone executable scripting and CLI tool for Hyprlock theme management.
===============================================================================
"""

import sys
import os
import subprocess
from pathlib import Path

# --- DYNAMIC DUSKY TUI PATH RESOLUTION (ZERO HARDCODED USERNAMES) ---
_DUSKY_TUI_CANDIDATES = [
    Path(__file__).resolve().parent.parent / "dusky_tui",
    Path.home() / "user_scripts" / "dusky_tui",
]
for _candidate in _DUSKY_TUI_CANDIDATES:
    if _candidate.is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from python.frontend.core_types import ConfigItem
from python.engines.hyprlock import HyprlockEngine, discover_themes

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "hyprlock"
TARGET_FILE = "~/.config/hypr/hyprlock.conf"
APP_TITLE = "Dusky Hyprlock"
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"

ENABLE_USER_PRESETS = False
USER_PRESETS_TAB = None

TABS = ["Themes", "Actions"]

# =============================================================================
# 2. DYNAMIC THEME DISCOVERY
# =============================================================================
_THEMES_ROOT = Path("~/.config/hypr/hyprlock_themes").expanduser().resolve()

THEMES_META = discover_themes(_THEMES_ROOT)

TOTAL_THEMES = len(THEMES_META)

# =============================================================================
# 3. TUI SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: THEMES GALLERY
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Active Theme",
            key="hyprlock",
            scope="DEFAULT",
            type_="int",
            default=1,
            min_val=1,
            max_val=TOTAL_THEMES if TOTAL_THEMES > 0 else 1,
            step=1,
            group="Themes",
            extended_help=(
                "**Active Hyprlock Theme**\n\n"
                "Chronological index of the active Hyprlock layout. Adjusting this number "
                "instantly switches the lock screen layout."
            ),
        ),
        ConfigItem(
            label="Available Themes",
            key="active_theme_folder",
            scope="DEFAULT",
            type_="menu",
            default=None,
            is_parent=True,
            expanded=True,
            group="Themes",
            extended_help=(
                "**Hyprlock Themes Gallery**\n\n"
                "Navigate down and press `Enter` on any theme to instantly apply and "
                "activate it. The list functions as an instant radio selection."
            ),
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: ACTIONS & TOOLS
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Next Theme",
            key="toggle_forward",
            scope="DEFAULT",
            type_="bool",
            default=False,
            options=["trigger:Next Theme"],
            group="Navigation",
            extended_help=(
                "**Next Theme**\n\n"
                "Cycles forward to the next theme in chronological order (wraps to start)."
            ),
        ),
        ConfigItem(
            label="Previous Theme",
            key="toggle_backward",
            scope="DEFAULT",
            type_="bool",
            default=False,
            options=["trigger:Previous Theme"],
            group="Navigation",
            extended_help=(
                "**Previous Theme**\n\n"
                "Cycles backward to the previous theme in chronological order (wraps to end)."
            ),
        ),
        ConfigItem(
            label="Test Lock Screen Session",
            key="action_test_lock",
            scope="DEFAULT",
            type_="bool",
            default=False,
            options=["trigger:Lock Screen"],
            group="Testing",
            extended_help=(
                "**Test Lock Screen**\n\n"
                "Launches `hyprlock` immediately to test and preview the active theme. "
                "Type your password to return."
            ),
        ),
        ConfigItem(
            label="Heal Configuration & State",
            key="action_heal_state",
            scope="DEFAULT",
            type_="bool",
            default=False,
            options=["trigger:Heal"],
            group="Maintenance",
            extended_help=(
                "**Heal Hyprlock Configuration**\n\n"
                "If `hyprlock.conf` becomes desynchronized, this action restores the "
                "exact theme recorded in the persistent state file."
            ),
        ),
        ConfigItem(
            label="Open Themes Directory",
            key="action_open_themes_dir",
            scope="DEFAULT",
            type_="action",
            default="xdg-open ~/.config/hypr/hyprlock_themes",
            group="File Management",
            extended_help=(
                "**Open Themes Directory**\n\n"
                "Opens `~/.config/hypr/hyprlock_themes` in your default file manager."
            ),
        ),
        ConfigItem(
            label="Edit Active hyprlock.conf",
            key="action_edit_config",
            scope="DEFAULT",
            type_="action",
            default="${EDITOR:-nano} ~/.config/hypr/hyprlock.conf",
            force_interactive=True,
            group="Editor",
            extended_help=(
                "**Edit hyprlock.conf**\n\n"
                "Opens the active `hyprlock.conf` file in your terminal editor."
            ),
        ),
    ],
}

# --- Inject dynamic theme preset items contiguous to the parent folder ---
dynamic_theme_items = []
for i, meta in enumerate(THEMES_META):
    fld = meta["folder"]
    nm = meta["name"]
    raw_nm = meta["raw_name"]
    desc = meta["description"] or "No description provided."
    author = meta["author"] or "Unknown"

    help_text = (
        f"**{raw_nm}**\n\n"
        f"- **Directory:** `{fld}`\n"
        f"- **Author:** {author}\n"
        f"- **Description:** {desc}\n\n"
        "Press `Enter` to instantly apply this Hyprlock theme."
    )

    dynamic_theme_items.append(
        ConfigItem(
            label=nm,
            key=f"__hyprlock_theme_{fld}",
            scope="DEFAULT",
            type_="preset",
            default=None,
            parent_ref="active_theme_folder",
            group="Themes",
            preset_payload={
                "hyprlock": i + 1
            },
            extended_help=help_text,
        )
    )

SCHEMA[0].extend(dynamic_theme_items)


# =============================================================================
# 4. STANDALONE CLI MODE (Full drop-in replacement for scripting)
# =============================================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Dusky Hyprlock Theme Manager - Scripting CLI & TUI Launcher",
        formatter_class=argparse.RawTextHelpFormatter,
        add_help=False,
    )

    parser.add_argument(
        "-n", "--next", "--toggle",
        dest="toggle",
        action="store_true",
        help="Switch to the next Hyprlock theme chronologically"
    )
    parser.add_argument(
        "-p", "--prev", "--previous", "--back_toggle",
        dest="back_toggle",
        action="store_true",
        help="Switch to the previous Hyprlock theme chronologically"
    )
    parser.add_argument(
        "-s", "--apply", "--set",
        dest="apply",
        type=str,
        metavar="THEME",
        help="Apply a specific Hyprlock theme by folder name, display name, or 1-based index"
    )
    parser.add_argument(
        "--first",
        action="store_true",
        help="Apply the first available Hyprlock theme"
    )
    parser.add_argument(
        "--heal",
        action="store_true",
        help="Restore / heal hyprlock.conf from the persistent state file"
    )
    parser.add_argument(
        "-l", "--list",
        action="store_true",
        help="List all available Hyprlock themes with active indicator"
    )
    parser.add_argument(
        "-c", "--current", "--get",
        dest="current",
        action="store_true",
        help="Print the currently active Hyprlock theme"
    )
    parser.add_argument(
        "-t", "--test", "--lock",
        dest="test_lock",
        action="store_true",
        help="Test / launch hyprlock immediately"
    )
    parser.add_argument(
        "-h", "--help",
        action="help",
        default=argparse.SUPPRESS,
        help="Show this help message and exit"
    )

    args = parser.parse_args()

    # Behavior 1: If executed with no arguments, launch the Dusky TUI
    if not any(vars(args).values()):
        main_script = None
        for candidate in _DUSKY_TUI_CANDIDATES:
            probe = candidate / "python" / "main" / "main.py"
            if probe.is_file():
                main_script = probe
                break

        if main_script and main_script.is_file():
            os.execvp(sys.executable, [sys.executable, str(main_script), str(Path(__file__).resolve())])
        else:
            print("[-] Error: Could not locate dusky_tui main.py to launch TUI.", file=sys.stderr)
            sys.exit(1)

    # Behavior 2: CLI scripting mutator
    engine = HyprlockEngine()
    state = engine.load_state()

    if args.list:
        themes = engine.get_themes()
        if not themes:
            print("[i] No Hyprlock themes found in ~/.config/hypr/hyprlock_themes/")
            sys.exit(0)

        active_folder = state.get("active_theme_folder", "")
        print(f"Available Hyprlock Themes ({len(themes)}):")
        for i, t in enumerate(themes, 1):
            is_active = (t["folder"] == active_folder)
            marker = "*" if is_active else " "
            status = " [ACTIVE]" if is_active else ""
            desc = f" - {t['description']}" if t["description"] else ""
            print(f" {marker} [{i:2d}] {t['name']} ({t['folder']}){status}{desc}")
        sys.exit(0)

    if args.current:
        active_name = state.get("active_theme_name", "Unknown")
        active_folder = state.get("active_theme_folder", "Unknown")
        active_num = state.get("active_theme_number", 1)
        print(f"{active_name} ({active_folder}) [#{active_num}]")
        sys.exit(0)

    if args.test_lock:
        lock_bin = str(Path(__file__).resolve().with_name("lock.sh"))
        print(f"[*] Launching {lock_bin}...")
        try:
            sys.exit(subprocess.run([lock_bin]).returncode)
        except Exception as e:
            print(f"[-] Error launching hyprlock: {e}", file=sys.stderr)
            sys.exit(1)

    changes = []
    if args.toggle:
        changes.append(("toggle_forward", "DEFAULT", "true", "bool"))
    elif args.back_toggle:
        changes.append(("toggle_backward", "DEFAULT", "true", "bool"))
    elif args.heal:
        changes.append(("action_heal_state", "DEFAULT", "true", "bool"))
    elif args.first:
        if TOTAL_THEMES > 0:
            changes.append(("active_theme_number", "DEFAULT", "1", "int"))
        else:
            print("[-] Error: No Hyprlock themes found.", file=sys.stderr)
            sys.exit(1)
    elif args.apply:
        changes.append(("theme", "DEFAULT", args.apply, "string"))

    if changes:
        success, msg, _ = engine.write_batch(changes)
        if success:
            print(f"[OK] {msg}")
            sys.exit(0)
        else:
            print(f"[-] Failed: {msg}", file=sys.stderr)
            sys.exit(1)
