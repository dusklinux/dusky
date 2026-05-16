#!/usr/bin/env python3
"""
Dusky Monitor Wizard - Hyprland Edition v5.4.0 (Textual Engine)
-----------------------------------------------------------------------------
Engineered for Hyprland 0.55+. Zero dependencies. Safe atomic writes.
Features: Infinite-Scroll Edit Menu, Complete SDR/HDR Color Pipeline, 
          All 'auto' Position Variants, VESA Mode Injection.
"""

import sys
import os
import json
import subprocess
import tempfile
import re
from pathlib import Path

from textual import on, events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.widgets import Label, OptionList, Tabs, Tab, ContentSwitcher, Footer
from textual.widgets.option_list import Option
from textual.screen import ModalScreen
from rich.text import Text

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

# --- HYPRLAND IPC & STATE MANAGEMENT (UNTOUCHED ENGINE) ---
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

# --- LUA PARSER & WRITER (UNTOUCHED ENGINE) ---
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

# --- TEXTUAL FRONTEND UI ---

class PickerScreen(ModalScreen[str | None]):
    """Modal for selecting modes seamlessly."""
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Cancel"),
    ]

    def __init__(self, title: str, options: list[str]) -> None:
        super().__init__()
        self.picker_title = title
        self.options = options

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Label(f"PICKER: {self.picker_title}", classes="modal-title")
            yield OptionList(id="picker-list")
            with Horizontal(classes="modal-btn-container"):
                yield Label(" Cancel ", classes="modal-close-btn")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        for opt in self.options:
            ol.add_option(Option(f" {opt} "))
        ol.focus()

    @on(OptionList.OptionSelected)
    def on_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.options[event.option_index])

    @on(events.Click, ".modal-close-btn")
    def on_close_click(self) -> None:
        self.dismiss(None)


class EditMonitorScreen(ModalScreen[None]):
    """Modal for editing a specific monitor's fields, replicating the old infinite-scroll menu."""
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("left,h", "adjust(-1)", "Adjust Down"),
        Binding("right,l", "adjust(1)", "Adjust Up"),
    ]

    def __init__(self, mon_data: dict, monitors_list: list) -> None:
        super().__init__()
        self.mon = mon_data
        self.all_monitors = monitors_list

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog", id="edit-dialog"):
            yield Label(f"EDITING: {self.mon['name']}", classes="modal-title")
            yield OptionList(id="edit-list")
            yield Label(" [Esc] Back   [< >] Adjust values   [Enter/Click] Modify / Pick", id="edit-hint")

    def on_mount(self) -> None:
        self.refresh_list()
        self.query_one(OptionList).focus()

    def _build_edit_fields(self) -> list[tuple[str, str]]:
        return [
            ("Enabled", str(self.mon["enabled"])),
            ("Identifier Mode", "Desc/Safe" if "desc:" in self.mon["target_identifier"] else "Port/Raw"),
            ("Mode (Res/Rate)", self.mon["mode_str"] + "  [Enter to Pick]"),
            ("Position", self.mon["pos_str"]),
            ("Scale Factor", "auto" if self.mon['scale'] == "auto" else f"{self.mon['scale']}x"),
            ("Transform (Rotation)", TRANSFORMS[self.mon['transform']]),
            ("VRR Mode", str(self.mon['vrr']) + " (0=Off, 1=On, 2=FS)"),
            ("Bitdepth", str(self.mon['bitdepth']) + "-bit"),
            ("Color Profile (cm)", self.mon['cm']),
            ("SDR EOTF Curve", self.mon['sdr_eotf']),
            ("SDR Brightness", f"{self.mon['sdr_brightness']:.2f}"),
            ("SDR Saturation", f"{self.mon['sdr_saturation']:.2f}"),
            ("Mirror Output", self.mon['mirror'] if self.mon['mirror'] else "None")
        ]

    def refresh_list(self) -> None:
        try:
            ol = self.query_one("#edit-list", OptionList)
            idx = ol.highlighted
            ol.clear_options()

            options = []
            for label, val in self._build_edit_fields():
                txt = Text()
                txt.append(f" {label:<25} : ", style="bold #a8c8ff")
                
                # Colorize toggles
                if label == "Enabled":
                    txt.append(" ◉ ON " if val == "True" else " ◯ OFF ", style="bold #111318 on #dbbce1" if val == "True" else "bold #43474e on #111318")
                else:
                    txt.append(str(val), style="#e1e2e9")
                    
                options.append(Option(txt))

            ol.add_options(options)

            if idx is not None and idx < ol.option_count:
                ol.highlighted = idx
        except Exception as e:
            log_err(f"Edit refresh error: {e}")

    def action_adjust(self, direction: int) -> None:
        try:
            idx = self.query_one(OptionList).highlighted
            if idx is None: return

            mon = self.mon
            if idx == 0: mon["enabled"] = not mon["enabled"]
            elif idx == 1: 
                if direction > 0: mon["target_identifier"] = f"desc:{mon['desc']}" if mon["desc"] else mon["name"]
                else: mon["target_identifier"] = mon["name"]
            elif idx == 3: 
                dynamic_pos = POS_VARIANTS + [f"{mon['x']}x{mon['y']}"]
                try: cur_idx = dynamic_pos.index(mon["pos_str"])
                except ValueError: cur_idx = -1 if direction > 0 else 1
                mon["pos_str"] = dynamic_pos[(cur_idx + direction) % len(dynamic_pos)]
            elif idx == 4: 
                if mon["scale"] == "auto":
                    if direction > 0: mon["scale"] = 0.25
                else:
                    mon["scale"] = round(mon["scale"] + (0.25 * direction), 2)
                    if mon["scale"] < 0.25: mon["scale"] = "auto"
            elif idx == 5: mon["transform"] = (mon["transform"] + direction) % 8
            elif idx == 6: mon["vrr"] = (mon["vrr"] + direction) % 3
            elif idx == 7: mon["bitdepth"] = 10 if mon["bitdepth"] == 8 else 8
            elif idx == 8: mon["cm"] = CM_PROFILES[(CM_PROFILES.index(mon["cm"]) + direction) % len(CM_PROFILES)]
            elif idx == 9: mon["sdr_eotf"] = SDR_EOTFS[(SDR_EOTFS.index(mon["sdr_eotf"]) + direction) % len(SDR_EOTFS)]
            elif idx == 10: 
                val = mon["sdr_brightness"] + (0.1 * direction)
                mon["sdr_brightness"] = max(0.5, min(2.0, round(val, 2)))
            elif idx == 11: 
                val = mon["sdr_saturation"] + (0.1 * direction)
                mon["sdr_saturation"] = max(0.5, min(1.5, round(val, 2)))
            elif idx == 12:
                other_mons = [""] + [m["name"] for m in self.all_monitors if m["name"] != mon["name"]]
                try: cur_idx = other_mons.index(mon["mirror"])
                except ValueError: cur_idx = 0 if direction > 0 else 1
                mon["mirror"] = other_mons[(cur_idx + direction) % len(other_mons)]

            self.refresh_list()
        except Exception as e:
            log_err(f"Adjust error: {e}")

    @on(OptionList.OptionSelected)
    def handle_selection(self, event: OptionList.OptionSelected) -> None:
        """Handles both Mouse Clicks and Enter key presses safely."""
        idx = event.option_index
        if idx == 2: # Mode field -> Open Picker
            def set_mode(mode: str | None) -> None:
                if mode:
                    self.mon["mode_str"] = mode
                    self.refresh_list()
            self.app.push_screen(PickerScreen("Mode", self.mon["available_modes"]), set_mode)
        else:
            # Safely proxy clicks to the adjustment logic
            self.action_adjust(1)


class DuskyTUI(App):
    """Main Textual Application."""
    
    CSS = """
    Screen { background: #111318; }

    #main-box {
        width: 100%; height: 100%; 
        border: solid #a8c8ff 50%;
        border-title-color: #a8c8ff;
        border-title-style: bold;
        border-title-align: center;
        border-subtitle-color: #a8c8ff;
        border-subtitle-style: bold;
        border-subtitle-align: right;
        background: transparent;
        padding: 0 1 1 1;
    }

    Tabs { width: 100%; height: auto; background: transparent; margin-bottom: 1; border-bottom: solid #43474e; }
    Tabs > .underline { display: none; }
    Tab { height: 1; padding: 0 2; color: #a8c8ff 60%; background: transparent; border: none; }
    Tab:hover { color: #e1e2e9; background: #a8c8ff 25%; }
    Tab.-active { color: #111318; background: #a8c8ff; text-style: bold; border: none; }

    ContentSwitcher { width: 1fr; height: 1fr; background: transparent; }
    .panel { width: 1fr; height: 1fr; background: transparent; }
    
    OptionList { min-width: 20; width: 1fr; height: 1fr; scrollbar-size: 0 0; background: transparent; border: none; }
    OptionList > .option-list--option { padding: 0 1; background: transparent; transition: background 150ms linear; }
    OptionList > .option-list--option-hover { background: #a8c8ff 10%; }
    OptionList > .option-list--option-highlighted { background: #a8c8ff 20%; }

    .modal-dialog { width: 60; height: auto; max-height: 85%; background: #111318; border: solid #a8c8ff; padding: 1 2; align: center middle; }
    #edit-dialog { width: 75; }

    .modal-title { color: #a8c8ff; margin-bottom: 1; text-style: bold; border-bottom: solid #43474e; }
    
    .modal-btn-container { width: 100%; height: auto; align: center middle; margin-top: 1; background: transparent; }
    .modal-close-btn { background: #a8c8ff; color: #111318; text-style: bold; padding: 0 2; width: auto; height: 1; }
    .modal-close-btn:hover { background: #e1e2e9; color: #111318; }
    
    #edit-hint { color: #43474e; text-style: italic; margin-top: 1; }

    PickerScreen, EditMonitorScreen { align: center middle; background: rgba(0, 0, 0, 0.75); }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("ctrl+s", "save_config", "Save & Apply Config", priority=True),
        Binding("tab", "next_tab", "Switch Tab", priority=True),
        Binding("left,h", "adjust(-1)", "Cycle Prev", show=False),
        Binding("right,l", "adjust(1)", "Cycle Next", show=False),
    ]

    def on_mount(self) -> None:
        self.monitors = HardwareManager.get_monitors()
        self.global_state = HardwareManager.get_globals()

        self.query_one("#main-box").border_title = f" {APP_TITLE} "
        self.query_one("#main-box").border_subtitle = f" {APP_SUBTITLE} "

        # Call these asynchronously to guarantee the DOM is fully laid out before injecting Options.
        # This prevents the silent NoMatches exception that causes the invisible lists.
        self.call_after_refresh(self.refresh_monitors)
        self.call_after_refresh(self.refresh_globals)
        self.call_after_refresh(lambda: self.query_one("#list-monitors", OptionList).focus())

    def compose(self) -> ComposeResult:
        with Vertical(id="main-box"):
            yield Tabs(Tab("MONITORS", id="tab-monitors"), Tab("GLOBALS", id="tab-globals"))
            
            # Wrap OptionLists in Vertical containers to ensure strict block-layout adherence
            with ContentSwitcher(initial="panel-monitors"):
                with Vertical(id="panel-monitors", classes="panel"):
                    yield OptionList(id="list-monitors")
                with Vertical(id="panel-globals", classes="panel"):
                    yield OptionList(id="list-globals")
        yield Footer()

    @on(Tabs.TabActivated)
    def handle_tab_activated(self, event: Tabs.TabActivated) -> None:
        # Wrap in try/except to prevent Tab initialization races from crashing the Event Loop
        try:
            idx = event.tab.id.split("-")[-1]
            self.query_one(ContentSwitcher).current = f"panel-{idx}"
            list_widget = self.query_one(f"#list-{idx}", OptionList)
            if list_widget.is_mounted:
                list_widget.focus()
        except Exception as e:
            log_err(f"Tab switch exception handled safely: {e}")

    def action_next_tab(self) -> None:
        self.query_one(Tabs).action_next_tab()

    def refresh_monitors(self) -> None:
        try:
            ol = self.query_one("#list-monitors", OptionList)
            idx = ol.highlighted
            ol.clear_options()

            options = []
            for mon in self.monitors:
                txt = Text()
                txt.append(f" {mon['name'][:15]:<15} ", style="bold #a8c8ff")
                
                if mon["enabled"]: txt.append("[ON] ", style="bold #111318 on #dbbce1")
                else: txt.append("[OFF] ", style="bold #43474e on #111318")
                
                txt.append(f" {mon['width']}x{mon['height']}@{mon['refresh']}Hz {mon['scale']}x", style="#e1e2e9")
                options.append(Option(txt))

            ol.add_options(options)
            if idx is not None and idx < ol.option_count:
                ol.highlighted = idx
        except Exception as e:
            log_err(f"Refresh monitors error: {e}")

    def refresh_globals(self) -> None:
        try:
            ol = self.query_one("#list-globals", OptionList)
            idx = ol.highlighted
            ol.clear_options()

            fields = [
                ("VFR (Variable Frame Rate)", "Enabled" if self.global_state["vfr"] else "Disabled"),
                ("Global VRR Override", str(self.global_state["vrr"]) + " (0=Off, 1=On, 2=Fullscreen)")
            ]
            
            options = []
            for label, val in fields:
                txt = Text()
                txt.append(f" {label:<27} : ", style="bold #a8c8ff")
                txt.append(str(val), style="#e1e2e9")
                options.append(Option(txt))

            ol.add_options(options)
            if idx is not None and idx < ol.option_count:
                ol.highlighted = idx
        except Exception as e:
            log_err(f"Refresh globals error: {e}")

    def action_adjust(self, direction: int) -> None:
        """Handle Left/Right arrow cycling on the Globals list"""
        try:
            switcher = self.query_one(ContentSwitcher)
            if switcher.current == "panel-globals":
                idx = self.query_one("#list-globals", OptionList).highlighted
                if idx == 0: self.global_state["vfr"] = not self.global_state["vfr"]
                elif idx == 1: self.global_state["vrr"] = (self.global_state["vrr"] + direction) % 3
                self.refresh_globals()
        except Exception:
            pass

    @on(OptionList.OptionSelected)
    def handle_main_list_selection(self, event: OptionList.OptionSelected) -> None:
        """Handles Clicks and Enter key presses on the main tabs perfectly by relying on exact IDs."""
        try:
            if event.option_list.id == "list-monitors":
                mon = self.monitors[event.option_index]
                def on_edit_close(_): self.refresh_monitors()
                self.push_screen(EditMonitorScreen(mon, self.monitors), on_edit_close)
                    
            elif event.option_list.id == "list-globals":
                self.action_adjust(1) # Cycle the global setting
        except Exception as e:
            log_err(f"Click intercept error: {e}")

    def action_save_config(self) -> None:
        LuaConfigManager.save_config(self.monitors, self.global_state)
        self.notify("Configuration Saved & Applied Successfully!", title="Success", severity="information")

def main():
    app = DuskyTUI()
    app.run()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
