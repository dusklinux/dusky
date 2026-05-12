#!/usr/bin/env python3
import os
import re
import json
import subprocess
import colorsys
import shlex
import shutil
import asyncio
import math
from pathlib import Path
from typing import Any
from collections import deque

from textual import on, events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.widgets import Label, Input, Tabs, Tab, ContentSwitcher, OptionList, Markdown
from textual.widgets.option_list import Option, OptionDoesNotExist
from textual.screen import ModalScreen
from textual.reactive import reactive
from textual.theme import Theme
from textual.timer import Timer
from textual.widget import Widget

from rich.text import Text

from python.frontend.core_types import ConfigItem, BaseEngine

# =============================================================================
# GLOBAL CACHE & REGEX COMPILE (Optimization)
# =============================================================================

_AUDIO_PLAYER_CACHE: str | None = None

_RE_RGB = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)")
_RE_HSL = re.compile(r"hsla?\(\s*([\d.]+)\s*,\s*([\d.]+)%?\s*,\s*([\d.]+)%?")
_RE_OKLCH = re.compile(r"oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)")
_RE_RGBA_ALPHA = re.compile(r"rgba\([^,]+,[^,]+,[^,]+,\s*([0-9.]+)\)")
_RE_HSLA_ALPHA = re.compile(r"hsla\([^,]+,[^,]+,[^,]+,\s*([0-9.]+)\)")

# =============================================================================
# COLOR UTILITIES
# =============================================================================

KNOWN_COLORS = {
    "Red": (255, 0, 0), "Green": (0, 128, 0), "Lime": (0, 255, 0),
    "Blue": (0, 0, 255), "Yellow": (255, 255, 0), "Cyan": (0, 255, 255),
    "Magenta": (255, 0, 255), "White": (255, 255, 255), "Black": (0, 0, 0),
    "Gray": (128, 128, 128), "Silver": (192, 192, 192), "Maroon": (128, 0, 0),
    "Olive": (128, 128, 0), "Purple": (128, 0, 128), "Teal": (0, 128, 128),
    "Navy": (0, 0, 128), "Orange": (255, 165, 0), "Pink": (255, 192, 203),
    "Brown": (165, 42, 42), "Indigo": (75, 0, 130), "Violet": (238, 130, 238),
    "Gold": (255, 215, 0), "Coral": (255, 127, 80), "Salmon": (250, 128, 114),
    "Khaki": (240, 230, 140), "Plum": (221, 160, 221), "Turquoise": (64, 224, 208),
    "Crimson": (220, 20, 60), "Azure": (240, 255, 255), "Beige": (245, 245, 220),
    "Chocolate": (210, 105, 30), "Tomato": (255, 99, 71), "Lavender": (230, 230, 250)
}

CYCLE_COLORS = ["Red", "Lime", "Blue", "Yellow", "Cyan", "Magenta", "White", "Black"]

def parse_color_format(val: str) -> str:
    val = str(val).strip().lower()
    if val.startswith("0x"): return "0xhex"
    if val.startswith("#"): return "hex"
    if val.startswith("rgba"): return "rgba"
    if val.startswith("rgb"): return "rgb"
    if val.startswith("hsla"): return "hsla"
    if val.startswith("hsl"): return "hsl"
    if val.startswith("oklch"): return "oklch"
    return "hex"

def color_to_rgb(val: str) -> tuple[int, int, int]:
    val = str(val).strip().lower()
    if val.startswith("0x"):
        v = val[2:]
        if len(v) == 8: v = v[2:] 
        if len(v) >= 6:
            try: return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
            except ValueError: pass
    if val.startswith("#"):
        v = val[1:]
        if len(v) in (3, 4): 
            try: return (int(v[0]*2, 16), int(v[1]*2, 16), int(v[2]*2, 16))
            except ValueError: pass
        if len(v) >= 6: 
            try: return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
            except ValueError: pass
    
    m_rgb = _RE_RGB.match(val)
    if m_rgb: return (int(m_rgb.group(1)), int(m_rgb.group(2)), int(m_rgb.group(3)))
        
    m_hsl = _RE_HSL.match(val)
    if m_hsl:
        h, s, l_ = float(m_hsl.group(1))/360.0, float(m_hsl.group(2))/100.0, float(m_hsl.group(3))/100.0
        r, g, b = colorsys.hls_to_rgb(h, l_, s)
        return (int(r*255), int(g*255), int(b*255))
        
    m_oklch = _RE_OKLCH.match(val)
    if m_oklch:
        l_val, c_val, h_val = float(m_oklch.group(1)), float(m_oklch.group(2)), float(m_oklch.group(3))
        r, g, b = colorsys.hls_to_rgb(h_val/360.0, l_val, min(c_val*2.5, 1.0))
        return (max(0, min(255, int(r*255))), max(0, min(255, int(g*255))), max(0, min(255, int(b*255))))
        
    return (128, 128, 128)

def get_color_name(r: int, g: int, b: int) -> str:
    best_name = "Unknown"
    best_dist = float('inf')
    for name, color in KNOWN_COLORS.items():
        d = (r-color[0])**2 + (g-color[1])**2 + (b-color[2])**2
        if d < best_dist:
            best_dist = d
            best_name = name
    return best_name

def format_rgb(color_name: str, fmt: str, original_val: str) -> str:
    r, g, b = KNOWN_COLORS.get(color_name, (128,128,128))
    
    if fmt == "hex":
        if len(original_val) == 9 and original_val.startswith("#"): return f"#{r:02x}{g:02x}{b:02x}{original_val[7:9]}"
        return f"#{r:02x}{g:02x}{b:02x}"
        
    if fmt == "0xhex":
        alpha = "ff"
        if original_val.startswith("0x") and len(original_val) == 10: alpha = original_val[2:4]
        return f"0x{alpha}{r:02x}{g:02x}{b:02x}"
        
    if fmt == "rgb": return f"rgb({r}, {g}, {b})"
        
    if fmt == "rgba":
        alpha = "1.0"
        m = _RE_RGBA_ALPHA.search(original_val)
        if m: alpha = m.group(1)
        return f"rgba({r}, {g}, {b}, {alpha})"
        
    if fmt in ("hsl", "hsla"):
        h, l, s = colorsys.rgb_to_hls(r/255.0, g/255.0, b/255.0)
        h_deg, s_pct, l_pct = int(h * 360), int(s * 100), int(l * 100)
        if fmt == "hsl": return f"hsl({h_deg}, {s_pct}%, {l_pct}%)"
        else:
            alpha = "1.0"
            m = _RE_HSLA_ALPHA.search(original_val)
            if m: alpha = m.group(1)
            return f"hsla({h_deg}, {s_pct}%, {l_pct}%, {alpha})"
            
    if fmt == "oklch":
        oklch_map = {
            "Red": "oklch(0.628 0.258 29.23)", "Lime": "oklch(0.866 0.295 142.5)",
            "Blue": "oklch(0.452 0.313 264.05)", "Yellow": "oklch(0.968 0.211 109.77)",
            "Cyan": "oklch(0.905 0.183 195.58)", "Magenta": "oklch(0.702 0.322 328.36)",
            "White": "oklch(1.0 0 0)", "Black": "oklch(0.0 0 0)",
        }
        return oklch_map.get(color_name, "oklch(0.5 0.2 180)")
        
    return f"#{r:02x}{g:02x}{b:02x}"

def load_matugen_json(file_path: Path) -> dict[str, str] | None:
    if not file_path.exists(): return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError): return None

# =============================================================================
# MODALS & OVERLAYS
# =============================================================================

class TextInputOverlay(ModalScreen[str | None]):
    def __init__(self, prompt: str, default: str) -> None:
        super().__init__()
        self.prompt_text = prompt
        self.default_text = default

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Label(self.prompt_text, id="modal-title")
            yield Input(value=self.default_text, id="modal-input")
            yield Label("Press Enter to save • Esc to cancel", id="modal-hint")
            with Horizontal(classes="modal-btn-container"):
                yield Label(" Cancel ", classes="modal-close-btn")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def handle_submit(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value)
        
    @on(events.Click, ".modal-close-btn")
    def on_close_click(self) -> None:
        self.dismiss(None)
        
    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)

class PickerScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("up,k", "cursor_up", "Up"),
        Binding("down,j", "cursor_down", "Down"),
    ]

    def __init__(self, title: str, options: list[str], hints: list[str]) -> None:
        super().__init__()
        self.picker_title = title
        self.options = options
        self.hints = hints

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog"):
            yield Label(f"PICKER: {self.picker_title}", id="picker-title")
            yield OptionList(id="picker-list")
            with Horizontal(classes="modal-btn-container"):
                yield Label(" Cancel ", classes="modal-close-btn")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        options_to_add = []
        for i, opt in enumerate(self.options):
            hint = self.hints[i] if i < len(self.hints) else ""
            txt = Text()
            txt.append(f" {opt} ", style="bold")
            if hint:
                txt.append(" - ")
                txt.append(hint, style=f"italic {self.app.theme_colors['muted']}")
            options_to_add.append(Option(txt))
            
        ol.add_options(options_to_add)
        ol.focus()

    @on(OptionList.OptionSelected)
    def on_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.options[event.option_index])

    def action_cursor_up(self) -> None: self.query_one(OptionList).action_cursor_up()
    def action_cursor_down(self) -> None: self.query_one(OptionList).action_cursor_down()
        
    @on(events.Click, ".modal-close-btn")
    def on_close_click(self) -> None:
        self.dismiss(None)
        
    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)

class SearchScreen(ModalScreen[tuple[int, int] | None]):
    BINDINGS = [
        Binding("down,j", "cursor_down", "Down"),
        Binding("up,k", "cursor_up", "Up"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="search-dialog"):
            yield Label("FUZZY FIND (Ctrl+F)", id="modal-title")
            yield Input(placeholder="Type to filter configurations...", id="search-input")
            yield OptionList(id="search-list")
            with Horizontal(classes="modal-btn-container"):
                yield Label(" Cancel ", classes="modal-close-btn")

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        self._search_cache = []
        for tab_idx, tab_items in self.app.schema.items():
            tab_name = self.app.tabs[tab_idx] if tab_idx < len(self.app.tabs) else f"Tab {tab_idx}"
            for item_idx, item in enumerate(tab_items):
                haystack = f"{tab_name} {item.label} {item.key} {item.type_}".lower().replace(" ", "")
                self._search_cache.append((tab_idx, item_idx, item, tab_name, haystack))
        self._populate_list("")

    @on(Input.Changed)
    def handle_input(self, event: Input.Changed) -> None:
        self._populate_list(event.value)

    def _populate_list(self, query: str) -> None:
        ol = self.query_one(OptionList)
        ol.clear_options()
        self.results = []
        
        query = query.lower().replace(" ", "")
        options_to_add = []
        
        for tab_idx, item_idx, item, tab_name, haystack in self._search_cache:
            match = True
            if query:
                q_idx, s_idx = 0, 0
                while q_idx < len(query) and s_idx < len(haystack):
                    if query[q_idx] == haystack[s_idx]: q_idx += 1
                    s_idx += 1
                match = (q_idx == len(query))
            
            if match:
                txt = Text()
                txt.append(f"[{tab_name}] ", style=self.app.theme_colors["accent"])
                txt.append(item.label, style="bold")
                if item.hints:
                    txt.append(f" - {item.hints[0]}", style=f"italic {self.app.theme_colors['muted']}")
                options_to_add.append(Option(txt, id=f"search_{tab_idx}_{item_idx}"))
                self.results.append((tab_idx, item_idx))
                
        ol.add_options(options_to_add)

    @on(OptionList.OptionSelected)
    def on_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_index is not None and event.option_index < len(self.results):
            self.dismiss(self.results[event.option_index])

    @on(Input.Submitted)
    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        ol = self.query_one(OptionList)
        if ol.highlighted is not None and ol.highlighted < len(self.results):
            self.dismiss(self.results[ol.highlighted])

    def action_cursor_down(self) -> None: self.query_one(OptionList).action_cursor_down()
    def action_cursor_up(self) -> None: self.query_one(OptionList).action_cursor_up()
        
    @on(events.Click, ".modal-close-btn")
    def on_close_click(self) -> None:
        self.dismiss(None)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)

class DiffScreen(ModalScreen[None]):
    def compose(self) -> ComposeResult:
        with Vertical(id="diff-dialog"):
            yield Label("MODIFICATIONS (From Launch)", id="modal-title")
            yield OptionList(id="diff-list")
            with Horizontal(classes="modal-btn-container"):
                yield Label(" Close ", classes="modal-close-btn")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        added_any = False
        
        for tab_idx, tab_items in self.app.schema.items():
            for item in tab_items:
                str_val = str(item.value)
                str_init = str(item.initial_value)
                if str_val != str_init:
                    added_any = True
                    txt = Text()
                    txt.append(f"[{self.app.tabs[tab_idx]}] ", style=self.app.theme_colors["accent"])
                    txt.append(f"{item.label}: ", style="bold")
                    txt.append(f"{str_init} ", style=f"strike {self.app.theme_colors['error']}")
                    txt.append("➜ ", style=self.app.theme_colors["muted"])
                    txt.append(f"{str_val}", style=f"bold {self.app.theme_colors['success']}")
                    ol.add_option(Option(txt, disabled=True))
                    
        if not added_any:
            ol.add_option(Option("No changes detected from initial load state.", disabled=True))
        
    @on(events.Click, ".modal-close-btn")
    def on_close_click(self) -> None:
        self.dismiss(None)

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)

class ShortcutsInfoScreen(ModalScreen[None]):
    def compose(self) -> ComposeResult:
        with Vertical(id="shortcuts-dialog"):
            yield Label("KEYBOARD SHORTCUTS", id="modal-title")
            yield OptionList(id="shortcuts-list")
            with Horizontal(classes="modal-btn-container"):
                yield Label(" Close ", classes="modal-close-btn")
                
    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        bindings_info = [
            ("q, ctrl+c", "Quit the application"),
            ("f1", "Show this shortcuts page"),
            ("?", "Toggle item documentation panel"),
            ("ctrl+f", "Fuzzy search all options"),
            ("/", "Inline search in current tab"),
            ("escape", "Clear inline search / Close modals"),
            ("tab", "Switch to Next Tab"),
            ("shift+tab", "Switch to Previous Tab"),
            ("d", "Show pending or modified items (Diff)"),
            ("u", "Undo last change"),
            ("ctrl+r", "Redo last undone change"),
            ("ctrl+t", "Toggle between Auto and Batch save modes"),
            ("ctrl+s", "Commit all pending changes (only available in Batch mode)"),
            ("enter", "Trigger action / Input string / Open Picker"),
            ("j, down", "Move cursor down"),
            ("k, up", "Move cursor up"),
            ("h, left", "Adjust value down / Cycle previous option"),
            ("l, right", "Adjust value up / Cycle next option"),
            ("g", "Scroll to top of list"),
            ("G", "Scroll to bottom of list"),
            ("ctrl+u, page_up", "Page up"),
            ("ctrl+d, page_down", "Page down"),
            ("r", "Reset highlighted item to default"),
            ("R", "Reset entire page to defaults"),
        ]
        
        for keys, desc in bindings_info:
            txt = Text()
            txt.append(f"{keys:<20}", style=self.app.theme_colors["accent"] + " bold")
            txt.append(" ➜ ", style=self.app.theme_colors["muted"])
            txt.append(desc, style=self.app.theme_colors["fg"])
            ol.add_option(Option(txt, disabled=True))
        
    @on(events.Click, ".modal-close-btn")
    def on_close_click(self) -> None:
        self.dismiss(None)
        
    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)

# =============================================================================
# INTERACTIVE COMPONENTS
# =============================================================================

class ConfigOptionList(OptionList):
    BINDINGS = [
        Binding("enter", "app.submit_current", "Action"),
        Binding("j,down", "cursor_down", "Down"),
        Binding("k,up", "cursor_up", "Up"),
        Binding("g", "scroll_top", "Top"),
        Binding("G", "scroll_bottom", "Bottom"),
        Binding("h,left,backspace", "app.adjust(-1)", "Adjust Down"),
        Binding("l,right", "app.adjust(1)", "Adjust Up"),
        Binding("r", "app.reset_item", "Reset"),
        Binding("R", "app.reset_all", "Reset Page"),
        Binding("ctrl+d,page_down", "page_down", "Page Down"),
        Binding("ctrl+u,page_up", "page_up", "Page Up"),
    ]
    
    last_highlighted_id: str | None = None
    _mouse_down_highlight: int | None = None
    _last_click_x: int = 0

    def action_scroll_top(self) -> None: self.highlighted = 0
    def action_scroll_bottom(self) -> None:
        if self.option_count > 0: self.highlighted = self.option_count - 1
    def action_page_down(self) -> None:
        if self.option_count == 0: return
        idx = self.highlighted if self.highlighted is not None else 0
        self.highlighted = min(self.option_count - 1, idx + 10)
    def action_page_up(self) -> None:
        if self.option_count == 0: return
        idx = self.highlighted if self.highlighted is not None else 0
        self.highlighted = max(0, idx - 10)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._mouse_down_highlight = self.highlighted
        self._last_click_x = event.x

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        if hasattr(super(), "watch_scroll_y"):
            super().watch_scroll_y(old_value, new_value)
        if hasattr(self.app, "_update_scroll_indicators"): 
            self.app._update_scroll_indicators()
            
    def watch_max_scroll_y(self, old_value: float, new_value: float) -> None:
        if hasattr(super(), "watch_max_scroll_y"):
            super().watch_max_scroll_y(old_value, new_value)
        if hasattr(self.app, "_update_scroll_indicators"): 
            self.app._update_scroll_indicators()
            
    def on_resize(self, event: events.Resize) -> None:
        if hasattr(self.app, "_update_scroll_indicators"): self.app._update_scroll_indicators()

class ScrollIndicator(Label):
    _dragging: bool = False
    _max_scroll_y: float = 0
    _track_height: int = 0

    def update_scroll(self, scroll_y: float, max_scroll_y: float, viewport_height: float, virtual_height: float) -> None:
        if max_scroll_y <= 0 or virtual_height <= 0 or viewport_height <= 2:
            self.display = False; return
        
        self.display = True
        self._max_scroll_y = max_scroll_y
        self._track_height = int(viewport_height) - 2
        
        if self._track_height < 1:
            self.update("▲\n▼"); return
            
        thumb_size = max(1, int(self._track_height * (viewport_height / virtual_height)))
        max_pos = self._track_height - thumb_size
        pos = int((scroll_y / max_scroll_y) * max_pos) if max_scroll_y > 0 else 0
            
        lines = ["▲"]
        lines.extend(["│"] * pos)
        lines.extend(["┃"] * thumb_size)
        lines.extend(["│"] * (self._track_height - pos - thumb_size))
        lines.append("▼")
        
        txt = Text()
        for i, char in enumerate(lines):
            style = "bold" if char in ("▲", "▼") else ("dim" if char == "│" else "")
            txt.append(char + ("\n" if i < len(lines)-1 else ""), style=style)
            
        self.update(txt)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if self._max_scroll_y <= 0: return
        try: tab_idx = int(self.id.split("-")[1])
        except (AttributeError, IndexError, ValueError): return
        
        ol = self.app.query_one(f"#list-{tab_idx}", ConfigOptionList)
        if event.y == 0: ol.scroll_y -= 1
        elif event.y == self.size.height - 1: ol.scroll_y += 1
        else:
            self._dragging = True
            self.capture_mouse()
            self._jump_to_y(event.y, ol)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._dragging:
            try: tab_idx = int(self.id.split("-")[1])
            except (AttributeError, IndexError, ValueError): return
            ol = self.app.query_one(f"#list-{tab_idx}", ConfigOptionList)
            self._jump_to_y(event.y, ol)

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._dragging:
            self._dragging = False
            self.release_mouse()

    def _jump_to_y(self, y: float, ol: ConfigOptionList) -> None:
        if self._track_height < 1: return
        relative_y = max(0, min(self._track_height - 1, y - 1))
        ratio = relative_y / (self._track_height - 1) if self._track_height > 1 else 0
        ol.scroll_y = int(ratio * self._max_scroll_y)

class Shortcut(Label):
    def __init__(self, key_text: str, label: str, action_name: str | None = None, **kwargs) -> None:
        super().__init__(classes="footer-shortcut", **kwargs)
        self.key_text = key_text
        self.label_text = label
        self.action_name = action_name

    def render(self) -> Text:
        txt = Text()
        txt.append(f"[{self.key_text}] ", style=self.app.theme_colors["accent"])
        txt.append(self.label_text, style=self.app.theme_colors["fg"])
        return txt

    async def on_click(self) -> None:
        if self.action_name: await self.app.run_action(self.action_name)

    def blink(self) -> None:
        self.add_class("-active")
        self.set_timer(0.2, lambda: self.remove_class("-active"))

class FileLink(Label):
    path = reactive("")
    
    def render(self) -> Text:
        txt = Text()
        txt.append(" 󰈔 Edit File ", style=self.app.theme_colors["accent"] + " bold underline")
        return txt
        
    def watch_path(self, new_val: str) -> None:
        if new_val:
            self.tooltip = f"Edit externally:\n{new_val}"
        
    def on_click(self, event: events.Click) -> None:
        if not self.path: return
        expanded_path = Path(self.path).expanduser().resolve()
        expanded_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            expanded_path.touch(exist_ok=True)
            if event.button == 1:
                subprocess.Popen(
                    ["xdg-open", str(expanded_path)], 
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            elif event.button == 3:
                editor_env = os.environ.get("VISUAL", os.environ.get("EDITOR", "nano"))
                editor_cmd = shlex.split(editor_env)
                with self.app.suspend():
                    subprocess.run([*editor_cmd, str(expanded_path)])
        except (FileNotFoundError, OSError):
            if hasattr(self.app, "notify_status"):
                getattr(self.app, "notify_status")("Error resolving path or launching editor.")

class ModeButton(Label):
    def on_mount(self) -> None:
        self.update_mode()

    def update_mode(self) -> None:
        txt = Text()
        txt.append(" Mode: ", style=self.app.theme_colors["fg"])
        mode_str = "AUTO" if self.app.auto_save else "BATCH"
        color = self.app.theme_colors["success"] if self.app.auto_save else self.app.theme_colors["warning"]
        txt.append(mode_str, style=color + " bold")
        
        pending = getattr(self.app, 'pending_commits', set())
        if not self.app.auto_save and pending:
            txt.append(f" │ Pending: {len(pending)}", style=self.app.theme_colors["fg"])
            
        self.update(txt)

    async def on_click(self) -> None:
        await self.app.run_action("toggle_save_mode")

class FlowContainer(Widget):
    def on_mount(self) -> None:
        self.styles.height = "auto"
        self.styles.width = "100%"
        self.call_after_refresh(self.reflow)

    def on_resize(self, event: events.Resize) -> None:
        self.reflow()

    def reflow(self) -> None:
        if not self.is_mounted: return
        width = self.size.width
        
        if width <= 0:
            self.call_after_refresh(self.reflow)
            return
            
        visible_children = []
        for child in self.children:
            if not child.display: continue
            child.styles.position = "absolute"
            cw = child.outer_size.width
            if cw <= 0: cw = len(child.render().plain) + 2 
            ch = child.outer_size.height
            if ch <= 0: ch = 1
            visible_children.append((child, cw, ch))

        if not visible_children:
            self.styles.height = 0
            return

        N = len(visible_children)
        max_item_w = 0
        max_item_h = 1
        for _, cw, ch in visible_children:
            max_item_w = max(max_item_w, cw)
            max_item_h = max(max_item_h, ch)

        col_w_needed = max_item_w
        max_cols_possible = max(1, width // col_w_needed)

        if max_cols_possible >= N:
            rows = 1
            cols = N
        else:
            rows = math.ceil(N / max_cols_possible)
            cols = math.ceil(N / rows)

        if cols > max_cols_possible:
            cols = max_cols_possible

        actual_col_width = width / cols if cols > 0 else width

        for i, (child, _, _) in enumerate(visible_children):
            r = i // cols
            c = i % cols
            x = int(c * actual_col_width)
            y = r * max_item_h
            child.styles.offset = (x, y)

        target_height = rows * max_item_h
        if self.styles.height != target_height:
            self.styles.height = target_height

class AppFooter(Vertical):
    status_msg = reactive("")

    def compose(self) -> ComposeResult:
        with FlowContainer(id="footer-shortcuts-container"):
            # Cleaned up visual footer footprint - keybind logic remains globally intact
            yield Shortcut("ctrl+s", "Batch Save", "save_batch", id="shortcut-ctrl-s")
            yield Shortcut("/", "Jump", "focus_local_search", id="shortcut-slash")
            yield Shortcut("ctrl+f", "Search", "search", id="shortcut-ctrl-f")
            yield Shortcut("r", "Reset Item", "reset_item", id="shortcut-r")
            yield Shortcut("R", "Reset Page", "reset_all", id="shortcut-R")
            yield Shortcut("q", "Quit", "quit", id="shortcut-q")

        with Horizontal(id="footer-bottom-row"):
            yield FileLink(id="file-link")
            yield Label(" │ ", classes="footer-sep")
            yield ModeButton(id="footer-legend", classes="mode-btn")
            yield Label("", id="status-bar")

    def on_resize(self, event: events.Resize) -> None:
        try: self.query_one(FlowContainer).reflow()
        except Exception: pass

    def watch_status_msg(self, new_val: str) -> None:
        try:
            for bar in self.query("#status-bar"):
                if new_val:
                    txt = Text()
                    txt.append(" │ Status: ", style=self.app.theme_colors["accent"])
                    txt.append(new_val, style=self.app.theme_colors["error"])
                    bar.update(txt)
                    bar.display = True
                else:
                    bar.display = False
        except Exception:
            pass

# =============================================================================
# MAIN APPLICATION
# =============================================================================

class DuskyTUI(App):
    CSS = """
    Screen { background: $background; }
    
    #main-box {
        width: 100%; height: 100%;
        border: solid $primary 50%;
        border-title-color: $primary;
        border-title-style: bold;
        border-title-align: center;
        border-subtitle-color: $primary;
        border-subtitle-style: bold;
        border-subtitle-align: right;
        background: transparent;
        padding: 0 1 1 1;
    }
    
    #tab-bar { width: 100%; height: 1; margin-bottom: 1; background: transparent; }
    
    #tabs-container { width: 1fr; height: 1; overflow-x: auto; scrollbar-size: 0 0; }
    
    .tab-arrow {
        width: 3; height: 1; content-align: center middle;
        background: $background; color: $primary; text-style: bold; display: none;
    }
    .tab-arrow:hover { color: $foreground; background: $primary 25%; }
    
    #content-area { height: 1fr; layout: horizontal; }
    
    ContentSwitcher { width: 1fr; height: 1fr; background: transparent; }
    
    #help-panel {
        width: 35%; height: 100%; min-width: 25; border-left: solid $primary;
        display: none; background: $background; padding: 1 2; overflow-y: auto;
    }
    
    #content-area.-show-help ContentSwitcher { width: 65%; }
    #content-area.-show-help #help-panel { display: block; }
    
    Tabs { width: auto; min-width: 100%; height: 1; background: transparent; }
    Tabs > .underline { display: none; }
    Tab { height: 1; padding: 0 1; color: $primary 60%; background: transparent; border: none; }
    Tab:hover { color: $foreground; background: $primary 25%; }
    Tab.-active { color: $background; background: $primary; text-style: bold; border: none; }
    
    .list-wrapper { height: 1fr; }
    ConfigOptionList { min-width: 20; width: 1fr; height: 1fr; scrollbar-size: 0 0; background: transparent; border: none; }
    ConfigOptionList > .option-list--option { padding: 0 1; background: transparent; transition: background 150ms linear; }
    ConfigOptionList > .option-list--option-hover { background: $primary 10%; }
    ConfigOptionList > .option-list--option-highlighted { background: $primary 20%; }
    ConfigOptionList > .option-list--option-disabled { background: transparent; color: $primary; }
    
    .indicator-column { width: 2; height: 1fr; background: transparent; align: right top; }
    ScrollIndicator { width: 1; height: 1fr; color: $primary; }
    ScrollIndicator:hover { color: $foreground; }
    
    #local-search {
        dock: bottom; border: none; border-top: solid $primary 50%;
        background: $primary 10%; color: $foreground;
        display: none; height: 3;
    }
    #local-search.-active { display: block; }
    
    #footer { height: auto; min-height: 2; dock: bottom; border-top: solid $secondary; padding: 0; background: transparent; }
    #footer-bottom-row { width: 100%; height: 1; margin-top: 0; }
    
    .footer-sep { color: $secondary; }
    
    .footer-shortcut { padding: 0 1; background: transparent; }
    .footer-shortcut:hover { text-style: bold; color: $foreground; background: $primary 25%; }
    .footer-shortcut.-active { text-style: bold; color: $background; background: $primary; }
    #status-bar { padding: 0 1; }
    
    .mode-btn { padding: 0 1; background: transparent; }
    .mode-btn:hover { text-style: bold; color: $foreground; background: $primary 25%; }
    
    #file-link { padding: 0 1; background: transparent; }
    #file-link:hover { text-style: bold; color: $foreground; background: $primary 25%; }
    
    TextInputOverlay, PickerScreen, SearchScreen, DiffScreen, ShortcutsInfoScreen { align: center middle; background: rgba(0, 0, 0, 0.75); }
    
    #picker-dialog { width: 60; height: 70%; background: $background; border: solid $primary; padding: 1 2; }
    #search-dialog { width: 60; height: 80%; background: $background; border: solid $primary; padding: 1 2; }
    #diff-dialog   { width: 70; height: 80%; background: $background; border: solid $primary; padding: 1 2; }
    #shortcuts-dialog { width: 70; height: 80%; background: $background; border: solid $primary; padding: 1 2; }
    #modal-dialog { width: 50; height: auto; background: $background; border: solid $primary; padding: 1 2; }
    
    #picker-list, #search-list, #diff-list, #shortcuts-list { height: 1fr; scrollbar-size: 0 0; background: transparent; border: none; }
    #search-list > .option-list--option { padding: 0 1; background: transparent; transition: background 100ms linear; }
    #search-list > .option-list--option-hover { background: $primary 10%; }
    #search-list > .option-list--option-highlighted { background: $primary 20%; color: $foreground; text-style: bold; }

    #diff-list > .option-list--option { padding: 0 1; background: transparent; }
    #shortcuts-list > .option-list--option { padding: 0 1; background: transparent; }
    
    /* Layout isolation technique - perfectly centers the 1-line button dynamically */
    .modal-btn-container {
        width: 100%; height: auto; align: center middle;
        margin-top: 1; background: transparent;
    }
    
    .modal-close-btn {
        background: $primary; color: $background; text-style: bold;
        padding: 0 2; width: auto; height: 1;
    }
    
    .modal-close-btn:hover { background: $foreground; color: $background; }
    
    #modal-title, #picker-title { color: $primary; margin-bottom: 1; text-style: bold; border-bottom: solid $secondary; }
    #modal-hint { color: $secondary; text-style: italic; content-align: center middle; width: 100%; margin-top: 1; }
    
    Input { border: none; background: transparent; color: $foreground; border-bottom: solid $primary; }
    Input:focus { border: none; border-bottom: solid $primary; }
    """

    BINDINGS = [
        Binding("q,ctrl+c", "quit", "Quit", priority=True),
        Binding("ctrl+f", "search", "Search", priority=True),
        Binding("f1", "show_shortcuts", "Shortcuts", priority=True),
        Binding("ctrl+t", "toggle_save_mode", "Toggle Mode", priority=True),
        Binding("ctrl+s", "save_batch", "Save Batch", priority=True),
        Binding("d", "show_diff", "Diff", priority=True),
        Binding("u", "undo", "Undo", priority=True),
        Binding("ctrl+r", "redo", "Redo", priority=True),
        Binding("?", "toggle_help", "Help", priority=True),
        Binding("/", "focus_local_search", "Search Inline", priority=True),
        Binding("tab", "next_tab", "Next Tab", priority=True),
        Binding("shift+tab", "prev_tab", "Prev Tab", priority=True),
        Binding("escape", "clear_local_search", "Clear Search", priority=True),
        Binding("alt+1", "switch_tab(0)", "Tab 1", show=False),
        Binding("alt+2", "switch_tab(1)", "Tab 2", show=False),
        Binding("alt+3", "switch_tab(2)", "Tab 3", show=False),
        Binding("alt+4", "switch_tab(3)", "Tab 4", show=False),
        Binding("alt+5", "switch_tab(4)", "Tab 5", show=False),
        Binding("alt+6", "switch_tab(5)", "Tab 6", show=False),
        Binding("alt+7", "switch_tab(6)", "Tab 7", show=False),
    ]

    auto_save = reactive(True)

    def __init__(self, engine: BaseEngine, schema: dict[int, list[ConfigItem]], tabs: list[str], title="Dusky Editor", theme_path: str | None = None, default_mode: str = "auto", **kwargs):
        super().__init__(**kwargs)
        self.engine = engine
        self.schema = schema
        self.tabs = tabs
        self.editor_title = title
        self.theme_path = Path(theme_path).expanduser().resolve() if theme_path else None
        
        self.pending_commits: set[tuple[int, int]] = set() 
        self.undo_stack: deque[tuple[int, int, Any, Any]] = deque(maxlen=50) 
        self.redo_stack: deque[tuple[int, int, Any, Any]] = deque(maxlen=50) 
        self._save_timers: dict[tuple[int, int], Timer] = {}
        
        self.theme_colors = {
            "bg": "#111318", "fg": "#e1e2e9", "accent": "#a8c8ff", 
            "error": "#ffb4ab", "warning": "#bdc7dc", "success": "#dbbce1", "muted": "#43474e"
        }
        
        if self.theme_path:
            loaded_theme = load_matugen_json(self.theme_path)
            if loaded_theme:
                self.theme_colors.update(loaded_theme)

        self.last_theme_mtime: float = 0.0
        self._status_timer: Timer | None = None
        
        self._cached_tabs_container: Horizontal | None = None
        self._cached_tab_left: Label | None = None
        self._cached_tab_right: Label | None = None

        self.auto_save = (default_mode.lower() == "auto")

    def compose(self) -> ComposeResult:
        with Vertical(id="main-box"):
            with Horizontal(id="tab-bar"):
                yield Label(" ◀ ", id="tab-left", classes="tab-arrow")
                with Horizontal(id="tabs-container"):
                    yield Tabs(
                        *[Tab(name, id=f"tab-id-{i}") for i, name in enumerate(self.tabs)], 
                        id="tabs"
                    )
                yield Label(" ▶ ", id="tab-right", classes="tab-arrow")
                
            with Horizontal(id="content-area"):
                with ContentSwitcher(initial="tab-0", id="content-switcher"):
                    for i, name in enumerate(self.tabs):
                        with Vertical(id=f"tab-{i}"):
                            with Horizontal(classes="list-wrapper"):
                                yield ConfigOptionList(id=f"list-{i}")
                                with Vertical(classes="indicator-column"):
                                    yield ScrollIndicator("", id=f"indicator-{i}")
                
                with Vertical(id="help-panel"):
                    yield Markdown("Select an item to view documentation.", id="help-markdown")
                    
            yield Input(id="local-search", placeholder="Type to jump... (Enter to close)")
            
        yield AppFooter(id="footer")

    def _build_option(self, item: ConfigItem, is_highlighted: bool = False) -> Text:
        txt = Text()
        exists = getattr(item, "exists_in_target", True)
        is_pending = (str(item.value) != str(item.initial_value))
        
        CURSOR_CHAR = "▶"
        cursor = f"{CURSOR_CHAR} " if is_highlighted else "  "
        txt.append(cursor, style=f"{self.theme_colors['accent']} bold" if is_highlighted else "")
        
        if exists:
            label_style = f"{self.theme_colors['fg']} bold" if is_highlighted else self.theme_colors["fg"]
            txt.append(f"{item.label:<35}", style=label_style)
        else:
            label_style = f"{self.theme_colors['muted']} strike" if not is_highlighted else f"{self.theme_colors['muted']} strike bold"
            raw_label = f"{item.label} [Missing]"
            padding_len = max(0, 35 - len(raw_label))
            txt.append(raw_label, style=label_style)
            txt.append(" " * padding_len)
        
        val_str = str(item.value)
        def_str = str(item.default)
        
        if item.type_ == "action":
            txt.append("   ")
            txt.append("⚡ Execute Action", style=f"bold {self.theme_colors['warning']}" if exists else f"{self.theme_colors['muted']} italic")
        else:
            is_modified = val_str != def_str
            if not self.auto_save and is_pending:
                txt.append("[+] ", style=self.theme_colors["warning"])
            else:
                dot_color = self.theme_colors["error"] if (is_modified and exists) else self.theme_colors["muted"]
                txt.append("●  ", style=dot_color)
            
            accent = self.theme_colors["accent"] if exists else self.theme_colors["muted"]
            fg = self.theme_colors["fg"] if exists else self.theme_colors["muted"]
            
            match item.type_:
                case "bool":
                    if not exists:
                        txt.append(f" {'◉ ON' if item.value else '◯ OFF'} ", style=f"{self.theme_colors['muted']} italic")
                    elif item.value:
                        txt.append(" ◉ ON  ", style=f"bold {self.theme_colors['bg']} on {self.theme_colors['success']}")
                    else:
                        txt.append(" ◯ OFF ", style=f"{self.theme_colors['muted']} on {self.theme_colors['bg']}")
                case "string":
                    if val_str == "":
                        txt.append("[✎] Unset", style=f"italic {self.theme_colors['muted']}")
                    else:
                        txt.append(f"[✎] {val_str}", style=accent)
                case "picker":
                    txt.append(f"[+] {val_str}", style=accent)
                case "color":
                    r, g, b = color_to_rgb(val_str)
                    hex_color = f"#{r:02x}{g:02x}{b:02x}"
                    color_name = get_color_name(r, g, b)
                    txt.append(" ⬤ ", style=hex_color if exists else self.theme_colors["muted"])
                    txt.append(f"{color_name}", style=accent)
                case _:
                    txt.append(val_str, style=fg)
                    
            if is_modified and is_highlighted and exists:
                txt.append("   ↩ Reset", style=f"italic {self.theme_colors['error']}")
                
        return txt

    async def on_mount(self) -> None:
        self.query_one("#main-box").border_title = f" {self.editor_title} "
        self.apply_theme_to_engine()
        self.query_one("#file-link", FileLink).path = self.engine.target_path
        
        self._cached_tabs_container = self.query_one("#tabs-container", Horizontal)
        self._cached_tab_left = self.query_one("#tab-left", Label)
        self._cached_tab_right = self.query_one("#tab-right", Label)
        
        try:
            batch_shortcut = self.query_one("#shortcut-ctrl-s")
            batch_shortcut.display = not self.auto_save
        except Exception: pass
        
        state = self.engine.load_state()
        
        for i in range(len(self.tabs)):
            ol = self.query_one(f"#list-{i}", ConfigOptionList)
            items = self.schema.get(i, [])
            if items:
                options = []
                current_group = None
                first_item_id = None
                
                for idx, item in enumerate(items):
                    cache_key = f"{item.scope}/{item.key}" if item.scope else item.key
                    if cache_key in state:
                        item.exists_in_target = True
                        raw_val = state[cache_key]
                        if item.type_ == "bool":
                            item.value = (raw_val == "true")
                        elif item.type_ in ("int", "float"):
                            try:
                                item.value = float(raw_val) if item.type_ == "float" else int(float(raw_val))
                            except ValueError: pass
                        elif item.type_ in ("string", "picker", "cycle", "color"):
                            item.value = raw_val[1:-1] if raw_val.startswith('"') and raw_val.endswith('"') else raw_val
                        else:
                            item.value = raw_val
                    else:
                        item.exists_in_target = False
                        
                    if not item._initial_loaded:
                        item.initial_value = item.value
                        item._initial_loaded = True
                    
                    if item.group and item.group != current_group:
                        current_group = item.group
                        header_txt = Text(f"── {current_group.upper()} ──", style=f"bold {self.theme_colors['accent']}")
                        options.append(Option(header_txt, id=f"header_{i}_{current_group}", disabled=True))
                        
                    opt_id = f"item_{i}_{idx}"
                    if first_item_id is None: first_item_id = opt_id
                    
                    is_hl = (first_item_id == opt_id)
                    options.append(Option(self._build_option(item, is_highlighted=is_hl), id=opt_id))
                    
                ol.add_options(options)
                ol.last_highlighted_id = first_item_id

        if first_ol := self.current_option_list:
            first_ol.focus()
            self._update_pagination(first_ol)

        if self.theme_path:
            self.set_interval(0.5, self.watch_theme_file)
            
        self.call_after_refresh(self.check_tab_overflow)
        self.call_after_refresh(self._update_scroll_indicators)
        self._update_footer_legend()

    @on(events.Resize)
    def handle_resize(self, event: events.Resize) -> None:
        self.check_tab_overflow()

    def watch_auto_save(self, old: bool, new: bool) -> None:
        if not getattr(self, "is_mounted", False): return
        self._update_footer_legend()
        
        try:
            batch_shortcut = self.query_one("#shortcut-ctrl-s")
            batch_shortcut.display = not new
            self.call_after_refresh(self.query_one("#footer-shortcuts-container", FlowContainer).reflow)
        except Exception: pass
            
        if new and getattr(self, "pending_commits", None):
            self.action_save_batch()

    def _update_footer_legend(self) -> None:
        if not getattr(self, "is_mounted", False): return
        try:
            legend = self.query_one("#footer-legend", ModeButton)
            legend.update_mode()
        except Exception:
            pass 

    @property
    def current_option_list(self) -> ConfigOptionList | None:
        try:
            switcher = self.query_one(ContentSwitcher)
            if switcher.current:
                idx = switcher.current.split("-")[1]
                return self.query_one(f"#list-{idx}", ConfigOptionList)
        except Exception: pass
        return None

    def check_tab_overflow(self) -> None:
        if not self._cached_tabs_container or not self._cached_tab_left or not self._cached_tab_right: return
        try:
            container, left, right = self._cached_tabs_container, self._cached_tab_left, self._cached_tab_right
            has_overflow = container.max_scroll_x > 0
            if has_overflow:
                left.display = container.scroll_x > 0.5
                right.display = container.scroll_x < (container.max_scroll_x - 0.5)
            else:
                left.display = right.display = False
        except Exception: pass

    @on(events.Click, "#tab-left")
    def scroll_tabs_left(self, event: events.Click) -> None:
        event.stop()
        if self._cached_tabs_container: self._cached_tabs_container.scroll_relative(x=-40, animate=True)

    @on(events.Click, "#tab-right")
    def scroll_tabs_right(self, event: events.Click) -> None:
        event.stop()
        if self._cached_tabs_container: self._cached_tabs_container.scroll_relative(x=40, animate=True)

    async def watch_theme_file(self) -> None:
        if not self.theme_path: return
        try:
            stat_info = await asyncio.to_thread(self.theme_path.stat)
            current_mtime = stat_info.st_mtime
            if current_mtime > self.last_theme_mtime:
                new_theme = await asyncio.to_thread(load_matugen_json, self.theme_path)
                if new_theme is not None:
                    self.last_theme_mtime = current_mtime
                    self.theme_colors.update(new_theme) 
                    self.apply_theme_to_engine()
                    self._refresh_all_ui()
                    for shortcut in self.query(Shortcut): shortcut.refresh()
                    self._update_footer_legend()
        except OSError: pass

    def apply_theme_to_engine(self) -> None:
        self._theme_toggle = not getattr(self, "_theme_toggle", False)
        theme_name = "dusky_matugen_A" if self._theme_toggle else "dusky_matugen_B"
        
        custom_theme = Theme(
            name=theme_name, 
            primary=self.theme_colors["accent"], 
            secondary=self.theme_colors["muted"],
            background=self.theme_colors["bg"], 
            surface=self.theme_colors["bg"],
            warning=self.theme_colors["warning"], 
            error=self.theme_colors["error"],
            success=self.theme_colors["success"], 
            variables={"foreground": self.theme_colors["fg"]},
        )
        self.register_theme(custom_theme)
        self.theme = theme_name

    @on(Tabs.TabActivated)
    def handle_tab_activated(self, event: Tabs.TabActivated) -> None:
        try:
            idx = event.tab.id.split("-")[-1]
            self.query_one(ContentSwitcher).current = f"tab-{idx}"
            event.tab.scroll_visible(animate=True, top=False)
            if ol := self.current_option_list:
                ol.focus()
                self._update_pagination(ol)
                self._update_scroll_indicators()
                self.check_tab_overflow()
        except Exception: pass

    def trigger_shortcut_blink(self, key_id: str) -> None:
        try: self.query_one(f"#shortcut-{key_id}", Shortcut).blink()
        except Exception: pass

    def toggle_shortcut_active(self, key_id: str, active: bool) -> None:
        try:
            sc = self.query_one(f"#shortcut-{key_id}", Shortcut)
            if active: sc.add_class("-active")
            else: sc.remove_class("-active")
        except Exception: pass

    def _get_item_from_id(self, opt_id: str) -> tuple[int, int, ConfigItem] | None:
        if not opt_id or not opt_id.startswith("item_"): return None
        try:
            _, t_idx, i_idx = opt_id.split("_")
            tab_idx, item_idx = int(t_idx), int(i_idx)
            return tab_idx, item_idx, self.schema[tab_idx][item_idx]
        except (ValueError, KeyError, IndexError): return None

    @on(OptionList.OptionHighlighted)
    def handle_option_highlight(self, event: OptionList.OptionHighlighted) -> None:
        ol = event.option_list
        if not isinstance(ol, ConfigOptionList) or not event.option_id: return
        
        parsed = self._get_item_from_id(event.option_id)
        if parsed:
            _, _, item = parsed
            try:
                content_area = self.query_one("#content-area")
                if content_area.has_class("-show-help"):
                    md = self.query_one("#help-markdown", Markdown)
                    help_text = item.extended_help or f"**{item.label}**\n\nNo extended documentation available."
                    md.update(help_text)
            except Exception: pass
            
        last_id = ol.last_highlighted_id
        if last_id and last_id != event.option_id:
            old_parsed = self._get_item_from_id(last_id)
            if old_parsed:
                try:
                    old_idx = ol.get_option_index(last_id)
                    ol.replace_option_prompt_at_index(old_idx, self._build_option(old_parsed[2], False))
                except OptionDoesNotExist: pass
                
        if parsed:
            try:
                curr_idx = ol.get_option_index(event.option_id)
                ol.replace_option_prompt_at_index(curr_idx, self._build_option(parsed[2], True))
                ol.last_highlighted_id = event.option_id
            except OptionDoesNotExist: pass
            
        self._update_pagination(ol)

    def _update_pagination(self, ol: ConfigOptionList) -> None:
        idx = ol.highlighted if ol.highlighted is not None else 0
        total = ol.option_count
        self.query_one("#main-box").border_subtitle = f" {idx + 1}/{total} " if total else " 0/0 "

    def _update_scroll_indicators(self) -> None:
        try:
            switcher = self.query_one(ContentSwitcher)
            if not switcher.current: return
            tab_idx = int(switcher.current.split("-")[1])
            ol = self.query_one(f"#list-{tab_idx}", ConfigOptionList)
            indicator = self.query_one(f"#indicator-{tab_idx}", ScrollIndicator)
            if ol.max_scroll_y > 0 and ol.size.height > 2:
                indicator.update_scroll(ol.scroll_y, ol.max_scroll_y, ol.size.height, ol.virtual_size.height)
            else:
                indicator.display = False
        except Exception: pass

    def notify_status(self, msg: str) -> None:
        app_footer = self.query_one(AppFooter)
        app_footer.status_msg = msg
        if self._status_timer: self._status_timer.stop()
        self._status_timer = self.set_timer(3, lambda: setattr(app_footer, 'status_msg', ""))

    def play_reset_sound(self) -> None:
        global _AUDIO_PLAYER_CACHE
        sound_path = "/usr/share/sounds/freedesktop/stereo/dialog-information.oga"
        if Path(sound_path).exists():
            if _AUDIO_PLAYER_CACHE is None:
                _AUDIO_PLAYER_CACHE = shutil.which("pw-play") or shutil.which("paplay") or shutil.which("mpv") or ""
                
            player = _AUDIO_PLAYER_CACHE
            if player:
                cmd = [player, sound_path]
                if player.endswith("mpv"): cmd.extend(["--no-video", "--really-quiet"])
                
                async def _play() -> None:
                    try:
                        proc = await asyncio.create_subprocess_exec(
                            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
                        )
                        await proc.wait()
                    except OSError:
                        pass
                        
                asyncio.create_task(_play())

    def _apply_value(self, tab_idx: int, item_idx: int, item: ConfigItem, new_val: Any, is_undo: bool = False, batch_mode: bool = False) -> bool:
        if not is_undo:
            self.undo_stack.append((tab_idx, item_idx, item.value, new_val))
            self.redo_stack.clear()
            
        item.value = new_val
        item.exists_in_target = True

        if isinstance(new_val, bool): val_str = "true" if new_val else "false"
        elif new_val is None: val_str = "nil"
        else: val_str = str(new_val)

        if self.auto_save and not batch_mode:
            k = (tab_idx, item_idx)
            if k in self._save_timers:
                self._save_timers[k].stop()
            self._save_timers[k] = self.set_timer(
                0.25, lambda: self._do_auto_save(item, val_str)
            )
        else:
            self.pending_commits.add((tab_idx, item_idx))
            if not batch_mode:
                self._update_footer_legend()
            
        self._refresh_single_ui(tab_idx, item_idx, item)
        return True

    def _do_auto_save(self, item: ConfigItem, val_str: str) -> None:
        success, msg, _ = self.engine.write_value(item.key, item.scope, val_str)
        if success:
            item.initial_value = item.value
            self.notify_status(f"Updated {item.label}")
        else:
            self.notify_status(f"Error: {msg}")

    def _refresh_single_ui(self, tab_idx: int, item_idx: int, item: ConfigItem) -> None:
        try:
            ol = self.query_one(f"#list-{tab_idx}", ConfigOptionList)
            opt_id = f"item_{tab_idx}_{item_idx}"
            idx = ol.get_option_index(opt_id)
            is_hl = (ol.last_highlighted_id == opt_id)
            ol.replace_option_prompt_at_index(idx, self._build_option(item, is_hl))
        except Exception: pass

    def _refresh_all_ui(self) -> None:
        for tab_idx, items in self.schema.items():
            for item_idx, item in enumerate(items):
                self._refresh_single_ui(tab_idx, item_idx, item)

    def action_toggle_save_mode(self) -> None:
        self.auto_save = not self.auto_save

    def action_save_batch(self) -> None:
        self.trigger_shortcut_blink("ctrl-s")
        if not self.pending_commits:
            self.notify_status("No pending changes.")
            return
            
        success_count = 0
        for tab_idx, item_idx in list(self.pending_commits):
            item = self.schema[tab_idx][item_idx]
            val = item.value
            if isinstance(val, bool): val_str = "true" if val else "false"
            elif val is None: val_str = "nil"
            else: val_str = str(val)
            
            success, _, _ = self.engine.write_value(item.key, item.scope, val_str)
            if success:
                item.initial_value = val
                self.pending_commits.discard((tab_idx, item_idx))
                success_count += 1
                
        self._refresh_all_ui()
        self._update_footer_legend()
        if success_count > 0:
            self.notify_status(f"Batched {success_count} commits successfully.")
            self.play_reset_sound()

    def action_show_diff(self) -> None:
        if isinstance(self.screen, DiffScreen):
            self.screen.dismiss(None)
            return
        if isinstance(self.screen, ModalScreen): return
        
        self.toggle_shortcut_active("d", True)
        self.push_screen(DiffScreen(), lambda _: self.toggle_shortcut_active("d", False))

    def action_show_shortcuts(self) -> None:
        if isinstance(self.screen, ShortcutsInfoScreen):
            self.screen.dismiss(None)
            return
        if isinstance(self.screen, ModalScreen): return
        
        self.toggle_shortcut_active("f1", True)
        self.push_screen(ShortcutsInfoScreen(), lambda _: self.toggle_shortcut_active("f1", False))

    def action_undo(self) -> None:
        if not self.undo_stack:
            self.notify_status("Nothing to undo.")
            return
            
        tab_idx, item_idx, old_val, new_val = self.undo_stack.pop()
        self.redo_stack.append((tab_idx, item_idx, old_val, new_val))
        item = self.schema[tab_idx][item_idx]
        self._apply_value(tab_idx, item_idx, item, old_val, is_undo=True)
        self.notify_status(f"Undid change to {item.label}")

    def action_redo(self) -> None:
        if not self.redo_stack:
            self.notify_status("Nothing to redo.")
            return
            
        tab_idx, item_idx, old_val, new_val = self.redo_stack.pop()
        self.undo_stack.append((tab_idx, item_idx, old_val, new_val))
        item = self.schema[tab_idx][item_idx]
        self._apply_value(tab_idx, item_idx, item, new_val, is_undo=True)
        self.notify_status(f"Redid change to {item.label}")

    def action_toggle_help(self) -> None:
        content_area = self.query_one("#content-area")
        content_area.toggle_class("-show-help")
        self.toggle_shortcut_active("help", content_area.has_class("-show-help"))
        
        if content_area.has_class("-show-help"):
            ol = self.current_option_list
            if ol and ol.last_highlighted_id:
                parsed = self._get_item_from_id(ol.last_highlighted_id)
                if parsed:
                    md = self.query_one("#help-markdown", Markdown)
                    md.update(parsed[2].extended_help or f"**{parsed[2].label}**\n\nNo extended documentation.")

    def action_focus_local_search(self) -> None:
        inp = self.query_one("#local-search", Input)
        inp.add_class("-active")
        inp.value = ""
        self.toggle_shortcut_active("slash", True)
        self.call_after_refresh(inp.focus)

    def action_clear_local_search(self) -> None:
        inp = self.query_one("#local-search", Input)
        if inp.has_class("-active"):
            inp.remove_class("-active")
            self.toggle_shortcut_active("slash", False)
            if ol := self.current_option_list: 
                self.call_after_refresh(ol.focus)
        elif isinstance(self.screen, ModalScreen):
            self.screen.dismiss(None)

    @on(Input.Changed, "#local-search")
    def handle_local_search(self, event: Input.Changed) -> None:
        query = event.value.lower().replace(" ", "")
        if not query: return
        ol = self.current_option_list
        if not ol: return
        
        try:
            tab_idx = int(ol.id.split("-")[1])
            items = self.schema.get(tab_idx, [])
            for item_idx, item in enumerate(items):
                if query in item.label.lower().replace(" ", ""):
                    opt_id = f"item_{tab_idx}_{item_idx}"
                    try:
                        idx = ol.get_option_index(opt_id)
                        ol.highlighted = idx
                        if hasattr(ol, "scroll_to_highlight"):
                            ol.scroll_to_highlight()
                        break
                    except OptionDoesNotExist: pass
        except Exception: pass

    @on(Input.Submitted, "#local-search")
    def submit_local_search(self, event: Input.Submitted) -> None:
        self.action_clear_local_search()

    def action_search(self) -> None:
        if isinstance(self.screen, SearchScreen):
            self.screen.dismiss(None)
            return
        if isinstance(self.screen, ModalScreen): return
        
        self.toggle_shortcut_active("ctrl-f", True)
        
        def check_reply(result: tuple[int, int] | None) -> None:
            self.toggle_shortcut_active("ctrl-f", False)
            if result is not None:
                tab_idx, item_idx = result
                self.action_switch_tab(tab_idx)
                
                def _focus_and_highlight():
                    try:
                        ol = self.query_one(f"#list-{tab_idx}", ConfigOptionList)
                        ol.focus()
                        idx = ol.get_option_index(f"item_{tab_idx}_{item_idx}")
                        ol.highlighted = idx
                    except Exception: pass
                    
                self.call_after_refresh(_focus_and_highlight)
                
        self.push_screen(SearchScreen(), check_reply)

    def action_next_tab(self) -> None: self.query_one(Tabs).action_next_tab()
    def action_prev_tab(self) -> None: self.query_one(Tabs).action_previous_tab()
    def action_switch_tab(self, index: int) -> None:
        if 0 <= index < len(self.tabs): self.query_one(Tabs).active = f"tab-id-{index}"

    def action_adjust(self, direction: int) -> None:
        ol = self.current_option_list
        if not ol or not ol.last_highlighted_id: return
        
        parsed = self._get_item_from_id(ol.last_highlighted_id)
        if not parsed: return
        tab_idx, item_idx, item = parsed
        
        new_val = item.value
        match item.type_:
            case "bool": new_val = not item.value
            case "int" | "float":
                step = item.step or 1
                new_val = item.value + (direction * step)
                if item.min_val is not None: new_val = max(item.min_val, new_val)
                if item.max_val is not None: new_val = min(item.max_val, new_val)
                new_val = round(new_val, 6) if item.type_ == "float" else int(new_val)
            case "cycle":
                if not item.options: return
                try: idx = item.options.index(item.value)
                except ValueError: idx = 0
                new_val = item.options[(idx + direction) % len(item.options)]
            case "color":
                r, g, b = color_to_rgb(str(item.value))
                current_name = get_color_name(r, g, b)
                try: idx = CYCLE_COLORS.index(current_name)
                except ValueError: idx = 0
                next_name = CYCLE_COLORS[(idx + direction) % len(CYCLE_COLORS)]
                fmt = parse_color_format(str(item.value))
                new_val = format_rgb(next_name, fmt, str(item.value))
            case _: return
            
        if new_val != item.value:
            self._apply_value(tab_idx, item_idx, item, new_val)

    def action_reset_item(self) -> None:
        self.trigger_shortcut_blink("r")
        ol = self.current_option_list
        if not ol or not ol.last_highlighted_id: return
        parsed = self._get_item_from_id(ol.last_highlighted_id)
        if parsed and str(parsed[2].value) != str(parsed[2].default):
            self._apply_value(parsed[0], parsed[1], parsed[2], parsed[2].default)

    def action_reset_all(self) -> None:
        self.trigger_shortcut_blink("R")
        try:
            switcher = self.query_one(ContentSwitcher)
            if not switcher.current: return
            tab_idx = int(switcher.current.split("-")[1])
            items = self.schema.get(tab_idx, [])
            success_count = 0
            
            for item_idx, item in enumerate(items):
                if str(item.value) != str(item.default):
                    if self._apply_value(tab_idx, item_idx, item, item.default, batch_mode=True):
                        success_count += 1
                        
            if success_count > 0:
                if self.auto_save:
                    self.action_save_batch()
                else:
                    self._update_footer_legend()
                self.notify_status(f"Reset {success_count} items in {self.tabs[tab_idx]}")
                self.play_reset_sound()
        except Exception: pass

    def action_submit_current(self) -> None:
        ol = self.current_option_list
        if ol and ol.last_highlighted_id:
            ol._last_click_x = 0
            ol._mouse_down_highlight = None
            self._handle_item_action(ol, ol.last_highlighted_id)

    @on(OptionList.OptionSelected)
    def handle_selection(self, event: OptionList.OptionSelected) -> None:
        ol = event.option_list
        if isinstance(ol, ConfigOptionList):
            if getattr(ol, "_mouse_down_highlight", None) == event.option_index:
                self._handle_item_action(ol, event.option_id)
            ol._mouse_down_highlight = None
            ol._last_click_x = 0

    def _handle_item_action(self, ol: ConfigOptionList, opt_id: str | None) -> None:
        if not opt_id: return
        parsed = self._get_item_from_id(opt_id)
        if not parsed: return
        tab_idx, item_idx, item = parsed
            
        is_modified = str(item.value) != str(item.default)
        
        if is_modified and item.type_ != "action":
            rendered_text = self._build_option(item, True)
            total_width = rendered_text.cell_len
            reset_width = 10 
            threshold = total_width - reset_width
            
            click_x = getattr(ol, "_last_click_x", 0)
            if threshold <= click_x <= total_width + 2:
                self.action_reset_item()
                return
                
        match item.type_:
            case "bool" | "cycle": self.action_adjust(1)
            case "int" | "float" | "string" | "color": self.prompt_string(tab_idx, item_idx, item)
            case "action": self.notify_status(f"Action triggered: {item.label}")
            case "picker": self.prompt_picker(tab_idx, item_idx, item)

    def prompt_string(self, tab_idx: int, item_idx: int, item: ConfigItem) -> None:
        def check_reply(new_val: str | None) -> None:
            if new_val is not None:
                if item.type_ == "int":
                    try: 
                        parsed_val = int(new_val)
                        if item.min_val is not None: parsed_val = max(int(item.min_val), parsed_val)
                        if item.max_val is not None: parsed_val = min(int(item.max_val), parsed_val)
                        new_val = parsed_val
                    except ValueError: 
                        self.notify_status("Error: Value must be an integer.")
                        return
                elif item.type_ == "float":
                    try: 
                        parsed_val = float(new_val)
                        if item.min_val is not None: parsed_val = max(float(item.min_val), parsed_val)
                        if item.max_val is not None: parsed_val = min(float(item.max_val), parsed_val)
                        new_val = parsed_val
                    except ValueError: 
                        self.notify_status("Error: Value must be a float.")
                        return
                        
                self._apply_value(tab_idx, item_idx, item, new_val)
        self.push_screen(TextInputOverlay(f"Enter new {item.label}:", str(item.value)), check_reply)

    def prompt_picker(self, tab_idx: int, item_idx: int, item: ConfigItem) -> None:
        def check_reply(new_val: str | None) -> None:
            if new_val is not None: self._apply_value(tab_idx, item_idx, item, new_val)
        self.push_screen(PickerScreen(item.label, item.options, item.hints), check_reply)
