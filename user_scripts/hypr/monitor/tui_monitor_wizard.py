#!/usr/bin/env python3
"""
===============================================================================
DUSKY MONITOR WIZARD: HIGH-PRECISION HYPRLAND DISPLAY & WORKSPACE MANAGER
===============================================================================
Dynamic TUI router schema for multi-monitor arrangements, 1/120 fractional scaling,
spatial placement geometry, workspace-to-output distribution, and HDR/SDR pipeline.
Native Wayland/Hyprland fractional scaling, spatial placement geometry, and workspace management.
"""

import json
import subprocess
import sys
from pathlib import Path

_DUSKY_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_ROOT))

from python.frontend.core_types import ConfigItem
from python.engines.monitor_engine import (
    preset_choices,
    grid_scales,
    format_scale,
    calculate_relative_positions,
    calculate_ppi,
    detect_bitdepth,
    is_internal_connector
)

# --- TUI ROUTER CONFIGURATION ---
ENGINE_TYPE = "monitor"
APP_TITLE = "Dusky Monitor & Workspace Wizard"
DEFAULT_MODE = "batch"  # Batch mode prevents Wayland configuration tearing during live updates
TARGET_FILE = "~/.config/hypr/edit_here/source/monitors.lua"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Presets"

# --- STANDARD FALLBACK RESOLUTIONS ---
STANDARD_RES = [
    (5120, 2880), (5120, 1440), (3840, 2400), (3840, 2160), (3840, 1600),
    (3440, 1440), (2880, 1800), (2560, 1600), (2560, 1440), (2560, 1080),
    (1920, 1200), (1920, 1080), (1680, 1050), (1600, 900), (1440, 900),
    (1366, 768), (1280, 1024), (1280, 800), (1280, 720), (1024, 768)
]

POS_COMPOSITOR_VARIANTS = [
    "auto", "auto-right", "auto-left", "auto-up", "auto-down",
    "auto-center-right", "auto-center-left", "auto-center-up", "auto-center-down"
]

CM_PROFILES = ["auto", "srgb", "dcip3", "dp3", "adobe", "wide", "edid", "hdr", "hdredid"]
SDR_EOTFS = ["default", "srgb", "gamma22"]


def _calculate_valid_scales(native_w: int, native_h: int) -> tuple[list[str], list[str]]:
    """
    Generates scale choices strictly adhering to Hyprland's 1/120 quantum grid.
    Includes exact resulting logical dimensions and enforces minimum logical bounds (640x360).
    """
    MIN_LOGICAL_LONG = 640
    MIN_LOGICAL_SHORT = 360
    long_, short = max(native_w, native_h), min(native_w, native_h)

    presets = preset_choices(native_w, native_h)
    all_sharp = grid_scales(native_w, native_h, min_scale=0.5, max_scale=3.5)

    combined = sorted(list(set(presets + all_sharp)))
    # Enforce ergonomic usability boundaries
    combined = [s for s in combined if (long_ / s >= MIN_LOGICAL_LONG and short / s >= MIN_LOGICAL_SHORT)]
    if not combined:
        combined = [1.0]

    options = ["auto"]
    hints = ["Automatic (PPI heuristic)"]

    for s in combined:
        fmt = format_scale(s)
        options.append(fmt)
        lw = int(round(native_w / s))
        lh = int(round(native_h / s))
        res_info = f"{lw}x{lh} logical"
        if s in presets:
            hints.append(f"{res_info} (Recommended)")
        else:
            hints.append(f"{res_info} (Sharp)")

    return options, hints


def generate_schema() -> tuple[list[str], dict[int, list[ConfigItem]]]:
    try:
        proc = subprocess.run(["hyprctl", "-j", "monitors", "all"], capture_output=True, text=True, timeout=3)
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

    available_outputs = [m.get("name", "") for m in monitors if m.get("name")]
    primary_file = Path("~/.config/hypr/primary_monitor").expanduser()
    saved_pri = ""
    if primary_file.exists():
        try:
            saved_pri = primary_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    primary_output = saved_pri if saved_pri in available_outputs else (available_outputs[0] if available_outputs else "eDP-1")
    remaining_outputs = [name for name in available_outputs if name != primary_output]
    secondary_output = remaining_outputs[0] if remaining_outputs else primary_output

    internal_outputs = [name for name in available_outputs if is_internal_connector(name)]
    external_outputs = [name for name in available_outputs if not is_internal_connector(name)]

    # Calculate spatial geometry options (relative positions)
    relative_positions = calculate_relative_positions(monitors)

    # -------------------------------------------------------------------------
    # 1. HARDWARE MONITORS TABS (One per physical output)
    # -------------------------------------------------------------------------
    for i, m in enumerate(monitors):
        name = m.get("name", f"Unknown-{i}")
        desc = m.get("description", "")
        make = m.get("make", "")
        model = m.get("model", "")
        serial = m.get("serial", "")
        tabs.append(name)
        schema[i] = []

        native_w = int(m.get("width", 1920))
        native_h = int(m.get("height", 1080))
        pw_mm = int(m.get("physicalWidth", 0))
        ph_mm = int(m.get("physicalHeight", 0))
        ppi, diag_in = calculate_ppi(native_w, native_h, pw_mm, ph_mm)
        detected_bpc = detect_bitdepth(m.get("currentFormat", ""))
        scope_str = f"monitor/{name}"

        # Modes & refresh rate
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

        # Identifiers
        ident_options = [name]
        ident_hints = ["Raw Port ID"]
        if desc:
            ident_options.append(f"desc:{desc}")
            ident_hints.append("Hardware Safe ID (EDID Description)")

        # Scales (1/120 fractional grid)
        scale_options, scale_hints = _calculate_valid_scales(native_w, native_h)

        # Positions (Dynamic relative placement + standard auto)
        rel_opts = relative_positions.get(name, [])
        pos_options = []
        pos_hints = []
        for val, lbl, hint in rel_opts:
            if val not in pos_options:
                pos_options.append(val)
                pos_hints.append(f"{lbl} — {hint}")

        for auto_var in POS_COMPOSITOR_VARIANTS:
            if auto_var not in pos_options:
                pos_options.append(auto_var)
                pos_hints.append("Compositor Keyword")

        # Hardware facts header for extended help
        hw_info = (
            f"**Hardware Identity**:\n"
            f"- Device: {make} {model} ({name})\n"
            f"- Serial: {serial if serial else 'N/A'}\n"
            f"- Description: {desc}\n"
            f"- Dimensions: {pw_mm}mm × {ph_mm}mm (~{diag_in}\" diag, {ppi} PPI)\n"
            f"- Buffer Format: {m.get('currentFormat', 'Unknown')} ({detected_bpc}-bit)\n"
            f"- Active Mode: {native_w}x{native_h} @ {base_refresh}Hz\n"
        )

        schema[i].extend([
            ConfigItem(
                label="Enable Monitor", key="enabled", scope=scope_str, type_="bool", default=True,
                group="Core Setup",
                extended_help=f"{hw_info}\n**Enable Monitor**:\nToggles the monitor state. Disabling removes it from the canvas and relocates windows to remaining displays."
            ),
            ConfigItem(
                label="Target Identifier", key="output", scope=scope_str, type_="picker", default=name,
                options=ident_options, hints=ident_hints, group="Core Setup",
                extended_help=f"{hw_info}\n**Target Identifier**:\nOutput name ('{name}') or 'desc:' description. Description matching prevents port-swapping issues."
            ),
            ConfigItem(
                label="Resolution & Rate", key="mode", scope=scope_str, type_="string", default="preferred",
                options=all_modes, group="Core Setup",
                extended_help=f"{hw_info}\n**Resolution & Refresh Rate**:\nSelect an advertised hardware mode or a virtual alias ('preferred', 'highres', 'highrr', 'maxwidth')."
            ),
            ConfigItem(
                label="Display Scale", key="scale", scope=scope_str, type_="picker", default="auto",
                options=scale_options, hints=scale_hints, group="Core Setup",
                extended_help=f"{hw_info}\n**Display Scale Factor**:\nFractional scaling on Hyprland's 1/120 grid. Presets prevent compositor yellow warning banners."
            ),
            ConfigItem(
                label="Visual Layout Designer", key="action_visual_layout", scope=scope_str, type_="action",
                default=f"{sys.executable} {Path(__file__).parent / 'monitor_layout.py'} --floating",
                force_interactive=False, group="Layout & Transforms",
                extended_help="Spawns an interactive 2D spatial canvas in a centered floating window to position, snap, and scale displays with live compositor preview."
            ),
            ConfigItem(
                label="Position on Canvas", key="position", scope=scope_str, type_="picker", default="auto",
                options=pos_options, hints=pos_hints, group="Layout & Transforms",
                extended_help=f"{hw_info}\n**Position on Canvas**:\nSelect auto-placement or an exact relative coordinate calculated against peer displays."
            ),
            ConfigItem(
                label="Rotation Transform", key="transform", scope=scope_str, type_="picker", default="0",
                options=["0", "1", "2", "3", "4", "5", "6", "7"],
                hints=[
                    "0° (normal)",
                    "90° (clockwise)",
                    "180° (inverted)",
                    "270° (counter-clockwise)",
                    "0° (flipped)",
                    "90° (flipped + clockwise)",
                    "180° (flipped + inverted)",
                    "270° (flipped + counter-clockwise)"
                ],
                group="Layout & Transforms",
                extended_help="Rotates or flips the monitor output. Logical width and height are automatically swapped on 90°/270°."
            ),
            ConfigItem(
                label="Reserved Padding", key="reserved_area", scope=scope_str, type_="int", default=0,
                group="Layout & Transforms",
                extended_help="Custom reserved margin (in pixels) unoccupied by tiled windows on all edges."
            ),
            ConfigItem(
                label="Mirror Output", key="mirror", scope=scope_str, type_="picker", default="",
                options=[""] + [out for out in available_outputs if out != name],
                hints=["None (Independent Screen)"] + [f"Clone {out}" for out in available_outputs if out != name],
                group="Layout & Transforms",
                extended_help="Mirrors another display pixel-for-pixel."
            ),
            ConfigItem(
                label="VRR", key="vrr", scope=scope_str, type_="cycle", default="0",
                options=["0", "1", "2"], hints=["Off", "Always On", "Fullscreen Only"], group="Advanced Display",
                extended_help="Configures per-display Variable Refresh Rate (VRR / FreeSync / G-Sync)."
            ),
            ConfigItem(
                label="Bitdepth", key="bitdepth", scope=scope_str, type_="cycle", default=str(detected_bpc),
                options=["8", "10", "16"], hints=["8-bit (Standard SDR)", "10-bit (Deep Color)", "16-bit (HDR Float)"],
                group="Advanced Display",
                extended_help="Enables 10-bit or 16-bit color output for supported HDR and wide-gamut monitors."
            ),
            ConfigItem(
                label="Force Wide Color", key="supports_wide_color", scope=scope_str, type_="cycle", default="0",
                options=["-1", "0", "1"], hints=["Force Off", "Auto Detect", "Force On"], group="Advanced Display",
                extended_help="Controls wide color gamut support (-1 = off, 0 = auto, 1 = on)."
            ),
            ConfigItem(
                label="Force HDR", key="supports_hdr", scope=scope_str, type_="cycle", default="0",
                options=["-1", "0", "1"], hints=["Force Off", "Auto Detect", "Force On"], group="Advanced Display",
                extended_help="Forces HDR capability on the display pipeline (-1 = off, 0 = auto, 1 = on)."
            ),
            ConfigItem(
                label="ICC Profile", key="icc", scope=scope_str, type_="string", default="",
                group="Color Pipeline",
                extended_help="Absolute filesystem path to an ICC color profile (.icc or .icm)."
            ),
            ConfigItem(
                label="Color Management", key="cm", scope=scope_str, type_="picker", default="auto",
                options=CM_PROFILES, group="Color Pipeline",
                extended_help="Selects color management preset. 'hdr' activates wide color gamut and PQ transfer."
            ),
            ConfigItem(
                label="SDR Curve (EOTF)", key="sdr_eotf", scope=scope_str, type_="picker", default="default",
                options=SDR_EOTFS, group="Color Pipeline",
                extended_help="Assumed electro-optical transfer function for SDR content."
            ),
            ConfigItem(
                label="HDR SDR Brightness", key="sdrbrightness", scope=scope_str, type_="float", default=1.0,
                min_val=0.1, max_val=3.0, step=0.1, group="HDR / SDR Mapping",
                extended_help="Controls white-point brightness of SDR content in HDR mode. Typically 1.0 to 1.8."
            ),
            ConfigItem(
                label="HDR SDR Saturation", key="sdrsaturation", scope=scope_str, type_="float", default=1.0,
                min_val=0.1, max_val=2.0, step=0.1, group="HDR / SDR Mapping",
                extended_help="Controls SDR color saturation in HDR mode. Default is 1.0."
            ),
            ConfigItem(
                label="SDR Min Luminance", key="sdr_min_luminance", scope=scope_str, type_="float", default=0.2,
                group="Luminance Tuning", extended_help="Minimum luminance (nits) for SDR-to-HDR tone mapping."
            ),
            ConfigItem(
                label="SDR Max Luminance", key="sdr_max_luminance", scope=scope_str, type_="int", default=80,
                group="Luminance Tuning", extended_help="Maximum luminance (nits) for SDR content."
            ),
            ConfigItem(
                label="Min Luminance", key="min_luminance", scope=scope_str, type_="float", default=-1.0,
                group="Luminance Tuning", extended_help="Monitor minimum measurable luminance (-1 = EDID default)."
            ),
            ConfigItem(
                label="Max Luminance", key="max_luminance", scope=scope_str, type_="int", default=-1,
                group="Luminance Tuning", extended_help="Monitor maximum peak luminance (-1 = EDID default)."
            ),
            ConfigItem(
                label="Max Avg Luminance", key="max_avg_luminance", scope=scope_str, type_="int", default=-1,
                group="Luminance Tuning", extended_help="Monitor maximum full-field average luminance (-1 = EDID default)."
            )
        ])

    # -------------------------------------------------------------------------
    # 2. WORKSPACE PLANNING TAB (SECTION 6: hl.workspace_rule)
    # -------------------------------------------------------------------------
    tabs.append("Workspaces")
    ws_idx = len(tabs) - 1
    schema[ws_idx] = []

    # Quick workspace distribution strategy presets
    seq_payload = {}
    for w_i in range(1, 11):
        target_m = primary_output if w_i <= 5 else secondary_output
        seq_payload[f"workspace_rule/{w_i}.monitor"] = target_m
        seq_payload[f"workspace_rule/{w_i}.default"] = (w_i in (1, 6))

    all_primary_payload = {}
    for w_i in range(1, 11):
        all_primary_payload[f"workspace_rule/{w_i}.monitor"] = primary_output
        all_primary_payload[f"workspace_rule/{w_i}.default"] = (w_i == 1)

    interleave_payload = {}
    for w_i in range(1, 11):
        target_m = primary_output if (w_i % 2 == 1) else secondary_output
        interleave_payload[f"workspace_rule/{w_i}.monitor"] = target_m
        interleave_payload[f"workspace_rule/{w_i}.default"] = (w_i in (1, 2))

    strategy_items = []
    if available_outputs:
        strategy_items.append(
            ConfigItem(
                label="Primary Bank Display", key="primary_monitor", scope="DEFAULT",
                type_="picker", default=primary_output, options=available_outputs, group="Banked Workspaces (1–10)",
                extended_help="Selects the primary display assigned to Workspace Bank 0 (workspaces 1–10) in multi_monitor_workspace.sh. Remaining connected displays are sorted left-to-right (workspaces 11–20, 21–30, etc.)."
            )
        )
    if len(available_outputs) > 1:
        strategy_items.append(
            ConfigItem(
                label="Sequential (1-5 / 6-10)", key="plan_seq", scope="DEFAULT",
                type_="preset", default=None, group="Quick Strategies",
                preset_payload=seq_payload,
                extended_help="Distributes workspaces 1-5 on primary, and 6-10 on secondary."
            )
        )
    strategy_items.append(
        ConfigItem(
            label="All on Primary", key="plan_all_pri", scope="DEFAULT",
            type_="preset", default=None, group="Quick Strategies",
            preset_payload=all_primary_payload,
            extended_help="Pins all workspaces (1-10) to the primary display."
        )
    )
    if len(available_outputs) > 1:
        strategy_items.append(
            ConfigItem(
                label="Interleaved (Odd / Even)", key="plan_inter", scope="DEFAULT",
                type_="preset", default=None, group="Quick Strategies",
                preset_payload=interleave_payload,
                extended_help="Alternates workspaces: Odd on primary, Even on secondary."
            )
        )

    schema[ws_idx].extend(strategy_items)

    # Workspaces 1..10 Individual Bindings (clean, concise labels)
    for w_num in range(1, 11):
        ws_str = str(w_num)
        ws_scope = f"workspace_rule/{ws_str}"
        grp = f"Workspaces {1 if w_num <= 5 else 6}–{5 if w_num <= 5 else 10}"

        schema[ws_idx].extend([
            ConfigItem(
                label=f"WS {ws_str} Monitor", key="monitor", scope=ws_scope, type_="picker",
                default=primary_output, options=available_outputs, group=grp,
                extended_help=f"Selects the display to which Workspace {ws_str} is pinned."
            ),
            ConfigItem(
                label=f"WS {ws_str} Default Focus", key="default", scope=ws_scope, type_="bool",
                default=(w_num == 1), group=grp,
                extended_help=f"When true, Workspace {ws_str} focuses automatically when this monitor connects."
            ),
            ConfigItem(
                label=f"WS {ws_str} Persistent", key="persistent", scope=ws_scope, type_="bool",
                default=False, group=grp,
                extended_help=f"When true, Workspace {ws_str} stays visible in the bar even when empty."
            )
        ])

    # -------------------------------------------------------------------------
    # 3. GLOBAL SYSTEM SETTINGS (SECTION 7: hl.config)
    # -------------------------------------------------------------------------
    tabs.append("Globals")
    g_idx = len(tabs) - 1
    schema[g_idx] = [
        ConfigItem(
            label="Variable Frame Rate (VFR)", key="vfr", scope="debug", type_="bool", default=True,
            group="Power & Performance",
            extended_help="When true, stops sending redundant frames when screen is static. Saves ~1 W on laptops."
        ),
        ConfigItem(
            label="Debug Overlay (FPS)", key="overlay", scope="debug", type_="bool", default=False,
            group="Power & Performance",
            extended_help="Draws a real-time overlay showing FPS, timings, and GPU damage regions."
        ),
        ConfigItem(
            label="Global VRR", key="vrr", scope="misc", type_="cycle", default="0",
            options=["0", "1", "2"], hints=["Off", "Always On", "Fullscreen Only"], group="Power & Performance",
            extended_help="Globally configures Variable Refresh Rate behavior across all outputs."
        ),
        ConfigItem(
            label="Global SDR EOTF", key="cm_sdr_eotf", scope="render", type_="picker", default="auto",
            options=["auto", "srgb", "gamma22"], group="Color Pipeline",
            extended_help="Default transfer function assumed for SDR displays."
        ),
        ConfigItem(
            label="Fullscreen HDR Passthrough", key="cm_fs_passthrough", scope="render", type_="bool", default=False,
            group="Color Pipeline",
            extended_help="Fullscreen HDR apps bypass Hyprland color pipeline for raw, zero-overhead gaming."
        ),
        ConfigItem(
            label="Auto HDR Promotion", key="cm_auto_hdr", scope="render", type_="bool", default=False,
            group="Color Pipeline",
            extended_help="Automatically promotes SDR content to HDR in supported media players."
        )
    ]

    # -------------------------------------------------------------------------
    # 4. PRESETS TAB (Concise, punchy labels without redundant prefixes)
    # -------------------------------------------------------------------------
    tabs.append(USER_PRESETS_TAB)
    p_idx = len(tabs) - 1
    preset_items = [
        ConfigItem(
            label="Gaming", key="preset_gaming", scope="DEFAULT", type_="preset",
            default=None, group="Display Profiles",
            preset_payload={
                "misc.vrr": 2,
                "debug.vfr": True,
                "render.cm_fs_passthrough": True,
                "render.cm_auto_hdr": False
            },
            extended_help="High-refresh gaming: Fullscreen VRR, VFR enabled, Fullscreen HDR passthrough active."
        ),
        ConfigItem(
            label="Color Accurate SDR", key="preset_color_sdr", scope="DEFAULT", type_="preset",
            default=None, group="Display Profiles",
            preset_payload={
                "misc.vrr": 0,
                "render.cm_sdr_eotf": "srgb",
                "render.cm_fs_passthrough": False,
                "render.cm_auto_hdr": False
            },
            extended_help="Calibrated sRGB pipeline: Piecewise sRGB curve, VRR disabled to prevent panel flicker."
        ),
        ConfigItem(
            label="Battery Saver", key="preset_battery", scope="DEFAULT", type_="preset",
            default=None, group="Display Profiles",
            preset_payload={
                "misc.vrr": 0,
                "debug.vfr": True,
                "render.cm_fs_passthrough": False,
                "render.cm_auto_hdr": False
            },
            extended_help="Maximum power efficiency: VFR active, VRR disabled, standard color management."
        )
    ]

    if internal_outputs and external_outputs:
        clamshell_payload = {}
        for im in internal_outputs:
            clamshell_payload[f"monitor/{im}.enabled"] = False
        for em in external_outputs:
            clamshell_payload[f"monitor/{em}.enabled"] = True
        for w_i in range(1, 11):
            clamshell_payload[f"workspace_rule/{w_i}.monitor"] = external_outputs[0]
            clamshell_payload[f"workspace_rule/{w_i}.default"] = (w_i == 1)

        preset_items.append(
            ConfigItem(
                label="Clamshell (External Only)", key="preset_clamshell", scope="DEFAULT", type_="preset",
                default=None, group="Display Profiles",
                preset_payload=clamshell_payload,
                extended_help="Clamshell mode: disables internal laptop screen and routes all workspaces to external monitor."
            )
        )

    preset_items.extend([
        ConfigItem(
            label="Visual Layout Designer", key="action_visual_layout_preset", scope="DEFAULT", type_="action",
            default=f"{sys.executable} {Path(__file__).parent / 'monitor_layout.py'} --floating",
            force_interactive=False, group="Visual Placement",
            extended_help="Spawns an interactive 2D spatial canvas in a centered floating window to position, snap, and scale displays with live compositor preview."
        ),
    ])

    schema[p_idx] = preset_items

    if len(tabs) == 3:  # Only Workspaces, Globals, Presets (no physical monitors found)
        tabs.insert(0, "Fallback")
        schema[0] = [
            ConfigItem(
                label="No Monitors Detected", key="none", type_="string", default="", group="Error",
                extended_help="Hyprland IPC returned zero active displays. Verify your socket is accessible."
            )
        ]
        schema = {k + 1 if k >= 0 else k: v for k, v in schema.items()}

    return tabs, schema


TABS, SCHEMA = generate_schema()

# =============================================================================
# DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    script_path = Path(__file__).resolve()
    main_router = Path.home() / "user_scripts" / "dusky_tui" / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
