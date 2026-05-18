#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: HYBRID TRACKPAD ENGINE
===============================================================================
Bridges standard Hyprland Lua AST parsing for trackpad physics variables,
while strictly handling `hl.gesture()` function blocks via AST-bracket mapping.
===============================================================================
"""

import re
from pathlib import Path
from typing import Any
from python.engines.lua import HyprlandLuaEngine

# -----------------------------------------------------------------------------
# TRANSLATION MAP: UI Friendly Labels <-> Lua Backend Code
# -----------------------------------------------------------------------------
ACTION_MAP = {
    "Native Workspace Swipe": '"workspace"',
    "Toggle Dusky QuickPanel": 'function()\n        hl.exec_cmd([[gdbus call --session --dest org.dusky.quickpanal --object-path /org/dusky/quickpanal --method org.freedesktop.Application.Activate ""]])\n    end',
    "Toggle Waybar": 'function()\n        hl.exec_cmd(dusky_scripts .. "waybar/waybar_toggle.sh")\n    end',
    "Toggle Blur & Opacity": 'function()\n        hl.exec_cmd(dusky_scripts .. "hypr_blur_opacity_shadow_toggle.sh")\n    end',
    "Media: Play / Pause": 'function()\n        hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh --play-pause")\n    end',
    "Media: Volume Up (+10%)": 'function()\n        hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh --vol-up 10")\n    end',
    "Media: Volume Down (-10%)": 'function()\n        hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh --vol-down 10")\n    end',
    "Screen: Brightness Up (+10%)": 'function()\n        hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh --bright-up 10")\n    end',
    "Screen: Brightness Down (-10%)": 'function()\n        hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh --bright-down 10")\n    end',
    "Disabled / Unbound": '__DELETE__'
}

def get_friendly_name(block_str: str) -> str:
    """Safely extracts the UI label by scanning the raw Lua block for keywords."""
    if '"workspace"' in block_str or "'workspace'" in block_str: return "Native Workspace Swipe"
    if "org.dusky.quickpanal" in block_str: return "Toggle Dusky QuickPanel"
    if "waybar_toggle.sh" in block_str: return "Toggle Waybar"
    if "hypr_blur_opacity_shadow_toggle.sh" in block_str: return "Toggle Blur & Opacity"
    if "--play-pause" in block_str: return "Media: Play / Pause"
    if "--vol-up" in block_str: return "Media: Volume Up (+10%)"
    if "--vol-down" in block_str: return "Media: Volume Down (-10%)"
    if "--bright-up" in block_str: return "Screen: Brightness Up (+10%)"
    if "--bright-down" in block_str: return "Screen: Brightness Down (-10%)"
    return "Disabled / Unbound"

def find_gesture_blocks(content: str) -> list[tuple[int, int, str, str, str]]:
    """
    Bulletproof structural parser. Uses deterministic bracket counting while safely 
    ignoring braces trapped inside Lua strings (quotes and multi-line brackets).
    """
    blocks = []
    idx = 0
    while True:
        # Robustly locate the start of a gesture block regardless of spacing
        match = re.search(r'hl\.gesture\s*\(\s*\{', content[idx:])
        if not match: break
        
        start = idx + match.start()
        brace_start = idx + match.end() - 1 # Index of the opening '{'
        
        brace_count = 0
        end = -1
        in_str_double = False
        in_str_single = False
        in_multi = False
        
        i = brace_start
        while i < len(content):
            char = content[i]
            prev = content[i-1] if i > 0 else ''
            
            # String boundary tracking
            if not in_multi and not in_str_single and char == '"' and prev != '\\':
                in_str_double = not in_str_double
            elif not in_multi and not in_str_double and char == "'" and prev != '\\':
                in_str_single = not in_str_single
            elif not in_str_double and not in_str_single:
                if not in_multi and content[i:i+2] == '[[':
                    in_multi = True
                    i += 1
                elif in_multi and content[i:i+2] == ']]':
                    in_multi = False
                    i += 1
                    
            # Only count braces if we are strictly outside of any string formats
            if not (in_str_double or in_str_single or in_multi):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        paren_idx = content.find(")", i)
                        end = paren_idx + 1 if paren_idx != -1 else i + 1
                        break
            i += 1
                    
        if end != -1:
            block_str = content[start:end]
            f_m = re.search(r'fingers\s*=\s*(\d+)', block_str)
            d_m = re.search(r'direction\s*=\s*"([^"]+)"', block_str)
            if f_m and d_m:
                blocks.append((start, end, block_str, f_m.group(1), d_m.group(1)))
            idx = end
        else:
            idx = start + match.end()
            
    return blocks

# -----------------------------------------------------------------------------
# ENGINE IMPLEMENTATION
# -----------------------------------------------------------------------------
class TrackpadLuaEngine(HyprlandLuaEngine):
    def load_state(self) -> dict[str, Any]:
        # Let the AST engine parse whatever is actually in the file
        state = super().load_state()

        # ---------------------------------------------------------------------
        # STATE VIRTUALIZATION:
        # Pre-fill standard Hyprland 0.55 physics variables and unbound gestures.
        # This prevents the UI from marking them as `[Missing]` while keeping
        # the config uncluttered until the user actively edits them.
        # ---------------------------------------------------------------------
        physics_defaults = {
            "gestures/workspace_swipe_distance": 300,
            "gestures/workspace_swipe_touch": False,
            "gestures/workspace_swipe_invert": True,
            "gestures/workspace_swipe_touch_invert": False,
            "gestures/workspace_swipe_min_speed_to_force": 30,
            "gestures/workspace_swipe_cancel_ratio": 0.5,
            "gestures/workspace_swipe_create_new": True,
            "gestures/workspace_swipe_direction_lock": True,
            "gestures/workspace_swipe_direction_lock_threshold": 10,
            "gestures/workspace_swipe_forever": False,
            "gestures/workspace_swipe_use_r": False,
            "gestures/close_max_timeout": 1000,
        }
        
        for key, val in physics_defaults.items():
            if key not in state:
                state[key] = val

        for fingers in [3, 4]:
            for direction in ["horizontal", "left", "right", "up", "down"]:
                state[f"gesture/{fingers}/{direction}/action"] = "Disabled / Unbound"

        # ---------------------------------------------------------------------
        # Overwrite the virtual defaults with actual file blocks if they exist
        # ---------------------------------------------------------------------
        try:
            content = Path(self.config_path).read_text(encoding="utf-8")
        except OSError:
            return state

        blocks = find_gesture_blocks(content)
        for _, _, block_str, fingers, direction in blocks:
            friendly_label = get_friendly_name(block_str)
            state[f"gesture/{fingers}/{direction}/action"] = friendly_label

        return state

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        standard_changes = []
        gesture_changes = []
        
        for key, scope, val, itype in changes:
            if scope.startswith("gesture/") and key == "action":
                gesture_changes.append((scope, val))
            else:
                standard_changes.append((key, scope, val, itype))
                
        success = True
        msg = ""
        debug = ""
        
        # 1. Process standard configuration via the AST Engine
        if standard_changes:
            success, msg, debug = super().write_batch(standard_changes)
            
        if not success:
            return success, msg, debug
            
        # 2. Process gesture changes safely via block-level structural replacement
        if gesture_changes:
            try:
                path = Path(self.config_path)
                content = path.read_text(encoding="utf-8")
                
                for scope, new_val in gesture_changes:
                    parts = scope.split('/')
                    if len(parts) != 3: continue
                    fingers = parts[1]
                    direction = parts[2]
                    
                    action_code = ACTION_MAP.get(new_val, ACTION_MAP["Disabled / Unbound"])
                    
                    # Re-scan blocks on every iteration because index offsets shift after substitution
                    blocks = find_gesture_blocks(content)
                    replaced = False
                    for start, end, _, b_fingers, b_direction in blocks:
                        if b_fingers == fingers and b_direction == direction:
                            # If action is set to Disabled, we cleanly snip the entire gesture out
                            if action_code == "__DELETE__":
                                content = content[:start] + content[end:]
                            else:
                                new_block = f'''hl.gesture({{\n    fingers   = {fingers},\n    direction = "{direction}",\n    action    = {action_code},\n}})'''
                                content = content[:start] + new_block + content[end:]
                            replaced = True
                            break
                            
                    # If gesture block doesn't exist, append it cleanly to the file (unless it's an unbind op)
                    if not replaced and action_code != "__DELETE__":
                        new_block = f'''hl.gesture({{\n    fingers   = {fingers},\n    direction = "{direction}",\n    action    = {action_code},\n}})'''
                        content = content.rstrip() + f"\n\n{new_block}\n"

                path.write_text(content, encoding="utf-8")
                
                if hasattr(self, 'file_mtimes'):
                    self.file_mtimes[str(path)] = path.stat().st_mtime
                elif hasattr(self, 'file_mtime'):
                    self.file_mtime = path.stat().st_mtime
                    
                msg = f"Successfully batched {len(changes)} writes (Hybrid Trackpad Engine)."
                
            except Exception as e:
                return False, f"Hybrid Engine failed to patch gesture block: {e}", debug
                
        return True, msg, debug
