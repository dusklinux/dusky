#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: HYPRLAND MONITOR & WORKSPACE LUA ENGINE
===============================================================================
Advanced Lua engine for Hyprland 0.55+ monitor and workspace configuration.
Incorporates high-precision Wayland fractional scaling mathematics (1/120 quantum),
relative spatial positioning geometry, hardware enrichment, and workspace planning
ensuring exact integer logical pixel coordinates and zero compositor scaling warnings.
"""

import os
lazy import math
lazy from math.integer import gcd
lazy import json
import re
lazy import subprocess
lazy from pathlib import Path
lazy from typing import Any

from python.engines.lua import HyprlandLuaEngine
from python.shared.config_io import atomic_write, read_text, boolean
from python.shared.lua_source import calls, replace_field

# =============================================================================
# 1. WAYLAND / HYPRLAND FRACTIONAL SCALING MATHEMATICS (wp_fractional_scale_v1)
# =============================================================================
# In Hyprland, fractional scale factors operate on a 1/120 quantum grid.
# A scale s = n / 120 is "sharp" if and only if both logical dimensions are
# exact integers: (120 * W) % n == 0 and (120 * H) % n == 0.
# Any scale violating this triggers Hyprland's yellow sub-pixel warning banner.

HYPRLAND_SCALE_STEPS: int = 120
MIN_SCALE: float = 0.25
MAX_SCALE: float = 4.0
PRECISION: int = 5


def sharp_at_numerator(width: int, height: int, numerator: int) -> bool:
    """Tests if scale factor (numerator / 120) yields exact integer logical coordinates."""
    if numerator <= 0:
        return False
    return (HYPRLAND_SCALE_STEPS * width) % numerator == 0 and (HYPRLAND_SCALE_STEPS * height) % numerator == 0


def is_sharp(width: int, height: int, scale: float) -> bool:
    """Verifies whether a given scale float divides the display resolution cleanly."""
    if not math.isfinite(scale) or scale <= 0 or width <= 0 or height <= 0:
        return False
    lw = width / scale
    lh = height / scale
    if abs(lw - round(lw)) < 1e-4 and abs(lh - round(lh)) < 1e-4:
        return True
    numerator = int(round(scale * HYPRLAND_SCALE_STEPS))
    if numerator <= 0 or abs((numerator / HYPRLAND_SCALE_STEPS) - scale) > 1e-4:
        return False
    return sharp_at_numerator(width, height, numerator)


def grid_scales(width: int, height: int, min_scale: float = 0.5, max_scale: float = 3.5) -> list[float]:
    """Generates all sharp scale values on the 1/120 grid between min_scale and max_scale."""
    if width <= 0 or height <= 0:
        return [1.0]
    min_num = math.ceil(max(min_scale, MIN_SCALE) * HYPRLAND_SCALE_STEPS)
    max_num = math.floor(min(max_scale, MAX_SCALE) * HYPRLAND_SCALE_STEPS)
    scales: list[float] = []
    for num in range(min_num, max_num + 1):
        if sharp_at_numerator(width, height, num):
            scales.append(round(num / HYPRLAND_SCALE_STEPS, PRECISION))
    return scales


def clean_scale(width: int, height: int, requested: float) -> float:
    """
    Rounds up to the nearest sharp scale on the 1/120 grid, capped at the largest
    divisor that divides the mode. Never rounds down, ensuring the desktop never
    becomes larger than requested on the 1/120 quantum grid.
    """
    if not math.isfinite(requested) or width <= 0 or height <= 0 or requested <= 0:
        return 1.0
    divisor = gcd(width * HYPRLAND_SCALE_STEPS, height * HYPRLAND_SCALE_STEPS)
    units = math.ceil(requested * HYPRLAND_SCALE_STEPS)
    if units > divisor:
        units = divisor
    if units < 1:
        units = 1
    while divisor % units != 0:
        units += 1
    return round(units / HYPRLAND_SCALE_STEPS, PRECISION)


def closest_sharp(width: int, height: int, requested: float, min_scale: float = 0.5, max_scale: float = 4.0) -> float:
    """Finds the sharp scale closest to the requested float."""
    if not math.isfinite(requested) or width <= 0 or height <= 0 or requested <= 0:
        return 1.0
    all_sharp = grid_scales(width, height, min_scale, max_scale)
    if not all_sharp:
        return 1.0
    return min(all_sharp, key=lambda s: abs(s - requested))


def preset_choices(width: int, height: int) -> list[float]:
    """
    Generates recommended scale presets ([1.0, 1.25, 1.5, 1.6, 2.0, 3.0],
    plus 4.0 for 5K+ screens), mapped through clean_scale, deduplicated, and sorted.
    """
    sharp = grid_scales(width, height, 1.0, MAX_SCALE)
    largest = sharp[-1] if sharp else 1.0
    presets = [1.0, 1.25, 1.5, 1.6, 2.0, 3.0]
    if width >= 5120 and sharp and sharp[-1] >= 4.0:
        presets.append(4.0)

    by_key: dict[float, dict[str, Any]] = {}
    for idx, req in enumerate(presets):
        eff = clean_scale(width, height, req)
        if eff > largest:
            continue
        key = round(eff * 100) / 100
        dist = abs(req - eff)
        if key not in by_key or dist < by_key[key]["dist"]:
            by_key[key] = {"val": eff, "idx": idx, "dist": dist}

    kept = sorted(by_key.values(), key=lambda c: c["idx"])
    return [c["val"] for c in kept]


def format_scale(value: float) -> str:
    """Formats scale float cleanly, stripping redundant trailing zeros."""
    fmt = f"{value:.{PRECISION}f}".rstrip("0").rstrip(".")
    return fmt if fmt else "1"


def logical_size(width: int, height: int, scale: float, transform: int = 0) -> tuple[int, int]:
    """
    Computes exact logical bounding box dimensions in compositor desktop coordinates,
    accounting for scale factor and rotation transforms (transforms 1, 3, 5, 7 swap width and height).
    """
    if scale <= 0:
        scale = 1.0
    eff_w = int(round(width / scale))
    eff_h = int(round(height / scale))
    if transform in (1, 3, 5, 7):
        return eff_h, eff_w
    return eff_w, eff_h


# =============================================================================
# 2. HARDWARE ENRICHMENT & RELATIVE SPATIAL POSITIONING
# =============================================================================

def is_internal_connector(name: str) -> bool:
    """Reports whether a connector name belongs to an internal laptop screen (eDP, LVDS, DSI)."""
    low = name.lower().strip()
    return low.startswith(("edp", "lvds", "dsi"))


def normalize_vrr(raw: Any) -> str:
    """Normalizes compositor VRR mode into standard string ('0'=off, '1'=on, '2'=fullscreen-only)."""
    if isinstance(raw, bool):
        return "1" if raw else "0"
    if isinstance(raw, (int, float)):
        val = int(raw)
        return str(val) if val in (0, 1, 2) else "0"
    s = str(raw).strip().lower()
    if s == "true":
        return "1"
    if s in ("false", "off", ""):
        return "0"
    return s if s in ("0", "1", "2") else "0"


def monitor_health(m: dict[str, Any]) -> str:
    """Assesses live monitor hardware health and power state."""
    name = m.get("name", "")
    if name.upper() == "FALLBACK":
        return "synthetic"
    if m.get("disabled", False):
        return "off"
    if not m.get("dpmsStatus", True):
        return "sleeping"
    if int(m.get("width", 0)) <= 0 or int(m.get("height", 0)) <= 0:
        return "no_signal"
    return "usable"


def wake_displays(outputs: list[str] | None = None) -> bool:
    """
    Wakes displays from DPMS sleep in Hyprland 0.55+ (evaluating Lua dispatcher with fallback).
    Guarantees that enabling a previously disabled/sleeping monitor immediately powers on the panel.
    """
    try:
        if outputs:
            for out in outputs:
                clean = out.strip().removeprefix("desc:")
                if not clean:
                    continue
                cmd = f'hl.dsp.dpms({{ action = "enable", monitor = {json.dumps(clean)} }})'
                res = subprocess.run(["hyprctl", "dispatch", cmd], capture_output=True, text=True, timeout=1)
                if res.returncode != 0:
                    subprocess.run(["hyprctl", "dispatch", "dpms", "on", clean], capture_output=True, timeout=1)
        else:
            cmd = 'hl.dsp.dpms({ action = "enable" })'
            res = subprocess.run(["hyprctl", "dispatch", cmd], capture_output=True, text=True, timeout=1)
            if res.returncode != 0:
                subprocess.run(["hyprctl", "dispatch", "dpms", "on"], capture_output=True, timeout=1)
        return True
    except Exception:
        return False


def calculate_ppi(width: int, height: int, physical_w_mm: int, physical_h_mm: int) -> tuple[float, float]:
    """Calculates display PPI and diagonal size in inches from physical EDID dimensions."""
    if physical_w_mm <= 0 or physical_h_mm <= 0 or width <= 0 or height <= 0:
        return 0.0, 0.0
    w_in = physical_w_mm / 25.4
    h_in = physical_h_mm / 25.4
    diagonal_in = math.sqrt(w_in ** 2 + h_in ** 2)
    diag_pixels = math.sqrt(width ** 2 + height ** 2)
    ppi = diag_pixels / diagonal_in if diagonal_in > 0 else 0.0
    return round(ppi, 1), round(diagonal_in, 1)


def detect_bitdepth(format_str: str) -> int:
    """Detects color bitdepth from Hyprland output pixel format string."""
    fmt = format_str.upper()
    if any(x in fmt for x in ("2101010", "101010", "RGB10", "BGR10")):
        return 10
    if any(x in fmt for x in ("16161616F", "RGBA16F", "RGB16F")):
        return 16
    return 8


def calculate_relative_positions(monitors: list[dict[str, Any]]) -> dict[str, list[tuple[str, str, str]]]:
    """
    Calculates exact relative spatial positions (right, left, above, below, centered)
    between all active connected monitors based on their logical dimensions.
    Returns: { monitor_name: [ (pos_value, label, hint), ... ] }
    """
    positions_map: dict[str, list[tuple[str, str, str]]] = {}

    monitor_bounds: dict[str, tuple[int, int, int, int]] = {}
    for m in monitors:
        name = m.get("name", "")
        if not name:
            continue
        w = int(m.get("width", 1920))
        h = int(m.get("height", 1080))
        s = float(m.get("scale", 1.0))
        t = int(m.get("transform", 0))
        x = int(m.get("x", 0))
        y = int(m.get("y", 0))
        lw, lh = logical_size(w, h, s, t)
        monitor_bounds[name] = (x, y, lw, lh)

    for m in monitors:
        name = m.get("name", "")
        if not name:
            continue
        cur_x, cur_y, my_w, my_h = monitor_bounds.get(name, (0, 0, 1920, 1080))
        opts: list[tuple[str, str, str]] = []

        opts.append((f"{cur_x}x{cur_y}", f"Current ({cur_x}x{cur_y})", "Active canvas coordinates"))
        opts.append(("0x0", "Origin (0x0)", "Top-left of virtual desktop space"))

        for peer_name, (px, py, pw, ph) in monitor_bounds.items():
            if peer_name == name:
                continue

            rx = px + pw
            ry_center = py + round((ph - my_h) / 2)
            opts.append((f"{rx}x{ry_center}", f"Right of {peer_name} (Centered)", f"Offset: {rx}x{ry_center}"))

            if ry_center != py:
                opts.append((f"{rx}x{py}", f"Right of {peer_name} (Top)", f"Offset: {rx}x{py}"))

            lx = px - my_w
            ly_center = py + round((ph - my_h) / 2)
            opts.append((f"{lx}x{ly_center}", f"Left of {peer_name} (Centered)", f"Offset: {lx}x{ly_center}"))

            if ly_center != py:
                opts.append((f"{lx}x{py}", f"Left of {peer_name} (Top)", f"Offset: {lx}x{py}"))

            by_y = py + ph
            bx_center = px + round((pw - my_w) / 2)
            opts.append((f"{bx_center}x{by_y}", f"Below {peer_name} (Centered)", f"Offset: {bx_center}x{by_y}"))

            ay_y = py - my_h
            ax_center = px + round((pw - my_w) / 2)
            opts.append((f"{ax_center}x{ay_y}", f"Above {peer_name} (Centered)", f"Offset: {ax_center}x{ay_y}"))

        positions_map[name] = opts

    return positions_map


# =============================================================================
# 3. MONITOR & WORKSPACE LUA ENGINE
# =============================================================================

class MonitorLuaEngine(HyprlandLuaEngine):
    """
    Specialized gatekeeper for Hyprland 0.55+ monitors and workspace rules.
    Injects virtual hardware state, validates 1/120 fractional scaling,
    handles dynamic relative placement, workspace rule synchronization, and globals.
    """
    def __init__(self, config_path: str = "~/Documents/monitors.lua"):
        expanded_path = str(Path(config_path).expanduser().resolve())
        super().__init__(config_path=expanded_path)
        self._scope_map: dict[str, str] = {}
        self._monitor_resolutions: dict[str, tuple[int, int]] = {}
        self._monitor_metadata: dict[str, dict[str, Any]] = {}

    def _get_valid_scale(self, requested_scale: float, phys_w: int, phys_h: int) -> str:
        """Coerces any arbitrary scale into the nearest mathematically sharp scale on the 1/120 grid."""
        if requested_scale <= 0.1:
            return str(requested_scale)
        eff = closest_sharp(phys_w, phys_h, requested_scale)
        return format_scale(eff)

    def load_state(self) -> dict[str, Any]:
        state = super().load_state()
        self._ast_state = dict(state)
        self._scope_map.clear()
        self._monitor_resolutions.clear()
        self._monitor_metadata.clear()

        try:
            res = subprocess.run(["hyprctl", "-j", "monitors", "all"], capture_output=True, text=True, timeout=3)
            raw = res.stdout.strip()
            if raw and not raw[0] in ("[", "{"):
                for i, line in enumerate(raw.splitlines()):
                    if line.strip().startswith(("[", "{")):
                        raw = "\n".join(raw.splitlines()[i:])
                        break
            live_monitors = json.loads(raw)
        except Exception:
            live_monitors = []

        # Filter out Hyprland synthetic FALLBACK placeholder heads
        live_monitors = [m for m in live_monitors if m.get("name", "").strip().upper() != "FALLBACK"]
        name_by_id = {str(m.get("id")): m.get("name") for m in live_monitors if "id" in m and "name" in m}

        normalized_state: dict[str, Any] = {}
        available_monitor_names: list[str] = []

        for m in live_monitors:
            name = m.get("name", "")
            desc = m.get("description", "")
            if not name:
                continue
            available_monitor_names.append(name)

            phys_w = int(m.get("width", 1920))
            phys_h = int(m.get("height", 1080))
            if phys_w <= 0: phys_w = 1920
            if phys_h <= 0: phys_h = 1080
            self._monitor_resolutions[name] = (phys_w, phys_h)

            pw_mm = int(m.get("physicalWidth", 0))
            ph_mm = int(m.get("physicalHeight", 0))
            ppi, diag_in = calculate_ppi(phys_w, phys_h, pw_mm, ph_mm)
            detected_bpc = detect_bitdepth(m.get("currentFormat", ""))

            # Normalize mirror targets (translates 'none' -> '' and monitor ID -> connector name)
            raw_mirror = str(m.get("mirrorOf", "")).strip()
            live_mirror = "" if raw_mirror.lower() in ("", "none") else name_by_id.get(raw_mirror, raw_mirror)

            # Hardware capability inspection and health state
            hw_details = m.get("hardwareDetails", {})
            vrr_capable = bool(hw_details.get("vrrCapable", False))
            hdr_capable = bool(hw_details.get("hdr", False))
            bt2020_capable = bool(hw_details.get("bt2020", False))
            dpms_status = bool(m.get("dpmsStatus", True))
            live_vrr = normalize_vrr(m.get("vrr", 0))

            self._monitor_metadata[name] = {
                "make": m.get("make", ""),
                "model": m.get("model", ""),
                "serial": m.get("serial", ""),
                "description": desc,
                "ppi": ppi,
                "diagonal_inches": diag_in,
                "bitdepth": detected_bpc,
                "refresh_rate": float(m.get("refreshRate", 60.0)),
                "current_mode": f"{phys_w}x{phys_h}@{float(m.get('refreshRate', 60.0)):.2f}",
                "vrr_capable": vrr_capable,
                "hdr_capable": hdr_capable,
                "bt2020_capable": bt2020_capable,
                "dpms_status": dpms_status,
                "mirror_of": live_mirror,
                "health": monitor_health(m)
            }

            ui_scope = f"monitor/{name}"
            ast_scope = ui_scope

            if desc:
                for key in state.keys():
                    if key.startswith("monitor/desc:"):
                        parts = key.split("/")
                        if len(parts) >= 2 and parts[1][5:] in desc:
                            ast_scope = f"monitor/{parts[1]}"
                            break

            self._scope_map[ui_scope] = ast_scope

            prefix = ast_scope + "/"
            for k, v in state.items():
                if k.startswith(prefix):
                    sub_key = k[len(prefix):]
                    if sub_key == "reserved_area" and isinstance(v, dict):
                        normalized_state[f"{ui_scope}/{sub_key}"] = 0
                    else:
                        normalized_state[f"{ui_scope}/{sub_key}"] = v

            cur_scale = m.get("scale", 1.0)
            clean_cur_scale = format_scale(closest_sharp(phys_w, phys_h, float(cur_scale)))

            # Read disabled state from AST if present; otherwise fall back to compositor hardware report
            is_disabled = bool(state.get(f"{ast_scope}/disabled", m.get("disabled", False)))
            normalized_state[f"{ui_scope}/disabled"] = is_disabled
            normalized_state[f"{ui_scope}/enabled"] = not is_disabled

            defaults = {
                "output": ast_scope.split("/")[1],
                "enabled": not is_disabled,
                "disabled": is_disabled,
                "mode": "preferred",
                "position": "auto",
                "scale": clean_cur_scale,
                "transform": str(m.get("transform", 0)),
                "vrr": live_vrr,
                "bitdepth": str(detected_bpc),
                "cm": m.get("colorManagementPreset", "auto") or "auto",
                "sdr_eotf": "default",
                "sdrbrightness": str(m.get("sdrBrightness", 1.0)),
                "sdrsaturation": str(m.get("sdrSaturation", 1.0)),
                "mirror": live_mirror,
                "icc": "",
                "reserved_area": 0,
                "supports_wide_color": "1" if bt2020_capable else "0",
                "supports_hdr": "1" if hdr_capable else "0",
                "sdr_min_luminance": 0.2,
                "sdr_max_luminance": 80,
                "min_luminance": -1.0,
                "max_luminance": -1,
                "max_avg_luminance": -1
            }

            for key, default_val in defaults.items():
                state_key = f"{ui_scope}/{key}"
                if state_key not in normalized_state:
                    normalized_state[state_key] = default_val

        # Preserve AST config blocks for monitors that are currently offline
        for k, v in state.items():
            is_matched = False
            if k.startswith("monitor/"):
                ast_monitor_name = k.split("/")[1]
                for ui_sc, ast_sc in self._scope_map.items():
                    if ast_sc == f"monitor/{ast_monitor_name}":
                        is_matched = True
                        break
            if not is_matched:
                normalized_state[k] = v

        # -------------------------------------------------------------
        # WORKSPACE RULES (SECTION 6): Load or initialize Workspaces 1..10
        # -------------------------------------------------------------
        primary_monitor = available_monitor_names[0] if available_monitor_names else ""
        for ws_idx in range(1, 11):
            ws_str = str(ws_idx)
            ws_scope = f"workspace_rule/{ws_str}"

            ws_mon_key = f"{ws_scope}/monitor"
            ws_def_key = f"{ws_scope}/default"
            ws_per_key = f"{ws_scope}/persistent"

            if ws_mon_key in state:
                normalized_state[ws_mon_key] = str(state[ws_mon_key])
            else:
                normalized_state[ws_mon_key] = primary_monitor

            if ws_def_key in state:
                normalized_state[ws_def_key] = bool(state[ws_def_key])
            else:
                normalized_state[ws_def_key] = (ws_idx == 1)

            if ws_per_key in state:
                normalized_state[ws_per_key] = bool(state[ws_per_key])
            else:
                normalized_state[ws_per_key] = False

        # -------------------------------------------------------------
        # GLOBAL SETTINGS (SECTION 7): Power, Render, and VFR
        # -------------------------------------------------------------
        global_defaults = {
            "debug/vfr": True,
            "debug/overlay": False,
            "misc/vrr": "0",
            "render/cm_sdr_eotf": "auto",
            "render/cm_fs_passthrough": False,
            "render/cm_auto_hdr": False
        }
        # -------------------------------------------------------------
        # PRIMARY MONITOR FOR BANKED WORKSPACES (~/.config/hypr/primary_monitor)
        # -------------------------------------------------------------
        primary_file = self._primary_path()
        if primary_file.exists():
            try:
                cached_pri = primary_file.read_text(encoding="utf-8").strip()
                if cached_pri:
                    primary_monitor = cached_pri
            except Exception:
                pass
        for key, value in global_defaults.items():
            normalized_state.setdefault(key, value)
        normalized_state["DEFAULT/primary_monitor"] = primary_monitor

        self.cache = normalized_state
        return normalized_state

    @staticmethod
    def _primary_path():
        configured = Path(os.environ.get('XDG_CONFIG_HOME', ''))
        base = configured if configured.is_absolute() else Path.home() / '.config'
        return (base / 'hypr/primary_monitor').resolve()

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        if not changes:
            return True, 'No pending changes.', ''
        with self._lock:
            try:
                return self._write_monitor_batch(changes)
            except (OSError, UnicodeError, ValueError, TypeError, OverflowError) as exc:
                return False, f'Monitor edit failed: {exc}', ''

    def _write_monitor_batch(self, changes):
        if not self.loaded_files and self.config_path.exists():
            self.load_state()
            if not self.loaded_files:
                return False, 'Cannot evaluate monitor configuration.', ''
        primary = None
        translated = {}
        requested_modes = {self._scope_map.get(scope, scope): str(value) for key, scope, value, _ in changes if key == 'mode'}
        for key, scope, value, kind in changes:
            if key == 'primary_monitor' and scope in ('DEFAULT', 'workspace', 'globals'):
                primary = str(value).strip()
                if any(c in primary for c in '\n\r\0'):
                    raise ValueError('Invalid primary monitor name.')
                continue
            ast_scope = self._scope_map.get(scope, scope)
            deleting = value is None or str(value) in {'nil', '__DELETE__'}
            if ast_scope.startswith('monitor/') and key == 'enabled':
                key = 'disabled'
                if not deleting:
                    value, kind = str(not boolean(value)).lower(), 'bool'
            if ast_scope.startswith('monitor/') and not deleting:
                if key in ('transform', 'vrr', 'bitdepth', 'reserved_area'):
                    value, kind = str(int(value)), 'int'
                if key == 'scale':
                    if str(value) == 'auto':
                        kind = 'string'
                    else:
                        scale = float(value)
                        if not math.isfinite(scale) or scale <= 0:
                            raise ValueError('Scale must be finite and positive; use auto for automatic scaling.')
                        resolution = self._monitor_resolutions.get(scope.removeprefix('monitor/'))
                        mode = requested_modes.get(ast_scope, getattr(self, '_ast_state', {}).get(ast_scope + '/mode', ''))
                        if match := re.fullmatch(r'([0-9]+)x([0-9]+)(?:@[^\s]+)?', str(mode)):
                            resolution = int(match[1]), int(match[2])
                        value = self._get_valid_scale(scale, *resolution) if resolution else str(scale)
                        kind = 'float'
                if key in ('sdrbrightness', 'sdrsaturation', 'sdr_min_luminance', 'sdr_max_luminance', 'min_luminance', 'max_luminance', 'max_avg_luminance'):
                    value, kind = str(float(value)), 'float'
            translated[key, ast_scope] = (key, ast_scope, value, kind)

        def transform(content):
            edits, additions = [], []
            grouped = {}
            for key, scope, value, kind in translated.values():
                if f'{scope}/{key}' not in getattr(self, '_ast_state', {}):
                    grouped.setdefault(scope, []).append(key)
            for scope, keys in grouped.items():
                method, sep, identity = scope.partition('/')
                if method in ('monitor', 'workspace_rule') and sep:
                    sites = [c for c in self._call_sites if c['method'] == method and c['id'] == identity]
                    if sites:
                        candidates = []
                        for a, b, first, last in calls(content, method):
                            line = content.count('\n', 0, a) + 1
                            if any(c['file'] == str(self.config_path) and c['line'] == line for c in sites):
                                candidates.append((a, b, content[a:b]))
                        if len(candidates) != 1 or len(sites) != 1:
                            raise ValueError(f'Cannot uniquely edit missing fields in {scope}.')
                        a, b, block = candidates[0]
                        for key in keys:
                            block = replace_field(block, key, 'nil')
                        edits.append((a, b, block))
                    else:
                        id_key = 'output' if method == 'monitor' else 'workspace'
                        entries = [f'{id_key} = {self._lua_string(identity)}']
                        entries.extend(f'[{self._lua_string(key)}] = nil' for key in keys)
                        additions.append(f'hl.{method}({{{", ".join(entries)}}})')
                elif method in ('misc', 'debug', 'render') and not sep:
                    entries = ', '.join(f'[{self._lua_string(key)}] = nil' for key in keys)
                    additions.append(f'hl.config({{{method} = {{{entries}}}}})')
            for a, b, block in sorted(edits, reverse=True):
                content = content[:a] + block + content[b:]
            return content + ''.join('\n' + block + '\n' for block in additions)

        if translated:
            missing = any(f'{scope}/{key}' not in getattr(self, '_ast_state', {}) for key, scope, _, _ in translated.values())
            result = self.write_transformed(list(translated.values()), transform) if missing else super().write_batch(list(translated.values()))
            if not result[0]:
                return result
        if primary is not None:
            path = self._primary_path()
            try:
                _, expected = read_text(path)
                atomic_write(path, primary + '\n', expected)
                self.cache['DEFAULT/primary_monitor'] = primary
            except (OSError, UnicodeError) as exc:
                context = ' (Lua changes already saved)' if translated else ''
                return False, f'Primary monitor save failed{context}: {exc}', ''

        # Ensure any newly enabled monitors are powered on from DPMS sleep
        enabled_monitors = [
            scope.removeprefix('monitor/') for key, scope, value, _ in changes
            if scope.startswith('monitor/') and key == 'enabled' and boolean(value)
        ]
        if enabled_monitors:
            wake_displays(enabled_monitors)

        return True, 'Monitor configuration saved.', ''
