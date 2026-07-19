#!/usr/bin/env python3
# dusky_interactive=true
# ==============================================================================
# DUSKY PACKAGE INSTALLER (v11.1 - Master-Suite Edition)
# ==============================================================================
# Architecture: Asynchronous Buffered PTY Streams | Textual Split-Screen TUI
# Added Subsystems: Fuzzy Package Finder | Freedesktop Audio Cues | Footer Telemetry
# Compatibility: Python 3.14+ | Pacman v7.1.0+ | Paru / Yay | ISO & Chroot Root
# ==============================================================================

import asyncio
import argparse
import codecs
import json
import fcntl
import os
import pty
import pwd
import re
import shutil
import signal
import struct
import sys
import termios
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Optional, Any

from rich.console import Console
from rich.text import Text
from textual import work, on, events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Static, RichLog, ProgressBar, Button, Label, Tree, Input, OptionList
)
from textual.widgets.option_list import Option
from textual.widgets.tree import TreeNode

# ==============================================================================
# TYPE DEFINITIONS & HIGH-PERFORMANCE COMPILED REGEXES (PEP 695 Syntax)
# ==============================================================================
type PackageList = list[str]
type ProfileMap = dict[str, PackageList]

SCRIPT_DIR: Path = Path(__file__).resolve().parent
PROFILES_DIR: Path = SCRIPT_DIR / "package_profiles"
AUR_PROFILES_DIR: Path = PROFILES_DIR / "aur"
PACMAN_DB_LOCK: Path = Path("/var/lib/pacman/db.lck")
TEMP_SUDOERS_FILE: Path = Path("/etc/sudoers.d/99_dusky_temp_aur")
DUSKY_THEME_JSON: Path = Path.home() / ".config/matugen/generated/dusky_tui.json"

# Pre-compiled regex to strip all ANSI escape sequences and terminal control codes
ANSI_STRIP_REGEX = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0?]*[ -/]*[@-~])')

class PackageStatus(Enum):
    PENDING = auto()
    INSTALLED = auto()
    INSTALLING = auto()
    FAILED = auto()
    SKIPPED = auto()

@dataclass
class PackageItem:
    name: str
    is_aur: bool
    profile: str
    status: PackageStatus = PackageStatus.PENDING
    error_msg: Optional[str] = None

@dataclass
class InstallationManifest:
    official_packages: list[PackageItem] = field(default_factory=list)
    aur_packages: list[PackageItem] = field(default_factory=list)
    total_requested: int = 0
    already_installed: int = 0

# ==============================================================================
# FREEDESKTOP ASYNCHRONOUS AUDIO NOTIFIER (Borrowed Subsystem)
# ==============================================================================
class AudioNotifier:
    """Non-blocking audio engine utilizing native system players for completion/fault alerts."""
    _player_cache: Optional[str] = None

    @classmethod
    def _get_player(cls) -> Optional[str]:
        if cls._player_cache is None:
            cls._player_cache = shutil.which("pw-play") or shutil.which("paplay") or shutil.which("mpv") or ""
        return cls._player_cache if cls._player_cache else None

    @classmethod
    def play(cls, sound_type: str = "alert") -> None:
        player = cls._get_player()
        if not player:
            return

        sound_map = {
            "alert": "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga",
            "info": "/usr/share/sounds/freedesktop/stereo/dialog-information.oga",
            "complete": "/usr/share/sounds/freedesktop/stereo/complete.oga"
        }
        target = sound_map.get(sound_type, sound_map["alert"])
        
        if not Path(target).exists():
            fallback = "/usr/share/sounds/freedesktop/stereo/bell.oga"
            target = fallback if Path(fallback).exists() else target

        if Path(target).exists():
            cmd = [player, target]
            if player.endswith("mpv"):
                cmd.extend(["--no-video", "--really-quiet"])
            try:
                subprocess_env = os.environ.copy()
                subprocess.Popen(
                    cmd,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=subprocess_env
                )
            except OSError:
                pass

# ==============================================================================
# CORE ENVIRONMENT & DUAL-CONTEXT PRIVILEGE RESOLUTION
# ==============================================================================
class PreflightError(Exception):
    """Raised when strict Arch Linux runtime conditions are unmet."""

@dataclass
class RuntimeContext:
    is_root: bool
    aur_helper: Optional[str] = None
    aur_user: Optional[str] = None

def verify_runtime_environment(has_aur_targets: bool) -> RuntimeContext:
    """Detects execution environment (Chroot Root vs User Desktop) and validates tools."""
    if not Path("/etc/arch-release").exists():
        raise PreflightError("CRITICAL: This installer is strictly for Arch Linux systems.")

    for cmd in ("pacman", "sudo"):
        if not shutil.which(cmd):
            raise PreflightError(f"CRITICAL: Required system binary not found: {cmd}.")

    is_root = os.geteuid() == 0
    aur_helper = None
    aur_user = None

    if has_aur_targets:
        for helper in ("paru", "yay"):
            if shutil.which(helper):
                aur_helper = helper
                break
                
        if not aur_helper:
            raise PreflightError("CRITICAL: AUR packages requested but no helper (paru/yay) found.")

        if is_root:
            target_user = os.environ.get("TARGET_USER", "").strip()
            if target_user:
                try:
                    pwd.getpwnam(target_user)
                    aur_user = target_user
                except KeyError:
                    pass
            
            if not aur_user:
                for p in pwd.getpwall():
                    if (
                        p.pw_uid >= 1000 
                        and p.pw_name != "nobody" 
                        and not p.pw_name.startswith("systemd-")
                        and "/bin" in p.pw_shell
                    ):
                        aur_user = p.pw_name
                        break

    return RuntimeContext(is_root=is_root, aur_helper=aur_helper, aur_user=aur_user)

# ==============================================================================
# THEME PARSING & GENERATION ENGINE
# ==============================================================================
def load_dusky_theme() -> str:
    """Parses generated matugen colors, generating an elegant neon slider TCSS layout."""
    fallback_css = """
    Screen { background: $surface; layout: vertical; }
    #top_header { height: 1; dock: top; content-align: center middle; background: $primary-darken-2; color: $text; text-style: bold; }
    #top_header .title { width: 100%; text-align: center; }
    #main_dashboard { layout: horizontal; height: 1fr; }
    #left_pane { width: 28%; border-right: vkey $primary-darken-1; background: $surface-darken-1; padding: 0 1; height: 100%; }
    #right_pane { width: 72%; height: 100%; layout: vertical; background: $surface; }
    #telemetry_box { height: 5; border-bottom: hkey $primary-darken-1; padding: 0 1; layout: vertical; align: left middle; }
    #status_label { text-style: bold; color: $accent; }
    #speed_label { color: $text-muted; text-style: italic; }
    #progress_bar { width: 100%; margin-top: 1; }
    RichLog { height: 1fr; border: none; scrollbar-size: 1 1; }
    Tree { background: $surface-darken-1; border: none; padding: 0; }
    
    /* Footer Telemetry Styling */
    #footer { height: 1; dock: bottom; background: $primary-darken-2; layout: horizontal; padding: 0 1; }
    .footer-shortcut { padding: 0 1; color: $text; }
    .footer-shortcut.-active { background: $accent; color: $surface; text-style: bold; }
    .footer_sep { color: $text-muted; }
    #footer_status { color: $success; text-style: italic; }
    
    /* Search & Conflict Modals */
    PackageSearchScreen, ConflictModalScreen { align: center middle; background: rgba(0, 0, 0, 0.85); }
    #search_dialog { width: 60; height: 75%; background: $surface; border: solid $accent; padding: 1 2; }
    #search_list { height: 1fr; border: none; }
    #search_list > .option-list--option-highlighted { background: $accent 25%; text-style: bold; }
    """
    if not DUSKY_THEME_JSON.exists():
        return fallback_css
    try:
        data = json.loads(DUSKY_THEME_JSON.read_text(encoding="utf-8"))
        bg = data.get("bg", "#0a1612")
        fg = data.get("fg", "#d8e6df")
        accent = data.get("accent", "#00e0b8")
        error = data.get("error", "#ffb4ab")
        warning = data.get("warning", "#a0d0cb")
        success = data.get("success", "#8dd2da")
        muted = data.get("muted", "#3d4945")
        
        custom_css = f"""
        Screen {{ background: {bg}; layout: vertical; color: {fg}; }}
        #top_header {{ height: 1; dock: top; content-align: center middle; background: {muted}; color: {accent}; text-style: bold; }}
        #top_header .title {{ width: 100%; text-align: center; }}
        #main_dashboard {{ layout: horizontal; height: 1fr; }}
        #left_pane {{ width: 28%; border-right: vkey {muted}; background: {bg}; padding: 0 1; height: 100%; }}
        #right_pane {{ width: 72%; height: 100%; layout: vertical; background: {bg}; }}
        #telemetry_box {{ height: 5; border-bottom: hkey {muted}; padding: 0 2; layout: vertical; align: left middle; }}
        #status_label {{ text-style: bold; color: {accent}; }}
        #speed_label {{ color: {warning}; text-style: italic; }}
        
        /* Corrected DOM Selectors for Textual Progress Bar */
        #progress_bar {{ width: 100%; margin-top: 1; height: 1; }}
        #progress_bar .bar--bar {{ color: {accent}; }}
        #progress_bar .bar--complete {{ color: {success}; }}
        #progress_bar .bar--track {{ background: {muted}; }}
        
        RichLog {{ height: 1fr; border: none; scrollbar-size: 1 1; background: {bg}; color: {fg}; }}
        Tree {{ background: {bg}; border: none; padding: 0; color: {fg}; }}
        Tree > TreeNode {{ color: {fg}; }}
        
        /* Footer Telemetry Styling */
        #footer {{ height: 1; dock: bottom; background: {muted}; layout: horizontal; padding: 0 1; }}
        .footer-shortcut {{ padding: 0 1; color: {fg}; }}
        .footer-shortcut.-active {{ background: {accent}; color: {bg}; text-style: bold; }}
        .footer_sep {{ color: {warning}; }}
        #footer_status {{ color: {success}; text-style: italic; }}
        
        /* Search & Conflict Modals */
        PackageSearchScreen, ConflictModalScreen {{ align: center middle; background: rgba(0, 0, 0, 0.85); }}
        #search_dialog {{ width: 60; height: 75%; background: {bg}; border: solid {accent}; padding: 1 2; }}
        #search_list {{ height: 1fr; border: none; background: {bg}; color: {fg}; }}
        #search_list > .option-list--option-highlighted {{ background: {accent} 25%; color: {fg}; text-style: bold; }}
        Input {{ border: none; border-bottom: solid {accent}; background: transparent; color: {fg}; }}
        """
        return custom_css
    except Exception:
        return fallback_css

# ==============================================================================
# PROFILE & MANIFEST RESOLUTION ENGINE
# ==============================================================================
class ProfileParser:
    """Scans, parses, and deduplicates package profiles from directory manifests."""
    
    @staticmethod
    def ensure_default_profiles() -> None:
        """Creates profile directories and empty skeleton files without populating dummy packages."""
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        AUR_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        
        sample_official = PROFILES_DIR / "01_all"
        if not sample_official.exists():
            sample_official.write_text(
                "# Add official Arch repository packages here (one per line or space-separated)\n",
                encoding="utf-8"
            )
            
        sample_aur = AUR_PROFILES_DIR / "01_all"
        if not sample_aur.exists():
            sample_aur.write_text(
                "# Add AUR packages here (one per line or space-separated)\n",
                encoding="utf-8"
            )

    @classmethod
    def _read_manifest_file(cls, file_path: Path) -> PackageList:
        if not file_path.exists() or not file_path.is_file():
            return []
        packages: PackageList = []
        for line in file_path.read_text(encoding="utf-8").splitlines():
            clean_line = line.split("#")[0].strip()
            if clean_line:
                packages.extend(clean_line.split())
        return packages

    @classmethod
    def resolve_manifests(cls, selected_profiles: list[str]) -> InstallationManifest:
        cls.ensure_default_profiles()
        target_names = selected_profiles if selected_profiles else ["01_all", "01_"]
        
        official_files = list(PROFILES_DIR.glob("*"))
        aur_files = list(AUR_PROFILES_DIR.glob("*"))
        
        manifest = InstallationManifest()
        seen_official: set[str] = set()
        seen_aur: set[str] = set()

        for p_file in sorted(official_files):
            if p_file.is_dir(): continue
            if any(p_file.name == t or p_file.stem == t or p_file.name.startswith(t) for t in target_names):
                for pkg_name in cls._read_manifest_file(p_file):
                    if pkg_name not in seen_official:
                        seen_official.add(pkg_name)
                        manifest.official_packages.append(
                            PackageItem(name=pkg_name, is_aur=False, profile=p_file.name)
                        )

        for p_file in sorted(aur_files):
            if p_file.is_dir(): continue
            if any(p_file.name == t or p_file.stem == t or p_file.name.startswith(t) for t in target_names):
                for pkg_name in cls._read_manifest_file(p_file):
                    if pkg_name not in seen_aur:
                        seen_aur.add(pkg_name)
                        manifest.aur_packages.append(
                            PackageItem(name=pkg_name, is_aur=True, profile=f"aur/{p_file.name}")
                        )

        manifest.total_requested = len(manifest.official_packages) + len(manifest.aur_packages)
        return manifest

# ==============================================================================
# ASYNCHRONOUS PACMAN & ALPM INTERACTION ENGINE
# ==============================================================================
class AsyncPackageManager:
    """Manages non-blocking ALPM database checks and PTY sub-process execution."""
    
    @staticmethod
    async def filter_installed_packages(manifest: InstallationManifest) -> None:
        """Queries local ALPM database via pacman -T in batch asynchronously."""
        all_items = manifest.official_packages + manifest.aur_packages
        if not all_items:
            return

        all_names = [item.name for item in all_items]
        proc = await asyncio.create_subprocess_exec(
            "pacman", "-T", *all_names,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
        stdout, _ = await proc.communicate()
        uninstalled_names = set(stdout.decode("utf-8", errors="replace").splitlines())
        
        for item in all_items:
            if item.name not in uninstalled_names:
                if item.status != PackageStatus.INSTALLED:
                    item.status = PackageStatus.INSTALLED
                    manifest.already_installed += 1
            else:
                if item.status == PackageStatus.INSTALLED:
                    manifest.already_installed = max(0, manifest.already_installed - 1)
                item.status = PackageStatus.PENDING

    @staticmethod
    async def maintain_sudo_heartbeat() -> None:
        """Keeps sudo timestamp alive in the background without leaking sub-processes."""
        try:
            while True:
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "-n", "-v",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                await proc.wait()
                await asyncio.sleep(45)
        except asyncio.CancelledError:
            pass

# ==============================================================================
# INTERACTIVE MODALS & SUBSEQUENCE FUZZY FINDER (Borrowed Subsystems)
# ==============================================================================
class PackageSearchScreen(ModalScreen[Optional[str]]):
    """Subsequence fuzzy finder modal adapted directly from DuskyTUI SearchScreen."""
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel"),
        Binding("down,j", "cursor_down", "Down"),
        Binding("up,k", "cursor_up", "Up"),
    ]

    def __init__(self, manifest: InstallationManifest):
        super().__init__()
        self.manifest = manifest
        self.results: list[str] = []
        self._search_cache: list[tuple[PackageItem, str]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="search_dialog"):
            yield Static("◈ FUZZY PACKAGE FINDER (Ctrl+F / /)", id="modal_title")
            yield Input(placeholder="Type to filter target packages...", id="search_input")
            yield OptionList(id="search_list")

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        for item in self.manifest.official_packages + self.manifest.aur_packages:
            haystack = f"{item.profile} {item.name} {'aur' if item.is_aur else 'official'}".lower()
            self._search_cache.append((item, haystack))
        self._populate_list("")

    @on(Input.Changed)
    def handle_input(self, event: Input.Changed) -> None:
        self._populate_list(event.value)

    def _populate_list(self, query: str) -> None:
        ol = self.query_one(OptionList)
        ol.clear_options()
        self.results = []
        
        query_lower = query.lower().strip()
        query_no_space = query_lower.replace(" ", "")
        scored_results: list[tuple[int, PackageItem]] = []

        for item, haystack in self._search_cache:
            if not query_no_space:
                scored_results.append((100, item))
                continue

            score = 0
            lbl = item.name.lower()
            
            if query_lower == lbl: score += 100
            elif lbl.startswith(query_lower): score += 50
            elif query_lower in lbl: score += 20
            
            q_idx, s_idx = 0, 0
            match_positions: list[int] = []
            while q_idx < len(query_no_space) and s_idx < len(haystack):
                if query_no_space[q_idx] == haystack[s_idx]:
                    match_positions.append(s_idx)
                    q_idx += 1
                s_idx += 1
            
            if q_idx == len(query_no_space):
                if len(match_positions) > 1:
                    spread = (match_positions[-1] - match_positions[0]) - (len(match_positions) - 1)
                    score += max(0, 15 - spread)
                else:
                    score += 15
                score += 5 
                
            if score > 0:
                scored_results.append((score, item))

        scored_results.sort(key=lambda x: (-x[0], x[1].profile, x[1].name))

        options_to_add = []
        for _, item in scored_results:
            txt = Text()
            badge = EliteInstallerApp._get_status_badge(item.status)
            txt.append_text(Text.from_markup(f"{badge} "))
            txt.append(f"[{item.profile}] ", style="cyan bold")
            txt.append(item.name, style="bold white" if item.status != PackageStatus.INSTALLED else "green")
            options_to_add.append(Option(txt))
            self.results.append(item.name)

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
        elif self.results:
            self.dismiss(self.results[0])

    def action_cursor_down(self) -> None: self.query_one(OptionList).action_cursor_down()
    def action_cursor_up(self) -> None: self.query_one(OptionList).action_cursor_up()
    def action_dismiss_modal(self) -> None: self.dismiss(None)


class ConflictModalScreen(ModalScreen[str]):
    """Modal screen displayed when package installation encounters a failure or conflict."""
    CSS = """
    #modal_dialog {
        width: 70;
        height: auto;
        border: heavy $error;
        background: $surface;
        padding: 1 2;
    }
    #modal_title {
        text-align: center;
        text-style: bold;
        color: $error;
        margin-bottom: 1;
    }
    #error_details {
        color: $text-muted;
        margin-bottom: 2;
    }
    #button_bar {
        layout: horizontal;
        align: center middle;
        height: 3;
    }
    Button { margin: 0 1; }
    """

    def __init__(self, package_name: str, error_msg: str):
        super().__init__()
        self.package_name = package_name
        self.error_msg = error_msg

    def compose(self) -> ComposeResult:
        with Container(id="modal_dialog"):
            yield Static(f"⚠️ INSTALLATION FAULT: {self.package_name}", id="modal_title")
            yield Static(f"Error Diagnostic:\n{self.error_msg}", id="error_details")
            with Horizontal(id="button_bar"):
                yield Button("Retry [R]", variant="primary", id="btn_retry")
                yield Button("Manual TTY [M]", variant="warning", id="btn_manual")
                yield Button("Skip Package [S]", variant="error", id="btn_skip")
                yield Button("Abort Suite [A]", variant="default", id="btn_abort")

    def on_mount(self) -> None:
        AudioNotifier.play("alert")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "btn_retry": self.dismiss("retry")
            case "btn_manual": self.dismiss("manual")
            case "btn_skip": self.dismiss("skip")
            case "btn_abort": self.dismiss("abort")

    def on_key(self, event) -> None:
        match event.key.lower():
            case "r": self.dismiss("retry")
            case "m": self.dismiss("manual")
            case "s": self.dismiss("skip")
            case "a": self.dismiss("abort")

# ==============================================================================
# FOOTER TELEMETRY & SHORTCUT COMPONENT (Borrowed Subsystem)
# ==============================================================================
class Shortcut(Label):
    """Interactive footer badge with neon pulse visual telemetry."""
    def __init__(self, key_text: str, label: str, **kwargs) -> None:
        super().__init__(classes="footer-shortcut", **kwargs)
        self.key_text = key_text
        self.label_text = label

    def render(self) -> Text:
        txt = Text()
        if self.has_class("-active"):
            txt.append(f"[{self.key_text}] ", style="bold #0a1612")
            txt.append(self.label_text, style="bold #0a1612")
        else:
            txt.append(f"[{self.key_text}] ", style="bold #00e0b8")
            txt.append(self.label_text, style="#d8e6df")
        return txt

    def blink(self) -> None:
        self.add_class("-active")
        self.refresh()
        def _unblink():
            self.remove_class("-active")
            self.refresh()
        self.set_timer(0.2, _unblink)

class AppFooter(Horizontal):
    """Bottom telemetry bar displaying hotkeys and real-time execution mode."""
    def compose(self) -> ComposeResult:
        yield Shortcut("Ctrl+F / /", "Fuzzy Search", id="sc_search")
        yield Shortcut("M", "Manual TTY", id="sc_manual")
        yield Shortcut("S", "Skip Package", id="sc_skip")
        yield Shortcut("Q / Ctrl+C", "Abort Pipeline", id="sc_quit")
        yield Label(" │ ", classes="footer_sep")
        yield Label("ALPM Engine: Active", id="footer_status")

# ==============================================================================
# TEXTUAL TUI FRONT-END & ARCHITECTURAL ORCHESTRATOR
# ==============================================================================
class EliteInstallerApp(App):
    """The unified Textual TUI managing async PTY streams and visual telemetry."""
    
    CSS = load_dusky_theme()
    BINDINGS = [
        Binding("ctrl+f,/", "open_search", "Search Packages", priority=True),
        Binding("q,ctrl+c", "quit_installer", "Quit", priority=True),
    ]

    def __init__(self, manifest: InstallationManifest, context: RuntimeContext):
        super().__init__()
        self.manifest = manifest
        self.ctx = context
        self.sudo_task: Optional[asyncio.Task] = None
        self.active_child_pid: Optional[int] = None
        
        self.tree_widget = Tree("◈ Target Profiles & Packages")
        self.log_widget = RichLog(id="pty_log", highlight=True, markup=False, wrap=True)
        
        self.progress_bar = ProgressBar(
            total=100,
            show_eta=False,
            show_percentage=False,
            id="progress_bar"
        )
        self.status_label = Label("Initializing installation sequence...", id="status_label")
        self.speed_label = Label("Bandwidth: -- MiB/s | ETA: --:--", id="speed_label")
        self.tree_nodes_map: dict[str, TreeNode] = {}
        self.profile_counts: dict[str, dict[str, int]] = {}

    def compose(self) -> ComposeResult:
        env_mode = "CHROOT ROOT" if self.ctx.is_root else "USER DESKTOP"
        helper_mode = f" | Helper: {self.ctx.aur_helper}" if self.ctx.aur_helper else " | Pacman Core Only"
        with Horizontal(id="top_header"):
            yield Static(
                f"◈ DUSKY PACKAGE INSTALLER  [{env_mode}{helper_mode}]",
                classes="title"
            )
        with Horizontal(id="main_dashboard"):
            with Vertical(id="left_pane"):
                yield self.tree_widget
            with Vertical(id="right_pane"):
                with Container(id="telemetry_box"):
                    yield self.status_label
                    yield self.speed_label
                    yield self.progress_bar
                yield self.log_widget
        yield AppFooter(id="footer")

    def on_mount(self) -> None:
        self.build_profile_tree()
        self.log_system("Environment pre-flight validated. Keyring & ALPM engine online.")
        if self.ctx.is_root and self.ctx.aur_user:
            self.log_system(f"Chroot Root Mode detected. Delegating AUR builds to: {self.ctx.aur_user}")
        self.log_system(
            f"Profiles loaded: {len(self.tree_nodes_map)} packages "
            f"({self.manifest.already_installed} already installed)."
        )
        self.run_installation_pipeline()

    def action_open_search(self) -> None:
        """Triggers the Subsequence Fuzzy Finder modal and highlights selected node in Left Pane tree."""
        if isinstance(self.screen, ModalScreen):
            return
        try: self.query_one("#sc_search", Shortcut).blink()
        except Exception: pass
        
        def on_search_selected(pkg_name: Optional[str]) -> None:
            if pkg_name and (node := self.tree_nodes_map.get(pkg_name)):
                parent = node.parent
                while parent:
                    parent.expand()
                    parent = parent.parent
                self.tree_widget.select_node(node)
                self.tree_widget.scroll_to_node(node)
                self.log_system(f"Fuzzy Finder navigated to target: {pkg_name}")

        self.push_screen(PackageSearchScreen(self.manifest), on_search_selected)

    def action_quit_installer(self) -> None:
        try: self.query_one("#sc_quit", Shortcut).blink()
        except Exception: pass
        self.log_system("Abort signal received. Terminating ALPM pipeline...", is_err=True)
        self.exit(1)

    def build_profile_tree(self) -> None:
        """Populates Left Pane hierarchy with profile folders and live status counters."""
        self.tree_widget.root.expand()
        profiles_dict: dict[str, list[PackageItem]] = {}
        for item in self.manifest.official_packages + self.manifest.aur_packages:
            profiles_dict.setdefault(item.profile, []).append(item)

        for profile_name, items in sorted(profiles_dict.items()):
            total = len(items)
            installed = sum(1 for i in items if i.status == PackageStatus.INSTALLED)
            self.profile_counts[profile_name] = {"total": total, "installed": installed}
            
            p_node = self.tree_widget.root.add(
                f"📁 {profile_name} ({installed}/{total})", expand=True
            )
            for item in items:
                badge = self._get_status_badge(item.status)
                node = p_node.add_leaf(f"{badge} {item.name}")
                self.tree_nodes_map[item.name] = node

    @staticmethod
    def _get_status_badge(status: PackageStatus) -> str:
        match status:
            case PackageStatus.INSTALLED: return "[green]✓[/green]"
            case PackageStatus.INSTALLING: return "[cyan]◉[/cyan]"
            case PackageStatus.PENDING: return "[blue]·[/blue]"
            case PackageStatus.FAILED: return "[red]✗[/red]"
            case PackageStatus.SKIPPED: return "[yellow]─[/yellow]"

    def update_package_node(self, pkg_name: str, status: PackageStatus) -> None:
        """Updates individual package icon and recalculates parent folder ratios."""
        if node := self.tree_nodes_map.get(pkg_name):
            badge = self._get_status_badge(status)
            node.label = Text.from_markup(f"{badge} {pkg_name}")
            
            for item in self.manifest.official_packages + self.manifest.aur_packages:
                if item.name == pkg_name:
                    old_status = item.status
                    item.status = status
                    if old_status != PackageStatus.INSTALLED and status == PackageStatus.INSTALLED:
                        self.profile_counts[item.profile]["installed"] += 1
                    elif old_status == PackageStatus.INSTALLED and status != PackageStatus.INSTALLED:
                        self.profile_counts[item.profile]["installed"] = max(0, self.profile_counts[item.profile]["installed"] - 1)
                        
                    installed = self.profile_counts[item.profile]["installed"]
                    total = self.profile_counts[item.profile]["total"]
                    if node.parent:
                        node.parent.label = Text.from_markup(
                            f"📁 {item.profile} ([green]{installed}[/green]/{total})"
                        )
                    break

    def log_system(self, msg: str, is_err: bool = False) -> None:
        color = "red" if is_err else "cyan"
        self.log_widget.write(Text.from_ansi(f"\033[1;{31 if is_err else 36}m[SYSTEM]\033[0m {msg}"))

    def handle_pty_line(self, line: str) -> None:
        """Processes an intact PTY line. Strips ANSI codes to extract telemetry and discard visual noise."""
        clean = line.strip()
        if not clean:
            return

        stripped = ANSI_STRIP_REGEX.sub("", clean).strip()
        if not stripped:
            return

        extracted_pct = None
        extracted_speed = None
        extracted_eta = None
        found_total = False

        if pct_match := re.search(r"(\d+)%", stripped):
            extracted_pct = pct_match.group(1)

        if total_match := re.search(r"Total\s+\(\d+/\d+\).*?(\d+(?:\.\d+)?\s+[A-Za-z]?i?B/s)\s+([\d:]+)", stripped):
            extracted_speed = total_match.group(1)
            extracted_eta = total_match.group(2)
            found_total = True
        elif not found_total:
            if dl_match := re.search(r"(\d+(?:\.\d+)?\s+[A-Za-z]?i?B/s)\s+([\d:]+)", stripped):
                extracted_speed = dl_match.group(1)
                extracted_eta = dl_match.group(2)

        if extracted_pct:
            self.status_label.update(f"⚡ Processing ALPM Transaction... ({extracted_pct}%)")
            try:
                self.progress_bar.progress = float(extracted_pct)
            except ValueError:
                pass

        if extracted_speed and extracted_eta:
            self.speed_label.update(f"Bandwidth: {extracted_speed} | ETA: {extracted_eta}")

        has_speed = bool(re.search(r"\d+(?:\.\d+)?\s+[A-Za-z]?i?B/s", stripped))
        has_bar = bool(re.search(r"\[[-#=coC\s]+\]", stripped)) or bool(re.search(r"\[[0-9;]*[mK]?[-#=coC\s]+", clean))
        is_fragment = bool(re.search(r"^[\[\]\-#=coC\s\d%:\.\w]+$", stripped)) and len(stripped) < 25
        is_prompt = stripped.startswith(":: Proceed with installation?") or "checking keyring" in stripped.lower()

        if has_speed or has_bar or is_fragment or is_prompt:
            return

        self.log_widget.write(Text.from_ansi(clean))

    @staticmethod
    def _is_package_manager_active() -> bool:
        """Scans /proc natively to check if any package management binaries are actively running."""
        target_procs = {"pacman", "paru", "yay", "makepkg", "fakeroot"}
        try:
            for entry in Path("/proc").iterdir():
                if entry.name.isdigit():
                    try:
                        comm = (entry / "comm").read_text().strip()
                        if comm in target_procs:
                            return True
                    except (OSError, FileNotFoundError):
                        continue
        except OSError:
            pass
        return False

    async def resolve_pacman_lock(self) -> bool:
        """Monitors db.lck; automatically removes stale locks if no package managers are running."""
        if not PACMAN_DB_LOCK.exists():
            return True
            
        self.log_system("Pacman database lock (/var/lib/pacman/db.lck) detected...", is_err=True)
        elapsed = 0
        
        while PACMAN_DB_LOCK.exists():
            if not self._is_package_manager_active():
                self.log_system("No active package managers detected in /proc. Lock is stale!", is_err=True)
                self.status_label.update("🧹 Removing stale pacman database lock...")
                self.log_system("Auto-removing stale /var/lib/pacman/db.lck...")
                
                if self.ctx.is_root:
                    try:
                        PACMAN_DB_LOCK.unlink(missing_ok=True)
                    except OSError as e:
                        self.log_system(f"Failed to remove lock: {e}", is_err=True)
                        return False
                else:
                    rm_proc = await asyncio.create_subprocess_exec(
                        "sudo", "rm", "-f", str(PACMAN_DB_LOCK),
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL
                    )
                    await rm_proc.wait()
                    if rm_proc.returncode != 0:
                        self.log_system("Failed to remove lock file via sudo.", is_err=True)
                        return False
                        
                self.log_system("Stale lock scrubbed successfully. Resuming pipeline.")
                return True

            if elapsed >= 300:
                self.log_system(f"Timed out after 300s waiting for {PACMAN_DB_LOCK} to release.", is_err=True)
                return False
                
            self.status_label.update(f"⚠️ PACMAN DB LOCKED: Active process running ({elapsed}s elapsed)...")
            await asyncio.sleep(1)
            elapsed += 1
            
        self.log_system("Pacman database lock released. Resuming pipeline.")
        return True

    def build_command(self, targets: list[str], is_aur: bool) -> list[str]:
        """Constructs privilege-aware execution commands for Chroot Root vs User Desktop."""
        if not is_aur:
            if self.ctx.is_root:
                return ["pacman", "-S", "--needed", "--noconfirm"] + targets
            return ["sudo", "pacman", "-S", "--needed", "--noconfirm"] + targets
        
        helper = self.ctx.aur_helper if self.ctx.aur_helper else "paru"
        base_aur = [helper, "-S", "--needed", "--noconfirm"] + targets
        if self.ctx.is_root and self.ctx.aur_user:
            return ["sudo", "-u", self.ctx.aur_user, "--preserve-env"] + base_aur
        return base_aur

    def build_manual_command(self, target: str, is_aur: bool) -> str:
        if not is_aur:
            return f"pacman -S {target}" if self.ctx.is_root else f"sudo pacman -S {target}"
        helper = self.ctx.aur_helper if self.ctx.aur_helper else "paru"
        if self.ctx.is_root and self.ctx.aur_user:
            return f"sudo -u {self.ctx.aur_user} {helper} -S {target}"
        return f"{helper} -S {target}"

    @work(thread=False)
    async def run_installation_pipeline(self) -> None:
        """Main async loop executing batch and granular package installations."""
        if not self.ctx.is_root and self.manifest.aur_packages:
            self.sudo_task = asyncio.create_task(AsyncPackageManager.maintain_sudo_heartbeat())
        
        try:
            if not await self.resolve_pacman_lock():
                self.exit(1)
                return

            self.status_label.update("Synchronizing databases & performing full system upgrade...")
            self.log_system("Executing full system upgrade (-Syu)...")
            
            upgrade_cmd = ["pacman", "-Syu", "--noconfirm"] if self.ctx.is_root else ["sudo", "pacman", "-Syu", "--noconfirm"]
            if not await self.execute_pty_command(upgrade_cmd):
                self.log_system("System upgrade failed or interrupted. Aborting suite.", is_err=True)
                return

            pending_official = [p for p in self.manifest.official_packages if p.status == PackageStatus.PENDING]
            if pending_official:
                await self.process_package_set(pending_official, is_aur=False)

            pending_aur = [p for p in self.manifest.aur_packages if p.status == PackageStatus.PENDING]
            if pending_aur and self.ctx.aur_helper:
                await self.process_package_set(pending_aur, is_aur=True)

            self.status_label.update("✨ All installation pipelines completed successfully!")
            self.speed_label.update("Bandwidth: Idle | ETA: 00:00")
            try: self.query_one("#footer_status", Label).update("ALPM Engine: Complete")
            except Exception: pass
            self.log_system("Installation sequence finished. All targets resolved.")
            AudioNotifier.play("complete")
            
        finally:
            if self.sudo_task: self.sudo_task.cancel()

    async def process_package_set(self, packages: list[PackageItem], is_aur: bool) -> None:
        """Attempts batch installation first; degrades seamlessly to granular recovery."""
        target_type = "AUR" if is_aur else "Official Repo"
        pkg_names = [p.name for p in packages]
        
        self.log_system(f"Attempting batch installation for {len(packages)} {target_type} package(s)...")
        for p in packages:
            self.update_package_node(p.name, PackageStatus.INSTALLING)

        if not await self.resolve_pacman_lock(): return

        batch_cmd = self.build_command(pkg_names, is_aur)
        if await self.execute_pty_command(batch_cmd):
            for p in packages:
                self.update_package_node(p.name, PackageStatus.INSTALLED)
                self.progress_bar.advance(1)
            self.log_system(f"Batch transaction for {target_type} completed successfully.")
            return

        self.log_system(
            f"Batch transaction failed for {target_type}. Initiating granular fallback mode...",
            is_err=True
        )
        
        for p in packages:
            await AsyncPackageManager.filter_installed_packages(self.manifest)
            if p.status == PackageStatus.INSTALLED:
                self.update_package_node(p.name, PackageStatus.INSTALLED)
                self.progress_bar.advance(1)
                continue

            self.update_package_node(p.name, PackageStatus.INSTALLING)
            self.status_label.update(f"Granular Target: {p.name} ({target_type})")
            if not await self.resolve_pacman_lock(): return

            cmd = self.build_command([p.name], is_aur)
            while True:
                if await self.execute_pty_command(cmd):
                    self.update_package_node(p.name, PackageStatus.INSTALLED)
                    self.progress_bar.advance(1)
                    self.log_system(f"Successfully installed: {p.name}")
                    break
                else:
                    self.update_package_node(p.name, PackageStatus.FAILED)
                    action = await self.push_screen_wait(
                        ConflictModalScreen(p.name, "Sub-process exited with non-zero status code.")
                    )
                    match action:
                        case "retry":
                            self.log_system(f"Retrying package: {p.name}...")
                            self.update_package_node(p.name, PackageStatus.INSTALLING)
                            continue
                        case "manual":
                            try: self.query_one("#sc_manual", Shortcut).blink()
                            except Exception: pass
                            self.log_system(f"Suspending TTY for manual intervention on {p.name}...")
                            with self.suspend():
                                sys.stdout.flush()
                                old_attr = None
                                try: old_attr = termios.tcgetattr(sys.stdin.fileno())
                                except termios.error: pass
                                    
                                os.system("clear")
                                print(f"\n--- MANUAL INTERVENTION TTY: {p.name} ---")
                                os.system(self.build_manual_command(p.name, is_aur))
                                print("\n--- Returning to Textual UI in 2 seconds ---")
                                time.sleep(2)
                                
                                if old_attr:
                                    try: termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_attr)
                                    except termios.error: pass
                            
                            await AsyncPackageManager.filter_installed_packages(self.manifest)
                            if p.status == PackageStatus.INSTALLED:
                                self.update_package_node(p.name, PackageStatus.INSTALLED)
                                self.progress_bar.advance(1)
                                break
                            continue
                        case "skip":
                            try: self.query_one("#sc_skip", Shortcut).blink()
                            except Exception: pass
                            self.update_package_node(p.name, PackageStatus.SKIPPED)
                            self.progress_bar.advance(1)
                            self.log_system(f"Skipped package: {p.name}", is_err=True)
                            break
                        case "abort":
                            self.log_system("User aborted installation sequence.", is_err=True)
                            self.exit(1)
                            return

    @staticmethod
    def _set_pty_size(fd: int, rows: int = 40, cols: int = 120) -> None:
        """Forces PTY dimensions to 120 columns to prevent pacman progress bar wrapping."""
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

    async def execute_pty_command(self, cmd: list[str]) -> bool:
        """Spawns asynchronous subprocess inside a PTY using an internal stateful line buffer."""
        master_fd, slave_fd = pty.openpty()
        self._set_pty_size(master_fd, rows=40, cols=120)
        
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                close_fds=True
            )
            os.close(slave_fd)
            slave_fd = -1
            self.active_child_pid = proc.pid
            
            loop = asyncio.get_running_loop()
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(reader)
            await loop.connect_read_pipe(lambda: protocol, os.fdopen(master_fd, "rb"))
            master_fd = -1

            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            line_buffer = ""

            while True:
                chunk = await reader.read(1024)
                if not chunk:
                    if text := decoder.decode(b"", final=True):
                        line_buffer += text
                    if line_buffer:
                        for line in re.split(r'[\r\n]+', line_buffer):
                            if line: self.handle_pty_line(line)
                    break
                
                if text := decoder.decode(chunk):
                    line_buffer += text
                    while True:
                        match = re.search(r'[\r\n]', line_buffer)
                        if not match:
                            break
                        idx = match.start()
                        line = line_buffer[:idx]
                        line_buffer = line_buffer[idx+1:]
                        if line:
                            self.handle_pty_line(line)

            rc = await proc.wait()
            self.active_child_pid = None
            return rc == 0
            
        except asyncio.CancelledError:
            if self.active_child_pid:
                try: os.kill(self.active_child_pid, signal.SIGTERM)
                except ProcessLookupError: pass
            raise
        except Exception as e:
            self.log_system(f"PTY Execution Exception: {e}", is_err=True)
            return False
            
        finally:
            self.active_child_pid = None
            if slave_fd != -1:
                try: os.close(slave_fd)
                except OSError: pass
            if master_fd != -1:
                try: os.close(master_fd)
                except OSError: pass

# ==============================================================================
# MAIN ENTRYPOINT & CLI PARSING
# ==============================================================================
def parse_command_line() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dusky Package Installer (Python 3.14 / Textual v11.1)"
    )
    parser.add_argument(
        "-p", "--profiles",
        nargs="+", default=[],
        help="Specify profile names or prefixes to install (e.g., -p 01_all 03_more)."
    )
    return parser.parse_args()

def main() -> None:
    args = parse_command_line()
    manifest = ProfileParser.resolve_manifests(args.profiles)
    
    if not manifest.official_packages and not manifest.aur_packages:
        Console().print("[bold yellow]:: No packages resolved from profiles! Check package_profiles/ directory.[/bold yellow]")
        sys.exit(0)

    try:
        has_aur_targets = len(manifest.aur_packages) > 0
        ctx = verify_runtime_environment(has_aur_targets)
    except PreflightError as err:
        Console().print(f"[bold red]{err}[/bold red]")
        sys.exit(1)

    asyncio.run(AsyncPackageManager.filter_installed_packages(manifest))

    if manifest.aur_packages and ctx.is_root:
        if not ctx.aur_user:
            Console().print(
                "[bold red]CRITICAL: AUR packages requested while running as root in Chroot, "
                "but no unprivileged user exists to run makepkg![/bold red]"
            )
            sys.exit(1)
        try:
            TEMP_SUDOERS_FILE.parent.mkdir(parents=True, exist_ok=True)
            TEMP_SUDOERS_FILE.write_text(f"{ctx.aur_user} ALL=(ALL) NOPASSWD: ALL\n", encoding="utf-8")
            TEMP_SUDOERS_FILE.chmod(0o440)
        except OSError as e:
            Console().print(f"[bold red]CRITICAL: Failed to configure temporary chroot sudoers: {e}[/bold red]")
            sys.exit(1)

    if not ctx.is_root:
        Console().print("[bold cyan]:: Authenticating sudo privileges for official repository installations...[/bold cyan]")
        if os.system("sudo -v") != 0:
            Console().print("[bold red]:: Sudo authentication failed. Aborting.[/bold red]")
            sys.exit(1)

    try:
        app = EliteInstallerApp(manifest, ctx)
        app.run()
    finally:
        if TEMP_SUDOERS_FILE.exists():
            try: TEMP_SUDOERS_FILE.unlink()
            except OSError: pass

if __name__ == "__main__":
    main()
