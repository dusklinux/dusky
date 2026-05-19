#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: MONITORS & RENDER CONFIGURATION SCHEMA
===============================================================================
"""

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "monitor"  # CRITICAL FIX: Actively route to the bridged engine
TARGET_FILE = "~/.config/hypr/edit_here/source/monitors.lua"
APP_TITLE = "Monitors & Render Settings"

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"

# =============================================================================
# 3. TABS DEFINITION
# =============================================================================
TABS = [
    "Power",
    "Color",
    "Profiles"
]

# =============================================================================
# 4. SCHEMA DEFINITION
# =============================================================================
SCHEMA = {
    # -------------------------------------------------------------------------
    # TAB 0: POWER & PERFORMANCE
    # -------------------------------------------------------------------------
    0: [
        ConfigItem(
            label="Variable Refresh Rate (VRR)",
            key="vrr",
            scope="misc",
            type_="int",
            default=0,
            options=[0, 1, 2],
            group="Display Performance",
            extended_help="**Variable Refresh Rate (VRR)**\n\nSets the global VRR behavior for all monitors:\n* `0` = Disabled\n* `1` = Always Enabled (Can cause brightness flicker on some displays)\n* `2` = Fullscreen apps only (Recommended for most desktops)"
        ),
        ConfigItem(
            label="Variable Frame Rate (VFR)",
            key="vfr",
            scope="debug",
            type_="bool",
            default=True,
            group="Display Performance",
            extended_help="**Variable Frame Rate (VFR)**\n\nWhen true, Hyprland stops sending frames to the GPU while nothing is changing on screen. Saves ~1 W on a laptop and looks identical. Disable only if you notice input latency regressions."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 1: COLOR PIPELINE & HDR
    # -------------------------------------------------------------------------
    1: [
        ConfigItem(
            label="Global SDR EOTF",
            key="cm_sdr_eotf",
            scope="render",
            type_="cycle",
            default="auto",
            options=["auto", "srgb", "gamma22"],
            group="Global Render Settings",
            extended_help="**Global SDR EOTF**\n\nThe transfer function applied to SDR/sRGB content.\n* `auto` = Hyprland decides (Recommended)\n* `srgb` = Piecewise sRGB curve (Best color accuracy on most panels)\n* `gamma22` = Traditional Gamma 2.2"
        ),
        ConfigItem(
            label="Fullscreen HDR Passthrough",
            key="cm_fs_passthrough",
            scope="render",
            type_="bool",
            default=False,
            group="Global Render Settings",
            extended_help="**Fullscreen HDR Passthrough**\n\nWhen true, fullscreen applications that output HDR signals bypass Hyprland's colour pipeline entirely for zero-overhead HDR gaming."
        ),
        ConfigItem(
            label="Automatic HDR Promotor",
            key="cm_auto_hdr",
            scope="render",
            type_="bool",
            default=False,
            group="Global Render Settings",
            extended_help="**Automatic HDR**\n\nExperimental feature: Automatically promotes SDR content to HDR where possible. Requires an HDR capable display and `--target-colorspace-hint-mode=source` in supported media players."
        ),
    ],

    # -------------------------------------------------------------------------
    # TAB 2: PROFILES & PRESETS
    # -------------------------------------------------------------------------
    2: [
        ConfigItem(
            label="Apply 'Gaming' Profile",
            key="preset_gaming",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Quick Profiles",
            preset_payload={
                "misc.vrr": 2,
                "debug.vfr": True,
                "render.cm_fs_passthrough": True,
                "render.cm_auto_hdr": False
            }
        ),
        ConfigItem(
            label="Apply 'Color Accurate SDR' Profile",
            key="preset_color_sdr",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="Quick Profiles",
            preset_payload={
                "misc.vrr": 0,
                "render.cm_sdr_eotf": "srgb",
                "render.cm_fs_passthrough": False,
                "render.cm_auto_hdr": False
            }
        ),
        ConfigItem(
            label="Factory Reset Defaults",
            key="preset_factory_reset",
            scope="DEFAULT",
            type_="preset",
            default=None,
            group="System Maintenance",
            preset_payload={
                "__ALL_DEFAULTS__": True
            }
        ),
    ]
}
