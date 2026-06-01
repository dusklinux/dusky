#!/usr/bin/env python3
import os
import json
import time
import re
import asyncio
import subprocess
from pathlib import Path
from typing import Any

from python.frontend.core_types import BaseEngine

# =============================================================================
# [ WAYBAR ENGINE - v4.8.3 PARITY ]
# Fully isolated Python process controller with UI-Cache synchronization.
# =============================================================================

class WaybarEngine(BaseEngine):
    def __init__(self, config_path: str = "~/.config/waybar/config.jsonc"):
        # STRICT ROOT BINDING: main.py aggressively resolves symlinks before passing config_path.
        # If the Waybar symlink is active, config_path points deep into the active theme's folder.
        # We must explicitly lock the root to the base directory to prevent the engine from getting trapped.
        self.config_root = Path("~/.config/waybar").expanduser().absolute()
        self.config_path = self.config_root / "config.jsonc"
            
        self.style_path = self.config_root / "style.css"
        self.state_file = self.config_root / ".dusky_waybar_state.json"
        
        self.cache: dict[str, Any] = {}
        self.theme_dirs: list[Path] = []
        self.theme_names: list[str] = []
        self._preview_task = None
        
        # Regex to replicate the bash `sed` position replacer strictly
        self.pos_regex = re.compile(r'("position"\s*:\s*)"([^"]+)"')

    @property
    def target_path(self) -> str:
        """Fulfills BaseEngine contract to supply the UI with the file path."""
        return str(self.config_path)

    def _refresh_themes(self) -> None:
        """Emulates Bash globbing: candidates=("${CONFIG_ROOT}"/*/config.jsonc)"""
        themes = sorted(self.config_root.glob("*/config.jsonc"))
        self.theme_dirs = [t.parent for t in themes]
        self.theme_names = [t.parent.name for t in themes]

    def _get_theme_position(self, config_file: Path) -> str:
        """Safely extracts the current 'position' from the jsonc file."""
        resolved_file = config_file.resolve()
        if not resolved_file.exists():
            return "unknown"
        try:
            content = resolved_file.read_text(encoding="utf-8")
            match = self.pos_regex.search(content)
            return match.group(2) if match else "unknown"
        except OSError:
            return "unknown"

    def _set_theme_position(self, config_file: Path, new_pos: str) -> bool:
        """Safely mutates the 'position' attribute inside the underlying jsonc file."""
        resolved_file = config_file.resolve()
        if not resolved_file.exists():
            return False
        try:
            content = resolved_file.read_text(encoding="utf-8")
            if not self.pos_regex.search(content):
                return False 
                
            new_content = self.pos_regex.sub(rf'\1"{new_pos}"', content)
            resolved_file.write_text(new_content, encoding="utf-8")
            return True
        except OSError:
            return False

    def load_state(self) -> dict[str, Any]:
        """Maps the current active symlink to its chronological array state."""
        self._refresh_themes()
        
        active_idx = -1
        active_name = ""
        current_pos = "unknown"
        
        # 1. Determine active state from the physical symlink target
        if self.config_path.is_symlink():
            target = self.config_path.resolve()
            if target.parent in self.theme_dirs:
                active_idx = self.theme_dirs.index(target.parent)
                active_name = self.theme_names[active_idx]
                current_pos = self._get_theme_position(target)
                
        # 2. AUTO-HEALING: If symlinks are broken, restore them
        elif self.state_file.exists():
            try:
                state_data = json.loads(self.state_file.read_text(encoding="utf-8"))
                saved_name = state_data.get("active_theme_name")
                if saved_name in self.theme_names:
                    active_name = saved_name
                    active_idx = self.theme_names.index(saved_name)
                    self._apply_symlinks_sync(self.theme_dirs[active_idx])
                    current_pos = self._get_theme_position(self.config_path.resolve())
            except (OSError, json.JSONDecodeError):
                pass
        
        self.cache = {
            "active_theme_index": active_idx,
            "active_theme_name": active_name,
            "waybar_position": current_pos,
        }
        
        # Inject dynamic menu state variables for the radio-button list
        for i, name in enumerate(self.theme_names):
            is_active = (active_name == name)
            self.cache[f"DEFAULT/__waybar_theme_{name}"] = is_active
            self.cache[f"__waybar_theme_{name}"] = is_active
            
        return self.cache

    def _apply_symlinks_sync(self, target_dir: Path) -> None:
        self.config_path.unlink(missing_ok=True)
        self.style_path.unlink(missing_ok=True)
        self.config_path.symlink_to(target_dir / "config.jsonc")
        target_style = target_dir / "style.css"
        if target_style.exists():
            self.style_path.symlink_to(target_style)

    async def _async_restart_waybar(self, target_dir: Path, set_sid: bool = True):
        self._apply_symlinks_sync(target_dir)
        
        proc = await asyncio.create_subprocess_exec("pkill", "-x", "waybar", stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        await proc.wait()
        
        for _ in range(15):
            check_proc = await asyncio.create_subprocess_exec("pgrep", "-x", "waybar", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            await check_proc.wait()
            if check_proc.returncode != 0:
                break
            await asyncio.sleep(0.1)
            
        proc = await asyncio.create_subprocess_exec("pkill", "-9", "-x", "waybar", stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        await proc.wait()
        await asyncio.sleep(0.2)
        
        try:
            uid = os.getuid()
            Path(f"/run/user/{uid}/uwsm-app.lock").unlink(missing_ok=True)
        except OSError:
            pass
        
        subprocess.Popen(
            ["uwsm-app", "--", "waybar"],
            start_new_session=set_sid,       
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL
        )

        # --- CRITICAL UI CACHE SYNC (RADIO BUTTON FIX) ---
        # The TUI masks mtime changes triggered by its own saves to prevent glitchy loops.
        # By waiting slightly and touching the RESOLVED target file AFTER the mask concludes,
        # we bypass the mask. The TUI detects this, rebuilds the cache natively, 
        # and successfully turns OFF all the inactive radio buttons!
        await asyncio.sleep(0.35)
        try:
            self.config_path.resolve().touch(exist_ok=True)
        except OSError:
            pass

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        self.load_state()
        
        if not self.theme_dirs:
            return False, "No valid themes found in ~/.config/waybar/", ""

        current_idx = self.cache.get("active_theme_index", 0)
        target_idx = current_idx
        requires_restart = False
        requires_detached = True 
        status_msg = ""
        
        for key, scope, val, itype in changes:
            str_val = str(val).lower()
            
            if key.startswith("__waybar_theme_"):
                if str_val == "false":
                    return False, "Theme is already active.", ""
                target_name = key.replace("__waybar_theme_", "")
                if target_name in self.theme_names:
                    target_idx = self.theme_names.index(target_name)
                    requires_restart = True
                    requires_detached = False
                    
            match key:
                case "toggle_forward" if str_val == "true":
                    target_idx = (current_idx + 1) % len(self.theme_dirs)
                    requires_restart = True
                    
                case "toggle_backward" if str_val == "true":
                    target_idx = (current_idx - 1 + len(self.theme_dirs)) % len(self.theme_dirs)
                    requires_restart = True
                        
                case "toggle_position":
                    resolved_target = self.theme_dirs[target_idx] / "config.jsonc"
                    current_pos = self._get_theme_position(resolved_target)
                    
                    # Strictly flip inverted opposites (Spacebar parity)
                    if current_pos == "top": target_pos = "bottom"
                    elif current_pos == "bottom": target_pos = "top"
                    elif current_pos == "left": target_pos = "right"
                    elif current_pos == "right": target_pos = "left"
                    else: target_pos = "top"
                    
                    if self._set_theme_position(resolved_target, target_pos):
                        requires_restart = True
                        requires_detached = True
                        status_msg = f"Position inverted to {target_pos.upper()}."
                    else:
                        return False, "Position key not found in target config.jsonc", ""
                        
                case "restore_state" if str_val == "true":
                    requires_restart = True 
                    requires_detached = True

        if target_idx < 0 or target_idx >= len(self.theme_dirs):
            return False, f"Index {target_idx} is out of bounds.", ""

        selected_dir = self.theme_dirs[target_idx]
        selected_name = self.theme_names[target_idx]

        if requires_restart:
            try:
                state_data = {
                    "active_theme_name": selected_name,
                    "active_theme_index": target_idx
                }
                self.state_file.write_text(json.dumps(state_data, indent=4), encoding="utf-8")
            except OSError:
                pass
            
            try:
                if self._preview_task and not self._preview_task.done():
                    self._preview_task.cancel()
                    
                self._preview_task = asyncio.create_task(self._async_restart_waybar(selected_dir, set_sid=requires_detached))
                
                if not status_msg:
                    status_msg = f"Applied theme: {selected_name}"
            except Exception as e:
                return False, f"Symlinks created but failed to restart waybar: {e}", ""

        return True, status_msg, ""
