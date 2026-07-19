#!/usr/bin/env python3
# dusky_interactive=true
# ==============================================================================
# DUSKY ARCH LINUX MASTER ORCHESTRATOR (v16.2.3 - Ultimate Master Suite)
# ==============================================================================
# Architecture: Asynchronous Buffered PTY Streams | Textual Split-Screen TUI
# Hardening: Zero-Injection Subprocesses | Sudo Heartbeat Engine | O(1) Indexing
# Storage: Strictly forced to ~/Documents as requested.
# Git Engine: Atomic Hash-Matching Sync | Safe-Restore | Conflict Isolation
# Compatibility: Python 3.11+ ONLY | Textual 8.2.8+ | systemd 261 | Kernel 7.1+
# ==============================================================================

import asyncio
import argparse
import atexit
import codecs
import datetime
import fcntl
import functools
import hashlib
import json
import os
import pty
import pwd
import re
import shlex
import shutil
import signal
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
import tomllib
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from rich.console import Console
from rich.text import Text
from textual import work, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Static, RichLog, ProgressBar, Button, Label, Tree, Input, OptionList, ContentSwitcher
)
from textual.widgets.option_list import Option
from textual.widgets.tree import TreeNode

# ==============================================================================
# PATHS - STRICTLY CONFINED TO ~/Documents
# ==============================================================================
def _get_xdg_runtime_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))

SCRIPT_DIR: Path = Path(__file__).resolve().parent
PROFILES_DIR: Path = SCRIPT_DIR / "profiles"

# Strictly forced paths (No XDG / .local fallback)
DOCUMENTS_ROOT: Path = Path.home() / "Documents"
LOG_BASE_DIR: Path = DOCUMENTS_ROOT / "logs"
STATE_BASE_DIR: Path = DOCUMENTS_ROOT

# Transient lock file (kept in memory/temp run directory)
LOCK_FILE: Path = _get_xdg_runtime_dir() / "dusky-orchestra.lock"

FALLBACK_ROWS: int = 40
FALLBACK_COLS: int = 120
_LOCK_FD: int | None = None

# ==============================================================================
# HIGH-PERFORMANCE COMPILED REGEXES - ATOMIC GROUPS (PYTHON 3.11+)
# ==============================================================================
_FRAG_CHARS: frozenset[str] = frozenset("[]-#= oO@%:.0123456789━─░▒▓█▏▎▍▌▋▊▉●○◉◌")
_HEX_COLOR_RE = re.compile(r'^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$')
_INTERACTIVE_RE = re.compile(r'^\s*#\s*dusky_interactive\s*=\s*(?:true|1)\b', re.IGNORECASE)

ANSI_STRIP_REGEX = re.compile(
    r'\x1B(?:[@-Z\\-_]|\[(?>(?:[0-?]*+)[ -/]*+[@-~])|\](?>\d*;.*?)(?:\x07|\x1B\\)|\]8;;.*?(?:\x07|\x1B\\)|\x1B\(B)'
)
PCT_REGEX = re.compile(r'(?<![0-9])(?>\d{1,2}|100)%')
SPEED_ETA_REGEX = re.compile(r'Total\s+\(\d+/\d+\).*?(\d+(?:\.\d+)?\s+[KMG]?i?B/s)\s+([\d:]+)', re.IGNORECASE)
ALT_SPEED_ETA_REGEX = re.compile(r'(\d+(?:\.\d+)?\s+[KMG]?i?B/s)\s+([\d:]+)', re.IGNORECASE)
PROGRESS_BAR_REGEX = re.compile(r'\[[#=\- ]{3,}\]|^\s*\[.*\]\s*\d+%|]\s+\d{1,3}%\s*$')
BRACKET_NEWLINE_RE = re.compile(r'[\r\n]+')
SINGLE_NEWLINE_RE = re.compile(r'[\r\n]')

class TaskStatus(Enum):
    PENDING = auto()
    COMPLETED = auto()
    RUNNING = auto()
    FAILED = auto()
    SKIPPED = auto()

@dataclass(slots=True)
class OrchestratorTask:
    raw_entry: str
    mode: str
    script_name: str
    args: list[str]
    ignore_fail: bool
    interactive: bool = False
    resolved_path: Path | None = None
    interpreter: str = "bash"
    state_key: str = ""
    status: TaskStatus = TaskStatus.PENDING
    error_msg: str | None = None

@dataclass(slots=True)
class ProfileConfig:
    filepath: Path
    name: str
    description: str
    post_script_delay: int
    git_enabled: bool
    git_dir: str
    git_work_tree: str
    git_remote: str
    search_dirs: list[str]
    conflict_resolutions: dict[str, str]
    tasks: list[OrchestratorTask]

# ==============================================================================
# FREEDESKTOP ASYNCHRONOUS AUDIO NOTIFIER - PIPEWIRE NATIVE
# ==============================================================================
class AudioNotifier:
    """Non-blocking audio engine utilizing native PipeWire."""

    @classmethod
    @functools.cache
    def _get_player(cls) -> str | None:
        for bin_name in ("pw-play", "paplay"):
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
        
        cmd = [player, "--media-role=event", str(target)] if player.endswith("pw-play") else [player, str(target)]
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
# UTILITIES & ACTIVE PROCESS LOCK RESOLUTION
# ==============================================================================
def resolve_home(path_str: str) -> Path:
    expanded = os.path.expandvars(path_str)
    return Path(expanded).expanduser()

def get_lock_holders() -> str:
    """Finds PIDs holding our lock file descriptor via /proc scanning."""
    if not LOCK_FILE.exists():
        return ""
    try:
        real_lock = LOCK_FILE.resolve()
    except Exception:
        return ""

    holders: list[str] = []
    proc_dir = Path("/proc")
    if not proc_dir.exists():
        return ""

    try:
        pids = [d for d in proc_dir.iterdir() if d.name.isdigit()]
    except PermissionError:
        return ""

    my_pid = str(os.getpid())
    for pid_dir in pids:
        if pid_dir.name == my_pid:
            continue
        fd_dir = pid_dir / "fd"
        try:
            if not fd_dir.exists():
                continue
            for fd_link in fd_dir.iterdir():
                try:
                    if fd_link.resolve() == real_lock:
                        cmdline_path = pid_dir / "cmdline"
                        cmd = ""
                        try:
                            if cmdline_path.exists():
                                cmd = cmdline_path.read_text(errors='replace').replace('\x00', ' ').strip()
                        except (PermissionError, OSError):
                            cmd = ""
                        if not cmd:
                            cmd = f"[pid {pid_dir.name}]"
                        holders.append(f"  - PID {pid_dir.name}: {cmd}")
                        break
                except (PermissionError, FileNotFoundError, OSError):
                    continue
        except (PermissionError, OSError):
            continue

    return "\n".join(holders)

def _cleanup_lock() -> None:
    global _LOCK_FD
    try:
        if _LOCK_FD is not None:
            try: fcntl.flock(_LOCK_FD, fcntl.LOCK_UN)
            except OSError: pass
            try: os.close(_LOCK_FD)
            except OSError: pass
            _LOCK_FD = None
        LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass

def acquire_lock() -> bool:
    """Acquires a non-blocking lock with O_CLOEXEC and proper FD lifetime."""
    global _LOCK_FD
    try:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        STATE_BASE_DIR.mkdir(parents=True, exist_ok=True)
        LOG_BASE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    try:
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    except Exception as e:
        sys.stderr.write(f"\033[1;31m[ERROR]\033[0m Could not open lock file {LOCK_FILE}: {e}\n")
        return False

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _LOCK_FD = fd
        atexit.register(_cleanup_lock)
        return True
    except BlockingIOError:
        sys.stdout.write(f"\033[1;31m[ERROR]\033[0m Another instance is already running.\n")
        holders = get_lock_holders()
        if holders:
            sys.stdout.write(f"{holders}\n")
            try: os.close(fd)
            except OSError: pass
            return False
        else:
            sys.stdout.write("\033[1;33m[WARN]\033[0m No live lock holder identified. Attempting to acquire stale lock...\n")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                _LOCK_FD = fd
                atexit.register(_cleanup_lock)
                return True
            except Exception:
                sys.stdout.write("\033[1;31m[ERROR]\033[0m Failed to acquire lock.\n")
                try: os.close(fd)
                except OSError: pass
                return False
    except OSError as e:
        sys.stdout.write(f"\033[1;31m[ERROR]\033[0m Failed to acquire lock: {e}\n")
        try: os.close(fd)
        except OSError: pass
        return False

# ==============================================================================
# ASYNCHRONOUS PRIVILEGE & SUDO HEARTBEAT ENGINE
# ==============================================================================
class SudoEngine:
    """Manages non-blocking sudo timestamp maintenance during lengthy workflows."""

    @staticmethod
    def verify_sudo() -> bool:
        if not shutil.which("sudo"):
            sys.stdout.write("\033[1;31m[FATAL]\033[0m sudo is required but not installed.\n")
            return False

        sys.stdout.write("\033[1;36m[DUSKY PRE-FLIGHT]\033[0m Securing administrative privileges...\n")
        try:
            if subprocess.run(['sudo', '-n', 'true'], capture_output=True).returncode == 0:
                return True
        except Exception:
            pass

        if sys.stdin.isatty():
            try:
                subprocess.run(['sudo', '-v'], check=True)
                return True
            except subprocess.CalledProcessError:
                pass

        sys.stdout.write("\033[1;31m[FATAL]\033[0m Sudo authentication failed. Aborting.\n")
        return False

    @staticmethod
    async def maintain_sudo_heartbeat(error_callback=None) -> None:
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
                        if error_callback: 
                            error_callback("Sudo heartbeat timed out. Admin privileges may be lost.")
                        break
                    
                    if proc.returncode != 0:
                        if error_callback: 
                            error_callback(f"Sudo heartbeat failed (code {proc.returncode}). Admin privileges may be lost.")
                        break
                except Exception as e:
                    if error_callback: 
                        error_callback(f"Sudo heartbeat exception: {e}")
                    break
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            pass

# ==============================================================================
# THEME PARSING & GENERATION ENGINE - DRY
# ==============================================================================
def get_theme_path() -> Path:
    target_user = os.environ.get("TARGET_USER") or os.environ.get("SUDO_USER")
    base_dir = Path.home()
    if target_user:
        try:
            pw = pwd.getpwnam(target_user.strip())
            base_dir = Path(pw.pw_dir)
        except KeyError:
            pass
    generated = base_dir / ".config/matugen/generated/dusky_tui.json"
    if generated.exists():
        return generated
    generated_fresh = base_dir / ".config/matugen/generated_fresh/dusky_tui.json"
    if generated_fresh.exists():
        return generated_fresh
    return generated

def _build_css_from_palette(palette: dict[str, str]) -> str:
    bg, fg, accent = palette["bg"], palette["fg"], palette["accent"]
    warning, success, muted, error_c = palette["warning"], palette["success"], palette["muted"], palette["error"]
    return f"""
        Screen {{ background: {bg}; color: {fg}; layout: vertical; }}
        #top_header {{ height: 1; dock: top; background: {muted}; color: {accent}; text-style: bold; }}
        #top_header .title {{ width: 100%; text-align: center; }}
        #main_dashboard {{ layout: horizontal; height: 1fr; }}
        #left_pane {{ width: 35%; border-right: solid {muted}; background: {bg}; padding: 0 1; height: 100%; }}
        #right_pane {{ width: 65%; height: 100%; layout: vertical; background: {bg}; }}
        #telemetry_box {{ height: 5; border-bottom: solid {muted}; padding: 0 2; layout: vertical; }}
        #status_label {{ text-style: bold; color: {accent}; }}
        #speed_label {{ color: {warning}; text-style: italic; }}
        #progress_bar {{ width: 100%; margin-top: 1; height: 1; }}
        RichLog {{ height: 1fr; border: none; background: {bg}; color: {fg}; scrollbar-gutter: stable; }}
        Tree {{ background: {bg}; color: {fg}; }}
        #footer {{ height: 1; dock: bottom; background: {muted}; layout: horizontal; padding: 0 1; }}
        .footer-shortcut {{ padding: 0 1; color: {fg}; }}
        .footer-shortcut.-active {{ background: {accent}; color: {bg}; text-style: bold; }}
        .footer_sep {{ color: {warning}; }}
        #footer_status {{ color: {success}; text-style: italic; }}
        TaskSearchScreen, ConflictModalScreen, ManualModalScreen {{ align: center middle; background: rgba(0,0,0,0.8); }}
        #search_dialog {{ width: 60; height: 75%; background: {bg}; border: solid {accent}; padding: 1 2; }}
        #search_list {{ height: 1fr; border: none; background: {bg}; color: {fg}; }}
        #modal_dialog {{ width: 70; height: auto; border: heavy {error_c}; background: {bg}; padding: 1 2; }}
        #manual_dialog {{ width: 70; height: auto; border: heavy {accent}; background: {bg}; padding: 1 2; }}
        #modal_title {{ text-align: center; text-style: bold; color: {error_c}; margin-bottom: 1; }}
        #manual_title {{ text-align: center; text-style: bold; color: {accent}; margin-bottom: 1; }}
        #error_details {{ color: {warning}; margin-bottom: 1; max-height: 10; overflow-y: auto; }}
        #button_bar {{ layout: horizontal; align: center middle; height: 3; }}
        Button {{ height: 1; min-width: 16; border: none; margin: 0 1; padding: 0; }}
        Input {{ background: {bg}; border: tall {accent}; color: {fg}; }}
        """

def load_dusky_theme() -> str:
    default_palette = {
        "bg": "#0a1612", "fg": "#d8e6df", "accent": "#00e0b8",
        "warning": "#a0d0cb", "success": "#8dd2da", "muted": "#1a2e28", "error": "#ffb4ab",
    }
    theme_file = get_theme_path()
    if not theme_file.exists():
        return _build_css_from_palette(default_palette)
    try:
        data = json.loads(theme_file.read_text(encoding="utf-8"))
        def safe(c: str, fallback: str) -> str:
            if not isinstance(c, str): return fallback
            c = c.strip()
            return c if _HEX_COLOR_RE.match(c) else fallback

        palette = {k: safe(data.get(k), default_palette[k]) for k in default_palette}
        return _build_css_from_palette(palette)
    except Exception:
        return _build_css_from_palette(default_palette)

# ==============================================================================
# TOML PARSER & PROFILE RESOLUTION ENGINE
# ==============================================================================
def parse_task_entry(raw_entry: str) -> OrchestratorTask:
    parts = [p.strip() for p in raw_entry.split("|")]
    if len(parts) == 2:
        mode, cmd = parts
        flags = ""
    elif len(parts) == 3:
        mode, flags, cmd = parts
    else:
        raise ValueError(f"Malformed entry: {raw_entry}")

    ignore_fail = False
    interactive = False
    for flag in flags.split(","):
        f = flag.strip().lower()
        if f in ("true", "ignore", "ignore-fail"):
            ignore_fail = True
        elif f in ("interactive", "tui", "prompt"):
            interactive = True

    cmd_tokens = shlex.split(cmd)
    if not cmd_tokens:
        raise ValueError(f"Empty command in entry: {raw_entry}")

    if cmd_tokens[0] == "true" and len(cmd_tokens) > 1:
        ignore_fail = True
        cmd_tokens = cmd_tokens[1:]

    return OrchestratorTask(
        raw_entry=raw_entry,
        mode=mode.upper(),
        script_name=cmd_tokens[0],
        args=cmd_tokens[1:],
        ignore_fail=ignore_fail,
        interactive=interactive
    )

def load_profile(filepath: Path) -> ProfileConfig:
    with open(filepath, "rb") as f:
        data = tomllib.load(f)

    p_data = data.get("profile", {})
    g_data = data.get("git", {})
    s_data = data.get("search_dirs", {})
    c_data = data.get("conflict_resolutions", {})
    seq_data = data.get("sequence", {})

    tasks = []
    for line in seq_data.get("scripts", []):
        try:
            tasks.append(parse_task_entry(line))
        except ValueError as e:
            sys.stderr.write(f"Error parsing profile {filepath.name}: {e}\n")
            sys.exit(1)

    return ProfileConfig(
        filepath=filepath,
        name=p_data.get("name", filepath.stem),
        description=p_data.get("description", ""),
        post_script_delay=p_data.get("post_script_delay", 0),
        git_enabled=g_data.get("enabled", False),
        git_dir=g_data.get("git_dir", "~/dusky"),
        git_work_tree=g_data.get("work_tree", "~/"),
        git_remote=g_data.get("remote", "origin"),
        search_dirs=[str(resolve_home(d)) for d in s_data.get("dirs", [])],
        conflict_resolutions=c_data,
        tasks=tasks
    )

def discover_profiles() -> list[ProfileConfig]:
    if not PROFILES_DIR.exists():
        sys.stderr.write(f"\033[1;31m[FATAL]\033[0m Profiles directory missing: {PROFILES_DIR}\n")
        sys.exit(1)
    return [load_profile(f) for f in sorted(PROFILES_DIR.glob("*.toml"))]

def resolve_and_validate_manifest(profile: ProfileConfig) -> bool:
    success = True
    search_dir_cache: dict[str, bool] = {}
    for i, task in enumerate(profile.tasks):
        hash_input = f"{i}:{task.script_name}:{'-'.join(task.args)}".encode()
        task.state_key = hashlib.md5(hash_input, usedforsecurity=False).hexdigest()

        if "/" in task.script_name:
            cand = resolve_home(task.script_name)
            if cand.exists() and cand.is_file():
                task.resolved_path = cand
        else:
            if task.script_name in profile.conflict_resolutions:
                cand = resolve_home(profile.conflict_resolutions[task.script_name])
                if cand.exists() and cand.is_file():
                    task.resolved_path = cand
            else:
                matches: list[Path] = []
                for d in profile.search_dirs:
                    p = Path(d) / task.script_name
                    key = str(p)
                    exists = search_dir_cache.get(key)
                    if exists is None:
                        exists = p.is_file()
                        search_dir_cache[key] = exists
                    if exists:
                        matches.append(p)
                if len(matches) == 1:
                    task.resolved_path = matches[0]
                elif len(matches) > 1:
                    sys.stderr.write(f"\033[1;31m[CONFLICT]\033[0m Multiple versions of {task.script_name} found:\n")
                    for m in matches: sys.stderr.write(f"  - {m}\n")
                    success = False

        if not task.resolved_path:
            sys.stderr.write(f"\033[1;31m[MISSING]\033[0m Could not find {task.script_name} in search dirs.\n")
            success = False
            continue

        first_line = ""
        try:
            with open(task.resolved_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line_num in range(20):
                    line = f.readline()
                    if not line: break
                    if line_num == 0: first_line = line.strip()
                    if _INTERACTIVE_RE.search(line): task.interactive = True
        except OSError:
            pass

        has_py_ext = task.resolved_path.suffix == '.py'
        has_sh_ext = task.resolved_path.suffix == '.sh'
        has_py_shebang = "python" in first_line
        has_sh_shebang = any(x in first_line for x in ["bash", "sh", "zsh"])
        
        if (has_py_ext and has_sh_shebang) or (has_sh_ext and has_py_shebang):
            sys.stderr.write(f"\033[1;33m[CONTRADICTION]\033[0m {task.script_name} has conflicting shebang and extension.\n")
            success = False
            
        if has_py_ext or has_py_shebang:
            task.interpreter = "python"
        elif has_sh_ext or has_sh_shebang:
            task.interpreter = "bash"

    return success

# ==============================================================================
# ADVANCED GIT SELF-UPDATE ENGINE (Ported from bash updater logic)
# ==============================================================================
def run_git_self_update(profile: ProfileConfig) -> bool:
    if not profile.git_enabled:
        return False
        
    git_dir = resolve_home(profile.git_dir)
    work_tree = resolve_home(profile.git_work_tree)
    
    if not git_dir.exists():
        sys.stdout.write(f"\033[1;33m[WARN]\033[0m Git dir not found ({git_dir}). Skipping self-update.\n")
        return False

    # 1. Advanced stale lock file cleanup (/proc verified)
    locks_to_check = ['index.lock', 'config.lock', 'packed-refs.lock', 'shallow.lock', 'HEAD.lock', 'ORIG_HEAD.lock', 'FETCH_HEAD.lock']
    for lock_name in locks_to_check:
        lock_file = git_dir / lock_name
        if lock_file.exists():
            try:
                lock_real = lock_file.resolve()
                is_open = False
                for pid_dir in Path('/proc').iterdir():
                    if not pid_dir.name.isdigit(): continue
                    fd_dir = pid_dir / 'fd'
                    if not fd_dir.exists(): continue
                    try:
                        for fd_link in fd_dir.iterdir():
                            if fd_link.resolve() == lock_real:
                                is_open = True
                                break
                    except OSError: pass
                    if is_open: break
                
                if is_open:
                    sys.stdout.write(f"\033[1;31m[ERROR]\033[0m Git lock {lock_file} is open by a live process. Aborting to prevent DB corruption.\n")
                    return False
                
                age = time.time() - lock_file.stat().st_mtime
                if age > 60:
                    lock_file.unlink(missing_ok=True)
                    sys.stdout.write(f"\033[1;36m[GIT]\033[0m Cleared stale Git lock: {lock_name}\n")
                else:
                    sys.stdout.write(f"\033[1;31m[ERROR]\033[0m Git lock {lock_file} is too recent to safely auto-clear. Aborting.\n")
                    return False
            except OSError:
                pass
        
    base_cmd = ["git", f"--git-dir={git_dir}", f"--work-tree={work_tree}"]
    sys.stdout.write("\033[1;36m[GIT]\033[0m Fetching upstream updates...\n")
    
    # 2. Advanced fetch with network timeout & retry mechanics
    fetch_success = False
    for attempt in range(1, 6):
        try:
            subprocess.run(base_cmd + ["fetch", profile.git_remote], check=True, capture_output=True, timeout=60)
            fetch_success = True
            break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            if attempt < 5:
                sys.stdout.write(f"\033[1;33m[WARN]\033[0m Fetch attempt {attempt}/5 failed. Retrying in 2s...\n")
                time.sleep(2)
            else:
                sys.stdout.write(f"\033[1;31m[ERROR]\033[0m Git fetch failed after 5 attempts.\n")
    
    if not fetch_success:
        return False

    try:
        local_head = subprocess.check_output(base_cmd + ["rev-parse", "HEAD"], text=True).strip()
        remote_ref = f"{profile.git_remote}/main"
        try:
            remote_head = subprocess.check_output(base_cmd + ["rev-parse", remote_ref], text=True).strip()
        except subprocess.CalledProcessError:
            remote_ref = f"{profile.git_remote}/master"
            remote_head = subprocess.check_output(base_cmd + ["rev-parse", remote_ref], text=True).strip()
            
        if local_head == remote_head:
            sys.stdout.write("\033[1;32m[GIT]\033[0m Orchestrator is up to date.\n")
            return False
            
        # 3. Diverged History / Unrelated History Check (merge-base)
        try:
            merge_base = subprocess.check_output(base_cmd + ["merge-base", "HEAD", remote_head], text=True).strip()
        except subprocess.CalledProcessError:
            merge_base = ""

        if merge_base != local_head and local_head != remote_head:
            sys.stdout.write(f"\n\033[1;33m[DIVERGED HISTORY]\033[0m Local history diverges from upstream.\n")
            sys.stdout.write("  1) Abort (keep current state) [DEFAULT]\n")
            sys.stdout.write("  2) Reset to upstream [RECOMMENDED] (Local tweaks will be backed up/restored)\n")
            sys.stdout.write("Choice [1-2] (default: 1): ")
            sys.stdout.flush()
            
            if sys.stdin.isatty():
                r, _, _ = select.select([sys.stdin], [], [], 60)
                choice = "1"
                if r:
                    choice = sys.stdin.readline().strip()
                if choice != "2":
                    sys.stdout.write("Aborting update by user request.\n")
                    return False
            else:
                sys.stdout.write("\n\033[1;31m[ERROR]\033[0m Non-interactive mode and diverged history. Aborting to prevent data loss.\n")
                return False

        sys.stdout.write(f"\033[1;36m[GIT]\033[0m Updating from {local_head[:7]} to {remote_head[:7]}...\n")

        # Prepare unified backup directories
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_base = Path.home() / "Documents" / "dusky_backups"
        backup_base.mkdir(parents=True, exist_ok=True)
        
        # 4. Bare Repo Collision Detection (Untracked work tree safety map)
        collision_dir = backup_base / f"untracked_collisions_{timestamp}"
        try:
            tracked_out = subprocess.check_output(base_cmd + ['ls-files', '-z'], text=True)
            tracked_files = set(tracked_out.split('\0'))
            
            incoming_out = subprocess.check_output(base_cmd + ['ls-tree', '-r', '-z', '--name-only', remote_ref], text=True)
            incoming_files = set(incoming_out.split('\0'))
            
            collisions = []
            for inc in incoming_files:
                if not inc: continue
                target_file = work_tree / inc
                if target_file.exists() and inc not in tracked_files:
                    collisions.append(inc)
            
            if collisions:
                sys.stdout.write(f"\033[1;33m[WARN]\033[0m Found {len(collisions)} untracked work-tree collisions. Backing up before overwrite...\n")
                collision_dir.mkdir(parents=True, exist_ok=True)
                for coll in collisions:
                    src = work_tree / coll
                    dest = collision_dir / coll
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
        except subprocess.CalledProcessError:
            pass

        # 5. Detect and protect local modifications via atomic diff-index hashing
        changed_files = {}
        try:
            diff_output = subprocess.check_output(
                base_cmd + ["diff-index", "-z", "--raw", "--no-renames", "HEAD"], 
                text=True
            )
            if diff_output:
                parts = diff_output.split('\0')
                i = 0
                while i < len(parts) - 1:
                    meta = parts[i]
                    path = parts[i+1]
                    i += 2
                    if not meta: continue
                    meta_tokens = meta.split()
                    if len(meta_tokens) >= 4:
                        old_oid = meta_tokens[2]
                        status = meta_tokens[4][0] if len(meta_tokens) >= 5 else "?"
                        if status != 'D': 
                            changed_files[path] = old_oid
        except subprocess.CalledProcessError:
            pass

        # Hash before script update execution
        my_path = Path(__file__).resolve()
        try:
            h_before = hashlib.sha256(my_path.read_bytes()).hexdigest()
        except OSError:
            h_before = ""
            
        backup_dir = None
        needs_merge_dir = None
        
        if changed_files:
            backup_dir = backup_base / f"user_mods_{timestamp}"
            needs_merge_dir = backup_base / f"needs_merge_{timestamp}"
            backup_dir.mkdir(parents=True, exist_ok=True)

            sys.stdout.write(f"\033[1;33m[WARN]\033[0m Local changes detected. Backing up {len(changed_files)} files...\n")
            for path in changed_files:
                src = work_tree / path
                if src.exists() and src.is_file():
                    dest = backup_dir / path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)

        # 6. Perform strict overwrite (Hard Reset)
        sys.stdout.write("\033[1;36m[GIT]\033[0m Performing hard reset to match upstream...\n")
        subprocess.run(base_cmd + ["reset", "--hard", remote_head], check=True, capture_output=True)

        # 7. Restore Safe Modifications (Atomically) & Isolate Conflicts
        if changed_files and backup_dir:
            sys.stdout.write("\033[1;36m[GIT]\033[0m Processing local edits (restoring safe changes)...\n")
            restored = 0
            merged = 0
            
            for path, old_oid in changed_files.items():
                backup_file = backup_dir / path
                if not backup_file.exists():
                    continue

                try:
                    tree_out = subprocess.check_output(base_cmd + ["ls-tree", "HEAD", "--", path], text=True).strip()
                    new_oid = ""
                    if tree_out:
                        new_oid = tree_out.split()[2]
                except subprocess.CalledProcessError:
                    new_oid = ""

                target_file = work_tree / path

                if new_oid == old_oid or not new_oid:
                    # Upstream didn't change it. Safe to restore using POSIX atomic os.replace()
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    tmp_file = target_file.parent / f".{target_file.name}.dusky_tmp"
                    shutil.copy2(backup_file, tmp_file)
                    os.replace(tmp_file, target_file)
                    restored += 1
                else:
                    # Upstream changed it. Isolate to needs_merge dir to prevent data loss.
                    needs_merge_dir.mkdir(parents=True, exist_ok=True)
                    conflict_dest = needs_merge_dir / path
                    conflict_dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup_file, conflict_dest)
                    merged += 1

            if restored > 0:
                sys.stdout.write(f"\033[1;32m[GIT]\033[0m Restored {restored} safe edits.\n")
            if merged > 0:
                sys.stdout.write(f"\033[1;33m[WARN]\033[0m {merged} files had upstream conflicts. Your versions are saved in: {needs_merge_dir}\n")
            
            shutil.rmtree(backup_dir, ignore_errors=True)
            
        # 8. Hash after to verify if script itself was altered
        try:
            h_after = hashlib.sha256(my_path.read_bytes()).hexdigest()
        except OSError:
            h_after = ""

        if h_before != h_after and h_after != "":
            sys.stdout.write("\033[1;32m[GIT]\033[0m Orchestrator script updated! Restarting process...\n")
            os.execv(sys.executable, [sys.executable] + sys.argv)
            return True
        else:
            sys.stdout.write("\033[1;32m[GIT]\033[0m Git updated, but orchestrator script was unchanged. Continuing...\n")

    except subprocess.CalledProcessError as e:
        sys.stdout.write(f"\033[1;33m[WARN]\033[0m Git operation failed: {e}\n")
    return False

# ==============================================================================
# UI HELPERS & INTERACTIVE MODALS
# ==============================================================================
def _get_status_badge_static(status: TaskStatus) -> str:
    match status:
        case TaskStatus.COMPLETED: return "[green]✔[/]"
        case TaskStatus.RUNNING: return "[yellow]◐[/]"
        case TaskStatus.FAILED: return "[red]✘[/]"
        case TaskStatus.SKIPPED: return "[dim]○[/]"
        case _: return "[dim]○[/]"

class TaskSearchScreen(ModalScreen[str | None]):
    """Fuzzy search modal for task navigation."""
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Dismiss"),
        Binding("ctrl+n", "cursor_down", "Down"),
        Binding("ctrl+p", "cursor_up", "Up"),
    ]

    def __init__(self, tasks: list[OrchestratorTask]):
        super().__init__()
        self.tasks = tasks
        self.results: list[str] = []

    def compose(self) -> ComposeResult:
        with Container(id="search_dialog"):
            yield Static("◈ Fuzzy Task Search (type to filter, Enter to jump)", id="search_title")
            yield Input(placeholder="Search tasks... (e.g. 'core nvim')", id="search_input")
            yield OptionList(id="search_list")

    def on_mount(self) -> None:
        self.query_one("#search_input", Input).focus()
        self._update_results("")

    def on_input_changed(self, event: Input.Changed) -> None:
        self._update_results(event.value)

    def _update_results(self, query: str) -> None:
        ol = self.query_one(OptionList)
        ol.clear_options()
        self.results.clear()

        query_lower = query.lower().strip()
        query_no_space = query_lower.replace(" ", "")

        if not query_lower:
            scored = [(0, t) for t in self.tasks[:200]]
        else:
            scored_results: list[tuple[int, OrchestratorTask]] = []
            for item in self.tasks:
                target = item.script_name.lower()
                score = 0
                if query_lower == target: score += 100
                elif target.startswith(query_lower): score += 50
                elif query_lower in target: score += 30

                if query_no_space and query_no_space in target.replace(" ", "").replace("-", "").replace("_", ""):
                    score += 20

                s_idx = q_idx = 0
                match_positions: list[int] = []
                while s_idx < len(target) and q_idx < len(query_no_space):
                    if target[s_idx] == query_no_space[q_idx]:
                        match_positions.append(s_idx)
                        q_idx += 1
                    s_idx += 1

                if q_idx == len(query_no_space) and query_no_space:
                    if len(match_positions) > 1:
                        spread = (match_positions[-1] - match_positions[0]) - (len(match_positions) - 1)
                        score += max(0, 15 - spread)
                    else: score += 15
                    score += 5

                if score > 0: scored_results.append((score, item))
            scored_results.sort(key=lambda x: (-x[0], x[1].script_name))
            scored = scored_results

        options_to_add: list[Option] = []
        for _, item in scored[:200]:
            txt = Text()
            badge = _get_status_badge_static(item.status)
            txt.append_text(Text.from_markup(f"{badge} "))
            txt.append(f"[{item.mode}] ", style="warning bold")
            txt.append(item.script_name, style="bold white" if item.status != TaskStatus.COMPLETED else "green")
            options_to_add.append(Option(txt, id=item.state_key))
            self.results.append(item.state_key)

        ol.add_options(options_to_add)

    @on(OptionList.OptionSelected)
    def on_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option and event.option.id: self.dismiss(str(event.option.id))
        elif event.option_index is not None and event.option_index < len(self.results): self.dismiss(self.results[event.option_index])

    @on(Input.Submitted)
    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        ol = self.query_one(OptionList)
        if ol.highlighted is not None and ol.highlighted < len(self.results): self.dismiss(self.results[ol.highlighted])
        elif self.results: self.dismiss(self.results[0])

    def action_cursor_down(self) -> None: self.query_one(OptionList).action_cursor_down()
    def action_cursor_up(self) -> None: self.query_one(OptionList).action_cursor_up()
    def action_dismiss_modal(self) -> None: self.dismiss(None)


class ConflictModalScreen(ModalScreen[str]):
    """Modal screen displayed when a task script encounters an execution fault."""
    def __init__(self, script_name: str, error_msg: str):
        super().__init__()
        self.script_name = script_name
        self.error_msg = error_msg

    def compose(self) -> ComposeResult:
        with Container(id="modal_dialog"):
            yield Static(f"⚠️ EXECUTION FAULT: {self.script_name}", id="modal_title")
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
        match getattr(event, "key", "").lower():
            case "r": self.dismiss("retry")
            case "m": self.dismiss("manual")
            case "s": self.dismiss("skip")
            case "a" | "escape" | "q": self.dismiss("abort")
            case _: pass

class ManualModalScreen(ModalScreen[str]):
    """Modal screen displayed when the user flags the execution pipeline for manual approval."""
    def __init__(self, script_name: str):
        super().__init__()
        self.script_name = script_name

    def compose(self) -> ComposeResult:
        with Container(id="manual_dialog"):
            yield Static(f"⏸ MANUAL OVERRIDE: {self.script_name}", id="manual_title")
            with Horizontal(id="button_bar"):
                yield Button("Proceed [Y]", variant="success", id="btn_yes")
                yield Button("Skip [S]", variant="warning", id="btn_skip")
                yield Button("Quit [Q]", variant="error", id="btn_quit")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "btn_yes": self.dismiss("yes")
            case "btn_skip": self.dismiss("skip")
            case _: self.dismiss("quit")

    def on_key(self, event) -> None:
        match getattr(event, "key", "").lower():
            case "y": self.dismiss("yes")
            case "s": self.dismiss("skip")
            case "q" | "escape": self.dismiss("quit")
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
        if not self.is_mounted: return
        if self._blink_timer is not None: self._blink_timer.stop()
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
        yield Label("Orchestrator Engine: Active", id="footer_status")

# ==============================================================================
# TEXTUAL TUI SELECTOR & ORCHESTRATOR FRONT-END
# ==============================================================================
class ProfileSelectorApp(App):
    """Interactive startup modal for discovering and choosing target manifests."""
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen { align: center middle; background: #0a1612; color: #d8e6df; }
    #selector_container { width: 80; height: auto; border: heavy #00e0b8; background: #0f221d; padding: 1 2; }
    #title { text-align: center; text-style: bold; color: #00e0b8; margin-bottom: 1; }
    OptionList { height: auto; border: none; background: #0f221d; color: #d8e6df; }
    .help_text { text-align: center; color: #a0d0cb; text-style: italic; margin-top: 1; }
    """

    def __init__(self, profiles: list[ProfileConfig]):
        super().__init__()
        self.profiles = profiles
        self.selected_profile: ProfileConfig | None = None

    def compose(self) -> ComposeResult:
        with Container(id="selector_container"):
            yield Static("◈ DUSKY ARCH MASTER ORCHESTRATOR v16.2.3", id="title")
            options = []
            for i, p in enumerate(self.profiles):
                prefix = "❯ " if i == 0 else "  "
                options.append(Option(f"{prefix}{i+1}. {p.name:<25} {p.description}", id=str(i)))
            yield OptionList(*options, id="profiles_list")
            yield Static("Press Enter to select. Esc to quit.", classes="help_text")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.selected_profile = self.profiles[int(event.option_id)]
        self.exit(0)
        
    def on_key(self, event) -> None:
        if event.key == "escape": self.exit(1)


class DuskyOrchestratorApp(App):
    """The unified Textual TUI managing async PTY streams, sudo heartbeats, and telemetry."""
    ENABLE_COMMAND_PALETTE = False

    BINDINGS = [
        Binding("ctrl+f", "open_search", "Search Tasks", priority=True),
    ]

    def __init__(self, profile: ProfileConfig, has_sudo: bool, manual: bool, stop_on_fail: bool, force: bool):
        super().__init__()
        self.profile = profile
        self.tasks = profile.tasks
        self.has_sudo = has_sudo
        self.manual = manual
        self.stop_on_fail = stop_on_fail
        self.force_flag = force
        self.active_child_pid = None
        self.current_pty_master = None
        self.active_task = None
        self.sudo_task = None
        self.CSS = load_dusky_theme()

        # Reverted: Files isolated strictly to ~/Documents using backwards compatible string replacement
        self.state_file = STATE_BASE_DIR / f".install_state_{self.profile.name.replace(' ', '_')}"
        
        self.completed_keys: set[str] = set()
        if self.state_file.exists():
            try:
                self.completed_keys = set(self.state_file.read_text(encoding="utf-8", errors="ignore").splitlines())
            except OSError:
                self.completed_keys = set()

        self.tree_widget = Tree("◈ Execution Sequence")
        self.log_widget = RichLog(id="pty_log", highlight=True, markup=True, wrap=True)
        self.progress_bar = ProgressBar(show_eta=False, show_percentage=False, id="progress_bar")
        self.status_label = Label("Initializing orchestrator sequence...", id="status_label")
        self.speed_label = Label("Status: Pre-flight check | ETA: --:--", id="speed_label")

        self.tree_nodes_map: dict[str, TreeNode] = {}
        self.task_index: dict[str, list[OrchestratorTask]] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="top_header"):
            yield Static(f"◈ DUSKY ORCHESTRATOR v16.2.3  [{self.profile.name}]", classes="title")
        with Horizontal(id="main_dashboard"):
            with Vertical(id="left_pane"):
                yield self.tree_widget
            with Vertical(id="right_pane"):
                with Container(id="telemetry_box"):
                    yield self.status_label
                    yield self.speed_label
                    yield self.progress_bar
                with ContentSwitcher(id="log_switcher"):
                    yield self.log_widget
                    for task in self.tasks:
                        yield RichLog(id=f"log_{task.state_key}", highlight=True, markup=True, wrap=True)
        yield AppFooter(id="footer")

    def on_mount(self) -> None:
        try:
            self.query_one("#log_switcher", ContentSwitcher).current = "pty_log"
        except Exception:
            pass
        self.progress_bar.total = max(1, len(self.tasks))
        self.build_task_tree()
        self.log_system("Environment pre-flight validated. Asynchronous PTY engine online.")
        
        for t in self.tasks:
            if t.state_key in self.completed_keys:
                self.update_task_node_by_key(t.state_key, TaskStatus.COMPLETED)
                self.progress_bar.advance(1)
                try:
                    task_log = self.query_one(f"#log_{t.state_key}", RichLog)
                    task_log.write(Text("Task completed in a previous run. Live logs not available in this session.", style="italic dim"))
                except Exception:
                    pass
                
        self.run_execution_pipeline()

    @on(Tree.NodeSelected)
    def on_node_selected(self, event: Tree.NodeSelected) -> None:
        node = event.node
        switcher = self.query_one("#log_switcher", ContentSwitcher)
        if node == self.tree_widget.root:
            switcher.current = "pty_log"
        elif node.data and isinstance(node.data, OrchestratorTask):
            switcher.current = f"log_{node.data.state_key}"

    def action_open_search(self) -> None:
        if isinstance(self.screen, ModalScreen): return
        try: self.query_one("#sc_search", Shortcut).blink()
        except Exception: pass

        def on_search_selected(state_key: str | None) -> None:
            if not state_key: return
            if node := self.tree_nodes_map.get(state_key):
                self.tree_widget.select_node(node)
                self.tree_widget.scroll_to_node(node)
                for t in self.tasks:
                    if t.state_key == state_key:
                        self.log_system(f"Fuzzy Finder navigated to: {t.script_name}")
                        break

        self.push_screen(TaskSearchScreen(self.tasks), on_search_selected)

    def action_quit_orchestrator(self) -> None:
        try: self.query_one("#sc_quit", Shortcut).blink()
        except Exception: pass
        self.log_system("Abort signal received. Terminating pipeline...", is_err=True)
        self.exit(1)

    def on_key(self, event) -> None:
        """Dynamically intercepts keystrokes to either route them into the active PTY or fire TUI shortcuts."""
        if self.current_pty_master is not None:
            try:
                if event.is_printable:
                    os.write(self.current_pty_master, event.character.encode())
                elif event.key == "enter":
                    os.write(self.current_pty_master, b"\r")
                elif event.key == "backspace":
                    os.write(self.current_pty_master, b"\x08")
                elif event.key == "ctrl+c":
                    # Reverted: Safely pass the SIGINT equivalent instead of killing the main application
                    os.write(self.current_pty_master, b"\x03")
            except OSError:
                pass
        else:
            if event.key in ("q", "ctrl+c"): self.action_quit_orchestrator()
            elif event.key == "/": self.action_open_search()

    def build_task_tree(self) -> None:
        """Populates Left Pane hierarchy with sequence steps and status counters O(1)."""
        self.tree_widget.root.expand()
        for task in self.tasks:
            self.task_index.setdefault(task.script_name, []).append(task)
            badge = _get_status_badge_static(task.status)
            node = self.tree_widget.root.add_leaf(f"{badge} [{task.mode}] {task.script_name}")
            node.data = task
            self.tree_nodes_map[task.state_key] = node

    def update_task_node_by_key(self, state_key: str, status: TaskStatus) -> None:
        if node := self.tree_nodes_map.get(state_key):
            for t in self.tasks:
                if t.state_key == state_key:
                    t.status = status
                    badge = _get_status_badge_static(status)
                    node.label = Text.from_markup(f"{badge} [{t.mode}] {t.script_name}")
                    if status == TaskStatus.RUNNING:
                        try:
                            self.tree_widget.select_node(node)
                            self.tree_widget.scroll_to_node(node)
                            self.query_one("#log_switcher", ContentSwitcher).current = f"log_{state_key}"
                        except Exception:
                            pass
                    break

    def log_system(self, msg: str, is_err: bool = False) -> None:
        prefix = "[bold red][SYSTEM][/]" if is_err else "[bold cyan][SYSTEM][/]"
        text = Text.from_markup(f"{prefix} {msg}")
        self.log_widget.write(text)
        if self.active_task:
            try:
                task_log = self.query_one(f"#log_{self.active_task.state_key}", RichLog)
                task_log.write(text)
            except Exception:
                pass

    def handle_pty_line(self, line: str) -> None:
        # Reverted: We keep lines mostly intact for logging, but extract clean versions for telemetry processing
        clean = line.strip('\r\n')
        if not clean: return
        
        # Parse for telemetry (non-destructive)
        stripped_for_telemetry = ANSI_STRIP_REGEX.sub("", clean) if "\x1b" in clean else clean
        
        extracted_pct = extracted_speed = extracted_eta = None
        if "%" in stripped_for_telemetry:
            if pct_match := PCT_REGEX.search(stripped_for_telemetry):
                extracted_pct = pct_match.group(0)

        if "B/s" in stripped_for_telemetry or "b/s" in stripped_for_telemetry.lower():
            if total_match := SPEED_ETA_REGEX.search(stripped_for_telemetry):
                extracted_speed, extracted_eta = total_match.group(1), total_match.group(2)
            elif dl_match := ALT_SPEED_ETA_REGEX.search(stripped_for_telemetry):
                extracted_speed, extracted_eta = dl_match.group(1), dl_match.group(2)

        if extracted_pct: self.status_label.update(f"⚡ Processing Task Sub-step... ({extracted_pct})")
        if extracted_speed and extracted_eta: self.speed_label.update(f"Throughput: {extracted_speed} | ETA: {extracted_eta}")

        # Standard log output (using Rich's native Ansi parser to render colors and strip raw ESC chars)
        display_line = clean
        if not display_line.strip(): return
        
        lower = display_line.lower()
        if "\x1b" not in display_line and any(k in lower for k in ("error", "failed", "warning", "conflict", "exists in filesystem")):
            text = Text(display_line, style="bold red")
        else:
            text = Text.from_ansi(display_line)

        self.log_widget.write(text)
        if self.active_task:
            try:
                task_log = self.query_one(f"#log_{self.active_task.state_key}", RichLog)
                task_log.write(text)
            except Exception:
                pass

    @staticmethod
    def _set_pty_size(fd: int) -> None:
        try:
            size = os.get_terminal_size()
            winsize = struct.pack("HHHH", size.lines, size.columns, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except (OSError, ValueError):
            try:
                winsize = struct.pack("HHHH", FALLBACK_ROWS, FALLBACK_COLS, 0, 0)
                fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
            except OSError:
                pass

    async def execute_pty_command(self, cmd: list[str]) -> bool:
        master_fd, slave_fd = pty.openpty()
        self.current_pty_master = master_fd
        self._set_pty_size(slave_fd)

        transport: asyncio.Transport | None = None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                start_new_session=True,
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
                        for line in BRACKET_NEWLINE_RE.split(line_buffer):
                            if line: self.handle_pty_line(line)
                    break

                try: text = decoder.decode(chunk)
                except Exception: text = chunk.decode("utf-8", errors="replace")

                line_buffer += text
                while True:
                    m = SINGLE_NEWLINE_RE.search(line_buffer)
                    if not m: break
                    idx = m.start()
                    line = line_buffer[:idx]
                    line_buffer = line_buffer[idx + 1 :]
                    if line: self.handle_pty_line(line)

            return (await proc.wait()) == 0

        except asyncio.CancelledError:
            if self.active_child_pid:
                try: os.killpg(self.active_child_pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    try: os.kill(self.active_child_pid, signal.SIGTERM)
                    except ProcessLookupError: pass
                
                try: await asyncio.wait_for(proc.wait(), timeout=1.5)
                except (TimeoutError, asyncio.TimeoutError):
                    try: os.killpg(self.active_child_pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        try: os.kill(self.active_child_pid, signal.SIGKILL)
                        except ProcessLookupError: pass
                    try: await asyncio.wait_for(proc.wait(), timeout=0.5)
                    except Exception: pass
                except Exception: pass
            raise
        except Exception as e:
            self.log_system(f"PTY Execution Exception: {e}", is_err=True)
            return False

        finally:
            self.current_pty_master = None
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

    async def _execute_task_cmd(self, task: OrchestratorTask, cmd: list[str]) -> bool:
        if task.interactive:
            self.log_system(f"Suspending TUI for interactive workflow: {task.script_name}...")
            with self.suspend():
                sys.stdout.flush()
                sys.stderr.flush()
                old_attr = None
                try: old_attr = termios.tcgetattr(sys.stdin.fileno())
                except termios.error: pass
                try:
                    subprocess.run(["clear"], check=False)
                    print(f"\n--- INTERACTIVE WORKFLOW: {task.script_name} ---")
                    print(f"Executing: {shlex.join(cmd)}\n")
                    proc = await asyncio.create_subprocess_exec(*cmd)
                    success = (await proc.wait()) == 0
                finally:
                    if old_attr:
                        try: termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_attr)
                        except termios.error: pass
            await asyncio.sleep(0.5)
            return success
        return await self.execute_pty_command(cmd)

    def _commit_task_state(self, task: OrchestratorTask) -> None:
        self.completed_keys.add(task.state_key)
        try:
            with open(self.state_file, "a", encoding="utf-8") as f:
                f.write(task.state_key + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            self.log_system(f"Failed to persist state for {task.script_name}: {e}", is_err=True)

    @work(name="execution_pipeline", exclusive=True)
    async def run_execution_pipeline(self) -> None:
        if self.has_sudo:
            self.sudo_task = asyncio.create_task(
                SudoEngine.maintain_sudo_heartbeat(
                    error_callback=lambda msg: self.log_system(msg, is_err=True)
                )
            )

        try:
            for task in self.tasks:
                if task.state_key in self.completed_keys:
                    continue

                if not task.resolved_path:
                    self.update_task_node_by_key(task.state_key, TaskStatus.FAILED)
                    self.log_system(f"Missing file: {task.script_name}", is_err=True)
                    if self.stop_on_fail:
                        self.log_system("stop-on-fail active. Aborting pipeline.", is_err=True)
                        self.exit(1)
                        return
                    
                    action = await self.push_screen_wait(
                        ConflictModalScreen(task.script_name, "File missing from disk. Target could not be resolved.")
                    )
                    
                    if action == "abort":
                        self.log_system("User aborted execution sequence.", is_err=True)
                        self.exit(1)
                        return
                    else:
                        self.update_task_node_by_key(task.state_key, TaskStatus.SKIPPED)
                        self.progress_bar.advance(1)
                        self.log_system(f"Skipped missing task: {task.script_name}", is_err=True)
                        continue

                if self.manual:
                    self.status_label.update(f"⏸ Pending Manual Approval: {task.script_name}")
                    action = await self.push_screen_wait(ManualModalScreen(task.script_name))
                    if action == "skip":
                        self.update_task_node_by_key(task.state_key, TaskStatus.SKIPPED)
                        self.progress_bar.advance(1)
                        self.log_system(f"Manual override: Skipped task {task.script_name}", is_err=True)
                        continue
                    elif action == "quit":
                        self.log_system("Manual override: Aborting pipeline.", is_err=True)
                        self.exit(1)
                        return

                self.active_task = task
                self.update_task_node_by_key(task.state_key, TaskStatus.RUNNING)
                self.status_label.update(f"Executing: {task.script_name} [{task.mode}]")
                self.log_system(f"\n>>> PROCESS INITIATED: {task.script_name}")

                args = list(task.args)
                if self.force_flag and "--force" not in args:
                    args.append("--force")
                    
                cmd = [task.interpreter, str(task.resolved_path)] + args
                if task.mode == "S":
                    cmd = ["sudo"] + cmd

                success = await self._execute_task_cmd(task, cmd)
                task_resolved = False

                while not success and not task_resolved:
                    if task.ignore_fail:
                        self.log_system(f"Task failed with non-zero exit code but marked ignore-fail. Continuing: {task.script_name}")
                        success = True
                        break

                    self.update_task_node_by_key(task.state_key, TaskStatus.FAILED)
                    if self.stop_on_fail:
                        self.log_system("stop-on-fail active. Aborting pipeline.", is_err=True)
                        self.exit(1)
                        return

                    action = await self.push_screen_wait(
                        ConflictModalScreen(task.script_name, "Sub-process exited with non-zero status code. Check log pane.")
                    )
                    match action:
                        case "retry":
                            self.log_system(f"Retrying task: {task.script_name}...")
                            self.update_task_node_by_key(task.state_key, TaskStatus.RUNNING)
                            success = await self._execute_task_cmd(task, cmd)
                        case "manual":
                            try: self.query_one("#sc_manual", Shortcut).blink()
                            except Exception: pass
                            self.log_system(f"Suspending TUI for manual intervention on {task.script_name}...")
                            with self.suspend():
                                sys.stdout.flush()
                                sys.stderr.flush()
                                old_attr = None
                                try: old_attr = termios.tcgetattr(sys.stdin.fileno())
                                except termios.error: pass
                                try:
                                    subprocess.run(["clear"], check=False)
                                    print(f"\n--- MANUAL INTERVENTION TTY: {task.script_name} ---")
                                    print(f"Executing: {shlex.join(cmd)}\n")
                                    proc = await asyncio.create_subprocess_exec(*cmd)
                                    await proc.wait()
                                finally:
                                    if old_attr:
                                        try: termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_attr)
                                        except termios.error: pass
                            await asyncio.sleep(0.5)
                            self.update_task_node_by_key(task.state_key, TaskStatus.COMPLETED)
                            self.progress_bar.advance(1)
                            self._commit_task_state(task)
                            task_resolved = True
                            break
                        case "skip":
                            try: self.query_one("#sc_skip", Shortcut).blink()
                            except Exception: pass
                            self.update_task_node_by_key(task.state_key, TaskStatus.SKIPPED)
                            self.progress_bar.advance(1)
                            self.log_system(f"Skipped task: {task.script_name}", is_err=True)
                            task_resolved = True
                            break
                        case "abort" | _:
                            self.log_system("User aborted execution sequence.", is_err=True)
                            self.exit(1)
                            return

                if success and not task_resolved:
                    self.update_task_node_by_key(task.state_key, TaskStatus.COMPLETED)
                    self.progress_bar.advance(1)
                    self._commit_task_state(task)
                    self.log_system(f"Successfully completed: {task.script_name}")
                    if self.profile.post_script_delay > 0:
                        await asyncio.sleep(self.profile.post_script_delay)
                self.active_task = None

            self.status_label.update("✨ All orchestrator sequences completed successfully!")
            self.speed_label.update("Status: Idle | ETA: 00:00")
            try: self.query_one("#footer_status", Label).update("Orchestrator Engine: Complete")
            except Exception: pass
            self.log_system("Execution sequence finished. All system targets resolved.")
            AudioNotifier.play("complete")

        finally:
            self.active_task = None
            if self.sudo_task:
                self.sudo_task.cancel()
                try: await self.sudo_task
                except asyncio.CancelledError: pass

# ==============================================================================
# MAIN ENTRYPOINT & CLI PARSING
# ==============================================================================
def parse_command_line() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dusky Arch Linux Orchestrator (v16.2.3 Ultimate Master Suite)",
        epilog="Example: ./orchestrator.py --profile 01_core",
    )
    parser.add_argument("--profile", help="Execute specific profile (name or filename stem)")
    parser.add_argument("--list", action="store_true", help="List all available profiles and exit")
    parser.add_argument("--list-scripts", action="store_true", help="List sequence of selected profile and exit")
    parser.add_argument("--reset", action="store_true", help="Reset the state file for the selected profile")
    parser.add_argument("--dry-run", action="store_true", help="Validate everything but do not execute any scripts")
    parser.add_argument("--force", action="store_true", help="Pass --force flag to all executed subscripts")
    parser.add_argument("--manual", "-m", action="store_true", help="Prompt before executing every single script")
    parser.add_argument("--stop-on-fail", action="store_true", help="Halt execution immediately if a script fails")
    return parser.parse_args()

def main() -> None:
    args = parse_command_line()
    profiles = discover_profiles()
    if not profiles:
        Console(stderr=True).print("[bold yellow]:: No profiles found in profiles/ directory.[/bold yellow]")
        sys.exit(1)
        
    selected_profile: ProfileConfig | None = None
    if args.list:
        for p in profiles:
            print(f"- {p.filepath.stem}: {p.name} ({p.description})")
        sys.exit(0)
        
    if args.profile:
        for p in profiles:
            if p.name == args.profile or p.filepath.stem == args.profile:
                selected_profile = p
                break
        if not selected_profile:
            Console(stderr=True).print(f"[bold red]Profile '{args.profile}' not found.[/bold red]")
            sys.exit(1)
    else:
        selector = ProfileSelectorApp(profiles)
        selector.run()
        selected_profile = selector.selected_profile
        
    if not selected_profile:
        sys.exit(1)
        
    if args.reset:
        safe_name = re.sub(r'[^A-Za-z0-9._-]', '_', selected_profile.name)
        sf = STATE_BASE_DIR / f".install_state_{safe_name}"
        if sf.exists():
            try:
                sf.unlink()
                print(f"Reset state for {selected_profile.name} at {sf}")
            except Exception as e:
                sys.stderr.write(f"Failed to reset state {sf}: {e}\n")
        
    if args.list_scripts:
        print(f"Sequence for {selected_profile.name}:")
        for i, t in enumerate(selected_profile.tasks):
            print(f"{i+1:3d}. [{t.mode}] {t.script_name} {' '.join(t.args)}")
        sys.exit(0)

    if not args.dry_run and run_git_self_update(selected_profile):
        sys.exit(0)
        
    if not acquire_lock():
        sys.exit(1)
        
    if not resolve_and_validate_manifest(selected_profile):
        Console(stderr=True).print("[bold red]Manifest validation failed.[/bold red]")
        sys.exit(1)
        
    if args.dry_run:
        print("Dry-run complete. Everything is valid.")
        sys.exit(0)
        
    has_sudo = any(t.mode == 'S' for t in selected_profile.tasks)
    if has_sudo and not SudoEngine.verify_sudo():
        sys.exit(1)
        
    try:
        app = DuskyOrchestratorApp(
            profile=selected_profile,
            has_sudo=has_sudo,
            manual=args.manual,
            stop_on_fail=args.stop_on_fail,
            force=args.force
        )
        app.run()
    except KeyboardInterrupt:
        Console(stderr=True).print("\n[bold red]:: Interrupted by user.[/]")
        sys.exit(130)

if __name__ == "__main__":
    main()
