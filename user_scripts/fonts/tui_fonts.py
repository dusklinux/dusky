#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: FONTCONFIG SCHEMA
===============================================================================
Target: ~/.config/fontconfig/conf.d/99-dusky-fonts.conf
Engine: Fontconfig XML Serializer
===============================================================================
Note: Picker options are discovered dynamically at import time via `fc-list`
so the schema always reflects the fonts actually installed on the machine
(fontconfig tooling is required).
===============================================================================
"""

import sys
import os
import shlex
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

# Inject Dusky TUI root into Python path for standalone execution
_USER_SCRIPTS = Path(os.environ.get("USER_SCRIPTS", str(Path(__file__).resolve().parents[1]))).expanduser().resolve()
_DUSKY_TUI_ROOT = _USER_SCRIPTS / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem
from python.engines.fontconfig import FontconfigEngine

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "fontconfig"
_CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", ""))
if not _CONFIG_HOME.is_absolute():
    _CONFIG_HOME = Path.home() / ".config"
TARGET_FILE = str(_CONFIG_HOME / "fontconfig/conf.d/99-dusky-fonts.conf")
APP_TITLE = "Dusky Fonts"

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================

THEME_FILE = str(_CONFIG_HOME / "matugen/generated/dusky_tui.json")
DEFAULT_MODE = "auto"
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Profiles"
REQUIRE_ROOT = False

# -------------------------------------------------------------------------
# 2b. TAB NOTICES (banners shown above each tab)
# -------------------------------------------------------------------------
TAB_NOTICES = {
    0: {"level": "info", "message": "New font files dropped into the archive dir appear here after a TUI restart. Applying changes syncs GTK/Qt/dconf; changing the archive directory also updates the cache. Restart apps to pick up new settings."},
    2: {"level": "info", "message": "Applies sync GTK + Qt (qt5ct/qt6ct) fonts. The manual actions below are only for re-running after manual edits or outside-TUI changes."},
}

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = [
    "Fonts",
    "Rendering",
    "Cache & Tools",
    "Profiles"
]

# =============================================================================
# 4. INSTALLED-FAMILY DISCOVERY (runtime, not hardcoded)
# -----------------------------------------------------------------------------
# Families are discovered from fontconfig and the configured archive.
_FAMILY_HINTS = frozendict({
    "Atkinson Hyperlegible": "High legibility",
    "Liberation Sans": "Metric-compatible Arial",
    "Adwaita Sans": "GNOME default",
    "FreeSans": "GNU free core",
    "Liberation Serif": "Metric-compatible Times New Roman",
    "FreeSerif": "GNU free core serif",
    "JetBrainsMono Nerd Font Mono": "JetBrains IDE default, icon-patched",
    "AtkynsonMono Nerd Font Mono": "Icon-patched mono variant",
    "JetBrainsMono Nerd Font": "JetBrains default (proportional-icons)",
    "FreeMono": "GNU free core mono",
    "Liberation Mono": "Metric-compatible Courier New",
    "Adwaita Mono": "GNOME default mono",
    "Symbols Nerd Font Mono": "Nerd icon symbols (mono metrics)",
    "Noto Color Emoji": "Google's standard emoji",
    "Symbols Nerd Font": "Nerd Font icon symbols (regular metrics)",
    "Font Awesome 7 Free": "Font Awesome icon set",
    "Font Awesome 7 Brands": "Font Awesome brand icons",
})

# =============================================================================
# # 4b. FONT ARCHIVE DIRECTORIES
# -----------------------------------------------------------------------------
# Fontconfig does NOT auto-scan arbitrary folders: only the well-known ones
# (~/.local/share/fonts, system paths) are indexed unless a <dir> element
# lists them in a config file. The font archive picked here gets:
#   * a <dir> entry written by the engine into 99-dusky-fonts.conf so
#     fontconfig + fc-cache index it natively, and
#   * an fc-scan based scan at schema import time (fc-list only sees dirs the
#     config already knows about; fc-scan works on raw font files) so the
#     Typeface pickers list archive families even on the very first run,
#     before the engine has written any config.
# =============================================================================

FONT_ARCHIVE_DIR = str(_USER_SCRIPTS / "fonts/archive")


def _scan_families() -> dict[str, str]:
    """Discover installed families and their fontconfig spacing metadata.

    Returns {family: spacing} where spacing is the fontconfig spacing
    property ("100"=monospace, "90"=dual-width, "110"=character-cell, "" otherwise, ASCII to keep
    fc-scan/fc-list calls uniform). Queries fc-list for installed fonts and
    fc-scan for raw files under the archive dir (fc-list only sees dirs the
    config already knows about).
    """
    meta: dict[str, str] = {}

    def ingest(stdout: str) -> None:
        for line in stdout.splitlines():
            family, _, spacing = line.strip().partition("\t")
            if family:
                value = spacing.strip()
                if family not in meta or value in ("90", "100", "110"):
                    meta[family] = value

    try:
        proc = subprocess.run(
            ["fc-list", "--format=%{family[0]}\t%{spacing}\n", ":"],
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        proc.check_returncode()
        if proc.stderr.strip():
            raise RuntimeError(proc.stderr.strip())
        ingest(proc.stdout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Cannot discover installed fonts: {exc}") from exc

    roots = [Path(FONT_ARCHIVE_DIR).expanduser()]
    config = Path(TARGET_FILE)
    if config.is_file() and config.stat().st_size:
        roots = [directory for element in ET.parse(config).getroot().findall("dir")
                 if (directory := FontconfigEngine.resolve_font_dir(element, config)) is not None] or roots
    for root in dict.fromkeys(roots):
        if not root.is_dir():
            continue
        try:
            proc = subprocess.run(
                ["fc-scan", "--format=%{family[0]}\t%{spacing}\n", str(root)],
                capture_output=True, text=True, encoding="utf-8", timeout=30,
            )
            # Exit 1 means no fonts found, including an empty archive.
            if proc.returncode not in (0, 1):
                proc.check_returncode()
            if proc.stderr.strip():
                raise RuntimeError(proc.stderr.strip())
            ingest(proc.stdout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Cannot scan font archive {root}: {exc}") from exc

    return meta


def _classify_families(meta: dict[str, str]) -> dict[str, list[str]]:
    """Classify families into picker buckets using metadata we can trust.

    Order matters:
      * emoji/symbol-ish names come first: icons are excluded and must never leak into
        mono even when their spacing says monospace (e.g. "Symbols Nerd
        Font Mono").
      * monospace is decided by the fontconfig spacing property, the same
        signal fontconfig itself uses for the monospace generic family;
        name guessing would mislabel proportional "Propo" variants as mono.
      * serif has no authoritative metadata exposed by this fontconfig
        (no PANOSE, no class property), so only families that state their
        genre in the name are classified as serif; ambiguous names
        (OpenDyslexic, Baskerville, ...) land in sans rather than guess.
      * everything else defaults to sans.
    """
    buckets: dict[str, list[str]] = {"sans": [], "serif": [], "mono": [], "emoji": []}
    for fam in sorted(meta):
        low = fam.lower()
        if "moji" in low:
            buckets["emoji"].append(fam)
        elif re.search(r"\b(symbols?|icons?|awesome)\b", low):
            continue
        elif meta[fam] in ("100", "90", "110"):
            buckets["mono"].append(fam)
        elif "sans" not in low and any(s in low for s in ("serif", "times", "georgia")):
            buckets["serif"].append(fam)
        else:
            buckets["sans"].append(fam)
    return buckets


def _scan_installed_families() -> dict[str, list[str]]:
    """Discover installed families on the live system and bucket them.

    Returns {bucket: [family,...]}; requires working fontconfig tools.
    """
    return _classify_families(_scan_families())


_INSTALLED = _scan_installed_families()

# Options + positionally-aligned hint lists per picker.
def _picker_options(bucket_key: str, default: str) -> tuple[list[str], list[str]]:
    fams = list(_INSTALLED[bucket_key])
    if bucket_key in ("sans", "serif"):
        # Names cannot reliably identify serif genres. Keep every text family
        # selectable, with the suggested genre first.
        fams += sorted({*_INSTALLED[key] for key in ("sans", "serif", "mono")} - set(fams))
    available = set(fams)
    if default not in fams:
        fams.insert(0, default)
    return fams, [(_FAMILY_HINTS.get(f, "Available family") if f in available else "Missing: install this font before applying") for f in fams]


_SANS_OPTIONS, _SANS_HINTS = _picker_options("sans", "Atkinson Hyperlegible")
_SERIF_OPTIONS, _SERIF_HINTS = _picker_options("serif", "Liberation Serif")
_MONO_OPTIONS, _MONO_HINTS = _picker_options("mono", "JetBrainsMono Nerd Font Mono")
_EMOJI_OPTIONS, _EMOJI_HINTS = _picker_options("emoji", "Noto Color Emoji")

# =============================================================================
# 4. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: FONTS
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="System Sans-Serif Font",
            key="sans-serif",
            scope="DEFAULT",
            type_="picker",
            default="Atkinson Hyperlegible",
            options=_SANS_OPTIONS,
            hints=_SANS_HINTS,
            extended_help="Sets the primary Sans-Serif font used across the desktop environment (GTK apps and applications requesting sans-serif; explicit application fonts remain independent). Options are scanned from fontconfig and the configured archive, so available families appear; missing defaults are labeled."
        ),
        ConfigItem(
            label="System Serif Font",
            key="serif",
            scope="DEFAULT",
            type_="picker",
            default="Liberation Serif",
            options=_SERIF_OPTIONS,
            hints=_SERIF_HINTS,
            extended_help="Sets the primary Serif font. Options are scanned from fontconfig and the configured archive, so available families appear; missing defaults are labeled."
        ),
        ConfigItem(
            label="System Monospace Font",
            key="monospace",
            scope="DEFAULT",
            type_="picker",
            default="JetBrainsMono Nerd Font Mono",
            options=_MONO_OPTIONS,
            hints=_MONO_HINTS,
            extended_help="Sets the primary fixed-width font. Terminal/editor fonts: options are scanned live from fc-list; patched Nerd Fonts are required for icon rendering in terminals."
        ),
        ConfigItem(
            label="System Emoji Font",
            key="emoji",
            scope="DEFAULT",
            type_="picker",
            default="Noto Color Emoji",
            options=_EMOJI_OPTIONS,
            hints=_EMOJI_HINTS,
            extended_help="Sets the emoji generic family and named emoji aliases; glyph coverage and app-specific fallback still govern rendering. Options are scanned from fontconfig and the configured archive; available families appear; missing defaults are labeled."
        ),
        ConfigItem(
            label="Font Archive Directory",
            key="font_dir",
            scope="DEFAULT",
            type_="string",
            default=FONT_ARCHIVE_DIR,
            extended_help="Directory containing downloadable fonts (TTF/OTF/TTC/OTF variable weight, formats supported by the installed FreeType build). Drop new font files here, then use the Cache & Tools 'Force Verbose Cache Rebuild' action. The engine writes a <dir> entry into 99-dusky-fonts.conf so fontconfig indexes it natively, and Typeface pickers auto-scan it for families."
        ),
    ],
    
    # -------------------------------------------------------------------------
    # TAB 1: RENDERING
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Enable Antialiasing",
            key="antialias",
            scope="DEFAULT",
            type_="bool",
            default=True,
            extended_help="Smooths the jagged edges of fonts. Highly recommended for modern high-DPI and standard displays."
        ),
        ConfigItem(
            label="Enable Font Hinting",
            key="hinting",
            scope="DEFAULT",
            type_="bool",
            default=True,
            extended_help="Master switch for font hinting. When enabled, FreeType aligns font glyphs to pixel boundaries for sharp text rendering."
        ),
        ConfigItem(
            label="Enable Auto-Hinting",
            key="autohint",
            scope="DEFAULT",
            type_="bool",
            default=False,
            extended_help="Forces FreeType's automatic hinting algorithm when a font's native hinting instructions are insufficient. Recommended only for fonts with broken/absent native hints; keep off by default."
        ),
        ConfigItem(
            label="Hinting Style",
            key="hintstyle",
            scope="DEFAULT",
            type_="picker",
            default="hintslight",
            options=["hintnone", "hintslight", "hintmedium", "hintfull"],
            hints=[
                "No pixel alignment", "Light alignment (Recommended)", 
                "Medium alignment", "Strict pixel alignment"
            ],
            extended_help="Controls how font outlines are aligned to the screen's pixel grid. 'hintslight' is heavily recommended for FreeType on modern Arch Linux."
        ),
        ConfigItem(
            label="Subpixel Geometry (RGBA)",
            key="rgba",
            scope="DEFAULT",
            type_="picker",
            default="none",
            options=["none", "rgb", "bgr", "vrgb", "vbgr"],
            hints=[
                "Grayscale smoothing", "Standard horizontal (Most common)", 
                "Reversed horizontal", "Standard vertical", "Reversed vertical"
            ],
            extended_help="Configures subpixel rendering for LCD displays. Use grayscale (none) for mixed monitors, unknown pixel layouts, and rotated displays. Select a subpixel layout only when it matches your panel."
        ),
        ConfigItem(
            label="LCD Filter",
            key="lcdfilter",
            scope="DEFAULT",
            type_="picker",
            default="lcddefault",
            options=["lcdnone", "lcddefault", "lcdlight", "lcdlegacy"],
            hints=[
                "No color fringing filter", "Standard filter (Recommended)", 
                "Light fringing filter", "Legacy FreeType filter"
            ],
            extended_help="Reduces color fringing when subpixel rendering is active. 'lcddefault' ensures optimal text clarity."
        ),
        ConfigItem(
            label="Enable Embedded Bitmaps",
            key="embeddedbitmap",
            scope="DEFAULT",
            type_="bool",
            default=True,
            extended_help="Controls embedded bitmap strikes in outline fonts. Color fonts retain their bitmap glyphs even when this is off, so bitmap emoji remain available."
        )
    ],

    # -------------------------------------------------------------------------
    # TAB 2: CACHE & TOOLS
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Force Verbose Cache Rebuild",
            key="trigger_refresh",
            scope="DEFAULT",
            type_="action",
            default="fc-cache --force --verbose",
            options=["trigger"],
            force_interactive=True,
            confirm_message="Are you sure you want to manually rebuild the font cache? This may take several seconds.",
            extended_help="Executes `fc-cache --force --verbose` to force an immediate, verbose rebuild of the system font cache, including newly added font files."
        ),
        ConfigItem(
            label="Verify Sans-Serif Resolution (fc-match)",
            key="trigger_verify_sans",
            scope="DEFAULT",
            type_="action",
            default="fc-match sans-serif",
            options=["trigger"],
            popup_message="Check status bar for the test output.",
            extended_help="Executes `fc-match sans-serif` to verify which exact font file fontconfig resolves for sans-serif requests."
        ),
        ConfigItem(
            label="Verify Monospace Font Resolution (fc-match)",
            key="trigger_verify_mono",
            scope="DEFAULT",
            type_="action",
            default="fc-match monospace",
            options=["trigger"],
            popup_message="Check status bar for the test output.",
            extended_help="Executes `fc-match monospace` to verify which exact font file fontconfig resolves for terminal/code requests."
        ),
        ConfigItem(
            label="Verify Arial Aliasing Fallback",
            key="trigger_verify",
            scope="DEFAULT",
            type_="action",
            default="fc-match 'Arial'",
            options=["trigger"],
            popup_message="Check status bar for the test output.",
            extended_help="Executes a test match to verify which exact font file the system currently falls back to when 'Arial' is requested."
        ),
        ConfigItem(
            label="Sync GTK & Qt Fonts (settings.ini + qt5ct/qt6ct)",
            key="trigger_sync_gtk",
            scope="DEFAULT",
            type_="action",
            default=f"{shlex.quote(sys.executable)} {shlex.quote(str(_DUSKY_TUI_ROOT / 'python/engines/fontconfig.py'))}",
            options=["trigger"],
            popup_message="Synced GTK font-name and Qt general/fixed to the configured families.",
            extended_help="Writes gtk-font-name (family + current size) into ~/.config/gtk-3.0/settings.ini and gtk-4.0/settings.ini, mirrors org.gnome.desktop.interface font-name/document-font-name via gsettings, and rewrites the [Fonts] general/fixed entries in qt5ct.conf and qt6ct.conf (sans-serif -> general, monospace -> fixed), preserving existing sizes/weights. This runs automatically on every apply; use the action to re-sync after manual edits."
        )
    ],
    
    # -------------------------------------------------------------------------
    # TAB 3: PROFILES (Presets)
    # -------------------------------------------------------------------------
    3: [
        ConfigItem(
            label="Apply Modern Sharp UI Profile",
            key="preset_modern_sharp",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="System Defaults",
            preset_payload={
                "sans-serif": "Atkinson Hyperlegible",
                "serif": "Liberation Serif",
                "monospace": "JetBrainsMono Nerd Font Mono",
                "emoji": "Noto Color Emoji",
                "antialias": True,
                "hinting": True,
                "autohint": False,
                "hintstyle": "hintslight",
                "rgba": "rgb",
                "lcdfilter": "lcddefault",
                "embeddedbitmap": True
            },
            extended_help="**Modern Sharp UI**\n\nApplies RGB subpixel rendering and slight hinting. Use this profile only for horizontal RGB panels; use grayscale profiles for unknown or mixed display layouts."
        ),
        ConfigItem(
            label="Apply Accessibility & Legibility Profile",
            key="preset_accessibility",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="System Defaults",
            preset_payload={
                "sans-serif": "Atkinson Hyperlegible",
                "serif": "Liberation Serif",
                "monospace": "JetBrainsMono Nerd Font Mono",
                "emoji": "Noto Color Emoji",
                "antialias": True,
                "hinting": True,
                "autohint": False,
                "hintstyle": "hintslight",
                "rgba": "none",
                "lcdfilter": "lcddefault",
                "embeddedbitmap": True
            },
            extended_help="**Accessibility Focus**\n\nPrioritizes character distinction using Atkinson Hyperlegible (Braille Institute) to prevent visual confusion between similar characters like '1', 'l', and 'I'."
        ),
        ConfigItem(
            label="Apply 4K / High-DPI Clean Profile",
            key="preset_hidpi_clean",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="System Defaults",
            preset_payload={
                "sans-serif": "Liberation Sans",
                "serif": "Liberation Serif",
                "monospace": "JetBrainsMono Nerd Font Mono",
                "emoji": "Noto Color Emoji",
                "antialias": True,
                "hinting": True,
                "autohint": False,
                "hintstyle": "hintnone",
                "rgba": "none",
                "lcdfilter": "lcdnone",
                "embeddedbitmap": True
            },
            extended_help="**High-DPI / 4K Clean Profile**\n\nOptimized for 4K and Retina-class displays. Disables subpixel LCD geometry (`rgba=none`) and pixel grid alignment (`hintstyle=hintnone`) for ultra-clean pure vector outline rendering."
        ),
        ConfigItem(
            label="Apply Full Hinting Profile",
            key="preset_full_hinting",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="System Defaults",
            preset_payload={
                "sans-serif": "Liberation Sans",
                "serif": "Liberation Serif",
                "monospace": "Liberation Mono",
                "emoji": "Noto Color Emoji",
                "antialias": True,
                "hinting": True,
                "autohint": False,
                "hintstyle": "hintfull",
                "rgba": "none",
                "lcdfilter": "lcddefault",
                "embeddedbitmap": True
            },
            extended_help="**Full Hinting Profile**\n\nRestores the classic open-source desktop appearance using metric-compatible Liberation fonts alongside strict/full hinting pixel alignment."
        ),
    ]
}

# =============================================================================
# DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    script_path = Path(__file__).resolve()
    # Route execution to the main Dusky TUI router
    main_router = _DUSKY_TUI_ROOT / "python/main/main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
