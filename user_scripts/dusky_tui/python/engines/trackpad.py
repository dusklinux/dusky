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
lazy from pathlib import Path
lazy from typing import Any
from python.engines.lua import HyprlandLuaEngine

# -----------------------------------------------------------------------------
# TRANSLATION MAP: UI Friendly Labels <-> Lua Backend Code
# -----------------------------------------------------------------------------
ACTION_MAP = {
    "Native Workspace Swipe": '"workspace"',
    "Open Dusky Tray": 'function()\n        hl.exec_cmd([["$HOME/.local/bin/dusky-tray"]])\n    end',
    "Toggle Waybar": 'function()\n        hl.exec_cmd(dusky_scripts .. "waybar/waybar_toggle.sh")\n    end',
    "Toggle Blur & Opacity": 'function()\n        hl.exec_cmd(dusky_scripts .. "hypr_blur_opacity_shadow_toggle.sh")\n    end',
    "Media: Play / Pause": 'function()\n        hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh --play-pause")\n    end',
    "Media: Volume Up (+10%)": 'function()\n        hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh --vol-up 10")\n    end',
    "Media: Volume Down (-10%)": 'function()\n        hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh --vol-down 10")\n    end',
    "Screen: Brightness Up (+10%)": 'function()\n        hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh --bright-up 10")\n    end',
    "Screen: Brightness Down (-10%)": 'function()\n        hl.exec_cmd(dusky_scripts .. "mako_osd/osd_router/osd_router.sh --bright-down 10")\n    end',
    'Native Tape Scroll': '"scroll_move"',
    'Move Window': '"move"',
    'Resize Window': '"resize"',
    'Toggle Floating': '"float"',
    'Toggle Fullscreen': '"fullscreen"',
    'Close Window': '"close"',
    "Disabled / Unbound": '__DELETE__'
}

def get_friendly_name(block_str: str) -> str:
    """Safely extracts the UI label by scanning the raw Lua block for keywords."""
    native = re.search(r'action\s*=\s*[\"\']([a-z_]+)[\"\']', block_str)
    if native:
        for label, code in ACTION_MAP.items():
            if code == '"' + native[1] + '"':
                return label
    if '"workspace"' in block_str or "'workspace'" in block_str: return "Native Workspace Swipe"
    if "dusky-tray" in block_str: return "Open Dusky Tray"
    if "waybar_toggle.sh" in block_str: return "Toggle Waybar"
    if "hypr_blur_opacity_shadow_toggle.sh" in block_str: return "Toggle Blur & Opacity"
    if "--play-pause" in block_str: return "Media: Play / Pause"
    if "--vol-up" in block_str: return "Media: Volume Up (+10%)"
    if "--vol-down" in block_str: return "Media: Volume Down (-10%)"
    if "--bright-up" in block_str: return "Screen: Brightness Up (+10%)"
    if "--bright-down" in block_str: return "Screen: Brightness Down (-10%)"
    return "Disabled / Unbound"

# Tokenize once so comments, escaped quotes, and Lua long strings cannot be
# mistaken for executable gestures or table braces.
_RE_LUA_TOKEN = re.compile(
    r'--\[(?P<comment_eq>=*)\[.*?\](?P=comment_eq)\]|--[^\r\n]*'
    r'|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''
    r'|\[(?P<string_eq>=*)\[.*?\](?P=string_eq)\]'
    r'|(?P<gesture>\bhl\.gesture\s*\(\s*\{)'
    r'|(?P<fingers>\bfingers\s*=\s*(?P<count>\d+))'
    r'|(?P<direction>\bdirection\s*=\s*(?P<quote>["\'])(?P<value>[^"\']+)(?P=quote))'
    r'|(?P<scope>\b(?:function|if|do|repeat|end|until)\b)'
    r'|(?P<brace>[{}])',
    re.DOTALL,
)
_RE_LUA_SPACE = re.compile(r'\s+|--\[(=*)\[.*?\]\1\]|--[^\r\n]*', re.DOTALL)


def find_gesture_blocks(content: str) -> list[tuple[int, int, str, str, str]]:
    """Find executable gesture tables while retaining their source offsets."""
    blocks = []
    tokens = iter(_RE_LUA_TOKEN.finditer(content))
    for token in tokens:
        if token.lastgroup != "gesture":
            continue
        start = token.start()
        depth = 1
        scope_depth = 0
        fingers = direction = None
        for inner in tokens:
            if inner.lastgroup == "gesture":
                depth += 1
            elif inner.lastgroup == "brace":
                depth += 1 if inner.group() == "{" else -1
            elif inner.lastgroup == "scope":
                scope_depth += -1 if inner.group() in ("end", "until") else 1
            elif depth == 1 and scope_depth == 0 and inner.lastgroup == "fingers":
                fingers = inner["count"]
            elif depth == 1 and scope_depth == 0 and inner.lastgroup == "direction":
                direction = inner["value"]
            if depth == 0:
                end = inner.end()
                while space := _RE_LUA_SPACE.prefixmatch(content, end):
                    end = space.end()
                if end == len(content) or content[end] != ")":
                    break
                end += 1
                block = content[start:end]
                if fingers and direction:
                    blocks.append((start, end, block, fingers, direction))
                break
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
