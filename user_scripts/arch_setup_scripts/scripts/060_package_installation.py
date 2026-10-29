#!/usr/bin/env python3
#d: Install all packages from the manifest (interactive TUI)
# dusky_interactive=true

from __future__ import annotations

import sys

# Runtime Python 3.14+ Gate
if sys.version_info < (3, 14):
    sys.stderr.write("[FATAL] Python 3.14+ is required for Dusky Package Installer.\n")
    sys.exit(1)

import argparse
import asyncio
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
import tempfile
import termios
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, TypeAlias

try:
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
except ImportError as exc:
    sys.stderr.write(f"[FATAL] Missing Python dependencies: {exc}\n")
    sys.stderr.write("Install required dependencies: python-textual python-rich\n")
    sys.exit(8)

# Check Textual Version Gate matching orchestrator (8.2.8+)
with suppress(Exception):
    textual_version = importlib_metadata.version("textual")
    parsed_ver = tuple(int(p) for p in re.split(r"[^0-9]+", textual_version) if p)
    if (parsed_ver + (0, 0, 0))[:3] < (8, 2, 8):
        sys.stderr.write(f"[FATAL] Textual 8.2.8+ is required. Installed: {textual_version}\n")
        sys.exit(1)

# ==============================================================================
# CONFIGURATION & GLOBAL CONSTANTS
# ==============================================================================
PackageNameList: TypeAlias = list[str]

SCRIPT_DIR: Path = Path(__file__).resolve().parent
PROFILES_DIR: Path = SCRIPT_DIR / "package_profiles"
AUR_PROFILES_DIR: Path = PROFILES_DIR / "aur"
PACMAN_DB_LOCK: Path = Path("/var/lib/pacman/db.lck")
TEMP_SUDOERS_FILE: Path = Path("/etc/sudoers.d/99_dusky_temp_aur")

def load_global_config() -> dict:
    config_path = SCRIPT_DIR.parent / "profiles" / "settings" / "orchestrator.toml"
    if config_path.exists():
        with suppress(Exception):
            import tomllib
            with open(config_path, "rb") as f:
                return tomllib.load(f)
    return {}

GLOBAL_CONFIG = load_global_config()
ASCII_MODE = GLOBAL_CONFIG.get("ui", {}).get("ascii_mode", False)

UNICODE_SYMBOLS = {
    "logo": "◈",
    "completed": "✔",
    "running": "●",
    "failed": "✘",
    "skipped": "○",
    "pending": "·",
    "sep": "│",
}

ASCII_SYMBOLS = {
    "logo": "DUSKY",
    "completed": "OK",
    "running": "RUN",
    "failed": "ERR",
    "skipped": "SKIP",
    "pending": "...",
    "sep": "|",
}

def S(key: str) -> str:
    return ASCII_SYMBOLS.get(key, key) if ASCII_MODE else UNICODE_SYMBOLS.get(key, key)

# Pre-compiled regexes matching orchestrator
CONTROL_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\([a-zA-Z]|\x1b\][^\x07]*\x07|\r")
PACKAGE_NAME_REGEX = re.compile(r'^[a-zA-Z0-9@._+\-]+$')
USERNAME_REGEX = re.compile(r'^[a-z_][a-z0-9_-]{0,31}$')
PCT_REGEX = re.compile(r"(?<!\d)(?:100(?:\.0+)?|\d{1,2}(?:\.\d+)?)%")
SPEED_ETA_REGEX = re.compile(
    r"Total\s*\(\s*\d+\s*/\s*\d+\s*\).*?(\d+(?:\.\d+)?\s*[KMG]?i?B/s)\s+([\d:]+)",
    re.IGNORECASE,
)
ALT_SPEED_ETA_REGEX = re.compile(
    r"(\d+(?:\.\d+)?\s*[KMG]?i?B/s)\s+([\d:]+)",
    re.IGNORECASE,
)
SINGLE_NEWLINE_RE = re.compile(r"[\r\n]")
PROGRESS_BAR_REGEX = re.compile(r'\[[#=\- ]{3,}\]|^\s*\[.*\]\s*\d+%|]\s+\d{1,3}%\s*$')

def _build_prompt_rules() -> list[tuple[str, re.Pattern[str], str]]:
    default_rules = [
        ("sudo_password", r"(?i)(\[sudo\] password for [^:]+:|^\s*Password:\s*$|sudo: a password is required|Password:\s*$)", "password"),
        ("pgp_import", r"(?i)(::\s*Import PGP key.*\?\s*\[Y/n\]|::\s*Append key\?.*\[Y/n\]|Import PGP key.*\?\s*\[Y/n\])", "yes"),
        ("pacman_proceed", r"(?i)::\s*(Proceed with (?:installation|download|upgrade)|Continue (?:installation|download|upgrade)).*\?\s*\[Y/n\]", "yes"),
        ("pacman_replace", r"(?i)::\s*Replace\s+.*\?\s*\[Y/n\]", "yes"),
        ("pacman_remove_conflict", r"(?i)(::\s*.*are in conflict.*|::\s*Remove conflicting package.*\?\s*\[y/N\]|::\s*Remove conflicting file.*\?\s*\[Y/n\]|Remove\s+.*\?\s*\[y/N\])", "yes"),
        ("aur_proceed", r"(?i)(Proceed with installation\?|Continue building\?|Continue installing\?|::\s*Proceed with (?:installation|download|build).*\?\s*\[Y/n\])", "yes"),
        ("generic_yes", r"(?i)\[Y/n\]|\(Y/n\)|\[y/N\]|\(y/N\)", "yes"),
    ]
    return [(name, re.compile(pat, re.MULTILINE), kind) for name, pat, kind in default_rules]

PROMPT_RULES = _build_prompt_rules()

class PackageStatus(str, Enum):
    PENDING = "pending"
    INSTALLED = "installed"
    INSTALLING = "installing"
    FAILED = "failed"
    SKIPPED = "skipped"

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
# AUDIO NOTIFIER ENGINE
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

        fd, temp_path = tempfile.mkstemp(prefix=".dusky_sudoers_", dir=str(TEMP_SUDOERS_FILE.parent))
        try:
            os.write(fd, rule.encode("utf-8"))
            os.fchmod(fd, 0o440)
            os.fchown(fd, 0, 0)
            os.close(fd)
        except Exception as e:
            with suppress(OSError): os.close(fd)
            if os.path.exists(temp_path):
                with suppress(OSError): os.unlink(temp_path)
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
                with suppress(OSError): os.unlink(temp_path)

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
# ENVIRONMENT & DUAL-CONTEXT PRIVILEGE RESOLUTION
# ==============================================================================
class PreflightError(Exception):
    """Raised when strict Arch Linux runtime conditions are unmet."""

@dataclass(slots=True)
class RuntimeContext:
    is_root: bool
    aur_helper: str | None = None
    aur_user: str | None = None
    no_upgrade: bool = False
    auto_exit: bool = False


def _is_eligible_aur_user(pw: pwd.struct_passwd) -> bool:
    if pw.pw_uid < 1000 or pw.pw_name in ("nobody", "root"):
        return False
    if pw.pw_name.startswith("systemd-") or pw.pw_shell.endswith(("nologin", "false")):
        return False
    home = Path(pw.pw_dir)
    return home.exists() and home.is_dir()

def get_env_label(is_root: bool, aur_user: str | None = None) -> str:
    """Accurately resolves environment label without false chroot reports."""
    is_chroot = Path("/etc/arch-root").exists() or Path("/.dockerenv").exists()
    if not is_chroot:
        with suppress(OSError):
            is_chroot = os.stat("/").st_ino != os.stat("/proc/1/root").st_ino

    if is_chroot:
        return "CHROOT ROOT"
    elif is_root:
        target = os.environ.get("TARGET_USER") or os.environ.get("SUDO_USER") or aur_user
        if target:
            return f"HOST (SUDO: {target})"
        return "HOST ROOT"
    return "USER DESKTOP"

def verify_runtime_environment(has_aur_targets: bool, no_upgrade: bool = False, auto_exit: bool = False) -> RuntimeContext:
    """Detects execution environment and validates toolchains."""
    is_arch = Path("/etc/arch-release").exists()
    if not is_arch:
        with suppress(Exception):
            os_release = Path("/etc/os-release").read_text()
            is_arch = "Arch" in os_release or "ID=arch" in os_release
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

    return RuntimeContext(is_root=is_root, aur_helper=aur_helper, aur_user=aur_user, no_upgrade=no_upgrade, auto_exit=auto_exit)

# ==============================================================================
# THEME & PALETTE ENGINE (MATCHING ORCHESTRATOR EXACTLY)
# ==============================================================================
def get_theme_path(aur_user: str | None = None) -> Path:
    candidates: list[Path] = []
    if aur_user:
        with suppress(KeyError):
            pw = pwd.getpwnam(aur_user)
            candidates.append(Path(pw.pw_dir) / ".config/matugen/generated/dusky_tui.json")
            candidates.append(Path(pw.pw_dir) / ".config/matugen/generated_fresh/dusky_tui.json")

    target_user = os.environ.get("TARGET_USER") or os.environ.get("SUDO_USER")
    if target_user:
        with suppress(KeyError):
            pw = pwd.getpwnam(target_user.strip())
            candidates.append(Path(pw.pw_dir) / ".config/matugen/generated/dusky_tui.json")
            candidates.append(Path(pw.pw_dir) / ".config/matugen/generated_fresh/dusky_tui.json")

    home = Path.home()
    candidates.extend([
        home / ".config/matugen/generated/dusky_tui.json",
        home / ".config/matugen/generated_fresh/dusky_tui.json",
    ])

    for p in candidates:
        if p.exists():
            return p

    return candidates[0]

_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

def load_palette(aur_user: str | None = None) -> dict[str, str]:
    default_palette = GLOBAL_CONFIG.get(
        "ui", {}
    ).get(
        "default_palette",
        {
            "bg": "#1a110e",
            "fg": "#f1dfd9",
            "accent": "#ffb59b",
            "warning": "#e7bdaf",
            "success": "#d5c68e",
            "muted": "#53433e",
            "error": "#ffb4ab",
        },
    )
    palette: dict[str, str] = dict(default_palette)
    theme_file = get_theme_path(aur_user)
    if theme_file.is_file():
        with suppress(Exception):
            data = json.loads(theme_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, str) and _HEX_COLOR_RE.match(v.strip()):
                        palette[str(k)] = v.strip()

    return palette

def build_app_css(p: dict[str, str]) -> str:
    return f"""
    Screen, Tree, RichLog, ScrollBar, #left_pane, #right_pane {{
        background: {p['bg']};
        color: {p['fg']};
        scrollbar-color: {p['accent']}80;
        scrollbar-color-hover: {p['accent']};
        scrollbar-color-active: {p['accent']};
        scrollbar-background: transparent;
        scrollbar-background-hover: transparent;
        scrollbar-background-active: transparent;
    }}

    #top_header {{
        height: 1;
        dock: top;
        background: {p['bg']};
        color: {p['accent']};
        text-style: bold;
        padding: 0 1;
    }}

    #header_title {{
        width: 100%;
        text-align: center;
    }}

    #main_dashboard {{
        layout: horizontal;
        height: 1fr;
    }}

    #left_pane {{
        width: 26%;
        border-right: solid {p['muted']}4d;
        background: {p['bg']};
        padding: 0;
        height: 100%;
        overflow-x: hidden;
        overflow-y: auto;
    }}

    #right_pane {{
        width: 74%;
        height: 100%;
        layout: vertical;
        background: {p['bg']};
        padding: 0;
        overflow: hidden;
    }}

    #telemetry_box {{
        height: 4;
        border-bottom: none;
        padding: 0 1;
        layout: vertical;
    }}

    #status_label {{
        text-style: bold;
        color: {p['accent']};
    }}

    #speed_label {{
        color: {p['warning']};
        text-style: italic;
    }}

    #progress_bar {{
        width: 100%;
        margin-top: 1;
        height: 1;
    }}

    RichLog {{
        height: 1fr;
        border: none;
        background: {p['bg']};
        color: {p['fg']};
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 0;
        padding: 0;
        margin: 0;
    }}

    Tree {{
        background: {p['bg']};
        color: {p['fg']};
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 0;
        padding: 0;
    }}

    Tree > .tree--guide-line {{
        display: none;
    }}

    Tree:focus {{
        background-tint: transparent 0%;
        background: {p['bg']};
    }}

    Tree > .tree--highlight-line {{
        background: transparent;
    }}

    Tree > .tree--cursor {{
        background: {p['muted']};
        color: {p['fg']};
        text-style: bold;
        border-left: tall {p['accent']};
    }}

    Tree:focus > .tree--cursor {{
        background: {p['muted']};
        color: {p['fg']};
        text-style: bold;
        border-left: tall {p['accent']};
    }}

    #footer {{
        height: 1;
        dock: bottom;
        background: {p['bg']};
        layout: horizontal;
        padding: 0 1;
        margin: 0;
        border: none;
    }}

    .footer-shortcut {{
        padding: 0 1;
        color: {p['fg']};
    }}

    .footer-shortcut.-active {{
        background: {p['accent']};
        color: {p['bg']};
        text-style: bold;
    }}

    .footer_sep {{
        color: {p['warning']};
    }}

    #footer_status {{
        color: {p['success']};
        text-style: italic;
    }}

    PackageSearchScreen, ConflictModalScreen, ConfirmQuitScreen, HelpScreen, LogSearchScreen, CompletionDialog {{
        align: center middle;
        background: rgba(0,0,0,0.88);
        width: 100%;
        height: 100%;
    }}

    #search_dialog, #log_search_dialog {{
        width: 86;
        height: 75%;
        background: {p['bg']};
        border: solid {p['accent']};
        padding: 1 3;
    }}

    #search_list, #log_search_list {{
        height: 1fr;
        border: none;
        background: {p['bg']};
        color: {p['fg']};
    }}

    * {{
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 0;
        scrollbar-color: {p['muted']} {p['bg']};
        scrollbar-color-hover: {p['accent']} {p['bg']};
        scrollbar-color-active: {p['accent']} {p['bg']};
    }}

    #confirm_dialog {{
        width: 60;
        height: auto;
        background: {p['bg']};
        border: solid {p['error']};
        padding: 1 3;
    }}

    #confirm_title, #confirm_text, #button_bar {{
        background: transparent;
        width: 100%;
    }}

    #confirm_title {{
        color: {p['warning']};
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }}

    #confirm_text {{
        color: {p['fg']};
        text-align: center;
        margin-bottom: 1;
    }}

    #modal_dialog {{
        width: 84;
        height: auto;
        background: {p['bg']};
        border: heavy {p['error']};
        padding: 1 3;
    }}

    #modal_title, #error_details {{
        background: transparent;
        width: 100%;
    }}

    #modal_title {{
        color: {p['error']};
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }}

    #error_details {{
        color: {p['warning']};
        margin-bottom: 1;
        max-height: 14;
        overflow-y: auto;
    }}

    #help_dialog {{
        width: 80;
        height: 70%;
        background: {p['bg']};
        border: heavy {p['accent']};
        padding: 1 3;
    }}

    #help_title {{
        color: {p['accent']};
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }}

    #completion_dialog {{
        width: 72;
        height: auto;
        background: {p['bg']};
        border: heavy {p['accent']};
        padding: 1 3;
    }}

    #completion_dialog.-success {{
        border: heavy {p['success']};
    }}

    #completion_dialog.-warning {{
        border: heavy {p['warning']};
    }}

    #completion_dialog.-error {{
        border: heavy {p['error']};
    }}

    #completion_title, #completion_message {{
        background: transparent;
        width: 100%;
    }}

    #completion_title {{
        color: {p['accent']};
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }}

    #completion_dialog.-success #completion_title {{
        color: {p['success']};
    }}

    #completion_dialog.-warning #completion_title {{
        color: {p['warning']};
    }}

    #completion_dialog.-error #completion_title {{
        color: {p['error']};
    }}

    #completion_message {{
        color: {p['fg']};
        max-height: 10;
        overflow-y: auto;
        margin-bottom: 1;
    }}

    #button_bar {{
        layout: horizontal;
        align: center middle;
        height: 1;
        margin-top: 1;
        width: 100%;
    }}

    Button, Button:focus, Button:hover, Button.-primary, Button.-error, Button.-warning {{
        height: 1 !important;
        min-height: 1 !important;
        max-height: 1 !important;
        border: none !important;
        outline: none !important;
        margin: 0 1 !important;
        padding: 0 1 !important;
        text-style: bold;
    }}

    Button.-primary {{
        background: {p['muted']};
        color: {p['fg']};
        border: none !important;
    }}

    Button.-primary:focus {{
        background: {p['accent']};
        color: {p['bg']};
        border: none !important;
    }}

    Button.-primary:hover {{
        background: {p['accent']};
        color: {p['bg']};
        border: none !important;
    }}

    Button.-error {{
        background: {p['muted']};
        color: {p['error']};
        border: none !important;
    }}

    Button.-error:focus {{
        background: {p['error']};
        color: {p['bg']};
        border: none !important;
    }}

    Button.-error:hover {{
        background: {p['error']};
        color: {p['bg']};
        border: none !important;
    }}

    Button.-warning {{
        background: {p['muted']};
        color: {p['warning']};
        border: none !important;
    }}

    Button.-warning:focus {{
        background: {p['warning']};
        color: {p['bg']};
        border: none !important;
    }}

    Button.-warning:hover {{
        background: {p['warning']};
        color: {p['bg']};
        border: none !important;
    }}

    Input {{
        background: {p['bg']};
        border: tall {p['accent']};
        color: {p['fg']};
    }}
    """

def _status_badge(status: PackageStatus, palette: dict[str, str]) -> Text:
    txt = Text()
    match status:
        case PackageStatus.INSTALLED:
            txt.append(f"{S('completed')} ", style=f"bold {palette['success']}")
        case PackageStatus.INSTALLING:
            txt.append(f"{S('running')} ", style=f"bold {palette['accent']}")
        case PackageStatus.PENDING:
            txt.append(f"{S('pending')} ", style=f"dim {palette['fg']}")
        case PackageStatus.FAILED:
            txt.append(f"{S('failed')} ", style=f"bold {palette['error']}")
        case PackageStatus.SKIPPED:
            txt.append(f"{S('skipped')} ", style=f"bold {palette['warning']}")
        case _:
            txt.append(f"{S('pending')} ", style="dim")
    return txt

# ==============================================================================
# PROFILE & MANIFEST RESOLUTION
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
# ASYNCHRONOUS PACMAN & ALPM INTERACTION
# ==============================================================================
class AsyncPackageManager:
    """Manages non-blocking ALPM database checks and subprocess execution."""

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
    async def remove_conflicting_packages(pkg_names: list[str], is_root: bool) -> bool:
        """Safely removes conflicting packages via pacman -Rdd."""
        clean_pkgs = [p for p in pkg_names if PACKAGE_NAME_REGEX.fullmatch(p)]
        if not clean_pkgs:
            return False
        cmd = ["pacman", "-Rdd", "--noconfirm"] + clean_pkgs
        if not is_root:
            cmd = ["sudo"] + cmd
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=30)
            return proc.returncode == 0
        except Exception:
            return False

    @staticmethod
    async def filter_installed_packages(manifest: InstallationManifest) -> None:
        """Queries local ALPM database asynchronously in safe chunks."""
        all_items = manifest.official_packages + manifest.aur_packages
        if not all_items:
            return

        all_names = [item.name for item in all_items]
        uninstalled_names: set[str] = set()

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
                    with suppress(ProcessLookupError): proc.kill()
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
                        with suppress(ProcessLookupError): proc.kill()
                        break
                    if proc.returncode != 0:
                        break
                except Exception:
                    break
                await asyncio.sleep(45)
        except asyncio.CancelledError:
            pass

# ==============================================================================
# MODAL SCREENS (SMART DIAGNOSTIC & OVERWRITE/CONFLICT INTEGRATION)
# ==============================================================================
class ConfirmQuitScreen(ModalScreen[str]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("y,enter", "confirm_abort", "Abort"),
        Binding("n,q", "cancel", "Cancel"),
    ]

    def __init__(self, palette: dict[str, str]):
        super().__init__()
        self.palette = palette

    def compose(self) -> ComposeResult:
        with Container(id="confirm_dialog"):
            yield Static(f"{S('logo')}  ABORT PACKAGE INSTALLATION?", id="confirm_title")
            yield Static("Are you sure you want to terminate the active installation sequence?", id="confirm_text")
            with Horizontal(id="button_bar"):
                yield Button(Text("Cancel [N]"), variant="primary", id="btn_cancel", flat=True)
                yield Button(Text("Abort [Y]"), variant="error", id="btn_abort", flat=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss("abort" if event.button.id == "btn_abort" else "cancel")

    def on_key(self, event: events.Key) -> None:
        key = event.key.lower()
        if key in ("y", "enter", "space"):
            self.dismiss("abort")
        elif key in ("n", "escape", "q"):
            self.dismiss("cancel")

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss("cancel")

    def action_confirm_abort(self) -> None:
        self.dismiss("abort")

    def action_cancel(self) -> None:
        self.dismiss("cancel")


class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("f1", "dismiss", "Dismiss"),
        Binding("question_mark", "dismiss", "Dismiss"),
    ]

    def __init__(self, palette: dict[str, str]):
        super().__init__()
        self.palette = palette

    def compose(self) -> ComposeResult:
        with Container(id="help_dialog"):
            yield Static(f"{S('logo')} Dusky Package Installer Keybindings & Help", id="help_title")

            text = Text()
            text.append("Global Navigation & Shortcuts\n", style=f"bold {self.palette['accent']}")
            text.append("  F1 / ?         Open / close (toggle) this Help screen\n")
            text.append("  Ctrl+F / /     Fuzzy search target packages\n")
            text.append("  Ctrl+L         Search execution output log\n")
            text.append("  F              Cycle package filter (all/pending/installing/installed/failed/skipped)\n")
            text.append("  q / Ctrl+Q / Ctrl+Z   Quit / Abort confirmation dialog\n\n")

            text.append("Conflict & Fault Recovery\n", style=f"bold {self.palette['accent']}")
            text.append("  O              Overwrite unmanaged conflicting files (--overwrite '*')\n")
            text.append("  C              Remove conflicting packages (pacman -Rdd)\n")
            text.append("  R              Retry package installation\n")
            text.append("  M              Manual TTY intervention shell\n")
            text.append("  S              Skip failing package\n\n")

            text.append("Pane Resizing & Selection\n", style=f"bold {self.palette['accent']}")
            text.append("  Alt+Right / ]  Expand left sidebar width\n")
            text.append("  Alt+Left / [   Shrink left sidebar width\n")
            text.append("  j / k or Up/Down       Navigate packages in left sidebar\n")

            yield Static(text)

            with Horizontal(id="button_bar"):
                yield Button(Text("Close [F1/?]"), variant="primary", id="btn_close", flat=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        key = event.key.lower()
        if key == "escape":
            event.stop()
            return
        if key in ("f1", "question_mark", "q", "enter", "space", "?") or event.character in ("?", "q"):
            self.dismiss(None)
            event.stop()

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)

    def action_dismiss(self) -> None:
        self.dismiss(None)


class LogSearchScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Dismiss"),
    ]

    def __init__(self, title: str, lines: list[str]):
        super().__init__()
        self.title_text = title
        self.lines = lines

    def compose(self) -> ComposeResult:
        with Container(id="log_search_dialog"):
            yield Static(f"{S('logo')} Log Search: {self.title_text}", id="log_search_title")
            yield Input(placeholder="Search execution log...", id="log_search_input")
            yield OptionList(id="log_search_list")

    def on_mount(self) -> None:
        self.query_one("#log_search_input", Input).focus()
        self._update("")

    def on_input_changed(self, event: Input.Changed) -> None:
        self._update(event.value)

    def _update(self, query: str) -> None:
        ol = self.query_one("#log_search_list", OptionList)
        ol.clear_options()

        q = query.strip().lower()
        if not q:
            return

        options: list[Option] = []
        for i, line in enumerate(self.lines, start=1):
            if q in line.lower():
                txt = Text()
                txt.append(f"{i:05d} ", style="dim")
                txt.append(line[:300])
                options.append(Option(txt))
                if len(options) >= 200:
                    break

        ol.add_options(options)

    @on(Input.Submitted)
    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


class CompletionDialog(ModalScreen[bool]):
    BINDINGS = [
        Binding("enter,space", "dismiss_stay", "View Logs"),
    ]

    def __init__(
        self,
        title: str = "INSTALLATION SEQUENCE COMPLETE",
        message: str = "",
        level: str = "success",
    ) -> None:
        super().__init__()
        self.title_text = title
        self.message = message
        self.level = level

    def compose(self) -> ComposeResult:
        with Container(id="completion_dialog", classes=f"-{self.level}"):
            yield Label(self.title_text, id="completion_title")
            yield Static(self.message, id="completion_message", markup=False)
            with Horizontal(id="button_bar"):
                yield Button(Text(" View Logs "), variant="primary", id="btn_completion_view", flat=True)
                yield Button(Text(" Quit "), variant="primary", id="btn_completion_quit", flat=True)

    def on_mount(self) -> None:
        with suppress(Exception):
            self.query_one("#btn_completion_view", Button).focus()

    def action_dismiss_stay(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn_completion_quit")

    @on(events.Click)
    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(False)


class PackageSearchScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Cancel"),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, manifest: InstallationManifest, palette: dict[str, str]):
        super().__init__()
        self.manifest = manifest
        self.palette = palette
        self.results: list[str] = []
        self._search_cache: list[tuple[PackageItem, str]] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="search_dialog"):
            yield Static(f"{S('logo')} FUZZY PACKAGE FINDER (Ctrl+F / /)", id="modal_title")
            yield Input(placeholder="Type to filter target packages...", id="search_input")
            yield OptionList(id="search_list")

    def on_mount(self) -> None:
        self.query_one("#search_input", Input).focus()
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
        self.results.clear()

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
            badge_text = _status_badge(item.status, self.palette)
            txt.append_text(badge_text)
            txt.append(f"[{item.profile}] ", style=f"bold {self.palette['accent']}")
            txt.append(
                item.name,
                style=f"bold {self.palette['fg']}" if item.status != PackageStatus.INSTALLED else f"bold {self.palette['success']}"
            )
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
    BINDINGS = [
        Binding("o", "overwrite", "Overwrite", show=True),
        Binding("c", "remove_conflict", "Remove Conflict", show=True),
        Binding("r", "retry", "Retry", show=True),
        Binding("m", "manual", "Manual TTY", show=True),
        Binding("s", "skip", "Skip", show=True),
        Binding("a,escape,q", "abort", "Abort", show=True),
    ]

    def __init__(
        self,
        package_name: str,
        error_msg: str,
        palette: dict[str, str],
        is_file_conflict: bool = False,
        pkg_conflicts: list[str] | None = None,
    ):
        super().__init__()
        self.package_name = package_name
        self.error_msg = error_msg
        self.palette = palette
        self.is_file_conflict = is_file_conflict
        self.pkg_conflicts = pkg_conflicts or []

    def compose(self) -> ComposeResult:
        with Container(id="modal_dialog"):
            yield Static(f"◈ INSTALLATION FAULT: {self.package_name}", id="modal_title")
            yield Static(self.error_msg, id="error_details")
            with Horizontal(id="button_bar"):
                if self.is_file_conflict:
                    yield Button(Text("Overwrite [O]"), variant="warning", id="btn_overwrite", flat=True)
                if self.pkg_conflicts:
                    yield Button(Text("Remove Conflict [C]"), variant="warning", id="btn_remove_conflict", flat=True)
                yield Button(Text("Retry [R]"), variant="primary", id="btn_retry", flat=True)
                yield Button(Text("Manual TTY [M]"), variant="warning", id="btn_manual", flat=True)
                yield Button(Text("Skip [S]"), variant="error", id="btn_skip", flat=True)
                yield Button(Text("Abort [A]"), variant="primary", id="btn_abort", flat=True)

    def on_mount(self) -> None:
        AudioNotifier.play("alert")
        with suppress(Exception):
            if self.is_file_conflict:
                self.query_one("#btn_overwrite", Button).focus()
            elif self.pkg_conflicts:
                self.query_one("#btn_remove_conflict", Button).focus()
            else:
                self.query_one("#btn_retry", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "btn_overwrite": self.dismiss("overwrite")
            case "btn_remove_conflict": self.dismiss("remove_conflict")
            case "btn_retry": self.dismiss("retry")
            case "btn_manual": self.dismiss("manual")
            case "btn_skip": self.dismiss("skip")
            case _: self.dismiss("abort")

    def on_key(self, event: events.Key) -> None:
        k = event.key.lower()
        match k:
            case "o": self.dismiss("overwrite")
            case "c": self.dismiss("remove_conflict")
            case "r": self.dismiss("retry")
            case "m": self.dismiss("manual")
            case "s": self.dismiss("skip")
            case "a" | "escape" | "q": self.dismiss("abort")
            case _: pass

# ==============================================================================
# FOOTER TELEMETRY & SHORTCUT COMPONENT
# ==============================================================================
class Shortcut(Label):
    """Interactive footer badge with pulse visual telemetry."""
    def __init__(self, key_text: str, label: str, palette: dict[str, str], **kwargs) -> None:
        super().__init__(classes="footer-shortcut", **kwargs)
        self.key_text = key_text
        self.label_text = label
        self.palette = palette
        self._blink_timer = None

    def render(self) -> Text:
        txt = Text()
        if self.has_class("-active"):
            txt.append(f"[{self.key_text}] ", style=f"bold {self.palette['bg']}")
            txt.append(self.label_text, style=f"bold {self.palette['bg']}")
        else:
            txt.append(f"[{self.key_text}] ", style=f"bold {self.palette['accent']}")
            txt.append(self.label_text, style=self.palette['fg'])
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
    def __init__(self, palette: dict[str, str], **kwargs):
        super().__init__(**kwargs)
        self.palette = palette

    def compose(self) -> ComposeResult:
        yield Shortcut("Ctrl+F", "Search", self.palette, id="sc_search")
        yield Shortcut("Ctrl+L", "Log", self.palette, id="sc_log")
        yield Shortcut("F", "Filter", self.palette, id="sc_filter")
        yield Shortcut("Q", "Quit", self.palette, id="sc_quit")
        yield Shortcut("?", "Help", self.palette, id="sc_help")
        yield Label(f" {S('sep')} ", classes="footer_sep")
        yield Label("ALPM Engine: Active", id="footer_status")

# ==============================================================================
# MAIN FRONT-END & ORCHESTRATOR APP
# ==============================================================================
FILTERS = ["all", "pending", "installing", "installed", "failed", "skipped"]

class EliteInstallerApp(App):
    """The unified Textual TUI managing async PTY streams and visual telemetry."""
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("ctrl+f", "open_search", "Search Packages", priority=True),
        Binding("/", "open_search", "Search Packages", priority=True),
        Binding("ctrl+l", "search_log", "Search Log", priority=True),
        Binding("ctrl+q", "request_quit", "Quit", priority=True),
        Binding("q", "request_quit", "Quit", priority=True),
        Binding("escape", "request_quit", "Quit", priority=True),
        Binding("ctrl+z", "request_quit", "Quit", priority=True),
        Binding("f1", "help", "Help", priority=True),
        Binding("question_mark", "help", "Help"),
        Binding("f", "cycle_filter", "Filter"),
        Binding("alt+left", "shrink_left_pane", "Shrink Sidebar", priority=True),
        Binding("alt+right", "expand_left_pane", "Expand Sidebar", priority=True),
        Binding("alt+h", "shrink_left_pane", "Shrink Sidebar", priority=True),
        Binding("alt+l", "expand_left_pane", "Expand Sidebar", priority=True),
        Binding("ctrl+left", "shrink_left_pane", "Shrink Sidebar", priority=True),
        Binding("ctrl+right", "expand_left_pane", "Expand Sidebar", priority=True),
        Binding("bracketleft", "shrink_left_pane", "Shrink Sidebar"),
        Binding("bracketright", "expand_left_pane", "Expand Sidebar"),
        Binding("j", "tree_down", "Tree Down"),
        Binding("k", "tree_up", "Tree Up"),
    ]

    def __init__(self, manifest: InstallationManifest, context: RuntimeContext):
        super().__init__()
        self.manifest = manifest
        self.ctx = context
        self.sudo_task: asyncio.Task | None = None
        self.active_child_pid: int | None = None
        self.current_pty_master: int | None = None

        self.palette = load_palette(context.aur_user)
        self.CSS = build_app_css(self.palette)

        self.tree_widget = Tree("Target Profiles & Packages")
        self.tree_widget.show_guides = False
        self.tree_widget.show_root = False

        self.log_widget = RichLog(id="pty_log", highlight=False, markup=False, wrap=True)
        self.progress_bar = ProgressBar(show_eta=False, show_percentage=False, id="progress_bar")
        self.status_label = Label("Initializing installation sequence...", id="status_label")
        self.speed_label = Label("Bandwidth: -- MiB/s | ETA: --:--", id="speed_label")

        self.tree_nodes_map: dict[str, TreeNode] = {}
        self.package_index: dict[str, list[PackageItem]] = {}
        self.profile_counts: dict[str, dict[str, int]] = {}
        self._log_lines: deque[str] = deque(maxlen=6000)

        self.left_pane_width: int = 26
        self.filter_mode = "all"

        self._prompt_counts: dict[str, int] = {}
        self._prompt_last: dict[str, float] = {}
        self._prompt_buffer: str = ""
        self._installation_completed: bool = False

    def compose(self) -> ComposeResult:
        env_mode = get_env_label(self.ctx.is_root, self.ctx.aur_user)
        helper_mode = f" | Helper: {self.ctx.aur_helper}" if self.ctx.aur_helper else " | Pacman Core Only"
        with Horizontal(id="top_header"):
            yield Static(
                f"{S('logo')} DUSKY PACKAGE INSTALLER v14.0  [{env_mode}{helper_mode}]",
                id="header_title",
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
        yield AppFooter(self.palette, id="footer")

    def on_mount(self) -> None:
        pending_total = self.manifest.total_requested - self.manifest.already_installed
        with suppress(Exception):
            self.progress_bar.update(total=max(1, pending_total))

        self.build_profile_tree()
        self.log_system("Environment pre-flight validated. Keyring & ALPM engine online.")
        if self.ctx.is_root and self.ctx.aur_user:
            self.log_system(f"Delegating AUR builds to unprivileged user: {self.ctx.aur_user}")
        self.log_system(
            f"Profiles loaded: {self.manifest.total_requested} packages "
            f"({self.manifest.already_installed} already installed, {pending_total} pending)."
        )
        self.run_installation_pipeline()

    # --------------------------------------------------------------------------
    # KEYBOARD & PANE INTERACTION ACTIONS
    # --------------------------------------------------------------------------
    def _is_modal_screen_active(self) -> bool:
        with suppress(Exception):
            return isinstance(self.screen, ModalScreen)
        return False

    def action_open_search(self) -> None:
        if self._is_modal_screen_active():
            return
        with suppress(Exception):
            self.query_one("#sc_search", Shortcut).blink()

        def on_search_selected(pkg_name: str | None) -> None:
            if pkg_name and (items := self.package_index.get(pkg_name)):
                item = items[0]
                node_key = f"{item.profile}::{item.name}"
                if node := self.tree_nodes_map.get(node_key):
                    parent = node.parent
                    while parent:
                        parent.expand()
                        parent = parent.parent
                    with suppress(Exception):
                        self.tree_widget.select_node(node)
                        self.tree_widget.scroll_to_node(node)
                    self.log_system(f"Fuzzy Finder navigated to: {pkg_name}")

        self.push_screen(PackageSearchScreen(self.manifest, self.palette), on_search_selected)

    def action_search_log(self) -> None:
        if self._is_modal_screen_active():
            return
        with suppress(Exception):
            self.query_one("#sc_log", Shortcut).blink()
        lines = list(self._log_lines)
        self.push_screen(LogSearchScreen("Installation Output", lines))

    def action_help(self) -> None:
        with suppress(Exception):
            if isinstance(self.screen, HelpScreen):
                self.screen.dismiss(None)
                return
        if self._is_modal_screen_active():
            return
        with suppress(Exception):
            self.query_one("#sc_help", Shortcut).blink()
        self.push_screen(HelpScreen(self.palette))

    def action_cycle_filter(self) -> None:
        if self._is_modal_screen_active():
            return
        with suppress(Exception):
            self.query_one("#sc_filter", Shortcut).blink()
        idx = FILTERS.index(self.filter_mode)
        self.filter_mode = FILTERS[(idx + 1) % len(FILTERS)]
        self.build_profile_tree()
        self.log_system(f"Package display filter: {self.filter_mode.upper()}")
        with suppress(Exception):
            self.query_one("#footer_status", Label).update(f"ALPM Engine: Active | Filter: {self.filter_mode.upper()}")

    def _set_pane_widths(self, width_pct: int) -> None:
        min_w = GLOBAL_CONFIG.get("ui", {}).get("min_left_pane_width", 15)
        max_w = GLOBAL_CONFIG.get("ui", {}).get("max_left_pane_width", 80)
        self.left_pane_width = max(min_w, min(max_w, width_pct))
        with suppress(Exception):
            self.query_one("#left_pane").styles.width = f"{self.left_pane_width}%"
            self.query_one("#right_pane").styles.width = f"{100 - self.left_pane_width}%"

    def _update_pane_width_from_mouse(self, mouse_screen_x: int) -> None:
        with suppress(Exception):
            dashboard = self.query_one("#main_dashboard")
            dash_x = dashboard.region.x
            dash_w = dashboard.region.width
            if dash_w > 0:
                rel_x = mouse_screen_x - dash_x
                pct = int(rel_x * 100 / dash_w)
                self._set_pane_widths(pct)

    def action_shrink_left_pane(self) -> None:
        self._set_pane_widths(self.left_pane_width - 4)

    def action_expand_left_pane(self) -> None:
        self._set_pane_widths(self.left_pane_width + 4)

    def action_tree_down(self) -> None:
        with suppress(Exception):
            self.tree_widget.action_cursor_down()

    def action_tree_up(self) -> None:
        with suppress(Exception):
            self.tree_widget.action_cursor_up()

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if self._is_modal_screen_active():
            return
        with suppress(Exception):
            dashboard = self.query_one("#main_dashboard")
            dash_x = dashboard.region.x
            dash_w = dashboard.region.width
            if dash_w > 0:
                current_split_x = dash_x + int(dash_w * self.left_pane_width / 100)
                if abs(event.screen_x - current_split_x) <= 6:
                    self._is_dragging_pane = True
                    self._update_pane_width_from_mouse(event.screen_x)

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if getattr(self, "_is_dragging_pane", False):
            if event.button == 0:
                self._is_dragging_pane = False
            else:
                self._update_pane_width_from_mouse(event.screen_x)

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self._is_dragging_pane = False

    def action_request_quit(self) -> None:
        if self._installation_completed:
            self.exit(0)
            return
        with suppress(Exception):
            if isinstance(self.screen, ConfirmQuitScreen):
                return
            if isinstance(self.screen, CompletionDialog):
                self.exit(0)
                return
        with suppress(Exception):
            self.query_one("#sc_quit", Shortcut).blink()

        async def on_quit_decision(result: str | None) -> None:
            if result == "abort":
                self.log_system("User requested sequence termination.", is_err=True)
                if self.active_child_pid:
                    with suppress(ProcessLookupError, PermissionError, OSError):
                        os.killpg(self.active_child_pid, signal.SIGTERM)
                self.exit(1)

        self.push_screen(ConfirmQuitScreen(self.palette), on_quit_decision)

    # --------------------------------------------------------------------------
    # TREE POPULATION & REBUILDING
    # --------------------------------------------------------------------------
    def build_profile_tree(self) -> None:
        """Populates Left Pane hierarchy with profile folders and status badges."""
        self.tree_widget.clear()
        self.tree_nodes_map.clear()

        profiles_dict: dict[str, list[PackageItem]] = {}
        all_items = self.manifest.official_packages + self.manifest.aur_packages
        for item in all_items:
            profiles_dict.setdefault(item.profile, []).append(item)
            if item.name not in self.package_index:
                self.package_index[item.name] = []
            if item not in self.package_index[item.name]:
                self.package_index[item.name].append(item)

        for profile_name, items in sorted(profiles_dict.items()):
            total = len(items)
            installed = sum(1 for i in items if i.status == PackageStatus.INSTALLED)
            self.profile_counts[profile_name] = {"total": total, "installed": installed}

            visible_items = items
            if self.filter_mode != "all":
                visible_items = [i for i in items if i.status.value == self.filter_mode]

            if not visible_items and self.filter_mode != "all":
                continue

            p_node = self.tree_widget.root.add(
                f"◈ {profile_name} ({installed}/{total})", expand=True
            )
            for item in sorted(visible_items, key=lambda x: x.name):
                badge_text = _status_badge(item.status, self.palette)
                lbl = Text()
                lbl.append_text(badge_text)
                lbl.append(item.name)
                node = p_node.add_leaf(lbl)
                self.tree_nodes_map[f"{item.profile}::{item.name}"] = node

    def update_package_node(self, pkg_name: str, status: PackageStatus) -> None:
        """O(1) package status updates and folder ratio recalculations."""
        items = self.package_index.get(pkg_name, [])
        for item in items:
            old_status = item.status
            item.status = status

            node_key = f"{item.profile}::{item.name}"
            if node := self.tree_nodes_map.get(node_key):
                badge_text = _status_badge(status, self.palette)
                lbl = Text()
                lbl.append_text(badge_text)
                lbl.append(pkg_name)
                node.label = lbl

                if old_status != PackageStatus.INSTALLED and status == PackageStatus.INSTALLED:
                    self.profile_counts[item.profile]["installed"] += 1
                elif old_status == PackageStatus.INSTALLED and status != PackageStatus.INSTALLED:
                    self.profile_counts[item.profile]["installed"] = max(
                        0, self.profile_counts[item.profile]["installed"] - 1
                    )

                installed = self.profile_counts[item.profile]["installed"]
                total = self.profile_counts[item.profile]["total"]
                if node.parent:
                    node.parent.label = f"◈ {item.profile} ({installed}/{total})"

    def log_system(self, msg: str, is_err: bool = False) -> None:
        """Writes system telemetry logs to widget and internal line queue."""
        style = f"bold {self.palette['error']}" if is_err else f"bold {self.palette['accent']}"
        txt = Text()
        txt.append("[SYSTEM] ", style=style)
        txt.append(msg, style=self.palette['fg'])
        self._log_lines.append(f"[SYSTEM] {msg}")
        self.log_widget.write(txt)

    def _extract_error_summary(self, pkg_name: str) -> tuple[str, bool, list[str]]:
        """Extracts smart error diagnostic and checks for file/package conflict patterns."""
        recent_lines = list(self._log_lines)[-200:]
        conflicts = [l for l in recent_lines if "exists in filesystem" in l]

        if conflicts:
            count = len(conflicts)
            sample = "\n".join(f"  • {c}" for c in conflicts[:8])
            if count > 8:
                sample += f"\n  ... and {count - 8} more conflicting file(s)."
            msg = (
                f"File Conflicts Detected ({count} files exist on disk unmanaged):\n"
                f"{sample}\n\n"
                f"Suggested Fix: Overwrite conflicting unmanaged files (--overwrite '*')."
            )
            return msg, True, []

        pkg_conflicts: list[str] = []
        for l in recent_lines:
            if m := re.search(r"(?i)(?:::\s*|conflicting dependencies:\s*)([a-zA-Z0-9@._+\-]+)\s+and\s+([a-zA-Z0-9@._+\-]+)\s+are in conflict", l):
                p1, p2 = m.group(1), m.group(2)
                other = p2 if p1 == pkg_name else p1
                if other not in pkg_conflicts:
                    pkg_conflicts.append(other)

        if pkg_conflicts:
            conflicts_str = ", ".join(f"'{p}'" for p in pkg_conflicts)
            msg = (
                f"Package Conflict Detected ({pkg_name}):\n"
                f"  • '{pkg_name}' conflicts with installed package(s): {conflicts_str}.\n\n"
                f"Suggested Fix: Remove conflicting package(s) or select [Remove Conflict & Install [C]] below."
            )
            return msg, False, pkg_conflicts

        errs = [l for l in recent_lines if any(k in l.lower() for k in ("error", "failed", "conflict", "invalid or corrupted", "pgp"))]
        if errs:
            msg = f"Installation Error Diagnostic ({pkg_name}):\n" + "\n".join(f"  • {e}" for e in errs[-10:])
            return msg, False, []

        return f"Sub-process exited with non-zero status code for {pkg_name}. Check log pane.", False, []

    def _maybe_respond_prompt(self, text: str) -> None:
        """Scans PTY stream for interactive prompts and auto-responds."""
        if self.current_pty_master is None:
            return

        self._prompt_buffer = (getattr(self, "_prompt_buffer", "") + text)[-4096:]
        tail = self._prompt_buffer

        for name, pattern, kind in PROMPT_RULES:
            if not pattern.search(tail):
                continue

            count = self._prompt_counts.get(name, 0)
            if count >= 500:
                continue

            now = time.monotonic()
            last = self._prompt_last.get(name, 0.0)
            if now - last < 0.35:
                continue

            response = b"y\r" if kind == "yes" else b"\r"
            with suppress(OSError):
                os.write(self.current_pty_master, response)

            self._prompt_counts[name] = count + 1
            self._prompt_last[name] = now
            self._prompt_buffer = ""
            self.log_system(f"Auto-responded to prompt: {name}")
            break

    def handle_pty_line(self, line: str) -> None:
        """Parses ANSI colors natively using Rich Text.from_ansi, matching orchestrator."""
        clean = line.strip("\r\n")
        if not clean:
            return

        stripped = CONTROL_ANSI_RE.sub("", clean)
        if not stripped:
            return

        self._log_lines.append(stripped)

        if pct_match := PCT_REGEX.search(stripped):
            with suppress(ValueError):
                pct_val = int(pct_match.group(0).rstrip("%"))
                if 0 <= pct_val <= 100:
                    self.status_label.update(f"◈ Processing ALPM Transaction... ({pct_val}%)")

        if total_match := SPEED_ETA_REGEX.search(stripped):
            self.speed_label.update(f"Bandwidth: {total_match.group(1)} | ETA: {total_match.group(2)}")
        elif dl_match := ALT_SPEED_ETA_REGEX.search(stripped):
            self.speed_label.update(f"Bandwidth: {dl_match.group(1)} | ETA: {dl_match.group(2)}")

        lower = stripped.lower()
        has_speed = bool(ALT_SPEED_ETA_REGEX.search(stripped))
        has_bar = bool(PROGRESS_BAR_REGEX.search(stripped))
        is_pacman_prompt = stripped.startswith(":: Proceed with installation?") or "checking keyring" in lower
        _frag_chars = set("[]-#= oO@%:.0123456789━─░▒▓█▏▎▍▌▋▊▉●○◉◌")
        is_fragment = len(stripped) < 40 and all(c in _frag_chars for c in stripped)

        if has_speed or has_bar or is_fragment or is_pacman_prompt:
            return

        if "\x1b" not in clean and any(
            k in lower for k in ("error", "failed", "warning", "conflict", "exists in filesystem")
        ):
            text = Text(stripped, style=f"bold {self.palette['error']}")
        else:
            try:
                text = Text.from_ansi(clean)
            except Exception:
                text = Text(stripped, style=self.palette['fg'])

        self.log_widget.write(text)

    async def resolve_pacman_lock(self) -> bool:
        if not PACMAN_DB_LOCK.exists():
            return True

        self.log_system(f"Pacman database lock {PACMAN_DB_LOCK} detected...", is_err=True)
        try:
            async with asyncio.timeout(300):
                while PACMAN_DB_LOCK.exists():
                    # Process names cannot establish ownership or reserve this
                    # pathname against a new transaction. Leave removal to its owner.
                    self.status_label.update("◈ PACMAN DB LOCKED: Waiting for release...")
                    await asyncio.sleep(1)
        except TimeoutError:
            self.log_system(f"Timed out after 300s waiting for {PACMAN_DB_LOCK}; lock left untouched.", is_err=True)
            return False

        self.log_system("Pacman database lock released. Resuming pipeline.")
        return True

    def build_command(self, targets: list[str], is_aur: bool, overwrite: bool = False) -> list[str]:
        """Constructs privilege-aware execution commands without shell quote pollution."""
        clean_targets = [t for t in targets if PACKAGE_NAME_REGEX.fullmatch(t)]
        if not clean_targets:
            raise PreflightError("No valid package names after sanitization.")
        
        flags = ["--needed", "--noconfirm", "--color=always"]
        if overwrite:
            flags.append('--overwrite=*')

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
                if not await self.refresh_pacman_keyrings():
                    self.log_system("Keyring refresh failed. Aborting suite.", is_err=True)
                    self.exit(1)
                    return

                self.status_label.update("Synchronizing databases & performing full system upgrade...")
                self.log_system("Executing full system upgrade (-Syu)...")
                upgrade_cmd = (
                    ["pacman", "-Syu", "--noconfirm", "--color=always"]
                    if self.ctx.is_root
                    else ["sudo", "pacman", "-Syu", "--noconfirm", "--color=always"]
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

            all_items = self.manifest.official_packages + self.manifest.aur_packages
            installed_cnt = sum(1 for p in all_items if p.status == PackageStatus.INSTALLED)
            skipped_cnt = sum(1 for p in all_items if p.status == PackageStatus.SKIPPED)
            failed_cnt = sum(1 for p in all_items if p.status == PackageStatus.FAILED)

            self.speed_label.update("Bandwidth: Idle | ETA: 00:00")
            self._installation_completed = True

            if failed_cnt > 0 or skipped_cnt > 0:
                self.status_label.update(
                    f"◈ Installation Completed with Warnings ({installed_cnt} installed, {skipped_cnt} skipped, {failed_cnt} failed)"
                )
                with suppress(Exception):
                    self.query_one("#footer_status", Label).update("ALPM Engine: Complete (Warnings)")
                self.log_system(
                    f"Sequence finished with warnings: {installed_cnt} installed, {skipped_cnt} skipped, {failed_cnt} failed.",
                    is_err=True,
                )
                AudioNotifier.play("alert")
                if self.ctx.auto_exit:
                    self.exit(0 if failed_cnt == 0 else 1)
                else:
                    self.push_screen(
                        CompletionDialog(
                            title="◈ INSTALLATION COMPLETED WITH WARNINGS",
                            message=f"Processed {self.manifest.total_requested} target(s):\n"
                                    f"  • {installed_cnt} Installed\n"
                                    f"  • {skipped_cnt} Skipped\n"
                                    f"  • {failed_cnt} Failed",
                            level="warning" if failed_cnt == 0 else "error",
                        ),
                        self._on_completion_reply,
                    )
            else:
                self.status_label.update("◈ All installation pipelines completed successfully!")
                with suppress(Exception):
                    self.query_one("#footer_status", Label).update("ALPM Engine: Complete")
                self.log_system("Installation sequence finished. All targets resolved.")
                AudioNotifier.play("complete")
                if self.ctx.auto_exit:
                    self.exit(0)
                else:
                    self.push_screen(
                        CompletionDialog(
                            title="◈ INSTALLATION SEQUENCE COMPLETE",
                            message=f"Successfully installed all {self.manifest.total_requested} package target(s).",
                            level="success",
                        ),
                        self._on_completion_reply,
                    )

        finally:
            if self.sudo_task:
                self.sudo_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.sudo_task
            with suppress(Exception):
                if sys.stdin.isatty():
                    os.system("stty sane 2>/dev/null")

    def _on_completion_reply(self, quit_now: bool | None) -> None:
        if quit_now:
            self.exit(0)

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
            if await self.execute_pty_command(cmd):
                self.update_package_node(p.name, PackageStatus.INSTALLED)
                self.progress_bar.advance(1)
                self.log_system(f"Successfully installed: {p.name}")
                continue

            if await AsyncPackageManager.is_package_installed(p.name):
                self.update_package_node(p.name, PackageStatus.INSTALLED)
                self.progress_bar.advance(1)
                self.log_system(f"Verified installed despite exit code: {p.name}")
                continue

            error_summary, is_file_conflict, pkg_conflicts = self._extract_error_summary(p.name)

            # SMART RECOVERY 1: Automatic file conflict resolution (--overwrite *)
            if is_file_conflict:
                self.log_system(f"File conflicts detected for {p.name}. Executing automatic recovery (--overwrite '*')...", is_err=True)
                overwrite_cmd = self.build_command([p.name], is_aur, overwrite=True)
                if await self.execute_pty_command(overwrite_cmd) or await AsyncPackageManager.is_package_installed(p.name):
                    self.update_package_node(p.name, PackageStatus.INSTALLED)
                    self.progress_bar.advance(1)
                    self.log_system(f"◈ Auto-resolved file conflicts and installed {p.name} using --overwrite '*'.")
                    continue

            # SMART RECOVERY 2: Automatic package conflict resolution (remove conflicting package)
            if pkg_conflicts:
                self.log_system(f"Package conflicts detected for {p.name} vs {pkg_conflicts}. Attempting auto-removal of conflicting package(s)...", is_err=True)
                if await AsyncPackageManager.remove_conflicting_packages(pkg_conflicts, self.ctx.is_root):
                    if await self.execute_pty_command(cmd) or await AsyncPackageManager.is_package_installed(p.name):
                        self.update_package_node(p.name, PackageStatus.INSTALLED)
                        self.progress_bar.advance(1)
                        self.log_system(f"◈ Auto-removed conflicting package(s) {pkg_conflicts} and installed {p.name} successfully.")
                        continue

            # SMART RECOVERY 3: Detailed Modal with Overwrite & Remove Conflict options
            self.update_package_node(p.name, PackageStatus.FAILED)
            action = await self.push_screen_wait(
                ConflictModalScreen(p.name, error_summary, self.palette, is_file_conflict=is_file_conflict, pkg_conflicts=pkg_conflicts)
            )
            match action:
                case "overwrite":
                    self.log_system(f"User requested forced overwrite for {p.name}...")
                    self.update_package_node(p.name, PackageStatus.INSTALLING)
                    overwrite_cmd = self.build_command([p.name], is_aur, overwrite=True)
                    if await self.execute_pty_command(overwrite_cmd) or await AsyncPackageManager.is_package_installed(p.name):
                        self.update_package_node(p.name, PackageStatus.INSTALLED)
                        self.progress_bar.advance(1)
                        self.log_system(f"Successfully installed with forced overwrite: {p.name}")
                    else:
                        self.update_package_node(p.name, PackageStatus.FAILED)
                case "remove_conflict":
                    self.log_system(f"User requested removal of conflicting package(s) {pkg_conflicts} for {p.name}...")
                    self.update_package_node(p.name, PackageStatus.INSTALLING)
                    if await AsyncPackageManager.remove_conflicting_packages(pkg_conflicts, self.ctx.is_root):
                        if await self.execute_pty_command(cmd) or await AsyncPackageManager.is_package_installed(p.name):
                            self.update_package_node(p.name, PackageStatus.INSTALLED)
                            self.progress_bar.advance(1)
                            self.log_system(f"Successfully removed conflict and installed {p.name}.")
                        else:
                            self.update_package_node(p.name, PackageStatus.FAILED)
                    else:
                        self.log_system(f"Failed to remove conflicting package(s) {pkg_conflicts}.", is_err=True)
                        self.update_package_node(p.name, PackageStatus.FAILED)
                case "retry":
                    self.log_system(f"Retrying package: {p.name}...")
                    self.update_package_node(p.name, PackageStatus.INSTALLING)
                    continue
                case "manual":
                    with suppress(Exception):
                        self.query_one("#sc_manual", Shortcut).blink()
                    self.log_system(f"Suspending TUI for manual intervention on {p.name}...")
                    with self.suspend():
                        sys.stdout.flush()
                        sys.stderr.flush()
                        old_attr = None
                        with suppress(termios.error):
                            old_attr = termios.tcgetattr(sys.stdin.fileno())
                        try:
                            subprocess.run(["clear"], check=False)
                            print(f"\n--- MANUAL INTERVENTION TTY: {p.name} ---")
                            manual_cmd = self.build_command([p.name], is_aur, overwrite=is_file_conflict)
                            print(f"Executing: {shlex.join(manual_cmd)}\n")
                            subprocess.run(manual_cmd, check=False)
                        finally:
                            if old_attr:
                                with suppress(termios.error):
                                    termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_attr)

                    await asyncio.sleep(1)
                    if await AsyncPackageManager.is_package_installed(p.name):
                        self.update_package_node(p.name, PackageStatus.INSTALLED)
                        self.progress_bar.advance(1)
                        break
                    continue
                case "skip":
                    with suppress(Exception):
                        self.query_one("#sc_skip", Shortcut).blink()
                    self.update_package_node(p.name, PackageStatus.SKIPPED)
                    self.progress_bar.advance(1)
                    self.log_system(f"Skipped package: {p.name}", is_err=True)
                    break
                case "abort" | _:
                    self.log_system("User aborted installation sequence.", is_err=True)
                    self.exit(1)
                    return

    async def refresh_pacman_keyrings(self) -> bool:
        """Ensures local pacman keyrings are initialized, populated, and up to date."""
        self.status_label.update("Validating and refreshing pacman keyrings...")
        self.log_system("Verifying pacman keyring state...")

        keyring_dir = Path("/etc/pacman.d/gnupg")
        has_keyring = keyring_dir.joinpath("trustdb.gpg").exists() and (
            keyring_dir.joinpath("pubring.kbx").exists() or keyring_dir.joinpath("pubring.gpg").exists()
        )

        if not has_keyring:
            self.log_system("Pacman keyring not initialized. Initializing now...", is_err=True)
            init_cmd = ["pacman-key", "--init"]
            if not self.ctx.is_root:
                init_cmd = ["sudo"] + init_cmd
            if not await self.execute_pty_command(init_cmd):
                self.log_system("Failed to initialize keyring.", is_err=True)
                return False

            populate_cmd = ["pacman-key", "--populate", "archlinux"]
            if not self.ctx.is_root:
                populate_cmd = ["sudo"] + populate_cmd
            if not await self.execute_pty_command(populate_cmd):
                self.log_system("Failed to populate keyring.", is_err=True)
                return False

        keyring_pkgs = ["archlinux-keyring"]
        self.log_system(f"Refreshing keyring packages: {', '.join(keyring_pkgs)}...")
        sync_cmd = ["pacman", "-Sy", "--needed", "--noconfirm", "--color=always"] + keyring_pkgs
        if not self.ctx.is_root:
            sync_cmd = ["sudo"] + sync_cmd

        if not await self.execute_pty_command(sync_cmd):
            self.log_system("Failed to refresh keyring packages.", is_err=True)
            return False

        self.log_system("Pacman keyrings are up to date.")
        return True

    @staticmethod
    def _set_pty_size(fd: int) -> None:
        try:
            size = os.get_terminal_size()
            winsize = struct.pack("HHHH", size.lines, size.columns, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            winsize = struct.pack("HHHH", 40, 120, 0, 0)
            with suppress(Exception):
                fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

    async def _kill_proc(self, proc: asyncio.subprocess.Process | None) -> None:
        pid = self.active_child_pid or (proc.pid if proc else None)
        if not pid:
            return
        with suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(pid, signal.SIGTERM)
        with suppress(ProcessLookupError, PermissionError, OSError):
            os.kill(pid, signal.SIGTERM)

        if proc:
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.5)
            except (TimeoutError, asyncio.TimeoutError):
                with suppress(ProcessLookupError, PermissionError, OSError):
                    os.killpg(pid, signal.SIGKILL)
                with suppress(ProcessLookupError, PermissionError, OSError):
                    os.kill(pid, signal.SIGKILL)
                with suppress(Exception):
                    await proc.wait()

    async def _wait_for_process(self, proc: asyncio.subprocess.Process) -> int:
        while True:
            if proc.returncode is not None:
                return proc.returncode

            with suppress(asyncio.TimeoutError, TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=0.15)
                if proc.returncode is not None:
                    return proc.returncode

            if proc.pid is not None:
                try:
                    pid, status = os.waitpid(proc.pid, os.WNOHANG)
                    if pid == proc.pid:
                        if os.WIFEXITED(status):
                            rc = os.WEXITSTATUS(status)
                        elif os.WIFSIGNALED(status):
                            rc = -os.WTERMSIG(status)
                        else:
                            rc = 0
                        if hasattr(proc, "_transport") and proc._transport:
                            proc._transport._returncode = rc  # type: ignore
                        return rc
                except (ChildProcessError, OSError):
                    if proc.returncode is not None:
                        return proc.returncode
                    return 0

            await asyncio.sleep(0.05)

    async def execute_pty_command(self, cmd: list[str]) -> bool:
        """Spawns subprocess inside PTY with async read loop and auto-prompting."""
        try:
            master_fd, slave_fd = pty.openpty()
        except OSError as e:
            self.log_system(f"PTY allocation failed: {e}", is_err=True)
            return False

        self.current_pty_master = master_fd
        self._set_pty_size(slave_fd)

        transport: asyncio.Transport | None = None
        proc: asyncio.subprocess.Process | None = None
        file_obj = None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        line_buffer = ""

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                start_new_session=True,
            )
            with suppress(OSError):
                os.close(slave_fd)
            slave_fd = -1

            self.active_child_pid = proc.pid

            loop = asyncio.get_running_loop()
            reader = asyncio.StreamReader(limit=1024 * 1024)
            protocol = asyncio.StreamReaderProtocol(reader)

            file_obj = os.fdopen(master_fd, "rb", buffering=0)
            master_fd = -1

            transport, _ = await loop.connect_read_pipe(lambda: protocol, file_obj)

            async def read_loop() -> None:
                nonlocal line_buffer
                while True:
                    try:
                        chunk = await reader.read(4096)
                    except Exception:
                        chunk = b""

                    if not chunk:
                        if line_buffer:
                            clean = CONTROL_ANSI_RE.sub("", line_buffer).strip()
                            if clean:
                                with suppress(Exception):
                                    self.handle_pty_line(clean)
                            line_buffer = ""
                        break

                    try:
                        text = decoder.decode(chunk)
                    except Exception:
                        text = chunk.decode("utf-8", errors="replace")

                    if text:
                        self._maybe_respond_prompt(text)

                    line_buffer += text
                    if len(line_buffer) > 32768:
                        clean_chunk = CONTROL_ANSI_RE.sub("", line_buffer[:32768]).strip()
                        if clean_chunk:
                            with suppress(Exception):
                                self.handle_pty_line(clean_chunk)
                        line_buffer = line_buffer[-4096:]

                    while True:
                        m = SINGLE_NEWLINE_RE.search(line_buffer)
                        if not m:
                            break
                        idx = m.start()
                        line = line_buffer[:idx]
                        line_buffer = line_buffer[idx + 1:]
                        clean = CONTROL_ANSI_RE.sub("", line).strip()
                        if clean:
                            with suppress(Exception):
                                self.handle_pty_line(clean)

            read_task = asyncio.create_task(read_loop())

            try:
                rc = await self._wait_for_process(proc)
                try:
                    await asyncio.wait_for(asyncio.shield(read_task), timeout=2.0)
                except (TimeoutError, asyncio.TimeoutError):
                    read_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await read_task
                except Exception:
                    pass

                return rc == 0
            except asyncio.CancelledError:
                await self._kill_proc(proc)
                read_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await read_task
                raise

        except asyncio.CancelledError:
            if proc:
                await self._kill_proc(proc)
            raise
        except Exception as e:
            self.log_system(f"PTY execution exception: {e}", is_err=True)
            return False
        finally:
            self.current_pty_master = None
            self.active_child_pid = None
            if transport is not None:
                with suppress(Exception):
                    transport.close()
            elif file_obj is not None:
                with suppress(Exception):
                    file_obj.close()
            elif master_fd != -1:
                with suppress(OSError):
                    os.close(master_fd)
            if slave_fd != -1:
                with suppress(OSError):
                    os.close(slave_fd)

# ==============================================================================
# MAIN ENTRYPOINT & CLI PARSING
# ==============================================================================
def parse_command_line() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dusky Package Installer (Python 3.14 / Textual v8.2.8+ Hardened)",
        epilog="Example: ./060_package_installation.py -p 01_all 02_desktop",
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
    parser.add_argument(
        "--auto-exit", "--subscript", "--no-completion-dialog",
        action="store_true",
        dest="auto_exit",
        help="Automatically exit upon completion without showing the completion dialog (for running as subscript).",
    )
    return parser.parse_args()

async def main_async(manifest: InstallationManifest, ctx: RuntimeContext) -> int:
    """Executes pre-flight ALPM queries and launches the TUI inside a single event loop."""
    try:
        await AsyncPackageManager.filter_installed_packages(manifest)
    except Exception as e:
        Console(stderr=True).print(f"[yellow]Warning: initial installed check failed: {e}[/]")

    app = EliteInstallerApp(manifest, ctx)
    result = await app.run_async()
    # Textual's return value carries self.exit(status); return_code also
    # captures framework failures that may not produce an application result.
    return app.return_code or (result if isinstance(result, int) else 0)

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
        ctx = verify_runtime_environment(has_aur_targets, no_upgrade=args.no_upgrade, auto_exit=args.auto_exit)
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
        sys.exit(asyncio.run(main_async(manifest, ctx)))
    except KeyboardInterrupt:
        Console(stderr=True).print("\n[bold red]:: Interrupted by user.[/]")
        sys.exit(130)
    finally:
        SudoersManager.cleanup()

if __name__ == "__main__":
    main()
