#!/usr/bin/env python3
# dusky_interactive=true
# ==============================================================================
# DUSKY PACKAGE INSTALLER (v14.0 - Hardened Master-Suite Edition)
# ==============================================================================
# Architecture: Asynchronous Buffered PTY Streams | Textual Split-Screen TUI
# Hardening: Zero-Injection Subprocesses | Atomic Sudoers | O(1) Indexing
# Compatibility: Python 3.12+ (3.14 verified) | Pacman v7.1+ | Textual 8.2.8+
# ==============================================================================

from __future__ import annotations

import asyncio
import argparse
import atexit
import codecs
import fcntl
import functools
import json
import os
import pty
import pwd
import re
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, TypeAlias

from rich.console import Console
from rich.text import Text
from textual import work, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Static, Log, ProgressBar, Button, Label, Tree, Input, OptionList
)
from textual.widgets.option_list import Option
from textual.widgets.tree import TreeNode

# ==============================================================================
# TYPE DEFINITIONS & HIGH-PERFORMANCE COMPILED REGEXES
# ==============================================================================
PackageNameList: TypeAlias = list[str]

SCRIPT_DIR: Path = Path(__file__).resolve().parent
PROFILES_DIR: Path = SCRIPT_DIR / "package_profiles"
AUR_PROFILES_DIR: Path = PROFILES_DIR / "aur"
PACMAN_DB_LOCK: Path = Path("/var/lib/pacman/db.lck")
TEMP_SUDOERS_FILE: Path = Path("/etc/sudoers.d/99_dusky_temp_aur")

# Pre-compiled regexes for hot-loop PTY parsing and security validation
ANSI_STRIP_REGEX = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\]0;.*?(?:\x07|\x1B\\))|\x1B\(B')
PACKAGE_NAME_REGEX = re.compile(r'^[a-zA-Z0-9@._+\-]+$')
USERNAME_REGEX = re.compile(r'^[a-z_][a-z0-9_-]{0,31}$')
PCT_REGEX = re.compile(r'(\d{1,3})%')
SPEED_ETA_REGEX = re.compile(r'Total\s+\(\d+/\d+\).*?(\d+(?:\.\d+)?\s+[KMG]?i?B/s)\s+([\d:]+)', re.IGNORECASE)
ALT_SPEED_ETA_REGEX = re.compile(r'(\d+(?:\.\d+)?\s+[KMG]?i?B/s)\s+([\d:]+)', re.IGNORECASE)
PROGRESS_BAR_REGEX = re.compile(r'\[[#=\- ]{3,}\]|^\s*\[.*\]\s*\d+%')

class PackageStatus(Enum):
    PENDING = auto()
    INSTALLED = auto()
    INSTALLING = auto()
    FAILED = auto()
    SKIPPED = auto()

@dataclass(slots=True)
class PackageItem:
    name: str
    is_aur: bool
    profile: str
    status: PackageStatus = PackageStatus.PENDING
    error_msg: str | None = None

@dataclass(slots=True)
class InstallationManifest:
    official_packages: list[PackageItem] = field(default_factory=list)
    aur_packages: list[PackageItem] = field(default_factory=list)
    total_requested: int = 0
    already_installed: int = 0

# ==============================================================================
# FREEDESKTOP ASYNCHRONOUS AUDIO NOTIFIER
# ==============================================================================
class AudioNotifier:
    """Non-blocking audio engine utilizing native system players."""

    @classmethod
    @functools.lru_cache(maxsize=1)
    def _get_player(cls) -> str | None:
        for bin_name in ("pw-play", "paplay", "canberra-gtk-play"):
            if p := shutil.which(bin_name):
                return p
        return None

    @classmethod
    def play(cls, sound_type: str = "alert") -> None:
        player = cls._get_player()
        if not player:
            return
        sound_map = {
            "alert": "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga",
            "info": "/usr/share/sounds/freedesktop/stereo/dialog-information.oga",
            "complete": "/usr/share/sounds/freedesktop/stereo/complete.oga",
        }
        target = Path(sound_map.get(sound_type, sound_map["alert"]))
        if not target.exists():
            fallback = Path("/usr/share/sounds/freedesktop/stereo/bell.oga")
            if fallback.exists():
                target = fallback
            else:
                return
        
        cmd = [player, str(target)]
        if player.endswith("canberra-gtk-play"):
            cmd = [player, "-i", "dialog-warning" if sound_type == "alert" else "complete"]
            
        try:
            subprocess.Popen(
                cmd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError:
            pass

# ==============================================================================
# SECURITY: ATOMIC LEAST-PRIVILEGE SUDOERS MANAGEMENT
# ==============================================================================
class SudoersManager:
    """Safely provisions temporary, least-privilege sudo rules with atomic replacement."""
    _installed: bool = False

    @staticmethod
    def _validate_username(name: str) -> str:
        name = name.strip()
        if not USERNAME_REGEX.fullmatch(name):
            raise RuntimeError(f"CRITICAL: Invalid username for sudoers: {name!r}")
        return name

    @classmethod
    def setup(cls, aur_user: str) -> None:
        aur_user = cls._validate_username(aur_user)
        TEMP_SUDOERS_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        rule = f"{aur_user} ALL=(ALL) NOPASSWD: /usr/bin/pacman, /usr/bin/paru, /usr/bin/yay\n"

        cls.cleanup()

        # Atomic write pattern: create secure temp file, validate with visudo, replace
        # Temp file MUST be on the same filesystem as the target to allow atomic os.replace()
        fd, temp_path = tempfile.mkstemp(prefix=".dusky_sudoers_", dir=str(TEMP_SUDOERS_FILE.parent))
        try:
            os.write(fd, rule.encode("utf-8"))
            os.fchmod(fd, 0o440)
            os.fchown(fd, 0, 0)
            os.close(fd)
        except Exception as e:
            try: os.close(fd)
            except OSError: pass
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise RuntimeError(f"CRITICAL: Failed to write temp sudoers file: {e}") from e

        try:
            res = subprocess.run(
                ["visudo", "-c", "-f", temp_path],
                capture_output=True,
                timeout=5,
                check=False,
            )
            if res.returncode != 0:
                raise RuntimeError(
                    f"CRITICAL: Generated sudoers rule failed syntax validation: {res.stderr.decode(errors='ignore')}"
                )
            os.replace(temp_path, TEMP_SUDOERS_FILE)
        finally:
            if os.path.exists(temp_path):
                try: os.unlink(temp_path)
                except OSError: pass

        if not cls._installed:
            atexit.register(cls.cleanup)
            cls._installed = True

    @classmethod
    def cleanup(cls) -> None:
        try:
            if TEMP_SUDOERS_FILE.exists() and not TEMP_SUDOERS_FILE.is_symlink():
                TEMP_SUDOERS_FILE.unlink(missing_ok=True)
        except OSError:
            pass

# ==============================================================================
# CORE ENVIRONMENT & DUAL-CONTEXT PRIVILEGE RESOLUTION
# ==============================================================================
class PreflightError(Exception):
    """Raised when strict Arch Linux runtime conditions are unmet."""

@dataclass(slots=True)
class RuntimeContext:
    is_root: bool
    aur_helper: str | None = None
    aur_user: str | None = None
    no_upgrade: bool = False

def _is_eligible_aur_user(pw: pwd.struct_passwd) -> bool:
    if pw.pw_uid < 1000 or pw.pw_name in ("nobody", "root"):
        return False
    if pw.pw_name.startswith("systemd-") or pw.pw_shell.endswith(("nologin", "false")):
        return False
    home = Path(pw.pw_dir)
    return home.exists() and home.is_dir()

def verify_runtime_environment(has_aur_targets: bool, no_upgrade: bool = False) -> RuntimeContext:
    """Detects execution environment and validates toolchains."""
    is_arch = Path("/etc/arch-release").exists()
    if not is_arch:
        try:
            os_release = Path("/etc/os-release").read_text()
            is_arch = "Arch" in os_release or "ID=arch" in os_release
        except Exception:
            pass
    if not is_arch:
        raise PreflightError("CRITICAL: This installer is strictly for Arch Linux systems.")

    for cmd in ("pacman", "sudo"):
        if not shutil.which(cmd):
            raise PreflightError(f"CRITICAL: Required system binary not found: {cmd}.")

    is_root = os.geteuid() == 0
    aur_helper: str | None = None
    aur_user: str | None = None

    if has_aur_targets:
        for helper in ("paru", "yay"):
            if shutil.which(helper):
                aur_helper = helper
                break
        if not aur_helper:
            raise PreflightError("CRITICAL: AUR packages requested but no helper (paru/yay) found.")

        if is_root:
            candidates: list[str] = []
            for env_key in ("TARGET_USER", "SUDO_USER", "USER"):
                if v := os.environ.get(env_key):
                    v = v.strip()
                    if v and USERNAME_REGEX.fullmatch(v):
                        candidates.append(v)
            for uname in candidates:
                try:
                    p = pwd.getpwnam(uname)
                    if _is_eligible_aur_user(p):
                        aur_user = p.pw_name
                        break
                except KeyError:
                    continue
            if not aur_user:
                for p in pwd.getpwall():
                    if _is_eligible_aur_user(p):
                        aur_user = p.pw_name
                        break

    return RuntimeContext(is_root=is_root, aur_helper=aur_helper, aur_user=aur_user, no_upgrade=no_upgrade)

# ==============================================================================
# THEME PARSING & GENERATION ENGINE
# ==============================================================================
def get_theme_path(aur_user: str | None = None) -> Path:
    if aur_user:
        try:
            pw = pwd.getpwnam(aur_user)
            candidate = Path(pw.pw_dir) / ".config/matugen/generated/dusky_tui.json"
            if candidate.exists():
                return candidate
        except KeyError:
            pass

    target_user = os.environ.get("TARGET_USER") or os.environ.get("SUDO_USER")
    if target_user:
        try:
            pw = pwd.getpwnam(target_user.strip())
            return Path(pw.pw_dir) / ".config/matugen/generated/dusky_tui.json"
        except KeyError:
            pass
    return Path.home() / ".config/matugen/generated/dusky_tui.json"

def load_dusky_theme(aur_user: str | None = None) -> str:
    """Parses matugen colors into valid Textual 8.x design tokens and rules."""
    fallback_css = """
    Screen { background: #0a1612; color: #d8e6df; layout: vertical; }
    #top_header { height: 1; dock: top; background: #1a2e28; color: #00e0b8; text-style: bold; content-align: center middle; }
    #top_header .title { width: 100%; content-align: center middle; }
    #main_dashboard { layout: horizontal; height: 1fr; }
    #left_pane { width: 28%; border-right: solid #2a3e38; background: #0a1612; padding: 0 1; height: 100%; }
    #right_pane { width: 72%; height: 100%; layout: vertical; background: #0a1612; }
    #telemetry_box { height: 5; border-bottom: solid #2a3e38; padding: 0 2; layout: vertical; }
    #status_label { text-style: bold; color: #00e0b8; }
    #speed_label { color: #a0d0cb; text-style: italic; }
    #progress_bar { width: 100%; margin-top: 1; height: 1; }
    Log { height: 1fr; border: none; background: #0a1612; color: #d8e6df; scrollbar-gutter: stable; text-wrap: wrap; }
    Tree { background: #0a1612; border: none; padding: 0; color: #d8e6df; }
    #footer { height: 1; dock: bottom; background: #1a2e28; layout: horizontal; padding: 0 1; }
    .footer-shortcut { padding: 0 1; color: #d8e6df; }
    .footer-shortcut.-active { background: #00e0b8; color: #0a1612; text-style: bold; }
    .footer_sep { color: #5a6e68; }
    #footer_status { color: #8dd2da; text-style: italic; }
    PackageSearchScreen, ConflictModalScreen { align: center middle; background: rgba(0,0,0,0.8); }
    #search_dialog { width: 60; height: 75%; background: #0f221d; border: solid #00e0b8; padding: 1 2; }
    #search_list { height: 1fr; border: none; background: #0f221d; }
    #modal_dialog { width: 70; height: auto; border: heavy #ff6b6b; background: #0f221d; padding: 1 2; }
    #modal_title { text-align: center; text-style: bold; color: #ff6b6b; margin-bottom: 1; }
    #error_details { color: #a0b8b2; margin-bottom: 1; max-height: 10; overflow-y: auto; }
    #button_bar { layout: horizontal; align: center middle; height: 3; }
    Button { margin: 0 1; }
    Input { background: #0a1612; border: tall #00e0b8; color: #d8e6df; }
    """

    theme_file = get_theme_path(aur_user)
    if not theme_file.exists():
        return fallback_css
    try:
        data = json.loads(theme_file.read_text(encoding="utf-8"))
        def safe(c: str, fallback: str) -> str:
            return c if isinstance(c, str) and c.startswith("#") and len(c) in (4, 7, 9) else fallback

        bg = safe(data.get("bg"), "#0a1612")
        fg = safe(data.get("fg"), "#d8e6df")
        accent = safe(data.get("accent"), "#00e0b8")
        warning = safe(data.get("warning"), "#a0d0cb")
        success = safe(data.get("success"), "#8dd2da")
        muted = safe(data.get("muted"), "#1a2e28")
        error_c = safe(data.get("error"), "#ffb4ab")

        return f"""
        Screen {{ background: {bg}; color: {fg}; layout: vertical; }}
        #top_header {{ height: 1; dock: top; background: {muted}; color: {accent}; text-style: bold; content-align: center middle; }}
        #top_header .title {{ width: 100%; content-align: center middle; }}
        #main_dashboard {{ layout: horizontal; height: 1fr; }}
        #left_pane {{ width: 28%; border-right: solid {muted}; background: {bg}; padding: 0 1; height: 100%; }}
        #right_pane {{ width: 72%; height: 100%; layout: vertical; background: {bg}; }}
        #telemetry_box {{ height: 5; border-bottom: solid {muted}; padding: 0 2; layout: vertical; }}
        #status_label {{ text-style: bold; color: {accent}; }}
        #speed_label {{ color: {warning}; text-style: italic; }}
        #progress_bar {{ width: 100%; margin-top: 1; height: 1; }}
        Log {{ height: 1fr; border: none; background: {bg}; color: {fg}; scrollbar-gutter: stable; text-wrap: wrap; }}
        Tree {{ background: {bg}; color: {fg}; }}
        #footer {{ height: 1; dock: bottom; background: {muted}; layout: horizontal; padding: 0 1; }}
        .footer-shortcut {{ padding: 0 1; color: {fg}; }}
        .footer-shortcut.-active {{ background: {accent}; color: {bg}; text-style: bold; }}
        .footer_sep {{ color: {warning}; }}
        #footer_status {{ color: {success}; text-style: italic; }}
        PackageSearchScreen, ConflictModalScreen {{ align: center middle; background: rgba(0,0,0,0.8); }}
        #search_dialog {{ width: 60; height: 75%; background: {bg}; border: solid {accent}; padding: 1 2; }}
        #search_list {{ height: 1fr; border: none; background: {bg}; color: {fg}; }}
        #modal_dialog {{ width: 70; height: auto; border: heavy {error_c}; background: {bg}; padding: 1 2; }}
        #modal_title {{ text-align: center; text-style: bold; color: {error_c}; margin-bottom: 1; }}
        #error_details {{ color: {warning}; margin-bottom: 1; max-height: 10; overflow-y: auto; }}
        #button_bar {{ layout: horizontal; align: center middle; height: 3; }}
        Button {{ margin: 0 1; }}
        Input {{ background: {bg}; border: tall {accent}; color: {fg}; }}
        """
    except Exception:
        return fallback_css

# ==============================================================================
# PROFILE & MANIFEST RESOLUTION ENGINE
# ==============================================================================
class ProfileParser:
    """Scans, parses, and deduplicates package profiles."""

    @staticmethod
    def ensure_default_profiles() -> None:
        PROFILES_DIR.mkdir(parents=True, exist_ok=True, mode=0o755)
        AUR_PROFILES_DIR.mkdir(parents=True, exist_ok=True, mode=0o755)

        sample_official = PROFILES_DIR / "01_all"
        if not sample_official.exists():
            sample_official.write_text(
                "# Official Arch repository packages (one per line or space-separated)\n# neovim git base-devel\n",
                encoding="utf-8",
            )
        sample_aur = AUR_PROFILES_DIR / "01_all"
        if not sample_aur.exists():
            sample_aur.write_text(
                "# AUR packages (one per line or space-separated)\n# paru visual-studio-code-bin\n",
                encoding="utf-8",
            )

    @classmethod
    def _read_manifest_file(cls, file_path: Path) -> PackageNameList:
        if not file_path.exists() or not file_path.is_file() or file_path.stat().st_size > 1_000_000:
            return []
        packages: PackageNameList = []
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return []
        for raw_line in content.splitlines():
            clean_line = raw_line.split("#", 1)[0].strip()
            if not clean_line:
                continue
            for token in clean_line.split():
                token = token.strip()
                if token and PACKAGE_NAME_REGEX.fullmatch(token):
                    packages.append(token)
        return packages

    @classmethod
    def resolve_manifests(cls, selected_profiles: list[str]) -> InstallationManifest:
        cls.ensure_default_profiles()

        official_files = [f for f in PROFILES_DIR.iterdir() if f.is_file() and not f.name.startswith(('.', '_'))]
        aur_files = [f for f in AUR_PROFILES_DIR.iterdir() if f.is_file() and not f.name.startswith(('.', '_'))]

        if selected_profiles:
            wanted = set(selected_profiles)
            official_files = [f for f in official_files if f.name in wanted or f.stem in wanted]
            aur_files = [f for f in aur_files if f.name in wanted or f.stem in wanted]

        manifest = InstallationManifest()
        seen_all: set[str] = set()

        for p_file in sorted(official_files, key=lambda p: p.name):
            for pkg_name in cls._read_manifest_file(p_file):
                if pkg_name not in seen_all:
                    seen_all.add(pkg_name)
                    manifest.official_packages.append(
                        PackageItem(name=pkg_name, is_aur=False, profile=p_file.name)
                    )

        for p_file in sorted(aur_files, key=lambda p: p.name):
            for pkg_name in cls._read_manifest_file(p_file):
                if pkg_name not in seen_all:
                    seen_all.add(pkg_name)
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
    async def is_package_installed(pkg_name: str) -> bool:
        if not PACKAGE_NAME_REGEX.fullmatch(pkg_name):
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "pacman", "-Qq", pkg_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
            return proc.returncode == 0
        except Exception:
            return False

    @staticmethod
    async def filter_installed_packages(manifest: InstallationManifest) -> None:
        """Queries local ALPM database in safe chunks asynchronously."""
        all_items = manifest.official_packages + manifest.aur_packages
        if not all_items:
            return

        all_names = [item.name for item in all_items]
        uninstalled_names: set[str] = set()

        # Batch in chunks of 500 to optimize throughput without exceeding ARG_MAX
        for i in range(0, len(all_names), 500):
            chunk = all_names[i:i+500]
            try:
                proc = await asyncio.create_subprocess_exec(
                    "pacman", "-T", *chunk,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
                    text = stdout.decode("utf-8", errors="replace")
                    for line in text.splitlines():
                        line = line.strip()
                        if line and PACKAGE_NAME_REGEX.fullmatch(line):
                            uninstalled_names.add(line)
                except (TimeoutError, asyncio.TimeoutError):
                    try: proc.kill()
                    except ProcessLookupError: pass
                    for name in chunk:
                        if not await AsyncPackageManager.is_package_installed(name):
                            uninstalled_names.add(name)
            except Exception:
                uninstalled_names.update(chunk)

        installed_count = 0
        for item in all_items:
            if item.name not in uninstalled_names:
                item.status = PackageStatus.INSTALLED
                installed_count += 1
            elif item.status == PackageStatus.INSTALLED:
                item.status = PackageStatus.PENDING
        manifest.already_installed = installed_count

    @staticmethod
    async def maintain_sudo_heartbeat() -> None:
        """Keeps sudo timestamp alive without leaking FDs."""
        try:
            while True:
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "sudo", "-n", "-v",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=10)
                    except (TimeoutError, asyncio.TimeoutError):
                        try: proc.kill()
                        except ProcessLookupError: pass
                        break
                    if proc.returncode != 0:
                        break
                except Exception:
                    break
                await asyncio.sleep(45)
        except asyncio.CancelledError:
            pass

# ==============================================================================
# INTERACTIVE MODALS & FUZZY FINDER
# ==============================================================================
def _get_status_badge_static(status: PackageStatus) -> str:
    match status:
        case PackageStatus.INSTALLED: return "[green]✓[/green]"
        case PackageStatus.INSTALLING: return "[cyan]◉[/cyan]"
        case PackageStatus.PENDING: return "[blue]·[/blue]"
        case PackageStatus.FAILED: return "[red]✗[/red]"
        case PackageStatus.SKIPPED: return "[yellow]─[/yellow]"
        case _: return "[blue]·[/blue]"

class PackageSearchScreen(ModalScreen[str | None]):
    """Subsequence fuzzy finder modal."""
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel"),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("k", "cursor_up", "Up", show=False),
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

        options_to_add: list[Option] = []
        for _, item in scored_results[:200]:
            txt = Text()
            badge = _get_status_badge_static(item.status)
            txt.append_text(Text.from_markup(f"{badge} "))
            txt.append(f"[{item.profile}] ", style="cyan bold")
            txt.append(item.name, style="bold white" if item.status != PackageStatus.INSTALLED else "green")
            options_to_add.append(Option(txt, id=item.name))
            self.results.append(item.name)

        ol.add_options(options_to_add)

    @on(OptionList.OptionSelected)
    def on_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option and event.option.id:
            self.dismiss(str(event.option.id))
        elif event.option_index is not None and event.option_index < len(self.results):
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
    """Modal screen displayed when package installation encounters failure."""
    CSS = """
    #modal_dialog { width: 70; height: auto; border: heavy #ff6b6b; background: #0f221d; padding: 1 2; }
    #modal_title { text-align: center; text-style: bold; color: #ff6b6b; margin-bottom: 1; }
    #error_details { color: #a0b8b2; margin-bottom: 2; max-height: 10; overflow-y: auto; }
    #button_bar { layout: horizontal; align: center middle; height: 3; }
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
                yield Button("Skip [S]", variant="error", id="btn_skip")
                yield Button("Abort [A]", variant="default", id="btn_abort")

    def on_mount(self) -> None:
        AudioNotifier.play("alert")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "btn_retry": self.dismiss("retry")
            case "btn_manual": self.dismiss("manual")
            case "btn_skip": self.dismiss("skip")
            case _: self.dismiss("abort")

    def on_key(self, event) -> None:
        k = getattr(event, "key", "").lower()
        match k:
            case "r": self.dismiss("retry")
            case "m": self.dismiss("manual")
            case "s": self.dismiss("skip")
            case "a" | "escape" | "q": self.dismiss("abort")
            case _: pass

# ==============================================================================
# FOOTER TELEMETRY & SHORTCUT COMPONENT
# ==============================================================================
class Shortcut(Label):
    """Interactive footer badge with neon pulse visual telemetry."""
    def __init__(self, key_text: str, label: str, **kwargs) -> None:
        super().__init__(classes="footer-shortcut", **kwargs)
        self.key_text = key_text
        self.label_text = label
        self._blink_timer = None

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
        if not self.is_mounted:
            return
        if self._blink_timer is not None:
            self._blink_timer.stop()
        self.add_class("-active")
        self.refresh()
        def _unblink():
            if self.is_mounted:
                self.remove_class("-active")
                self.refresh()
        self._blink_timer = self.set_timer(0.2, _unblink)

class AppFooter(Horizontal):
    """Bottom telemetry bar displaying hotkeys and real-time execution mode."""
    def compose(self) -> ComposeResult:
        yield Shortcut("Ctrl+F / /", "Fuzzy Search", id="sc_search")
        yield Shortcut("M", "Manual TTY", id="sc_manual")
        yield Shortcut("S", "Skip", id="sc_skip")
        yield Shortcut("Q / Ctrl+C", "Abort", id="sc_quit")
        yield Label(" │ ", classes="footer_sep")
        yield Label("ALPM Engine: Active", id="footer_status")

# ==============================================================================
# TEXTUAL TUI FRONT-END & ORCHESTRATOR
# ==============================================================================
class EliteInstallerApp(App):
    """The unified Textual TUI managing async PTY streams and visual telemetry."""

    BINDINGS = [
        Binding("ctrl+f", "open_search", "Search Packages", priority=True),
        Binding("/", "open_search", "Search Packages", priority=True),
        Binding("q", "quit_installer", "Quit", priority=True),
        Binding("ctrl+c", "quit_installer", "Quit", priority=True),
    ]

    def __init__(self, manifest: InstallationManifest, context: RuntimeContext):
        super().__init__()
        self.manifest = manifest
        self.ctx = context
        self.sudo_task: asyncio.Task | None = None
        self.active_child_pid: int | None = None
        self.CSS = load_dusky_theme(context.aur_user)

        self.tree_widget = Tree("◈ Target Profiles & Packages")
        # Log initialized cleanly without the removed 'wrap' parameter
        self.log_widget = Log(id="pty_log", highlight=True)
        self.progress_bar = ProgressBar(show_eta=False, show_percentage=False, id="progress_bar")
        self.status_label = Label("Initializing installation sequence...", id="status_label")
        self.speed_label = Label("Bandwidth: -- MiB/s | ETA: --:--", id="speed_label")

        self.tree_nodes_map: dict[str, TreeNode] = {}
        self.package_index: dict[str, list[PackageItem]] = {}
        self.profile_counts: dict[str, dict[str, int]] = {}

    def compose(self) -> ComposeResult:
        env_mode = "CHROOT ROOT" if self.ctx.is_root else "USER DESKTOP"
        helper_mode = f" | Helper: {self.ctx.aur_helper}" if self.ctx.aur_helper else " | Pacman Core Only"
        with Horizontal(id="top_header"):
            yield Static(
                f"◈ DUSKY PACKAGE INSTALLER v14.0  [{env_mode}{helper_mode}]",
                classes="title",
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
        pending_total = self.manifest.total_requested - self.manifest.already_installed
        try:
            self.progress_bar.update(total=max(1, pending_total))
        except Exception:
            self.progress_bar.total = max(1, pending_total)

        self.build_profile_tree()
        self.log_system("Environment pre-flight validated. Keyring & ALPM engine online.")
        if self.ctx.is_root and self.ctx.aur_user:
            self.log_system(f"Chroot Root Mode: delegating AUR builds to {self.ctx.aur_user}")
        self.log_system(
            f"Profiles loaded: {self.manifest.total_requested} packages "
            f"({self.manifest.already_installed} already installed, {pending_total} pending)."
        )
        self.run_installation_pipeline()

    def action_open_search(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        try: self.query_one("#sc_search", Shortcut).blink()
        except Exception: pass

        def on_search_selected(pkg_name: str | None) -> None:
            if pkg_name and (items := self.package_index.get(pkg_name)):
                item = items[0]
                node_key = f"{item.profile}::{item.name}"
                if node := self.tree_nodes_map.get(node_key):
                    parent = node.parent
                    while parent:
                        parent.expand()
                        parent = parent.parent
                    self.tree_widget.select_node(node)
                    self.tree_widget.scroll_to_node(node)
                    self.log_system(f"Fuzzy Finder navigated to: {pkg_name}")

        self.push_screen(PackageSearchScreen(self.manifest), on_search_selected)

    def action_quit_installer(self) -> None:
        try: self.query_one("#sc_quit", Shortcut).blink()
        except Exception: pass
        self.log_system("Abort signal received. Terminating pipeline...", is_err=True)
        self.exit(1)

    def build_profile_tree(self) -> None:
        """Populates Left Pane hierarchy with profile folders and live status counters."""
        self.tree_widget.root.expand()
        profiles_dict: dict[str, list[PackageItem]] = {}
        for item in self.manifest.official_packages + self.manifest.aur_packages:
            profiles_dict.setdefault(item.profile, []).append(item)
            self.package_index.setdefault(item.name, []).append(item)

        for profile_name, items in sorted(profiles_dict.items()):
            total = len(items)
            installed = sum(1 for i in items if i.status == PackageStatus.INSTALLED)
            self.profile_counts[profile_name] = {"total": total, "installed": installed}

            p_node = self.tree_widget.root.add(
                f"📁 {profile_name} ({installed}/{total})", expand=True
            )
            for item in sorted(items, key=lambda x: x.name):
                badge = _get_status_badge_static(item.status)
                node = p_node.add_leaf(f"{badge} {item.name}")
                self.tree_nodes_map[f"{item.profile}::{item.name}"] = node

    def update_package_node(self, pkg_name: str, status: PackageStatus) -> None:
        """O(1) package status updates and folder ratio recalculations."""
        items = self.package_index.get(pkg_name, [])
        for item in items:
            old_status = item.status
            item.status = status

            node_key = f"{item.profile}::{item.name}"
            if node := self.tree_nodes_map.get(node_key):
                badge = _get_status_badge_static(status)
                node.label = Text.from_markup(f"{badge} {pkg_name}")

                if old_status != PackageStatus.INSTALLED and status == PackageStatus.INSTALLED:
                    self.profile_counts[item.profile]["installed"] += 1
                elif old_status == PackageStatus.INSTALLED and status != PackageStatus.INSTALLED:
                    self.profile_counts[item.profile]["installed"] = max(
                        0, self.profile_counts[item.profile]["installed"] - 1
                    )

                installed = self.profile_counts[item.profile]["installed"]
                total = self.profile_counts[item.profile]["total"]
                if node.parent:
                    node.parent.label = Text.from_markup(
                        f"📁 {item.profile} ([green]{installed}[/green]/{total})"
                    )

    def log_system(self, msg: str, is_err: bool = False) -> None:
        """Use Rich markup for Textual Log widget."""
        prefix = "[bold red][SYSTEM][/]" if is_err else "[bold cyan][SYSTEM][/]"
        self.log_widget.write_line(f"{prefix} {msg}")

    def handle_pty_line(self, line: str) -> None:
        clean = line.strip()
        if not clean:
            return

        stripped = ANSI_STRIP_REGEX.sub("", clean).strip()
        if not stripped:
            return

        extracted_pct = None
        extracted_speed = None
        extracted_eta = None

        if pct_match := PCT_REGEX.search(stripped):
            try:
                pct_val = int(pct_match.group(1))
                if 0 <= pct_val <= 100:
                    extracted_pct = pct_match.group(1)
            except ValueError:
                pass

        if total_match := SPEED_ETA_REGEX.search(stripped):
            extracted_speed = total_match.group(1)
            extracted_eta = total_match.group(2)
        elif dl_match := ALT_SPEED_ETA_REGEX.search(stripped):
            extracted_speed = dl_match.group(1)
            extracted_eta = dl_match.group(2)

        if extracted_pct:
            self.status_label.update(f"⚡ Processing ALPM Transaction... ({extracted_pct}%)")
        if extracted_speed and extracted_eta:
            self.speed_label.update(f"Bandwidth: {extracted_speed} | ETA: {extracted_eta}")

        lower = stripped.lower()
        if any(k in lower for k in ("error", "failed", "warning", "conflict", "exists in filesystem")):
            self.log_widget.write_line(clean)
            return

        has_speed = bool(ALT_SPEED_ETA_REGEX.search(stripped))
        has_bar = bool(PROGRESS_BAR_REGEX.search(stripped))
        is_fragment = len(stripped) < 20 and all(c in "[]-#= oO@%:.0123456789" for c in stripped)
        is_pacman_prompt = stripped.startswith(":: Proceed with installation?") or "checking keyring" in lower

        if has_speed or has_bar or is_fragment or is_pacman_prompt:
            return

        self.log_widget.write_line(clean)

    @staticmethod
    def _is_package_manager_active() -> bool:
        """Scans /proc natively, ignoring current script and parent shell PIDs."""
        target_procs = {"pacman", "paru", "yay", "makepkg", "fakeroot"}
        my_pid = os.getpid()
        parent_pid = os.getppid()
        try:
            for entry in Path("/proc").iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    pid = int(entry.name)
                    if pid in (my_pid, parent_pid):
                        continue
                    comm_path = entry / "comm"
                    if not comm_path.exists():
                        continue
                    if comm_path.read_text().strip() in target_procs:
                        return True
                except (OSError, FileNotFoundError, ValueError, PermissionError):
                    continue
        except OSError:
            pass
        return False

    async def resolve_pacman_lock(self) -> bool:
        if not PACMAN_DB_LOCK.exists():
            return True

        self.log_system(f"Pacman database lock {PACMAN_DB_LOCK} detected...", is_err=True)
        try:
            async with asyncio.timeout(300):
                while PACMAN_DB_LOCK.exists():
                    if not self._is_package_manager_active():
                        self.log_system("No active package managers in /proc. Lock appears stale!", is_err=True)
                        self.status_label.update("🧹 Removing stale pacman database lock...")

                        if self.ctx.is_root:
                            try:
                                PACMAN_DB_LOCK.unlink(missing_ok=True)
                            except OSError as e:
                                self.log_system(f"Failed to remove lock: {e}", is_err=True)
                                return False
                        else:
                            rm_proc = await asyncio.create_subprocess_exec(
                                "sudo", "-n", "rm", "-f", str(PACMAN_DB_LOCK),
                                stdout=asyncio.subprocess.DEVNULL,
                                stderr=asyncio.subprocess.DEVNULL,
                            )
                            try:
                                await asyncio.wait_for(rm_proc.wait(), timeout=10)
                            except (TimeoutError, asyncio.TimeoutError):
                                try: rm_proc.kill()
                                except ProcessLookupError: pass
                                return False
                            if rm_proc.returncode != 0:
                                self.log_system("Failed to remove lock file via sudo -n.", is_err=True)
                                return False

                        self.log_system("Stale lock scrubbed. Resuming pipeline.")
                        return True

                    self.status_label.update("⚠️ PACMAN DB LOCKED: Active process running...")
                    await asyncio.sleep(1)
        except (TimeoutError, asyncio.TimeoutError):
            self.log_system(f"Timed out after 300s waiting for {PACMAN_DB_LOCK}.", is_err=True)
            return False

        self.log_system("Pacman database lock released. Resuming pipeline.")
        return True

    def build_command(self, targets: list[str], is_aur: bool) -> list[str]:
        """Constructs privilege-aware execution commands without shell injection."""
        clean_targets = [t for t in targets if PACKAGE_NAME_REGEX.fullmatch(t)]
        if not clean_targets:
            raise PreflightError("No valid package names after sanitization.")
        
        flags = ["--needed", "--noconfirm", "--color=never"]
        if not is_aur:
            cmd = ["pacman", "-S"] + flags + ["--"] + clean_targets
            return cmd if self.ctx.is_root else ["sudo"] + cmd

        helper = self.ctx.aur_helper
        if not helper:
            raise PreflightError("CRITICAL: AUR installation requested but no helper found.")

        base_aur = [helper, "-S"] + flags + ["--"] + clean_targets
        if self.ctx.is_root and self.ctx.aur_user:
            return ["sudo", "--preserve-env=HOME,XDG_CACHE_HOME", "-u", self.ctx.aur_user] + base_aur
        return base_aur

    @work(name="install_pipeline", exclusive=True)
    async def run_installation_pipeline(self) -> None:
        if not self.ctx.is_root:
            self.sudo_task = asyncio.create_task(AsyncPackageManager.maintain_sudo_heartbeat())

        try:
            if not await self.resolve_pacman_lock():
                self.exit(1)
                return

            if not self.ctx.no_upgrade:
                self.status_label.update("Synchronizing databases & performing full system upgrade...")
                self.log_system("Executing full system upgrade (-Syu)...")
                upgrade_cmd = (
                    ["pacman", "-Syu", "--noconfirm", "--color=never"]
                    if self.ctx.is_root
                    else ["sudo", "pacman", "-Syu", "--noconfirm", "--color=never"]
                )
                if not await self.execute_pty_command(upgrade_cmd):
                    self.log_system("System upgrade failed or interrupted. Aborting suite.", is_err=True)
                    self.exit(1)
                    return
            else:
                self.log_system("Skipping full system upgrade per user request.")

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
            if self.sudo_task:
                self.sudo_task.cancel()
                try: await self.sudo_task
                except asyncio.CancelledError: pass

    async def process_package_set(self, packages: list[PackageItem], is_aur: bool) -> None:
        target_type = "AUR" if is_aur else "Official Repo"
        pkg_names = [p.name for p in packages]

        self.log_system(f"Attempting batch installation for {len(packages)} {target_type} package(s)...")
        for p in packages:
            self.update_package_node(p.name, PackageStatus.INSTALLING)

        if not await self.resolve_pacman_lock():
            self.exit(1)
            return

        batch_cmd = self.build_command(pkg_names, is_aur)
        if await self.execute_pty_command(batch_cmd):
            for p in packages:
                self.update_package_node(p.name, PackageStatus.INSTALLED)
                self.progress_bar.advance(1)
            self.log_system(f"Batch transaction for {target_type} completed successfully.")
            return

        self.log_system(f"Batch transaction failed for {target_type}. Switching to granular fallback...", is_err=True)
        await AsyncPackageManager.filter_installed_packages(self.manifest)

        for p in packages:
            if p.status == PackageStatus.INSTALLED:
                self.update_package_node(p.name, PackageStatus.INSTALLED)
                self.progress_bar.advance(1)
                continue

            self.update_package_node(p.name, PackageStatus.INSTALLING)
            self.status_label.update(f"Granular Target: {p.name} ({target_type})")
            if not await self.resolve_pacman_lock():
                self.exit(1)
                return

            cmd = self.build_command([p.name], is_aur)
            while True:
                if await self.execute_pty_command(cmd):
                    self.update_package_node(p.name, PackageStatus.INSTALLED)
                    self.progress_bar.advance(1)
                    self.log_system(f"Successfully installed: {p.name}")
                    break
                else:
                    if await AsyncPackageManager.is_package_installed(p.name):
                        self.update_package_node(p.name, PackageStatus.INSTALLED)
                        self.progress_bar.advance(1)
                        self.log_system(f"Verified installed despite exit code: {p.name}")
                        break

                    self.update_package_node(p.name, PackageStatus.FAILED)
                    action = await self.push_screen_wait(
                        ConflictModalScreen(p.name, "Sub-process exited with non-zero status code. Check log pane.")
                    )
                    match action:
                        case "retry":
                            self.log_system(f"Retrying package: {p.name}...")
                            self.update_package_node(p.name, PackageStatus.INSTALLING)
                            continue
                        case "manual":
                            try: self.query_one("#sc_manual", Shortcut).blink()
                            except Exception: pass
                            self.log_system(f"Suspending TUI for manual intervention on {p.name}...")
                            with self.suspend():
                                sys.stdout.flush()
                                sys.stderr.flush()
                                old_attr = None
                                try: old_attr = termios.tcgetattr(sys.stdin.fileno())
                                except termios.error: pass
                                try:
                                    subprocess.run(["clear"], check=False)
                                    print(f"\n--- MANUAL INTERVENTION TTY: {p.name} ---")
                                    manual_cmd = self.build_command([p.name], is_aur)
                                    print(f"Executing: {shlex.join(manual_cmd)}\n")
                                    subprocess.run(manual_cmd, check=False)
                                finally:
                                    if old_attr:
                                        try: termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_attr)
                                        except termios.error: pass

                            await asyncio.sleep(1)
                            if await AsyncPackageManager.is_package_installed(p.name):
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
                        case "abort" | _:
                            self.log_system("User aborted installation sequence.", is_err=True)
                            self.exit(1)
                            return

    @staticmethod
    def _set_pty_size(fd: int, rows: int = 40, cols: int = 120) -> None:
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

    async def execute_pty_command(self, cmd: list[str]) -> bool:
        """Spawns async subprocess inside a leak-free PTY with incremental UTF-8."""
        master_fd, slave_fd = pty.openpty()
        os.set_inheritable(master_fd, False)
        os.set_inheritable(slave_fd, True)
        self._set_pty_size(slave_fd, rows=40, cols=120)

        transport: asyncio.Transport | None = None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                preexec_fn=os.setsid,
            )
            try: os.close(slave_fd)
            except OSError: pass
            slave_fd = -1
            self.active_child_pid = proc.pid

            loop = asyncio.get_running_loop()
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(reader)

            file_obj = os.fdopen(master_fd, "rb", buffering=0)
            master_fd = -1
            transport, _ = await loop.connect_read_pipe(lambda: protocol, file_obj)

            line_buffer = ""
            while True:
                try: chunk = await reader.read(1024)
                except Exception: chunk = b""
                if not chunk:
                    if line_buffer:
                        for line in re.split(r"[\r\n]+", line_buffer):
                            if line: self.handle_pty_line(line)
                    break

                try: text = decoder.decode(chunk)
                except Exception: text = chunk.decode("utf-8", errors="replace")

                line_buffer += text
                while True:
                    m = re.search(r"[\r\n]", line_buffer)
                    if not m: break
                    idx = m.start()
                    line = line_buffer[:idx]
                    line_buffer = line_buffer[idx + 1 :]
                    if line: self.handle_pty_line(line)

            rc = await proc.wait()
            return rc == 0

        except asyncio.CancelledError:
            if self.active_child_pid:
                try: os.killpg(self.active_child_pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    try: os.kill(self.active_child_pid, signal.SIGTERM)
                    except ProcessLookupError: pass
                
                try:
                    await asyncio.wait_for(proc.wait(), timeout=1.5)
                except (TimeoutError, asyncio.TimeoutError):
                    try: os.killpg(self.active_child_pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        try: os.kill(self.active_child_pid, signal.SIGKILL)
                        except ProcessLookupError: pass
                    try: await asyncio.wait_for(proc.wait(), timeout=0.5)
                    except Exception: pass
                except Exception:
                    pass
            raise
        except Exception as e:
            self.log_system(f"PTY Execution Exception: {e}", is_err=True)
            return False

        finally:
            self.active_child_pid = None
            if transport:
                try: transport.close()
                except Exception: pass
            elif master_fd != -1:
                try: os.close(master_fd)
                except OSError: pass
            if slave_fd != -1:
                try: os.close(slave_fd)
                except OSError: pass

# ==============================================================================
# MAIN ENTRYPOINT & CLI PARSING
# ==============================================================================
def parse_command_line() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dusky Package Installer (Python 3.14 / Textual v14.0 Hardened)",
        epilog="Example: ./060_package_installation.py -p 01_core 02_desktop",
    )
    parser.add_argument(
        "-p", "--profiles",
        nargs="+",
        default=[],
        metavar="PROFILE",
        help="Specify exact profile names to install (e.g., -p 01_all 03_more).",
    )
    parser.add_argument(
        "--no-upgrade",
        action="store_true",
        help="Skip full system upgrade (-Syu) step.",
    )
    return parser.parse_args()

async def main_async(manifest: InstallationManifest, ctx: RuntimeContext) -> None:
    """Executes pre-flight ALPM queries and launches the TUI inside a single event loop."""
    try:
        await AsyncPackageManager.filter_installed_packages(manifest)
    except Exception as e:
        Console(stderr=True).print(f"[yellow]Warning: initial installed check failed: {e}[/]")

    app = EliteInstallerApp(manifest, ctx)
    await app.run_async()

def main() -> None:
    args = parse_command_line()
    manifest = ProfileParser.resolve_manifests(args.profiles)

    if not manifest.official_packages and not manifest.aur_packages:
        Console(stderr=True).print(
            "[bold yellow]:: No packages resolved from profiles! Check package_profiles/ directory.[/bold yellow]"
        )
        sys.exit(0)

    try:
        has_aur_targets = len(manifest.aur_packages) > 0
        ctx = verify_runtime_environment(has_aur_targets, no_upgrade=args.no_upgrade)
    except PreflightError as err:
        Console(stderr=True).print(f"[bold red]{err}[/bold red]")
        sys.exit(1)

    if manifest.aur_packages and ctx.is_root:
        if not ctx.aur_user:
            Console(stderr=True).print(
                "[bold red]CRITICAL: AUR packages requested while running as root in Chroot, "
                "but no unprivileged user exists to run makepkg![/bold red]"
            )
            sys.exit(1)
        try:
            SudoersManager.setup(ctx.aur_user)
        except RuntimeError as e:
            Console(stderr=True).print(f"[bold red]{e}[/bold red]")
            sys.exit(1)

        # Ensure sudoers cleanup runs even on SIGTERM (atexit/finally don't fire for signals)
        def _sigterm_cleanup(signum: int, frame: object) -> None:
            SudoersManager.cleanup()
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
        signal.signal(signal.SIGTERM, _sigterm_cleanup)

    if not ctx.is_root:
        Console().print("[bold cyan]:: Elevating privileges via sudo...[/bold cyan]")
        try:
            os.execvp("sudo", ["sudo", sys.executable, __file__] + sys.argv[1:])
        except OSError as e:
            Console(stderr=True).print(f"[bold red]:: Privilege elevation failed: {e}[/bold red]")
            sys.exit(1)

    try:
        asyncio.run(main_async(manifest, ctx))
    except KeyboardInterrupt:
        Console(stderr=True).print("\n[bold red]:: Interrupted by user.[/]")
        sys.exit(130)
    finally:
        SudoersManager.cleanup()

if __name__ == "__main__":
    main()
