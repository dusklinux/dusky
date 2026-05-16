#!/usr/bin/env python3
"""
Dusky Monitor Wizard - Hyprland Edition v5.4.0 (Ultimate Lua Engine)
-----------------------------------------------------------------------------
Engineered for Hyprland 0.55+. Zero dependencies. Safe atomic writes.
Features: Infinite-Scroll Edit Menu, Complete SDR/HDR Color Pipeline, 
          All 'auto' Position Variants, VESA Mode Injection.
"""

import sys
import os
import json
import subprocess
import curses
import tempfile
import re
from pathlib import Path

# --- CONFIGURATION ---
APP_TITLE = "DUSKY MONITOR WIZARD v5.4.0"
APP_SUBTITLE = "Hyprland Lua Engine"
CONFIG_DIR = Path.home() / ".config/hypr/edit_here/source"
TARGET_CONFIG = CONFIG_DIR / "monitors.lua"
DEBUG_LOG = Path("/tmp/dusky_debug.log")

TRANSFORMS = ["Normal", "90°", "180°", "270°", "Flipped", "Flipped-90°", "Flipped-180°", "Flipped-270°"]
SPECIAL_MODES = ["preferred", "highres", "highrr", "maxwidth"]
POS_VARIANTS = ["auto", "auto-right", "auto-left", "auto-up", "auto-down", 
                "auto-center-right", "auto-center-left", "auto-center-up", "auto-center-down"]
CM_PROFILES = ["auto", "srgb", "dcip3", "dp3", "adobe", "wide", "edid", "hdr", "hdredid"]
SDR_EOTFS = ["default", "srgb", "gamma22"]

# Standard VESA fallback resolutions for sparse EDIDs
STANDARD_RES = [
    (3840, 2160), (3440, 1440), (2560, 1440), (2560, 1080), 
    (1920, 1200), (1920, 1080), (1680, 1050), (1600, 900), 
    (1440, 900), (1366, 768), (1280, 1024), (1280, 800), 
    (1280, 720), (1024, 768)
]

def log_err(msg):
    try:
        with open(DEBUG_LOG, "a") as f:
            f.write(f"[ERROR] {msg}\n")
    except Exception:
        pass

# --- HYPRLAND IPC & STATE MANAGEMENT ---
class HardwareManager:
    @staticmethod
    def get_monitors():
        try:
            res = subprocess.run(["hyprctl", "monitors", "all", "-j"], capture_output=True, text=True, check=True)
            monitors = json.loads(res.stdout)
            
            state = []
            for m in monitors:
                raw_refresh = float(m.get("refreshRate", 60.0))
                native_w = int(m.get("width", 1920))
                native_h = int(m.get("height", 1080))
                base_refresh = round(raw_refresh, 2)
                
                avail_modes_raw = m.get("availableModes", [])
                clean_modes = [mode.replace("Hz", "").strip() for mode in avail_modes_raw]
                
                fallback_modes = []
                for w, h in STANDARD_RES:
                    if w <= native_w and h <= native_h:
                        fallback_modes.append(f"{w}x{h}@{base_refresh}")
                        if base_refresh != 60.0:
                            fallback_modes.append(f"{w}x{h}@60.00")
                            
                all_modes = SPECIAL_MODES + clean_modes
                for f_mode in fallback_modes:
                    if f_mode not in all_modes:
                        all_modes.append(f_mode)
                
                mon_data = {
                    "name": m.get("name", "Unknown"),
                    "desc": m.get("description", ""),
                    "enabled": not m.get("disabled", False),
                    "width": native_w,
                    "height": native_h,
                    "refresh": base_refresh,
                    "scale": float(m.get("scale", 1.0)),
                    "transform": int(m.get("transform", 0)),
                    "x": int(m.get("x", 0)),
                    "y": int(m.get("y", 0)),
                    "vrr": int(m.get("vrr", 0)), 
                    "bitdepth": 10 if "101010" in m.get("currentFormat", "") else 8,
                    "cm": m.get("colorManagementPreset", "auto"),
                    "sdr_brightness": float(m.get("sdrBrightness", 1.0)),
                    "sdr_saturation": float(m.get("sdrSaturation", 1.0)),
                    "sdr_eotf": "default", 
                    "mirror": "",
                    "target_identifier": m.get("name", "Unknown"), 
                    "mode_str": "preferred",
                    "pos_str": "auto",
                    "available_modes": all_modes
                }
                state.append(mon_data)
            return state
        except Exception as e:
            log_err(f"Failed to fetch monitors via hyprctl: {e}")
            return []

    @staticmethod
    def get_globals():
        try:
            res = subprocess.run(["hyprctl", "getoption", "debug:vfr", "-j"], capture_output=True, text=True)
            vfr_state = json.loads(res.stdout).get("int", 1) == 1
        except:
            vfr_state = True
            
        try:
            res = subprocess.run(["hyprctl", "getoption", "misc:vrr", "-j"], capture_output=True, text=True)
            vrr_state = json.loads(res.stdout).get("int", 0)
        except:
            vrr_state = 0

        return {"vfr": vfr_state, "vrr": vrr_state}

# --- LUA PARSER & WRITER ---
class LuaConfigManager:
    @staticmethod
    def _build_lua_properties(mon: dict) -> str:
        lines = []
        lines.append(f'    output   = "{mon["target_identifier"]}",')
        
        if not mon["enabled"]:
            lines.append('    disabled = true,')
            return "\n".join(lines)

        lines.append(f'    mode     = "{mon["mode_str"]}",')
        lines.append(f'    position = "{mon["pos_str"]}",')
        
        scale_val = f'{mon["scale"]:g}' if isinstance(mon["scale"], float) else '"auto"'
        lines.append(f'    scale    = {scale_val},')
        
        if mon["transform"] != 0:
            lines.append(f'    transform = {mon["transform"]},')
        if mon["vrr"] > 0:
            lines.append(f'    vrr      = {mon["vrr"]},')
        if mon["bitdepth"] == 10:
            lines.append('    bitdepth = 10,')
        if mon["cm"] != "auto":
            lines.append(f'    cm       = "{mon["cm"]}",')
        if mon["sdr_eotf"] != "default":
            lines.append(f'    sdr_eotf = "{mon["sdr_eotf"]}",')
        if mon["sdr_brightness"] != 1.0:
            lines.append(f'    sdrbrightness = {mon["sdr_brightness"]},')
        if mon["sdr_saturation"] != 1.0:
            lines.append(f'    sdrsaturation = {mon["sdr_saturation"]},')
        if mon["mirror"]:
            lines.append(f'    mirror   = "{mon["mirror"]}",')
            
        return "\n".join(lines)

    @staticmethod
    def save_config(monitors_state, global_state):
        if not TARGET_CONFIG.exists():
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            TARGET_CONFIG.write_text("-- USER CONFIGURATION: monitors.lua\n\n")

        with open(TARGET_CONFIG, "r") as f:
            config_text = f.read()

        block_pattern = re.compile(
            r'(^[ \t]*hl\.monitor\s*\(\s*\{(?:[^{}]|\{[^{}]*\})*\}\s*\))', 
            re.MULTILINE | re.DOTALL
        )

        processed_monitors = set()
        
        # Unmanaged advanced fields to preserve safely
        unmanaged_keys = [
            "icc", "reserved_area", "supports_wide_color", "supports_hdr", 
            "sdr_min_luminance", "sdr_max_luminance", "min_luminance", 
            "max_luminance", "max_avg_luminance"
        ]

        def block_replacer(match: re.Match) -> str:
            block = match.group(1)
            output_match = re.search(r'output\s*=\s*["\'](.*?)["\']', block)
            if not output_match:
                return block
                
            out_val = output_match.group(1)
            
            target_mon = None
            for m in monitors_state:
                if out_val == m["name"] or (out_val.startswith("desc:") and out_val[5:] in m["desc"]):
                    target_mon = m
                    target_mon["target_identifier"] = out_val 
                    break
            
            if target_mon:
                processed_monitors.add(target_mon["name"])
                
                # Retain strings if unmodified
                mode_match = re.search(r'mode\s*=\s*["\'](.*?)["\']', block)
                if mode_match and target_mon["mode_str"] == "preferred":
                    target_mon["mode_str"] = mode_match.group(1)

                pos_match = re.search(r'position\s*=\s*["\'](.*?)["\']', block)
                if pos_match and target_mon["pos_str"] == "auto":
                    target_mon["pos_str"] = pos_match.group(1)

                new_props = LuaConfigManager._build_lua_properties(target_mon)
                
                extra_lines = []
                for line in block.splitlines():
                    if any(key in line for key in unmanaged_keys):
                        extra_lines.append(line.strip(' \t,}'))

                if extra_lines:
                    new_props += ",\n    " + ",\n    ".join(extra_lines)

                return f"hl.monitor({{\n{new_props}\n}})"
            
            return block

        new_text = block_pattern.sub(block_replacer, config_text)

        for mon in monitors_state:
            if mon["name"] not in processed_monitors:
                new_text += f"\n-- Auto-generated by Dusky Monitor Wizard\nhl.monitor({{\n{LuaConfigManager._build_lua_properties(mon)}\n}})\n"

        vfr_str = "true" if global_state["vfr"] else "false"
        new_text = re.sub(r'(vfr\s*=\s*)(true|false)', rf'\g<1>{vfr_str}', new_text)
        new_text = re.sub(r'(vrr\s*=\s*)([0-2])', rf'\g<1>{global_state["vrr"]}', new_text)

        fd, temp_path = tempfile.mkstemp(dir=TARGET_CONFIG.parent)
        try:
            with os.fdopen(fd, 'w') as temp_file:
                temp_file.write(new_text)
                
            os.chmod(temp_path, TARGET_CONFIG.stat().st_mode)
            os.replace(temp_path, TARGET_CONFIG)
            
            subprocess.run(["hyprctl", "reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            os.remove(temp_path)
            log_err(f"Atomic write failed: {e}")

# --- CURSES TUI ENGINE ---
class DuskyUI:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        curses.curs_set(0)
        curses.use_default_colors()
        self.stdscr.timeout(100)
        
        # ENABLE MOUSE CLICKS
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_RED, -1)
        curses.init_pair(4, curses.COLOR_YELLOW, -1)
        
        self.monitors = HardwareManager.get_monitors()
        self.global_state = HardwareManager.get_globals()
        
        self.view_state = 0 
        self.selected_row = 0
        self.picker_scroll = 0
        self.edit_scroll = 0
        self.current_edit_mon = None

    def draw_box(self, y, h, w):
        self.stdscr.attron(curses.color_pair(1))
        self.stdscr.addstr(y, 0, "┌" + "─" * (w-2) + "┐")
        for i in range(1, h-1):
            self.stdscr.addstr(y+i, 0, "│")
            self.stdscr.addstr(y+i, w-1, "│")
            
        try:
            self.stdscr.addstr(y+h-1, 0, "└" + "─" * (w-2) + "┘")
        except curses.error:
            pass
        self.stdscr.attroff(curses.color_pair(1))

    def render_header(self, w):
        self.stdscr.addstr(2, 2, f"{APP_TITLE} - {APP_SUBTITLE}", curses.A_BOLD)
        # FIX: Changed x=1 to x=0 to snap perfectly with the vertical box walls
        self.stdscr.addstr(3, 0, "├" + "─" * (w-2) + "┤", curses.color_pair(1))

        if self.view_state in [0, 3]:
            t1_attr = curses.color_pair(1) | curses.A_REVERSE if self.view_state == 0 else curses.A_DIM
            t2_attr = curses.color_pair(1) | curses.A_REVERSE if self.view_state == 3 else curses.A_DIM
            self.stdscr.addstr(4, 2, " [ MONITORS ] ", t1_attr)
            self.stdscr.addstr(4, 18, " [ GLOBALS ] ", t2_attr)
            # FIX: Changed x=1 to x=0 
            self.stdscr.addstr(5, 0, "├" + "─" * (w-2) + "┤", curses.color_pair(1))
            return 6
        return 4

    def render_monitors(self, start_y, h, w):
        for idx, mon in enumerate(self.monitors):
            if start_y + idx >= h - 3: break
            attr = curses.color_pair(1) | curses.A_REVERSE if idx == self.selected_row else curses.A_NORMAL
            status = "ON " if mon["enabled"] else "OFF"
            status_attr = curses.color_pair(2) if mon["enabled"] else curses.color_pair(3)
            
            row_str = f" {mon['name'][:15]:<15} [{status}] {mon['width']}x{mon['height']}@{mon['refresh']}Hz {mon['scale']}x"
            self.stdscr.addstr(start_y + idx, 2, row_str, attr)
            
        save_idx = len(self.monitors)
        save_attr = curses.color_pair(4) | curses.A_REVERSE if self.selected_row == save_idx else curses.color_pair(4)
        self.stdscr.addstr(h - 3, 2, "[ Save & Apply Configuration ]", save_attr)
        self.stdscr.addstr(h - 2, 2, "[Tab] Switch Menu   [Enter] Edit", curses.A_DIM)

    def render_globals(self, start_y, h, w):
        fields = [
            ("VFR (Variable Frame Rate)", "Enabled" if self.global_state["vfr"] else "Disabled"),
            ("Global VRR Override", str(self.global_state["vrr"]) + " (0=Off, 1=On, 2=Fullscreen)")
        ]
        
        for idx, (label, val) in enumerate(fields):
            attr = curses.color_pair(1) | curses.A_REVERSE if idx == self.selected_row else curses.A_NORMAL
            self.stdscr.addstr(start_y + idx, 2, f" {label:<27} : {val}", attr)
            
        save_idx = len(fields)
        save_attr = curses.color_pair(4) | curses.A_REVERSE if self.selected_row == save_idx else curses.color_pair(4)
        self.stdscr.addstr(h - 3, 2, "[ Save & Apply Configuration ]", save_attr)
        self.stdscr.addstr(h - 2, 2, "[Tab] Switch Menu   [< >] Adjust", curses.A_DIM)

    def _build_edit_fields(self, mon):
        return [
            ("Enabled", str(mon["enabled"])),
            ("Identifier Mode", "Desc/Safe" if "desc:" in mon["target_identifier"] else "Port/Raw"),
            ("Mode (Res/Rate)", mon["mode_str"] + "  [Enter to Pick]"),
            ("Position", mon["pos_str"]),
            ("Scale Factor", "auto" if mon['scale'] == "auto" else f"{mon['scale']}x"),
            ("Transform (Rotation)", TRANSFORMS[mon['transform']]),
            ("VRR Mode", str(mon['vrr']) + " (0=Off, 1=On, 2=FS)"),
            ("Bitdepth", str(mon['bitdepth']) + "-bit"),
            ("Color Profile (cm)", mon['cm']),
            ("SDR EOTF Curve", mon['sdr_eotf']),
            ("SDR Brightness", f"{mon['sdr_brightness']:.2f}"),
            ("SDR Saturation", f"{mon['sdr_saturation']:.2f}"),
            ("Mirror Output", mon['mirror'] if mon['mirror'] else "None")
        ]

    def render_edit(self, start_y, h, w):
        mon = self.current_edit_mon
        self.stdscr.addstr(start_y, 2, f"Editing: {mon['name']}", curses.color_pair(4) | curses.A_BOLD)
        # FIX: Changed x=1 to x=0
        self.stdscr.addstr(start_y + 1, 0, "├" + "─" * (w-2) + "┤", curses.color_pair(1))
        
        fields = self._build_edit_fields(mon)
        list_y = start_y + 2
        max_edit_display = h - list_y - 2
        
        start_idx = self.edit_scroll
        end_idx = min(start_idx + max_edit_display, len(fields))
        
        for i in range(start_idx, end_idx):
            idx = i - start_idx
            label, val = fields[i]
            attr = curses.color_pair(1) | curses.A_REVERSE if i == self.selected_row else curses.A_NORMAL
            
            display_str = f" {label:<22} : {val} ".ljust(w-4)
            self.stdscr.addstr(list_y + idx, 2, display_str, attr)
            
        scroll_indicator = f" Scroll [{start_idx+1}-{end_idx}/{len(fields)}] " if len(fields) > max_edit_display else ""
        self.stdscr.addstr(h-2, 2, f"[Esc] Back   [< >] Adjust values   {scroll_indicator}", curses.A_DIM)

    def render_picker(self, start_y, h, w):
        mon = self.current_edit_mon
        self.stdscr.addstr(start_y, 2, f"Select Mode for {mon['name']}", curses.color_pair(4) | curses.A_BOLD)
        # FIX: Changed x=1 to x=0
        self.stdscr.addstr(start_y + 1, 0, "├" + "─" * (w-2) + "┤", curses.color_pair(1))
        
        modes = mon["available_modes"]
        list_start_y = start_y + 2
        max_display = h - list_start_y - 2
        
        start_idx = self.picker_scroll
        end_idx = min(start_idx + max_display, len(modes))
        
        for i in range(start_idx, end_idx):
            idx = i - start_idx
            attr = curses.color_pair(1) | curses.A_REVERSE if i == self.selected_row else curses.A_NORMAL
            display_str = f" {modes[i]} ".ljust(w-4)
            self.stdscr.addstr(list_start_y + idx, 2, display_str, attr)
            
        self.stdscr.addstr(h-2, 2, "[Esc] Cancel   [Enter] Confirm Selection", curses.A_DIM)

    def run(self):
        while True:
            self.stdscr.clear()
            h, w = self.stdscr.getmaxyx()
            w = min(w - 1, 85) # Drop 1 column to prevent horizontal edge wrapping
            
            self.draw_box(1, h-1, w)
            start_y = self.render_header(w)
            
            if self.view_state == 0: self.render_monitors(start_y, h, w)
            elif self.view_state == 1: self.render_edit(start_y, h, w)
            elif self.view_state == 2: self.render_picker(start_y, h, w)
            elif self.view_state == 3: self.render_globals(start_y, h, w)
                
            self.stdscr.refresh()
            
            key = self.stdscr.getch()
            if key == ord('q'):
                break
            
            # HANDLE MOUSE CLICKS
            if key == curses.KEY_MOUSE:
                try:
                    _, mx, my, _, bstate = curses.getmouse()
                    if bstate & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED):
                        if my == 4 and self.view_state in [0, 3]:
                            # Map coordinates to the visual tab locations
                            if 2 <= mx <= 15:
                                self.view_state = 0
                                self.selected_row = 0
                            elif 18 <= mx <= 30:
                                self.view_state = 3
                                self.selected_row = 0
                except curses.error:
                    pass
                continue

            if key == 9: # TAB
                if self.view_state == 0: self.view_state = 3
                elif self.view_state == 3: self.view_state = 0
                self.selected_row = 0
                continue

            if self.view_state == 0: # Monitors
                max_row = len(self.monitors)
                if key in [curses.KEY_UP, ord('k')] and self.selected_row > 0:
                    self.selected_row -= 1
                elif key in [curses.KEY_DOWN, ord('j')] and self.selected_row < max_row:
                    self.selected_row += 1
                elif key in [10, 13]:
                    if self.selected_row == max_row:
                        LuaConfigManager.save_config(self.monitors, self.global_state)
                        self.stdscr.addstr(h-3, 2, " Configuration Saved Successfully! ", curses.color_pair(2) | curses.A_REVERSE)
                        self.stdscr.refresh()
                        curses.napms(1000)
                    else:
                        self.view_state = 1
                        self.current_edit_mon = self.monitors[self.selected_row]
                        self.selected_row = 0
                        self.edit_scroll = 0

            elif self.view_state == 3: # Globals
                max_row = 2
                if key in [curses.KEY_UP, ord('k')] and self.selected_row > 0:
                    self.selected_row -= 1
                elif key in [curses.KEY_DOWN, ord('j')] and self.selected_row < max_row:
                    self.selected_row += 1
                elif key in [curses.KEY_RIGHT, ord('l'), curses.KEY_LEFT, ord('h')]:
                    if self.selected_row == 0: self.global_state["vfr"] = not self.global_state["vfr"]
                    elif self.selected_row == 1: 
                        dir_val = 1 if key in [curses.KEY_RIGHT, ord('l')] else -1
                        self.global_state["vrr"] = (self.global_state["vrr"] + dir_val) % 3
                elif key in [10, 13]:
                    if self.selected_row == max_row:
                        LuaConfigManager.save_config(self.monitors, self.global_state)
                        self.stdscr.addstr(h-3, 2, " Configuration Saved Successfully! ", curses.color_pair(2) | curses.A_REVERSE)
                        self.stdscr.refresh()
                        curses.napms(1000)

            elif self.view_state == 1: # Edit
                mon = self.current_edit_mon
                fields_len = len(self._build_edit_fields(mon))
                max_edit_display = h - start_y - 4
                
                if key == 27: # Esc
                    self.view_state = 0
                    self.selected_row = self.monitors.index(mon)
                elif key in [curses.KEY_UP, ord('k')] and self.selected_row > 0:
                    self.selected_row -= 1
                    if self.selected_row < self.edit_scroll:
                        self.edit_scroll -= 1
                elif key in [curses.KEY_DOWN, ord('j')] and self.selected_row < fields_len - 1:
                    self.selected_row += 1
                    if self.selected_row >= self.edit_scroll + max_edit_display:
                        self.edit_scroll += 1
                elif key in [10, 13]: # Enter
                    if self.selected_row == 2: # Open Mode Picker
                        self.view_state = 2
                        self.selected_row = 0
                        self.picker_scroll = 0
                elif key in [curses.KEY_RIGHT, ord('l')]:
                    if self.selected_row == 0: mon["enabled"] = not mon["enabled"]
                    elif self.selected_row == 1: mon["target_identifier"] = f"desc:{mon['desc']}" if mon["desc"] else mon["name"]
                    elif self.selected_row == 3: 
                        dynamic_pos_list = POS_VARIANTS + [f"{mon['x']}x{mon['y']}"]
                        try: idx = dynamic_pos_list.index(mon["pos_str"])
                        except: idx = -1
                        mon["pos_str"] = dynamic_pos_list[(idx + 1) % len(dynamic_pos_list)]
                    elif self.selected_row == 4: 
                        if mon["scale"] == "auto": mon["scale"] = 0.25
                        else: mon["scale"] = round(mon["scale"] + 0.25, 2)
                    elif self.selected_row == 5: mon["transform"] = (mon["transform"] + 1) % 8
                    elif self.selected_row == 6: mon["vrr"] = (mon["vrr"] + 1) % 3
                    elif self.selected_row == 7: mon["bitdepth"] = 10 if mon["bitdepth"] == 8 else 8
                    elif self.selected_row == 8: mon["cm"] = CM_PROFILES[(CM_PROFILES.index(mon["cm"]) + 1) % len(CM_PROFILES)]
                    elif self.selected_row == 9: mon["sdr_eotf"] = SDR_EOTFS[(SDR_EOTFS.index(mon["sdr_eotf"]) + 1) % len(SDR_EOTFS)]
                    elif self.selected_row == 10: mon["sdr_brightness"] = min(2.0, round(mon["sdr_brightness"] + 0.1, 2))
                    elif self.selected_row == 11: mon["sdr_saturation"] = min(1.5, round(mon["sdr_saturation"] + 0.1, 2))
                    elif self.selected_row == 12:
                        other_mons = [""] + [m["name"] for m in self.monitors if m["name"] != mon["name"]]
                        try: idx = other_mons.index(mon["mirror"])
                        except: idx = 0
                        mon["mirror"] = other_mons[(idx + 1) % len(other_mons)]
                        
                elif key in [curses.KEY_LEFT, ord('h')]:
                    if self.selected_row == 0: mon["enabled"] = not mon["enabled"]
                    elif self.selected_row == 1: mon["target_identifier"] = mon["name"]
                    elif self.selected_row == 3: 
                        dynamic_pos_list = POS_VARIANTS + [f"{mon['x']}x{mon['y']}"]
                        try: idx = dynamic_pos_list.index(mon["pos_str"])
                        except: idx = 1
                        mon["pos_str"] = dynamic_pos_list[(idx - 1) % len(dynamic_pos_list)]
                    elif self.selected_row == 4:
                        if mon["scale"] != "auto":
                            mon["scale"] = round(mon["scale"] - 0.25, 2)
                            if mon["scale"] < 0.25: mon["scale"] = "auto"
                    elif self.selected_row == 5: mon["transform"] = (mon["transform"] - 1) % 8
                    elif self.selected_row == 6: mon["vrr"] = (mon["vrr"] - 1) % 3
                    elif self.selected_row == 7: mon["bitdepth"] = 8 if mon["bitdepth"] == 10 else 10
                    elif self.selected_row == 8: mon["cm"] = CM_PROFILES[(CM_PROFILES.index(mon["cm"]) - 1) % len(CM_PROFILES)]
                    elif self.selected_row == 9: mon["sdr_eotf"] = SDR_EOTFS[(SDR_EOTFS.index(mon["sdr_eotf"]) - 1) % len(SDR_EOTFS)]
                    elif self.selected_row == 10: mon["sdr_brightness"] = max(0.5, round(mon["sdr_brightness"] - 0.1, 2))
                    elif self.selected_row == 11: mon["sdr_saturation"] = max(0.5, round(mon["sdr_saturation"] - 0.1, 2))
                    elif self.selected_row == 12:
                        other_mons = [""] + [m["name"] for m in self.monitors if m["name"] != mon["name"]]
                        try: idx = other_mons.index(mon["mirror"])
                        except: idx = 1
                        mon["mirror"] = other_mons[(idx - 1) % len(other_mons)]

            elif self.view_state == 2: # Picker
                modes = self.current_edit_mon["available_modes"]
                list_start_y = start_y + 2
                max_display = h - list_start_y - 2
                
                if key == 27: # Esc
                    self.view_state = 1
                    self.selected_row = 2 # Mode field index
                elif key in [curses.KEY_UP, ord('k')] and self.selected_row > 0:
                    self.selected_row -= 1
                    if self.selected_row < self.picker_scroll:
                        self.picker_scroll -= 1
                elif key in [curses.KEY_DOWN, ord('j')] and self.selected_row < len(modes) - 1:
                    self.selected_row += 1
                    if self.selected_row >= self.picker_scroll + max_display:
                        self.picker_scroll += 1
                elif key in [10, 13]: # Enter
                    self.current_edit_mon["mode_str"] = modes[self.selected_row]
                    self.view_state = 1
                    self.selected_row = 2 # Return focus to Mode field

def main(stdscr):
    app = DuskyUI(stdscr)
    app.run()

if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
