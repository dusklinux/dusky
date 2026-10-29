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
from python.shared.lua_source import fields, replace_field, string_value, tokens

# -----------------------------------------------------------------------------
# TRANSLATION MAP: UI Friendly Labels <-> Lua Backend Code
# -----------------------------------------------------------------------------
ACTION_MAP = {
    "Native Workspace Swipe": '"workspace"',
    "Open Dusky Tray": 'function()\n        hl.exec_cmd([["$HOME/.local/bin/dusky-tray"]])\n    end',
    "Toggle Waybar": 'function()\n        hl.exec_cmd(dusky_scripts .. "waybar/waybar_toggle.sh")\n    end',
    "Toggle Blur & Opacity": 'function()\n        hl.exec_cmd(dusky_scripts .. "hypr/hypr_blur_opacity_shadow_toggle.sh")\n    end',
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
    "Toggle Magic Scratchpad": '"special"',
    "Toggle Cursor Zoom (2x)": '"cursor_zoom"',
    "Disabled / Unbound": '__DELETE__'
}

def get_friendly_name(block_str: str) -> str:
    """Safely extracts the UI label by scanning the raw Lua block for keywords."""
    try:
        spans = fields(block_str, block_str.index('{'), block_str.rindex('}')+1)
        a, b = spans['action']
        action = block_str[a:b]
    except (ValueError, KeyError):
        return "Custom Lua Action"
    native = string_value(action)
    if native:
        if native == "unset":
            return "Disabled / Unbound"
        if native == 'special' and ('workspace_name' not in spans or string_value(block_str[slice(*spans['workspace_name'])]) != 'magic'):
            return "Custom Lua Action"
        if native in {'float', 'fullscreen', 'cursor_zoom'} and 'mode' in spans and block_str[slice(*spans['mode'])].strip() not in {'nil', '\"\"', "''"}:
            return "Custom Lua Action"
        if native == 'cursor_zoom' and ('zoom_level' not in spans or block_str[slice(*spans['zoom_level'])].strip('\"\' ') not in {'2', '2.0'}):
            return "Custom Lua Action"
        return next((label for label, code in ACTION_MAP.items() if code == '"'+native+'"'), "Custom Lua Action")
    block_str = ' '.join(t[1] for t in tokens(action))
    if "dusky-tray" in block_str: return "Open Dusky Tray"
    if "waybar_toggle.sh" in block_str: return "Toggle Waybar"
    if "hypr_blur_opacity_shadow_toggle.sh" in block_str: return "Toggle Blur & Opacity"
    if "--play-pause" in block_str: return "Media: Play / Pause"
    if "--vol-up" in block_str: return "Media: Volume Up (+10%)"
    if "--vol-down" in block_str: return "Media: Volume Down (-10%)"
    if "--bright-up" in block_str: return "Screen: Brightness Up (+10%)"
    if "--bright-down" in block_str: return "Screen: Brightness Down (-10%)"
    return "Custom Lua Action"

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

        for fingers in [2, 3, 4]:
            for direction in ["horizontal", "vertical", "left", "right", "up", "down", "pinch"]:
                state[f"gesture/{fingers}/{direction}/action"] = "Disabled / Unbound"

        # ---------------------------------------------------------------------
        # Overwrite the virtual defaults with actual file blocks if they exist
        # ---------------------------------------------------------------------
        try:
            content = Path(self.config_path).read_text(encoding="utf-8")
        except OSError:
            return state

        executed = {c['line'] for c in self._call_sites if c['file'] == str(self.config_path) and c['method'] == 'gesture'}
        blocks = find_gesture_blocks(content)
        for start, _, block_str, fingers, direction in blocks:
            if content.count('\n', 0, start)+1 not in executed:
                continue
            friendly_label = get_friendly_name(block_str)
            state[f"gesture/{fingers}/{direction}/action"] = friendly_label

        return state

    def write_batch(self, changes: list[tuple[str, str, str, str]]) -> tuple[bool, str, str]:
        standard = [c for c in changes if not c[1].startswith("gesture/")]
        gestures = [c for c in changes if c[1].startswith("gesture/")]
        if not gestures:
            return super().write_batch(standard)
        if not self.loaded_files:
            self.load_state()
        executed = {c['line'] for c in self._call_sites
                    if c['file'] == str(self.config_path) and c['method'] == 'gesture'}

        def transform(content):
            # Find original source spans once. Never replace a dormant callback.
            slots = {}
            for a, b, block, f, d in find_gesture_blocks(content):
                if content.count('\n', 0, a)+1 in executed:
                    slots.setdefault((f, d), []).append((a, b, block))
            edits, appended = [], []
            unique = {(key, scope): (key, scope, value, kind) for key, scope, value, kind in gestures}
            for key, scope, value, _ in unique.values():
                parts = scope.split('/')
                if key != 'action' or len(parts) != 3 or not parts[1].isdigit() or not 2 <= int(parts[1]) <= 9:
                    raise ValueError(f'Invalid gesture binding: {scope}/{key}')
                f, d = parts[1:]
                if d not in {'swipe', 'horizontal', 'vertical', 'left', 'right', 'up', 'down', 'pinch', 'pinchin', 'pinchout'}:
                    raise ValueError(f'Invalid gesture direction: {d}')
                matches = slots.get((f, d), [])
                if len(matches) > 1:
                    raise ValueError(f'Multiple gestures use {f} fingers and {d}; edit their Lua definitions individually.')
                if value == 'Custom Lua Action' and matches:
                    continue
                if value not in ACTION_MAP:
                    raise ValueError(f'Unknown gesture action: {value}')
                raw = ACTION_MAP[value]
                if value == 'Disabled / Unbound':
                    if matches:
                        edits.append((matches[0][0], matches[0][1], ''))
                    continue
                block = matches[0][2] if matches else f'hl.gesture({{fingers={f}, direction="{d}"}})'
                block = replace_field(block, 'action', raw)
                if value in {'Toggle Floating', 'Toggle Fullscreen', 'Toggle Cursor Zoom (2x)'}:
                    block = replace_field(block, 'mode', 'nil')
                if value == 'Toggle Magic Scratchpad':
                    block = replace_field(block, 'workspace_name', '"magic"')
                elif value == 'Toggle Cursor Zoom (2x)':
                    block = replace_field(block, 'zoom_level', '"2"')
                if matches:
                    edits.append((matches[0][0], matches[0][1], block))
                else:
                    appended.append(block)
            for a, b, replacement in sorted(edits, reverse=True):
                content = content[:a]+replacement+content[b:]
            return content + ''.join('\n'+block+'\n' for block in appended)

        return self.write_transformed(standard, transform)
