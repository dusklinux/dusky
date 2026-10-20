#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: DUSKY SITES WEBSITES SCHEMA & CONTROLLER
===============================================================================
This file serves a dual purpose:
1. It is the visual layout schema consumed by Dusky TUI (`main.py tui_dusky_sites.py`).
2. It is a standalone executable scripting tool launched via desktop entry or terminal.
===============================================================================
"""

import sys
import os
import shlex
from pathlib import Path

# --- RESOLVE PATH BEFORE IMPORTS FOR STANDALONE CLI ---
_dusky_root = Path.home() / "user_scripts" / "dusky_tui"
if str(_dusky_root) not in sys.path:
    sys.path.insert(0, str(_dusky_root))

from python.frontend.core_types import ConfigItem
from python.engines.dusky_sites import DuskySitesEngine

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "dusky_sites"
TARGET_FILE = "~/.config/dusky/settings/dusky_sites/config.json"
APP_TITLE = "Dusky Sites"
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"

ENABLE_USER_PRESETS = False
USER_PRESETS_TAB = None

TABS = ["Websites", "Engine Settings", "Setup"]

_SETUP_SCRIPT = Path(__file__).resolve().parent / "dusky_sites_setup.py"
_SETUP_CMD = f"{shlex.quote(sys.executable)} {shlex.quote(str(_SETUP_SCRIPT))}"

# =============================================================================
# DYNAMIC SCHEMA GENERATION
# =============================================================================
engine = DuskySitesEngine(TARGET_FILE)
engine.load_state()

tab0_items = [
    ConfigItem(
        label="Enable Webpage Color Injection",
        key="webThemeEnabled",
        scope="DEFAULT",
        type_="bool",
        default=False,
        value=engine.cache.get("webThemeEnabled", False),
        group="Global Settings",
        extended_help="**Global Webpage Color Injection**\n\nDisabled by default (OPT-IN ONLY).\n\nDusky Sites themes the browser UI and Firefox internal pages. Enable this toggle to inject Matugen CSS into HTTP(S) webpages."
    ),
    ConfigItem(
        label="Theme Templateless Sites",
        key="forceUnthemedWebsites",
        scope="DEFAULT",
        type_="bool",
        default=False,
        value=engine.cache.get("forceUnthemedWebsites", False),
        group="Fallback Theming",
        extended_help="**Fallback Templateless Theming**\n\nDisabled by default (OPT-IN ONLY).\n\nWhen 'Enable Webpage Color Injection' is ON, this toggle enables Dark Reader fallback rules and dynamic luminance analysis for websites that lack a custom CSS template in `~/.config/dusky_sites/`."
    ),
]

site_files = engine.get_site_files()
for css_file in site_files:
    domain = css_file.stem.lower()
    key_name = f"site_{domain}"
    is_enabled = engine.cache.get(key_name, True)

    tab0_items.append(
        ConfigItem(
            label=domain,
            key=key_name,
            scope="DEFAULT",
            type_="bool",
            default=True,
            value=is_enabled,
            group="Website Templates",
            extended_help=f"**{domain} Website Theme**\n\nToggle dynamic Matugen color injection for `{domain}` (`{css_file.name}`). When disabled, this website remains unthemed."
        )
    )

tab1_items = [
    ConfigItem(
        label="userChrome.css (UI & Menus)",
        key="userChromeEnabled",
        scope="DEFAULT",
        type_="bool",
        default=True,
        value=engine.cache.get("userChromeEnabled", True),
        group="Stylesheet Injection",
        extended_help="**userChrome.css (Browser UI & Right-Click Menus)**\n\nControls whether native context menu, sidebar, and popups CSS rules (`dusky_menu.css`) are injected into profile `userChrome.css`. Apply with Install / Update, then restart Firefox."
    ),
    ConfigItem(
        label="userContent.css (Internal Pages)",
        key="userContentEnabled",
        scope="DEFAULT",
        type_="bool",
        default=True,
        value=engine.cache.get("userContentEnabled", True),
        group="Stylesheet Injection",
        extended_help="**userContent.css (Internal Pages)**\n\nControls whether about:pages and internal document stylesheets are injected into profile `userContent.css`. Apply with Install / Update, then restart Firefox."
    ),
]

SCHEMA = {
    0: tab0_items,
    1: tab1_items,
    2: [
        ConfigItem(
            label="Install / Update",
            key="action_install",
            scope="DEFAULT",
            type_="action",
            default=f"{_SETUP_CMD} --yes",
            options=["trigger"],
            group="Install / Update",
            confirm_message="Install or update Dusky Sites across all detected native Firefox profiles? This runs the full setup script.",
            extended_help="**Install / Update**\n\nRuns `dusky_sites_setup.py` with `--yes`, dynamically discovering native Firefox 158+ profiles in the traditional and XDG registries (including external profile paths) and installing the native host, messaging manifests, per-profile XPI, and userChrome.css styling."
        ),
        ConfigItem(
            label="Uninstall System",
            key="action_uninstall",
            scope="DEFAULT",
            type_="action",
            default=f"{_SETUP_CMD} --uninstall --yes",
            options=["trigger"],
            group="Uninstall / Remove",
            confirm_message="Uninstall Dusky Sites and reset user configuration? Site templates in ~/.config/dusky_sites/ and dev source files will be preserved.",
            extended_help="**Uninstall System**\n\nRuns `dusky_sites_setup.py --uninstall --yes`, removing the native host, messaging manifests, per-profile XPI, userChrome.css styling, and user configuration (config.json) from every profile. Custom site templates in `~/.config/dusky_sites/` and dev files remain safe."
        ),
    ],
}

# =============================================================================
# 2. STANDALONE CLI & TUI LAUNCHER
# =============================================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Dusky Sites Manager",
        formatter_class=argparse.RawTextHelpFormatter,
        add_help=False
    )
    
    web_group = parser.add_mutually_exclusive_group()
    web_group.add_argument("--enable-web", action="store_true", help="Enable global webpage color injection")
    web_group.add_argument("--disable-web", action="store_true", help="Disable global webpage color injection")
    parser.add_argument("-h", "--help", action="help", default=argparse.SUPPRESS, help="Show this help message and exit")

    args = parser.parse_args()

    # If executed with no arguments, launch the interactive Dusky TUI!
    if not any(vars(args).values()):
        main_script = Path("~/user_scripts/dusky_tui/python/main/main.py").expanduser().resolve()
        if not main_script.exists():
            main_script = Path("~/user_scripts/dusky_tui/main/main.py").expanduser().resolve()

        if main_script.exists():
            os.execvp(sys.executable, [sys.executable, str(main_script), __file__])
        else:
            print("[-] Error: Could not locate dusky_tui main.py to launch TUI.")
            sys.exit(1)

    changes = []
    if args.enable_web:
        changes.append(("webThemeEnabled", "DEFAULT", "true", "bool"))
    elif args.disable_web:
        changes.append(("webThemeEnabled", "DEFAULT", "false", "bool"))

    if changes:
        success, msg, _ = engine.write_batch(changes)
        if success:
            print(f"[OK] {msg}")
            sys.exit(0)
        else:
            print(f"[-] Failed: {msg}")
            sys.exit(1)
