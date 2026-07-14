#!/usr/bin/env python3
# ==============================================================================
# Purpose: Interactive TUI to generate, copy, and append Hyprland window rules.
#          * Engine: Dusky TUI Engine v5.2.0 (Math-Perfected Lua Edition)
#          * Core: Window Scanning & Lua Rule Generation Logic (Hyprland 0.55+)
# ==============================================================================

import curses
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Dict, Optional, Any

# --- Configuration ---
TARGET_FILE = os.path.expanduser("~/.config/hypr/edit_here/source/window_rules.lua")
APP_TITLE = "Dusky Window Rule Generator"
APP_VERSION = "v5.3.0"

@dataclass
class MonitorData:
    id: int
    name: str
    width: int
    height: int
    scale: float
    x: int
    y: int
    transform: int

    @property
    def logical_width(self) -> float:
        # Swap width/height if the monitor is rotated 90 or 270 degrees
        w = self.height if self.transform % 2 != 0 else self.width
        return w / self.scale if self.scale > 0 else w

    @property
    def logical_height(self) -> float:
        # Swap width/height if the monitor is rotated 90 or 270 degrees
        h = self.width if self.transform % 2 != 0 else self.height
        return h / self.scale if self.scale > 0 else h

@dataclass
class ClientData:
    address: str
    title: str
    app_class: str
    mon_id: int
    w: int
    h: int
    x: int
    y: int
    floating: bool
    mapped: bool
    workspace_name: str

@dataclass
class GeneratedRule:
    address: str
    title: str
    app_class: str
    rule_text: str

def check_dependencies() -> None:
    """Verifies that required external CLI tools are installed."""
    deps = ["hyprctl", "wl-copy"]
    missing = []
    for dep in deps:
        if subprocess.call(["which", dep], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
            missing.append(dep)
    
    if missing:
        print(f"\033[1;31m[ERROR]\033[0m Missing dependencies: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

def escape_regex(s: str) -> str:
    """Escapes strings specifically for Google's RE2 syntax via Lua strings."""
    special_chars = r'\.[]*^$()+?{}|'
    # Double backslashes assure Lua sends the raw escape token (like `\|`) to the RE2 regex parser.
    escaped = "".join(f"\\\\{c}" if c in special_chars else c for c in s)
    return escaped.replace('"', '\\"')

def sanitize_name(s: str) -> str:
    """Sanitizes application class for the named rule."""
    cleaned = re.sub(r'[^a-zA-Z0-9_-]', '', s)
    return cleaned if cleaned else "unnamed"

def fmt_float(v: float) -> str:
    """Formats floats mathematically clean (e.g. 0.5000 -> 0.5, 1.0000 -> 1)."""
    s = f"{v:.4f}"
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    return s if s else "0"

def generate_lua_rule(client: ClientData, monitor: MonitorData) -> GeneratedRule:
    """Generates the Lua syntax block based on window and monitor metrics."""
    local_x = client.x - monitor.x
    local_y = client.y - monitor.y

    log_m_w = monitor.logical_width
    log_m_h = monitor.logical_height

    r_w = client.w / log_m_w if log_m_w else 0
    r_h = client.h / log_m_h if log_m_h else 0
    r_x = local_x / log_m_w if log_m_w else 0
    r_y = local_y / log_m_h if log_m_h else 0

    r_w_str = fmt_float(r_w)
    r_h_str = fmt_float(r_h)
    r_x_str = fmt_float(r_x)
    r_y_str = fmt_float(r_y)

    safe_class = escape_regex(client.app_class)
    safe_title = escape_regex(client.title)
    safe_name = sanitize_name(client.app_class)

    rule_text = f"""-- -----------------------------------------------------
-- {client.title}

hl.window_rule({{
    name = "{safe_name}",
    match = {{
        class = "^({safe_class})$",
        -- title = "^({safe_title})$",
        -- xwayland = true,          -- match only XWayland windows
        -- float = true,          -- match only floating windows
        -- fullscreen = false,       -- match only non-fullscreen windows
        -- pin = false,           -- match only non-pinned windows
    }},
    float = true,

    -- [OPTION A: STICKY/PINNED DIALOG]
    -- pin = true,                   -- locks window to show on ALL workspaces

    -- [OR]

    -- [OPTION B: NATIVE ACTIVE-WORKSPACE ONLY DIALOG] (Requires initial_workspace_tracking = 2 in workspace_rules.lua)
    -- workspace = "unset",          -- Spawns dynamically on the ACTIVE workspace only (won't follow you when switching)
    -- focus_on_activate = true,     -- Automatically steal focus on spawn
    -- stay_focused = true,          -- Keep focus locked on this window as long as it remains visible

    -- tile = true,                  -- force tiled (not floating)

    size = {{{client.w}, {client.h}}},
    -- size = {{"monitor_w * {r_w_str}", "monitor_h * {r_h_str}"}},
    -- min_size = {{200, 100}},       -- clamp minimum size
    -- max_size = {{1920, 1080}},     -- clamp maximum size
    -- keep_aspect_ratio = true,     -- preserve aspect ratio when resizing

    move = {{{local_x}, {local_y}}},
    -- move = {{"monitor_w * {r_x_str}", "monitor_h * {r_y_str}"}},
    -- move = {{"monitor_w - window_w - 20", "monitor_h - window_h - 20"}},
    -- center = true,                -- center on the monitor (ignores move)
    -- center = {1},               -- center including reserved areas (e.g. waybar)

    -- --- Animation ---
    -- Pick ONE style. Remove the "--" from the line you want.
    --
    -- animation = "popin",          -- scale from centre, auto start %
    -- animation = "popin 60%",      -- scale in starting from 60% size
    -- animation = "popin 70%",      -- scale in starting from 70% size
    -- animation = "popin 80%",      -- scale in starting from 80% size
    -- animation = "popin 87%",      -- Hyprland official default start %
    -- animation = "popin 90%",      -- subtle pop, close to full size
    -- animation = "popin 95%",      -- very subtle, barely perceptible
    --
    -- animation = "slide",          -- slide from nearest monitor edge (auto)
    -- animation = "slide top",      -- always slide in from the top
    -- animation = "slide bottom",   -- always slide in from the bottom
    -- animation = "slide left",     -- always slide in from the left
    -- animation = "slide right",    -- always slide in from the right
    --
    -- animation = "gnomed",         -- GNOME-style (scale + fade combo)
    -- animation = "fade",           -- opacity fade only, no motion
    --
    -- no_anim = true,               -- disable ALL animation for this window

    -- --- Visuals & Effects ---
    -- opacity = "0.9 override 0.9 override",    -- active opacity, inactive opacity
    -- opacity = "1.0 override 0.85 override",   -- fully opaque active, dimmed inactive
    -- opacity = "0.95",                          -- both states same value
    -- opaque = true,                             -- force fully opaque (ignores opacity rule)
    --
    -- rounding = 10,                -- corner radius in px (overrides global)
    -- rounding = 0,                 -- sharp corners for this window only
    -- rounding_power = 2,           -- rounding curve power (2 = standard, higher = squircle)
    --
    -- border_size = 2,              -- border thickness in px (overrides global)
    -- border_size = 0,              -- no border for this window
    -- border_color = "rgb(ff0000)",             -- solid red border
    -- border_color = "rgba(33ccffee)",          -- RGBA hex border colour
    -- border_color = {{colors = {{"rgba(33ccffee)", "rgba(00ff99ee)"}}, angle = 45}},  -- gradient border
    --
    -- no_blur = true,               -- disable blur behind this window
    -- xray = true,                  -- see through to wallpaper (xray blur)
    -- no_shadow = true,             -- disable drop shadow
    -- no_dim = true,                -- never dim when inactive
    -- dim_around = true,            -- dim everything except this window
    -- no_focus = true,              -- window cannot receive focus
    -- no_maximize = "maximize",           -- prevent the window from being maximized

    -- --- Fullscreen / Layout ---
    -- fullscreen = true,                -- open in fullscreen (client-side)
    -- immediate = true,                 -- bypass Wayland sync protocol (tearing opt-in)
    -- group = "set",                    -- add to a window group
    -- group = "lock",                   -- lock group membership (no auto-grouping)
    -- group = "barred",                 -- hide this window's tab in the group bar
    -- group = "deny",                   -- deny grouping entirely

    -- --- Focus & Raise ---
    -- no_initial_focus = true,            -- Do not focus when first opened
    -- focus_on_activate = false,          -- Do not focus even if application requests focus (prevents focus stealing, e.g. for PiP)

    -- --- Suppress / Lifecycle ---
    -- suppress_event = "maximize",      -- eat maximize requests from the app
    -- suppress_event = "fullscreen",    -- eat fullscreen requests from the app

    -- --- Placement ---
    -- workspace = "{client.workspace_name}",    -- force to a specific workspace
    -- workspace = "special:magic",              -- open on special/scratch workspace
    -- workspace = "name:gaming",                -- force to named workspace
    -- monitor = "{monitor.name}",               -- force to a specific monitor
}})"""

    return GeneratedRule(
        address=client.address,
        title=client.title[:60] if client.title else "Unknown",
        app_class=client.app_class,
        rule_text=rule_text
    )

def scan_windows() -> List[GeneratedRule]:
    """Interrogates Hyprland for live monitors and clients to build rule models."""
    try:
        mon_output = subprocess.check_output(["hyprctl", "monitors", "-j"], text=True)
        mon_json = json.loads(mon_output)
    except Exception as e:
        print(f"\033[1;31m[ERROR]\033[0m Failed to fetch monitors: {e}", file=sys.stderr)
        sys.exit(1)

    mon_map: Dict[int, MonitorData] = {}
    for m in mon_json:
        mon_map[m.get('id', 0)] = MonitorData(
            id=m.get('id', 0),
            name=m.get('name', 'unknown'),
            width=m.get('width', 1920),
            height=m.get('height', 1080),
            scale=m.get('scale', 1.0),
            x=m.get('x', 0),
            y=m.get('y', 0),
            transform=m.get('transform', 0)
        )

    if not mon_map:
        print("\033[1;31m[ERROR]\033[0m No monitors found.", file=sys.stderr)
        sys.exit(1)

    try:
        clients_output = subprocess.check_output(["hyprctl", "clients", "-j"], text=True)
        clients_json = json.loads(clients_output)
    except Exception as e:
        print(f"\033[1;31m[ERROR]\033[0m Failed to fetch clients: {e}", file=sys.stderr)
        sys.exit(1)

    generated_rules = []
    for c in clients_json:
        if not c.get('mapped', False): continue
        app_class = c.get('class') or c.get('initialClass')
        if not app_class: continue
        
        mon_id = c.get('monitor', -1)
        if mon_id not in mon_map: continue
        
        at = c.get('at', [0, 0])
        size = c.get('size', [0, 0])
        workspace_info = c.get('workspace', {})
        
        client_data = ClientData(
            address=c.get('address', ''),
            title=c.get('title', ''),
            app_class=app_class,
            mon_id=mon_id,
            w=size[0],
            h=size[1],
            x=at[0],
            y=at[1],
            floating=c.get('floating', False),
            mapped=True,
            workspace_name=workspace_info.get('name', 'unknown')
        )
        
        generated_rules.append(generate_lua_rule(client_data, mon_map[mon_id]))
        
    return generated_rules


class DuskyTUI:
    def __init__(self, stdscr: curses.window, rules: List[GeneratedRule]):
        self.stdscr = stdscr
        self.rules = rules
        self.item_count = len(rules)
        self.selected_row = 0
        self.scroll_offset = 0
        self.status_msg = ""
        
        # --- Advanced Color Palette ---
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)     # Keys / UI Accents
        curses.init_pair(2, curses.COLOR_MAGENTA, -1)  # Booleans / UI Borders
        curses.init_pair(3, curses.COLOR_WHITE, -1)    # General Text
        curses.init_pair(4, curses.COLOR_GREEN, -1)    # Strings / Keywords
        curses.init_pair(5, curses.COLOR_YELLOW, -1)   # Numbers / Arrays / Warnings
        
        if curses.COLORS >= 256:
            curses.init_pair(6, 245, -1) # True grey for comments
        else:
            curses.init_pair(6, curses.COLOR_BLACK, -1) # Fallback

        curses.curs_set(0)
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)

    def draw_safe(self, y: int, x: int, text: str, attr: int = 0) -> None:
        max_y, max_x = self.stdscr.getmaxyx()
        if 0 <= y < max_y and 0 <= x < max_x:
            safe_text = text[:max_x - x - 1]
            self.stdscr.addstr(y, x, safe_text, attr)

    def draw_highlighted_lua(self, y: int, x: int, line: str, max_x: int) -> None:
        """Custom Tokenizer for intuitive Lua syntax highlighting."""
        if x >= max_x: return
        
        comment_idx = line.find("--")
        prefix = line
        comment = ""
        
        if comment_idx != -1:
            prefix = line[:comment_idx]
            comment = line[comment_idx:]
            
        current_x = x
        
        def draw_part(text: str, attr: int):
            nonlocal current_x
            if current_x >= max_x or not text: return
            safe_text = text[:max_x - current_x - 1]
            try:
                self.stdscr.addstr(y, current_x, safe_text, attr)
                current_x += len(safe_text)
            except curses.error:
                pass

        if not prefix.strip():
            draw_part(prefix, 0)
            draw_part(comment, curses.color_pair(6))
            return
            
        # Key = Value splitting
        if "=" in prefix:
            key, val = prefix.split("=", 1)
            draw_part(key, curses.color_pair(1)) # Cyan keys
            draw_part("=", curses.color_pair(3)) # White equals
            
            val_strip = val.strip()
            # Determine Value type
            if val_strip.startswith('"') or val_strip.startswith("'"):
                attr = curses.color_pair(4) # Green Strings
            elif val_strip.startswith("{") or any(char.isdigit() for char in val_strip):
                attr = curses.color_pair(5) # Yellow Tables/Numbers
            elif val_strip.startswith("true") or val_strip.startswith("false"):
                attr = curses.color_pair(2) # Magenta Booleans
            else:
                attr = curses.color_pair(3)
                
            draw_part(val, attr)
        elif "hl.window_rule" in prefix:
            idx = prefix.find("hl.window_rule")
            draw_part(prefix[:idx], 0)
            draw_part("hl.window_rule", curses.color_pair(4) | curses.A_BOLD)
            draw_part(prefix[idx+len("hl.window_rule"):], curses.color_pair(3))
        else:
            draw_part(prefix, curses.color_pair(3))
            
        if comment:
            draw_part(comment, curses.color_pair(6))

    def copy_clipboard(self) -> None:
        rule_text = self.rules[self.selected_row].rule_text
        try:
            subprocess.run(['wl-copy'], input=rule_text, text=True, check=True)
            self.status_msg = "[SUCCESS] Copied to clipboard!"
        except Exception:
            self.status_msg = "[ERROR] Failed to copy (wl-copy missing or Wayland error?)"

    def append_selection(self) -> None:
        rule_text = self.rules[self.selected_row].rule_text
        try:
            os.makedirs(os.path.dirname(TARGET_FILE), exist_ok=True)
            
            needs_newline = False
            if os.path.exists(TARGET_FILE) and os.path.getsize(TARGET_FILE) > 0:
                with open(TARGET_FILE, "rb") as f:
                    f.seek(-1, 2)
                    if f.read(1) != b'\n':
                        needs_newline = True
                        
            with open(TARGET_FILE, "a", encoding="utf-8") as f:
                if needs_newline:
                    f.write("\n")
                f.write(rule_text + "\n")
                
            self.status_msg = f"[SUCCESS] Rule appended to {os.path.basename(TARGET_FILE)}!"
        except Exception as e:
            self.status_msg = f"[ERROR] Failed to append: {str(e)}"

    def handle_scroll(self, max_display: int) -> None:
        if self.selected_row < self.scroll_offset:
            self.scroll_offset = self.selected_row
        elif self.selected_row >= self.scroll_offset + max_display:
            self.scroll_offset = self.selected_row - max_display + 1

    def run(self) -> None:
        self.stdscr.timeout(1000) # 1000ms (1 second) non-blocking timeout

        while True:
            # --- REAL-TIME SCANNING & CURSOR RETENTION ---
            old_address = None
            if self.rules and 0 <= self.selected_row < len(self.rules):
                old_address = self.rules[self.selected_row].address

            # Rescan windows from Hyprland
            self.rules = scan_windows()
            self.item_count = len(self.rules)

            # Re-anchor the cursor so it doesn't bounce when the list updates
            if self.item_count == 0:
                self.selected_row = 0
            elif old_address:
                new_idx = next((i for i, r in enumerate(self.rules) if r.address == old_address), -1)
                if new_idx != -1:
                    self.selected_row = new_idx
                else:
                    self.selected_row = min(self.selected_row, self.item_count - 1)
            else:
                self.selected_row = min(self.selected_row, max(0, self.item_count - 1))

            self.stdscr.erase()
            max_y, max_x = self.stdscr.getmaxyx()
            
            if max_y < 12 or max_x < 30:
                self.draw_safe(0, 0, "Terminal too small.", curses.color_pair(5))
                self.stdscr.refresh()
                if self.stdscr.getch() == ord('q'):
                    break
                continue

            box_width = min(110, max_x - 2)
            available_h = max_y - 6 
            
            list_h = max(3, int(available_h * 0.40))
            preview_h = max(3, available_h - list_h)

            self.handle_scroll(list_h)

            border_line = "─" * (box_width - 2)
            c_mag = curses.color_pair(2)
            
            # --- HEADER / TOP BORDER ---
            self.draw_safe(0, 0, f"┌{border_line}┐", c_mag)
            
            # Embed Keybinds into the top border (0 wasted lines)
            keybinds = " [↑/↓] Nav  [Enter] Append  [c] Copy  [q] Quit "
            kb_x = box_width - len(keybinds) - 2
            if kb_x > 2:
                self.draw_safe(0, kb_x, keybinds, curses.color_pair(1) | curses.A_BOLD)
            
            title_str = f" {APP_TITLE} "
            ver_str = f"{APP_VERSION} "
            
            self.draw_safe(1, 0, "│", c_mag)
            self.draw_safe(1, 2, title_str, curses.color_pair(3) | curses.A_BOLD)
            self.draw_safe(1, 2 + len(title_str), ver_str, curses.color_pair(1))
            self.draw_safe(1, box_width - 1, "│", c_mag)
            
            self.draw_safe(2, 0, f"├{border_line}┤", c_mag)

            # --- LIST ITEMS ---
            for i in range(list_h):
                row_y = 3 + i
                item_idx = self.scroll_offset + i
                
                self.draw_safe(row_y, 0, "│", c_mag)
                self.draw_safe(row_y, box_width - 1, "│", c_mag)
                
                if item_idx < self.item_count:
                    rule = self.rules[item_idx]
                    if item_idx == self.selected_row:
                        self.draw_safe(row_y, 2, f"➤  {rule.app_class} ", curses.color_pair(1) | curses.A_REVERSE)
                        self.draw_safe(row_y, 4 + len(rule.app_class) + 2, f":: {rule.title}", curses.color_pair(3))
                    else:
                        self.draw_safe(row_y, 3, f"{rule.app_class} ", curses.color_pair(1))
                        self.draw_safe(row_y, 3 + len(rule.app_class) + 1, f":: {rule.title}", curses.color_pair(6))

            mid_y = 3 + list_h
            self.draw_safe(mid_y, 0, f"├{border_line}┤", c_mag)
            self.draw_safe(mid_y + 1, 0, "│", c_mag)
            self.draw_safe(mid_y + 1, 2, "LUA RULE PREVIEW:", curses.color_pair(3) | curses.A_BOLD)
            self.draw_safe(mid_y + 1, box_width - 1, "│", c_mag)

            # --- PREVIEW BOX ---
            preview_start_y = mid_y + 2
            rule_lines = self.rules[self.selected_row].rule_text.split('\n')
            
            for i in range(preview_h):
                row_y = preview_start_y + i
                self.draw_safe(row_y, 0, "│", c_mag)
                self.draw_safe(row_y, box_width - 1, "│", c_mag)
                
                if i < len(rule_lines):
                    # Route to our shiny new tokenizer for rendering
                    self.draw_highlighted_lua(row_y, 2, rule_lines[i], box_width - 1)

            # --- BOTTOM BORDER ---
            bot_y = preview_start_y + preview_h
            self.draw_safe(bot_y, 0, f"└{border_line}┘", c_mag)
            
            # Embed Target/Status into the bottom border (0 wasted lines)
            status_text = f" {self.status_msg} " if self.status_msg else f" Target: {TARGET_FILE} "
            status_color = curses.color_pair(5) | curses.A_BOLD if self.status_msg else curses.color_pair(3)
            if box_width > len(status_text) + 4:
                self.draw_safe(bot_y, 2, status_text, status_color)

            self.stdscr.refresh()

            # --- INPUT ROUTER ---
            try:
                ch = self.stdscr.getch()
            except curses.error:
                ch = -1

            if ch == -1:
                continue # Timeout hit, loop back to the top to rescan and redraw

            self.status_msg = "" # Only clear the status message on an actual keypress

            match ch:
                case curses.KEY_UP | 107: # 107 = 'k'
                    self.selected_row = max(0, self.selected_row - 1)
                case curses.KEY_DOWN | 106: # 106 = 'j'
                    self.selected_row = min(self.item_count - 1, self.selected_row + 1)
                case curses.KEY_PPAGE:
                    self.selected_row = max(0, self.selected_row - list_h)
                case curses.KEY_NPAGE:
                    self.selected_row = min(self.item_count - 1, self.selected_row + list_h)
                case 103: # 'g'
                    self.selected_row = 0
                case 71: # 'G'
                    self.selected_row = self.item_count - 1
                case 99 | 67: # 'c' or 'C'
                    self.copy_clipboard()
                case 10 | 13: # Enter
                    self.append_selection()
                case 113 | 81 | 27: # 'q', 'Q', or ESC
                    break
                case curses.KEY_MOUSE:
                    try:
                        _, mx, my, _, bstate = curses.getmouse()
                        if bstate & curses.BUTTON4_PRESSED: 
                            self.selected_row = max(0, self.selected_row - 1)
                        elif bstate & curses.BUTTON5_PRESSED: 
                            self.selected_row = min(self.item_count - 1, self.selected_row + 1)
                    except curses.error:
                        pass
                case curses.KEY_RESIZE:
                    pass 


def main(stdscr: curses.window) -> None:
    check_dependencies()
    
    rules = scan_windows()
    if not rules:
        print("No mapped, visible windows found on active monitors.")
        return

    tui = DuskyTUI(stdscr, rules)
    tui.run()

if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        sys.exit(130)
