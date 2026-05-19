#!/usr/bin/env python3
import re
import json
import subprocess
from pathlib import Path
from typing import Any

from python.engines.lua import HyprlandLuaEngine

class MonitorLuaEngine(HyprlandLuaEngine):
    """
    Specialized gatekeeper for Hyprland 0.55+ monitors. 
    Injects Virtual Hardware State so partial Lua blocks don't cause 
    the TUI to mark valid default values as [Missing]. Handles Globals securely.
    """
    def __init__(self, config_path: str = "~/Documents/monitors.lua"):
        expanded_path = str(Path(config_path).expanduser().resolve())
        super().__init__(config_path=expanded_path)
        self._scope_map: dict[str, str] = {}
        # Stores exact physical resolutions for scale validation
        self._monitor_resolutions: dict[str, tuple[int, int]] = {}

    def _get_valid_scale(self, requested_scale: float, phys_w: int, phys_h: int) -> str:
        """
        Hyprland fractional scaling math: "A valid scale must divide your resolution cleanly".
        Calculates the nearest mathematically perfect scale to prevent the annoying yellow warning.
        """
        import math
        if requested_scale <= 0.1: return str(requested_scale)
        
        lw = phys_w / requested_scale
        lh = phys_h / requested_scale
        
        # Check if requested scale is already perfect (within a tiny float epsilon)
        if abs(lw - round(lw)) <= 0.001 and abs(lh - round(lh)) <= 0.001:
            fmt = f"{requested_scale:.6f}".rstrip("0").rstrip(".")
            return fmt if fmt else "1"
            
        # The ONLY mathematically perfect scales are of the form gcd(W, H) / k
        g = math.gcd(phys_w, phys_h)
        k_float = g / requested_scale
        
        k_low = math.floor(k_float)
        k_high = math.ceil(k_float)
        
        candidates = []
        if k_low > 0: candidates.append(g / k_low)
        if k_high > 0: candidates.append(g / k_high)
        
        if not candidates:
            return str(requested_scale)
            
        best_scale = min(candidates, key=lambda s: abs(s - requested_scale))
        fmt = f"{best_scale:.6f}".rstrip("0").rstrip(".")
        return fmt if fmt else "1"

    def load_state(self) -> dict[str, Any]:
        state = super().load_state()
        self._scope_map.clear()
        self._monitor_resolutions.clear()
        
        try:
            res = subprocess.run(["hyprctl", "-j", "monitors", "all"], capture_output=True, text=True, timeout=2)
            raw = res.stdout.strip()
            if raw and not raw[0] in ("[", "{"):
                for i, line in enumerate(raw.splitlines()):
                    if line.strip().startswith(("[", "{")):
                        raw = "\n".join(raw.splitlines()[i:])
                        break
            live_monitors = json.loads(raw)
        except Exception:
            live_monitors = []

        normalized_state = {}
        
        for m in live_monitors:
            name = m.get("name", "")
            desc = m.get("description", "")
            if not name: continue
            
            # Scrape hardware resolution to fuel the scale validator
            phys_w = int(m.get("width", 1920))
            phys_h = int(m.get("height", 1080))
            if phys_w <= 0: phys_w = 1920
            if phys_h <= 0: phys_h = 1080
            self._monitor_resolutions[name] = (phys_w, phys_h)
            
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
                    if k[len(prefix):] == "reserved_area" and isinstance(v, dict):
                        normalized_state[f"{ui_scope}/{k[len(prefix):]}"] = 0
                    else:
                        normalized_state[f"{ui_scope}/{k[len(prefix):]}"] = v

            defaults = {
                "output": ast_scope.split("/")[1], 
                "disabled": m.get("disabled", False),
                "mode": "preferred",
                "position": "auto",
                "scale": "auto",
                "transform": str(m.get("transform", 0)),
                "vrr": str(m.get("vrr", 0)),
                "bitdepth": str(10 if "101010" in m.get("currentFormat", "") else 8),
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

        # Preserve AST config blocks for monitors that are offline/unplugged
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

        global_defaults = {
            "debug/vfr": True,
            "misc/vrr": "0",
            "render/cm_sdr_eotf": "auto",
            "render/cm_fs_passthrough": False,
            "render/cm_auto_hdr": False
        }
        for k, v in global_defaults.items():
            if k not in normalized_state:
                normalized_state[k] = v
                
        self.cache = normalized_state
        return normalized_state

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        translated_changes = []
        required_ast_scopes = set()
        
        for key, scope, val, itype in changes:
            ast_scope = self._scope_map.get(scope, scope)
            
            # --- FRACTIONAL SCALING VALIDATION & FIX ---
            # Automatically coerce invalid generic scales into mathematically pristine logical coordinates
            if key == "scale" and scope.startswith("monitor/"):
                mon_name = scope.split("/")[1] if len(scope.split("/")) >= 2 else ""
                
                # We skip things like 'auto' or 'preferred' as they are not floats
                if isinstance(val, (int, float)) or (isinstance(val, str) and val not in ("auto", "preferred", "highres", "highrr")):
                    try:
                        scale_val = float(val)
                        phys_w, phys_h = self._monitor_resolutions.get(mon_name, (1920, 1080))
                        val = self._get_valid_scale(scale_val, phys_w, phys_h)
                    except ValueError:
                        pass
                        
            translated_changes.append((key, ast_scope, val, itype))
            
            if ast_scope.startswith("monitor/"):
                parts = ast_scope.split("/")
                if len(parts) >= 2:
                    required_ast_scopes.add(parts[1])

        current_ast_state = super().load_state()

        if required_ast_scopes:
            existing_outputs = set()
            for k in current_ast_state.keys():
                if k.startswith("monitor/"):
                    parts = k.split("/")
                    if len(parts) >= 2:
                        existing_outputs.add(parts[1])
                        
            missing = required_ast_scopes - existing_outputs
            if missing:
                self._ensure_monitor_blocks_exist(missing)

        # Inject missing global keys via hl.config merging so the Lua mutator can find them
        missing_globals = {}
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

    def _ensure_monitor_blocks_exist(self, missing_monitors: set[str]) -> None:
        if not self.config_path.exists():
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text("-- Auto-generated Configuration\n\n")

        append_text = ""
        for mon in missing_monitors:
            # Safely create a pristine block so the Lua AST mutator can update it.
            append_text += (
                f"\n-- Auto-injected by Dusky Monitor Engine\n"
                f"hl.monitor({{\n"
                f"    output = \"{mon}\",\n"
                f"    mode = \"preferred\",\n"
                f"    position = \"auto\",\n"
                f"    scale = \"auto\",\n"
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

    def _ensure_globals_block_exists(self, missing_globals: dict[str, list[str]] = None) -> None:
        if not self.config_path.exists():
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text("-- Auto-generated Configuration\n\n")
            
        append_text = ""
        
        if missing_globals:
            # Leverage Hyprland's `hl.config` deep-merging behavior.
            append_text += "\n-- Auto-injected missing globals\nhl.config({\n"
            for scope, keys in missing_globals.items():
                append_text += f"    {scope} = {{\n"
                for k in keys:
                    # Provide a dummy value; the Lua mutator will immediately overwrite it on the next pass
                    append_text += f"        {k} = 0,\n"
                append_text += "    },\n"
            append_text += "})\n"
        else:
            # Safe fallback if called without specific globals
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
