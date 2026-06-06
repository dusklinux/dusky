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
# [ WAYBAR ENGINE - v4.8.7 PARITY ]
# Fully isolated Python process controller with UI-Cache synchronization.
# Resolves Headless Async Destruction & AST Regex Position Mutators.
# =============================================================================

class WaybarEngine(BaseEngine):
    def __init__(self, config_path: str = "~/.config/waybar"):
        self.config_path = Path(config_path).expanduser().absolute()
        
        if self.config_path.name == "config.jsonc":
            self.config_root = self.config_path.parent
        else:
            self.config_root = self.config_path
            self.config_path = self.config_root / "config.jsonc"
            
        self.style_path = self.config_root / "style.css"
        
        # --- NEW STATE FILE LOCATION ---
        state_dir = Path("~/.config/dusky/settings/waybar").expanduser().resolve()
        state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = state_dir / ".dusky_waybar_state.json"
        
        self.cache: dict[str, Any] = {}
        self.theme_dirs: list[Path] = []
        self.theme_names: list[str] = []
        self._preview_task = None
        
        # Regex to replicate the bash `sed` position replacer strictly
        self.pos_regex = re.compile(r'("position"\s*:\s*)"([^"]+)"')

    @property
    def target_path(self) -> str:
        # CRITICAL FIX: We pass the DIRECTORY to the UI File Watcher, not the symlink.
        # This guarantees that our asynchronous os.utime() trigger below successfully 
        # forces the UI to reload and wipe the old states.
        return str(self.config_root)

    def _refresh_themes(self) -> None:
        themes = sorted(self.config_root.glob("*/config.jsonc"))
        self.theme_dirs = [t.parent for t in themes]
        self.theme_names = [t.parent.name for t in themes]

    def _get_theme_position(self, config_file: Path) -> str:
        resolved_file = config_file.resolve()
        if not resolved_file.exists():
            return "unknown"
        try:
            content = resolved_file.read_text(encoding="utf-8")
            match = self.pos_regex.search(content)
            return match.group(2).lower() if match else "unknown"
        except OSError:
            return "unknown"

    def _set_theme_position(self, config_file: Path, new_pos: str) -> bool:
        resolved_file = config_file.resolve()
        if not resolved_file.exists():
            return False
        try:
            content = resolved_file.read_text(encoding="utf-8")
            if not self.pos_regex.search(content):
                return False 
                
            # Safely replace only the FIRST occurrence (main bar), preserving module positions
            # \g<1> safely backreferences the regex group in Python
            new_content = self.pos_regex.sub(rf'\g<1>"{new_pos}"', content, count=1)
            resolved_file.write_text(new_content, encoding="utf-8")
            return True
        except OSError:
            return False

    def load_state(self) -> dict[str, Any]:
        self._refresh_themes()
        
        active_idx = -1
        active_name = "unknown"
        
        if self.config_path.is_symlink():
            target = self.config_path.resolve()
            # Failsafe: evaluate target.parent even if config.jsonc is missing inside it
            if target.parent in self.theme_dirs:
                active_idx = self.theme_dirs.index(target.parent)
                active_name = self.theme_names[active_idx]
                
        # Critical patch: Check the state file if symlink failed to match (e.g., folder was renamed).
        # We DO NOT apply symlinks here. We simply recover the index so the UI knows where we left off.
        # This stops the "automatic symlink changing" behavior and allows the manual "Heal" action to do its job.
        if active_idx == -1 and self.state_file.exists():
            try:
                state_data = json.loads(self.state_file.read_text(encoding="utf-8"))
                saved_idx = state_data.get("active_theme_index", -1)
                
                if 0 <= saved_idx < len(self.theme_names):
                    active_name = self.theme_names[saved_idx]
                    active_idx = saved_idx
            except (OSError, json.JSONDecodeError):
                pass
        
        active_number = active_idx + 1 if active_idx >= 0 else 1

        # PREVENTING [Missing] STRIKETHROUGH:
        # We explicitly lock the momentary push-button states to False on every load.
        # This brilliantly ensures the UI Preset engine never thinks they are "Active"
        # and always shows the "Apply" button ready to be clicked again.
        self.cache = {
            "active_theme_index": active_idx,
            "active_theme_name": active_name,
            "active_theme_number": active_number,
            "waybar": active_number,
            "DEFAULT/active_theme_index": active_idx,
            "DEFAULT/active_theme_name": active_name,
            "DEFAULT/active_theme_number": active_number,
            "DEFAULT/waybar": active_number,
            
            "action_invert_pos": False,
            "DEFAULT/action_invert_pos": False,
            
            "action_heal_state": False,
            "DEFAULT/action_heal_state": False,
        }
        
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
        
        try:
            proc = await asyncio.create_subprocess_exec("pkill", "-x", "waybar", stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            await proc.wait()
        except OSError:
            pass
        
        for _ in range(15):
            try:
                check_proc = await asyncio.create_subprocess_exec("pgrep", "-x", "waybar", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                await check_proc.wait()
                if check_proc.returncode != 0:
                    break
            except OSError:
                break
            await asyncio.sleep(0.1)
            
        try:
            proc = await asyncio.create_subprocess_exec("pkill", "-9", "-x", "waybar", stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            await proc.wait()
        except OSError:
            pass
            
        await asyncio.sleep(0.2)
        
        # Removed uwsm-app wrapper to prevent supervisor respawning bugs that freeze waybar_toggle.sh.
        # Launch waybar exactly identically to how it runs manually in the terminal.
        try:
            subprocess.Popen(
                ["waybar"],
                start_new_session=set_sid,       
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL
            )
        except OSError:
            pass

        # UI CACHE REFRESH TRIGGER
        # Pauses slightly to let the UI's write mask expire, then artificially 
        # bumps the directory mtime. This brilliantly forces the UI to reload the state natively.
        await asyncio.sleep(0.5)
        try:
            os.utime(self.config_root, None)
        except OSError:
            pass

    def write_value(self, target_key: str, target_scope: str, new_value: str, item_type: str = "string") -> tuple[bool, str, str]:
        return self.write_batch([(target_key, target_scope, new_value, item_type)])

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        self.load_state()
        
        if not self.theme_dirs:
            return False, "No valid themes found in ~/.config/waybar/", ""

        # PREVENT NEGATIVE INDEXING BUG: If symlinks are broken, fallback to 0. 
        # This prevents the script from accidentally altering the very last theme in the folder.
        current_idx = self.cache.get("active_theme_index", -1)
        if current_idx < 0:
            current_idx = 0
            
        target_idx = current_idx
        requires_restart = False
        requires_detached = True 
        status_msg = ""
        
        for key, scope, val, itype in changes:
            str_val = str(val).lower()
            
            match key:
                case "active_theme_number" | "waybar":
                    try:
                        target_idx = int(val) - 1
                        if 0 <= target_idx < len(self.theme_dirs):
                            requires_restart = True
                            requires_detached = True
                        else:
                            return False, f"Theme number {val} is out of bounds.", ""
                    except ValueError:
                        return False, f"Invalid theme number: {val}", ""

                case "active_theme_name":
                    target_name = str(val)
                    if target_name in self.theme_names:
                        target_idx = self.theme_names.index(target_name)
                        requires_restart = True
                        requires_detached = True  # Survives terminal closure
                    else:
                        # Fallback parsing to allow chronological index passing directly via strings (e.g., CLI --apply 10)
                        try:
                            target_idx = int(target_name) - 1
                            if 0 <= target_idx < len(self.theme_dirs):
                                requires_restart = True
                                requires_detached = True
                            else:
                                return False, f"Theme number '{val}' out of bounds.", ""
                        except ValueError:
                            return False, f"Theme '{target_name}' not found.", ""
                        
                case "toggle_forward" if str_val == "true":
                    target_idx = (current_idx + 1) % len(self.theme_dirs)
                    requires_restart = True
                    
                case "toggle_backward" if str_val == "true":
                    target_idx = (current_idx - 1 + len(self.theme_dirs)) % len(self.theme_dirs)
                    requires_restart = True
                    
                case "active_theme_index":
                    try:
                        target_idx = int(val)
                        requires_restart = True
                        requires_detached = True
                    except ValueError:
                        return False, f"Invalid numeric index: {val}", ""
                        
                case "action_invert_pos" if str_val == "true":
                    resolved_target = self.theme_dirs[target_idx] / "config.jsonc"
                    current_pos = self._get_theme_position(resolved_target)
                    
                    if current_pos == "top": target_pos = "bottom"
                    elif current_pos == "bottom": target_pos = "top"
                    elif current_pos == "left": target_pos = "right"
                    elif current_pos == "right": target_pos = "left"
                    else: target_pos = "bottom"
                    
                    if self._set_theme_position(resolved_target, target_pos):
                        requires_restart = True
                        requires_detached = True
                        status_msg = f"Position inverted to {target_pos.upper()}."
                    else:
                        return False, "Position key not found in target config.jsonc", ""
                        
                case "action_heal_state" if str_val == "true":
                    # Forcefully read the state file to override the symlink
                    if self.state_file.exists():
                        try:
                            state_data = json.loads(self.state_file.read_text(encoding="utf-8"))
                            saved_name = state_data.get("active_theme_name")
                            saved_idx = state_data.get("active_theme_index", -1)
                            
                            if saved_name and saved_name in self.theme_names:
                                target_idx = self.theme_names.index(saved_name)
                            elif 0 <= saved_idx < len(self.theme_dirs):
                                target_idx = saved_idx
                        except (OSError, json.JSONDecodeError):
                            pass
                            
                    requires_restart = True 
                    requires_detached = True
                    status_msg = "State restored from file and symlinks healed."

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
                try:
                    loop = asyncio.get_running_loop()
                    if self._preview_task and not self._preview_task.done():
                        self._preview_task.cancel()
                    self._preview_task = loop.create_task(self._async_restart_waybar(selected_dir, set_sid=requires_detached))
                except RuntimeError:
                    asyncio.run(self._async_restart_waybar(selected_dir, set_sid=requires_detached))
                
                if not status_msg:
                    status_msg = f"Applied theme: {selected_name}"
            except Exception as e:
                return False, f"Symlinks created but failed to restart waybar: {e}", ""

        return True, status_msg, ""
