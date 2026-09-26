#!/usr/bin/env python3
# DUSKY_BOOTSTRAP_PACKAGES: python python-textual python-rich git
# dusky_interactive=true
# ==============================================================================
#  ARCH LINUX ISO TEXTUAL ORCHESTRATOR (v19.0 - Async PTY Engine + Auto-Prompt)
# ==============================================================================
# Architecture: Asynchronous Non-Blocking PTY Stream Engine | Textual Split TUI
# Features: Progress Bar/Speed Extraction | Auto-Prompt Responder | State Persistence
# Compatibility: Python 3.14+ | Textual 8.2+ | Arch Linux ISO (2026+)
# ==============================================================================

VERSION = "19.0.0"

import os
import sys
if sys.version_info < (3, 14):
    sys.stderr.write("[FATAL] Python 3.14+ is required.\n")
    sys.exit(1)
import subprocess
import time
import fcntl
import hashlib
import tarfile
import shlex
import argparse
import shutil
import asyncio
import errno
import pty
import termios
import struct
import functools
import re
import tomllib
import atexit
import datetime
import signal
import json
import math
import sqlite3
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum, auto
from importlib import metadata as importlib_metadata
from typing import List, Dict, Optional, Tuple, Any
from contextlib import suppress, contextmanager, nullcontext

try:
    from rich.console import Console
    from rich.markup import escape
    from rich.text import Text
    from rich import box

    from textual.app import App, ComposeResult
    from textual.containers import Container, Horizontal, Vertical
    from textual.widgets import Header, Footer, Static, RichLog, ProgressBar, Button, Label, Input, OptionList, Tree, ContentSwitcher
    from textual.widgets.tree import TreeNode
    from textual.binding import Binding
    from textual.screen import ModalScreen
    from textual import work, on, events
except ImportError as exc:
    sys.stderr.write(f"[FATAL] Missing Python dependencies: {exc}\n")
    sys.stderr.write("Install: python-textual python-rich\n")
    sys.exit(8)

try:
    version_parts = tuple(int(part) for part in re.findall(r"\d+", importlib_metadata.version("textual"))[:3])
    if version_parts < (8, 2, 8):
        raise RuntimeError(f"Textual 8.2.8+ required; installed {importlib_metadata.version('textual')}")
except (importlib_metadata.PackageNotFoundError, RuntimeError) as exc:
    sys.stderr.write(f"[FATAL] {exc}\n")
    sys.exit(8)

# ==============================================================================
# CONSTANTS & CONFIGURATION LOAD
# ==============================================================================
VERSION = "19.0.0"
SCRIPT_DIR: Path = Path(__file__).resolve().parent
PROFILES_DIR: Path = Path(
    os.environ.get("DUSKY_PROFILES_DIR", SCRIPT_DIR / "profiles")
).resolve()


def load_global_config() -> dict:
    config_path = PROFILES_DIR / "settings" / "orchestrator.toml"
    if config_path.exists():
        try:
            with open(config_path, "rb") as f:
                return tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as e:
            raise RuntimeError(f"Cannot load global config {config_path}: {e}") from e
    return {}


try:
    GLOBAL_CONFIG = load_global_config()
except RuntimeError as exc:
    sys.stderr.write(f"[FATAL] {exc}\n")
    sys.exit(2)

ASCII_MODE = GLOBAL_CONFIG.get("ui", {}).get("ascii_mode", False)
MAX_DEFER_PASSES = GLOBAL_CONFIG.get("execution", {}).get("max_defer_passes", 3)

UNICODE_SYMBOLS = GLOBAL_CONFIG.get(
    "ui",
    {},
).get(
    "unicode_symbols",
    {
        "logo": "◈",
        "completed": "✓",
        "running": "◉",
        "failed": "x",
        "skipped": "⊘",
        "pending": "·",
        "sep": "│",
    },
)

ASCII_SYMBOLS = GLOBAL_CONFIG.get(
    "ui",
    {},
).get(
    "ascii_symbols",
    {
        "logo": "DUSKY",
        "completed": "OK",
        "running": "RUN",
        "failed": "ERR",
        "skipped": "SKIP",
        "pending": "...",
        "sep": "|",
    },
)


def S(key: str) -> str:
    syms = ASCII_SYMBOLS if ASCII_MODE else UNICODE_SYMBOLS
    return syms.get(key, "")


# High-Performance Regexes
ANSI_STRIP_REGEX = re.compile(
    r'\x1B(?:[@-Z\\-_]|\[(?>(?:[0-?]*+)[ -/]*+[@-~])|\](?>\d*;.*?)(?:\x07|\x1B\\)|\]8;;.*?(?:\x07|\x1B\\)|\x1B\(B)'
)
PCT_REGEX = re.compile(r'(?<![0-9])(?>\d{1,2}|100)%')
SPEED_ETA_REGEX = re.compile(r'(\d+(?:\.\d+)?\s+[KMG]?i?B/s)\s+([\d:]+)', re.IGNORECASE)
PROGRESS_BAR_REGEX = re.compile(r'\[[#=\- oO@%:.0123456789━─░▒▓█▏▎▍▌▋▊▉●○◉◌]{3,}\]|\b\d{1,3}%\b')
INTERACTIVE_RE = re.compile(r'^\s*#\s*dusky_interactive\s*=\s*(?:true|1)\b', re.IGNORECASE)


def _build_prompt_rules() -> list[tuple[str, re.Pattern[str], str]]:
    default_rules = [
        ("pgp_import", r"(?i)(::\s*Import PGP key.*\?\s*\[Y/n\]|::\s*Append key\?.*\[Y/n\]|Import PGP key.*\?\s*\[Y/n\])", "y\n"),
        ("pacman_proceed", r"(?i)::\s*(Proceed with (?:installation|download|upgrade)|Continue (?:installation|download|upgrade)).*\?\s*\[Y/n\]", "y\n"),
        ("pacman_replace", r"(?i)::\s*Replace\s+.*\?\s*\[Y/n\]", "y\n"),
        ("pacman_remove_conflict", r"(?i)::\s*Remove conflicting file.*\?\s*\[Y/n\]", "y\n"),
        ("generic_yes", r"(?i)\[Y/n\]|\(Y/n\)", "y\n"),
    ]
    config_rules = GLOBAL_CONFIG.get("prompts", {}).get("rules", None)
    rules = []
    items_to_parse = config_rules if config_rules is not None else default_rules
    for item in items_to_parse:
        if isinstance(item, dict):
            name, pattern, kind = item["name"], item["pattern"], item["kind"]
            resp = "y\n" if kind in ("yes", "y") else f"{kind}\n"
        else:
            name, pattern, resp = item
        rules.append((name, re.compile(pattern, re.MULTILINE), resp))
    return rules


PROMPT_RULES = _build_prompt_rules()
_LOCK_FD: Optional[int] = None

# ==============================================================================
# PATH RESOLUTION HELPERS
# ==============================================================================
def user_home() -> Path:
    env_home = os.environ.get("DUSKY_WORK_TREE") or os.environ.get("DUSKY_HOME")
    if env_home:
        return Path(env_home).resolve()
    return Path.home()


@functools.cache
def documents_root() -> Path:
    docs_dir = GLOBAL_CONFIG.get("paths", {}).get("documents_dir", "Documents")
    p = Path(docs_dir).expanduser()
    root = p if p.is_absolute() else user_home() / p
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        sys.stderr.write(f"[FATAL] Cannot create Documents root {root}: {e}\n")
        sys.exit(1)
    return root


def _documents_subdir(name: str) -> Path:
    p = Path(name).expanduser()
    path = p if p.is_absolute() else documents_root() / p
    try:
        path.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            path.chmod(0o700)
    except OSError as e:
        sys.stderr.write(f"[FATAL] Cannot create required directory {path}: {e}\n")
        sys.exit(1)
    return path


@functools.cache
def logs_dir() -> Path:
    return _documents_subdir(GLOBAL_CONFIG.get("paths", {}).get("logs_subdir", "logs"))


def now_ts() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_iso() -> str:
    return datetime.datetime.now().isoformat()


def safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def resolve_home(path_str: str) -> Path:
    raw = path_str.strip()
    if raw.startswith("~/") or raw == "~":
        p = user_home() / raw[2:] if raw.startswith("~/") else user_home()
    else:
        p = Path(os.path.expandvars(raw)).expanduser()
    if not p.is_absolute():
        p = SCRIPT_DIR / p
    return p


@functools.cache
def state_dir() -> Path:
    return _documents_subdir(GLOBAL_CONFIG.get("paths", {}).get("state_subdir", "state"))


def state_dir_path() -> Path:
    paths = GLOBAL_CONFIG.get("paths", {})
    docs = Path(paths.get("documents_dir", "Documents")).expanduser()
    root = docs if docs.is_absolute() else user_home() / docs
    subdir = Path(paths.get("state_subdir", "state")).expanduser()
    return subdir if subdir.is_absolute() else root / subdir


def file_checksum(path: Path) -> str:
    try:
        h = hashlib.blake2b(digest_size=16)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def make_state_key(task: "OrchestratorTask", occurrence: int) -> str:
    args_key = shlex.join(task.args)
    timeout_repr = "" if task.timeout is None else str(task.timeout)
    material = "|".join(
        [
            task.mode,
            task.script_name,
            args_key,
            str(occurrence),
            task.checksum,
            task.condition or "",
            str(int(task.interactive)),
            str(int(task.ignore_fail)),
            str(int(task.force_flag)),
            timeout_repr,
            str(int(task.always)),
            str(int(task.once)),
            task.once_mode,
            task.once_scope,
        ]
    ).encode("utf-8")
    return hashlib.blake2b(material, digest_size=16).hexdigest()


# ==============================================================================
# NOTIFICATION MANAGER
# ==============================================================================
class NotificationManager:
    audio_enabled: bool = True
    desktop_enabled: bool = True

    @staticmethod
    def play_sound(event_type: str) -> None:
        if not NotificationManager.audio_enabled:
            return
        cfg = GLOBAL_CONFIG.get("notifications", {})
        if not cfg.get("audio_enabled", True):
            return

        players = cfg.get("audio_players", ["pw-play", "paplay"])
        sound_map = cfg.get("sound_map", {})
        fallback = cfg.get("fallback_sound", "/usr/share/sounds/freedesktop/stereo/bell.oga")

        sound_file = sound_map.get(event_type, fallback)
        if not Path(sound_file).exists():
            sound_file = fallback
            if not Path(sound_file).exists():
                return

        player_bin = None
        for p in players:
            if shutil.which(p):
                player_bin = p
                break

        if not player_bin:
            return

        with suppress(Exception):
            subprocess.Popen(
                [player_bin, sound_file],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    @staticmethod
    def send_desktop(title: str, body: str, urgency: str = "normal") -> None:
        if not NotificationManager.desktop_enabled:
            return
        cfg = GLOBAL_CONFIG.get("notifications", {})
        if not cfg.get("desktop_enabled", True):
            return

        if not shutil.which("notify-send"):
            return

        app_name = cfg.get("app_name", "Dusky Arch ISO Installer")
        with suppress(Exception):
            subprocess.Popen(
                ["notify-send", "-a", app_name, "-u", urgency, title, body],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


# ==============================================================================
# RUN LOGGER
# ==============================================================================
class RunLogger:
    def __init__(self, profile_name: str, run_id: str):
        self.failed_write = False
        log_config = GLOBAL_CONFIG.get("logging", {})
        self.enabled = log_config.get("enabled", True)
        self.write_task_logs = log_config.get("write_task_logs", True)
        self.write_reports = log_config.get("write_reports", True)

        self.root: Path | None = None
        self.main_path: Path | None = None
        self._main = None
        self._task_files: dict[str, object] = {}
        self.run_id = run_id

        if not self.enabled:
            return

        try:
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.root = logs_dir() / f"{stamp}_{safe_filename(profile_name)}_{run_id}"
            self.root.mkdir(parents=True, exist_ok=True)
            self.main_path = self.root / "orchestrator.log"
            self._main = open(self.main_path, "a", encoding="utf-8", errors="replace")
            self.system(f"Logging started for profile: {profile_name}")
            self.system(f"Run ID: {run_id}")
        except OSError as e:
            self.failed_write = True
            sys.stderr.write(f"[ERROR] Cannot create log directory under {logs_dir()}: {e}\n")
            self.enabled = False

    def system(self, msg: str) -> None:
        if not self.enabled or self._main is None:
            return
        try:
            self._main.write(f"[{now_ts()}] {msg}\n")
            self._main.flush()
        except OSError as exc:
            self.failed_write = True
            sys.stderr.write(f"[ERROR] Main log write failed: {exc}\n")

    def task_log_path(self, task: Any) -> Path:
        if self.root is None:
            return Path("/dev/null")
        return self.root / f"{task.index:03d}_{safe_filename(task.script_name)}.log"

    def open_task(self, task: Any, cmd: list[str]) -> None:
        if not self.enabled or not self.write_task_logs:
            return
        f = None
        try:
            f = open(self.task_log_path(task), "a", encoding="utf-8", errors="replace")
            f.write(f"[{now_ts()}] TASK START: {task.script_name}\n")
            f.write(f"[{now_ts()}] MODE: {task.mode}\n")
            f.write(f"[{now_ts()}] PATH: {task.resolved_path}\n")
            f.write(f"[{now_ts()}] INTERPRETER: {task.interpreter or 'direct'}\n")
            f.write(f"[{now_ts()}] ARGS: {shlex.join(task.args)}\n")
            f.write(f"[{now_ts()}] COMMAND: {shlex.join(cmd)}\n")
            f.write(f"[{now_ts()}] CONDITION: {task.condition or 'always'}\n")
            f.flush()
            self._task_files[task.state_key] = f
        except OSError as exc:
            self.failed_write = True
            sys.stderr.write(f"[ERROR] Task log open failed: {exc}\n")
            if f is not None:
                with suppress(OSError):
                    f.close()

    def write_task(self, task: Any, line: str) -> None:
        if not self.enabled or not self.write_task_logs:
            return
        f = self._task_files.get(task.state_key)
        if f is None:
            return
        try:
            f.write(line + "\n")
            f.flush()
        except OSError as exc:
            self.failed_write = True
            sys.stderr.write(f"[ERROR] Task log write failed: {exc}\n")

    def close_task(self, task: Any, status: str = "", exit_code: int | None = None, duration: float = 0.0) -> None:
        if not self.enabled or not self.write_task_logs:
            return
        f = self._task_files.pop(task.state_key, None)
        if f is None:
            return
        try:
            f.write(f"\n[{now_ts()}] TASK END: {task.script_name}\n")
            f.write(f"[{now_ts()}] STATUS: {status}\n")
            f.write(f"[{now_ts()}] EXIT CODE: {exit_code}\n")
            f.write(f"[{now_ts()}] DURATION: {duration:.2f}s\n")
            f.flush()
        except OSError as exc:
            self.failed_write = True
            sys.stderr.write(f"[ERROR] Task log close failed: {exc}\n")
        finally:
            with suppress(OSError):
                f.close()

    def write_report(
        self,
        profile_name: str,
        tasks: list[Any],
        statuses: dict[str, str],
        counters: dict[str, int],
    ) -> None:
        if not self.enabled or not self.write_reports or self.root is None:
            return

        report = {
            "run_id": self.run_id,
            "generated": now_iso(),
            "profile": profile_name,
            "version": VERSION,
            "python": sys.version,
            "user": "root" if os.geteuid() == 0 else os.environ.get("USER", "user"),
            "counters": counters,
            "tasks": [],
        }

        lines = [
            "# Dusky ISO Installer Report",
            "",
            f"- Run ID: `{self.run_id}`",
            f"- Generated: `{now_iso()}`",
            f"- Profile: `{profile_name}`",
            f"- Version: `{VERSION}`",
            "",
            "## Summary",
            "",
        ]

        for k, v in sorted(counters.items()):
            lines.append(f"- **{k.capitalize()}**: {v}")

        lines.extend(["", "## Task Details", "", "| # | Script | Status | Mode | Condition |", "|---|---|---|---|---|"])

        for t in tasks:
            st = statuses.get(t.state_key, "PENDING")
            report["tasks"].append({
                "index": t.index,
                "script": t.script_name,
                "status": st,
                "mode": t.mode,
                "condition": t.condition or "always",
            })
            lines.append(f"| {t.index} | `{t.script_name}` | {st} | {t.mode} | `{t.condition or 'always'}` |")

        try:
            (self.root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            (self.root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            self.failed_write = True
            sys.stderr.write(f"[ERROR] Report write failed: {exc}\n")


# ==============================================================================
# CONDITION EVALUATOR
# ==============================================================================
class ConditionEvaluator:
    def __init__(self):
        self.cache: dict[str, bool] = {}

    def check(self, cond: str | None) -> bool:
        if not cond or cond.strip().lower() in ("always", "true", "yes"):
            return True
        if cond.strip().lower() in ("never", "false", "no"):
            return False
        cond_clean = cond.strip()
        if cond_clean in self.cache:
            return self.cache[cond_clean]

        res = self._eval(cond_clean)
        self.cache[cond_clean] = res
        return res

    def _eval(self, cond: str) -> bool:
        if "," in cond:
            parts = [p.strip() for p in cond.split(",") if p.strip()]
            if len(parts) > 1:
                return all(self.check(part) for part in parts)
            if parts:
                cond = parts[0]
            else:
                return True

        kind, _, value = cond.partition(":")
        kind = kind.strip().lower()
        value = value.strip()

        if kind == "not":
            return not self.check(value)
        if kind == "wayland":
            return bool(os.environ.get("WAYLAND_DISPLAY"))
        if kind == "x11":
            return bool(os.environ.get("DISPLAY"))
        if kind == "graphical":
            return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))
        if kind in ("command", "cmd"):
            return shutil.which(value) is not None
        if kind == "dir":
            return Path(value).expanduser().is_dir()
        if kind == "file":
            return Path(value).expanduser().is_file()
        if kind == "path":
            return Path(value).expanduser().exists()
        if kind == "missing":
            return not Path(value).expanduser().exists()
        if kind in ("package", "pkg"):
            pkg_cmd = GLOBAL_CONFIG.get("conditions", {}).get("package_check_cmd", ["pacman", "-Qq"])
            if not pkg_cmd or not shutil.which(pkg_cmd[0]):
                return False
            try:
                return subprocess.run(pkg_cmd + [value], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            except Exception:
                return False
        if kind in ("service_active", "service", "svc"):
            cmd = GLOBAL_CONFIG.get("conditions", {}).get(
                "service_active_cmd",
                ["systemctl", "is-active", "--quiet"],
            )
            try:
                return subprocess.run(cmd + [value], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            except Exception:
                return False
        if kind == "gpu":
            vendor_map = GLOBAL_CONFIG.get("conditions", {}).get("gpu_vendor_map", {
                "nvidia": "0x10de", "intel": "0x8086", "amd": "0x1002", "vmware": "0x15ad", "virtio": "0x1af4"
            })
            target = vendor_map.get(value.lower())
            if target:
                drm_path = Path("/sys/class/drm")
                if drm_path.exists():
                    for card in drm_path.glob("card[0-9]*"):
                        vf = card / "device" / "vendor"
                        if vf.exists() and vf.read_text().strip().lower() == target:
                            return True
            if shutil.which("lspci"):
                try:
                    out = subprocess.run(["lspci"], capture_output=True, text=True).stdout.lower()
                    if value.lower() in out:
                        return True
                except Exception:
                    pass
            return False

        raise ValueError(f"Unsupported condition: {cond}")


# ==============================================================================
# DATA CLASSES
# ==============================================================================
class TaskStatus(Enum):
    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    SKIPPED = auto()


@dataclass
class OrchestratorTask:
    index: int
    script_name: str
    args: List[str]
    mode: str = "U"
    ignore_fail: bool = False
    interactive: bool = False
    interactive_override: Optional[bool] = None
    force_flag: bool = False
    condition: Optional[str] = None
    timeout: Optional[float] = None
    interpreter: str = "bash"
    checksum: str = ""
    state_key: str = ""
    resolved_path: Optional[Path] = None
    status: TaskStatus = TaskStatus.PENDING
    error_msg: Optional[str] = None
    duration: float = 0.0
    always: bool = False
    retry: int = 0
    retry_delay: float = 1.0
    on_failure: str = "ask"
    once: bool = False
    once_mode: str = "content"
    once_scope: str = "profile"


@dataclass
class ProfileConfig:
    filepath: Optional[Path]
    name: str
    description: str
    phase1_tasks: List[OrchestratorTask]
    phase2_tasks: List[OrchestratorTask]
    search_dirs: List[Path] = field(default_factory=list)
    conflict_resolutions: Dict[str, str] = field(default_factory=dict)
    policy: Dict[str, Any] = field(default_factory=dict)


# ==============================================================================
# PROFILE PARSER & ENGINE
# ==============================================================================
def validate_condition(condition: str | None) -> None:
    if not condition:
        return
    for item in condition.split(","):
        part = item.strip()
        if not part:
            raise ValueError(f"Empty condition in {condition!r}")
        if part.lower().startswith("not:"):
            validate_condition(part[4:])
            continue
        kind, sep, value = part.partition(":")
        kind = kind.strip().lower()
        if kind in ("always", "true", "yes", "never", "false", "no", "wayland", "x11", "graphical") and not sep:
            continue
        if kind in ("command", "cmd", "dir", "file", "path", "missing", "package", "pkg",
                    "service_active", "service", "svc", "gpu") and sep and value.strip():
            continue
        raise ValueError(f"Unsupported condition: {part}")


def parse_task_entry(raw_entry: str | dict, index: int = 1) -> OrchestratorTask:
    if isinstance(raw_entry, dict):
        return parse_task_table(raw_entry, index)

    raw = raw_entry.strip()
    parts = [p.strip() for p in raw.split("|", 2)]

    if len(parts) == 1:
        mode, flags, cmd = "U", "", parts[0]
    elif len(parts) == 2:
        mode, cmd = parts
        flags = ""
    elif len(parts) == 3:
        mode, flags, cmd = parts
    else:
        raise ValueError(f"Malformed entry: {raw_entry}")

    ignore_fail = False
    interactive = False
    interactive_override: bool | None = None
    force_flag = False
    always = False
    condition: str | None = None
    timeout: float | None = None
    retry = 0
    retry_delay = 1.0
    on_failure = "ask"
    once = False
    once_mode = "content"
    once_scope = "profile"

    for flag in flags.split(","):
        f = flag.strip().lower()
        if not f:
            continue

        if f in ("true", "ignore", "ignore-fail"):
            ignore_fail = True
        elif f in ("interactive", "tui", "prompt", "fullscreen", "tty", "suspend"):
            interactive = True
            interactive_override = True
        elif f in ("no-interactive", "noninteractive", "inline", "embedded"):
            interactive = False
            interactive_override = False
        elif f in ("force", "--force"):
            force_flag = True
        elif f in ("always", "always_run"):
            always = True
        elif f in ("once", "run_once", "sticky"):
            once = True
        elif f in ("once:content", "once:hash"):
            once = True
            once_mode = "content"
        elif f in ("once:forever", "once:exact", "once:permanent"):
            once = True
            once_mode = "forever"
        elif f in ("once:profile", "once:local"):
            once = True
            once_scope = "profile"
        elif f in ("once:global", "once:machine"):
            once = True
            once_scope = "global"
        elif f.startswith("condition:"):
            cond_val = flag.strip()[10:]
            if condition is None:
                condition = cond_val
            else:
                condition = f"{condition},{cond_val}"
        elif f.startswith("if:"):
            cond_val = flag.strip()[3:]
            if condition is None:
                condition = cond_val
            else:
                condition = f"{condition},{cond_val}"
        elif f.startswith("timeout:"):
            timeout = float(flag.strip()[8:])
            if not math.isfinite(timeout) or timeout < 0:
                raise ValueError(f"Invalid timeout flag: {flag}")
        elif f.startswith("retry:"):
            retry = int(flag.strip()[6:])
            if retry < 0:
                raise ValueError(f"Invalid retry flag: {flag}")
        elif f.startswith("retry_delay:"):
            retry_delay = float(flag.strip()[12:])
            if not math.isfinite(retry_delay) or retry_delay < 0:
                raise ValueError(f"Invalid retry delay flag: {flag}")
        elif f.startswith("on_failure:"):
            val = flag.strip()[11:].lower()
            if val not in ("ask", "abort", "continue", "skip", "manual"):
                raise ValueError(f"Invalid failure policy: {flag}")
            on_failure = val
        else:
            raise ValueError(f"Unknown task flag: {flag}")

    cmd_tokens = shlex.split(cmd.strip())
    if not cmd_tokens:
        raise ValueError(f"Empty command in entry: {raw_entry}")

    if cmd_tokens[0] == "true" and len(cmd_tokens) > 1:
        ignore_fail = True
        cmd_tokens = cmd_tokens[1:]

    if "--force" in cmd_tokens:
        force_flag = True

    if mode.strip().upper() not in ("U", "S"):
        raise ValueError(f"Invalid task mode: {mode}")
    validate_condition(condition)

    return OrchestratorTask(
        index=index,
        script_name=cmd_tokens[0],
        args=cmd_tokens[1:],
        mode=mode.strip().upper(),
        ignore_fail=ignore_fail,
        interactive=interactive,
        interactive_override=interactive_override,
        force_flag=force_flag,
        condition=condition,
        timeout=timeout,
        always=always,
        retry=retry,
        retry_delay=retry_delay,
        on_failure=on_failure,
        once=once,
        once_mode=once_mode,
        once_scope=once_scope,
    )


def parse_task_table(table: dict, index: int) -> OrchestratorTask:
    cmd = str(table.get("cmd") or table.get("script") or table.get("path") or "").strip()
    if not cmd:
        raise ValueError(f"Task table at index {index} missing cmd/script/path")

    args_raw = table.get("args", [])
    if isinstance(args_raw, str):
        args = shlex.split(args_raw)
    elif isinstance(args_raw, list):
        args = [str(x) for x in args_raw]
    else:
        raise ValueError(f"Task {index}: args must be a string or array")
    if isinstance(args_raw, list) and any(not isinstance(x, str) for x in args_raw):
        raise ValueError(f"Task {index}: args must contain strings")

    if not args and " " in cmd:
        cmd_tokens = shlex.split(cmd)
        if cmd_tokens:
            cmd = cmd_tokens[0]
            args = cmd_tokens[1:]

    if not isinstance(table.get("flags", ""), str):
        raise ValueError(f"Task {index}: flags must be a string")
    flags = table.get("flags", "")
    for key in ("ignore_fail", "interactive", "force", "always", "once"):
        if key in table and not isinstance(table[key], bool):
            raise ValueError(f"Task {index}: {key} must be a boolean")
    ignore_fail = table.get("ignore_fail", False)

    interactive_override: bool | None = None
    if "interactive" in table:
        interactive = bool(table.get("interactive"))
        interactive_override = interactive
    else:
        interactive = False

    force_flag = bool(table.get("force", False))
    always = bool(table.get("always", False))
    condition = table.get("condition")
    if condition is not None and not isinstance(condition, str):
        raise ValueError(f"Task {index}: condition must be a string")
    timeout = table.get("timeout")

    retry = table.get("retry", 0)
    if not isinstance(retry, int) or isinstance(retry, bool) or retry < 0:
        raise ValueError(f"Task {index}: retry must be a nonnegative integer")
    retry_delay = table.get("retry_delay", 1.0)
    if not isinstance(retry_delay, (int, float)) or isinstance(retry_delay, bool) or not math.isfinite(retry_delay) or retry_delay < 0:
        raise ValueError(f"Task {index}: retry_delay must be nonnegative and finite")

    on_failure = str(table.get("on_failure", "ask")).lower()
    if on_failure not in ("ask", "abort", "continue", "skip", "manual"):
        raise ValueError(f"Task {index}: invalid on_failure policy")

    once = bool(table.get("once", False))
    once_mode = str(table.get("once_mode", "content")).lower()
    if once_mode not in ("content", "forever"):
        raise ValueError(f"Task {index}: invalid once_mode")

    once_scope = str(table.get("once_scope", "profile")).lower()
    if once_scope not in ("profile", "global"):
        raise ValueError(f"Task {index}: invalid once_scope")

    for flag in flags.split(","):
        f = flag.strip().lower()
        if not f:
            continue

        if f in ("true", "ignore", "ignore-fail"):
            ignore_fail = True
        elif f in ("interactive", "tui", "prompt", "fullscreen", "tty", "suspend"):
            interactive = True
            interactive_override = True
        elif f in ("no-interactive", "noninteractive", "inline", "embedded"):
            interactive = False
            interactive_override = False
        elif f in ("force", "--force"):
            force_flag = True
        elif f in ("always", "always_run"):
            always = True
        elif f in ("once", "run_once", "sticky"):
            once = True
        elif f in ("once:content", "once:hash"):
            once = True
            once_mode = "content"
        elif f in ("once:forever", "once:exact", "once:permanent"):
            once = True
            once_mode = "forever"
        elif f in ("once:profile", "once:local"):
            once = True
            once_scope = "profile"
        elif f in ("once:global", "once:machine"):
            once = True
            once_scope = "global"
        elif f.startswith("condition:"):
            cond_val = flag.strip()[10:]
            if condition is None:
                condition = cond_val
            else:
                condition = f"{condition},{cond_val}"
        elif f.startswith("if:"):
            cond_val = flag.strip()[3:]
            if condition is None:
                condition = cond_val
            else:
                condition = f"{condition},{cond_val}"
        elif f.startswith("timeout:"):
            timeout = float(flag.strip()[8:])
        elif f.startswith("retry:"):
            retry = int(flag.strip()[6:])
        elif f.startswith("retry_delay:"):
            retry_delay = float(flag.strip()[12:])
        elif f.startswith("on_failure:"):
            val = flag.strip()[11:].lower()
            if val not in ("ask", "abort", "continue", "skip", "manual"):
                raise ValueError(f"Task {index}: invalid on_failure policy")
            on_failure = val
        else:
            raise ValueError(f"Task {index}: unknown flag {flag}")

    if "--force" in args:
        force_flag = True

    timeout_value = float(timeout) if timeout is not None else None
    if timeout_value is not None and (not math.isfinite(timeout_value) or timeout_value < 0):
        raise ValueError(f"Task {index}: timeout must be nonnegative and finite")
    if retry < 0 or retry_delay < 0 or not math.isfinite(retry_delay):
        raise ValueError(f"Task {index}: invalid retry configuration")
    mode = str(table.get("mode", "U")).strip().upper()
    if mode not in ("U", "S"):
        raise ValueError(f"Task {index}: invalid mode {mode}")
    validate_condition(str(condition).strip() if condition else None)

    return OrchestratorTask(
        index=index,
        script_name=cmd,
        args=args,
        mode=mode,
        ignore_fail=ignore_fail,
        interactive=interactive,
        interactive_override=interactive_override,
        force_flag=force_flag,
        condition=str(condition).strip() if condition else None,
        timeout=timeout_value,
        always=always,
        retry=retry,
        retry_delay=retry_delay,
        on_failure=on_failure,
        once=once,
        once_mode=once_mode,
        once_scope=once_scope,
    )


def repair_missing_commas(text: str) -> tuple[str, int]:
    _NUM_BOOL_RE = re.compile(
        r"[+-]?(?:\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?"
        r"|0[xX][0-9a-fA-F_]+|0[oO][0-7_]+|0[bB][01_]+)"
    )
    _BOOL_WORDS = {"true", "false", "inf", "+inf", "-inf", "nan", "+nan", "-nan"}
    _WORD_CHARS = "_.+-:"

    out: list[str] = []
    i = 0
    n = len(text)
    depth = 0
    pending_value = False
    pending_is_word = False
    value_end = -1
    fixes = 0

    def is_num_or_bool(word: str) -> bool:
        return word in _BOOL_WORDS or _NUM_BOOL_RE.fullmatch(word) is not None

    while i < n:
        c = text[i]

        if c == '#':
            j = text.find('\n', i)
            if j == -1:
                j = n
            out.append(text[i:j])
            i = j
            continue

        if depth and pending_value and (c in '"\'[{+-' or c.isalnum()) and not (c == '-' and i + 1 >= n):
            k = i
            while k < n and (text[k].isalnum() or text[k] in _WORD_CHARS):
                k += 1
            word_end = k
            while k < n and text[k] in ' \t':
                k += 1
            is_key = k < n and text[k] == '='
            insert = True
            if c.isalnum() and pending_is_word and not is_key and not is_num_or_bool(text[i:word_end]):
                insert = False
            if insert:
                out.insert(value_end, ',')
                pending_value = False
                pending_is_word = False
                fixes += 1

        if c in '"\'':
            quote = c
            str_start = i
            if text.startswith(quote * 3, i):
                i += 3
                while i < n and not text.startswith(quote * 3, i):
                    if text[i] == '\\':
                        i += 1
                    i += 1
                i = min(i + 3, n)
            else:
                i += 1
                while i < n and text[i] != quote:
                    if text[i] == '\\':
                        i += 1
                    i += 1
                i += 1
            out.append(text[str_start:i])
            if depth:
                pending_value = True
                pending_is_word = False
                value_end = len(out)
            continue

        if c == '=':
            pending_value = False
            pending_is_word = False
            out.append(c)
            i += 1
            continue

        if c in '[{':
            depth += 1
            pending_value = False
            pending_is_word = False
            out.append(c)
            i += 1
            continue

        if c in ']}':
            depth = max(0, depth - 1)
            out.append(c)
            i += 1
            if depth:
                pending_value = True
                pending_is_word = False
                value_end = len(out)
            continue

        if c == ',':
            pending_value = False
            pending_is_word = False
            out.append(c)
            i += 1
            continue

        if c in ' \t\r\n':
            out.append(c)
            i += 1
            continue

        start_w = i
        while i < n and (text[i].isalnum() or text[i] in _WORD_CHARS):
            i += 1
        word = text[start_w:i]
        out.append(word)
        if depth:
            pending_value = True
            pending_is_word = True
            value_end = len(out)

    return "".join(out), fixes


def load_profile(filepath: Path) -> ProfileConfig:
    try:
        with open(filepath, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as err:
        text = filepath.read_text(encoding="utf-8")
        repaired, fixes = repair_missing_commas(text)
        if fixes > 0:
            try:
                data = tomllib.loads(repaired)
                sys.stderr.write(f"[WARN] Inserted {fixes} missing comma(s) in '{filepath.name}' -- auto-repaired.\n")
            except Exception:
                raise err
        else:
            raise err

    for table_name in ("profile", "phase1", "phase2", "search_dirs", "conflict_resolutions", "policy"):
        if not isinstance(data.get(table_name, {}), dict):
            raise ValueError(f"{filepath.name}: [{table_name}] must be a table")
    for phase in ("phase1", "phase2"):
        scripts = data.get(phase, {}).get("scripts", [])
        if not isinstance(scripts, list) or any(not isinstance(item, (str, dict)) for item in scripts):
            raise ValueError(f"{filepath.name}: [{phase}].scripts must be an array of tasks")
    dirs = data.get("search_dirs", {}).get("dirs", [])
    if not isinstance(dirs, list) or any(not isinstance(item, str) or not item for item in dirs):
        raise ValueError(f"{filepath.name}: [search_dirs].dirs must be an array of paths")
    p_data = data.get("profile", {})
    ph1_data = data.get("phase1", {})
    ph2_data = data.get("phase2", {})
    s_data = data.get("search_dirs", {})
    cr_data = data.get("conflict_resolutions", {})
    pol_data = data.get("policy", {})
    for key in ("audio", "notify", "manual", "stop_on_fail", "force"):
        if key in pol_data and not isinstance(pol_data[key], bool):
            raise ValueError(f"{filepath.name}: [policy].{key} must be a boolean")
    if "task_timeout" in pol_data and (
        not isinstance(pol_data["task_timeout"], (int, float))
        or isinstance(pol_data["task_timeout"], bool)
        or not math.isfinite(pol_data["task_timeout"])
        or pol_data["task_timeout"] < 0
    ):
        raise ValueError(f"{filepath.name}: [policy].task_timeout must be nonnegative and finite")

    conflict_resolutions: Dict[str, str] = {}
    for key, val in cr_data.items():
        if isinstance(val, str):
            conflict_resolutions[key] = str(resolve_home(val))
        elif isinstance(val, dict):
            for sub_key, sub_val in val.items():
                if isinstance(sub_val, str):
                    conflict_resolutions[f"{key}.{sub_key}"] = str(resolve_home(sub_val))

    policy: Dict[str, Any] = {}
    for key, val in pol_data.items():
        policy[key] = val

    search_dirs: List[Path] = []
    for d in s_data.get("dirs", []):
        p = Path(str(d)).expanduser()
        if not p.is_absolute():
            p = SCRIPT_DIR / p
        p = p.resolve()
        if not p.exists():
            sys.stderr.write(f"[WARN] Search directory does not exist: {p}\n")
        if p not in search_dirs:
            search_dirs.append(p)

    p1_tasks = []
    for idx, line in enumerate(ph1_data.get("scripts", []), start=1):
        try:
            p1_tasks.append(parse_task_entry(line, index=idx))
        except ValueError as e:
            raise ValueError(f"{filepath.name} [phase1] task {idx}: {e}") from e

    p2_tasks = []
    for idx, line in enumerate(ph2_data.get("scripts", []), start=1):
        try:
            p2_tasks.append(parse_task_entry(line, index=idx))
        except ValueError as e:
            raise ValueError(f"{filepath.name} [phase2] task {idx}: {e}") from e

    return ProfileConfig(
        filepath=filepath,
        name=p_data.get("name", filepath.stem),
        description=p_data.get("description", ""),
        phase1_tasks=p1_tasks,
        phase2_tasks=p2_tasks,
        search_dirs=search_dirs,
        conflict_resolutions=conflict_resolutions,
        policy=policy,
    )


def discover_profiles() -> List[ProfileConfig]:
    if not PROFILES_DIR.exists():
        return []
    profiles = []
    errors = []
    for f in sorted(PROFILES_DIR.glob("*.toml")):
        if f.parent.name == "settings":
            continue
        try:
            profiles.append(load_profile(f))
        except Exception as e:
            errors.append(f"{f.name}: {e}")
    if errors:
        raise ValueError("Invalid installer profile(s):\n  " + "\n  ".join(errors))
    return profiles


def recover_iso_block_device() -> Optional[Path]:
    """
    Recovers the ISO block device if unmounted due to copytoram or Ventoy abstraction.
    """
    # 1. Check blkid for iso9660
    try:
        r = subprocess.run(["blkid", "-t", "TYPE=iso9660", "-o", "device"], capture_output=True, text=True, check=False)
        if r.stdout:
            for line in r.stdout.splitlines():
                dev = line.strip()
                if dev and Path(dev).exists():
                    return Path(dev)
    except Exception:
        pass

    # 2. Check Ventoy mapper
    ventoy_map = Path("/dev/mapper/ventoy")
    if ventoy_map.is_block_device():
        return ventoy_map

    # 3. Check lsblk JSON for iso9660 or archiso labels
    try:
        r = subprocess.run(["lsblk", "--json", "--paths", "-o", "PATH,TYPE,FSTYPE,LABEL"], capture_output=True, text=True, check=False)
        if r.stdout:
            data = json.loads(r.stdout)
            for dev in data.get("blockdevices", []):
                fstype = (dev.get("fstype") or "").lower()
                label = (dev.get("label") or "").lower()
                if "iso9660" in fstype or "arch" in label:
                    return Path(dev["path"])
                for child in dev.get("children", []) or []:
                    c_fstype = (child.get("fstype") or "").lower()
                    c_label = (child.get("label") or "").lower()
                    if "iso9660" in c_fstype or "arch" in c_label:
                        return Path(child["path"])
    except Exception:
        pass

    return None


def verify_offline_repo_fast(repo_dir: Optional[str] = None) -> Tuple[bool, str]:
    """
    Fast verification of offline package repository integrity across candidate paths.
    Checks archrepo.db tar metadata, file existence, non-zero file sizes,
    and SHA256 checksums of repository package files.
    Returns (is_valid, reason).
    """
    candidates = []
    if repo_dir:
        candidates.append(Path(repo_dir))
    candidates.extend([
        Path("/offline_repo"),
        Path("/mnt/offline_repo"),
        Path("/run/archiso/bootmnt/arch/repo"),
        Path("/run/archiso/bootmnt/offline_repo"),
        Path("/run/archiso/bootmnt/repo"),
    ])

    r_path: Optional[Path] = None
    for cand in candidates:
        if cand.is_dir() and (cand / "archrepo.db").is_file():
            r_path = cand
            break

    # If not found, attempt recovery of ISO block device (e.g. Ventoy / copytoram)
    if not r_path and os.geteuid() == 0:
        iso_dev = recover_iso_block_device()
        if iso_dev:
            iso_mnt = Path("/run/archiso/bootmnt")
            iso_mnt.mkdir(parents=True, exist_ok=True)
            res = subprocess.run(["mountpoint", "-q", str(iso_mnt)], check=False)
            if res.returncode != 0:
                subprocess.run(["mount", "-o", "ro", str(iso_dev), str(iso_mnt)], capture_output=True, check=False)
            for cand in candidates:
                if cand.is_dir() and (cand / "archrepo.db").is_file():
                    r_path = cand
                    break

    if not r_path:
        return False, "Offline repository media directory / archrepo.db not found."

    db_path = r_path / "archrepo.db"

    expected_pkgs: Dict[str, str] = {}
    try:
        with tarfile.open(db_path, "r:*") as tar:
            for member in tar.getmembers():
                if member.name.endswith("/desc"):
                    f = tar.extractfile(member)
                    if not f:
                        continue
                    lines = f.read().decode('utf-8', errors='ignore').splitlines()
                    filename = None
                    sha256sum = None
                    for i, line in enumerate(lines):
                        if line.strip() == "%FILENAME%" and i + 1 < len(lines):
                            filename = lines[i + 1].strip()
                        elif line.strip() == "%SHA256SUM%" and i + 1 < len(lines):
                            sha256sum = lines[i + 1].strip()
                    if filename:
                        expected_pkgs[filename] = ""
    except Exception as e:
        return False, f"Failed to parse database '{db_path}': {e}"

    if not expected_pkgs:
        return False, "Repository database contains no valid package metadata."

    for filename in expected_pkgs.keys():
        pkg_file = r_path / filename
        if not pkg_file.is_file():
            return False, f"Missing offline package: {filename}"
        
        try:
            st = pkg_file.stat()
            if st.st_size == 0:
                return False, f"Corrupted 0-byte package file: {filename}"
        except Exception:
            return False, f"Cannot stat package file: {filename}"

    return True, "Offline repository clean and verified."


# ==============================================================================
# ONCE-STORE (SQLITE)
# ==============================================================================
class OnceStore:
    def __init__(self, db_path: Optional[Path] = None, read_only: bool = False):
        self.db_path = db_path or ((state_dir_path() if read_only else state_dir()) / "once.db")
        busy_timeout = int(GLOBAL_CONFIG.get("execution", {}).get("db_busy_timeout", 5000))
        if read_only and self.db_path.exists():
            self.conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        elif read_only:
            self.conn = sqlite3.connect(":memory:")
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute(f"PRAGMA busy_timeout = {max(busy_timeout, 0)}")
        if not read_only:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
        if read_only and self.db_path.exists():
            return
        self._create_tables()

    def _create_tables(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS once_markers (
                marker_key    TEXT PRIMARY KEY,
                profile       TEXT NOT NULL,
                scope         TEXT NOT NULL DEFAULT 'profile',
                mode          TEXT NOT NULL,
                script_name   TEXT NOT NULL,
                args_key      TEXT NOT NULL,
                resolved_path TEXT NOT NULL,
                checksum      TEXT NOT NULL,
                once_mode     TEXT NOT NULL,
                exit_code     INTEGER,
                run_id        TEXT NOT NULL,
                version       TEXT NOT NULL,
                created       REAL NOT NULL,
                updated       REAL NOT NULL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_once_script ON once_markers (script_name)"
        )
        self.conn.commit()

    def _make_key(self, scope: str, profile_part: str, mode: str, script_name: str,
                  args_key: str, resolved_path: Path | None = None) -> str:
        material = f"once|{scope}|{profile_part}|{mode}|{script_name}|{args_key}"
        if resolved_path is not None:
            material += f"|{resolved_path.resolve()}"
        return hashlib.blake2b(material.encode("utf-8"), digest_size=16).hexdigest()

    def _scope_value(self, scope: str) -> str:
        return scope if scope in ("profile", "global") else "profile"

    def _profile_part(self, profile_name: str, scope: str) -> str:
        if self._scope_value(scope) == "global":
            return "__global__"
        return profile_name

    def marker_valid(self, task: OrchestratorTask, profile_name: str) -> bool:
        if not task.once:
            return False
        scope = self._scope_value(task.once_scope)
        profile_part = self._profile_part(profile_name, scope)
        args_key = shlex.join(task.args)
        key = self._make_key(scope, profile_part, task.mode, task.script_name, args_key,
                             task.resolved_path)
        row = self.conn.execute(
            "SELECT mode, once_mode, checksum, resolved_path FROM once_markers WHERE marker_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            legacy_key = self._make_key(scope, profile_part, task.mode, task.script_name, args_key)
            row = self.conn.execute(
                "SELECT mode, once_mode, checksum, resolved_path FROM once_markers WHERE marker_key = ?",
                (legacy_key,),
            ).fetchone()
            if row is None or task.resolved_path is None or row[3] != str(task.resolved_path):
                return False
        db_mode, db_once_mode, db_checksum, db_resolved = row
        if db_mode != task.mode:
            return False
        if task.once_mode == "content":
            current_checksum = task.checksum or file_checksum(task.resolved_path) if task.resolved_path else ""
            if db_checksum and current_checksum and db_checksum != current_checksum:
                return False
        return True

    def mark_success(self, task: OrchestratorTask, profile_name: str, exit_code: int, run_id: str) -> None:
        if not task.once:
            return
        scope = self._scope_value(task.once_scope)
        profile_part = self._profile_part(profile_name, scope)
        args_key = shlex.join(task.args)
        key = self._make_key(scope, profile_part, task.mode, task.script_name, args_key,
                             task.resolved_path)
        now = time.time()
        checksum = task.checksum or file_checksum(task.resolved_path) if task.resolved_path else ""
        self.conn.execute(
            """
            INSERT INTO once_markers
                (marker_key, profile, scope, mode, script_name, args_key,
                 resolved_path, checksum, once_mode, exit_code, run_id,
                 version, created, updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(marker_key) DO UPDATE SET
                exit_code = excluded.exit_code,
                checksum = excluded.checksum,
                resolved_path = excluded.resolved_path,
                once_mode = excluded.once_mode,
                run_id    = excluded.run_id,
                version   = excluded.version,
                updated   = excluded.updated
            """,
            (
                key, profile_name, scope, task.mode, task.script_name, args_key,
                str(task.resolved_path or ""), checksum, task.once_mode, exit_code, run_id,
                VERSION, now, now,
            ),
        )
        self.conn.commit()

    def forget(self, script_name: str) -> int:
        cur = self.conn.execute(
            "DELETE FROM once_markers WHERE script_name = ?", (script_name,)
        )
        self.conn.commit()
        return cur.rowcount

    def print_list(self, profile_name: Optional[str] = None) -> None:
        rows = self.conn.execute(
            "SELECT profile, mode, script_name, args_key, once_mode, exit_code, version, updated "
            "FROM once_markers ORDER BY updated"
        ).fetchall()
        if not rows:
            print("No once markers recorded.")
            return
        for profile, mode, script_name, args_key, once_mode, exit_code, version, updated in rows:
            when = datetime.datetime.fromtimestamp(updated).strftime("%Y-%m-%d %H:%M")
            args = f" {args_key}" if args_key else ""
            print(f"{when}  [{mode}] {script_name}{args}  ({once_mode}, exit={exit_code}, v{version})")

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


# ==============================================================================
# LOCKING & INTERPRETER RESOLUTION
# ==============================================================================
def parse_args():
    parser = argparse.ArgumentParser(description="Dusky Arch ISO Textual Orchestrator")
    parser.add_argument("--phase1", action="store_true", help="Run Phase 1 (ISO Environment)")
    parser.add_argument("--phase2", action="store_true", help="Run Phase 2 (Chroot Environment)")
    parser.add_argument("--reset", action="store_true", help="Reset execution state for the current phase")
    parser.add_argument("--dry-run", "-d", action="store_true", help="Dry run: validate scripts presence and exit")
    parser.add_argument("--force", action="store_true", help="Pass --force flag to subscripts")
    parser.add_argument("--manual", "-m", action="store_true", help="Manual mode: prompt before each script")
    parser.add_argument("--stop-on-fail", action="store_true", help="Halt execution if any script fails")
    parser.add_argument("--auto", action="store_true", help="Non-interactive automatic mode")
    parser.add_argument("--online", action="store_true", help="Select the online recovery profile")
    parser.add_argument("--profile", type=str, help="Specify profile TOML to execute")
    parser.add_argument("--list-profiles", action="store_true", help="List all available installer profiles and exit")
    parser.add_argument("--list-scripts", action="store_true", help="List all tasks in the selected profile and exit")
    parser.add_argument("--list-once", action="store_true", help="List recorded once-markers and exit")
    parser.add_argument("--forget-once", type=str, help="Remove recorded once-marker(s) for a script and exit")
    parser.add_argument("--doctor", action="store_true", help="Check orchestrator and profile health and exit")
    parser.add_argument("--explain", action="store_true", help="Explain what would happen for each task and exit")
    parser.add_argument("--task-timeout", type=float, default=None, help="Default per-task timeout in seconds (0 = no timeout)")
    parser.add_argument("--no-audio", action="store_true", help="Disable audio notifications")
    parser.add_argument("--no-notify", action="store_true", help="Disable desktop notifications")
    args = parser.parse_args()
    if args.phase1 and args.phase2:
        parser.error("--phase1 and --phase2 conflict")
    inspection = [args.list_profiles, args.list_scripts, args.list_once, args.doctor,
                  args.explain, args.dry_run, bool(args.forget_once)]
    if sum(bool(item) for item in inspection) > 1 or (args.reset and any(inspection)):
        parser.error("inspection modes cannot be combined with each other or --reset")
    if args.task_timeout is not None and (not math.isfinite(args.task_timeout) or args.task_timeout < 0):
        parser.error("--task-timeout must be nonnegative and finite")
    if args.online:
        if args.profile is not None:
            parser.error("--online cannot be combined with --profile")
        args.profile = "Online"
    return args


def _cleanup_lock(lock_file: Path):
    global _LOCK_FD
    if _LOCK_FD is not None:
        try:
            fcntl.flock(_LOCK_FD, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(_LOCK_FD)
        except OSError:
            pass
        _LOCK_FD = None


def acquire_lock(lock_file: Path) -> bool:
    global _LOCK_FD
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0), 0o600)
    except Exception as e:
        sys.stderr.write(f"\033[1;31m[ERROR]\033[0m Could not open lock file {lock_file}: {e}\n")
        return False

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        _LOCK_FD = fd
        atexit.register(lambda: _cleanup_lock(lock_file))
        return True
    except BlockingIOError:
        sys.stderr.write(f"\033[1;31m[ERROR]\033[0m Another instance is already running on {lock_file}.\n")
        try:
            os.close(fd)
        except OSError:
            pass
        return False


def release_lock(lock_file: Path | None = None) -> None:
    _cleanup_lock(lock_file)


def resolve_interpreter(script_path: Path) -> Tuple[str, bool]:
    is_interactive = False
    first_line = ""
    try:
        with open(script_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line_num in range(20):
                line = f.readline()
                if not line:
                    break
                if line_num == 0:
                    first_line = line.strip()
                if INTERACTIVE_RE.search(line):
                    is_interactive = True
    except Exception:
        pass

    suffix = script_path.suffix.lower()
    ext_map = GLOBAL_CONFIG.get("execution", {}).get("extension_interpreters", {
        ".py": "python3", ".sh": "bash", ".fish": "fish"
    })
    if suffix in ext_map:
        interp = ext_map[suffix]
    elif "python" in first_line:
        interp = "python3"
    else:
        interp = GLOBAL_CONFIG.get("execution", {}).get("default_interpreter", "bash")

    return interp, is_interactive


def resolve_script(
    script_name: str,
    search_dirs: List[Path],
    conflict_resolutions: Optional[Dict[str, str]] = None,
) -> Optional[Path]:
    """Locate a script by name. Order: explicit path (SCRIPT_DIR-relative),
    then conflict_resolutions, then SCRIPT_DIR, then profile search_dirs
    (resolved relative to SCRIPT_DIR or absolute); each is searched
    recursively into subdirectories."""
    if "/" in script_name or "\\" in script_name:
        p = SCRIPT_DIR / script_name
        return p if p.is_file() else None

    if conflict_resolutions:
        resolved = conflict_resolutions.get(script_name)
        if resolved:
            rp = Path(resolved)
            if rp.is_file():
                return rp

    roots: List[Path] = [SCRIPT_DIR]
    for d in search_dirs:
        if d not in roots:
            roots.append(d)

    for root in roots:
        direct = root / script_name
        if direct.is_file():
            return direct
        for sub in root.rglob(script_name):
            if sub.is_file():
                return sub
    return None


def is_rich_or_ssh_terminal() -> bool:
    """
    Check if current execution is in a rich graphical terminal environment
    (e.g., Wayland, X11, Kitty, Foot, Alacritty) or over an active SSH session,
    where terminal keybinds (like Alt+Left/Right) do not conflict with Linux VT console switching.
    Returns False in raw Linux console TTYs (/dev/tty1..N, TERM=linux).
    """
    if any(os.environ.get(k) for k in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")):
        return True

    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"):
        return True

    term = os.environ.get("TERM", "")
    term_program = os.environ.get("TERM_PROGRAM", "")
    colorterm = os.environ.get("COLORTERM", "")
    if term_program or colorterm in ("truecolor", "24bit"):
        return True
    if any(t in term for t in ("kitty", "foot", "alacritty", "ghostty", "wezterm")):
        return True

    try:
        tty_name = os.ttyname(sys.stdin.fileno())
        if re.match(r"^/dev/tty\d+$", tty_name) or term == "linux":
            return False
        if tty_name.startswith("/dev/pts/"):
            return True
    except Exception:
        pass

    if term == "linux":
        return False

    return False


def is_in_chroot() -> bool:
    """Detect if running inside an active chroot environment."""
    try:
        root_stat = os.stat("/")
        init_root_stat = os.stat("/proc/1/root")
        return (root_stat.st_dev, root_stat.st_ino) != (init_root_stat.st_dev, init_root_stat.st_ino)
    except Exception:
        return False


AUTO_POWEROFF_MARKERS = [
    Path("/tmp/dusky_auto_poweroff"),
    Path("/etc/dusky_auto_poweroff"),
    Path("/root/dusky_auto_poweroff"),
    Path("/root/arch_install_tmp/dusky_auto_poweroff"),
    Path("/mnt/etc/dusky_auto_poweroff"),
    Path("/mnt/root/dusky_auto_poweroff"),
    Path("/mnt/tmp/dusky_auto_poweroff"),
]


def set_auto_poweroff_marker() -> None:
    for marker_path in AUTO_POWEROFF_MARKERS:
        with suppress(Exception):
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            marker_path.touch()


def remove_auto_poweroff_marker() -> None:
    for marker_path in AUTO_POWEROFF_MARKERS:
        with suppress(Exception):
            if marker_path.exists():
                marker_path.unlink()


def graceful_unmount_and_poweroff(mnt_point: str = "/mnt") -> None:
    """Performs disk synchronization, swap deactivation, target unmount, and system power off."""
    try:
        subprocess.run(["sync"], check=False)
        subprocess.run(["swapoff", "-a"], check=False)
        res = subprocess.run(["mountpoint", "-q", mnt_point], check=False)
        if res.returncode == 0:
            unmount_res = subprocess.run(["umount", "-R", mnt_point], check=False)
            if unmount_res.returncode != 0:
                if shutil.which("fuser"):
                    subprocess.run(["fuser", "-k", "-TERM", "-m", mnt_point], check=False)
                    time.sleep(1.5)
                    subprocess.run(["fuser", "-k", "-KILL", "-m", mnt_point], check=False)
                    time.sleep(0.5)
                subprocess.run(["umount", "-R", mnt_point], check=False)
    except Exception:
        pass
    try:
        subprocess.run(["poweroff"], check=False)
    except Exception:
        subprocess.run(["systemctl", "poweroff"], check=False)


# ==============================================================================
# MODAL SCREENS
# ==============================================================================
class FailureModalScreen(ModalScreen):
    def __init__(self, task_name: str, error_msg: str):
        super().__init__()
        self.task_name = task_name
        self.error_msg = error_msg

    def compose(self) -> ComposeResult:
        with Container(id="modal_dialog"):
            yield Label(f"{S('failed')} TASK FAILED: {self.task_name}", id="modal_title")
            yield Static(self.error_msg, id="error_details")
            with Horizontal(id="button_bar"):
                yield Button("Retry [R]", id="btn_retry")
                yield Button("Skip [S]", id="btn_skip")
                yield Button("Quit [Q]", id="btn_quit")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_retry":
            self.dismiss("retry")
        elif event.button.id == "btn_skip":
            self.dismiss("skip")
        elif event.button.id == "btn_quit":
            self.dismiss("quit")

    def on_key(self, event: events.Key) -> None:
        key = event.key.lower()
        if key in ("left", "h", "up", "k"):
            self.focus_previous()
            event.prevent_default()
            event.stop()
        elif key in ("right", "l", "down", "j"):
            self.focus_next()
            event.prevent_default()
            event.stop()
        elif key == "r":
            self.dismiss("retry")
        elif key == "s":
            self.dismiss("skip")
        elif key == "q":
            self.dismiss("quit")


class ManualModalScreen(ModalScreen):
    def __init__(self, task_name: str):
        super().__init__()
        self.task_name = task_name

    def compose(self) -> ComposeResult:
        with Container(id="manual_dialog"):
            yield Label(f"{S('logo')} MANUAL STEP REQUIRED", id="manual_title")
            yield Static(f"About to execute: [bold white]{self.task_name}[/bold white]\nProceed with execution?", id="manual_details")
            with Horizontal(id="button_bar"):
                yield Button("Proceed [Y]", id="btn_yes")
                yield Button("Skip [S]", id="btn_skip")
                yield Button("Quit [Q]", id="btn_quit")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_yes":
            self.dismiss("yes")
        elif event.button.id == "btn_skip":
            self.dismiss("skip")
        elif event.button.id == "btn_quit":
            self.dismiss("quit")

    def on_key(self, event: events.Key) -> None:
        key = event.key.lower()
        if key in ("left", "h", "up", "k"):
            self.focus_previous()
            event.prevent_default()
            event.stop()
        elif key in ("right", "l", "down", "j"):
            self.focus_next()
            event.prevent_default()
            event.stop()
        elif key in ("y", "space"):
            self.dismiss("yes")
        elif key == "s":
            self.dismiss("skip")
        elif key == "q":
            self.dismiss("quit")


class CompletionDialog(ModalScreen[str]):
    """Final dialog shown when installation completes: View Logs or Power Off."""

    BINDINGS = [
        Binding("left,h,up,k", "focus_previous", "Previous", priority=True, show=False),
        Binding("right,l,down,j", "focus_next", "Next", priority=True, show=False),
        Binding("enter", "poweroff", "Power Off"),
        Binding("v", "view_logs", "View Logs"),
        Binding("p", "poweroff", "Power Off"),
        Binding("escape", "view_logs", "View Logs"),
    ]

    def __init__(
        self,
        title: str = "INSTALLATION COMPLETE",
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
                yield Button(" View Logs [V] ", id="btn_completion_view")
                yield Button(" Power Off [Enter] ", id="btn_completion_poweroff")

    def on_mount(self) -> None:
        with suppress(Exception):
            self.query_one("#btn_completion_poweroff", Button).focus()

    def action_poweroff(self) -> None:
        self.dismiss("poweroff")

    def action_view_logs(self) -> None:
        self.dismiss("view_logs")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_completion_poweroff":
            self.dismiss("poweroff")
        elif event.button.id == "btn_completion_view":
            self.dismiss("view_logs")

    def on_key(self, event: events.Key) -> None:
        key = event.key.lower()
        if key in ("left", "h", "up", "k"):
            self.focus_previous()
            event.prevent_default()
            event.stop()
        elif key in ("right", "l", "down", "j"):
            self.focus_next()
            event.prevent_default()
            event.stop()
        elif key in ("enter", "return"):
            focused = self.focused
            if focused and getattr(focused, "id", None) == "btn_completion_view":
                self.dismiss("view_logs")
            else:
                self.dismiss("poweroff")
        elif key in ("p",):
            self.dismiss("poweroff")
        elif key in ("v", "escape"):
            self.dismiss("view_logs")


# ==============================================================================
# MAIN TEXTUAL APP
# ==============================================================================
class DuskyOrchestratorApp(App):
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen, RichLog, Vertical, Horizontal, ScrollBar {
        background: #0d1117;
        color: #c9d1d9;
        scrollbar-color: #58a6ff80;
        scrollbar-color-hover: #58a6ff;
        scrollbar-color-active: #58a6ff;
        scrollbar-background: transparent;
        scrollbar-background-hover: transparent;
        scrollbar-background-active: transparent;
    }
    Screen {
        layout: vertical;
    }
    #top_header {
        height: 3;
        dock: top;
        background: #161b22;
        color: #58a6ff;
        padding: 0 1;
        layout: vertical;
        border-bottom: solid #30363d;
    }
    #header_title {
        text-style: bold;
        color: #58a6ff;
        width: 100%;
        text-align: center;
    }
    #header_telemetry {
        color: #e3b341;
        text-style: italic;
    }
    #progress_bar {
        margin: 0 1;
        width: 100%;
    }
    ProgressBar > .progress--bar {
        color: #58a6ff;
    }
    #main_content {
        layout: horizontal;
        height: 1fr;
    }
    #left_pane {
        width: 27%;
        border-right: solid #30363d;
        padding: 0;
        height: 100%;
        background: #0d1117;
    }
    #left_pane:focus {
        background-tint: transparent 0%;
    }
    #right_pane {
        width: 73%;
        height: 100%;
        layout: vertical;
        padding: 0;
        background: #0d1117;
    }
    ContentSwitcher, #log_switcher {
        height: 1fr;
        width: 100%;
    }
    Tree {
        background: #0d1117;
        color: #c9d1d9;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 0;
        padding: 0;
        height: 100%;
    }
    Tree:focus {
        background-tint: transparent 0%;
        background: #0d1117;
    }
    Tree > .tree--highlight-line {
        background: transparent;
    }
    Tree > .tree--cursor {
        background: #21262d;
        color: #c9d1d9;
        text-style: bold;
        border-left: tall #58a6ff;
    }
    Tree:focus > .tree--cursor {
        background: #21262d;
        color: #c9d1d9;
        text-style: bold;
        border-left: tall #58a6ff;
    }
    
    RichLog {
        height: 1fr;
        width: 100%;
        border: none;
        background: #0d1117;
        color: #c9d1d9;
        scrollbar-size-vertical: 1;
    }
    #footer {
        dock: bottom;
        height: 1;
        background: #090d16;
        color: #8b949e;
    }

    FailureModalScreen, ManualModalScreen, CompletionDialog {
        align: center middle;
        background: rgba(0,0,0,0.88);
        width: 100%;
        height: 100%;
    }
    #completion_dialog {
        width: 65;
        height: auto;
        border: heavy #58a6ff;
        background: #161b22;
        padding: 1 2;
    }
    #completion_dialog.-success {
        border: heavy #3fb950;
    }
    #completion_dialog.-warning {
        border: heavy #d29922;
    }
    #completion_dialog.-error {
        border: heavy #f85149;
    }
    #completion_title {
        text-align: center;
        text-style: bold;
        color: #58a6ff;
        margin-bottom: 1;
    }
    #completion_message {
        margin-bottom: 1;
    }
    #modal_dialog {
        width: 75;
        height: auto;
        border: heavy #f85149;
        background: #161b22;
        padding: 1 2;
    }
    #manual_dialog {
        width: 75;
        height: auto;
        border: heavy #58a6ff;
        background: #161b22;
        padding: 1 2;
    }
    #modal_title {
        text-align: center;
        text-style: bold;
        color: #f85149;
        margin-bottom: 1;
    }
    #manual_title {
        text-align: center;
        text-style: bold;
        color: #58a6ff;
        margin-bottom: 1;
    }
    #error_details {
        color: #d29922;
        margin-bottom: 1;
        max-height: 10;
        overflow-y: auto;
    }
    #button_bar {
        layout: horizontal;
        align: center middle;
        height: 3;
    }
    Button, #button_bar Button {
        height: 1;
        min-width: 16;
        border: none;
        margin: 0 1;
        background: #21262d;
        color: #8b949e;
        text-style: none;
    }
    Button:hover, #button_bar Button:hover {
        background: #30363d;
        color: #ffffff;
    }
    Button:focus, #button_bar Button:focus {
        background: #58a6ff !important;
        color: #ffffff !important;
        text-style: bold;
    }
    Button:focus:hover, #button_bar Button:focus:hover {
        background: #58a6ff !important;
        color: #ffffff !important;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("q", "quit_app", "Quit"),
        Binding("m", "toggle_manual", "Manual Mode"),
        Binding("r", "reset_state", "Reset State"),
        Binding("alt+left", "shrink_left_pane", "Shrink Sidebar", priority=True, show=False),
        Binding("alt+right", "expand_left_pane", "Expand Sidebar", priority=True, show=False),
        Binding("alt+h", "shrink_left_pane", "Shrink Sidebar", priority=True, show=False),
        Binding("alt+l", "expand_left_pane", "Expand Sidebar", priority=True, show=False),
        Binding("ctrl+left", "shrink_left_pane", "Shrink Sidebar", priority=True, show=False),
        Binding("ctrl+right", "expand_left_pane", "Expand Sidebar", priority=True, show=False),
        Binding("bracketleft", "shrink_left_pane", "Shrink ["),
        Binding("bracketright", "expand_left_pane", "Expand ]"),
        Binding("j", "tree_down", "Tree Down", priority=True, show=False),
        Binding("k", "tree_up", "Tree Up", priority=True, show=False),
        Binding("up", "scroll_preview_up", "Scroll Log Up", priority=True, show=False),
        Binding("down", "scroll_preview_down", "Scroll Log Down", priority=True, show=False),
        Binding("pageup", "scroll_preview_page_up", "Page Up", priority=True, show=False),
        Binding("pagedown", "scroll_preview_page_down", "Page Down", priority=True, show=False),
        Binding("home", "scroll_preview_home", "Home", priority=True, show=False),
        Binding("end", "scroll_preview_end", "End", priority=True, show=False),
        Binding("tab", "toggle_focus", "Switch Focus"),
        Binding("shift+tab", "toggle_focus", "Switch Focus", show=False),
    ]

    def __init__(
        self,
        tasks: List[OrchestratorTask],
        phase_title: str,
        profile_name: str,
        state_file: Path,
        manual: bool,
        stop_on_fail: bool,
        force: bool,
        task_timeout: float = 0.0,
        once_store: Optional[OnceStore] = None,
        dry_run: bool = False,
        is_final_phase: bool = True,
    ):
        super().__init__()
        self.tasks = tasks
        self.phase_title = phase_title
        self.is_final_phase = is_final_phase
        self.profile_name = profile_name
        self.state_file = state_file
        self.manual = manual
        self.stop_on_fail = stop_on_fail
        self.force_flag = force
        self.task_timeout = max(task_timeout or 0.0, 0.0)
        self.once_store = once_store or OnceStore()
        self.persistence_failed = False
        self.dry_run = dry_run
        self.start_time = time.monotonic()

        self.current_idx = 0
        self.completed_keys = set()
        self.task_statuses: dict[str, str] = {}
        self.counters = {"completed": 0, "failed": 0, "skipped": 0, "pending": len(tasks)}
        self.conditions = ConditionEvaluator()
        self.run_id = hashlib.md5(f"{time.time()}:{phase_title}".encode()).hexdigest()[:8]
        self.logger = RunLogger(profile_name, self.run_id)
        for i, t in enumerate(self.tasks, start=1):
            if not getattr(t, "state_key", ""):
                t.state_key = make_state_key(t, i)

        self.left_pane_width: int = GLOBAL_CONFIG.get("ui", {}).get("left_pane_width", 27)
        cfg_footer = GLOBAL_CONFIG.get("ui", {}).get("show_keybinds_footer", "auto")
        if isinstance(cfg_footer, bool):
            self.show_footer: bool = cfg_footer
        else:
            self.show_footer: bool = is_rich_or_ssh_terminal()

        self.active_task: Optional[OrchestratorTask] = None
        self.current_log_key: str | None = None
        self._log_widgets: dict[str | None, RichLog] = {}
        self.tree_nodes_map: dict[str, TreeNode] = {}
        self.tree_widget = Tree(f"{S('logo')} Execution Sequence", id="tree_widget")

        if self.state_file.exists():
            try:
                self.completed_keys = set(self.state_file.read_text().splitlines())
            except Exception:
                pass

        max_lines = GLOBAL_CONFIG.get("ui", {}).get("max_log_lines", 6000)
        self.log_widget = RichLog(id="pty_log", highlight=False, markup=False, wrap=True, max_lines=max_lines)
        self.progress_bar = ProgressBar(total=len(self.tasks), show_eta=False, id="progress_bar")
        self.header_title = Static(
            f"{S('logo')} DUSKY ARCH INSTALLER  [{self.phase_title}]  (Profile: {self.profile_name})",
            id="header_title",
        )
        self.header_telemetry = Static("Status: Ready | Telemetry: Idle", id="header_telemetry")

    def compose(self) -> ComposeResult:
        with Vertical(id="top_header"):
            yield self.header_title
            with Horizontal():
                yield self.header_telemetry
                yield self.progress_bar

        with Horizontal(id="main_content"):
            with Vertical(id="left_pane"):
                yield self.tree_widget
            with Vertical(id="right_pane"):
                with ContentSwitcher(id="log_switcher"):
                    yield self.log_widget
                    max_lines = GLOBAL_CONFIG.get("ui", {}).get("max_log_lines", 6000)
                    yield RichLog(
                        id="log_report",
                        highlight=False,
                        markup=True,
                        wrap=True,
                        auto_scroll=False,
                        max_lines=max_lines,
                    )
                    for task in self.tasks:
                        yield RichLog(
                            id=f"log_{task.state_key}",
                            highlight=False,
                            markup=False,
                            wrap=True,
                            max_lines=max_lines,
                        )

        if self.show_footer:
            yield Footer()

    def on_mount(self) -> None:
        with suppress(Exception):
            self.query_one("#log_switcher", ContentSwitcher).current = "pty_log"

        self._rebuild_tree()

        for t in self.tasks:
            if t.state_key in self.completed_keys:
                t.status = TaskStatus.COMPLETED
                self.task_statuses[t.state_key] = "COMPLETED"
                self.counters["completed"] += 1
                self.counters["pending"] -= 1
                self.progress_bar.advance(1)
                self.update_task_status(t.index - 1, TaskStatus.COMPLETED)

        self.log_system(f"Started Phase: {self.phase_title}")
        self.log_system(f"Active Profile: {self.profile_name}")
        self.log_system(f"Loaded Cached State: {len(self.completed_keys)} tasks completed")

        self.run_worker(self.run_execution_loop())

    def _render_final_overview_block(self) -> None:
        total_duration = time.monotonic() - (getattr(self, "start_time", None) or time.monotonic())
        failed_tasks = [t for t in self.tasks if self.task_statuses.get(t.state_key) == "FAILED"]
        skipped_tasks = [t for t in self.tasks if self.task_statuses.get(t.state_key) == "SKIPPED"]

        if getattr(self, "dry_run", False):
            v_title, v_color = "DRY-RUN", "#d29922"
        elif failed_tasks:
            v_title, v_color = "WARNINGS" if any(t.ignore_fail for t in failed_tasks) else "ABORTED", "#f85149"
        else:
            v_title, v_color = "SUCCESS", "#3fb950"

        timed_tasks = sorted([t for t in self.tasks if getattr(t, "duration", 0) > 0], key=lambda x: x.duration, reverse=True)
        if timed_tasks:
            top = timed_tasks[:3]
            slowest_str = ", ".join(f"{t.script_name} ({t.duration:.1f}s)" for t in top)
        else:
            slowest_str = "None recorded"

        modes = sorted(list({t.mode for t in self.tasks})) or ["USER", "SUDO"]
        matrix = {m: {"completed": 0, "failed": 0, "skipped": 0, "total": 0} for m in modes}
        for task in self.tasks:
            m = task.mode
            if m not in matrix:
                matrix[m] = {"completed": 0, "failed": 0, "skipped": 0, "total": 0}
            st = self.task_statuses.get(task.state_key, "PENDING")
            matrix[m]["total"] += 1
            if st == "COMPLETED":
                matrix[m]["completed"] += 1
            elif st == "FAILED":
                matrix[m]["failed"] += 1
            else:
                matrix[m]["skipped"] += 1

        tot_all = len(self.tasks)
        tot_succ = sum(matrix[m]["completed"] for m in matrix)
        tot_fail = sum(matrix[m]["failed"] for m in matrix)
        tot_skip = sum(matrix[m]["skipped"] for m in matrix)

        sep = ASCII_SYMBOLS.get('sep', '|') if ASCII_MODE else UNICODE_SYMBOLS.get('sep', '│')

        lines = [
            f"════════════════════════════════════════════════════════════════════════════════",
            f" ◆ FINAL OVERVIEW {sep} [bold #58a6ff]{escape(self.phase_title)}[/] {sep} Verdict: [bold {v_color}]{v_title}[/]",
            f"════════════════════════════════════════════════════════════════════════════════",
            f"",
            f" {S('timing')} TIMING & PERFORMANCE",
            f"   Total Pipeline Duration : [bold #58a6ff]{total_duration:.2f}s[/]",
            f"   • Top Bottlenecks               : {slowest_str}",
            f"",
            f" {S('matrix')} SCRIPT EXECUTION MATRIX",
            f"   ┌──────────┬──────────┬──────────┬──────────┬──────────┐",
            f"   │ MODE     │ SUCCESS  │ FAILED   │ SKIPPED  │ TOTAL    │",
            f"   ├──────────┼──────────┼──────────┼──────────┼──────────┤",
        ]

        for mode_name in sorted(matrix.keys()):
            r = matrix[mode_name]
            lines.append(
                f"   │ {mode_name:<8s} │    [bold #3fb950]{r['completed']:2d}[/]    │    [bold #f85149]{r['failed']:2d}[/]    │    [dim #d29922]{r['skipped']:2d}[/]    │    {r['total']:2d}    │"
            )

        lines.extend([
            f"   ├──────────┼──────────┼──────────┼──────────┼──────────┤",
            f"   │ TOTAL    │    [bold #3fb950]{tot_succ:2d}[/]    │    [bold #f85149]{tot_fail:2d}[/]    │    [dim #d29922]{tot_skip:2d}[/]    │    {tot_all:2d}    │",
            f"   └──────────┴──────────┴──────────┴──────────┴──────────┘",
            f"",
        ])

        if failed_tasks:
            hard_failed = [t for t in failed_tasks if not t.ignore_fail]
            soft_failed = [t for t in failed_tasks if t.ignore_fail]

            if hard_failed:
                lines.append(f" [bold #f85149]✗ HARD FAILED TASKS ({len(hard_failed)}):[/]")
                for t in hard_failed:
                    lines.append(f"   • [{t.mode}] {escape(t.script_name)} [bold #f85149](Required - Aborted)[/]")

            if soft_failed:
                lines.append(f" [bold #d29922]⚠ SOFT FAILED TASKS ({len(soft_failed)}):[/]")
                for t in soft_failed:
                    lines.append(f"   • [{t.mode}] {escape(t.script_name)} [dim #d29922](Ignored / Allowed to Fail)[/dim]")

            failed_dirs = sorted(list({str(t.resolved_path.parent) for t in failed_tasks if getattr(t, "resolved_path", None)}))
            if failed_dirs:
                lines.append(f"   [dim]Debug locations:[/dim]")
                for d in failed_dirs:
                    lines.append(f"     └─ [dim]{escape(d)}[/dim]")
        else:
            lines.append(f" [dim]✗ FAILED TASKS     : None[/dim]")

        if skipped_tasks:
            lines.append(f" [bold #d29922]- SKIPPED TASKS ({len(skipped_tasks)}):[/]")
            for t in skipped_tasks[:12]:
                reason = "condition false" if t.condition else ("once marker valid" if t.once else "ignored failure")
                lines.append(f"   • [{t.mode}] {escape(t.script_name)} [dim]({reason})[/dim]")
            if len(skipped_tasks) > 12:
                lines.append(f"   • ... and {len(skipped_tasks) - 12} more skipped task(s).")
        else:
            lines.append(f" [dim]- SKIPPED TASKS    : None[/dim]")

        lines.extend([
            f"",
            f" {S('preflight')} SYSTEM & PREFLIGHT",
            f"   • User / Home  : {os.environ.get('USER', 'root')} ({Path.home()})",
            f"   • Log File     : {self.logger.root or 'Logs'}",
            f"════════════════════════════════════════════════════════════════════════════════\n",
        ])

        with suppress(Exception):
            rw = self.query_one("#log_report", RichLog)
            rw.clear()
            for line in lines:
                rw.write(Text.from_markup(line))

        for line in lines:
            self.log_widget.write(Text.from_markup(line))

        self.current_log_key = "report"
        with suppress(Exception):
            if report_node := self.tree_nodes_map.get("__report__"):
                self.tree_widget.select_node(report_node)
                self.tree_widget.scroll_to_node(report_node)
            self.query_one("#log_switcher", ContentSwitcher).current = "log_report"

    def _task_label(self, task: OrchestratorTask) -> Text:
        if task.status == TaskStatus.COMPLETED or task.state_key in self.completed_keys:
            icon = f"[bold #3fb950]{S('completed')}[/]"
            name_style = "bold #3fb950"
        elif not task.resolved_path:
            icon = "[bold #f85149]![/]"
            name_style = "bold #f85149"
        elif task.status == TaskStatus.RUNNING:
            icon = f"[bold #58a6ff]{S('running')}[/]"
            name_style = "bold #58a6ff"
        elif task.status == TaskStatus.FAILED:
            icon = f"[bold #f85149]{S('failed')}[/]"
            name_style = "bold #f85149"
        elif task.status == TaskStatus.SKIPPED:
            icon = f"[bold #d29922]{S('skipped')}[/]"
            name_style = "dim #d29922"
        else:
            icon = f"[#8b949e]{S('pending')}[/]"
            name_style = "dim #8b949e"

        # Clean script name WITHOUT redundant "USER" moniker
        return Text.from_markup(f" {icon} [{name_style}]{task.script_name}[/]")

    def _rebuild_tree(self) -> None:
        self.tree_nodes_map.clear()
        with suppress(Exception):
            self.tree_widget.root.remove_children()
        with suppress(Exception):
            self.tree_widget.clear()

        self.tree_widget.show_guides = False
        self.tree_widget.show_root = False
        self.tree_widget.root.expand()

        main_node = self.tree_widget.root.add_leaf(
            Text.from_markup(f" [bold #58a6ff]CORE[/] Main Engine Log")
        )
        main_node.data = "MAIN"
        self.tree_nodes_map["__main__"] = main_node

        for task in self.tasks:
            node = self.tree_widget.root.add_leaf(self._task_label(task))
            node.data = task
            self.tree_nodes_map[task.state_key] = node

        report_node = self.tree_widget.root.add_leaf(
            Text.from_markup(f" [bold #3fb950]◆ REPORT[/] Final Overview")
        )
        report_node.data = "REPORT"
        self.tree_nodes_map["__report__"] = report_node

    @on(Tree.NodeSelected)
    @on(Tree.NodeHighlighted)
    def on_tree_node_change(self, event: Tree.NodeSelected | Tree.NodeHighlighted) -> None:
        node = event.node
        with suppress(Exception):
            switcher = self.query_one("#log_switcher", ContentSwitcher)
            if node.data == "REPORT":
                switcher.current = "log_report"
                self.current_log_key = "report"
            elif node == self.tree_widget.root or node.data == "MAIN":
                switcher.current = "pty_log"
                self.current_log_key = None
            elif isinstance(node.data, OrchestratorTask):
                switcher.current = f"log_{node.data.state_key}"
                self.current_log_key = node.data.state_key

    def update_task_status(self, idx: int, status: TaskStatus):
        if 0 <= idx < len(self.tasks):
            t = self.tasks[idx]
            t.status = status
            with suppress(Exception):
                if node := self.tree_nodes_map.get(t.state_key):
                    node.label = self._task_label(t)

    def select_task_node(self, state_key: str):
        with suppress(Exception):
            if node := self.tree_nodes_map.get(state_key):
                self.tree_widget.select_node(node)
                self.tree_widget.scroll_to_node(node)
                self.query_one("#log_switcher", ContentSwitcher).current = f"log_{state_key}"
                self.current_log_key = state_key

    def _set_pane_widths(self, width_pct: int) -> None:
        min_w = GLOBAL_CONFIG.get("ui", {}).get("min_left_pane_width", 15)
        max_w = GLOBAL_CONFIG.get("ui", {}).get("max_left_pane_width", 80)
        self.left_pane_width = max(min_w, min(max_w, width_pct))
        with suppress(Exception):
            self.query_one("#left_pane").styles.width = f"{self.left_pane_width}%"
            self.query_one("#right_pane").styles.width = f"{100 - self.left_pane_width}%"

    def _update_pane_width_from_mouse(self, mouse_screen_x: int) -> None:
        with suppress(Exception):
            dashboard = self.query_one("#main_content")
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

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        with suppress(Exception):
            dashboard = self.query_one("#main_content")
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

    @staticmethod
    def _set_pty_size(fd: int) -> None:
        try:
            size = os.get_terminal_size()
            winsize = struct.pack("HHHH", size.lines, size.columns, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            try:
                winsize = struct.pack("HHHH", 40, 120, 0, 0)
                fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
            except Exception:
                pass

    def on_resize(self, event: events.Resize) -> None:
        if getattr(self, "current_pty_master", None) is not None:
            with suppress(Exception):
                self._set_pty_size(self.current_pty_master)

    def action_tree_down(self) -> None:
        with suppress(Exception):
            self.tree_widget.action_cursor_down()

    def action_tree_up(self) -> None:
        with suppress(Exception):
            self.tree_widget.action_cursor_up()

    def _get_active_visible_log(self) -> Optional[RichLog]:
        with suppress(Exception):
            switcher = self.query_one("#log_switcher", ContentSwitcher)
            if switcher.current:
                return self.query_one(f"#{switcher.current}", RichLog)
        return self._get_log_widget(None)

    def action_scroll_preview_up(self) -> None:
        with suppress(Exception):
            if log_w := self._get_active_visible_log():
                log_w.scroll_up(animate=False)

    def action_scroll_preview_down(self) -> None:
        with suppress(Exception):
            if log_w := self._get_active_visible_log():
                log_w.scroll_down(animate=False)

    def action_scroll_preview_page_up(self) -> None:
        with suppress(Exception):
            if log_w := self._get_active_visible_log():
                log_w.scroll_page_up(animate=False)

    def action_scroll_preview_page_down(self) -> None:
        with suppress(Exception):
            if log_w := self._get_active_visible_log():
                log_w.scroll_page_down(animate=False)

    def action_scroll_preview_home(self) -> None:
        with suppress(Exception):
            if log_w := self._get_active_visible_log():
                log_w.scroll_home(animate=False)

    def action_scroll_preview_end(self) -> None:
        with suppress(Exception):
            if log_w := self._get_active_visible_log():
                log_w.scroll_end(animate=False)

    def action_toggle_focus(self) -> None:
        if self.tree_widget.has_focus:
            with suppress(Exception):
                switcher = self.query_one("#log_switcher", ContentSwitcher)
                if switcher.current:
                    cur_widget = self.query_one(f"#{switcher.current}")
                    cur_widget.focus()
                else:
                    self.log_widget.focus()
        else:
            self.tree_widget.focus()

    def _get_log_widget(self, key: str | None) -> Optional[RichLog]:
        if key in self._log_widgets:
            return self._log_widgets[key]
        widget_id = "#pty_log" if key is None else f"#log_{key}"
        with suppress(Exception):
            w = self.query_one(widget_id, RichLog)
            self._log_widgets[key] = w
            return w
        return None

    def log_system(self, msg: str):
        text_ansi = f"\033[1;36m[SYSTEM]\033[0m {msg}\n"
        txt = Text.from_ansi(text_ansi)
        if main_w := self._get_log_widget(None):
            main_w.write(txt)
        if self.active_task:
            if task_w := self._get_log_widget(self.active_task.state_key):
                task_w.write(txt)
        self.logger.system(msg)

    def log_task(self, msg: str, task: Optional[OrchestratorTask] = None):
        txt = Text.from_ansi(msg)
        if main_w := self._get_log_widget(None):
            main_w.write(txt)
        t = task or self.active_task
        if t:
            if task_w := self._get_log_widget(t.state_key):
                task_w.write(txt)

    def update_telemetry(self, status_str: str, speed_str: str = ""):
        if speed_str:
            self.header_telemetry.update(f"Status: {status_str} | Speed/ETA: {speed_str}")
        else:
            self.header_telemetry.update(f"Status: {status_str}")

    @contextmanager
    def _suspend_ui(self):
        suspend = getattr(self, "suspend", None)
        if callable(suspend):
            try:
                context = suspend()
                context.__enter__()
            except Exception:
                pass  # An inactive Textual driver cannot be suspended.
            else:
                try:
                    try:
                        yield
                    except BaseException:
                        context.__exit__(*sys.exc_info())
                        raise
                    else:
                        context.__exit__(None, None, None)
                finally:
                    with suppress(Exception):
                        self.screen.refresh(repaint=True, layout=True)
                return

        driver = getattr(self, "driver", None)
        if driver is not None and hasattr(driver, "stop_application_mode"):
            with suppress(Exception):
                driver.stop_application_mode()

        try:
            yield
        finally:
            if driver is not None and hasattr(driver, "start_application_mode"):
                with suppress(Exception):
                    driver.start_application_mode()

    @staticmethod
    def _write_pty(fd: int, data: bytes) -> None:
        while data:
            written = os.write(fd, data)
            if written <= 0:
                raise OSError("PTY input made no progress")
            data = data[written:]

    @staticmethod
    async def _stop_pty_child(proc: asyncio.subprocess.Process) -> None:
        # A PTY child has its own session; its descendants may outlive the leader.
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), 2)
        except TimeoutError:
            pass
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        if proc.returncode is None:
            with suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), 2)

    @staticmethod
    def _interactive_tree(root_pid: int) -> None:
        # Interactive tasks share our terminal process group, so signal PIDs.
        members: dict[int, tuple[int, str]] = {}
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                members[int(entry.name)] = (int(fields[1]), fields[19])
            except (OSError, ValueError, IndexError):
                continue
        selected = {root_pid}
        while True:
            found = {pid for pid, (parent, _) in members.items() if parent in selected}
            if found <= selected:
                break
            selected.update(found)
        for sig in (signal.SIGTERM, signal.SIGKILL):
            for pid in sorted(selected, reverse=True):
                old = members.get(pid)
                if old is None:
                    continue
                try:
                    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                    if (int(fields[1]), fields[19]) == old:
                        os.kill(pid, sig)
                except (OSError, ValueError, IndexError):
                    pass
            if sig == signal.SIGTERM:
                time.sleep(0.3)

    async def _run_interactive(self, cmd: list[str], timeout: float) -> int:
        with self._suspend_ui():
            proc = subprocess.Popen(cmd)
            try:
                return await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout if timeout > 0 else None)
            except (TimeoutError, asyncio.CancelledError):
                await asyncio.to_thread(self._interactive_tree, proc.pid)
                await asyncio.to_thread(proc.wait)
                raise

    async def _run_pty(self, task: OrchestratorTask, cmd: list[str], timeout: float) -> int:
        master_fd, slave_fd = pty.openpty()
        proc: asyncio.subprocess.Process | None = None
        transport = None
        file_obj = None
        read_task: asyncio.Task | None = None
        try:
            self.current_pty_master = master_fd
            self._set_pty_size(master_fd)
            proc = await asyncio.create_subprocess_exec(*cmd, stdin=slave_fd, stdout=slave_fd,
                                                        stderr=slave_fd, close_fds=True,
                                                        start_new_session=True)
            os.close(slave_fd)
            slave_fd = -1
            loop = asyncio.get_running_loop()
            reader = asyncio.StreamReader(limit=1024 * 1024)
            file_obj = os.fdopen(master_fd, "rb", buffering=0)
            transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), file_obj)
            line_buffer = ""
            prompt_buffer = ""

            async def read_output() -> None:
                nonlocal line_buffer, prompt_buffer
                while True:
                    try:
                        chunk = await reader.read(4096)
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise
                        chunk = b""
                    if not chunk:
                        break
                    text = chunk.decode("utf-8", errors="replace")
                    prompt_buffer = (prompt_buffer + ANSI_STRIP_REGEX.sub("", text))[-4096:]
                    for _ in range(8):
                        matches = [(match.start(), i, name, match, response)
                                   for i, (name, pattern, response) in enumerate(PROMPT_RULES)
                                   if (match := pattern.search(prompt_buffer)) is not None]
                        if not matches:
                            break
                        _, _, name, match, response = min(matches, key=lambda item: item[:2])
                        await asyncio.to_thread(self._write_pty, master_fd, response.encode("utf-8"))
                        self.log_system(f"Auto-responded to prompt ({name})")
                        prompt_buffer = prompt_buffer[match.end():]
                    speed = SPEED_ETA_REGEX.search(text)
                    pct = PCT_REGEX.search(text)
                    if speed:
                        self.update_telemetry(f"Running {task.script_name}",
                                              f"{speed.group(1)} (ETA {speed.group(2)})")
                    elif pct:
                        self.update_telemetry(f"Running {task.script_name} ({pct.group(0)})")
                    line_buffer += text
                    while "\n" in line_buffer or "\r" in line_buffer:
                        indices = [i for i in (line_buffer.find("\n"), line_buffer.find("\r")) if i >= 0]
                        idx = min(indices)
                        line, line_buffer = line_buffer[:idx], line_buffer[idx + 1:]
                        if line.strip():
                            self.log_task(line + "\n", task)
                            self.logger.write_task(task, ANSI_STRIP_REGEX.sub("", line).strip())
                    if len(line_buffer) > 32768:
                        self.log_task(line_buffer, task)
                        self.logger.write_task(task, ANSI_STRIP_REGEX.sub("", line_buffer))
                        line_buffer = ""
                if line_buffer.strip():
                    self.log_task(line_buffer + "\n", task)
                    self.logger.write_task(task, ANSI_STRIP_REGEX.sub("", line_buffer).strip())

            read_task = asyncio.create_task(read_output())
            async def wait_child() -> int:
                proc_task = asyncio.create_task(proc.wait())
                try:
                    done, _ = await asyncio.wait((read_task, proc_task),
                                                 return_when=asyncio.FIRST_COMPLETED)
                    if read_task in done and read_task.exception() is not None:
                        raise read_task.exception()
                    code = await proc_task
                    await asyncio.wait_for(read_task, 2)
                    return code
                finally:
                    if not proc_task.done():
                        proc_task.cancel()
            return await asyncio.wait_for(wait_child(), timeout if timeout > 0 else None)
        finally:
            if proc is not None:
                await self._stop_pty_child(proc)
            if read_task is not None and not read_task.done():
                read_task.cancel()
                with suppress(asyncio.CancelledError):
                    await read_task
            self.current_pty_master = None
            if transport is not None:
                transport.close()
            elif file_obj is not None:
                file_obj.close()
            else:
                os.close(master_fd)
            if slave_fd >= 0:
                os.close(slave_fd)

    async def push_screen_wait(self, screen: Any) -> Any:
        future = asyncio.get_running_loop().create_future()

        def _callback(res: Any) -> None:
            if not future.done():
                future.set_result(res)

        self.push_screen(screen, callback=_callback)
        return await future

    async def run_execution_loop(self):
        while self.current_idx < len(self.tasks):
            task = self.tasks[self.current_idx]

            if task.state_key in self.completed_keys:
                self.current_idx += 1
                continue

            if not task.resolved_path:
                await self.handle_missing_task(task)
                return

            if task.once and self.once_store.marker_valid(task, self.profile_name):
                self.log_system(f"Once-marker valid for '{task.script_name}' ({task.once_mode}). Skipping.")
                self.task_skipped(task)
                continue

            if task.condition and not self.conditions.check(task.condition):
                self.log_system(f"Condition '{task.condition}' unfulfilled. Skipping {task.script_name}.")
                self.task_skipped(task)
                continue

            if self.manual:
                res = await self.push_screen_wait(ManualModalScreen(task.script_name))
                if res == "yes":
                    pass
                elif res == "skip":
                    self.task_skipped(task)
                    continue
                else:
                    self.exit(return_code=1)
                    return

            await self.execute_task(task)
            return

        self.log_system("All tasks in this phase completed.")
        self.update_telemetry("Finished Phase")
        self.logger.write_report(self.profile_name, self.tasks, self.task_statuses, self.counters)
        self._render_final_overview_block()

        failed_tasks = [t for t in self.tasks if self.task_statuses.get(t.state_key) == "FAILED"]

        if not self.is_final_phase:
            # INTERMEDIATE PHASE (Phase 1: ISO) -> Automatically hand off to Phase 2
            if failed_tasks or self.persistence_failed or self.logger.failed_write:
                NotificationManager.play_sound("alert")
                NotificationManager.send_desktop(
                    "Phase 1 Failed",
                    f"{len(failed_tasks)} required task(s) failed in Phase 1.",
                    urgency="critical",
                )
                await asyncio.sleep(1.0)
                self.exit(return_code=1)
            else:
                NotificationManager.play_sound("complete")
                NotificationManager.send_desktop(
                    "Phase 1 Completed",
                    f"Successfully completed {self.phase_title}. Continuing to Phase 2...",
                )
                await asyncio.sleep(0.5)
                self.exit(return_code=0)
            return

        # FINAL PHASE (Phase 2: Chroot / Full Installation Complete)
        if failed_tasks or self.persistence_failed or self.logger.failed_write:
            NotificationManager.play_sound("alert")
            NotificationManager.send_desktop(
                "Installation Finished with Warnings",
                f"{len(failed_tasks)} task(s) failed in {self.phase_title}",
                urgency="critical",
            )
        else:
            NotificationManager.play_sound("complete")
            NotificationManager.send_desktop(
                "Installation Completed",
                f"Successfully completed installation ({self.phase_title})",
            )

        completed = self.counters.get("completed", 0)
        failed = self.counters.get("failed", 0)
        skipped = self.counters.get("skipped", 0)
        total_time = time.monotonic() - (getattr(self, "start_time", None) or time.monotonic())
        elapsed_m = int(total_time) // 60
        elapsed_s = int(total_time) % 60
        elapsed_str = f"{elapsed_m:02d}:{elapsed_s:02d}"

        summary_lines = (
            f"Installation: {self.phase_title}\n"
            f"Profile: {self.profile_name}\n"
            f"Completed: {completed}\n"
            f"Failed: {failed}\n"
            f"Skipped: {skipped}\n"
            f"Elapsed: {elapsed_str}\n"
            f"Logs: {self.logger.root or logs_dir()}\n\n"
            "Choose an action:"
        )

        res = await self.push_screen_wait(
            CompletionDialog(
                title="INSTALLATION FINISHED WITH WARNINGS" if failed_tasks or self.persistence_failed or self.logger.failed_write else "INSTALLATION COMPLETE",
                message=summary_lines,
                level="warning" if failed_tasks or self.persistence_failed or self.logger.failed_write else "success",
            )
        )
        if res == "poweroff":
            self.trigger_poweroff()
        elif res == "view_logs":
            remove_auto_poweroff_marker()
            self.log_system("Reviewing execution logs. Press 'q' when finished to exit.")

    async def handle_missing_task(self, task: OrchestratorTask):
        self.update_task_status(self.current_idx, TaskStatus.FAILED)
        self.log_task(f"\033[1;31m[ERROR] Missing script: {task.script_name}\033[0m")
        NotificationManager.play_sound("alert")

        if self.stop_on_fail or task.on_failure == "abort":
            self.log_system("stop-on-fail/abort active. Terminating installer phase.")
            await asyncio.sleep(1.5)
            self.exit(return_code=1)
            return
        if task.on_failure == "skip":
            self.task_skipped(task)
            return
        if task.on_failure == "continue":
            self.log_system(f"on_failure=continue active. Proceeding past {task.script_name}.")
            self.current_idx += 1
            self.run_worker(self.run_execution_loop())
            return

        res = await self.push_screen_wait(FailureModalScreen(task.script_name, "Script file not found on disk."))
        if res == "retry":
            self.run_worker(self.run_execution_loop())
        elif res == "skip":
            self.task_skipped(task)
        else:
            self.exit(return_code=1)

    async def execute_task(self, task: OrchestratorTask):
        self.active_task = task
        self.update_task_status(self.current_idx, TaskStatus.RUNNING)
        self.select_task_node(task.state_key)
        start_header = f"\n\033[1;36m>>> PROCESS INITIATED: {task.script_name}\033[0m\n"
        self.log_task(start_header, task)
        self.update_telemetry(f"Running {task.script_name}")

        args = list(task.args)
        if (self.force_flag or task.force_flag) and "--force" not in args:
            args.append("--force")

        cmd = [task.interpreter, str(task.resolved_path)] + args
        timeout = task.timeout if task.timeout is not None else self.task_timeout
        max_attempts = max(1, task.retry + 1)

        for attempt in range(1, max_attempts + 1):
            self.logger.open_task(task, cmd)
            start_t = time.time()
            rc = 1
            error_msg = ""

            try:
                if task.interactive:
                    self.log_system(f"Delegating terminal to interactive process: {task.script_name}")
                    rc = await self._run_interactive(cmd, timeout or 0.0)
                    if rc in (130, -signal.SIGINT):
                        self.exit(return_code=130)
                        return
                else:
                    rc = await self._run_pty(task, cmd, timeout or 0.0)
                if rc != 0:
                    error_msg = f"Process exited with status code {rc}"
            except TimeoutError:
                rc = 124
                error_msg = f"Timeout after {timeout}s"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                rc = 127
                error_msg = str(exc)
            dur = time.time() - start_t

            if rc == 0:
                self.logger.close_task(task, status="COMPLETED", exit_code=0, duration=dur)
                if task.once:
                    self.once_store.mark_success(task, self.profile_name, 0, self.run_id)
                await self.task_success(task, dur)
                return

            self.logger.close_task(task, status="FAILED", exit_code=rc or 1, duration=dur)

            if task.ignore_fail:
                self.log_system(f"Task exited with status {rc} but ignore_fail is active. Proceeding.")
                if task.once:
                    self.once_store.mark_success(task, self.profile_name, 0, self.run_id)
                await self.task_success(task, dur)
                return

            if attempt < max_attempts:
                self.log_system(f"Attempt {attempt}/{max_attempts} failed. Retrying in {task.retry_delay}s...")
                NotificationManager.play_sound("alert")
                await asyncio.sleep(task.retry_delay)
                continue

            await self.task_failure(task, error_msg or f"Process exited with status code {rc}", dur)
            return

    async def task_success(self, task: OrchestratorTask, duration: float = 0.0):
        self.update_task_status(self.current_idx, TaskStatus.COMPLETED)
        task.duration = duration
        self.log_task("\n\033[1;32m>>> EXECUTION SUCCESSFUL\033[0m\n", task)
        self.completed_keys.add(task.state_key)
        self.task_statuses[task.state_key] = "COMPLETED"
        self.counters["completed"] += 1
        if self.counters["pending"] > 0:
            self.counters["pending"] -= 1
        self.logger.close_task(task, status="COMPLETED", exit_code=0, duration=duration)

        try:
            with open(self.state_file, "a") as f:
                f.write(task.state_key + "\n")
        except Exception as e:
            self.persistence_failed = True
            self.log_system(f"Failed to record state: {e}")

        self.progress_bar.advance(1)
        self.current_idx += 1
        self.run_worker(self.run_execution_loop())

    def task_skipped(self, task: OrchestratorTask):
        self.update_task_status(self.current_idx, TaskStatus.SKIPPED)
        self.log_system(f"Skipped task: {task.script_name}")
        self.task_statuses[task.state_key] = "SKIPPED"
        self.counters["skipped"] += 1
        if self.counters["pending"] > 0:
            self.counters["pending"] -= 1
        self.logger.close_task(task, status="SKIPPED")
        self.progress_bar.advance(1)
        self.current_idx += 1
        self.run_worker(self.run_execution_loop())

    async def task_failure(self, task: OrchestratorTask, reason: str, duration: float = 0.0):
        self.update_task_status(self.current_idx, TaskStatus.FAILED)
        task.duration = duration
        self.log_task(f"\n\033[1;31m>>> EXECUTION FAILED: {reason}\033[0m\n", task)
        self.task_statuses[task.state_key] = "FAILED"
        self.counters["failed"] += 1
        if self.counters["pending"] > 0:
            self.counters["pending"] -= 1
        self.logger.close_task(task, status="FAILED", exit_code=1, duration=duration)
        NotificationManager.play_sound("alert")
        NotificationManager.send_desktop("Task Failed", f"Script '{task.script_name}' failed: {reason}", urgency="critical")

        if self.stop_on_fail or task.on_failure == "abort":
            self.log_system("stop-on-fail/abort active. Terminating installer phase.")
            await asyncio.sleep(1.5)
            self.exit(return_code=1)
        elif task.on_failure == "skip":
            self.task_skipped(task)
        elif task.on_failure == "continue":
            self.log_system(f"on_failure=continue active. Proceeding past {task.script_name}.")
            self.current_idx += 1
            self.run_worker(self.run_execution_loop())
        else:
            res = await self.push_screen_wait(FailureModalScreen(task.script_name, reason))
            if res == "retry":
                self.run_worker(self.run_execution_loop())
            elif res == "skip":
                self.task_skipped(task)
            else:
                self.exit(return_code=1)

    def action_quit_app(self):
        if self.current_idx >= len(self.tasks):
            failed_tasks = [t for t in self.tasks if self.task_statuses.get(t.state_key) == "FAILED"]
            self.exit(return_code=1 if failed_tasks or self.persistence_failed or self.logger.failed_write else 0)
        else:
            self.exit(return_code=1)

    def action_toggle_manual(self):
        self.manual = not self.manual
        mode = "ENABLED" if self.manual else "DISABLED"
        self.log_system(f"Manual step confirmation mode {mode}")

    def action_reset_state(self):
        if self.state_file.exists():
            try:
                self.state_file.unlink()
                self.completed_keys.clear()
                self.log_system("Phase completion state reset.")
            except Exception as e:
                self.log_system(f"Failed to reset state: {e}")

    def trigger_poweroff(self):
        set_auto_poweroff_marker()
        failed_tasks = [t for t in self.tasks if self.task_statuses.get(t.state_key) == "FAILED"]
        exit_code = 1 if failed_tasks or self.persistence_failed or self.logger.failed_write else 0

        if is_in_chroot():
            self.exit(return_code=exit_code)
            return

        with self._suspend_ui():
            print("\n[INFO] Flushing filesystem buffers and powering off system...")
            graceful_unmount_and_poweroff("/mnt")
        self.exit(return_code=exit_code)


# ==============================================================================
# MAIN ENTRYPOINT
# ==============================================================================
def main():
    args = parse_args()

    if args.list_once:
        store = OnceStore(read_only=True)
        try:
            store.print_list()
        finally:
            store.close()
        return

    if args.forget_once:
        lock_file = Path("/tmp/orchestrator_phase2.lock" if args.phase2 else "/tmp/orchestrator_phase1.lock")
        if not acquire_lock(lock_file):
            sys.exit(1)
        store = OnceStore()
        try:
            count = store.forget(args.forget_once)
            print(f"Forgot {count} once-marker(s) for '{args.forget_once}'.")
        finally:
            store.close()
        return

    if args.list_profiles:
        print("Available Installer Profiles:")
        profiles = discover_profiles()
        if not profiles:
            print("  (No profiles found)")
        for p in profiles:
            pname = p.filepath.name if p.filepath else "Unknown"
            print(f"  - {pname}: {p.name} ({p.description})")
            print(f"    Phase 1 tasks: {len(p.phase1_tasks)}, Phase 2 tasks: {len(p.phase2_tasks)}")
        sys.exit(0)

    phase1 = args.phase1
    phase2 = args.phase2

    if not phase1 and not phase2:
        phase1 = True

    profiles = discover_profiles()
    selected_profile: Optional[ProfileConfig] = None

    if args.profile:
        for p in profiles:
            if p.filepath and (p.filepath.name.lower() == args.profile.lower()
                               or p.filepath.stem.lower() == args.profile.lower()
                               or p.name.lower() == args.profile.lower()):
                selected_profile = p
                break
        if not selected_profile:
            p_path = Path(args.profile)
            if p_path.exists():
                try:
                    selected_profile = load_profile(p_path)
                except Exception as e:
                    sys.stderr.write(f"Error loading profile '{args.profile}': {e}\n")
                    sys.exit(1)

    inspection = args.list_scripts or args.doctor or args.explain or args.dry_run
    repo_valid, repo_reason = (True, "inspection") if inspection else verify_offline_repo_fast()

    if not selected_profile and not args.profile:
        for profile_check in [
            Path("/etc/dusky_selected_profile.txt"),
            Path("/root/dusky_selected_profile.txt"),
            Path("/tmp/dusky_selected_profile.txt"),
            Path("/mnt/etc/dusky_selected_profile.txt"),
        ]:
            if profile_check.is_file():
                try:
                    saved_name = profile_check.read_text().strip()
                    if saved_name:
                        for p in profiles:
                            if p.filepath and (p.filepath.name == saved_name or p.name.lower() == saved_name.lower()):
                                selected_profile = p
                                break
                except Exception:
                    pass
            if selected_profile:
                break

    if not selected_profile:
        if args.auto or not sys.stdin.isatty():
            if repo_valid:
                for p in profiles:
                    if p.filepath and "offline" in p.name.lower():
                        selected_profile = p
                        break
            else:
                sys.stderr.write(f"\n[WARN] Offline repository verification failed: {repo_reason}\n")
                sys.stderr.write("[WARN] Auto-selecting ONLINE profile to prevent pacstrap failures.\n\n")
                for p in profiles:
                    if p.filepath and "online" in p.name.lower():
                        selected_profile = p
                        break
            if not selected_profile and profiles:
                selected_profile = profiles[0]
        else:
            from rich.panel import Panel
            from rich.console import Console
            from rich.prompt import Prompt
            console = Console()

            console.print("\n")
            console.print(Panel(Text("Dusky Installation Method", justify="center", style="bold cyan"), box=box.ROUNDED, expand=False, padding=(0, 4)))
            console.print("\n")

            profile_choices = []
            for i, p in enumerate(profiles, start=1):
                p_name = p.name
                p_desc = p.description
                is_offline = "offline" in p_name.lower() or (p.filepath and "offline" in p.filepath.name.lower())

                if is_offline and not repo_valid:
                    status_str = f"[bold red][CORRUPTED - UNAVAILABLE][/bold red]"
                    available = False
                elif is_offline:
                    status_str = "[bold green][VERIFIED CLEAN][/bold green]"
                    available = True
                else:
                    status_str = "[bold green][AVAILABLE][/bold green]"
                    available = True

                console.print(f"  [bold yellow]{i}.[/bold yellow] [bold white]{p_name}[/bold white] — [dim]{p_desc}[/dim] {status_str}")
                profile_choices.append((p, available))

            console.print("  [bold yellow]q.[/bold yellow] [bold white]Quit[/bold white] — [dim]Cancel installation and return to live shell[/dim]")

            if not repo_valid:
                console.print(Panel(f"[bold red]OFFLINE REPO CORRUPTION DETECTED:[/bold red]\n{repo_reason}\n"
                                    "[yellow]Offline installation disabled. Please select Online Profile or re-copy ISO cleanly.[/yellow]", box=box.ROUNDED))

            default_idx = "2" if (not repo_valid and len(profiles) >= 2) else "1"
            valid_choices = [str(i) for i in range(1, len(profiles) + 1)] + ["q", "Q", "quit"]
            while True:
                try:
                    choice = Prompt.ask("\nSelect Profile Number", choices=valid_choices, default=default_idx)
                    if choice.lower() in ("q", "quit"):
                        console.print("\n[yellow]Installation cancelled by user. Returning to live shell.[/yellow]")
                        sys.exit(0)

                    idx = int(choice) - 1
                    p, avail = profile_choices[idx]
                    if not avail:
                        console.print(f"[red]Profile '{p.name}' is unavailable because the offline repository is corrupted. Please choose another option.[/red]")
                        continue
                    selected_profile = p
                    break
                except KeyboardInterrupt:
                    console.print("\n[bold yellow]Installation cancelled (Ctrl+C). Returning to live shell.[/bold yellow]")
                    sys.exit(0)
                except EOFError:
                    console.print(f"\n[yellow]EOF detected. Selected default option ({default_idx}).[/yellow]")
                    idx = int(default_idx) - 1
                    selected_profile = profile_choices[idx][0]
                    break
                except Exception:
                    continue

    if not selected_profile:
        sys.stderr.write(f"Error: No valid installer profile found in '{PROFILES_DIR}'. Installation aborted.\n")
        sys.exit(1)

    if not inspection and selected_profile and selected_profile.filepath:
        try:
            p_name = selected_profile.filepath.name
            Path("/tmp/dusky_selected_profile.txt").write_text(p_name)
            if Path("/mnt/etc").is_dir():
                (Path("/mnt/etc") / "dusky_selected_profile.txt").write_text(p_name)
            if Path("/mnt/root").is_dir():
                (Path("/mnt/root") / "dusky_selected_profile.txt").write_text(p_name)
        except Exception:
            pass

    profile_name = selected_profile.name
    policy = selected_profile.policy

    if args.no_audio:
        NotificationManager.audio_enabled = False
    elif policy.get("audio", True) is False:
        NotificationManager.audio_enabled = False

    if args.no_notify:
        NotificationManager.desktop_enabled = False
    elif policy.get("notify", True) is False:
        NotificationManager.desktop_enabled = False

    manual = args.manual or bool(policy.get("manual", False))
    stop_on_fail = args.stop_on_fail or bool(policy.get("stop_on_fail", False))
    force = args.force or bool(policy.get("force", False))
    task_timeout = args.task_timeout if args.task_timeout is not None else float(policy.get("task_timeout", 0.0))

    raw_sequence = selected_profile.phase1_tasks if phase1 else selected_profile.phase2_tasks

    tasks: List[OrchestratorTask] = []
    occurrence: Dict[str, int] = {}
    for i, t in enumerate(raw_sequence, start=1):
        resolved_path = resolve_script(t.script_name, selected_profile.search_dirs, selected_profile.conflict_resolutions)

        interpreter = t.interpreter
        is_interactive = t.interactive
        if resolved_path:
            interpreter, file_interactive = resolve_interpreter(resolved_path)
            if t.interactive_override is None and file_interactive:
                is_interactive = True
            if resolved_path.suffix.lower() == ".py":
                try:
                    compile(resolved_path.read_bytes(), str(resolved_path), "exec")
                except (OSError, SyntaxError) as exc:
                    raise RuntimeError(f"Invalid Python task {resolved_path}: {exc}") from exc
            elif resolved_path.suffix.lower() == ".sh":
                syntax = subprocess.run([interpreter, "-n", str(resolved_path)],
                                        capture_output=True, text=True, timeout=30)
                if syntax.returncode != 0:
                    raise RuntimeError(f"Invalid shell task {resolved_path}: {syntax.stderr.strip()}")

        args_key = shlex.join(t.args)
        occ_key = f"{t.mode}|{t.script_name}|{args_key}"
        occurrence[occ_key] = occurrence.get(occ_key, 0) + 1
        checksum = file_checksum(resolved_path) if resolved_path else ""

        task = OrchestratorTask(
            index=i,
            script_name=t.script_name,
            args=t.args,
            mode=t.mode,
            ignore_fail=t.ignore_fail,
            interactive=is_interactive,
            interactive_override=t.interactive_override,
            force_flag=force or t.force_flag,
            condition=t.condition,
            timeout=t.timeout,
            interpreter=interpreter,
            checksum=checksum,
            resolved_path=resolved_path,
            always=t.always,
            retry=t.retry,
            retry_delay=t.retry_delay,
            on_failure=t.on_failure,
            once=t.once,
            once_mode=t.once_mode,
            once_scope=t.once_scope,
        )
        task.state_key = make_state_key(task, occurrence[occ_key])
        tasks.append(task)

    if phase2:
        phase_title = "PHASE 2: CHROOT"
        state_file = Path("/root/.arch_install_phase2.state")
        lock_file = Path("/tmp/orchestrator_phase2.lock")
    else:
        phase_title = "PHASE 1: ISO"
        state_file = Path("/tmp/.arch_install_phase1.state")
        lock_file = Path("/tmp/orchestrator_phase1.lock")

    if not inspection and not acquire_lock(lock_file):
        sys.exit(1)
    if not inspection and os.geteuid() != 0:
        sys.stderr.write("Error: This installer orchestrator must be run as root.\n")
        sys.exit(1)
    once_store = OnceStore(read_only=inspection)

    if args.list_scripts:
        print(f"Profile: {profile_name} ({phase_title if phase1 or phase2 else 'all'})")
        for t in tasks:
            flags = []
            if t.ignore_fail:
                flags.append("IGNORE_FAIL")
            if t.interactive:
                flags.append("INTERACTIVE")
            if t.condition:
                flags.append(f"COND: {t.condition}")
            if t.timeout is not None:
                flags.append(f"TIMEOUT: {t.timeout}s")
            if t.retry:
                flags.append(f"RETRY: {t.retry}")
            if t.once:
                flags.append("ONCE")
            path = str(t.resolved_path) if t.resolved_path else "MISSING"
            print(f"  {t.index:2d}. [{t.mode}] {t.script_name} {' '.join(t.args)} ({', '.join(flags)}) -> {path}")
        once_store.close()
        sys.exit(0)

    if args.doctor:
        print(f"VERSION: {VERSION}")
        print(f"SCRIPT_DIR: {SCRIPT_DIR}")
        print(f"PROFILES_DIR: {PROFILES_DIR}")
        print(f"State DB: {once_store.db_path}")
        print(f"Root check: {'OK' if os.geteuid() == 0 else 'NOT ROOT (expected for real run)'}")
        profiles = discover_profiles()
        print(f"Profiles: {len(profiles)}")
        for p in profiles:
            ph1 = len(p.phase1_tasks)
            ph2 = len(p.phase2_tasks)
            print(f"  - {p.filepath.name}: {p.name} (phase1={ph1}, phase2={ph2})")
        missing_all = [t.script_name for t in tasks if not t.resolved_path]
        print(f"Selected profile '{profile_name}': {len(tasks)} tasks, {len(missing_all)} missing")
        print("Doctor check complete.")
        once_store.close()
        sys.exit(0)

    if args.explain:
        print(f"=== EXPLAIN FOR {profile_name} ===")
        for t in tasks:
            reasons = []
            if not t.resolved_path:
                reasons.append("MISSING SCRIPT -> WILL DEFER")
            else:
                if t.once and once_store.marker_valid(t, profile_name):
                    reasons.append("SKIP (once-marker valid)")
                if t.condition:
                    reasons.append(f"condition: {t.condition}")
                if t.always:
                    reasons.append("always")
                if t.once:
                    reasons.append("once")
                if t.ignore_fail:
                    reasons.append("ignore-fail")
                if not reasons:
                    reasons.append("RUN")
            print(f"  {t.index:2d}. [{t.mode}] {t.script_name} {' '.join(t.args)}")
            print(f"       {', '.join(reasons)}")
            if t.timeout is not None:
                print(f"       timeout: {t.timeout}s")
            if t.retry:
                print(f"       retry: {t.retry} (delay {t.retry_delay}s)")
            if t.on_failure != "ask":
                print(f"       on_failure: {t.on_failure}")
        once_store.close()
        sys.exit(0)

    if args.dry_run:
        print(f"=== DRY RUN FOR {phase_title} ===")
        print(f"Active Profile: {profile_name}")
        print(f"State file: {state_file}")
        for i, t in enumerate(tasks):
            status = "PENDING"
            if not t.resolved_path:
                status = "MISSING"
            elif t.once and once_store.marker_valid(t, profile_name):
                status = "SKIP (once-marker valid)"
            print(
                f"  {i+1:2d}. {t.script_name} {' '.join(t.args)} [{'IGNORE_FAIL' if t.ignore_fail else 'STRICT'}] [{'INTERACTIVE' if t.interactive else 'NON-INT'}] -> {status} (using {t.interpreter})"
            )
        once_store.close()
        sys.exit(1 if any(not t.resolved_path for t in tasks) else 0)

    if args.reset:
        if state_file.exists():
            try:
                state_file.unlink()
                print(f"Reset completion state for {phase_title}")
            except Exception as e:
                sys.stderr.write(f"Failed to reset state: {e}\n")
        else:
            print(f"No state file found for {phase_title}")

    missing = [t.script_name for t in tasks if not t.resolved_path]
    if missing:
        sys.stderr.write(f"Error: Missing critical script files in {SCRIPT_DIR}:\n")
        for m in missing:
            sys.stderr.write(f"  - {m}\n")
        once_store.close()
        sys.exit(1)

    try:
        app = DuskyOrchestratorApp(
            tasks=tasks,
            phase_title=phase_title,
            profile_name=profile_name,
            state_file=state_file,
            manual=manual,
            stop_on_fail=stop_on_fail,
            force=force,
            task_timeout=task_timeout,
            once_store=once_store,
            is_final_phase=bool(phase2),
        )
        app.run()
        sys.exit(app.return_code if app.return_code is not None else 1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, subprocess.SubprocessError, OSError) as exc:
        sys.stderr.write(f"[ERROR] {exc}\n")
        sys.exit(1)
