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

    def load_state(self) -> dict[str, Any]:
        state = super().load_state()
        self._scope_map.clear()
        
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
                    # SAFETY CHECK: If a user has manually defined `reserved_area` as a Lua table,
                    # the lua AST engine parses it as a dict. Since our TUI casts it as an int,
                    # it will crash during deserialization. We intercept it here and coerce it to 0.
                    # Advanced users must edit the table in the raw file.
                    if k[len(prefix):] == "reserved_area" and isinstance(v, dict):
                        normalized_state[f"{ui_scope}/{k[len(prefix):]}"] = 0
                    else:
                        normalized_state[f"{ui_scope}/{k[len(prefix):]}"] = v

            # HIGH-STAKES UPDATE: 
            # Injecting the complete 0.55+ properties so they don't show up as [Missing] 
            # in the UI for users who haven't explicitly written them to their config yet.
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
                # --- Hyprland 0.55+ HDR / Luminance / ICC Defaults ---
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

        # Pass through the Global Variables untouched from the AST
        for k, v in state.items():
            if not k.startswith("monitor/"):
                normalized_state[k] = v
                
        # HIGH-STAKES UPDATE: Ensure Global Render settings don't show as [Missing] either
        global_defaults = {
            "debug/vfr": True,
            "misc/vrr": "0",
            "render/cm_sdr_eotf": "auto",
            "render/cm_auto_hdr": False
        }
        for k, v in global_defaults.items():
            if k not in normalized_state:
                normalized_state[k] = v
                
        return normalized_state

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        translated_changes = []
        required_ast_scopes = set()
        needs_globals = False
        
        for key, scope, val, itype in changes:
            ast_scope = self._scope_map.get(scope, scope)
            translated_changes.append((key, ast_scope, val, itype))
            
            if ast_scope.startswith("monitor/"):
                parts = ast_scope.split("/")
                if len(parts) >= 2:
                    required_ast_scopes.add(parts[1])
            elif ast_scope in ("misc", "debug", "render"):
                needs_globals = True

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

        # Safety Net: If the user deleted the hl.config block, recreate it before saving globals
        if needs_globals:
            has_globals_in_ast = any(k.startswith("misc/") or k.startswith("debug/") or k.startswith("render/") for k in current_ast_state.keys())
            if not has_globals_in_ast:
                self._ensure_globals_block_exists()

        return super().write_batch(translated_changes)

    def _ensure_monitor_blocks_exist(self, missing_monitors: set[str]) -> None:
        if not self.config_path.exists():
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text("-- Auto-generated Configuration\n\n")

        with open(self.config_path, "r", encoding="utf-8") as f:
            content = f.read()

        append_text = ""
        for mon in missing_monitors:
            pattern = rf'output\s*=\s*["\']{re.escape(mon)}["\']'
            if not re.search(pattern, content):
                append_text += (
                    f"\n-- Auto-injected by Dusky Monitor Engine\n"
                    f"hl.monitor({{\n"
                    f"    output = \"{mon}\",\n"
                    f"    mode = \"preferred\",\n"
                    f"    position = \"auto\",\n"
                    f"    scale = \"auto\"\n"
                    f"}})\n"
                )

        if append_text:
            with open(self.config_path, "a", encoding="utf-8") as f:
                f.write(append_text)
            self.file_mtimes[str(self.config_path)] = self.config_path.stat().st_mtime

    def _ensure_globals_block_exists(self) -> None:
        if not self.config_path.exists():
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text("-- Auto-generated Configuration\n\n")
            
        with open(self.config_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        if "hl.config" not in content:
            append_text = (
                "\n-- Auto-injected Global Render & Power Settings\n"
                "hl.config({\n"
                "    misc = { vrr = 0 },\n"
                "    debug = { vfr = true },\n"
                "    render = { cm_sdr_eotf = \"auto\", cm_auto_hdr = false }\n"
                "})\n"
            )
            with open(self.config_path, "a", encoding="utf-8") as f:
                f.write(append_text)
            self.file_mtimes[str(self.config_path)] = self.config_path.stat().st_mtime
