#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: DUSKY KEYS OSD CONFIGURATION SCHEMA
===============================================================================
Target: ~/.config/dusky/settings/dusky_keys/config.toml
Engine: TOML Engine
===============================================================================
"""

import sys
import subprocess
from pathlib import Path

_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "toml"
TARGET_FILE = "~/.config/dusky/settings/dusky_keys/config.toml"
APP_TITLE = "Dusky Keys"

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Presets"

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = [
    "Display",
    "Chording & Mouse",
    "Symbols",
    "Presets"
]

# =============================================================================
# 4. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: DISPLAY (OSD Popup & Buffer)
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Buffer Size (Items)",
            key="buffer_size",
            scope="display",
            type_="int",
            default=10,
            min_val=1,
            max_val=20,
            step=1,
            group="Buffer & Overflow",
            extended_help="**OSD Buffer Capacity**\n\nMaximum number of keystrokes/chords remembered in the OSD display buffer before shifting out older keys. Setting to 10 allows key items to span across the entire notification pill."
        ),
        ConfigItem(
            label="Display Timeout (Seconds)",
            key="display_timeout",
            scope="display",
            type_="float",
            default=2.5,
            min_val=0.5,
            max_val=10.0,
            step=0.5,
            group="Buffer & Overflow",
            extended_help="**OSD Popup Timeout**\n\nDuration in seconds to keep the OSD notification on screen after the last keypress before automatically fading out."
        ),
        ConfigItem(
            label="Use Compact Symbols",
            key="compact_symbols",
            scope="display",
            type_="bool",
            default=True,
            group="Formatting",
            extended_help="**Compact Symbols Mode**\n\nDisplays physical key labels using the US key map. Uses compact Unicode glyphs for modifier keys (❖, ⌃, ⌥, ⇧) and special keys (⇥, ⏎, ⌫, Esc). Highly recommended to fit key combos into compact OSD bars."
        ),
        ConfigItem(
            label="Use Pango HTML Markup",
            key="use_pango_markup",
            scope="display",
            type_="bool",
            default=False,
            group="Formatting",
            extended_help="**Pango Markup**\n\nAdds escaped bold text to the notification body. Mako must have `markup=true` and a format containing `%b`; the default Dusky Keys rule uses `%s` and shows plain text."
        ),
        ConfigItem(
            label="Item Delimiter Separator",
            key="separator",
            scope="display",
            type_="string",
            default=" ",
            options=[" ", " • ", " | ", " - ", "   "],
            group="Formatting",
            extended_help="**Keystroke Separator**\n\nThe delimiter character inserted between sequential items in the OSD display stream."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: CHORDING & MOUSE
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Enable Smart Chording",
            key="enable_chording",
            scope="chording",
            type_="bool",
            default=True,
            group="Key Combos",
            extended_help="**Smart Modifier Chording**\n\nCombines held modifier keys and target character into unified compact chord blocks (e.g. `❖S` or `⌃C`) instead of outputting separate modifier boxes."
        ),
        ConfigItem(
            label="Suppress Pure Modifiers on Hold",
            key="suppress_pure_modifiers",
            scope="chording",
            type_="bool",
            default=True,
            group="Key Combos",
            extended_help="**Suppress Pure Modifiers**\n\nSuppresses modifier keys while held down, outputting the modifier symbol ONLY if tapped and released alone without pressing another key."
        ),
        ConfigItem(
            label="Enable Mouse Click Capture",
            key="enable_mouse",
            scope="mouse",
            type_="bool",
            default=False,
            group="Mouse Capture",
            extended_help="**Mouse Click Monitoring**\n\nCaptures mouse button events (LMB, RMB, MMB, Back, Forward) and renders them in the OSD (including modifier combos like `❖LMB`)."
        ),
        ConfigItem(
            label="Left Click Symbol",
            key="left_click",
            scope="mouse",
            type_="string",
            default="LMB",
            group="Mouse Symbols",
            extended_help="**Left Click Label**\n\nText symbol rendered when Left Mouse Button is clicked."
        ),
        ConfigItem(
            label="Right Click Symbol",
            key="right_click",
            scope="mouse",
            type_="string",
            default="RMB",
            group="Mouse Symbols",
            extended_help="**Right Click Label**\n\nText symbol rendered when Right Mouse Button is clicked."
        ),
        ConfigItem(
            label="Middle Click Symbol",
            key="middle_click",
            scope="mouse",
            type_="string",
            default="MMB",
            group="Mouse Symbols",
            extended_help="**Middle Click Label**\n\nText symbol rendered when Middle Mouse Button / Scroll Wheel is clicked."
        ),
        ConfigItem(
            label="Side Button Symbol (Back)",
            key="side_click",
            scope="mouse",
            type_="string",
            default="Back",
            group="Mouse Symbols",
            extended_help="**Side Button Label**\n\nText symbol rendered for side mouse button (Back / Mouse 4)."
        ),
        ConfigItem(
            label="Extra Button Symbol (Forward)",
            key="extra_click",
            scope="mouse",
            type_="string",
            default="Fwd",
            group="Mouse Symbols",
            extended_help="**Extra Button Label**\n\nText symbol rendered for extra mouse button (Forward / Mouse 5)."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: SYMBOLS (Custom Key Glyph Overrides)
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Super / Meta Key",
            key="super",
            scope="symbols",
            type_="string",
            default="❖",
            group="Modifiers",
            extended_help="**Super Key Glyph**\n\nSymbol used for Super / Windows / Command key. Standard options: `❖`, `⌘`, `Sup`."
        ),
        ConfigItem(
            label="Control Key",
            key="ctrl",
            scope="symbols",
            type_="string",
            default="⌃",
            group="Modifiers",
            extended_help="**Control Key Glyph**\n\nSymbol used for Ctrl key. Standard options: `⌃`, `Ctrl`, `^`."
        ),
        ConfigItem(
            label="Alt Key",
            key="alt",
            scope="symbols",
            type_="string",
            default="⌥",
            group="Modifiers",
            extended_help="**Alt Key Glyph**\n\nSymbol used for Alt key. Standard options: `⌥`, `Alt`."
        ),
        ConfigItem(
            label="Shift Key",
            key="shift",
            scope="symbols",
            type_="string",
            default="⇧",
            group="Modifiers",
            extended_help="**Shift Key Glyph**\n\nSymbol used for Shift key. Standard options: `⇧`, `Shift`."
        ),
        ConfigItem(
            label="Tab Key",
            key="tab",
            scope="symbols",
            type_="string",
            default="⇥",
            group="Special Keys",
            extended_help="**Tab Key Glyph**\n\nSymbol used for Tab key. Standard options: `⇥`, `Tab`."
        ),
        ConfigItem(
            label="Enter / Return Key",
            key="enter",
            scope="symbols",
            type_="string",
            default="⏎",
            group="Special Keys",
            extended_help="**Enter Key Glyph**\n\nSymbol used for Enter / Return key. Standard options: `⏎`, `↵`, `Enter`."
        ),
        ConfigItem(
            label="Backspace Key",
            key="backspace",
            scope="symbols",
            type_="string",
            default="⌫",
            group="Special Keys",
            extended_help="**Backspace Key Glyph**\n\nSymbol used for Backspace key. Standard options: `⌫`, `Back`."
        ),
        ConfigItem(
            label="Delete Key",
            key="delete",
            scope="symbols",
            type_="string",
            default="⌦",
            group="Special Keys",
            extended_help="**Delete Key Glyph**\n\nSymbol used for Delete key. Standard options: `⌦`, `Del`."
        ),
        ConfigItem(
            label="Escape Key",
            key="escape",
            scope="symbols",
            type_="string",
            default="⎋",
            group="Special Keys",
            extended_help="**Escape Key Glyph**\n\nSymbol used for Escape key. Standard options: `⎋`, `Esc`."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 3: PRESETS & DAEMON CONTROL
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="Restart Dusky Keys Engine",
            key="action_restart_engine",
            scope="DEFAULT",
            type_="action",
            default='systemd-run --user --collect --quiet -- bash "$HOME/user_scripts/mako_osd/dusky_keys/dusky_keys.sh" --restart',
            force_interactive=False,
            group="Engine Daemon",
            extended_help="**Restart Engine**\n\nRequests a restart in a transient user service, so the visualizer survives closing this TUI. Loads configuration changes and starts the engine if it was disabled."
        ),
        ConfigItem(
            label="Install / Repair Environment",
            key="action_rebuild_env",
            scope="DEFAULT",
            type_="action",
            default='bash "$HOME/user_scripts/mako_osd/dusky_keys/dusky_keys.sh" --setup',
            force_interactive=True,
            group="Engine Daemon",
            extended_help="**Install / Repair Environment**\n\nInstalls missing Arch dependencies and prepares the native Python 3.15 UV environment. Repairs corrupted packages using portable compiler defaults. Stop the engine before running setup; sudo may require authentication."
        ),
        ConfigItem(
            label="Apply Compact Minimal Preset",
            key="preset_compact_minimal",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Presets",
            preset_payload={
                "display.buffer_size": 10,
                "display.compact_symbols": True,
                "display.use_pango_markup": False,
                "chording.enable_chording": True,
                "chording.suppress_pure_modifiers": True,
                "mouse.enable_mouse": True,
                "mouse.left_click": "LMB",
                "mouse.right_click": "RMB",
                "symbols.super": "❖",
                "symbols.ctrl": "⌃"
            },
            extended_help="**Compact Minimal Profile**\n\nApplies sleek Unicode glyphs (`❖`, `⌃`, `⌥`, `⇧`), smart modifier chording, clean `LMB`/`RMB` mouse labels, and edge-to-edge 10-item buffer."
        ),
        ConfigItem(
            label="Apply Standard Text Profile",
            key="preset_standard_text",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Presets",
            preset_payload={
                "display.buffer_size": 6,
                "display.compact_symbols": False,
                "display.use_pango_markup": False,
                "chording.enable_chording": False,
                "mouse.enable_mouse": False,
                "symbols.super": "Super",
                "symbols.ctrl": "Ctrl"
            },
            extended_help="**Standard Text Profile**\n\nReverts to full text words (`Super`, `Ctrl`, `Enter`) with non-chorded sequential keystroke rendering."
        ),
    ]
}

# =============================================================================
# DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    script_path = Path(__file__).resolve()
    main_router = _DUSKY_TUI_ROOT / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
