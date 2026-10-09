#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: MATUGEN COLOR PRESETS & DYNAMIC PALETTE MANAGER
===============================================================================
Target: Arch Linux / Hyprland / Matugen / Wayland
Python 3.15 implementation replacing legacy bash script dusky_matugen_presets.sh.
Supports all 5 legacy palettes, custom hex/rgb input, full theme/animation controls,
favorites persistence, and a dedicated tab for hardcoded vivid/exact schemas.
===============================================================================
"""

import os
import re
import sys
lazy import json
lazy import subprocess
from pathlib import Path

# --- Inject dusky_tui root into sys.path ---
_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem
from python.engines.matugen_presets import MatugenPresetsEngine

# =============================================================================
# 1. CORE APPLICATION ROUTING (REQUIRED BY DUSKY TUI)
# =============================================================================
ENGINE_TYPE = "matugen_presets"
TARGET_FILE = "~/.config/dusky/settings/dusky_theme/state.conf"
APP_TITLE = "Dusky Color Presets"
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
TABS = [
    "Fav",
    "Vibrant",
    "Neon",
    "Deep",
    "Pastel",
    "Mono",
    "Exact",
    "Custom",
    "Theme",
    "Anim",
    "Presets",
]

# =============================================================================
# 4. PALETTE DATASETS (100% PARITY WITH dusky_matugen_presets.sh)
# =============================================================================

PALETTES: dict[str, list[tuple[str, str]]] = {
    "Vibrant": [
        ("Hyper Red", "#FF0000"),
        ("Electric Blue", "#0000FF"),
        ("Toxic Green", "#00FF00"),
        ("Pure Magenta", "#FF00FF"),
        ("Cyan Punch", "#00FFFF"),
        ("Safety Yellow", "#FFFF00"),
        ("Blood Orange", "#FF4500"),
        ("Plasma Purple", "#6A0DAD"),
        ("Deep Pink", "#FF1493"),
        ("Ultramarine", "#120A8F"),
        ("Emerald City", "#50C878"),
        ("Crimson Tide", "#DC143C"),
        ("Chartreuse", "#7FFF00"),
        ("Spring Green", "#00FF7F"),
        ("Azure Sky", "#007FFF"),
        ("Violet Ray", "#EE82EE"),
        ("Aquamarine", "#7FFFD4"),
        ("Solid Gold", "#FFD700"),
        ("Rich Teal", "#008080"),
        ("Olive Drab", "#808000"),
    ],
    "Neon": [
        ("Laser Lemon", "#FFFF66"),
        ("Hot Pink", "#FF69B4"),
        ("Cyber Grape", "#58427C"),
        ("Neon Carrot", "#FFA343"),
        ("Matrix Green", "#03A062"),
        ("Electric Indigo", "#6F00FF"),
        ("Miami Pink", "#FF5AC4"),
        ("Vice Blue", "#00C6FF"),
        ("Radioactive", "#CCFF00"),
        ("Plastic Purple", "#D400FF"),
        ("Arcade Red", "#FF0055"),
        ("Hacker Green", "#00FF2A"),
        ("Synthwave Sun", "#FF7E00"),
        ("Tron Cyan", "#6EFFFF"),
        ("Flux Capacitor", "#FFAE00"),
        ("Highlighter Blue", "#1F51FF"),
        ("Shocking Pink", "#FC0FC0"),
        ("Lime Light", "#BFFF00"),
    ],
    "Deep": [
        ("Midnight Blue", "#191970"),
        ("Dark Slate", "#2F4F4F"),
        ("Saddle Brown", "#8B4513"),
        ("Dark Olive", "#556B2F"),
        ("Indigo Dye", "#4B0082"),
        ("Maroon", "#800000"),
        ("Navy", "#000080"),
        ("Dark Green", "#006400"),
        ("Dark Cyan", "#008B8B"),
        ("Dark Magenta", "#8B008B"),
        ("Tyrian Purple", "#66023C"),
        ("Oxblood", "#4A0404"),
        ("Deep Forest", "#013220"),
        ("Night Sky", "#0C090A"),
        ("Black Cherry", "#540026"),
        ("Deep Coffee", "#3B2F2F"),
    ],
    "Pastel": [
        ("Baby Blue", "#89CFF0"),
        ("Mint Cream", "#F5FFFA"),
        ("Lavender", "#E6E6FA"),
        ("Peach Puff", "#FFDAB9"),
        ("Misty Rose", "#FFE4E1"),
        ("Honeydew", "#F0FFF0"),
        ("Alice Blue", "#F0F8FF"),
        ("Lemon Chiffon", "#FFFACD"),
        ("Tea Green", "#D0F0C0"),
        ("Celeste", "#B2FFFF"),
        ("Mauve", "#E0B0FF"),
        ("Salmon", "#FA8072"),
        ("Cornflower", "#6495ED"),
        ("Thistle", "#D8BFD8"),
        ("Wheat", "#F5DEB3"),
    ],
    "Mono": [
        ("Pure Black", "#000000"),
        ("Pure White", "#FFFFFF"),
        ("Dim Gray", "#696969"),
        ("Slate Gray", "#708090"),
        ("Light Slate", "#778899"),
        ("Silver", "#C0C0C0"),
        ("Gainsboro", "#DCDCDC"),
        ("Charcoal", "#36454F"),
        ("Onyx", "#353839"),
        ("Gunmetal", "#2A3439"),
    ],
}

def _clean_slug(text: str) -> str:
    """Creates a deterministic alphanumeric identifier."""
    return re.sub(r"[^a-zA-Z0-9_]+", "_", text.strip().lower()).strip("_")

# =============================================================================
# 5. DYNAMIC BUILDERS FOR FAVORITES & EXACT TABS
# =============================================================================

def _build_favorites_items(engine: MatugenPresetsEngine | None = None) -> list[ConfigItem]:
    """Generates schema rows for Tab 0 (Favorites)."""
    if engine is None:
        engine = MatugenPresetsEngine()
        engine.load_state()

    items: list[ConfigItem] = [
        ConfigItem(
            label="Active Color",
            key="last_applied_hex",
            scope="DEFAULT",
            type_="color",
            default="#0000FF",
            group="Palette Controls",
            extended_help=(
                "**Active Color**\n\n"
                "The currently applied system color palette source. "
                "You can press Enter to type any custom hex color directly."
            ),
        ),
        ConfigItem(
            label="Save Active Color as Favorite",
            key="action_add_favorite",
            scope="DEFAULT",
            type_="bool",
            default=False,
            options=["trigger:Favorite Active"],
            group="Palette Controls",
            extended_help=(
                "**Save Active Favorite**\n\n"
                "Saves the currently applied hex color into your persistent "
                "`theme_preset_fav` favorites file."
            ),
        ),
        ConfigItem(
            label="Remove Active from Favorites",
            key="action_remove_favorite",
            scope="DEFAULT",
            type_="bool",
            default=False,
            options=["trigger:Remove Active"],
            group="Palette Controls",
            extended_help=(
                "**Remove Active Favorite**\n\n"
                "Removes the current active color from your saved favorites list."
            ),
        ),
    ]

    if engine.favorites:
        for idx, (label, hex_code) in enumerate(engine.favorites):
            items.append(
                ConfigItem(
                    label=label,
                    key=f"fav_apply_{idx}",
                    scope="DEFAULT",
                    type_="bool",
                    default=False,
                    options=[f"trigger:{hex_code}"],
                    group="Saved Favorites",
                    extended_help=(
                        f"**{label}**\n\n"
                        f"HEX: `{hex_code}`\n\n"
                        f"Press Enter to apply this saved favorite color preset."
                    ),
                )
            )
    else:
        items.append(
            ConfigItem(
                label="No Favorites Saved Yet",
                key="fav_notice_empty",
                scope="DEFAULT",
                type_="action",
                default=":",
                group="Saved Favorites",
                extended_help=(
                    "**Favorites List**\n\n"
                    "You have no saved favorites yet.\n"
                    "Select any color preset and click 'Favorite Active' to add it here!"
                ),
            )
        )

    return items

def _build_exact_items(engine: MatugenPresetsEngine | None = None) -> list[ConfigItem]:
    """Generates schema rows for Tab 6 (Exact / Hardcoded Schemas)."""
    if engine is None:
        engine = MatugenPresetsEngine()
        engine.load_state()

    items: list[ConfigItem] = [
        ConfigItem(
            label="Active Color",
            key="active_color",
            scope="exact",
            type_="color",
            default="#0000FF",
            group="Exact Controls",
            extended_help=(
                "**Active Color**\n\n"
                "The currently applied system color palette source."
            ),
        ),
        ConfigItem(
            label="Exact Scheme Strategy",
            key="exact_scheme_override",
            scope="DEFAULT",
            type_="cycle",
            default="per-preset",
            options=[
                "per-preset",
                "scheme-fidelity",
                "scheme-vibrant",
                "scheme-fruit-salad",
                "scheme-content",
                "scheme-rainbow",
                "scheme-monochrome",
            ],
            group="Exact Controls",
            extended_help=(
                "**Exact Scheme Strategy**\n\n"
                "- `per-preset`: Uses each preset's optimal hardcoded schema (e.g. `scheme-fidelity` for pure primary colors).\n"
                "- `scheme-*`: Force a specific scheme for all exact presets.\n\n"
                "This guarantees vivid, accurate colors matching the palette without unwanted tonal desaturation."
            ),
        ),
        ConfigItem(
            label="Save Active Color as Favorite",
            key="action_add_favorite",
            scope="exact",
            type_="bool",
            default=False,
            options=["trigger:Favorite Active"],
            group="Exact Controls",
            extended_help="Saves the current active color into your favorites.",
        ),
    ]

    for p in engine.exact_presets:
        clean_scheme = p.get("scheme", "scheme-fidelity").removeprefix("scheme-")
        items.append(
            ConfigItem(
                label=f"{p['label']} [{clean_scheme}]",
                key=f"exact_preset_{p['id']}",
                scope="DEFAULT",
                type_="bool",
                default=False,
                options=[f"trigger:{p['hex']}"],
                group="Hardcoded Vivid & Accurate Presets",
                extended_help=(
                    f"**{p['label']}**\n\n"
                    f"- HEX: `{p['hex']}`\n"
                    f"- Hardcoded Scheme: `{p.get('scheme', 'scheme-fidelity')}`\n"
                    f"- Contrast: `{p.get('contrast', '0')}`\n"
                    f"- Mode: `{p.get('mode', 'dark')}`\n\n"
                    f"{p.get('desc', 'Guarantees accurate, vivid generation.')}\n\n"
                    f"Hit Enter to apply this exact color with its hardcoded scheme."
                ),
            )
        )

    # Custom hardcoded preset builder section
    items.extend([
        ConfigItem(
            label="New Exact Color HEX",
            key="new_exact_hex",
            scope="DEFAULT",
            type_="string",
            default="#FF0080",
            group="Add Hardcoded Preset",
            extended_help="Enter a hex code for a new curated preset entry.",
        ),
        ConfigItem(
            label="Hardcoded Scheme Type",
            key="new_exact_scheme",
            scope="DEFAULT",
            type_="cycle",
            default="scheme-fidelity",
            options=[
                "scheme-fidelity",
                "scheme-vibrant",
                "scheme-fruit-salad",
                "scheme-content",
                "scheme-rainbow",
                "scheme-monochrome",
            ],
            group="Add Hardcoded Preset",
            extended_help="Select the exact scheme algorithm to hardcode with this color.",
        ),
        ConfigItem(
            label="Save New Hardcoded Preset",
            key="action_add_exact_preset",
            scope="DEFAULT",
            type_="bool",
            default=False,
            options=["trigger:Save Hardcoded"],
            group="Add Hardcoded Preset",
            extended_help="Saves this new color and its assigned schema into `exact_color_presets.json`.",
        ),
    ])

    # Deletion controls for user-added custom exact presets
    custom_exacts = [p for p in engine.exact_presets if p.get("id", "").startswith("custom_")]
    if custom_exacts:
        for p in custom_exacts:
            items.append(
                ConfigItem(
                    label=f"Delete {p['label']}",
                    key=f"action_del_exact_{p['id']}",
                    scope="DEFAULT",
                    type_="bool",
                    default=False,
                    options=["trigger:Delete Preset"],
                    group="Manage Custom Hardcoded Presets",
                    extended_help=f"Removes `{p['label']}` from `exact_color_presets.json`.",
                )
            )

    return items

def DEFERRED_LOAD() -> tuple[list[int], dict[int, list[ConfigItem]]]:
    """
    Called by Dusky TUI after initial render and whenever the user presses F5.
    Dynamically refreshes the Favorites tab (0) and Exact Schemes tab (6).
    """
    engine = MatugenPresetsEngine()
    engine.load_state()

    fav_items = _build_favorites_items(engine)
    exact_items = _build_exact_items(engine)

    return [0, 6], {0: fav_items, 6: exact_items}

# =============================================================================
# 6. MASTER SCHEMA DEFINITION
# =============================================================================

SCHEMA: dict[int, list[ConfigItem]] = {i: [] for i in range(len(TABS))}

# --- TAB 0: FAVORITES ---
SCHEMA[0] = _build_favorites_items()

# --- TABS 1-5: COLOR PALETTES (Vibrant, Neon, Deep, Pastel, Mono) ---
_PALETTE_TAB_MAP = [
    (1, "Vibrant", "Vibrant Presets"),
    (2, "Neon", "Neon Presets"),
    (3, "Deep", "Deep Presets"),
    (4, "Pastel", "Pastel Presets"),
    (5, "Mono", "Monochrome Presets"),
]

for tab_idx, pal_name, group_title in _PALETTE_TAB_MAP:
    palette_colors = PALETTES[pal_name]
    tab_items: list[ConfigItem] = []

    prefix = pal_name[:3].lower()
    for name, hex_code in palette_colors:
        slug = _clean_slug(name)
        clean_hex = hex_code.lstrip("#")
        tab_items.append(
            ConfigItem(
                label=name,
                key=f"color_{prefix}_{slug}__{clean_hex}",
                scope="DEFAULT",
                type_="bool",
                default=False,
                options=[f"trigger:{hex_code}"],
                group=group_title,
                extended_help=(
                    f"**{name}**\n\n"
                    f"HEX: `{hex_code}`\n\n"
                    f"Press Enter to apply this color preset across your system via Matugen."
                ),
            )
        )

    SCHEMA[tab_idx] = tab_items

# --- TAB 6: EXACT / HARDCODED SCHEMAS ---
SCHEMA[6] = _build_exact_items()

# --- TAB 7: CUSTOM INPUT ---
SCHEMA[7] = [
    ConfigItem(
        label="Input HEX Code",
        key="custom_hex",
        scope="DEFAULT",
        type_="string",
        default="#FF0000",
        group="Custom Input",
        extended_help=(
            "**Custom HEX Input**\n\n"
            "Enter any 6-character hex color (e.g. `#FF0055` or `00C6FF`).\n"
            "Press Enter to generate and apply the Matugen palette immediately."
        ),
    ),
    ConfigItem(
        label="Input RGB Values",
        key="custom_rgb",
        scope="DEFAULT",
        type_="string",
        default="255 0 0",
        group="Custom Input",
        extended_help=(
            "**Custom RGB Input**\n\n"
            "Enter 3 space-separated RGB numbers between 0 and 255 (e.g. `255 0 128`).\n"
            "Press Enter to convert to hex and apply the palette immediately."
        ),
    ),
    ConfigItem(
        label="Input HSL Values",
        key="custom_hsl",
        scope="DEFAULT",
        type_="string",
        default="240 100% 50%",
        group="Custom Input",
        extended_help=(
            "**Custom HSL Input**\n\n"
            "Enter Hue (0-360), Saturation (0-100%), and Lightness (0-100%).\n"
            "Examples: `340 100% 50%`, `240 100 50`, `hsl(120, 100%, 40%)`.\n"
            "Press Enter to convert to hex and apply the palette immediately."
        ),
    ),
    ConfigItem(
        label="Regenerate Last Applied Color",
        key="action_regen",
        scope="DEFAULT",
        type_="bool",
        default=False,
        options=["trigger:Regenerate"],
        group="Custom Input",
        extended_help=(
            "**Regenerate Last Color**\n\n"
            "Re-runs Matugen generation for the last active hex color, updating all app templates."
        ),
    ),
]

# --- TAB 8: THEME SETTINGS ---
SCHEMA[8] = [
    ConfigItem(
        label="Apply Theme Settings",
        key="action_apply_theme",
        scope="DEFAULT",
        type_="bool",
        default=False,
        options=["trigger:Apply Settings"],
        group="Theme Configuration",
        extended_help=(
            "**Apply & Save Settings**\n\n"
            "Applies and saves all theme settings to `theme_ctl` and the desktop interface."
        ),
    ),
    ConfigItem(
        label="Mode",
        key="mode",
        scope="DEFAULT",
        type_="cycle",
        default="dark",
        options=["dark", "light"],
        group="Theme Configuration",
        extended_help=(
            "**Theme Mode**\n\n"
            "Selects system-wide dark or light palette generation."
        ),
    ),
    ConfigItem(
        label="Scheme Type",
        key="type",
        scope="DEFAULT",
        type_="cycle",
        default="scheme-tonal-spot",
        options=[
            "scheme-fidelity",
            "scheme-content",
            "scheme-fruit-salad",
            "scheme-vibrant",
            "scheme-rainbow",
            "scheme-neutral",
            "scheme-tonal-spot",
            "scheme-expressive",
            "scheme-monochrome",
            "scheme-smart",
            "disable",
        ],
        group="Theme Configuration",
        extended_help=(
            "**Matugen Scheme Type**\n\n"
            "- `scheme-fidelity`: Preserves source color hue and saturation strictly.\n"
            "- `scheme-vibrant`: Maximizes chromatic vibrancy and punchy accents.\n"
            "- `scheme-fruit-salad`: Playful color combinations.\n"
            "- `scheme-tonal-spot`: Standard Material You spec with balanced tonal spots.\n"
            "- `scheme-monochrome`: Pure black/white grayscale with no invented hues."
        ),
    ),
    ConfigItem(
        label="Contrast",
        key="contrast",
        scope="DEFAULT",
        type_="cycle",
        default="0",
        options=[
            "0",
            "-1.0",
            "-0.8",
            "-0.6",
            "-0.4",
            "-0.2",
            "0.2",
            "0.4",
            "0.6",
            "0.8",
            "1.0",
            "disable",
        ],
        group="Theme Configuration",
        extended_help=(
            "**Contrast Level**\n\n"
            "`0` is standard spec. Negative values decrease contrast, positive values increase it."
        ),
    ),
    ConfigItem(
        label="Source Color Index",
        key="index",
        scope="DEFAULT",
        type_="cycle",
        default="0",
        options=["0", "1", "2", "3"],
        group="Theme Configuration",
        extended_help="Selects which dominant color extracted from images is prioritized.",
    ),
    ConfigItem(
        label="Base16 Backend",
        key="base16",
        scope="DEFAULT",
        type_="cycle",
        default="disable",
        options=["disable", "wal"],
        group="Theme Configuration",
        extended_help="Selects base16 color generation backend (`wal` or `disable`).",
    ),
]

# --- TAB 9: ANIMATION SETTINGS ---
SCHEMA[9] = [
    ConfigItem(
        label="Apply Animation Settings",
        key="action_apply_anim",
        scope="DEFAULT",
        type_="bool",
        default=False,
        options=["trigger:Apply Settings"],
        group="Wallpaper Animation",
        extended_help="Applies and saves transition animation settings for `awww`.",
    ),
    ConfigItem(
        label="Transition Type",
        key="t_type",
        scope="DEFAULT",
        type_="cycle",
        default="random",
        options=[
            "random",
            "simple",
            "fade",
            "left",
            "right",
            "top",
            "bottom",
            "wipe",
            "wave",
            "grow",
            "center",
            "any",
            "outer",
            "none",
            "disable",
        ],
        group="Wallpaper Animation",
        extended_help="Transition effect used when changing wallpapers.",
    ),
    ConfigItem(
        label="Duration (sec)",
        key="t_dur",
        scope="DEFAULT",
        type_="cycle",
        default="2",
        options=["disable", "0.5", "1", "2", "3", "5", "10"],
        group="Wallpaper Animation",
        extended_help="Transition duration in seconds.",
    ),
    ConfigItem(
        label="FPS",
        key="t_fps",
        scope="DEFAULT",
        type_="cycle",
        default="60",
        options=["disable", "30", "60", "90", "120", "144"],
        group="Wallpaper Animation",
        extended_help="Transition frame rate limit.",
    ),
    ConfigItem(
        label="Bezier Curve",
        key="t_bez",
        scope="DEFAULT",
        type_="cycle",
        default=".54,0,.34,.99",
        options=[
            "disable",
            ".54,0,.34,.99",
            "0,0,1,1",
            ".85,0,.15,1",
            ".17,.67,.83,.67",
        ],
        group="Wallpaper Animation",
        extended_help="Cubic bezier easing velocity curve.",
    ),
    ConfigItem(
        label="Angle (Deg)",
        key="t_ang",
        scope="DEFAULT",
        type_="cycle",
        default="30",
        options=["disable", "0", "30", "45", "90", "135", "180", "225", "270", "315"],
        group="Wallpaper Animation",
        extended_help="Transition direction angle in degrees for directional effects.",
    ),
    ConfigItem(
        label="Position",
        key="t_pos",
        scope="DEFAULT",
        type_="cycle",
        default="center",
        options=[
            "disable",
            "center",
            "top",
            "left",
            "right",
            "bottom",
            "top-left",
            "top-right",
            "bottom-left",
            "bottom-right",
        ],
        group="Wallpaper Animation",
        extended_help="Center origin position for radial transitions like grow.",
    ),
]

# --- TAB 10: PRESETS ---
# Native Dusky TUI Profiles tab.
SCHEMA[10] = [
    ConfigItem(
        label="Factory Reset All",
        key="preset_factory_reset",
        scope="DEFAULT",
        type_="preset",
        default=None,
        group="System Defaults",
        confirm_message="Reset all theme settings to defaults?",
        preset_payload={"__ALL_DEFAULTS__": True},
        extended_help="Reverts all settings back to their original system defaults.",
    )
]

# =============================================================================
# 7. STANDALONE EXECUTION / SCRIPTING CLI
# =============================================================================

def main() -> int:
    """Standalone CLI entry point and TUI launcher."""
    script_path = Path(__file__).resolve()
    main_router = _DUSKY_TUI_ROOT / "python" / "main" / "main.py"

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg in ("-h", "--help"):
            print("Dusky Color Presets TUI & CLI")
            print("Usage:")
            print("  python3 tui_color_presets.py                           # Launch interactive TUI")
            print("  python3 tui_color_presets.py --color #HEX             # Apply hex color directly")
            print("  python3 tui_color_presets.py --rgb <R> <G> <B>        # Apply RGB color")
            print("  python3 tui_color_presets.py --hsl <H> <S> <L>        # Apply HSL color")
            print("  python3 tui_color_presets.py --exact <ID>             # Apply exact hardcoded preset")
            print("  python3 tui_color_presets.py --preview #HEX [SCHEME]  # In-memory Material You preview")
            print("  python3 tui_color_presets.py --list-exact              # List all hardcoded presets")
            print("  python3 tui_color_presets.py --add-exact <HEX> <SCHEME> [LABEL] # Add exact preset")
            print("  python3 tui_color_presets.py --del-exact <ID>         # Delete exact preset")
            print("  python3 tui_color_presets.py --list-fav                # List saved favorites")
            print("  python3 tui_color_presets.py --add-fav <HEX> [LABEL]  # Add favorite color")
            print("  python3 tui_color_presets.py --del-fav <HEX>          # Delete favorite color")
            print("  python3 tui_color_presets.py --apply-settings          # Apply cached theme/anim settings")
            return 0

        engine = MatugenPresetsEngine()
        engine.load_state()

        if arg == "--color":
            if len(sys.argv) < 3:
                print("[-] Error: Missing HEX argument for --color (e.g. --color '#FF0000')", file=sys.stderr)
                return 1
            ok, msg = engine.apply_color(sys.argv[2])
            print(f"[OK] {msg}" if ok else f"[-] {msg}", file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1

        elif arg == "--rgb":
            if len(sys.argv) < 3:
                print("[-] Error: Missing RGB arguments for --rgb (e.g. --rgb 255 0 128)", file=sys.stderr)
                return 1
            rgb_str = " ".join(sys.argv[2:]) if len(sys.argv) > 3 else sys.argv[2]
            ok, msg, _ = engine.write_value("custom_rgb", "DEFAULT", rgb_str, "string")
            print(f"[OK] {msg}" if ok else f"[-] {msg}", file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1

        elif arg == "--hsl":
            if len(sys.argv) < 3:
                print("[-] Error: Missing HSL arguments for --hsl (e.g. --hsl '340 100% 50%')", file=sys.stderr)
                return 1
            hsl_str = " ".join(sys.argv[2:]) if len(sys.argv) > 3 else sys.argv[2]
            ok, msg, _ = engine.write_value("custom_hsl", "DEFAULT", hsl_str, "string")
            print(f"[OK] {msg}" if ok else f"[-] {msg}", file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1

        elif arg == "--exact":
            if len(sys.argv) < 3:
                print("[-] Error: Missing preset ID for --exact (e.g. --exact vivid_red)", file=sys.stderr)
                return 1
            target_id = sys.argv[2]
            matched = next((p for p in engine.exact_presets if p["id"] == target_id), None)
            if not matched:
                print(f"[-] Exact preset '{target_id}' not found.", file=sys.stderr)
                return 1
            ok, msg = engine.apply_color(
                matched["hex"],
                scheme_override=matched.get("scheme", "scheme-fidelity"),
                contrast_override=matched.get("contrast", "0"),
                mode_override=matched.get("mode", "dark"),
            )
            print(f"[OK] {msg}" if ok else f"[-] {msg}", file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1

        elif arg == "--preview":
            if len(sys.argv) < 3:
                print("[-] Error: Missing HEX argument for --preview (e.g. --preview '#FF0000' [SCHEME])", file=sys.stderr)
                return 1
            hex_target = sys.argv[2]
            scheme_target = sys.argv[3] if len(sys.argv) > 3 else "scheme-tonal-spot"
            mode_target = sys.argv[4] if len(sys.argv) > 4 else "dark"
            contrast_target = sys.argv[5] if len(sys.argv) > 5 else "0"
            preview = engine.get_palette_preview(
                hex_target,
                scheme=scheme_target,
                mode=mode_target,
                contrast=contrast_target,
            )
            if not preview:
                print(f"[-] Failed to generate preview for '{hex_target}'.", file=sys.stderr)
                return 1
            print(f"Material You In-Memory Token Preview ({hex_target} | {scheme_target} | {mode_target}):")
            for token, col in preview.items():
                print(f"  {token:<25} {col}")
            return 0

        elif arg == "--list-exact":
            print("Available Hardcoded Vivid & Accurate Presets:")
            for p in engine.exact_presets:
                print(f"  {p['id']:<18} {p['hex']:<9} [{p.get('scheme', 'scheme-fidelity')}] - {p['label']}")
            return 0

        elif arg == "--add-exact":
            if len(sys.argv) < 4:
                print("[-] Error: Usage: --add-exact <HEX> <SCHEME> [LABEL]", file=sys.stderr)
                return 1
            hex_val = sys.argv[2]
            scheme_val = sys.argv[3]
            lbl = sys.argv[4] if len(sys.argv) > 4 else None
            ok, res = engine.add_exact_preset(hex_val, scheme=scheme_val, label=lbl)
            if ok:
                print(f"[OK] Added exact preset '{res}' ({hex_val} [{scheme_val}])")
                return 0
            else:
                print(f"[-] {res}", file=sys.stderr)
                return 1

        elif arg == "--del-exact":
            if len(sys.argv) < 3:
                print("[-] Error: Missing preset ID for --del-exact", file=sys.stderr)
                return 1
            target_id = sys.argv[2]
            if engine.delete_exact_preset(target_id):
                print(f"[OK] Deleted exact preset '{target_id}'")
                return 0
            else:
                print(f"[-] Exact preset '{target_id}' not found.", file=sys.stderr)
                return 1

        elif arg == "--list-fav":
            print(f"Saved Favorites ({len(engine.favorites)}):")
            for lbl, hx in engine.favorites:
                print(f"  {lbl:<25} {hx}")
            return 0

        elif arg == "--add-fav":
            if len(sys.argv) < 3:
                print("[-] Error: Missing HEX argument for --add-fav", file=sys.stderr)
                return 1
            hex_val = sys.argv[2]
            lbl = sys.argv[3] if len(sys.argv) > 3 else None
            ok, msg = engine.add_favorite(hex_val, lbl)
            print(f"[OK] {msg}" if ok else f"[-] {msg}", file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1

        elif arg == "--del-fav":
            if len(sys.argv) < 3:
                print("[-] Error: Missing HEX or label for --del-fav", file=sys.stderr)
                return 1
            target = sys.argv[2]
            ok, msg = engine.remove_favorite(target)
            print(f"[OK] {msg}" if ok else f"[-] {msg}", file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1

        elif arg == "--apply-settings":
            ok, msg = engine.apply_settings()
            print(f"[OK] {msg}" if ok else f"[-] {msg}", file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1

    # Delegate to Dusky TUI master router
    if not main_router.is_file():
        print(f"[-] Error: Master router not found at {main_router}", file=sys.stderr)
        return 1

    cmd = [sys.executable, str(main_router), str(script_path)] + sys.argv[1:]
    return subprocess.run(cmd).returncode

if __name__ == "__main__":
    sys.exit(main())
