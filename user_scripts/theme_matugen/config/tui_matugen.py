#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: MATUGEN THEME CONFIGURATOR SCHEMA
===============================================================================
Target: Arch Linux / Hyprland / Matugen dynamic TOML template manager.
Python 3.14.7 implementation replacing legacy bash script dusky_matugen_config_tui.sh.
"""

import sys
import shutil
import subprocess
from pathlib import Path

# Inject dusky_tui root into sys.path
_DUSKY_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_ROOT))

from python.frontend.core_types import ConfigItem
from python.engines.matugen import MatugenEngine

# =============================================================================
# 1. CORE APPLICATION ROUTING (REQUIRED)
# =============================================================================
ENGINE_TYPE = "matugen"
TARGET_FILE = "~/.config/matugen/config.toml"
APP_TITLE = "Dusky Matugen TUI"
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
TABS = ["GTK & Qt", "System", "Apps", "Media & Misc", "Discovered", "Presets"]

# Binary checking map for --smart scanning
CHECK_CMDS: dict[str, str] = {
    "waybar": "waybar",
    "wlogout": "wlogout",
    "rofi": "rofi",
    "mako": "mako",
    "dusky_cursor": "hyprctl",
    "kitty": "kitty",
    "foot": "foot",
    "opencode": "opencode",
    "vscode": "vscodium",
    "alacritty": "alacritty",
    "neovim": "nvim",
    "zed": "zeditor",
    "yazi": "yazi",
    "zathura": "zathura",
    "tmux": "tmux",
    "zellij": "zellij",
    "fastfetch": "fastfetch",
    "khal": "khal",
    "obsidian": "obsidian",
    "obs": "obs",
    "vesktop": "vesktop",
    "beeper": "beeper",
    "spicetify": "spicetify",
    "pywalfox": "pywalfox",
    "dolphin": "dolphin",
    "papirus-folders": "papirus-folders",
    "kate_syntax": "kate",
    "konsole": "konsole",
    "konsole_profile": "konsole",
    "kvantum_kvconfig": "kvantummanager",
    "kvantum_svg": "kvantummanager",
    "gimp": "gimp",
}

# Track all explicitly registered keys for auto-discovery
REGISTERED_KEYS: set[str] = set()

# =============================================================================
# 4. SCHEMA DEFINITION
# =============================================================================
SCHEMA: dict[int, list[ConfigItem]] = {
    # -------------------------------------------------------------------------
    # TAB 0: GTK & Qt
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="GTK 3",
            key="gtk3",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="GTK Theming",
            extended_help="**GTK 3 Color Generation**\n\nGenerates `gtk-3.css` and links it to `~/.config/gtk-3.0/gtk.css` for adw-gtk3."
        ),
        ConfigItem(
            label="GTK 4",
            key="gtk4",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="GTK Theming",
            extended_help="**GTK 4 / Libadwaita Generation**\n\nGenerates `gtk-4.css` and links libadwaita styling to `~/.config/gtk-4.0/`."
        ),
        ConfigItem(
            label="GtkSourceView (Mousepad)",
            key="gtksourceview",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="GTK Theming",
            extended_help="**GtkSourceView / Mousepad Syntax & Gutter Scheme**\n\nGenerates `matugen.xml` style scheme for Mousepad, Gedit, and GtkSourceView editors."
        ),
        ConfigItem(
            label="Icon Theme",
            key="icon_theme",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Icons",
            extended_help="**Icon Theme Setting**\n\nSets GTK interface icon-theme property."
        ),
        ConfigItem(
            label="Qt5 CT",
            key="qt5ct",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Qt Theming",
            extended_help="**Qt5 Color Palette**\n\nGenerates Matugen color scheme for Qt5 Configuration Tool."
        ),
        ConfigItem(
            label="Qt6 CT",
            key="qt6ct",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Qt Theming",
            extended_help="**Qt6 Color Palette**\n\nGenerates Matugen color scheme for Qt6 Configuration Tool."
        ),
        ConfigItem(
            label="KDE Frameworks (kdeglobals)",
            key="kdeglobals",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="KDE Theming",
            extended_help="**KDE Frameworks 6 / Dolphin Theming**\n\nGenerates `kdeglobals` and `~/.local/share/color-schemes/Matugen.colors` for Dolphin, Kate, Gwenview, and KF6 apps."
        ),
        ConfigItem(
            label="Kate Syntax Highlighting",
            key="kate_syntax",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="KDE Theming",
            extended_help="**Kate / KWrite Syntax Theme**\n\nGenerates Matugen color scheme for Kate syntax highlighting (`kate_syntax`)."
        ),
        ConfigItem(
            label="Kvantum Config",
            key="kvantum_kvconfig",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Kvantum",
            extended_help="**Kvantum Matugen Theme Config**\n\nGenerates `kvconfig` for Kvantum Qt SVG engine."
        ),
        ConfigItem(
            label="Kvantum SVG",
            key="kvantum_svg",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Kvantum",
            extended_help="**Kvantum Matugen Theme SVG Assets**\n\nGenerates dynamic SVG assets for Kvantum."
        ),
        ConfigItem(
            label="GIMP Theme",
            key="gimp",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="GIMP",
            extended_help="**GIMP 3.x Theme**\n\nGenerates Matugen colors for GIMP (separate from GTK). Creates `~/.config/GIMP/3.0|3.2/themes/Matugen/gimp.css` and personal `gimp.css` override. Only enabled via --smart if `gimp` is installed."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: SYSTEM
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Hyprland",
            key="hyprland",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Compositor",
            extended_help="**Hyprland Color Scheme**\n\nGenerates `hyprland-colors.lua` and reloads Hyprland borders/colors."
        ),
        ConfigItem(
            label="Hyprlock",
            key="hyprlock",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Screen Lock",
            extended_help="**Hyprlock Theming**\n\nGenerates color definitions for the Hyprlock lock screen."
        ),
        ConfigItem(
            label="Dusky Cursor",
            key="dusky_cursor",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Compositor",
            extended_help="**Dusky Cursor (Accent-Themed Bibata)**\n\nRecolors Bibata-Modern-Classic bitmaps with the Matugen accent (fill) + mode-aware outline into the `Dusky` XCursor theme and applies it live (`hyprctl setcursor`, gsettings, D-Bus env, Hyprland Lua env, GTK settings). Rebuilds automatically on every theme switch; disable to keep your current cursor untouched."
        ),
        ConfigItem(
            label="Waybar",
            key="waybar",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Status Bar",
            extended_help="**Waybar Styling**\n\nGenerates `waybar-colors.css` and sends SIGUSR2 to refresh active Waybar instances."
        ),
        ConfigItem(
            label="Wlogout",
            key="wlogout",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Power Menu",
            extended_help="**Wlogout Theme**\n\nGenerates color variables for the Wlogout session menu."
        ),
        ConfigItem(
            label="Rofi",
            key="rofi",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Launcher",
            extended_help="**Rofi Application Launcher**\n\nGenerates Rasi stylesheet `rofi-colors.rasi`."
        ),
        ConfigItem(
            label="Mako Notifications",
            key="mako",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Notifications",
            extended_help="**Mako Notification Daemon**\n\nGenerates `mako-colors.ini` and reloads Mako."
        ),
        ConfigItem(
            label="Theme Notify",
            key="theme_notify",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Notifications",
            extended_help="**Theme Change Notification**\n\nSends desktop notification upon theme update completion."
        ),
        ConfigItem(
            label="Dusky Control Center",
            key="dusky_control_center",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Dusky Services",
            extended_help="**Dusky Control Center Service**\n\nTriggers `dusky.service` systemd restart on theme changes."
        ),
        ConfigItem(
            label="Dusky Tray",
            key="dusky_tray",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Applications",
            extended_help="**Dusky Tray Palette**\n\nGenerates the tray palette. An open tray reloads theme changes automatically."
        ),
        ConfigItem(
            label="Dusky TUI Theme",
            key="dusky_tui",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Dusky Services",
            extended_help="**Dusky TUI Color Scheme**\n\nGenerates `dusky_tui.json` for all Dusky TUI interactive apps."
        ),
        ConfigItem(
            label="Dusky Visualizer",
            key="dusky_visualizer_colors",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Dusky Services",
            extended_help="**Dusky Visualizer Colors**\n\nGenerates palette variables for desktop audio visualizer."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: APPS
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Kitty",
            key="kitty",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Terminals",
            extended_help="**Kitty Terminal**\n\nGenerates `kitty-colors.conf` and reloads Kitty instances live via SIGUSR1."
        ),
        ConfigItem(
            label="Foot Terminal",
            key="foot",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Terminals",
            extended_help="**Foot Terminal**\n\nGenerates `foot-colors.ini` for Foot Wayland terminal emulator."
        ),
        ConfigItem(
            label="Konsole",
            key="konsole",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Terminals",
            extended_help="**Konsole Terminal**\n\nGenerates Matugen palette for KDE Konsole."
        ),
        ConfigItem(
            label="Konsole Profile",
            key="konsole_profile",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Terminals",
            extended_help="**Konsole Profile Colors**\n\nGenerates Konsole profile color scheme."
        ),
        ConfigItem(
            label="Alacritty",
            key="alacritty",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Terminals",
            extended_help="**Alacritty Terminal**\n\nGenerates `alacritty-colors.toml`."
        ),
        ConfigItem(
            label="OpenCode",
            key="opencode",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Editors",
            extended_help="**OpenCode / VSCode Variant**\n\nGenerates theme JSON for OpenCode."
        ),
        ConfigItem(
            label="VS Code",
            key="vscode",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Editors",
            extended_help="**Visual Studio Code / VSCodium**\n\nGenerates theme settings for VSCodium."
        ),
        ConfigItem(
            label="NeoVim",
            key="neovim",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Editors",
            extended_help="**Neovim Text Editor**\n\nGenerates `neovim-colors.lua` and signals active Neovim processes."
        ),
        ConfigItem(
            label="Zed Editor",
            key="zed",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Editors",
            extended_help="**Zed Code Editor**\n\nGenerates `zed-theme.json`."
        ),
        ConfigItem(
            label="Yazi",
            key="yazi",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="File Managers",
            extended_help="**Yazi File Manager**\n\nGenerates Matugen palette for Yazi terminal file manager."
        ),
        ConfigItem(
            label="Zathura",
            key="zathura",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Document Viewers",
            extended_help="**Zathura PDF Viewer**\n\nGenerates `zathura-colors` for zathurarc."
        ),
        ConfigItem(
            label="Starship",
            key="starship",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Shell Prompt",
            extended_help="**Starship Shell Prompt**\n\nGenerates `starship-colors.toml`."
        ),
        ConfigItem(
            label="Tmux",
            key="tmux",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Terminal Multiplexers",
            extended_help="**Tmux Terminal Multiplexer**\n\nGenerates `tmux-colors.conf`."
        ),
        ConfigItem(
            label="Zellij",
            key="zellij",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Terminal Multiplexers",
            extended_help="**Zellij Terminal Multiplexer**\n\nGenerates `zellij-colors.kdl` for Zellij sessions."
        ),
        ConfigItem(
            label="Steam",
            key="steam",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Gaming",
            extended_help="**AdwSteamGtk / Steam**\n\nGenerates custom CSS for AdwSteamGtk."
        ),
        ConfigItem(
            label="Obsidian",
            key="obsidian",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Productivity",
            extended_help="**Obsidian Knowledge Base**\n\nGenerates custom CSS snippet for Obsidian themes."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 3: MEDIA & MISC
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="OBS Studio",
            key="obs",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Media Production",
            extended_help="**OBS Studio Broadcaster**\n\nGenerates `obs.obt` theme file."
        ),
        ConfigItem(
            label="Vesktop",
            key="vesktop",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Communication",
            extended_help="**Vesktop Discord Client**\n\nGenerates Midnight Discord stylesheet `midnight-discord.css`."
        ),
        ConfigItem(
            label="Beeper",
            key="beeper",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Communication",
            extended_help="**Beeper Chat**\n\nGenerates custom CSS for Beeper."
        ),
        ConfigItem(
            label="Spicetify",
            key="spicetify",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Audio",
            extended_help="**Spicetify Spotify Client**\n\nGenerates `spotify-colors.ini` for Spicetify themes."
        ),
        ConfigItem(
            label="Cava",
            key="cava",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Audio Visualizer",
            extended_help="**Cava Audio Visualizer**\n\nGenerates `cava-colors.ini` and reloads Cava via SIGUSR1."
        ),
        ConfigItem(
            label="Btop",
            key="btop",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="System Monitors",
            extended_help="**Btop System Monitor**\n\nGenerates `btop-colors.theme` for Btop resource monitor."
        ),
        ConfigItem(
            label="Fastfetch",
            key="fastfetch",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="System Information",
            extended_help="**Fastfetch System Info**\n\nGenerates `fastfetch-colors.jsonc`."
        ),
        ConfigItem(
            label="Khal Calendar",
            key="khal",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Productivity",
            extended_help="**Khal CLI Calendar**\n\nGenerates color settings for Khal."
        ),
        ConfigItem(
            label="Pywalfox",
            key="pywalfox",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Web Browsers",
            extended_help="**Pywalfox Firefox Connector**\n\nGenerates Pywalfox colors and updates Firefox themes."
        ),
        ConfigItem(
            label="Dusky Sites (Webpages)",
            key="dusky_sites",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Web Browser Styles",
            extended_help="**Dusky Sites Web Theme**\n\nGenerates CSS overrides for web applications."
        ),
        ConfigItem(
            label="Papirus Folders",
            key="papirus-folders",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Icons",
            extended_help="**Papirus Folders (Lab Best-of-Breed)**\n\nNative matugen Lab-distance mapping of `colors.primary` to closest Papirus palette (adwaita, black, blue, bluegrey, breeze, brown, carmine, cyan, darkcyan, deeporange, green, grey, indigo, magenta, nordic, orange, palebrown, paleorange, pink, red, teal, violet, white, yaru, yellow) via `colors_to_compare` + `closest_color`.\n\nRuns `papirus_folders.sh` asynchronously in the background to update folder colors without blocking Matugen. Disable to leave Papirus folder icons untouched."
        ),
        ConfigItem(
            label="Standalone Commands",
            key="standalone_commands",
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Developer Tools",
            extended_help="**Standalone Shell Commands Theme**\n\nGenerates color export script for standalone tools."
        ),
        ConfigItem(
            label="Dump All Matugen Colors",
            key="master_dump",
            scope="DEFAULT",
            type_="bool",
            default=False,
            group="Developer Tools",
            extended_help="**Master Dump Palette**\n\nGenerates complete raw JSONL dump of all Matugen colors."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 4: DISCOVERED
    # -------------------------------------------------------------------------
    4: [],

    # -------------------------------------------------------------------------
    # TAB 5: PRESETS
    # -------------------------------------------------------------------------
    5: [
        ConfigItem(
            label="Factory Reset — Defaults",
            key="preset_factory_reset",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Built-in Presets",
            confirm_message="Reset **all** templates to their factory defaults?",
            preset_payload={"__ALL_DEFAULTS__": True},
            extended_help="**Factory Reset**\n\nReverts every template toggle to its `default` (true → enabled, false → disabled). Uses `{\"__ALL_DEFAULTS__\": True}` so omitted keys are correctly handled and the match ratio tracks defaults."
        ),
        ConfigItem(
            label="Standard Workstation",
            key="preset_standard",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Built-in Presets",
            confirm_message="Apply Standard Workstation profile? This will enable the curated Dusky desktop suite and disable optional extras.",
            preset_payload={
                "alacritty": False, "beeper": False, "btop": True, "cava": True,
                "dusky_control_center": False, "dusky_tray": True,
                "dusky_cursor": False,
                "dusky_sites": True, "dusky_tui": True, "dusky_visualizer_colors": True,
                "fastfetch": True, "foot": True, "gtk3": True, "gtk4": True,
                "gtksourceview": True, "hyprland": True, "hyprlock": True,
                "icon_theme": True, "kate_syntax": True,
                "kdeglobals": True, "khal": False, "kitty": True, "konsole": True,
                "konsole_profile": True, "kvantum_kvconfig": False, "kvantum_svg": False,
                "gimp": False,
                "mako": True, "master_dump": False, "neovim": True, "obs": False,
                "obsidian": False, "opencode": False, "papirus-folders": False,
                "pywalfox": True, "qt5ct": True, "qt6ct": True, "rofi": True,
                "spicetify": False, "standalone_commands": True, "starship": False,
                "steam": False, "theme_notify": True, "tmux": False, "vesktop": False,
                "vscode": False, "waybar": True, "wlogout": True, "yazi": True,
                "zathura": False, "zed": False, "zellij": False
            },
            extended_help="**Standard Workstation**\n\nCurated Dusky suite: GTK 3/4, Icons, Qt5/6, KDE (kdeglobals + kate_syntax + konsole), Hyprland stack (hyprland, hyprlock, waybar, wlogout, rofi, mako), theme_notify, dusky_tui/visualizer, kitty/foot, neovim/yazi, cava/btop/fastfetch, pywalfox/dusky_sites, standalone_commands. Heavy rebuilders (dusky_cursor, papirus-folders) default to off for instant theme switching."
        ),
        ConfigItem(
            label="Minimal — Core Only",
            key="preset_minimal",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Built-in Presets",
            confirm_message="Apply Minimal profile? Only Hyprland, Waybar, Kitty/Foot, Rofi and Mako will remain enabled.",
            preset_payload={
                "alacritty": False, "beeper": False, "btop": False, "cava": False,
                "dusky_control_center": False, "dusky_tray": True,
                "dusky_cursor": False,
                "dusky_sites": False, "dusky_tui": True, "dusky_visualizer_colors": False,
                "fastfetch": False, "foot": True, "gtk3": False, "gtk4": False,
                "gtksourceview": False, "hyprland": True, "hyprlock": False,
                "icon_theme": False, "kate_syntax": False,
                "kdeglobals": False, "khal": False, "kitty": True, "konsole": False,
                "konsole_profile": False, "kvantum_kvconfig": False, "kvantum_svg": False,
                "gimp": False,
                "mako": True, "master_dump": False, "neovim": False, "obs": False,
                "obsidian": False, "opencode": False, "papirus-folders": False,
                "pywalfox": False, "qt5ct": False, "qt6ct": False, "rofi": True,
                "spicetify": False, "standalone_commands": False, "starship": False,
                "steam": False, "theme_notify": False, "tmux": False, "vesktop": False,
                "vscode": False, "waybar": True, "wlogout": False, "yazi": False,
                "zathura": False, "zed": False, "zellij": False
            },
            extended_help="**Minimal Core**\n\nUltra-light profile for performance or debugging: only `hyprland`, `waybar`, `kitty`/`foot`, `rofi`, `mako`, and `dusky_tui` stay enabled; everything else is disabled. Full strict snapshot."
        ),
        ConfigItem(
            label="Enable All Templates",
            key="preset_all_on",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Built-in Presets",
            confirm_message="Enable **all** templates? This will uncomment every `[templates.*]` block.",
            preset_payload={
                "alacritty": True, "beeper": True, "btop": True, "cava": True,
                "dusky_control_center": True, "dusky_tray": True,
                "dusky_cursor": True,
                "dusky_sites": True, "dusky_tui": True, "dusky_visualizer_colors": True,
                "fastfetch": True, "foot": True, "gtk3": True, "gtk4": True,
                "gtksourceview": True, "hyprland": True, "hyprlock": True,
                "icon_theme": True, "kate_syntax": True,
                "kdeglobals": True, "khal": True, "kitty": True, "konsole": True,
                "konsole_profile": True, "kvantum_kvconfig": True, "kvantum_svg": True,
                "gimp": True,
                "mako": True, "master_dump": True, "neovim": True, "obs": True,
                "obsidian": True, "opencode": True, "papirus-folders": True,
                "pywalfox": True, "qt5ct": True, "qt6ct": True, "rofi": True,
                "spicetify": True, "standalone_commands": True, "starship": True,
                "steam": True, "theme_notify": True, "tmux": True, "vesktop": True,
                "vscode": True, "waybar": True, "wlogout": True, "yazi": True,
                "zathura": True, "zed": True, "zellij": True
            },
            extended_help="**Enable Everything**\n\nTurns **on** every known template block."
        ),
    ]
}

# Populate registered keys set — used by DEFERRED_LOAD to find unmapped templates
for items in SCHEMA.values():
    for item in items:
        if item.type_ != "preset":
            REGISTERED_KEYS.add(item.key)

# =============================================================================
# 5. DYNAMIC AUTO-DISCOVERY (DEFERRED LOAD)
# =============================================================================
def DEFERRED_LOAD() -> tuple[list[int], dict[int, list[ConfigItem]]]:
    """
    Scans `config.toml` for any [templates.<key>] block not registered in static tabs
    and injects them into the Discovered tab.
    """
    cfg_file = Path(TARGET_FILE).expanduser().resolve()
    if not cfg_file.exists():
        return [], {}

    engine = MatugenEngine(config_path=cfg_file)
    state = engine.load_state()

    unmapped = [k for k in state.keys() if k not in REGISTERED_KEYS and "/" not in k]
    if not unmapped:
        placeholder = [
            ConfigItem(
                label="No undiscovered templates",
                key="discovered_placeholder",
                scope="DEFAULT",
                type_="action",
                default=":",
                group="Auto-Discovered",
                extended_help="**Auto-Discovered Templates**\n\nNo undiscovered `[templates.*]` blocks were found. All templates in `config.toml` are already covered by the static tabs. Press F5 after adding templates to refresh this tab."
            )
        ]
        for it in placeholder:
            it.exists_in_target = True
        return [4], {4: placeholder}

    unmapped.sort()

    disc_items: list[ConfigItem] = []
    for key in unmapped:
        item = ConfigItem(
            label=key,
            key=key,
            scope="DEFAULT",
            type_="bool",
            default=True,
            group="Auto-Discovered",
            extended_help=f"**Auto-Discovered Template: {key}**\n\nFound `[templates.{key}]` block in `config.toml` that is not covered by static tabs. Toggle to comment/uncomment the block."
        )
        disc_items.append(item)

    # Return tuple form per MASTER_SCHEMA: indices + new_items dict
    return [4], {4: disc_items}


# =============================================================================
# 6. HEADLESS AUTONOMOUS CLI HANDLERS (--smart / --default)
# =============================================================================
def apply_headless(*, smart: bool) -> int:
    """Apply defaults or installed-package toggles using one configuration snapshot."""
    cfg_file = Path(TARGET_FILE).expanduser().resolve()
    engine = MatugenEngine(config_path=cfg_file)
    state = engine.load_state()
    items = [item for tab in SCHEMA.values() for item in tab if item.type_ == "bool"]
    items.extend(
        ConfigItem(label=key, key=key, type_="bool", default=True, scope="DEFAULT")
        for key in sorted(state)
        if key not in REGISTERED_KEYS and "/" not in key
    )
    changes = []
    for item in items:
        if item.key not in state:
            continue
        command = CHECK_CMDS.get(item.key) if smart else None
        enabled = shutil.which(command) is not None if command else bool(item.default)
        changes.append((item.key, "DEFAULT", "true" if enabled else "false", "bool"))

    if not state:
        print(f"[-] No template blocks found in {cfg_file}.", file=sys.stderr)
        return 1
    ok, message, _ = engine.write_batch(changes)
    if not ok:
        print(f"[-] Configuration update failed: {message}", file=sys.stderr)
        return 1
    print(f"[+] {'Smart package scan applied' if smart else 'Restored defaults'}: {cfg_file}.")
    return 0


def run_smart_scan() -> int:
    return apply_headless(smart=True)


def run_default_reset() -> int:
    return apply_headless(smart=False)


# =============================================================================
# 7. DIRECT EXECUTION ROUTER
# =============================================================================
if __name__ == "__main__":
    if sys.argv[1:] == ["--smart"]:
        sys.exit(run_smart_scan())

    if sys.argv[1:] == ["--default"]:
        sys.exit(run_default_reset())

    main_script = _DUSKY_ROOT / "python" / "main" / "main.py"
    if main_script.exists():
        sys.exit(subprocess.run([sys.executable, str(main_script), str(Path(__file__).resolve()), *sys.argv[1:]]).returncode)
    else:
        print(f"[-] Error: Could not find Dusky TUI master router at {main_script}")
        sys.exit(1)
