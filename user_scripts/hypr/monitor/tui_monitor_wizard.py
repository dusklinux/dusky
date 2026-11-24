#!/usr/bin/env python3
"""
===============================================================================
DUSKY MONITOR WIZARD: HIGH-PRECISION HYPRLAND DISPLAY & WORKSPACE MANAGER
===============================================================================
Target: Arch Linux (rolling) | Linux 7.3+ | Python 3.15+ | Hyprland git (Lua)
Standard: Wayland exclusive | wp_fractional_scale_v1 (1/120, 6-decimal parity)

Dynamic TUI router schema for multi-monitor arrangements, 1/120 fractional
scaling, rotation-aware logical hints, spatial placement geometry, workspace
distribution, and HDR/SDR pipeline configuration.
"""

import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_DUSKY_ROOT = (
    Path(os.environ["DUSKY_TUI_ROOT"]).expanduser()
    if os.environ.get("DUSKY_TUI_ROOT")
    else (Path.home() / "user_scripts" / "dusky_tui")
)
if str(_DUSKY_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_ROOT))

from python.frontend.core_types import ConfigItem
from python.engines.monitor_engine import (
    MAX_SCALE,
    MIN_SCALE,
    PRECISION,
    calculate_ppi,
    calculate_relative_positions,
    detect_bitdepth,
    format_scale,
    grid_scales,
    is_internal_connector,
    is_sharp,
    logical_size,
    monitor_health,
    normalize_description,
    normalize_vrr,
    preset_choices,
    short_description,
    snap_scale,
)
from python.shared.lua_source import calls, fields, find_table_bounds, string_value

# --- XDG PATH HELPERS ---
def _xdg_config_home() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if configured:
        cand = Path(configured).expanduser()
        if cand.is_absolute():
            return cand
    return Path.home() / ".config"


def _default_target_file() -> Path:
    return (_xdg_config_home() / "hypr" / "edit_here" / "source" / "monitors.lua").resolve(strict=False)


def _default_theme_file() -> Path:
    return _xdg_config_home() / "matugen" / "generated" / "dusky_tui.json"


def _default_primary_file() -> Path:
    return (_xdg_config_home() / "hypr" / "primary_monitor").resolve(strict=False)


# --- TUI ROUTER CONFIGURATION ---
ENGINE_TYPE = "monitor"
APP_TITLE = "Dusky Monitor & Workspace Wizard"
DEFAULT_MODE = "batch"  # Batch mode prevents Wayland configuration tearing during live updates
TARGET_FILE = str(_default_target_file())
THEME_FILE = str(_default_theme_file())
ENABLE_USER_PRESETS = True
USER_PRESETS_TAB = "Presets"

# --- STANDARD FALLBACK RESOLUTIONS ---
STANDARD_RES: list[tuple[int, int]] = [
    (5120, 2880), (5120, 1440), (3840, 2400), (3840, 2160), (3840, 1600),
    (3440, 1440), (2880, 1800), (2560, 1600), (2560, 1440), (2560, 1080),
    (1920, 1200), (1920, 1080), (1680, 1050), (1600, 900), (1440, 900),
    (1366, 768), (1280, 1024), (1280, 800), (1280, 720), (1024, 768),
]

POS_COMPOSITOR_VARIANTS: list[str] = [
    "auto", "auto-right", "auto-left", "auto-up", "auto-down",
    "auto-center-right", "auto-center-left", "auto-center-up", "auto-center-down",
]

CM_PROFILES: list[str] = ["auto", "srgb", "dcip3", "dp3", "adobe", "wide", "edid", "hdr", "hdredid"]
SDR_EOTFS: list[str] = ["default", "srgb", "gamma22"]

_HEALTH_LABELS: dict[str, str] = {
    "usable": "Usable (Active)",
    "sleeping": "Sleeping (DPMS Off)",
    "off": "Disabled",
    "no_signal": "No Signal (0x0)",
    "synthetic": "Synthetic Fallback",
}

_RE_HZ_SUFFIX = re.compile(r"\s*hz$", re.IGNORECASE)


# --- NULL-SAFE JSON COERCION HELPERS ---
def _safe_int(value: Any, default: int = 0) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        f = float(value)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return bool(value)


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


# --- PERSISTED SCALE HINT EXTRACTION ---
def _load_persisted_scale_hints(config_path: Path) -> list[tuple[str, float]]:
    """Extracts `(output_spec, numeric_scale)` pairs from active `hl.monitor` blocks."""
    try:
        real = config_path.resolve(strict=False)
        if not real.is_file():
            return []
        content = real.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []

    hints: list[tuple[str, float]] = []
    try:
        for _, _, inner_a, inner_b in calls(content, "monitor"):
            try:
                open_pos, close_end = find_table_bounds(content, inner_a, inner_b)
                flds = fields(content, open_pos, close_end)
            except ValueError:
                continue

            if "disabled" in flds:
                ds, de = flds["disabled"]
                if content[ds:de].strip() == "true":
                    continue

            if "output" not in flds or "scale" not in flds:
                continue

            os_, oe_ = flds["output"]
            output_spec = string_value(content[os_:oe_])
            if not output_spec:
                continue

            ss, se = flds["scale"]
            raw_scale = content[ss:se].strip()
            if string_value(raw_scale) is not None:
                continue
            parsed = _safe_float(raw_scale, -1.0)
            if parsed > 0:
                hints.append((output_spec, parsed))
    except Exception:
        return []

    return hints


def _match_scale_hint(hints: list[tuple[str, float]], name: str, desc: str) -> float | None:
    desc_clean = normalize_description(desc)
    desc_short = short_description(desc_clean)
    for output_spec, val in reversed(hints):
        if output_spec == name:
            return val
        if output_spec.startswith("desc:"):
            prefix = output_spec[5:].strip()
            if prefix and (
                desc.startswith(prefix)
                or desc_clean.startswith(prefix)
                or desc_short.startswith(prefix)
            ):
                return val
    return None


def _query_live_monitors() -> list[Any]:
    try:
        proc = subprocess.run(
            ["hyprctl", "-j", "monitors", "all"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        raw = (proc.stdout or "").strip()
        if raw and raw[0] not in ("[", "{"):
            lines = raw.splitlines()
            for idx, line in enumerate(lines):
                if line.strip().startswith(("[", "{")):
                    raw = "\n".join(lines[idx:])
                    break
        parsed = json.loads(raw) if raw else []
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _calculate_valid_scales(
    native_w: int,
    native_h: int,
    current_scale: float = 1.0,
    transform: int = 0,
    scale_hint: float | None = None,
) -> tuple[list[str], list[str]]:
    """Generates 6-decimal scale choices aligned with Hyprland's 1/120 quantum grid.

    Snaps 2-decimal IPC floats via `snap_scale()` using persisted `monitors.lua`
    hints, always preserves a valid positive active scale (even if non-sharp),
    deduplicates by formatted string, and computes rotation-aware logical hints.
    """
    eff_w = native_w if native_w > 0 else 1920
    eff_h = native_h if native_h > 0 else 1080
    rot = transform & 7

    presets = preset_choices(eff_w, eff_h)
    all_sharp = grid_scales(eff_w, eff_h, min_scale=MIN_SCALE, max_scale=MAX_SCALE)

    combined_set: set[float] = {round(s, PRECISION) for s in presets}
    combined_set.update(round(s, PRECISION) for s in all_sharp)

    snapped_current: float | None = None
    if math.isfinite(current_scale) and current_scale > 0:
        snapped_current = round(snap_scale(current_scale, eff_w, eff_h, hint=scale_hint), PRECISION)
        if snapped_current > 0:
            combined_set.add(snapped_current)

    if not combined_set:
        combined_set.add(1.0)

    presets_fmt = {format_scale(p) for p in presets}
    cur_fmt = format_scale(snapped_current) if snapped_current is not None else None

    options: list[str] = ["auto"]
    hints: list[str] = ["Automatic (PPI heuristic)"]
    seen_fmt: set[str] = {"auto"}

    for s in sorted(combined_set):
        fmt = format_scale(s)
        if fmt in seen_fmt:
            continue
        seen_fmt.add(fmt)
        options.append(fmt)

        lw, lh = logical_size(eff_w, eff_h, s, rot)
        res_info = f"{lw}x{lh} logical"
        is_preset = fmt in presets_fmt
        is_current = cur_fmt is not None and fmt == cur_fmt
        sharp = is_sharp(eff_w, eff_h, s)

        if is_preset and is_current:
            hints.append(f"{res_info} (Recommended, Current)")
        elif is_current and not sharp:
            hints.append(f"{res_info} (Current — Non-Sharp)")
        elif is_current:
            hints.append(f"{res_info} (Current)")
        elif is_preset:
            hints.append(f"{res_info} (Recommended)")
        else:
            hints.append(f"{res_info} (Sharp)")

    return options, hints


def generate_schema(
    *,
    monitors: list[dict[str, Any]] | None = None,
    config_path: str | Path | None = None,
    primary_path: str | Path | None = None,
) -> tuple[list[str], dict[int, list[ConfigItem]]]:
    raw_monitors = _query_live_monitors() if monitors is None else _safe_list(monitors)

    # Filter out non-dict entries, blank names, and Hyprland synthetic FALLBACK heads
    clean_monitors: list[dict[str, Any]] = [
        m for m in raw_monitors
        if isinstance(m, dict)
        and _safe_str(m.get("name")).strip()
        and _safe_str(m.get("name")).strip().upper() != "FALLBACK"
    ]

    cfg_file = (
        _default_target_file()
        if config_path is None
        else Path(config_path).expanduser().resolve(strict=False)
    )
    pri_file = (
        _default_primary_file()
        if primary_path is None
        else Path(primary_path).expanduser().resolve(strict=False)
    )

    persisted_hints = _load_persisted_scale_hints(cfg_file)

    name_by_id = {
        _safe_str(m.get("id")): _safe_str(m.get("name")).strip()
        for m in clean_monitors
        if m.get("id") is not None and _safe_str(m.get("name")).strip()
    }

    available_outputs = [_safe_str(m.get("name")).strip() for m in clean_monitors]
    active_outputs = [
        _safe_str(m.get("name")).strip()
        for m in clean_monitors
        if monitor_health(m) in ("usable", "sleeping")
    ]

    saved_pri = ""
    if pri_file.exists():
        try:
            saved_pri = pri_file.read_text(encoding="utf-8").strip()
        except OSError:
            pass

    if saved_pri and saved_pri in active_outputs:
        primary_output = saved_pri
    elif active_outputs:
        primary_output = active_outputs[0]
    elif saved_pri and saved_pri in available_outputs:
        primary_output = saved_pri
    elif available_outputs:
        primary_output = available_outputs[0]
    else:
        primary_output = "eDP-1"

    remaining_active = [name for name in active_outputs if name != primary_output]
    remaining_all = [name for name in available_outputs if name != primary_output]
    secondary_output = (
        remaining_active[0]
        if remaining_active
        else (remaining_all[0] if remaining_all else primary_output)
    )

    internal_outputs = [name for name in available_outputs if is_internal_connector(name)]
    external_outputs = [name for name in available_outputs if not is_internal_connector(name)]

    relative_positions = calculate_relative_positions(clean_monitors)

    tabs: list[str] = []
    schema: dict[int, list[ConfigItem]] = {}

    # -------------------------------------------------------------------------
    # 1. HARDWARE MONITORS TABS (Or Contiguous Fallback at Index 0)
    # -------------------------------------------------------------------------
    if not clean_monitors:
        tabs.append("Fallback")
        schema[0] = [
            ConfigItem(
                label="No Monitors Detected",
                key="none",
                scope="DEFAULT",
                type_="string",
                default="",
                group="Error",
                extended_help="Hyprland IPC returned zero active displays. Verify your socket is accessible.",
            )
        ]
    else:
        for i, m in enumerate(clean_monitors):
            name = _safe_str(m.get("name")).strip()
            desc = _safe_str(m.get("description")).strip()
            make = _safe_str(m.get("make")).strip()
            model = _safe_str(m.get("model")).strip()
            serial = _safe_str(m.get("serial")).strip()
            tabs.append(name)
            schema[i] = []

            raw_w = _safe_int(m.get("width"), 0)
            raw_h = _safe_int(m.get("height"), 0)
            eff_w = raw_w if raw_w > 0 else 1920
            eff_h = raw_h if raw_h > 0 else 1080
            pw_mm = _safe_int(m.get("physicalWidth"), 0)
            ph_mm = _safe_int(m.get("physicalHeight"), 0)
            ppi, diag_in = (
                calculate_ppi(raw_w, raw_h, pw_mm, ph_mm)
                if (raw_w > 0 and raw_h > 0)
                else (0.0, 0.0)
            )

            raw_format = _safe_str(m.get("currentFormat"), "Unknown").strip() or "Unknown"
            detected_bpc = detect_bitdepth(raw_format)
            bitdepth_default = "10" if detected_bpc >= 10 else "8"
            scope_str = f"monitor/{name}"

            raw_mirror = _safe_str(m.get("mirrorOf")).strip()
            mirror_target = "" if raw_mirror.lower() in ("", "none") else name_by_id.get(raw_mirror, raw_mirror)
            if mirror_target not in available_outputs or mirror_target == name:
                mirror_target = ""

            vrr_default = normalize_vrr(m.get("vrr", 0))

            hw_details = _safe_dict(m.get("hardwareDetails"))
            vrr_cap = _safe_bool(hw_details.get("vrrCapable"), False)
            hdr_cap = _safe_bool(hw_details.get("hdr"), False)
            bt2020_cap = _safe_bool(hw_details.get("bt2020"), False)
            is_dis = _safe_bool(m.get("disabled"), False)
            health_code = monitor_health(m)
            health_lbl = _HEALTH_LABELS.get(health_code, health_code)

            clean_modes: list[str] = []
            for mode_entry in _safe_list(m.get("availableModes")):
                if mode_entry is None:
                    continue
                cleaned_mode = _RE_HZ_SUFFIX.sub("", str(mode_entry).strip()).strip()
                if cleaned_mode and cleaned_mode not in clean_modes:
                    clean_modes.append(cleaned_mode)

            base_refresh_float = _safe_float(m.get("refreshRate"), 60.0)
            if base_refresh_float <= 0:
                base_refresh_float = 60.0
            base_refresh = f"{base_refresh_float:.2f}"

            fallback_modes: list[str] = []
            for w, h in STANDARD_RES:
                if w <= eff_w and h <= eff_h:
                    fallback_modes.append(f"{w}x{h}@{base_refresh}")
                    if base_refresh != "60.00":
                        fallback_modes.append(f"{w}x{h}@60.00")

            all_modes = ["preferred", "highres", "highrr", "maxwidth"] + clean_modes
            for f_mode in fallback_modes:
                if f_mode not in all_modes:
                    all_modes.append(f_mode)

            # Hardware-safe identifier strips trailing '(PORT)' from EDID description
            ident_options = [name]
            ident_hints = ["Raw Port ID"]
            clean_desc = normalize_description(desc)
            if clean_desc:
                ident_options.append(f"desc:{clean_desc}")
                ident_hints.append("Hardware Safe ID (EDID Description)")

            transform = _safe_int(m.get("transform"), 0) & 7
            cur_scale_float = _safe_float(m.get("scale"), 1.0)
            if cur_scale_float <= 0:
                cur_scale_float = 1.0
            hint_val = _match_scale_hint(persisted_hints, name, desc)
            scale_options, scale_hints = _calculate_valid_scales(
                eff_w,
                eff_h,
                cur_scale_float,
                transform=transform,
                scale_hint=hint_val,
            )

            rel_opts = relative_positions.get(name, [])
            pos_options: list[str] = []
            pos_hints: list[str] = []
            for val, lbl, hint in rel_opts:
                if val not in pos_options:
                    pos_options.append(val)
                    pos_hints.append(f"{lbl} — {hint}")

            for auto_var in POS_COMPOSITOR_VARIANTS:
                if auto_var not in pos_options:
                    pos_options.append(auto_var)
                    pos_hints.append("Compositor Keyword")

            active_mode_info = (
                f"{raw_w}x{raw_h} @ {base_refresh}Hz"
                if (raw_w > 0 and raw_h > 0)
                else "No Signal (0x0)"
            )
            hw_info = (
                f"**Hardware Identity**:\n"
                f"- Device: {make} {model} ({name})\n"
                f"- Serial: {serial if serial else 'N/A'}\n"
                f"- Description: {desc}\n"
                f"- Dimensions: {pw_mm}mm × {ph_mm}mm (~{diag_in}\" diag, {ppi} PPI)\n"
                f"- Capabilities: VRR: {'Supported' if vrr_cap else 'No'} | HDR: {'Supported' if hdr_cap else 'No'} | Wide Color: {'Supported' if bt2020_cap else 'No'}\n"
                f"- Power / State: {health_lbl}\n"
                f"- Buffer Format: {raw_format} ({detected_bpc}-bit)\n"
                f"- Active Mode: {active_mode_info}\n"
            )

            schema[i].extend([
                ConfigItem(
                    label="Enable Monitor", key="enabled", scope=scope_str, type_="bool", default=not is_dis,
                    group="Core Setup",
                    extended_help=f"{hw_info}\n**Enable Monitor**:\nToggles the monitor state. Disabling removes it from the canvas and relocates windows to remaining displays.",
                ),
                ConfigItem(
                    label="Target Identifier", key="output", scope=scope_str, type_="picker", default=name,
                    options=ident_options, hints=ident_hints, group="Core Setup",
                    extended_help=f"{hw_info}\n**Target Identifier**:\nOutput name ('{name}') or 'desc:' description. Description matching prevents port-swapping issues.",
                ),
                ConfigItem(
                    label="Resolution & Rate", key="mode", scope=scope_str, type_="string", default="preferred",
                    options=all_modes, group="Core Setup",
                    extended_help=f"{hw_info}\n**Resolution & Refresh Rate**:\nSelect an advertised hardware mode or a virtual alias ('preferred', 'highres', 'highrr', 'maxwidth').",
                ),
                ConfigItem(
                    label="Display Scale", key="scale", scope=scope_str, type_="picker", default="auto",
                    options=scale_options, hints=scale_hints, group="Core Setup",
                    extended_help=f"{hw_info}\n**Display Scale Factor**:\nFractional scaling on Hyprland's 1/120 grid. Presets prevent compositor yellow warning banners.",
                ),
                ConfigItem(
                    label="Visual Layout Designer", key="action_visual_layout", scope=scope_str, type_="action",
                    default=f"{sys.executable} {Path(__file__).parent / 'monitor_layout.py'} --floating",
                    force_interactive=False, group="Layout & Transforms",
                    extended_help="Spawns an interactive 2D spatial canvas in a centered floating window to position, snap, and scale displays with live compositor preview.",
                ),
                ConfigItem(
                    label="Position on Canvas", key="position", scope=scope_str, type_="picker", default="auto",
                    options=pos_options, hints=pos_hints, group="Layout & Transforms",
                    extended_help=f"{hw_info}\n**Position on Canvas**:\nSelect auto-placement or an exact relative coordinate calculated against peer displays.",
                ),
                ConfigItem(
                    label="Rotation Transform", key="transform", scope=scope_str, type_="picker", default=str(transform),
                    options=["0", "1", "2", "3", "4", "5", "6", "7"],
                    hints=[
                        "0° (normal)",
                        "90° (clockwise)",
                        "180° (inverted)",
                        "270° (counter-clockwise)",
                        "0° (flipped)",
                        "90° (flipped + clockwise)",
                        "180° (flipped + inverted)",
                        "270° (flipped + counter-clockwise)",
                    ],
                    group="Layout & Transforms",
                    extended_help="Rotates or flips the monitor output. Logical width and height are automatically swapped on 90°/270°.",
                ),
                ConfigItem(
                    label="Reserved Padding", key="reserved_area", scope=scope_str, type_="int", default=0,
                    group="Layout & Transforms",
                    extended_help="Custom reserved margin (in pixels) unoccupied by tiled windows on all edges.",
                ),
                ConfigItem(
                    label="Mirror Output", key="mirror", scope=scope_str, type_="picker", default=mirror_target,
                    options=[""] + [out for out in available_outputs if out != name],
                    hints=["None (Independent Screen)"] + [f"Clone {out}" for out in available_outputs if out != name],
                    group="Layout & Transforms",
                    extended_help="Mirrors another display pixel-for-pixel.",
                ),
                ConfigItem(
                    label="VRR", key="vrr", scope=scope_str, type_="cycle", default=vrr_default,
                    options=["0", "1", "2"], hints=["Off", "Always On", "Fullscreen Only"], group="Advanced Display",
                    extended_help="Configures per-display Variable Refresh Rate (VRR / FreeSync / G-Sync).",
                ),
                ConfigItem(
                    label="Bitdepth", key="bitdepth", scope=scope_str, type_="cycle", default=bitdepth_default,
                    options=["8", "10"], hints=["8-bit (Standard SDR)", "10-bit (Deep Color)"],
                    group="Advanced Display",
                    extended_help="Enables 8-bit SDR or 10-bit deep color output for supported HDR and wide-gamut monitors.",
                ),
                ConfigItem(
                    label="Force Wide Color", key="supports_wide_color", scope=scope_str, type_="cycle", default="0",
                    options=["-1", "0", "1"], hints=["Force Off", "Auto Detect", "Force On"], group="Advanced Display",
                    extended_help="Controls wide color gamut support (-1 = off, 0 = auto, 1 = on).",
                ),
                ConfigItem(
                    label="Force HDR", key="supports_hdr", scope=scope_str, type_="cycle", default="0",
                    options=["-1", "0", "1"], hints=["Force Off", "Auto Detect", "Force On"], group="Advanced Display",
                    extended_help="Forces HDR capability on the display pipeline (-1 = off, 0 = auto, 1 = on).",
                ),
                ConfigItem(
                    label="ICC Profile", key="icc", scope=scope_str, type_="string", default="",
                    group="Color Pipeline",
                    extended_help="Absolute filesystem path to an ICC color profile (.icc or .icm).",
                ),
                ConfigItem(
                    label="Color Management", key="cm", scope=scope_str, type_="picker", default="auto",
                    options=CM_PROFILES, group="Color Pipeline",
                    extended_help="Selects color management preset. 'hdr' activates wide color gamut and PQ transfer.",
                ),
                ConfigItem(
                    label="SDR Curve (EOTF)", key="sdr_eotf", scope=scope_str, type_="picker", default="default",
                    options=SDR_EOTFS, group="Color Pipeline",
                    extended_help="Assumed electro-optical transfer function for SDR content.",
                ),
                ConfigItem(
                    label="HDR SDR Brightness", key="sdrbrightness", scope=scope_str, type_="float", default=1.0,
                    min_val=0.1, max_val=3.0, step=0.1, group="HDR / SDR Mapping",
                    extended_help="Controls white-point brightness of SDR content in HDR mode. Typically 1.0 to 1.8.",
                ),
                ConfigItem(
                    label="HDR SDR Saturation", key="sdrsaturation", scope=scope_str, type_="float", default=1.0,
                    min_val=0.1, max_val=2.0, step=0.1, group="HDR / SDR Mapping",
                    extended_help="Controls SDR color saturation in HDR mode. Default is 1.0.",
                ),
                ConfigItem(
                    label="SDR Min Luminance", key="sdr_min_luminance", scope=scope_str, type_="float", default=0.2,
                    group="Luminance Tuning", extended_help="Minimum luminance (nits) for SDR-to-HDR tone mapping.",
                ),
                ConfigItem(
                    label="SDR Max Luminance", key="sdr_max_luminance", scope=scope_str, type_="int", default=80,
                    group="Luminance Tuning", extended_help="Maximum luminance (nits) for SDR content.",
                ),
                ConfigItem(
                    label="Min Luminance", key="min_luminance", scope=scope_str, type_="float", default=-1.0,
                    group="Luminance Tuning", extended_help="Monitor minimum measurable luminance (-1 = EDID default).",
                ),
                ConfigItem(
                    label="Max Luminance", key="max_luminance", scope=scope_str, type_="int", default=-1,
                    group="Luminance Tuning", extended_help="Monitor maximum peak luminance (-1 = EDID default).",
                ),
                ConfigItem(
                    label="Max Avg Luminance", key="max_avg_luminance", scope=scope_str, type_="int", default=-1,
                    group="Luminance Tuning", extended_help="Monitor maximum full-field average luminance (-1 = EDID default).",
                ),
            ])

    # -------------------------------------------------------------------------
    # 2. WORKSPACE PLANNING TAB (SECTION 6: hl.workspace_rule)
    # -------------------------------------------------------------------------
    tabs.append("Workspaces")
    ws_idx = len(tabs) - 1
    schema[ws_idx] = []

    seq_payload: dict[str, Any] = {}
    for w_i in range(1, 11):
        target_m = primary_output if w_i <= 5 else secondary_output
        seq_payload[f"workspace_rule/{w_i}.monitor"] = target_m
        seq_payload[f"workspace_rule/{w_i}.default"] = (w_i in (1, 6))

    all_primary_payload: dict[str, Any] = {}
    for w_i in range(1, 11):
        all_primary_payload[f"workspace_rule/{w_i}.monitor"] = primary_output
        all_primary_payload[f"workspace_rule/{w_i}.default"] = (w_i == 1)

    interleave_payload: dict[str, Any] = {}
    for w_i in range(1, 11):
        target_m = primary_output if (w_i % 2 == 1) else secondary_output
        interleave_payload[f"workspace_rule/{w_i}.monitor"] = target_m
        interleave_payload[f"workspace_rule/{w_i}.default"] = (w_i in (1, 2))

    strategy_items: list[ConfigItem] = []
    if available_outputs:
        strategy_items.append(
            ConfigItem(
                label="Primary Bank Display", key="primary_monitor", scope="DEFAULT",
                type_="picker", default=primary_output, options=available_outputs, group="Banked Workspaces (1–10)",
                extended_help="Selects the primary display assigned to Workspace Bank 0 (workspaces 1–10) in multi_monitor_workspace.sh. Remaining connected displays are sorted left-to-right (workspaces 11–20, 21–30, etc.).",
            )
        )
    if secondary_output != primary_output:
        strategy_items.append(
            ConfigItem(
                label="Sequential (1-5 / 6-10)", key="plan_seq", scope="DEFAULT",
                type_="preset", default=None, group="Quick Strategies",
                preset_payload=seq_payload,
                extended_help="Distributes workspaces 1-5 on primary, and 6-10 on secondary.",
            )
        )
    strategy_items.append(
        ConfigItem(
            label="All on Primary", key="plan_all_pri", scope="DEFAULT",
            type_="preset", default=None, group="Quick Strategies",
            preset_payload=all_primary_payload,
            extended_help="Pins all workspaces (1-10) to the primary display.",
        )
    )
    if secondary_output != primary_output:
        strategy_items.append(
            ConfigItem(
                label="Interleaved (Odd / Even)", key="plan_inter", scope="DEFAULT",
                type_="preset", default=None, group="Quick Strategies",
                preset_payload=interleave_payload,
                extended_help="Alternates workspaces: Odd on primary, Even on secondary.",
            )
        )

    schema[ws_idx].extend(strategy_items)

    ws_picker_options = available_outputs if available_outputs else [primary_output]
    for w_num in range(1, 11):
        ws_str = str(w_num)
        ws_scope = f"workspace_rule/{ws_str}"
        grp = f"Workspaces {1 if w_num <= 5 else 6}–{5 if w_num <= 5 else 10}"

        schema[ws_idx].extend([
            ConfigItem(
                label=f"WS {ws_str} Monitor", key="monitor", scope=ws_scope, type_="picker",
                default=primary_output, options=ws_picker_options, group=grp,
                extended_help=f"Selects the display to which Workspace {ws_str} is pinned.",
            ),
            ConfigItem(
                label=f"WS {ws_str} Default Focus", key="default", scope=ws_scope, type_="bool",
                default=(w_num == 1), group=grp,
                extended_help=f"When true, Workspace {ws_str} focuses automatically when this monitor connects.",
            ),
            ConfigItem(
                label=f"WS {ws_str} Persistent", key="persistent", scope=ws_scope, type_="bool",
                default=False, group=grp,
                extended_help=f"When true, Workspace {ws_str} stays visible in the bar even when empty.",
            ),
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
            extended_help="When true, stops sending redundant frames when screen is static. Saves ~1 W on laptops.",
        ),
        ConfigItem(
            label="Debug Overlay (FPS)", key="overlay", scope="debug", type_="bool", default=False,
            group="Power & Performance",
            extended_help="Draws a real-time overlay showing FPS, timings, and GPU damage regions.",
        ),
        ConfigItem(
            label="Global VRR", key="vrr", scope="misc", type_="cycle", default="0",
            options=["0", "1", "2"], hints=["Off", "Always On", "Fullscreen Only"], group="Power & Performance",
            extended_help="Globally configures Variable Refresh Rate behavior across all outputs.",
        ),
        ConfigItem(
            label="Global SDR EOTF", key="cm_sdr_eotf", scope="render", type_="picker", default="auto",
            options=["auto", "srgb", "gamma22"], group="Color Pipeline",
            extended_help="Default transfer function assumed for SDR displays.",
        ),
        ConfigItem(
            label="Direct Scanout", key="direct_scanout", scope="render", type_="cycle", default="0",
            options=["0", "1", "2"], hints=["Off", "Always On", "Auto (Games)"], group="Power & Performance",
            extended_help="Bypasses compositor rendering for fullscreen apps (0 = Off, 1 = Always On, 2 = Auto for games).",
        ),
        ConfigItem(
            label="Auto HDR Promotion", key="cm_auto_hdr", scope="render", type_="cycle", default="0",
            options=["0", "1", "2"], hints=["Off", "Auto (HDR)", "Auto (HDR EDID)"], group="Color Pipeline",
            extended_help="Automatically switches to HDR mode for fullscreen HDR apps (0 = Off, 1 = HDR, 2 = HDR EDID).",
        ),
    ]

    # -------------------------------------------------------------------------
    # 4. PRESETS TAB
    # -------------------------------------------------------------------------
    tabs.append(USER_PRESETS_TAB)
    p_idx = len(tabs) - 1
    preset_items: list[ConfigItem] = [
        ConfigItem(
            label="Gaming", key="preset_gaming", scope="DEFAULT", type_="preset",
            default=None, group="Display Profiles",
            preset_payload={
                "misc.vrr": 2,
                "debug.vfr": True,
                "render.direct_scanout": 2,
                "render.cm_auto_hdr": 1,
            },
            extended_help="High-refresh gaming: Fullscreen VRR, VFR enabled, Auto Game Direct Scanout and Auto HDR active.",
        ),
        ConfigItem(
            label="Color Accurate SDR", key="preset_color_sdr", scope="DEFAULT", type_="preset",
            default=None, group="Display Profiles",
            preset_payload={
                "misc.vrr": 0,
                "render.cm_sdr_eotf": "srgb",
                "render.direct_scanout": 0,
                "render.cm_auto_hdr": 0,
            },
            extended_help="Calibrated sRGB pipeline: Piecewise sRGB curve, direct scanout and VRR disabled to prevent flicker.",
        ),
        ConfigItem(
            label="Battery Saver", key="preset_battery", scope="DEFAULT", type_="preset",
            default=None, group="Display Profiles",
            preset_payload={
                "misc.vrr": 0,
                "debug.vfr": True,
                "render.direct_scanout": 0,
                "render.cm_auto_hdr": 0,
            },
            extended_help="Maximum power efficiency: VFR active, VRR and Auto HDR disabled.",
        ),
    ]

    if internal_outputs and external_outputs:
        clamshell_payload: dict[str, Any] = {}
        for im in internal_outputs:
            clamshell_payload[f"monitor/{im}.enabled"] = False
            clamshell_payload[f"monitor/{im}.mirror"] = ""
        for em in external_outputs:
            clamshell_payload[f"monitor/{em}.enabled"] = True

        # Assign workspaces 1..10 sequentially across external monitors, guaranteeing
        # exactly one default workspace (the lowest-numbered one) per external display.
        num_ext = len(external_outputs)
        seen_default_ext: set[str] = set()
        for w_i in range(1, 11):
            target_ext_idx = min(num_ext - 1, (w_i - 1) * num_ext // 10)
            target_ext = external_outputs[target_ext_idx]
            is_default_ws = target_ext not in seen_default_ext
            if is_default_ws:
                seen_default_ext.add(target_ext)
            clamshell_payload[f"workspace_rule/{w_i}.monitor"] = target_ext
            clamshell_payload[f"workspace_rule/{w_i}.default"] = is_default_ws

        preset_items.append(
            ConfigItem(
                label="Clamshell (External Only)", key="preset_clamshell", scope="DEFAULT", type_="preset",
                default=None, group="Display Profiles",
                preset_payload=clamshell_payload,
                extended_help=(
                    f"Clamshell mode: disables internal laptop screen and distributes workspaces "
                    f"across {num_ext} external display{'s' if num_ext > 1 else ''}."
                ),
            )
        )

    preset_items.append(
        ConfigItem(
            label="Visual Layout Designer", key="action_visual_layout_preset", scope="DEFAULT", type_="action",
            default=f"{sys.executable} {Path(__file__).parent / 'monitor_layout.py'} --floating",
            force_interactive=False, group="Visual Placement",
            extended_help="Spawns an interactive 2D spatial canvas in a centered floating window to position, snap, and scale displays with live compositor preview.",
        )
    )

    schema[p_idx] = preset_items
    return tabs, schema


TABS, SCHEMA = generate_schema()

# =============================================================================
# DIRECT EXECUTION HANDLER
# =============================================================================
if __name__ == "__main__":
    script_path = Path(__file__).resolve()
    main_router = _DUSKY_ROOT / "python" / "main" / "main.py"

    if main_router.exists():
        sys.exit(subprocess.run([sys.executable, str(main_router), str(script_path)] + sys.argv[1:]).returncode)
    else:
        print(f"[-] Error: Main Dusky TUI router not found at {main_router}", file=sys.stderr)
        sys.exit(1)
