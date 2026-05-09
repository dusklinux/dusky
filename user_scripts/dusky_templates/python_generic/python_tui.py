#!/usr/bin/env python3
import os
import json
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Literal

from textual import work, on, events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.widgets import (
    Label, Input, ListView, ListItem, 
    Tabs, TabbedContent, TabPane, OptionList
)
from textual.widgets.option_list import Option
from textual.screen import ModalScreen
from textual.reactive import reactive
from textual.theme import Theme
from textual.timer import Timer

from rich.text import Text

# =============================================================================
# HOT-RELOADING NATIVE JSON THEME ENGINE
# =============================================================================

THEME_FILE_PATH = Path("~/.config/matugen/generated/dusky_tui.json").expanduser()

def load_matugen_json(file_path: Path) -> dict[str, str]:
    colors = {
        "bg": "#111318", "fg": "#e1e2e9", "accent": "#a8c8ff", 
        "error": "#ffb4ab", "warning": "#bdc7dc", "success": "#dbbce1", "muted": "#43474e"
    }
    if not file_path.exists():
        return colors
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            colors.update(data)
    except (OSError, json.JSONDecodeError):
        pass
    return colors

THEME = load_matugen_json(THEME_FILE_PATH)

# =============================================================================
# SCHEMA & DATA DEFINITIONS
# =============================================================================

type ConfigType = Literal["bool", "int", "float", "string", "cycle", "action", "menu", "picker"]

@dataclass(kw_only=True)
class ConfigItem:
    label: str
    key: str
    scope: str = "DEFAULT"
    type_: ConfigType
    default: Any
    options: list[str] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    min_val: float | None = None
    max_val: float | None = None
    step: float | None = None
    value: Any = None

    def __post_init__(self) -> None:
        if self.value is None:
            self.value = self.default

TABS = ["General", "Network", "Display", "System", "Audio", "Storage", "Security"]
SCHEMA: dict[int, list[ConfigItem]] = {
    0: [
        ConfigItem(label="Enable Daemon", key="service_enabled", type_="bool", default=True),
        ConfigItem(label="Timeout (ms)", key="timeout", type_="int", default=100, min_val=0, max_val=5000, step=50),
        ConfigItem(label="Log Prefix", key="log_prefix", type_="string", default="myapp_"),
        ConfigItem(label="Scale Factor", key="scale", type_="float", default=1.0, min_val=0.5, max_val=3.0, step=0.1),
    ],
    1: [
        ConfigItem(label="Hostname", key="hostname", type_="string", default="arch-linux"),
        ConfigItem(label="Protocol", key="protocol", type_="cycle", default="tcp", options=["tcp", "udp", "icmp"]),
        ConfigItem(label="Port", key="port", type_="int", default=8080, min_val=1, max_val=65535, step=1),
    ],
    3: [
        ConfigItem(label="Select Theme", key="demo_picker", type_="picker", default="Tokyo Night", 
                   options=["Catppuccin Mocha", "Nord", "Dracula", "Gruvbox", "Tokyo Night"],
                   hints=["Warm & Pastel", "Arctic Cold", "Vampire Dark", "Retro Groove", "Neon Lights"]),
        ConfigItem(label="Restart Daemon", key="demo_sudo", type_="action", default=""),
        ConfigItem(label="Shadow Color", key="color", type_="cycle", default="0xee1a1a1a", options=["0xee1a1a1a", "0xff000000"]),
    ]
}

# Auto-hydrate test data
for i in range(len(TABS)):
    if i not in SCHEMA: SCHEMA[i] = []
    for j in range(len(SCHEMA[i]), 35):
        cat = TABS[i]
        cycle_type = j % 3
        if cycle_type == 0:
            SCHEMA[i].append(ConfigItem(label=f"{cat} Flag {j}", key=f"{cat.lower()}_{j}", type_="bool", default=(j % 2 == 0)))
        elif cycle_type == 1:
            SCHEMA[i].append(ConfigItem(label=f"{cat} Buffer {j}", key=f"{cat.lower()}_{j}", type_="int", default=256 + j, min_val=0, max_val=4096, step=16))
        else:
            SCHEMA[i].append(ConfigItem(label=f"{cat} Path {j}", key=f"{cat.lower()}_{j}", type_="string", default=f"/etc/{cat.lower()}/conf.d"))

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

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def handle_submit(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value)
        
    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.dismiss(None)

class PickerScreen(ModalScreen[str | None]):
    def __init__(self, title: str, options: list[str], hints: list[str]) -> None:
        super().__init__()
        self.picker_title = title
        self.options = options
        self.hints = hints

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog"):
            yield Label(f"PICKER: {self.picker_title}", id="picker-title")
            
            list_items = []
            for i, opt in enumerate(self.options):
                hint = self.hints[i] if i < len(self.hints) else ""
                txt = Text()
                txt.append(opt)
                if hint:
                    txt.append(" - ")
                    txt.append(hint, style=f"italic {THEME['muted']}")
                list_items.append(ListItem(Label(txt)))
                
            yield ListView(*list_items, id="picker-list")

    def on_mount(self) -> None:
        self.query_one(ListView).focus()

    @on(ListView.Selected)
    def on_selected(self, event: ListView.Selected) -> None:
        idx = self.query_one(ListView).index
        if idx is not None:
            self.dismiss(self.options[idx])

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.dismiss(None)

# =============================================================================
# INTERACTIVE COMPONENTS
# =============================================================================

class ConfigOptionList(OptionList):
    """Subclassed OptionList with native scroll tracking and cached index."""
    last_highlighted_idx: int = 0

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if hasattr(self.app, "_update_scroll_indicators"):
            self.app._update_scroll_indicators()
            
    def watch_max_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_max_scroll_y(old_value, new_value)
        if hasattr(self.app, "_update_scroll_indicators"):
            self.app._update_scroll_indicators()

class Shortcut(Label):
    def __init__(self, key_text: str, label: str, action_name: str | None = None) -> None:
        super().__init__(classes="footer-shortcut")
        self.key_text = key_text
        self.label_text = label
        self.action_name = action_name

    def render(self) -> Text:
        txt = Text()
        txt.append(f"[{self.key_text}] ", style=THEME["accent"])
        txt.append(self.label_text, style=THEME["fg"])
        return txt

    def on_click(self) -> None:
        if self.action_name:
            getattr(self.app, f"action_{self.action_name}")()

class FileLink(Label):
    path = "~/.config/myapp/settings.conf"
    
    def render(self) -> Text:
        txt = Text()
        txt.append(" File: ", style=THEME["accent"])
        txt.append(self.path, style=THEME["fg"] + " underline")
        return txt
        
    def on_click(self, event: events.Click) -> None:
        expanded_path = Path(self.path).expanduser().resolve()
        expanded_path.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            expanded_path.touch(exist_ok=True)
            if event.button == 1:
                subprocess.Popen(
                    ["gnome-text-editor", str(expanded_path)], 
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            elif event.button == 3:
                with self.app.suspend():
                    subprocess.run(["nvim", str(expanded_path)])
        except (FileNotFoundError, OSError):
            if hasattr(self.app, "notify_status"):
                getattr(self.app, "notify_status")("Error resolving path or launching editor.")

class AppFooter(Vertical):
    status_msg = reactive("")

    def compose(self) -> ComposeResult:
        with Horizontal(id="footer-controls"):
            yield Shortcut("Tab", "Category", "next_tab")
            yield Shortcut("r", "Reset Item", "reset_item")
            yield Shortcut("R", "Reset All", "reset_all")
            yield Shortcut("←/→ h/l", "Adjust")
        with Horizontal(id="footer-secondary"):
            yield Shortcut("Enter", "Action", "submit_current")
            yield Shortcut("q", "Quit", "quit")
            yield Label(f"   [{THEME['muted']}]●[/] Default  [{THEME['error']}]●[/] Modified", id="footer-legend")
        
        with Horizontal(id="footer-bottom-row"):
            yield Label("", id="status-bar")
            yield FileLink(id="file-link")

    def watch_status_msg(self, new_val: str) -> None:
        for bar in self.query("#status-bar"):
            for link in self.query("#file-link"):
                if new_val:
                    txt = Text()
                    txt.append(" Status: ", style=THEME["accent"])
                    txt.append(new_val, style=THEME["error"])
                    bar.update(txt)
                    bar.display = True
                    link.display = False
                else:
                    bar.display = False
                    link.display = True

# =============================================================================
# MAIN APPLICATION
# =============================================================================

class DuskyApp(App):
    CSS = """
    Screen { background: $background; }
    
    #main-box {
        width: 100%; height: 100%;
        border: solid $secondary;
        border-title-color: $primary;
        border-title-style: bold;
        border-title-align: center;
        border-subtitle-color: $primary;
        border-subtitle-style: bold;
        border-subtitle-align: right;
        background: transparent;
        padding: 0 1;
    }
    
    TabbedContent { height: 1fr; margin-bottom: 1; background: transparent; }
    ContentSwitcher { height: 1fr; background: transparent; }
    
    Tabs { height: 1; margin-bottom: 1; background: transparent; }
    Tabs > .underline { display: none; } 
    Tab { height: 1; padding: 0 1; color: $secondary; background: transparent; border: none; }
    Tab:hover { color: $text; background: $primary 25%; }
    Tab.-active { color: $background; background: $primary; text-style: bold; border: none; }
    
    .list-wrapper { height: 1fr; }
    ConfigOptionList { width: 1fr; height: 1fr; scrollbar-size: 0 0; background: transparent; border: none; }
    ConfigOptionList > .option-list--option { padding: 0 1; background: transparent; }
    ConfigOptionList > .option-list--option-hover { background: $primary 10%; }
    ConfigOptionList > .option-list--option-highlighted { background: $primary 20%; }
    
    .indicator-column { width: 2; height: 1fr; background: transparent; }
    .scroll-up-indicator { dock: top; height: 1; color: $primary; text-style: bold; display: none; }
    .scroll-down-indicator { dock: bottom; height: 1; color: $primary; text-style: bold; display: none; }
    
    #footer { height: 4; dock: bottom; border-top: solid $secondary; padding-top: 0; background: transparent; }
    #footer-controls { width: 100%; }
    
    .footer-shortcut { margin-right: 2; padding: 0 1; background: transparent; }
    .footer-shortcut:hover { text-style: bold; color: $text; background: $primary 25%; }
    #footer-legend { color: $text; }
    
    #footer-bottom-row { margin-top: 1; }
    #file-link { padding: 0 1; background: transparent; }
    #file-link:hover { text-style: bold; color: $text; background: $primary 25%; }
    
    TextInputOverlay, PickerScreen { align: center middle; background: rgba(0, 0, 0, 0.75); }
    #modal-dialog { width: 50; height: 7; background: $background; border: solid $primary; padding: 1 2; }
    #modal-title { color: $primary; margin-bottom: 1; }
    Input { border: none; background: transparent; color: $text; border-bottom: solid $primary; }
    Input:focus { border: none; border-bottom: solid $primary; }
    
    #picker-dialog { width: 60; height: 15; background: $background; border: solid $primary; padding: 1 2; }
    #picker-title { color: $primary; margin-bottom: 1; text-style: bold; border-bottom: solid $secondary; }
    #picker-list { height: 1fr; scrollbar-size: 0 0; background: transparent; border: none; }
    #picker-list > ListItem { height: 1; background: transparent; }
    #picker-list > ListItem.-highlight { background: $primary 20%; }
    #picker-footer { color: $primary; margin-top: 1; }
    """

    BINDINGS = [
        Binding("q,ctrl+c", "quit", "Quit", priority=True),
        Binding("tab", "next_tab", "Next Tab", priority=True),
        Binding("shift+tab", "prev_tab", "Prev Tab", priority=True),
        Binding("j,down", "cursor_down", "Down", priority=True),
        Binding("k,up", "cursor_up", "Up", priority=True),
        Binding("g", "scroll_top", "Top", priority=True),
        Binding("G", "scroll_bottom", "Bottom", priority=True),
        Binding("h,left,backspace", "adjust(-1)", "Adjust Down", priority=True),
        Binding("l,right", "adjust(1)", "Adjust Up", priority=True),
        Binding("r", "reset_item", "Reset", priority=True),
        Binding("R", "reset_all", "Reset All", priority=True),
        Binding("ctrl+d,page_down", "page_down", "Page Down", priority=True),
        Binding("ctrl+u,page_up", "page_up", "Page Up", priority=True),
    ]

    last_theme_mtime: float = 0.0
    _status_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="main-box"):
            with TabbedContent(id="tabs"):
                for i, name in enumerate(TABS):
                    with TabPane(name, id=f"tab-{i}"):
                        with Horizontal(classes="list-wrapper"):
                            yield ConfigOptionList(id=f"list-{i}")
                            with Vertical(classes="indicator-column"):
                                yield Label("▲", classes="scroll-up-indicator", id=f"up-{i}")
                                yield Label("▼", classes="scroll-down-indicator", id=f"down-{i}")
            yield AppFooter(id="footer")

    def _build_option(self, item: ConfigItem, is_highlighted: bool = False) -> Text:
        """Constructs Rich text cleanly, mitigating arbitrary string injection bugs."""
        txt = Text()
        
        # Standardized purely geometric cursor to prevent bold font-fallback shifting
        CURSOR_CHAR = "▶"
        cursor = f"{CURSOR_CHAR} " if is_highlighted else "  "
        txt.append(cursor, style=f"{THEME['accent']} bold" if is_highlighted else "")
        
        label_style = f"{THEME['fg']} bold" if is_highlighted else THEME["fg"]
        txt.append(f"{item.label:<35}", style=label_style)
        
        val_str = str(item.value)
        def_str = str(item.default)
        dot_color = THEME["error"] if val_str != def_str else THEME["muted"]
        
        txt.append("●", style=dot_color)
        txt.append(" : ")
        
        match item.type_:
            case "bool":
                txt.append("ON" if item.value else "OFF", style=THEME["accent"] if item.value else THEME["muted"])
            case "string":
                if val_str == "":
                    txt.append("[✎] Unset", style=f"italic {THEME['muted']}")
                else:
                    txt.append(f"[✎] {val_str}", style=THEME["accent"])
            case "action":
                txt.append("▶ press Enter", style=THEME["accent"])
            case "picker":
                txt.append(f"[+] {val_str}", style=THEME["accent"])
            case _:
                txt.append(val_str, style=THEME["fg"])
                
        return txt

    async def on_mount(self) -> None:
        self.query_one("#main-box").border_title = " Generic System Config Editor v7.0.4 "
        self.apply_theme_to_engine()
        
        for i in range(len(TABS)):
            ol = self.query_one(f"#list-{i}", ConfigOptionList)
            items = SCHEMA.get(i, [])
            if items:
                options = [Option(self._build_option(item, is_highlighted=(idx == 0)), id=f"item_{i}_{idx}") for idx, item in enumerate(items)]
                ol.add_options(options)
                ol.last_highlighted_idx = 0

        if first_ol := self.current_option_list:
            first_ol.focus()
            self._update_pagination(first_ol)

        self.set_interval(0.5, self.watch_theme_file)
        self.call_after_refresh(self._update_scroll_indicators)

    @property
    def current_option_list(self) -> ConfigOptionList | None:
        try:
            tc = self.query_one(TabbedContent)
            if tc.active:
                idx = tc.active.split("-")[1]
                return self.query_one(f"#list-{idx}", ConfigOptionList)
        except Exception:
            pass
        return None

    def watch_theme_file(self) -> None:
        try:
            current_mtime = THEME_FILE_PATH.stat().st_mtime
            if current_mtime > self.last_theme_mtime:
                self.last_theme_mtime = current_mtime
                new_theme = load_matugen_json(THEME_FILE_PATH)
                THEME.update(new_theme) 
                
                self.apply_theme_to_engine()
                
                for i in range(len(TABS)):
                    try:
                        ol = self.query_one(f"#list-{i}", ConfigOptionList)
                        items = SCHEMA.get(i, [])
                        last_idx = ol.last_highlighted_idx
                        
                        for idx, item in enumerate(items):
                            is_hl = (idx == last_idx) and (self.current_option_list == ol)
                            ol.replace_option_prompt_at_index(idx, self._build_option(item, is_hl))
                    except Exception:
                        continue
                        
                for shortcut in self.query(Shortcut):
                    shortcut.refresh()
                    
                for footer in self.query(AppFooter):
                    for legend in footer.query("#footer-legend"):
                        legend.update(f"   [{THEME['muted']}]●[/] Default  [{THEME['error']}]●[/] Modified")
                for link in self.query(FileLink):
                    link.refresh()
        except OSError:
            pass

    def apply_theme_to_engine(self) -> None:
        self._theme_revision = getattr(self, "_theme_revision", 0) + 1
        theme_name = f"dusky_matugen_rev{self._theme_revision}"

        custom_theme = Theme(
            name=theme_name,
            primary=THEME["accent"],
            secondary=THEME["muted"],
            background=THEME["bg"],
            surface=THEME["bg"],
            warning=THEME["warning"],
            error=THEME["error"],
            success=THEME["success"],
            foreground=THEME["fg"],
        )
        
        self.register_theme(custom_theme)
        self.theme = theme_name

    @on(TabbedContent.TabActivated)
    def handle_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if ol := self.current_option_list:
            ol.focus()
            self._update_pagination(ol)
            self._update_scroll_indicators()

    @on(OptionList.OptionHighlighted)
    def handle_option_highlight(self, event: OptionList.OptionHighlighted) -> None:
        ol = event.option_list
        if not isinstance(ol, ConfigOptionList):
            return
            
        try:
            tab_idx = int(ol.id.split("-")[1])
        except (AttributeError, IndexError, ValueError):
            return
            
        last_idx = ol.last_highlighted_idx
        
        if last_idx is not None and last_idx != event.option_index:
            try:
                item = SCHEMA[tab_idx][last_idx]
                ol.replace_option_prompt_at_index(last_idx, self._build_option(item, False))
            except (IndexError, KeyError):
                pass
                
        if event.option_index is not None:
            try:
                item = SCHEMA[tab_idx][event.option_index]
                ol.replace_option_prompt_at_index(event.option_index, self._build_option(item, True))
                ol.last_highlighted_idx = event.option_index
            except (IndexError, KeyError):
                pass
            
        self._update_pagination(ol)

    def _update_pagination(self, ol: ConfigOptionList) -> None:
        idx = ol.highlighted if ol.highlighted is not None else 0
        total = ol.option_count
        main_box = self.query_one("#main-box")
        main_box.border_subtitle = f" {idx + 1}/{total} " if total else " 0/0 "

    def _update_scroll_indicators(self) -> None:
        tc = self.query_one(TabbedContent)
        if not tc.active: return
        
        try:
            tab_idx = int(tc.active.split("-")[1])
            ol = self.query_one(f"#list-{tab_idx}", ConfigOptionList)
            up_ind = self.query_one(f"#up-{tab_idx}", Label)
            down_ind = self.query_one(f"#down-{tab_idx}", Label)
            
            up_ind.display = ol.scroll_y > 0
            down_ind.display = ol.scroll_y < ol.max_scroll_y
        except Exception:
            pass

    def notify_status(self, msg: str) -> None:
        app_footer = self.query_one(AppFooter)
        app_footer.status_msg = msg
        
        if self._status_timer is not None:
            self._status_timer.stop()
            
        self._status_timer = self.set_timer(3, lambda: setattr(app_footer, 'status_msg', ""))

    def action_next_tab(self) -> None: 
        self.query_one(Tabs).action_next_tab()
        
    def action_prev_tab(self) -> None: 
        self.query_one(Tabs).action_previous_tab()
        
    def action_cursor_down(self) -> None: 
        if ol := self.current_option_list: ol.action_cursor_down()
            
    def action_cursor_up(self) -> None: 
        if ol := self.current_option_list: ol.action_cursor_up()
            
    def action_scroll_top(self) -> None: 
        if ol := self.current_option_list:
            ol.highlighted = 0
    
    def action_scroll_bottom(self) -> None:
        if ol := self.current_option_list:
            if ol.option_count > 0:
                ol.highlighted = ol.option_count - 1
            
    def action_page_down(self) -> None:
        if ol := self.current_option_list:
            if ol.option_count == 0: return
            idx = ol.highlighted if ol.highlighted is not None else 0
            ol.highlighted = min(ol.option_count - 1, idx + 10)
        
    def action_page_up(self) -> None:
        if ol := self.current_option_list:
            if ol.option_count == 0: return
            idx = ol.highlighted if ol.highlighted is not None else 0
            ol.highlighted = max(0, idx - 10)

    def action_adjust(self, direction: int) -> None:
        ol = self.current_option_list
        if not ol or ol.highlighted is None: return
        
        tc = self.query_one(TabbedContent)
        tab_idx = int(tc.active.split("-")[1])
        item_idx = ol.highlighted
        item = SCHEMA.get(tab_idx, [])[item_idx]
        
        match item.type_:
            case "bool":
                item.value = not item.value
            case "int" | "float":
                step = item.step or 1
                new_val = item.value + (direction * step)
                if item.min_val is not None: new_val = max(item.min_val, new_val)
                if item.max_val is not None: new_val = min(item.max_val, new_val)
                item.value = round(new_val, 6) if item.type_ == "float" else int(new_val)
            case "cycle":
                if not item.options: return
                try: idx = item.options.index(item.value)
                except ValueError: idx = 0
                item.value = item.options[(idx + direction) % len(item.options)]
            case _: return
            
        ol.replace_option_prompt_at_index(item_idx, self._build_option(item, True))
        self.notify_status(f"Updated {item.label}")

    def action_reset_item(self) -> None:
        ol = self.current_option_list
        if ol and ol.highlighted is not None:
            tc = self.query_one(TabbedContent)
            tab_idx = int(tc.active.split("-")[1])
            item_idx = ol.highlighted
            item = SCHEMA[tab_idx][item_idx]
            
            item.value = item.default
            ol.replace_option_prompt_at_index(item_idx, self._build_option(item, True))
            self.notify_status(f"Reset {item.label}")

    def action_reset_all(self) -> None:
        tc = self.query_one(TabbedContent)
        if not tc.active: return
        
        tab_idx = int(tc.active.split("-")[1])
        items = SCHEMA.get(tab_idx, [])
        for item in items:
            item.value = item.default
            
        if ol := self.current_option_list:
            for idx, item in enumerate(items):
                is_hl = (idx == ol.highlighted)
                ol.replace_option_prompt_at_index(idx, self._build_option(item, is_hl))
                
        self.notify_status(f"Reset all items in {TABS[tab_idx]}")

    def action_submit_current(self) -> None:
        ol = self.current_option_list
        if ol and ol.highlighted is not None:
            self._handle_item_action(ol, ol.highlighted)

    @on(OptionList.OptionSelected)
    def handle_selection(self, event: OptionList.OptionSelected) -> None:
        self._handle_item_action(event.option_list, event.option_index)

    def _handle_item_action(self, ol: ConfigOptionList, index: int) -> None:
        try:
            tab_idx = int(ol.id.split("-")[1])
            item = SCHEMA[tab_idx][index]
        except (AttributeError, IndexError, ValueError, KeyError):
            return
            
        match item.type_:
            case "bool" | "cycle": 
                self.action_adjust(1)
            case "int" | "float" | "string": 
                self.prompt_string(ol, tab_idx, index, item)
            case "action":
                if item.key == "demo_sudo":
                    self.notify_status("Acquiring Sudo... Simulated daemon restart.")
            case "picker": 
                self.prompt_picker(ol, tab_idx, index, item)

    @work
    async def prompt_string(self, ol: ConfigOptionList, tab_idx: int, item_idx: int, item: ConfigItem) -> None:
        new_val = await self.push_screen(TextInputOverlay(f"Enter new {item.label}:", str(item.value)))
        if new_val is not None:
            if item.type_ == "int":
                try: 
                    new_val = int(new_val)
                except ValueError: 
                    self.notify_status("Error: Value must be an integer.")
                    return
            elif item.type_ == "float":
                try: 
                    new_val = float(new_val)
                except ValueError: 
                    self.notify_status("Error: Value must be a float.")
                    return
                    
            item.value = new_val
            is_hl = (item_idx == ol.highlighted)
            ol.replace_option_prompt_at_index(item_idx, self._build_option(item, is_hl))
            self.notify_status(f"Written: {item.label} = {new_val}")

    @work
    async def prompt_picker(self, ol: ConfigOptionList, tab_idx: int, item_idx: int, item: ConfigItem) -> None:
        new_val = await self.push_screen(PickerScreen(item.label, item.options, item.hints))
        if new_val is not None:
            item.value = new_val
            is_hl = (item_idx == ol.highlighted)
            ol.replace_option_prompt_at_index(item_idx, self._build_option(item, is_hl))
            self.notify_status(f"Selected: {new_val}")

if __name__ == "__main__":
    app = DuskyApp()
    app.run()
