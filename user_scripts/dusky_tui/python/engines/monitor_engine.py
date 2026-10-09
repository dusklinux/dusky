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

lazy import math
lazy from math.integer import gcd
lazy import json
import re
lazy import subprocess
lazy from pathlib import Path
lazy from typing import Any

from python.engines.lua import HyprlandLuaEngine

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
    if scale <= 0 or width <= 0 or height <= 0:
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
    if width <= 0 or height <= 0 or requested <= 0:
        return 1.0
    divisor = gcd(width * HYPRLAND_SCALE_STEPS, height * HYPRLAND_SCALE_STEPS)
    units = int(round(requested * HYPRLAND_SCALE_STEPS))
    if units > divisor:
        units = divisor
    if units < 1:
        units = 1
    while divisor % units != 0:
        units += 1
    return round(units / HYPRLAND_SCALE_STEPS, PRECISION)


def closest_sharp(width: int, height: int, requested: float, min_scale: float = 0.5, max_scale: float = 4.0) -> float:
    """Finds the sharp scale closest to the requested float."""
    if width <= 0 or height <= 0 or requested <= 0:
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
    fmt = f"{value:.4f}".rstrip("0").rstrip(".")
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

            self._monitor_metadata[name] = {
                "make": m.get("make", ""),
                "model": m.get("model", ""),
                "serial": m.get("serial", ""),
                "description": desc,
                "ppi": ppi,
                "diagonal_inches": diag_in,
                "bitdepth": detected_bpc,
                "refresh_rate": float(m.get("refreshRate", 60.0)),
                "current_mode": f"{phys_w}x{phys_h}@{float(m.get('refreshRate', 60.0)):.2f}"
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
                "vrr": str(m.get("vrr", 0)),
                "bitdepth": str(detected_bpc),
                "cm": m.get("colorManagementPreset", "auto") or "auto",
                "sdr_eotf": "default",
                "sdrbrightness": str(m.get("sdrBrightness", 1.0)),
                "sdrsaturation": str(m.get("sdrSaturation", 1.0)),
                "mirror": "",
                "icc": "",
                "reserved_area": 0,
                "supports_wide_color": "0",
                "supports_hdr": "0",
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
        primary_monitor = available_monitor_names[0] if available_monitor_names else "eDP-1"
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
        primary_file = Path("~/.config/hypr/primary_monitor").expanduser()
        if primary_file.exists():
            try:
                cached_pri = primary_file.read_text(encoding="utf-8").strip()
                if cached_pri:
                    primary_monitor = cached_pri
            except Exception:
                pass
        normalized_state["DEFAULT/primary_monitor"] = primary_monitor

        self.cache = normalized_state
        return normalized_state

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        standard_changes = []
        primary_mon_to_save: str | None = None
        for key, scope, val, itype in changes:
            if key == "primary_monitor" and scope in ("DEFAULT", "workspace", "globals"):
                primary_mon_to_save = str(val).strip()
            else:
                standard_changes.append((key, scope, val, itype))

        if primary_mon_to_save is not None:
            try:
                pri_path = Path("~/.config/hypr/primary_monitor").expanduser()
                pri_path.parent.mkdir(parents=True, exist_ok=True)
                pri_path.write_text(f"{primary_mon_to_save}\n", encoding="utf-8")
                self.cache["DEFAULT/primary_monitor"] = primary_mon_to_save
            except Exception:
                pass

        if not standard_changes:
            return True, "Primary monitor configuration updated", ""

        changes = standard_changes
        translated_changes = []
        required_ast_scopes: set[str] = set()
        required_ws_scopes: set[str] = set()
        missing_monitor_keys: dict[str, list[tuple[str, str]]] = {}

        for key, scope, val, itype in changes:
            ast_scope = self._scope_map.get(scope, scope)

            # Map virtual 'enabled' key to Hyprland's native 'disabled' property
            if key == "enabled" and ast_scope.startswith("monitor/"):
                key = "disabled"
                is_on = str(val).lower() in ("true", "1", "yes", "on", "t", "y")
                val = "false" if is_on else "true"
                itype = "bool"

            # Enforce Lua integer types for compositor fields
            if key in ("transform", "vrr", "bitdepth", "reserved_area") and ast_scope.startswith("monitor/"):
                itype = "int"
                try:
                    val = str(int(val))
                except (ValueError, TypeError):
                    val = "0"

            # --- FRACTIONAL SCALING VALIDATION & HYPRLAND QUANTUM ENFORCEMENT ---
            if key == "scale" and ast_scope.startswith("monitor/"):
                mon_name = ast_scope.split("/")[1] if len(ast_scope.split("/")) >= 2 else ""
                if isinstance(val, (int, float)) or (isinstance(val, str) and val not in ("auto", "preferred", "highres", "highrr")):
                    try:
                        scale_val = float(val)
                        phys_w, phys_h = self._monitor_resolutions.get(mon_name, (1920, 1080))
                        val = self._get_valid_scale(scale_val, phys_w, phys_h)
                        itype = "float"
                    except ValueError:
                        pass

            # Enforce Lua float types for color/luminance fields
            if key in ("sdrbrightness", "sdrsaturation", "sdr_min_luminance", "sdr_max_luminance", "min_luminance", "max_luminance", "max_avg_luminance") and ast_scope.startswith("monitor/"):
                itype = "float"
                try:
                    val = str(float(val))
                except (ValueError, TypeError):
                    pass

            translated_changes.append((key, ast_scope, val, itype))

            if ast_scope.startswith("monitor/"):
                parts = ast_scope.split("/")
                if len(parts) >= 2:
                    mon_id = parts[1]
                    required_ast_scopes.add(mon_id)
                    if mon_id not in missing_monitor_keys:
                        missing_monitor_keys[mon_id] = []
                    missing_monitor_keys[mon_id].append((key, str(val)))

            elif ast_scope.startswith("workspace_rule/"):
                parts = ast_scope.split("/")
                if len(parts) >= 2:
                    required_ws_scopes.add(parts[1])

        current_ast_state = super().load_state()

        # 1. Ensure monitor blocks exist for entirely new monitors
        if required_ast_scopes:
            existing_outputs = set()
            for k in current_ast_state.keys():
                if k.startswith("monitor/"):
                    parts = k.split("/")
                    if len(parts) >= 2:
                        existing_outputs.add(parts[1])
            missing_monitors = required_ast_scopes - existing_outputs
            if missing_monitors:
                self._ensure_monitor_blocks_exist(missing_monitors)
                current_ast_state = super().load_state()

        # 2. Ensure missing keys inside existing uncommented monitor blocks are injected safely
        if self.config_path.exists() and missing_monitor_keys:
            self._ensure_monitor_keys_exist(missing_monitor_keys, current_ast_state)

        # 3. Ensure workspace rule blocks exist
        if required_ws_scopes:
            existing_ws = set()
            for k in current_ast_state.keys():
                if k.startswith("workspace_rule/"):
                    parts = k.split("/")
                    if len(parts) >= 2:
                        existing_ws.add(parts[1])
            missing_workspaces = required_ws_scopes - existing_ws
            if missing_workspaces:
                self._ensure_workspace_rule_blocks_exist(missing_workspaces)

        # 4. Inject missing global keys via hl.config deep merging
        missing_globals: dict[str, list[str]] = {}
        for key, ast_scope, val, itype in translated_changes:
            if ast_scope in ("misc", "debug", "render"):
                state_key = f"{ast_scope}/{key}"
                if state_key not in current_ast_state:
                    if ast_scope not in missing_globals:
                        missing_globals[ast_scope] = []
                    missing_globals[ast_scope].append(key)

        if missing_globals:
            self._ensure_globals_block_exists(missing_globals)

        return super().write_batch(translated_changes)

    def _ensure_monitor_keys_exist(self, missing_keys_by_output: dict[str, list[tuple[str, str]]], current_ast_state: dict[str, Any]) -> None:
        """Injects missing property fields into active uncommented hl.monitor tables so the AST mutator finds them."""
        try:
            content = self.config_path.read_text(encoding="utf-8")
        except Exception:
            return

        modified = False
        for mon_id, key_vals in missing_keys_by_output.items():
            for key, val_repr in key_vals:
                state_key = f"monitor/{mon_id}/{key}"
                if state_key not in current_ast_state:
                    if val_repr in ("true", "false") or key in ("disabled", "vrr", "bitdepth", "transform", "reserved_area"):
                        lua_val = val_repr
                    elif key in ("scale", "sdrbrightness", "sdrsaturation", "sdr_min_luminance", "sdr_max_luminance", "min_luminance", "max_luminance", "max_avg_luminance") and val_repr not in ("auto", "preferred", "highres", "highrr"):
                        try:
                            float(val_repr)
                            lua_val = val_repr
                        except ValueError:
                            lua_val = json.dumps(val_repr)
                    else:
                        lua_val = json.dumps(val_repr)

                    # Only match uncommented, active hl.monitor lines (line-anchored)
                    pattern = r'(?m)^([ \t]*hl\.monitor\s*\(\s*\{[^}]*?output\s*=\s*[\"\']' + re.escape(mon_id) + r'[\"\'][^}]*?)(\})'
                    def repl(m, k=key, v=lua_val):
                        block = m.group(1)
                        if re.search(r'\b' + re.escape(k) + r'\s*=', block):
                            return m.group(0)
                        clean_block = block.rstrip()
                        comma_prefix = "" if clean_block.endswith(",") else ","
                        return clean_block + comma_prefix + '\n    ' + k + ' = ' + v + '\n' + m.group(2)

                    new_content, count = re.subn(pattern, repl, content)
                    if count > 0:
                        content = new_content
                        modified = True

        if modified:
            self.config_path.write_text(content, encoding="utf-8")
            self.file_mtimes[str(self.config_path)] = self.config_path.stat().st_mtime

    def _ensure_monitor_blocks_exist(self, missing_monitors: set[str]) -> None:
        if not self.config_path.exists():
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text("-- Auto-generated Configuration\n\n")

        append_text = ""
        for mon in missing_monitors:
            append_text += (
                f"\n-- Auto-injected by Dusky Monitor Engine\n"
                f"hl.monitor({{\n"
                f"    output = \"{mon}\",\n"
                f"    mode = \"preferred\",\n"
                f"    position = \"auto\",\n"
                f"    scale = \"auto\",\n"
                f"    disabled = false,\n"
                f"    transform = 0,\n"
                f"    bitdepth = 8,\n"
                f"    cm = \"auto\",\n"
                f"    sdr_eotf = \"default\",\n"
                f"    sdrbrightness = 1.0,\n"
                f"    sdrsaturation = 1.0,\n"
                f"    vrr = 0,\n"
                f"    reserved_area = 0\n"
                f"}})\n"
            )

        if append_text:
            with open(self.config_path, "a", encoding="utf-8") as f:
                f.write(append_text)
            self.file_mtimes[str(self.config_path)] = self.config_path.stat().st_mtime

    def _ensure_workspace_rule_blocks_exist(self, missing_workspaces: set[str], default_monitor: str = "eDP-1") -> None:
        if not self.config_path.exists():
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text("-- Auto-generated Configuration\n\n")

        if self._monitor_resolutions:
            default_monitor = next(iter(self._monitor_resolutions.keys()))

        append_text = ""
        for ws in sorted(missing_workspaces, key=lambda x: int(x) if x.isdigit() else 999):
            append_text += (
                f"\n-- Auto-injected Workspace Binding\n"
                f"hl.workspace_rule({{\n"
                f"    workspace = \"{ws}\",\n"
                f"    monitor = \"{default_monitor}\",\n"
                f"    default = {'true' if ws == '1' else 'false'},\n"
                f"    persistent = false\n"
                f"}})\n"
            )

        if append_text:
            with open(self.config_path, "a", encoding="utf-8") as f:
                f.write(append_text)
            self.file_mtimes[str(self.config_path)] = self.config_path.stat().st_mtime

    def _ensure_globals_block_exists(self, missing_globals: dict[str, list[str]] = None) -> None:
        if not self.config_path.exists():
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text("-- Auto-generated Configuration\n\n")

        append_text = ""
        if missing_globals:
            append_text += "\n-- Auto-injected missing globals\nhl.config({\n"
            for scope, keys in missing_globals.items():
                append_text += f"    {scope} = {{\n"
                for k in keys:
                    append_text += f"        {k} = 0,\n"
                append_text += "    },\n"
            append_text += "})\n"
        else:
            with open(self.config_path, "r", encoding="utf-8") as f:
                content = f.read()
            if "hl.config" not in content:
                append_text = (
                    "\n-- Auto-injected Global Render & Power Settings\n"
                    "hl.config({\n"
                    "    misc = { vrr = 0 },\n"
                    "    debug = { vfr = true },\n"
                    "    render = { cm_sdr_eotf = \"auto\", cm_fs_passthrough = false, cm_auto_hdr = false }\n"
                    "})\n"
                )

        if append_text:
            with open(self.config_path, "a", encoding="utf-8") as f:
                f.write(append_text)
            self.file_mtimes[str(self.config_path)] = self.config_path.stat().st_mtime
