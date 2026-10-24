#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: MATUGEN COLOR PRESETS & DYNAMIC PALETTE MANAGER
===============================================================================
Target: Arch Linux / Hyprland / Matugen / Wayland
Python 3.15 implementation replacing legacy bash script dusky_matugen_presets.sh.
Groups seed colors separately from curated generation recipes and baked Vivid packs.
Supports custom HEX/RGB/HSL input, theme/animation controls, and saved favorites.
===============================================================================
"""

import re
import sys
lazy import subprocess
from pathlib import Path

# --- Inject dusky_tui and vivid root into sys.path ---
_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))
_VIVID_DIR = Path.home() / "user_scripts" / "theme_matugen" / "vivid"
if str(_VIVID_DIR) not in sys.path:
    sys.path.insert(0, str(_VIVID_DIR))

from python.frontend.core_types import ConfigItem
from python.engines.matugen_presets import MatugenPresetsEngine
lazy import vivid_data

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
TABS = ["Favorites", "Vivid", "Seeds", "Curated", "Custom", "Theme", "Anim", "Presets"]
_TAB_INDEX = {name: index for index, name in enumerate(TABS)}

TAB_NOTICES = {
    _TAB_INDEX["Favorites"]: {"message": "Vivid favorites restore their complete pack. Seed favorites generate with current Theme settings. After saving or removing a favorite, press F5 to refresh the list."},
    _TAB_INDEX["Vivid"]: {"message": "Complete baked palettes with fixed colors. Favorite Active saves the whole pack. Applying Theme settings generates from its accent and leaves the baked palette."},
    _TAB_INDEX["Seeds"]: {"message": "Input colors for Matugen, grouped by seed appearance. Generated accents depend on Theme settings; pure grays use monochrome unless the scheme is disabled."},
    _TAB_INDEX["Curated"]: {"message": "Saved color + scheme recipes. Each applies its saved mode and contrast and updates Theme settings. Generated tones can differ from the seed."},
}

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
            label="Favorite Active Theme",
            key="action_add_favorite",
            scope="DEFAULT",
            type_="bool",
            default=False,
            options=["trigger:Favorite Active"],
            group="Palette Controls",
            extended_help=(
                "**Save Active Favorite**\n\n"
                "Saves the active Vivid pack by its identity, or saves the current seed color for generation with Theme settings."
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
                "Removes the active Vivid pack or seed color from your favorites."
            ),
        ),
    ]

    if engine.vivid_favorites_error:
        items.append(ConfigItem(
            label="Vivid Favorites File Error",
            key="vivid_favorites_notice",
            type_="string",
            default=engine.vivid_favorites_error,
            read_only=True,
            group="Saved Vivid Packs",
            extended_help=f"Cannot read `{engine.vivid_favorites_file}`. Correct the file, then refresh with F5. Existing content is retained.",
        ))

    for preset in engine.vivid_favorites:
        items.append(
            ConfigItem(
                label=preset["label"],
                key=f"fav_vivid_apply_{preset['id']}",
                type_="bool",
                default=False,
                options=[f"trigger:{preset['hex']}"],
                group="Saved Vivid Packs",
                extended_help=(
                    f"**{preset['label']}**\n\n"
                    f"Restores the complete Vivid pack `{preset['id']}` through the Vivid apply path. "
                    "If its definition is unavailable, applying reports an error."
                ),
            )
        )

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
                    group="Saved Seed Colors",
                    extended_help=(
                        f"**{label}**\n\n"
                        f"HEX: `{hex_code}`\n\n"
                        "Generates from this saved seed using current Theme settings. Vivid pack favorites are listed separately."
                    ),
                )
            )
    elif not engine.vivid_favorites and not engine.vivid_favorites_error:
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
                    "Apply a Vivid pack or seed color, then choose Favorite Active to save it here."
                ),
            )
        )

    for preset in engine.vivid_favorites:
        items.append(ConfigItem(
            label=f"Remove {preset['label']}",
            key=f"fav_vivid_remove_{preset['id']}",
            type_="bool",
            default=False,
            options=["trigger:Remove Favorite"],
            group="Manage Saved Vivid Packs",
            extended_help="Removes this favorite without applying or deleting the Vivid pack.",
        ))

    return items

def _build_exact_items(engine: MatugenPresetsEngine | None = None) -> list[ConfigItem]:
    """Generates curated color-and-scheme recipes; legacy keys remain stable."""
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
            group="Curated Controls",
            extended_help=(
                "**Active Color**\n\n"
                "The currently applied system color palette source."
            ),
        ),
        ConfigItem(
            label="Curated Scheme Override",
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
            group="Curated Controls",
            extended_help=(
                "**Curated Scheme Override**\n\n"
                "- `per-preset`: Uses each preset's saved scheme (e.g. `scheme-fidelity` for pure primary colors).\n"
                "- `scheme-*`: Force a specific scheme for all curated presets.\n\n"
                "These recipes generate Material You tones using their saved mode and contrast. They do not reproduce the seed exactly; use Vivid for baked desktop palettes."
            ),
        ),
        ConfigItem(
            label="Favorite Active Theme",
            key="action_add_favorite",
            scope="exact",
            type_="bool",
            default=False,
            options=["trigger:Favorite Active"],
            group="Curated Controls",
            extended_help="Saves the active Vivid pack or seed color into your favorites.",
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
                group="Color + Scheme Recipes",
                extended_help=(
                    f"**{p['label']}**\n\n"
                    f"- HEX: `{p['hex']}`\n"
                    f"- Hardcoded Scheme: `{p.get('scheme', 'scheme-fidelity')}`\n"
                    f"- Contrast: `{p.get('contrast', '0')}`\n"
                    f"- Mode: `{p.get('mode', 'dark')}`\n\n"
                    "Matugen derives tonal colors from this seed; generated accents may differ.\n\n"
                    "Press Enter to generate with this recipe. This also updates Theme settings."
                ),
            )
        )

    # Custom hardcoded preset builder section
    items.extend([
        ConfigItem(
            label="New Recipe HEX",
            key="new_exact_hex",
            scope="DEFAULT",
            type_="string",
            default="#FF0080",
            group="Add Recipe (Dark Mode, Standard Contrast)",
            extended_help="Enter a hex code for a new curated preset entry.",
        ),
        ConfigItem(
            label="Recipe Scheme",
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
            group="Add Recipe (Dark Mode, Standard Contrast)",
            extended_help="Select the generation scheme to save with this seed color.",
        ),
        ConfigItem(
            label="Save New Recipe",
            key="action_add_exact_preset",
            scope="DEFAULT",
            type_="bool",
            default=False,
            options=["trigger:Save Recipe"],
            group="Add Recipe (Dark Mode, Standard Contrast)",
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
                    group="Manage Custom Recipes",
                    extended_help=f"Removes `{p['label']}` from `exact_color_presets.json`.",
                )
            )

    return items


def _build_vivid_items() -> list[ConfigItem]:
    """Build baked pack selectors without changing their definitions or colors."""

    controls = [
        ConfigItem(
            label="Active Vivid Color",
            key="vivid_active_hex",
            scope="DEFAULT",
            type_="color",
            default="#00FF00",
            read_only=True,
            group="Vivid Status",
            extended_help="Accent of the active Vivid pack, or the last seed when no pack is active. Read-only.",
        ),
        ConfigItem(
            label="Active Vivid Preset",
            key="vivid_active_id",
            scope="DEFAULT",
            type_="string",
            default="none",
            read_only=True,
            group="Vivid Status",
            extended_help="Pack identity recorded in state.conf. Read-only.",
        ),
        ConfigItem(
            label="Favorite Active Theme",
            key="action_add_favorite",
            scope="vivid",
            type_="bool",
            default=False,
            options=["trigger:Favorite Active"],
            group="Vivid Status",
            extended_help="Saves the active Vivid pack by its identity so Favorites can restore the complete pack. Press F5 to refresh the Favorites list.",
        ),
        ConfigItem(
            label="Return to Wallpaper",
            key="action_resume_wallpaper",
            scope="DEFAULT",
            type_="bool",
            default=False,
            options=["trigger:Wallpaper"],
            group="Vivid Status",
            extended_help="Restores owned asset choices and resumes wallpaper generation.",
        ),
    ]

    presets, diagnostics = vivid_data.load_presets_and_diagnostics()

    diag_rows = [
        ConfigItem(
            label=f"[!] {diag[:30]}...",
            key=f"vivid_diag_{i}",
            scope="DEFAULT",
            type_="string",
            default=diag,
            read_only=True,
            group="Definition Diagnostics",
            extended_help=f"**Source Definition Diagnostic**\n\n`{diag}`\n\nResolve this error in the Vivid source definitions to restore this preset.",
        )
        for i, diag in enumerate(diagnostics)
    ]

    rows = [
        ConfigItem(
            label=preset["label"],
            key=f"vivid_apply_{preset['id']}",
            scope="DEFAULT",
            type_="bool",
            default=False,
            options=[f"trigger:{preset['hex']}"],
            group="Vivid Packs",
            extended_help=(
                f"**{preset['label']}**\n\n"
                f"Accent: `{preset['hex']}`. Background: `{preset.get('bg_hex', '')}`.\n\n"
                f"{preset.get('desc', '')}\n\n"
                "Applies a pre-baked vivid desktop palette instantly from cache, or auto-recovers missing packs."
            ),
        )
        for preset in presets
    ]
    return controls + diag_rows + rows


def DEFERRED_LOAD() -> tuple[list[int], dict[int, list[ConfigItem]]]:
    """
    Called by Dusky TUI after initial render and whenever the user presses F5.
    Refreshes Favorites, Vivid, and Curated using their current tab positions.
    """
    engine = MatugenPresetsEngine()
    engine.load_state()

    refreshed = {
        _TAB_INDEX["Favorites"]: _build_favorites_items(engine),
        _TAB_INDEX["Vivid"]: _build_vivid_items(),
        _TAB_INDEX["Curated"]: _build_exact_items(engine),
    }
    return list(refreshed), refreshed

# =============================================================================
# 6. MASTER SCHEMA DEFINITION
# =============================================================================

SCHEMA: dict[int, list[ConfigItem]] = {i: [] for i in range(len(TABS))}

# --- TAB 0: FAVORITES ---
_initial_engine = MatugenPresetsEngine()
_initial_engine.load_state()
SCHEMA[_TAB_INDEX["Favorites"]] = _build_favorites_items(_initial_engine)

# Preserve legacy palette names in keys so existing saved profiles still resolve.
_PALETTE_GROUPS = [
    ("Vibrant", "Classic Seeds"),
    ("Neon", "Electric Seeds"),
    ("Deep", "Dark Seeds"),
    ("Pastel", "Soft Seeds"),
    ("Mono", "Neutral Seeds"),
]

for pal_name, group_title in _PALETTE_GROUPS:
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
                    "Uses the current Theme scheme, mode, and contrast to generate a palette. The swatch is the input seed, not the resulting accent. For baked vivid colors, use Vivid."
                ),
            )
        )

    SCHEMA[_TAB_INDEX["Seeds"]].extend(tab_items)

# --- CURATED GENERATION RECIPES ---
SCHEMA[_TAB_INDEX["Curated"]] = _build_exact_items(_initial_engine)
del _initial_engine

# --- CUSTOM INPUT ---
SCHEMA[_TAB_INDEX["Custom"]] = [
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

# --- THEME SETTINGS ---
SCHEMA[_TAB_INDEX["Theme"]] = [
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
            "Applies and saves theme settings. If Vivid is active, generates a new palette from its accent and leaves the baked Vivid pack."
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
            "- `scheme-fidelity`: Builds tones influenced by the source color; output is not an exact HEX match.\n"
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

# --- ANIMATION SETTINGS ---
SCHEMA[_TAB_INDEX["Anim"]] = [
    ConfigItem(
        label="Save Animation Settings",
        key="action_apply_anim",
        scope="DEFAULT",
        type_="bool",
        default=False,
        options=["trigger:Save Settings"],
        group="Wallpaper Animation",
        extended_help="Saves transition settings for the next wallpaper change without regenerating the active palette.",
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

# --- SAVED SETTINGS PROFILES ---
# Native Dusky TUI Profiles tab.
SCHEMA[_TAB_INDEX["Presets"]] = [
    ConfigItem(
        label="Reset Theme and Animation Settings",
        key="preset_factory_reset",
        scope="DEFAULT",
        type_="preset",
        default=None,
        group="System Defaults",
        confirm_message="Reset theme and animation settings and generate from the default color?",
        preset_payload={"__ALL_DEFAULTS__": True},
        extended_help="Resets theme and animation settings and generates from the default color. Saved favorites, recipes, and Vivid definitions are retained.",
    )
]

# --- VIVID (PRE-BAKED COLOR PACKS) ---
SCHEMA[_TAB_INDEX["Vivid"]] = _build_vivid_items()

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
            print("  python3 tui_color_presets.py --color '#HEX'             # Apply hex color directly")
            print("  python3 tui_color_presets.py --rgb <R> <G> <B>        # Apply RGB color")
            print("  python3 tui_color_presets.py --hsl <H> <S> <L>        # Apply HSL color")
            print("  python3 tui_color_presets.py --exact <ID>             # Apply curated generation recipe")
            print("  python3 tui_color_presets.py --preview '#HEX' [SCHEME] [MODE] [CONTRAST]  # In-memory Material You preview")
            print("  python3 tui_color_presets.py --list-exact              # List curated generation recipes")
            print("  python3 tui_color_presets.py --add-exact <HEX> <SCHEME> [LABEL] # Add exact preset")
            print("  python3 tui_color_presets.py --del-exact <ID>         # Delete exact preset")
            print("  python3 tui_color_presets.py --favorite-active        # Favorite active Vivid pack or seed")
            print("  python3 tui_color_presets.py --add-vivid-fav <ID>     # Favorite a complete Vivid pack")
            print("  python3 tui_color_presets.py --del-vivid-fav <ID>     # Remove a Vivid favorite")
            print("  python3 tui_color_presets.py --list-fav                # List saved favorites")
            print("  python3 tui_color_presets.py --add-fav <HEX> [LABEL]  # Add favorite color")
            print("  python3 tui_color_presets.py --del-fav <HEX>          # Delete favorite color")
            print("  python3 tui_color_presets.py --apply-settings          # Apply cached theme/anim settings")
            print("  python3 tui_color_presets.py --vivid <ID>             # Apply pre-baked vivid preset")
            print("  python3 tui_color_presets.py --list-vivid             # List pre-baked vivid presets")
            print("  python3 tui_color_presets.py --resume-wallpaper       # Return to live wallpaper theme")
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
            print("Available Curated Generation Recipes (legacy --exact IDs):")
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
            print(f"Saved Favorites ({len(engine.favorites) + len(engine.vivid_favorites)}):")
            for preset in engine.vivid_favorites:
                print(f"  [Vivid] {preset['label']} ({preset['id']})")
            for lbl, hx in engine.favorites:
                print(f"  {lbl:<25} {hx}")
            return 0

        elif arg == "--favorite-active":
            ok, msg = engine.add_active_favorite()
            print(f"[OK] {msg}" if ok else f"[-] {msg}", file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1

        elif arg in ("--add-vivid-fav", "--del-vivid-fav"):
            if len(sys.argv) != 3:
                print(f"[-] Usage: {arg} <VIVID_ID>", file=sys.stderr)
                return 1
            action = engine.add_vivid_favorite if arg == "--add-vivid-fav" else engine.remove_vivid_favorite
            ok, msg = action(sys.argv[2])
            print(f"[OK] {msg}" if ok else f"[-] {msg}", file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1

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

        elif arg == "--vivid":
            if len(sys.argv) < 3:
                print("[-] Error: Missing preset ID for --vivid", file=sys.stderr)
                return 1
            target_id = sys.argv[2]
            ok, msg, _ = engine.apply_vivid(target_id)
            print(f"[OK] {msg}" if ok else f"[-] {msg}", file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1

        elif arg == "--list-vivid":
            presets = engine.list_vivid_presets()
            print(f"Available Pre-Baked Vivid Presets ({len(presets)}):")
            for p in presets:
                print(f"  {p['id']:<18} {p['hex']:<9} (bg: {p.get('bg_hex', ''):<9}) - {p['label']}")
            return 0

        elif arg == "--resume-wallpaper":
            ok, msg, _ = engine.resume_wallpaper()
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
