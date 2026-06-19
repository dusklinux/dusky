#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: DYNAMIC MAKO GLANCE COMPONENT SCHEMA
===============================================================================
This schema utilizes programmatic generation to map 17 distinct hardware
modules to their respective UI folders without bloat. Granular control is 
retained for every single parameter dynamically.
===============================================================================
"""

from python.frontend.core_types import ConfigItem

# =============================================================================
# 1. CORE APPLICATION ROUTING
# =============================================================================
ENGINE_TYPE = "ini"                        
TARGET_FILE = "~/.config/matugen/templates/mako.ini"      
APP_TITLE = "Dusky Glance Config"                 

# =============================================================================
# 2. UI & ENVIRONMENT BEHAVIOR
# =============================================================================
DEFAULT_MODE = "auto"                      
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json" 

ENABLE_USER_PRESETS = True                 
USER_PRESETS_TAB = "Profiles"              

GLOBAL_POPUP = {
    "title": "Color Application Notice",
    "message": "To apply color changes, you must regenerate them by changing your wallpaper or using 'Regenerate' in the Profiles tab.",
    "level": "info",           
    "require_confirm": False,  
    "cancel_quits": False      
}

# =============================================================================
# 3. GLOBAL COLOR PALETTES (FULLY RESTORED)
# =============================================================================
COLOR_OPTIONS = [
    # --- Matugen Material Design Variables ---
    "{{colors.primary.default.hex}}", "{{colors.on_primary.default.hex}}",
    "{{colors.primary_container.default.hex}}", "{{colors.on_primary_container.default.hex}}",
    "{{colors.secondary.default.hex}}", "{{colors.on_secondary.default.hex}}",
    "{{colors.secondary_container.default.hex}}", "{{colors.on_secondary_container.default.hex}}",
    "{{colors.tertiary.default.hex}}", "{{colors.on_tertiary.default.hex}}",
    "{{colors.tertiary_container.default.hex}}", "{{colors.on_tertiary_container.default.hex}}",
    "{{colors.surface.default.hex}}", "{{colors.on_surface.default.hex}}",
    "{{colors.surface_variant.default.hex}}", "{{colors.on_surface_variant.default.hex}}",
    "{{colors.outline.default.hex}}", "{{colors.outline_variant.default.hex}}",
    "{{colors.error.default.hex}}", "{{colors.on_error.default.hex}}",
    "{{colors.error_container.default.hex}}", "{{colors.on_error_container.default.hex}}",
    
    # --- Hardcoded Palette (Standard & Vibrant) ---
    "#ff0000", "#00ff00", "#0000ff", "#ffffff", "#000000", "#00000000",
    "#ffd700", "#39ff14", "#ff00ff", "#00ffff", "#ffa500", "#800080",
    "#ffc0cb", "#a52a2a", "#808080", "#c0c0c0", 
    
    # --- Hardcoded Palette (Pastel & Atmospheric) ---
    "#1e1e2e", "#f5e0dc", "#f38ba8", "#a6e3a1", 
    "#89b4fa", "#f9e2af", "#cba6f7", "#94e2d5"
]

COLOR_HINTS = [
    # --- Matugen Hints ---
    "Primary", "On Primary", "Primary Container", "On Primary Cont",
    "Secondary", "On Secondary", "Secondary Container", "On Sec Cont",
    "Tertiary", "On Tertiary", "Tertiary Container", "On Ter Cont",
    "Surface", "On Surface", "Surface Variant", "On Surf Var",
    "Outline", "Outline Variant",
    "Error", "On Error", "Error Container", "On Err Cont",
    
    # --- Hardcoded Standard Hints ---
    "Red", "Green", "Blue", "White", "Black", "Transparent",
    "Gold", "Neon Green", "Magenta", "Cyan", "Orange", "Purple",
    "Pink", "Brown", "Gray", "Silver", 
    
    # --- Hardcoded Atmospheric Hints ---
    "Catppuccin Base", "Rosewater", "Pastel Red", "Pastel Green", 
    "Pastel Blue", "Pastel Yellow", "Lavender", "Mint"
]

ALPHA_HELP = (
    "\n\n**Alpha Opacity Quick Reference:**\n"
    "`1a` = 10% | `33` = 20% | `4d` = 30% | `66` = 40%\n"
    "`80` = 50% | `99` = 60% | `b3` = 70% | `cc` = 80%\n"
    "`e6` = 90% | `ff` = 100%\n\n"
    "Append these to any hex or Matugen variable (e.g., `{{colors.surface.default.hex}}1a`)."
)

# =============================================================================
# 4. TABS DEFINITION
# =============================================================================
TABS = [
    "Global",
    "Alerts",
    "Time",
    "Hardware",
    "Storage",
    "Status",
    "Profiles"
]

# =============================================================================
# 5. DYNAMIC COMPONENT GENERATORS
# =============================================================================

def build_standard_glance(suffix, label_name, group_name="Modules"):
    """
    Dynamically generates a Hybrid Folder containing all configuration parameters
    mapped exactly to the specified [app-name=dusky-glance-{suffix}] scope.
    """
    app_name = f"dusky-glance-{suffix}" if suffix else "dusky-glance"
    scope = f"app-name={app_name}"
    menu_key = f"menu_{suffix.replace('-', '_')}" if suffix else "menu_global"
    uid = f"{scope}.{menu_key}"
    
    # Surgical variants directly compiled from the active Mako specification sheet
    width_map = {
        "": 170, "clock": 170, "stopwatch": 170, "timer": 170, "pomodoro": 170,
        "cpu": 100, "ram": 120, "ram-temp": 160, "zram": 210, "temp": 110, "battery": 190,
        "disk": 240, "disk-read": 190, "disk-write": 190, "disk-temp": 100,
        "network": 190, "uptime": 170, "workspace": 140
    }
    
    height_map = {
        "cpu": 38
    }
    
    border_size_map = {
        "ram-temp": 0,
        "network": 1
    }

    width_val = width_map.get(suffix, 170)
    height_val = height_map.get(suffix, 40)
    border_size_val = border_size_map.get(suffix, 2)

    return [
        ConfigItem(
            label=label_name,
            key=menu_key,
            scope=scope,
            type_="menu",
            default=None,
            is_parent=True,
            group=group_name,
            extended_help=f"**{label_name} Settings**\n\nGranular geometry and color controls exclusively scoped to the `{app_name}` service block."
        ),
        
        # ----------------- GEOMETRY & SPACING -----------------
        ConfigItem(
            label="Anchor",
            key="anchor",
            scope=scope,
            type_="cycle",
            default="bottom-right",
            options=["top-right", "top-center", "top-left", "bottom-right", "bottom-center", "bottom-left", "center-right", "center-left", "center"],
            parent_ref=uid,
            extended_help="**Dashboard Anchor**\n\nThe exact quadrant of the physical screen where the Glance widget originates. Usually kept at `bottom-right` to stay out of the way of primary workspace tasks."
        ),
        ConfigItem(
            label="Layer",
            key="layer",
            scope=scope,
            type_="cycle",
            default="top",
            options=["background", "bottom", "top", "overlay"],
            parent_ref=uid,
            extended_help="**Window Layering**\n\nArranges the widget at the specified layer relative to normal windows. Using `overlay` will cause notifications to be displayed above fullscreen windows."
        ),
        ConfigItem(
            label="Align",
            key="text-alignment",
            scope=scope,
            type_="cycle",
            default="center",
            options=["left", "center", "right"],
            parent_ref=uid,
            extended_help="**Text Justification**\n\nAligns the text to visually anchor against the screen edge (e.g. `right` if the widget is anchored `bottom-right`)."
        ),
        ConfigItem(
            label="Width",
            key="width",
            scope=scope,
            type_="int",
            default=width_val,
            min_val=100,
            max_val=800,
            step=10,
            parent_ref=uid,
            extended_help="**Total Width**\n\nMaximum horizontal width allocated in pixels for the Glance string payload. Increase this if your custom system metrics scripts start getting truncated."
        ),
        ConfigItem(
            label="Height",
            key="height",
            scope=scope,
            type_="int",
            default=height_val,
            min_val=20,
            max_val=200,
            step=2,
            parent_ref=uid,
            extended_help="**Total Height**\n\nVertical pixel height for the widget. Keep this thin to maintain a floating text-bar illusion."
        ),
        ConfigItem(
            label="Margin",
            key="margin",
            scope=scope,
            type_="string",
            default="0,8,0,0",
            parent_ref=uid,
            extended_help="**Spatiotemporal Margin Offset**\n\nCSS-style margins (Top, Right, Bottom, Left) that push the dashboard away from the edges of the Wayland output screen."
        ),
        ConfigItem(
            label="Padding",
            key="padding",
            scope=scope,
            type_="string",
            default="0",
            parent_ref=uid,
            extended_help="**Internal Guard Padding**\n\nSpace inserted between the active metrics text and the bounding box. Left at `0` for true transparent floating widgets."
        ),
        ConfigItem(
            label="Radius",
            key="border-radius",
            scope=scope,
            type_="int",
            default=18,
            min_val=0,
            max_val=50,
            step=1,
            parent_ref=uid,
            extended_help="**Corner Arc Smoothing**\n\nPixel curvature for the widget's corners. Highly visible if the background color opacity is raised above zero."
        ),
        ConfigItem(
            label="Border Size",
            key="border-size",
            scope=scope,
            type_="int",
            default=border_size_val,
            min_val=0,
            max_val=10,
            step=1,
            parent_ref=uid,
            extended_help="**Stroke Thickness**\n\nDetermines the width of the framing border. Usually disabled (`0`) for the floating text look."
        ),
        ConfigItem(
            label="Border Color",
            key="border-color",
            scope=scope,
            type_="color",
            default="{{colors.on_primary_container.default.hex}}",
            options=COLOR_OPTIONS,
            hints=COLOR_HINTS,
            parent_ref=uid,
            extended_help="**Widget Stroke Color**\n\nThe color of the outer stroke. This relies on `border-size` being greater than 0." + ALPHA_HELP
        ),

        # ----------------- ELEMENTS & BEHAVIOR -----------------
        ConfigItem(
            label="Icons",
            key="icons",
            scope=scope,
            type_="bool",
            default=False,
            parent_ref=uid,
            extended_help="**Icon Toggle**\n\nDetermines if Mako attempts to render external `.svg`/`.png` icons. This is typically OFF to prevent breaking the strict text formatting of the script payload."
        ),
        ConfigItem(
            label="Format",
            key="format",
            scope=scope,
            type_="string",
            default="%b",
            parent_ref=uid,
            extended_help="**Data Interpreter**\n\nDictates exactly how the incoming bash script payload is mapped. `%b` strips out the summary title and only displays the raw metric body."
        ),
        ConfigItem(
            label="Timeout",
            key="default-timeout",
            scope=scope,
            type_="int",
            default=0,
            min_val=0,
            max_val=10000,
            step=100,
            parent_ref=uid,
            extended_help="**Refresh Desync Control**\n\nShould universally remain `0` (Infinite). This delegates complete timeout and refresh control directly to the background bash daemon updating the widget."
        ),
        ConfigItem(
            label="Font",
            key="font",
            scope=scope,
            type_="string",
            default="monospace 10",
            parent_ref=uid,
            extended_help="**Typography & Size**\n\nDefines the font family and size for the Glance widget (e.g., `monospace 10`, `Ubuntu 12`)."
        ),
        ConfigItem(
            label="OnClick",
            key="on-button-left",
            scope=scope,
            type_="cycle",
            default='exec notify-send "Toggle FullScreen Temporarily to hide the Overlay"',
            options=[
                'exec notify-send "Toggle FullScreen Temporarily to hide the Overlay"', 
                'exec bash -c "pkill rofi; uwsm-app -- $HOME/user_scripts/rofi/dusky_glance.sh"'
            ],
            parent_ref=uid,
            extended_help="**Interactive Shell Hook**\n\nThe shell command executed when physically clicking the widget. By default, it spawns the larger Rofi master dashboard interface using UWSM."
        ),
        
        # ----------------- COLORS -----------------
        ConfigItem(
            label="Background",
            key="background-color",
            scope=scope,
            type_="color",
            default="{{colors.on_primary.default.hex}}b3",
            options=COLOR_OPTIONS,
            hints=COLOR_HINTS,
            parent_ref=uid,
            extended_help="**Widget Fill Color**\n\nThe dominant background shade for the widget. Set to fully transparent (`#00000000`) by default for an integrated, frameless HUD aesthetic." + ALPHA_HELP
        ),
        ConfigItem(
            label="Text",
            key="text-color",
            scope=scope,
            type_="color",
            default="{{colors.on_primary_container.default.hex}}",
            options=COLOR_OPTIONS,
            hints=COLOR_HINTS,
            parent_ref=uid,
            extended_help="**Active Metrics Typography**\n\nColor utilized for rendering the live RAM, CPU, and Network metrics." + ALPHA_HELP
        ),
    ]

def build_alert_glance():
    """Builds the special Critical Alert folder with alternate defaults."""
    app_name = "dusky-glance-alert"
    scope = f"app-name={app_name}"
    uid = f"{scope}.menu_alert"
    
    return [
        ConfigItem(
            label="System-Alert",
            key="menu_alert",
            scope=scope,
            type_="menu",
            default=None,
            is_parent=True,
            group="Warnings",
            extended_help="**Critical Alerts**\n\nSettings for timer expirations, breaks, and threat notifications."
        ),
        ConfigItem(
            label="Anchor",
            key="anchor",
            scope=scope,
            type_="cycle",
            default="top-center",
            options=["top-right", "top-center", "top-left", "bottom-right", "bottom-center", "bottom-left", "center-right", "center-left", "center"],
            parent_ref=uid,
            extended_help="**Critical Alert Anchor**\n\nThe screen quadrant where serious hardware events (e.g., Critical Battery, Unsafe Ejection) will drop from. `top-center` grabs maximum user attention."
        ),
        ConfigItem(
            label="Align",
            key="text-alignment",
            scope=scope,
            type_="cycle",
            default="center",
            options=["left", "center", "right"],
            parent_ref=uid,
            extended_help="**Warning Text Justification**\n\nCenters the alert text dead-middle for maximum readability."
        ),
        ConfigItem(
            label="Width",
            key="width",
            scope=scope,
            type_="int",
            default=300,
            min_val=100,
            max_val=800,
            step=10,
            parent_ref=uid,
            extended_help="**Warning Box Width**\n\nThe horizontal span allocated for rendering the alert string."
        ),
        ConfigItem(
            label="Height",
            key="height",
            scope=scope,
            type_="int",
            default=80,
            min_val=20,
            max_val=200,
            step=4,
            parent_ref=uid,
            extended_help="**Warning Box Height**\n\nThe vertical thickness for the alert box. Slightly larger to accommodate a visible warning icon."
        ),
        ConfigItem(
            label="Margin",
            key="margin",
            scope=scope,
            type_="string",
            default="20,0,0,0",
            parent_ref=uid,
            extended_help="**Alert Screen Offset**\n\nPushes the alert frame away from the absolute edge of the screen so it floats independently."
        ),
        ConfigItem(
            label="Padding",
            key="padding",
            scope=scope,
            type_="string",
            default="10",
            parent_ref=uid,
            extended_help="**Alert Internal Buffer**\n\nSpacing separating the text payload from the warning borders."
        ),
        ConfigItem(
            label="Radius",
            key="border-radius",
            scope=scope,
            type_="int",
            default=12,
            min_val=0,
            max_val=50,
            step=1,
            parent_ref=uid,
            extended_help="**Alert Softening Arc**\n\nApplies curvature to the harsh warning box corners."
        ),
        ConfigItem(
            label="Border Size",
            key="border-size",
            scope=scope,
            type_="int",
            default=1,
            min_val=0,
            max_val=10,
            step=1,
            parent_ref=uid,
            extended_help="**Alert Structural Framing**\n\nPixel thickness of the bounding stroke around the warning popup."
        ),
        ConfigItem(
            label="Border Color",
            key="border-color",
            scope=scope,
            type_="color",
            default="{{colors.secondary.default.hex}}",
            options=COLOR_OPTIONS,
            hints=COLOR_HINTS,
            parent_ref=uid,
            extended_help="**Warning Stroke Color**\n\nColor forming the outline barrier of the popup." + ALPHA_HELP
        ),

        ConfigItem(
            label="Font",
            key="font",
            scope=scope,
            type_="string",
            default="monospace 10",
            parent_ref=uid,
            extended_help="**Typography & Size**\n\nDefines the font family and size for the System Alert widget (e.g., `monospace 12`)."
        ),
        ConfigItem(
            label="Icons",
            key="icons",
            scope=scope,
            type_="bool",
            default=True,
            parent_ref=uid,
            extended_help="**Enable Warning Emblems**\n\nAllows the system to attach `.svg` icons (like a red battery symbol or disconnected cable) to visually augment the threat level."
        ),
        ConfigItem(
            label="IgnoreTime",
            key="ignore-timeout",
            scope=scope,
            type_="bool",
            default=True,
            parent_ref=uid,
            extended_help="**Acknowledge Lockout**\n\nCRITICAL setting. Forces Mako to hold the alert on the screen indefinitely until a physical interaction (click) occurs, ensuring the user *cannot* miss the hardware warning."
        ),
        ConfigItem(
            label="OnClick",
            key="on-button-left",
            scope=scope,
            type_="string",
            default="dismiss",
            parent_ref=uid,
            extended_help="**Acknowledgment Action**\n\nThe operation mapped to clicking the warning popup. Defaults to `dismiss` to acknowledge the alert and clear the screen real estate."
        ),
        
        ConfigItem(
            label="Background",
            key="background-color",
            scope=scope,
            type_="color",
            default="{{colors.secondary_container.default.hex}}ee",
            options=COLOR_OPTIONS,
            hints=COLOR_HINTS,
            parent_ref=uid,
            extended_help="**Warning Fill Color**\n\nThe overarching container color for critical system prompts. Set to a highly visible, slightly translucent shade." + ALPHA_HELP
        ),
        ConfigItem(
            label="Text",
            key="text-color",
            scope=scope,
            type_="color",
            default="{{colors.on_secondary_container.default.hex}}",
            options=COLOR_OPTIONS,
            hints=COLOR_HINTS,
            parent_ref=uid,
            extended_help="**Warning Typography**\n\nHigh-contrast color utilized for the critical alert text." + ALPHA_HELP
        ),
    ]

# =============================================================================
# 6. SCHEMA ASSEMBLY 
# =============================================================================
SCHEMA = {
    # --- TAB 0: Global Fallback ---
    0: build_standard_glance("", "Fallback", "Global"),

    # --- TAB 1: System Alerts ---
    1: build_alert_glance(),

    # --- TAB 2: Time & Focus ---
    2: build_standard_glance("clock", "Clock", "Time") +
       build_standard_glance("stopwatch", "Stopwatch", "Time") +
       build_standard_glance("timer", "Timer", "Time") +
       build_standard_glance("pomodoro", "Pomodoro", "Time"),

    # --- TAB 3: Core Hardware ---
    3: build_standard_glance("cpu", "CPU", "Hardware") +
       build_standard_glance("ram", "RAM", "Hardware") +
       build_standard_glance("ram-temp", "RAM-Temp", "Hardware") +
       build_standard_glance("zram", "ZRAM", "Hardware") +
       build_standard_glance("temp", "Temperature", "Hardware") +
       build_standard_glance("battery", "Battery", "Hardware"),

    # --- TAB 4: Storage Metrics ---
    4: build_standard_glance("disk", "Disk-Space", "Storage") +
       build_standard_glance("disk-read", "Disk-Read", "Storage") +
       build_standard_glance("disk-write", "Disk-Write", "Storage") +
       build_standard_glance("disk-temp", "Disk-Temp", "Storage"),

    # --- TAB 5: Peripheral Status ---
    5: build_standard_glance("network", "Network", "Status") +
       build_standard_glance("uptime", "Uptime", "Status") +
       build_standard_glance("workspace", "Workspace", "Status"),

    # --- TAB 6: Profiles & Execution Hooks ---
    6: [
        ConfigItem(
            label="Regenerate",
            key="action_reload_mako", 
            scope="DEFAULT",          
            type_="action",
            default="bash -c '~/user_scripts/theme_matugen/theme_ctl.sh refresh && makoctl reload'",
            group="Execution",
            extended_help="**Live Daemon Cycle**\n\nExecutes `theme_ctl.sh refresh` to re-compile all specific Matugen templates safely, then immediately invokes `makoctl reload` to push your new Glance parameters to the live Wayland surface without restarting Hyprland."
        ),
        ConfigItem(
            label="Reset",
            key="preset_factory_reset",
            scope="DEFAULT",          
            type_="preset",
            default=None,
            group="Defaults",
            confirm_message="Are you absolutely sure you want to perform a factory reset? All granular adjustments will be wiped.",
            preset_payload={
                "__ALL_DEFAULTS__": True
            },
            extended_help="**Sanity Reset**\n\nDid you break the Rofi shell execution hook or mess up the geometry? Triggering this profile restores every widget/alert parameter identically to the original Dusky default specifications."
        ),
    ]
}
