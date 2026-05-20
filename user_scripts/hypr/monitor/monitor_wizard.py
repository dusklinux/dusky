#!/usr/bin/env python3
import json
import subprocess
from python.frontend.core_types import ConfigItem

# --- TUI ROUTER CONFIGURATION ---
ENGINE_TYPE = "monitor"
APP_TITLE = "Dusky Monitor Wizard"
DEFAULT_MODE = "batch"  # Monitors should default to batch to prevent Wayland configuration tearing
TARGET_FILE = "~/.config/hypr/edit_here/source/monitors.lua"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json" # <-- This is the surgical fix!
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Presets"

# --- MATHEMATICAL CONSTANTS ---
# Extensively expanded to support 16:10, 21:9, 32:9, and higher resolutions natively
STANDARD_RES = [
    (5120, 2880), (5120, 1440), (3840, 2400), (3840, 2160), (3840, 1600),
    (3440, 1440), (2880, 1800), (2560, 1600), (2560, 1440), (2560, 1080),
    (1920, 1200), (1920, 1080), (1680, 1050), (1600, 900), (1440, 900),
    (1366, 768), (1280, 1024), (1280, 800), (1280, 720), (1024, 768)
]

SCALE_STEPS = [
    0.5, 0.6, 0.75, 0.8, 0.9, 1.0, 1.0625, 1.1, 1.125, 1.15, 1.2, 1.25,
    1.33, 1.4, 1.5, 1.6, 1.67, 1.75, 1.8, 1.88, 2.0, 2.25, 2.4, 2.5,
    2.67, 2.8, 3.0
]

POS_VARIANTS = [
    "auto", "auto-right", "auto-left", "auto-up", "auto-down", 
    "auto-center-right", "auto-center-left", "auto-center-up", "auto-center-down"
]

CM_PROFILES = ["auto", "srgb", "dcip3", "dp3", "adobe", "wide", "edid", "hdr", "hdredid"]
SDR_EOTFS = ["default", "srgb", "gamma22"]


def _calculate_valid_scales(native_w: int, native_h: int) -> list[str]:
    valid_scales = []
    for s in SCALE_STEPS:
        lw, lh = native_w / s, native_h / s
        if lw < 640 or lh < 360: continue
        valid_scales.append(s)
            
    valid = valid_scales if valid_scales else [1.0]
    return ["auto"] + [f"{v:g}" for v in valid]

def generate_schema() -> tuple[list[str], dict[int, list[ConfigItem]]]:
    try:
        proc = subprocess.run(["hyprctl", "-j", "monitors", "all"], capture_output=True, text=True, timeout=5)
        raw = proc.stdout.strip()
        if raw and not raw[0] in ("[", "{"):
            for i, line in enumerate(raw.splitlines()):
                if line.strip().startswith(("[", "{")):
                    raw = "\n".join(raw.splitlines()[i:])
                    break
        monitors = json.loads(raw)
    except Exception:
        monitors = []

    tabs = []
    schema = {}
    
    available_outputs = [m.get("name", "") for m in monitors]
    
    # ---------------------------------------------------------
    # 1. HARDWARE MONITORS
    # ---------------------------------------------------------
    for i, m in enumerate(monitors):
        name = m.get("name", f"Unknown-{i}")
        desc = m.get("description", "")
        tabs.append(name)
        schema[i] = []
        
        native_w = int(m.get("width", 1920))
        native_h = int(m.get("height", 1080))
        scope_str = f"monitor/{name}"
        
        avail_modes_raw = m.get("availableModes", [])
        clean_modes = [mode.replace("Hz", "").replace("hz", "").strip() for mode in avail_modes_raw]
        
        base_refresh_float = float(m.get("refreshRate", 60.0))
        base_refresh = f"{base_refresh_float:.2f}"
        
        fallback_modes = []
        for w, h in STANDARD_RES:
            if w <= native_w and h <= native_h:
                fallback_modes.append(f"{w}x{h}@{base_refresh}")
                if base_refresh != "60.00":
                    fallback_modes.append(f"{w}x{h}@60.00")
                    
        all_modes = ["preferred", "highres", "highrr", "maxwidth"] + clean_modes
        for f_mode in fallback_modes:
            if f_mode not in all_modes:
                all_modes.append(f_mode)

        ident_options = [name]
        ident_hints = ["Raw Port ID"]
        if desc:
            ident_options.append(f"desc:{desc}")
            ident_hints.append("Hardware Safe ID")

        schema[i].extend([
            ConfigItem(
                label="Enable Monitor", key="disabled", scope=scope_str, type_="bool", default=False,
                group="Core Setup", extended_help="Toggles the monitor state. Disabling a monitor literally removes it from the layout, moving all windows and workspaces to remaining ones."
            ),
            ConfigItem(
                label="Target Identifier", key="output", scope=scope_str, type_="picker", default=name,
                options=ident_options, hints=ident_hints, group="Core Setup",
                extended_help="Output name or 'desc:' description prefix. Leaving this empty defines a fallback rule for when no other rules match."
            ),
            ConfigItem(
                label="Resolution & Rate", key="mode", scope=scope_str, type_="string", default="preferred",
                options=all_modes, group="Core Setup",
                extended_help="Select a preset, or manually type a resolution/refresh rate (e.g. '1920x1080@144'). Special values: preferred, highres, highrr, maxwidth. You can also pass a custom 'modeline' string here."
            ),
            ConfigItem(
                label="Display Scale", key="scale", scope=scope_str, type_="picker", default="auto",
                options=_calculate_valid_scales(native_w, native_h), group="Core Setup",
                extended_help="Scale factor. 'auto' lets Hyprland decide based on PPI. Warning: A valid scale must divide your resolution cleanly without decimals to avoid invalid logical pixel errors."
            ),
            ConfigItem(
                label="Position on Canvas", key="position", scope=scope_str, type_="picker", default="auto",
                options=POS_VARIANTS + [f"{m.get('x', 0)}x{m.get('y', 0)}"], group="Layout & Transforms",
                extended_help="Position in pixels (e.g. 1920x0). Hyprland uses an inverse Y cartesian system (negative y is higher). 'auto' auto-places based on the top-left corner. 'auto-center' places based on the monitor center."
            ),
            ConfigItem(
                label="Rotation Transform", key="transform", scope=scope_str, type_="picker", default="0",
                options=["0", "1", "2", "3", "4", "5", "6", "7"],
                hints=["Normal", "90°", "180°", "270°", "Flipped", "Flipped+90°", "Flipped+180°", "Flipped+270°"],
                group="Layout & Transforms", extended_help="Rotates or flips the monitor output."
            ),
            ConfigItem(
                label="Reserved Area", key="reserved_area", scope=scope_str, type_="int", default=0,
                group="Layout & Transforms", extended_help="A custom reserved area (in pixels) unoccupied by tiled windows on all sides. Note: TUI supports integer (all sides) only. To define individual sides (top/bottom/left/right), use the manual 'Edit File' option."
            ),
            ConfigItem(
                label="Mirror Output", key="mirror", scope=scope_str, type_="picker", default="",
                options=[""] + [out for out in available_outputs if out != name],
                hints=["None"] + ["Clone this display"] * (len(available_outputs)-1),
                group="Layout & Transforms",
                extended_help="Mirrors another display. Mirroring will not re-render elements for the second monitor (e.g., 1080p mirrored to 4K is still 1080p). Squishing/stretching will occur on differing aspect ratios (like 16:9 vs 16:10)."
            ),
            ConfigItem(
                label="Variable Refresh Rate", key="vrr", scope=scope_str, type_="cycle", default="0",
                options=["0", "1", "2"], hints=["Off", "On", "Fullscreen Only"], group="Advanced Display",
                extended_help="Configures per-display Variable Refresh Rate (VRR / FreeSync)."
            ),
            ConfigItem(
                label="Bitdepth", key="bitdepth", scope=scope_str, type_="cycle", default="8",
                options=["8", "10"], group="Advanced Display",
                extended_help="Enable 10-bit support. Note: Colors registered in Hyprland (e.g., border color) do not support 10-bit, and some apps do not support 10-bit screen capture."
            ),
            ConfigItem(
                label="Force Wide Color", key="supports_wide_color", scope=scope_str, type_="cycle", default="0",
                options=["-1", "0", "1"], hints=["Off", "Auto", "On"], group="Advanced Display",
                extended_help="Force wide color gamut support. (-1 = off, 0 = auto, 1 = on)"
            ),
            ConfigItem(
                label="Force HDR", key="supports_hdr", scope=scope_str, type_="cycle", default="0",
                options=["-1", "0", "1"], hints=["Off", "Auto", "On"], group="Advanced Display",
                extended_help="Force HDR support. (-1 = off, 0 = auto, 1 = on)"
            ),
            ConfigItem(
                label="ICC Profile Path", key="icc", scope=scope_str, type_="string", default="",
                group="Color Pipeline", extended_help="Absolute path to an ICC profile. Applying an ICC overrides the CM preset, forces sdr_eotf to sRGB, and is fundamentally incompatible with HDR gaming."
            ),
            ConfigItem(
                label="Color Management", key="cm", scope=scope_str, type_="picker", default="auto",
                options=CM_PROFILES, group="Color Pipeline",
                extended_help="'auto' uses sRGB for 8bpc and wide for 10bpc. 'hdr' enables experimental wide color gamut and HDR PQ transfer function."
            ),
            ConfigItem(
                label="SDR Transfer Curve", key="sdr_eotf", scope=scope_str, type_="picker", default="default",
                options=SDR_EOTFS, group="Color Pipeline",
                extended_help="The transfer function assumed to be in use on an SDR display for sRGB content. 'default' follows the global render:cm_sdr_eotf setting."
            ),
            ConfigItem(
                label="HDR: SDR Brightness", key="sdrbrightness", scope=scope_str, type_="float", default=1.0,
                min_val=0.1, max_val=3.0, step=0.1, group="HDR / SDR Mapping",
                extended_help="Controls SDR brightness in HDR mode. Typical brightness values should be in the 1.0 to 2.0 range."
            ),
            ConfigItem(
                label="HDR: SDR Saturation", key="sdrsaturation", scope=scope_str, type_="float", default=1.0,
                min_val=0.1, max_val=2.0, step=0.1, group="HDR / SDR Mapping",
                extended_help="Controls SDR saturation in HDR mode. Default is 1.0."
            ),
            ConfigItem(
                label="SDR Min Luminance", key="sdr_min_luminance", scope=scope_str, type_="float", default=0.2,
                group="Luminance Tuning", extended_help="SDR minimum luminance for SDR to HDR mapping."
            ),
            ConfigItem(
                label="SDR Max Luminance", key="sdr_max_luminance", scope=scope_str, type_="int", default=80,
                group="Luminance Tuning", extended_help="SDR maximum luminance."
            ),
            ConfigItem(
                label="Monitor Min Luminance", key="min_luminance", scope=scope_str, type_="float", default=-1.0,
                group="Luminance Tuning", extended_help="Monitor minimum possible luminance. Default is -1."
            ),
            ConfigItem(
                label="Monitor Max Luminance", key="max_luminance", scope=scope_str, type_="int", default=-1,
                group="Luminance Tuning", extended_help="Monitor maximum possible luminance. Default is -1."
            ),
            ConfigItem(
                label="Max Avg Luminance", key="max_avg_luminance", scope=scope_str, type_="int", default=-1,
                group="Luminance Tuning", extended_help="Monitor maximum average luminance. Default is -1."
            )
        ])

    # ---------------------------------------------------------
    # 2. GLOBAL SYSTEM SETTINGS
    # ---------------------------------------------------------
    tabs.append("Globals")
    g_idx = len(tabs) - 1
    schema[g_idx] = [
        ConfigItem(
            label="Variable Frame Rate (VFR)", key="vfr", scope="debug", type_="bool", default=True,
            group="Power & Performance", extended_help="When true, Hyprland stops sending frames to the GPU while nothing is changing on screen. Saves ~1 W on laptops."
        ),
        ConfigItem(
            label="Global VRR Override", key="vrr", scope="misc", type_="cycle", default="0",
            options=["0", "1", "2"], hints=["Off", "Always On", "Fullscreen Only"], group="Power & Performance",
            extended_help="Globally sets the Variable Refresh Rate behavior across all monitors."
        ),
        ConfigItem(
            label="Global SDR EOTF", key="cm_sdr_eotf", scope="render", type_="picker", default="auto",
            options=["auto", "srgb", "gamma22"], group="Color Pipeline",
            extended_help="Sets the default transfer function assumed for SDR displays. Monitors set to 'default' will inherit this value."
        ),
        ConfigItem(
            label="Auto HDR Promotion", key="cm_auto_hdr", scope="render", type_="bool", default=False,
            group="Color Pipeline", extended_help="If enabled, fullscreen HDR is possible without explicitly setting the monitor 'cm' property to 'hdr'."
        )
    ]
        
    tabs.append(USER_PRESETS_TAB)
    
    if len(tabs) == 2: # Only Globals and Presets exist
        tabs.insert(0, "Fallback")
        schema[0] = [ConfigItem(label="No Monitors Detected", key="none", type_="string", default="", group="Error", extended_help="Hyprland IPC returned no monitors. Verify your socket is accessible.")]
        # Shift indices
        schema = {k+1 if k >= 0 else k: v for k, v in schema.items()}

    return tabs, schema

TABS, SCHEMA = generate_schema()
