#!/usr/bin/env python3
# DUSKY_BOOTSTRAP_PACKAGES: python python-textual python-rich git
# dusky_interactive=true
# ==============================================================================
# DUSKY ARCH LINUX MASTER ORCHESTRATOR
# ==============================================================================
# Target: Arch Linux bleeding edge | Python 3.14.7+ | Textual 8.2.8+ | systemd 262+
# ==============================================================================
import sys

if sys.version_info < (3, 14, 7):
    sys.stderr.write("[FATAL] Python 3.14.7+ is required.\n")
    sys.exit(1)

import argparse
import asyncio
import atexit
import base64
import codecs
import datetime
import errno
import fcntl
import functools
import hashlib
import json
import math
import os
import pty
import pwd
import re
import select
import shlex
import shutil
import signal
import sqlite3
import struct
import subprocess
import tempfile
import termios
import time
import tomllib
import uuid
from collections import deque
from contextlib import suppress, contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Literal

VERSION = "19.0.2"


def parse_command_line() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dusky Arch Linux Orchestrator",
        epilog="Example: ./orchestrator.py --profile 01_main",
        allow_abbrev=False,
    )

    parser.add_argument(
        "--profile",
        "-p",
        help="Execute specific profile (name, stem, filename, or number)",
    )
    parser.add_argument("--list", action="store_true", help="List all available profiles and exit")
    parser.add_argument("--list-scripts", action="store_true", help="List sequence of selected profile and exit")
    parser.add_argument("--reset", action="store_true", help="Reset state for selected profile and exit")
    parser.add_argument("--reset-and-run", action="store_true", help="Reset state for selected profile, then run")
    parser.add_argument("--list-once", action="store_true", help="List persistent once markers and exit")
    parser.add_argument(
        "--forget-once",
        action="append",
        default=[],
        metavar="SCRIPT",
        help="Forget persistent once marker(s) for a script name or path. Can be repeated.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate everything but do not execute scripts")
    parser.add_argument("--explain", action="store_true", help="Explain run decisions and exit")
    parser.add_argument("--force", action="store_true", help="Export DUSKY_FORCE=1 and pass --force to scripts")
    parser.add_argument("--manual", "-m", action="store_true", help="Prompt before executing every script")
    parser.add_argument("--stop-on-fail", action="store_true", help="Halt execution immediately if a script fails")
    parser.add_argument("--no-git-update", action="store_true", help="Skip git self-update")
    parser.add_argument("--git-update-only", action="store_true", help="Run git self-update and exit")
    parser.add_argument("--offline", action="store_true", help="Skip network-dependent git update")
    parser.add_argument("--yes", "-y", action="store_true", help="Assume yes for destructive git update prompts")
    parser.add_argument("--sudo-password", help="Provide sudo password non-interactively")
    parser.add_argument("--sudo-password-file", help="Read sudo password from file")
    parser.add_argument("--task-timeout", type=float, default=0.0, help="Per-task timeout in seconds (0 disables)")
    parser.add_argument("--allow-root", action="store_true", help="Allow running as root (not recommended)")
    parser.add_argument("--ascii", action="store_true", help="Use ASCII symbols instead of Unicode")
    parser.add_argument("--no-audio", action="store_true", help="Disable audio notifications")
    parser.add_argument("--no-notify", action="store_true", help="Disable desktop notifications")
    parser.add_argument("--no-inhibit", action="store_true", help="Do not inhibit sleep/idle")
    parser.add_argument("--doctor", action="store_true", help="Run environment diagnostics and exit")
    parser.add_argument("--version", action="version", version=f"Dusky Orchestrator {VERSION}")

    args = parser.parse_args()
    modes = [name for name in ("list", "list_scripts", "reset", "reset_and_run",
                              "list_once", "forget_once", "dry_run", "explain",
                              "git_update_only", "doctor") if getattr(args, name)]
    if len(modes) > 1:
        parser.error("Conflicting modes: " + ", ".join("--" + name.replace("_", "-") for name in modes))
    if not math.isfinite(args.task_timeout) or args.task_timeout < 0:
        parser.error("--task-timeout must be finite and nonnegative")
    return args


EARLY_ARGS = parse_command_line() if __name__ == "__main__" else None


try:
    from rich.console import Console
    from rich.markup import escape
    from rich.text import Text
    from textual import work, on, events
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, Horizontal, Vertical
    from textual.screen import ModalScreen
    from textual.widgets import (
        Static,
        RichLog,
        ProgressBar,
        Button,
        Label,
        Tree,
        Input,
        OptionList,
        ContentSwitcher,
    )
    from textual.widgets.option_list import Option
    from textual.widgets.tree import TreeNode
except ImportError as exc:
    sys.stderr.write(f"[FATAL] Missing Python dependencies: {exc}\n")
    sys.stderr.write("Install: python-textual python-rich\n")
    sys.exit(8)

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
            raise SystemExit(f"[FATAL] Cannot load global config {config_path}: {e}") from e
    return {}


def normalize_global_config(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Global config must be a TOML table")
    table_names = ("ui", "paths", "logging", "execution", "conditions", "notifications", "sudo", "git", "prompts")
    for name in table_names:
        table = raw.get(name, {})
        if not isinstance(table, dict):
            raise ValueError(f"[{name}] must be a table")
    specs = {
        "ui": {"ascii_mode": bool, "left_pane_width": int, "max_log_lines": int, "max_deque_lines": int},
        "paths": {"documents_dir": str, "namespace": str, "lock_file": str, "askpass_prefix": str,
                  "state_subdir": str, "logs_subdir": str, "backups_subdir": str},
        "logging": {"enabled": bool, "write_task_logs": bool, "write_reports": bool},
        "execution": {"disk_space_reserve_bytes": int, "db_busy_timeout": int, "default_interpreter": str},
        "sudo": {"heartbeat_interval": int},
        "git": {"upstream_branch": str, "upstream_ref": str, "default_repo_url": str,
                "fetch_max_attempts": int, "timeout_fetch": int, "backup_retention": int},
        "prompts": {"cooldown": (int, float)},
    }
    positive = {"left_pane_width", "max_log_lines", "max_deque_lines",
                "db_busy_timeout", "heartbeat_interval", "fetch_max_attempts", "timeout_fetch",
                "backup_retention"}
    for table_name, keys in specs.items():
        for key, typ in keys.items():
            value = raw.get(table_name, {}).get(key)
            if value is None:
                continue
            if not isinstance(value, typ) or (typ is int and isinstance(value, bool)):
                raise ValueError(f"[{table_name}].{key} has the wrong type")
            if isinstance(value, str) and (not value or "\0" in value):
                raise ValueError(f"[{table_name}].{key} must be a nonempty string without NUL")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if not math.isfinite(value) or (key in positive and value <= 0) or value < 0:
                    raise ValueError(f"[{table_name}].{key} is out of range")
    for table, names in {
        "conditions": ("package_check_cmd", "service_active_cmd", "user_service_active_cmd"),
        "sudo": ("env_keep",), "git": ("env_strip",),
        "notifications": ("audio_players",), "ui": ("theme_paths",),
    }.items():
        for name in names:
            val = raw.get(table, {}).get(name)
            if val is not None and (not isinstance(val, list) or any(not isinstance(v, str) or not v for v in val)):
                raise ValueError(f"[{table}].{name} must be a list of nonempty strings")
    for table, names in {"ui": ("default_palette", "unicode_symbols", "ascii_symbols"),
                         "execution": ("extension_interpreters",),
                         "conditions": ("gpu_vendor_map",), "notifications": ("sound_map",),
                         "git": ("env_inject",)}.items():
        for name in names:
            val = raw.get(table, {}).get(name)
            if val is not None and (not isinstance(val, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in val.items())):
                raise ValueError(f"[{table}].{name} must be a string table")
    rules = raw.get("prompts", {}).get("rules", [])
    if not isinstance(rules, list) or any(not isinstance(r, dict) or
        not all(isinstance(r.get(k), str) for k in ("name", "pattern", "kind")) or
        r["kind"] not in ("password", "yes", "no", "enter") for r in rules):
        raise ValueError("[prompts].rules contains an invalid prompt rule")
    return raw


try:
    GLOBAL_CONFIG = normalize_global_config(load_global_config())
except ValueError as exc:
    raise SystemExit(f"[FATAL] {exc}") from exc

ASCII_MODE = GLOBAL_CONFIG.get("ui", {}).get("ascii_mode", False)

UNICODE_SYMBOLS = GLOBAL_CONFIG.get(
    "ui",
    {},
).get(
    "unicode_symbols",
    {
        "logo": "◈",
        "completed": "✔",
        "running": "●",
        "failed": "✘",
        "skipped": "○",
        "pending": "·",
        "sep": "│",
        "timing": "⚡",
        "matrix": "⬢",
        "preflight": "⚙",
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
        "timing": "TIME",
        "matrix": "MAT",
        "preflight": "SYS",
    },
)


def S(key: str) -> str:
    return ASCII_SYMBOLS.get(key, key) if ASCII_MODE else UNICODE_SYMBOLS.get(key, key)


# ==============================================================================
# VERSION / RUNTIME GATES
# ==============================================================================
def version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in re.split(r"[^0-9]+", value.strip()):
        if part:
            parts.append(int(part))
    return tuple(parts)


def check_runtime_versions() -> None:
    if sys.version_info < (3, 14, 7):
        sys.stderr.write("[FATAL] Python 3.14.7+ is required.\n")
        sys.exit(1)

    try:
        textual_version = importlib_metadata.version("textual")
        parsed = (version_tuple(textual_version) + (0, 0, 0))[:3]
        if parsed < (8, 2, 8):
            sys.stderr.write(
                f"[FATAL] Textual 8.2.8+ is required. Installed: {textual_version}\n"
            )
            sys.exit(1)
    except importlib_metadata.PackageNotFoundError:
        sys.stderr.write("[FATAL] Textual is not installed.\n")
        sys.exit(1)


def ensure_not_root(allow_root: bool) -> None:
    if os.geteuid() != 0:
        return
    if allow_root:
        return

    if os.environ.get("SUDO_USER"):
        sys.stderr.write(
            "[FATAL] Run this orchestrator as your normal user, not via sudo.\n"
            "       If you truly intend to run as root, pass --allow-root.\n"
        )
    else:
        sys.stderr.write(
            "[FATAL] Running as root is not intended. Use --allow-root to force.\n"
        )
    sys.exit(13)


# ==============================================================================
# XDG / PATHS
# ==============================================================================
@functools.cache
def target_user_pw() -> pwd.struct_passwd:
    if os.geteuid() == 0:
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user and sudo_user != "root":
            with suppress(KeyError):
                return pwd.getpwnam(sudo_user)
        return pwd.getpwuid(0)
    return pwd.getpwuid(os.getuid())


def user_home() -> Path:
    env_home = os.environ.get("DUSKY_WORK_TREE") or os.environ.get("DUSKY_HOME")
    if env_home:
        return Path(env_home).resolve()
    return Path(target_user_pw().pw_dir)


def xdg_state_home() -> Path:
    default = user_home() / ".local" / "state"
    if os.geteuid() == 0 and target_user_pw().pw_uid != 0:
        return default
    env = os.environ.get("XDG_STATE_HOME")
    return Path(env).expanduser() if env else default


def xdg_data_home() -> Path:
    default = user_home() / ".local" / "share"
    if os.geteuid() == 0 and target_user_pw().pw_uid != 0:
        return default
    env = os.environ.get("XDG_DATA_HOME")
    return Path(env).expanduser() if env else default


def xdg_cache_home() -> Path:
    default = user_home() / ".cache"
    if os.geteuid() == 0 and target_user_pw().pw_uid != 0:
        return default
    env = os.environ.get("XDG_CACHE_HOME")
    return Path(env).expanduser() if env else default


def ensure_dir(path: Path, mode: int = 0o700) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        path.chmod(mode)
    return path


def safe_dir(primary: Path, fallback: Path, mode: int = 0o700) -> Path:
    try:
        return ensure_dir(primary, mode)
    except OSError:
        return ensure_dir(fallback, mode)


@functools.cache
def runtime_dir() -> Path:
    pw = target_user_pw()
    candidates: list[Path] = []
    ns = GLOBAL_CONFIG.get("paths", {}).get("namespace", "dusky")

    if os.geteuid() == 0 and pw.pw_uid != 0:
        candidates.append(Path(f"/run/user/{pw.pw_uid}") / ns)
    else:
        env = os.environ.get("XDG_RUNTIME_DIR")
        if env:
            candidates.append(Path(env) / ns)
        candidates.append(Path(f"/run/user/{pw.pw_uid}") / ns)

    candidates.append(Path(tempfile.gettempdir()) / f"{ns}-{pw.pw_uid}" / "run")

    for candidate in candidates:
        try:
            return ensure_dir(candidate, 0o700)
        except OSError:
            continue

    return ensure_dir(Path.cwd() / f".{ns}-run", 0o700)


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
        sys.stderr.write(f"[FATAL] Cannot create required Documents directory {path}: {e}\n")
        sys.exit(1)
    return path


@functools.cache
def state_dir() -> Path:
    return _documents_subdir(GLOBAL_CONFIG.get("paths", {}).get("state_subdir", "state"))


def state_dir_path() -> Path:
    """Find persistent state without creating directories during inspection."""
    paths = GLOBAL_CONFIG.get("paths", {})
    docs = Path(paths.get("documents_dir", "Documents")).expanduser()
    docs = docs if docs.is_absolute() else user_home() / docs
    sub = Path(paths.get("state_subdir", "state")).expanduser()
    return sub if sub.is_absolute() else docs / sub


@functools.cache
def logs_dir() -> Path:
    return _documents_subdir(GLOBAL_CONFIG.get("paths", {}).get("logs_subdir", "logs"))


@functools.cache
def backups_dir() -> Path:
    return _documents_subdir(GLOBAL_CONFIG.get("paths", {}).get("backups_subdir", "dusky_backups"))


@functools.cache
def cache_dir() -> Path:
    pw = target_user_pw()
    ns = GLOBAL_CONFIG.get("paths", {}).get("namespace", "dusky")
    return safe_dir(
        xdg_cache_home() / ns,
        Path(tempfile.gettempdir()) / f"{ns}-{pw.pw_uid}" / "cache",
    )


@functools.cache
def askpass_dir() -> Path:
    return ensure_dir(runtime_dir() / "askpass", 0o700)


def lock_path() -> Path:
    lock_name = GLOBAL_CONFIG.get("paths", {}).get("lock_file", "orchestrator.lock")
    p = Path(lock_name).expanduser()
    return p if p.is_absolute() else runtime_dir() / p


# ==============================================================================
# REGEX
# ==============================================================================
_INTERACTIVE_RE = re.compile(
    r"^\s*#\s*dusky_interactive\s*=\s*(?:true|1)\b",
    re.IGNORECASE,
)
_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
ANSI_STRIP_REGEX = re.compile(
    r"\x1B(?:[@-Z\\_-]|\[[0-?]*[ -/]*[@-~]|\][^\x1b]*(?:\x07|\x1B\\))"
)
PCT_REGEX = re.compile(r"(?<!\d)(?:100(?:\.0+)?|\d{1,2}(?:\.\d+)?)%")
SPEED_ETA_REGEX = re.compile(
    r"Total\s*\(\s*\d+\s*/\s*\d+\s*\).*?(\d+(?:\.\d+)?\s*[KMG]?i?B/s)\s+([\d:]+)",
    re.IGNORECASE,
)
ALT_SPEED_ETA_REGEX = re.compile(
    r"(\d+(?:\.\d+)?\s*[KMG]?i?B/s)\s+([\d:]+)",
    re.IGNORECASE,
)
BRACKET_NEWLINE_RE = re.compile(r"[\r\n]+")
SINGLE_NEWLINE_RE = re.compile(r"[\r\n]")


def _build_prompt_rules() -> list[tuple[str, re.Pattern[str], str]]:
    default_rules = [
        ("sudo_password", r"(?i)(\[sudo\] password for [^:]+:|^\s*Password:\s*$|Password:\s*$)", "password"),
        ("pgp_import", r"(?i)(::\s*Import PGP key.*\?\s*\[Y/n\]|::\s*Append key\?.*\[Y/n\]|Import PGP key.*\?\s*\[Y/n\])", "yes"),
        ("pacman_proceed", r"(?i)::\s*(Proceed with (?:installation|download|upgrade)|Continue (?:installation|download|upgrade)).*\?\s*\[Y/n\]", "yes"),
        ("pacman_replace", r"(?i)::\s*Replace\s+.*\?\s*\[Y/n\]", "yes"),
        ("pacman_remove_conflict", r"(?i)::\s*Remove conflicting file.*\?\s*\[Y/n\]", "yes"),
        ("aur_proceed", r"(?i)(Proceed with installation\?|Continue building\?|Continue installing\?|::\s*Proceed with (?:installation|download|build).*\?\s*\[Y/n\])", "yes"),
        ("generic_yes", r"(?i)\[Y/n\]|\(Y/n\)|\[y/N\]|\(y/N\)", "yes"),
    ]
    config_rules = GLOBAL_CONFIG.get("prompts", {}).get("rules", None)
    rules = []
    items_to_parse = config_rules if config_rules is not None else default_rules
    for item in items_to_parse:
        if isinstance(item, dict):
            name, pattern, kind = item["name"], item["pattern"], item["kind"]
        else:
            name, pattern, kind = item
        rules.append((name, re.compile(pattern, re.MULTILINE), kind))
    return rules


PROMPT_RULES: list[tuple[str, re.Pattern[str], str]] = _build_prompt_rules()


# ==============================================================================
# MODEL
# ==============================================================================
class TaskStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    RUNNING = "running"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(slots=True, kw_only=True)
class OrchestratorTask:
    raw_entry: str
    mode: str
    script_name: str
    args: list[str] = field(default_factory=list)
    ignore_fail: bool = False
    interactive: bool = False
    interactive_override: bool | None = None
    force_flag: bool = False
    condition: str | None = None
    timeout: float | None = None
    index: int = 0
    resolved_path: Path | None = None
    description: str = ""
    interpreter: str = "bash"
    interpreter_args: list[str] = field(default_factory=list)
    checksum: str = ""
    state_key: str = ""
    status: TaskStatus = TaskStatus.PENDING
    error_msg: str | None = None
    duration: float = 0.0

    always: bool = False
    retry: int = 0
    retry_delay: float = 1.0
    on_failure: str = "ask"
    once: bool = False
    once_mode: str = "content"
    once_scope: str = "profile"


@dataclass(slots=True, kw_only=True)
class ProfileConfig:
    filepath: Path
    name: str
    description: str = ""
    post_script_delay: int = 0
    git_enabled: bool = False
    git_dir: str = "~/dusky"
    git_work_tree: str = "~/"
    git_remote: str = "origin"
    git_repo_url: str = "https://github.com/dusklinux/dusky"
    search_dirs: list[str] = field(default_factory=list)
    conflict_resolutions: dict[str, str] = field(default_factory=dict)
    tasks: list[OrchestratorTask] = field(default_factory=list)
    policy: dict = field(default_factory=dict)


# ==============================================================================
# UTILITIES
# ==============================================================================
def resolve_home(path_str: str) -> Path:
    raw = path_str.strip()
    if raw.startswith("~/") or raw == "~":
        p = user_home() / raw[2:] if raw.startswith("~/") else user_home()
    else:
        p = Path(os.path.expandvars(raw)).expanduser()
    if not p.is_absolute():
        p = SCRIPT_DIR / p
    return p


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", str(name)).strip("._")
    return cleaned or "unnamed"


def now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_ts() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def file_checksum(path: Path) -> str:
    try:
        h = hashlib.blake2b(digest_size=16)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def make_state_key(task: OrchestratorTask, occurrence: int) -> str:
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
# STATE STORE
# ==============================================================================
class StateStore:
    DONE = {
        "completed",
        "skipped",
        "ignored",
        "manual",
        "completed_once",
    }

    def __init__(self, profile: ProfileConfig, read_only: bool = False):
        self.path = (state_dir_path() if read_only else state_dir()) / f"{safe_filename(profile.name)}.db"
        self.read_only = read_only
        busy_timeout = GLOBAL_CONFIG.get("execution", {}).get("db_busy_timeout", 5000)
        if read_only:
            if self.path.exists():
                self.conn = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True,
                                            timeout=busy_timeout / 1000.0)
            else:
                self.conn = sqlite3.connect(":memory:")
        else:
            self.conn = sqlite3.connect(self.path, timeout=busy_timeout / 1000.0)
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA synchronous=NORMAL;")
        if read_only and self.path.exists():
            return
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS state (
                state_key TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                script TEXT,
                checksum TEXT,
                exit_code INTEGER,
                note TEXT,
                updated TEXT,
                duration REAL DEFAULT 0.0
            )
            """
        )
        cur = self.conn.execute("PRAGMA table_info(state);")
        columns = [row[1] for row in cur.fetchall()]
        if "duration" not in columns:
            self.conn.execute("ALTER TABLE state ADD COLUMN duration REAL DEFAULT 0.0;")
        self.conn.commit()

    def statuses(self) -> dict[str, str]:
        cur = self.conn.execute("SELECT state_key, status FROM state")
        return {str(k): str(v) for k, v in cur.fetchall()}

    def durations(self) -> dict[str, float]:
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(state)")}
        if "duration" not in columns:
            return {}
        cur = self.conn.execute("SELECT state_key, duration FROM state")
        return {str(k): float(v or 0.0) for k, v in cur.fetchall()}

    @classmethod
    def is_done(cls, status: str | None) -> bool:
        return bool(status) and status in cls.DONE

    def mark(
        self,
        task: OrchestratorTask,
        status: str,
        exit_code: int | None = None,
        note: str = "",
    ) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO state
                (state_key, status, script, checksum, exit_code, note, updated, duration)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.state_key,
                status,
                task.script_name,
                task.checksum,
                exit_code,
                note,
                now_iso(),
                float(task.duration),
            ),
        )
        self.conn.commit()

    def reset(self) -> None:
        with suppress(Exception):
            self.conn.close()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self.path}{suffix}").unlink(missing_ok=True)

    def close(self) -> None:
        with suppress(Exception):
            self.conn.close()


def reset_state_for_profile(profile: ProfileConfig) -> None:
    base_path = state_dir() / f"{safe_filename(profile.name)}.db"
    for suffix in ("", "-wal", "-shm"):
        Path(f"{base_path}{suffix}").unlink(missing_ok=True)
    print(f"Reset state for {profile.name} at {base_path}")


class OnceStore:

    def __init__(self, read_only: bool = False) -> None:
        self.read_only = read_only
        self.path = (state_dir_path() if read_only else state_dir()) / "once.db"
        busy_timeout = GLOBAL_CONFIG.get("execution", {}).get("db_busy_timeout", 5000)
        if read_only:
            if self.path.exists():
                self.conn = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True,
                                            timeout=busy_timeout / 1000.0)
            else:
                self.conn = sqlite3.connect(":memory:")
        else:
            self.conn = sqlite3.connect(self.path, timeout=busy_timeout / 1000.0)
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA synchronous=NORMAL;")
        if read_only and self.path.exists():
            columns = {row[1] for row in self.conn.execute("PRAGMA table_info(once_markers)")}
            self._notified_select = "notified_checksum" if "notified_checksum" in columns else "''"
            return
        self.conn.execute(
            """
CREATE TABLE IF NOT EXISTS once_markers (
    marker_key TEXT PRIMARY KEY,
    profile TEXT NOT NULL,
    scope TEXT NOT NULL,
    mode TEXT NOT NULL,
    script_name TEXT NOT NULL,
    args_key TEXT NOT NULL,
    resolved_path TEXT,
    checksum TEXT,
    once_mode TEXT NOT NULL,
    exit_code INTEGER,
    run_id TEXT,
    version TEXT,
    created TEXT,
    updated TEXT
)
"""
        )
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(once_markers)")}
        if "notified_checksum" not in columns:
            self.conn.execute("ALTER TABLE once_markers ADD COLUMN notified_checksum TEXT DEFAULT '';")
        self._notified_select = "notified_checksum"

        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_once_script ON once_markers(script_name);"
        )
        self.conn.commit()

    @staticmethod

    def make_key(task: OrchestratorTask, profile_name: str) -> str:
        scope = task.once_scope if task.once_scope in ("profile", "global") else "profile"
        profile_part = "__global__" if scope == "global" else profile_name
        material = "|".join(
            [
                "once",
                scope,
                profile_part,
                task.mode,
                task.script_name,
                shlex.join(task.args),
            ]
        ).encode("utf-8")
        return hashlib.blake2b(material, digest_size=16).hexdigest()

    def marker_valid(self, task: OrchestratorTask, profile_name: str) -> bool:
        return self.check_marker_status(task, profile_name) in ("skip", "notify_sealed")

    def check_marker_status(self, task: OrchestratorTask, profile_name: str) -> Literal["run", "skip", "notify_sealed"]:
        if not task.once:
            return "run"

        key = self.make_key(task, profile_name)
        cur = self.conn.execute(
            f"SELECT checksum, once_mode, {self._notified_select} FROM once_markers WHERE marker_key = ?",
            (key,),
        )
        row = cur.fetchone()
        if row is None:
            return "run"

        stored_checksum, stored_mode, notified_checksum = row

        if task.once_mode == "forever" or stored_mode == "forever":
            return "skip"

        if task.once_mode == "sealed" or stored_mode == "sealed":
            if bool(task.checksum) and stored_checksum != task.checksum:
                if notified_checksum != task.checksum:
                    return "notify_sealed"
            return "skip"

        if bool(task.checksum) and stored_checksum == task.checksum:
            return "skip"

        return "run"

    def mark_sealed_notified(self, task: OrchestratorTask, profile_name: str) -> None:
        key = self.make_key(task, profile_name)
        self.conn.execute(
            "UPDATE once_markers SET notified_checksum = ?, updated = ? WHERE marker_key = ?",
            (task.checksum, now_iso(), key),
        )
        self.conn.commit()

    def mark_success(
        self,
        task: OrchestratorTask,
        profile_name: str,
        exit_code: int | None = None,
        run_id: str = "",
    ) -> None:
        if not task.once:
            return

        key = self.make_key(task, profile_name)
        args_key = shlex.join(task.args)

        self.conn.execute(
            """
INSERT INTO once_markers (
    marker_key,
    profile,
    scope,
    mode,
    script_name,
    args_key,
    resolved_path,
    checksum,
    once_mode,
    exit_code,
    run_id,
    version,
    created,
    updated
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(marker_key) DO UPDATE SET
    profile=excluded.profile,
    scope=excluded.scope,
    mode=excluded.mode,
    script_name=excluded.script_name,
    args_key=excluded.args_key,
    resolved_path=excluded.resolved_path,
    checksum=excluded.checksum,
    once_mode=excluded.once_mode,
    exit_code=excluded.exit_code,
    run_id=excluded.run_id,
    version=excluded.version,
    updated=excluded.updated
""",
            (
                key,
                profile_name,
                task.once_scope,
                task.mode,
                task.script_name,
                args_key,
                str(task.resolved_path),
                task.checksum,
                task.once_mode,
                exit_code,
                run_id,
                VERSION,
                now_iso(),
                now_iso(),
            ),
        )
        self.conn.commit()

    def forget(self, script: str) -> int:
        script = script.strip()
        if not script:
            return 0

        escaped = script.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        cur = self.conn.execute(
            """
DELETE FROM once_markers
WHERE script_name = ?
   OR resolved_path = ?
   OR script_name LIKE ? ESCAPE '\\'
""",
            (script, script, f"%/{escaped}"),
        )
        self.conn.commit()
        return cur.rowcount

    def list_markers(self) -> list[dict[str, object]]:
        cur = self.conn.execute(
            """
SELECT
    profile,
    scope,
    mode,
    script_name,
    args_key,
    resolved_path,
    checksum,
    once_mode,
    exit_code,
    run_id,
    updated
FROM once_markers
ORDER BY profile, script_name, args_key
"""
        )

        rows: list[dict[str, object]] = []
        for row in cur.fetchall():
            rows.append(
                {
                    "profile": row[0],
                    "scope": row[1],
                    "mode": row[2],
                    "script_name": row[3],
                    "args_key": row[4],
                    "resolved_path": row[5],
                    "checksum": row[6],
                    "once_mode": row[7],
                    "exit_code": row[8],
                    "run_id": row[9],
                    "updated": row[10],
                }
            )
        return rows

    def print_list(self) -> None:
        rows = self.list_markers()
        if not rows:
            print("No persistent once markers found.")
            return

        print(f"Persistent once markers ({len(rows)}):")
        for i, row in enumerate(rows, start=1):
            print(f"{i:3d}. [{row['mode']}] {row['script_name']}")
            print(f"     profile:   {row['profile']}")
            print(f"     scope:     {row['scope']}")
            print(f"     args:      {row['args_key']}")
            print(f"     path:      {row['resolved_path']}")
            print(f"     mode:      {row['once_mode']}")
            print(f"     checksum:  {row['checksum']}")
            print(f"     exit_code: {row['exit_code']}")
            print(f"     run_id:    {row['run_id']}")
            print(f"     updated:   {row['updated']}")
            print()

    def close(self) -> None:
        with suppress(Exception):
            self.conn.close()


# ==============================================================================
# LOGGER
# ==============================================================================
class RunLogger:

    def __init__(self, profile: ProfileConfig, run_id: str):
        log_config = GLOBAL_CONFIG.get("logging", {})
        self.enabled = log_config.get("enabled", True)
        self.write_task_logs = log_config.get("write_task_logs", True)
        self.write_reports = log_config.get("write_reports", True)

        self.root: Path | None = None
        self.main_path: Path | None = None
        self._main = None
        self._task_files: dict[str, object] = {}
        self._task_counts: dict[str, int] = {}
        self.run_id = run_id
        self.failed_write = False

        if not self.enabled:
            return

        try:
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.root = logs_dir() / f"{stamp}_{safe_filename(profile.name)}_{run_id}"
            ensure_dir(self.root, 0o700)
            self.main_path = self.root / "orchestrator.log"
            self._main = open(self.main_path, "a", encoding="utf-8", errors="replace")
            self.system(f"Logging started for profile: {profile.name}")
            self.system(f"Run ID: {run_id}")
        except OSError as e:
            sys.stderr.write(f"[FATAL] Cannot create log directory or main log file under {logs_dir()}: {e}\n")
            sys.exit(1)

    def system(self, msg: str) -> None:
        if not self.enabled or self._main is None:
            return
        try:
            self._main.write(f"[{now_ts()}] {msg}\n")
            self._main.flush()
        except OSError as exc:
            self.failed_write = True
            sys.stderr.write(f"[WARN] Main log write failed: {exc}\n")

    def task_log_path(self, task: OrchestratorTask) -> Path:
        if self.root is None:
            return Path("/dev/null")
        return self.root / f"{task.index:03d}_{safe_filename(task.script_name)}.log"

    def open_task(self, task: OrchestratorTask, cmd: list[str]) -> None:
        if not self.enabled or not self.write_task_logs:
            return

        if task.state_key in self._task_files:
            self.write_task(task, f"[{now_ts()}] RETRY")
            return

        try:
            f = open(self.task_log_path(task), "a", encoding="utf-8", errors="replace")
            f.write(f"[{now_ts()}] TASK START: {task.script_name}\n")
            f.write(f"[{now_ts()}] MODE: {task.mode}\n")
            f.write(f"[{now_ts()}] PATH: {task.resolved_path}\n")
            f.write(f"[{now_ts()}] INTERPRETER: {task.interpreter or 'direct'}\n")
            f.write(f"[{now_ts()}] ARGS: {shlex.join(task.args)}\n")
            f.write(f"[{now_ts()}] COMMAND: {shlex.join(cmd)}\n")
            f.write(f"[{now_ts()}] CONDITION: {task.condition or 'always'}\n")
            f.write(f"[{now_ts()}] ALWAYS: {task.always}\n")
            f.write(f"[{now_ts()}] ONCE: {task.once}\n")
            f.write(f"[{now_ts()}] ONCE_MODE: {task.once_mode}\n")
            f.write(f"[{now_ts()}] ONCE_SCOPE: {task.once_scope}\n")
            f.write(f"[{now_ts()}] RETRY: {task.retry}\n")
            f.write(f"[{now_ts()}] ON_FAILURE: {task.on_failure}\n")
            f.flush()
            self._task_files[task.state_key] = f
            self._task_counts[task.state_key] = 0
        except OSError as exc:
            self.failed_write = True
            sys.stderr.write(f"[WARN] Task log open failed: {exc}\n")

    def write_task(self, task: OrchestratorTask, line: str) -> None:
        if not self.enabled or not self.write_task_logs:
            return
        f = self._task_files.get(task.state_key)
        if f is None:
            return
        try:
            f.write(line + "\n")
            count = self._task_counts.get(task.state_key, 0) + 1
            self._task_counts[task.state_key] = count
            if count % 25 == 0:
                f.flush()
        except OSError as exc:
            self.failed_write = True
            sys.stderr.write(f"[WARN] Task log write failed: {exc}\n")

    def close_task(
        self,
        task: OrchestratorTask,
        status: str = "",
        exit_code: int | None = None,
        duration: float = 0.0,
    ) -> None:
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
            sys.stderr.write(f"[WARN] Task log close failed: {exc}\n")
        finally:
            with suppress(OSError):
                f.close()

    def write_report(
        self,
        profile: ProfileConfig,
        tasks: list[OrchestratorTask],
        statuses: dict[str, str],
        counters: dict[str, int],
    ) -> None:
        if not self.enabled or not self.write_reports or self.root is None:
            return

        report = {
            "run_id": self.run_id,
            "generated": now_iso(),
            "profile": profile.name,
            "profile_file": str(profile.filepath),
            "version": VERSION,
            "python": sys.version,
            "user": target_user_pw().pw_name,
            "uid": target_user_pw().pw_uid,
            "home": str(user_home()),
            "counters": counters,
            "tasks": [],
        }

        lines = [
            "# Dusky Orchestrator Report",
            "",
            f"- Run ID: `{self.run_id}`",
            f"- Generated: `{now_iso()}`",
            f"- Profile: `{profile.name}`",
            f"- Version: `{VERSION}`",
            "",
            "## Counters",
            "",
        ]

        for k, v in sorted(counters.items()):
            lines.append(f"- {k}: {v}")

        lines.extend(["", "## Tasks", ""])

        for task in tasks:
            status = statuses.get(task.state_key, "pending")
            item = {
                "index": task.index,
                "script": task.script_name,
                "mode": task.mode,
                "status": status,
                "path": str(task.resolved_path),
                "args": task.args,
                "condition": task.condition,
                "duration": task.duration,
                "checksum": task.checksum,
                "always": task.always,
                "interactive": task.interactive,
                "interactive_override": task.interactive_override,
                "once": task.once,
                "once_mode": task.once_mode,
                "once_scope": task.once_scope,
                "retry": task.retry,
                "on_failure": task.on_failure,
            }
            report["tasks"].append(item)
            lines.append(
                f"{task.index:03d}. [{task.mode}] {task.script_name} -> {status} ({task.duration:.2f}s)"
            )

        try:
            (self.root / "report.json").write_text(
                json.dumps(report, indent=2, default=str),
                encoding="utf-8",
            )
            (self.root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            self.failed_write = True
            sys.stderr.write(f"[WARN] Report write failed: {exc}\n")

    def close_all(self) -> None:
        if not self.enabled:
            return

        for f in list(self._task_files.values()):
            with suppress(OSError):
                f.flush()
                f.close()
        self._task_files.clear()

        if self._main is not None:
            with suppress(OSError):
                self.system("Logging stopped.")
                self._main.flush()
                self._main.close()
            self._main = None


# ==============================================================================
# NOTIFIERS / INHIBITOR
# ==============================================================================
class AudioNotifier:
    enabled = True

    @classmethod
    @functools.cache

    def _get_player(cls) -> str | None:
        players = GLOBAL_CONFIG.get("notifications", {}).get("audio_players", ["pw-play", "paplay"])
        for bin_name in players:
            if p := shutil.which(bin_name):
                return p
        return None

    @classmethod

    def play(cls, sound_type: str = "alert") -> None:
        if not cls.enabled or not GLOBAL_CONFIG.get("notifications", {}).get("audio_enabled", True):
            return

        player = cls._get_player()
        if not player:
            return

        sound_map = GLOBAL_CONFIG.get(
            "notifications",
            {},
        ).get(
            "sound_map",
            {
                "alert": "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga",
                "info": "/usr/share/sounds/freedesktop/stereo/dialog-information.oga",
                "complete": "/usr/share/sounds/freedesktop/stereo/complete.oga",
            },
        )
        target = Path(sound_map.get(sound_type, sound_map.get("alert", "")))
        if not target.exists():
            fallback_sound = GLOBAL_CONFIG.get(
                "notifications",
                {},
            ).get("fallback_sound", "/usr/share/sounds/freedesktop/stereo/bell.oga")
            fallback = Path(fallback_sound)
            if fallback.exists():
                target = fallback
            else:
                return

        cmd = (
            [player, "--media-role=event", str(target)]
            if player.endswith("pw-play")
            else [player, str(target)]
        )

        with suppress(OSError):
            subprocess.Popen(
                cmd,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )


class DesktopNotifier:
    enabled = True

    @classmethod

    def notify(cls, title: str, body: str, urgency: str = "normal") -> None:
        if not cls.enabled or not GLOBAL_CONFIG.get("notifications", {}).get("desktop_enabled", True):
            return
        if not shutil.which("notify-send"):
            return
        app_name = GLOBAL_CONFIG.get("notifications", {}).get("app_name", "Dusky Orchestrator")
        with suppress(OSError):
            subprocess.Popen(
                [
                    "notify-send",
                    f"--app-name={app_name}",
                    f"--urgency={urgency}",
                    title,
                    body,
                ],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )


class SleepInhibitor:

    def __init__(self, enabled: bool = True):
        self.proc = None
        if not enabled:
            return
        if not shutil.which("systemd-inhibit") or not shutil.which("sleep"):
            return

        with suppress(OSError):
            self.proc = subprocess.Popen(
                [
                    "systemd-inhibit",
                    "--what=idle:sleep",
                    "--who=Dusky Orchestrator",
                    "--why=System setup running",
                    "--mode=block",
                    "sleep",
                    "infinity",
                ],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )

    def close(self) -> None:
        if self.proc is None:
            return
        with suppress(Exception):
            self.proc.terminate()
            self.proc.wait(timeout=3)
        with suppress(Exception):
            self.proc.kill()
        self.proc = None


# ==============================================================================
# LOCK
# ==============================================================================
_LOCK_FD: int | None = None


def get_lock_holders() -> str:
    lp = lock_path()
    if not lp.exists():
        return ""

    try:
        real_lock = lp.resolve()
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
                    if os.readlink(fd_link) == str(real_lock):
                        cmdline_path = pid_dir / "cmdline"
                        cmd = ""
                        with suppress(PermissionError, OSError):
                            if cmdline_path.exists():
                                cmd = cmdline_path.read_text(errors="replace").replace("\x00", " ").strip()
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
            with suppress(OSError):
                fcntl.flock(_LOCK_FD, fcntl.LOCK_UN)
            with suppress(OSError):
                os.close(_LOCK_FD)
            _LOCK_FD = None
    except OSError:
        pass


def acquire_lock() -> bool:
    global _LOCK_FD
    if _LOCK_FD is not None:
        return True
    lp = lock_path()

    with suppress(OSError):
        ensure_dir(lp.parent, 0o700)

    try:
        fd = os.open(
            str(lp),
            os.O_CREAT | os.O_RDWR | os.O_CLOEXEC,
            0o600,
        )
    except Exception as e:
        sys.stderr.write(f"[ERROR] Could not open lock file {lp}: {e}\n")
        return False

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        _LOCK_FD = fd
        atexit.register(_cleanup_lock)
        return True
    except BlockingIOError:
        sys.stderr.write("[ERROR] Another instance is already running.\n")
        holders = get_lock_holders()
        if holders:
            sys.stderr.write(holders + "\n")
        with suppress(OSError):
            os.close(fd)
        return False
    except OSError as e:
        sys.stderr.write(f"[ERROR] Failed to acquire lock: {e}\n")
        with suppress(OSError):
            os.close(fd)
        return False


def release_lock() -> None:
    _cleanup_lock()


# ==============================================================================
# SUDO ENGINE
# ==============================================================================
class SudoEngine:
    _password: str | None = None
    _askpass_path: Path | None = None
    _mode: str = "none"  # none | root | nopasswd | password
    _registered_atexit: bool = False

    ENV_KEEP = GLOBAL_CONFIG.get(
        "sudo",
        {},
    ).get(
        "env_keep",
        [
            "HOME",
            "USER",
            "LOGNAME",
            "SHELL",
            "PATH",
            "TERM",
            "COLORTERM",
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "TZ",
            "XDG_RUNTIME_DIR",
            "XDG_CONFIG_HOME",
            "XDG_CACHE_HOME",
            "XDG_STATE_HOME",
            "XDG_DATA_HOME",
            "XDG_SESSION_TYPE",
            "XDG_CURRENT_DESKTOP",
            "DBUS_SESSION_BUS_ADDRESS",
            "WAYLAND_DISPLAY",
            "SSH_AUTH_SOCK",
            "SSH_AGENT_PID",
            "SUDO_ASKPASS",
            "PYTHONUNBUFFERED",
            "PYTHONUTF8",
            "PYTHONDONTWRITEBYTECODE",
            "PAGER",
            "SYSTEMD_PAGER",
            "GIT_PAGER",
            "EDITOR",
            "VISUAL",
            "QT_QPA_PLATFORMTHEME",
            "GTK_THEME",
            "XCURSOR_THEME",
            "XCURSOR_SIZE",
            "MOZ_ENABLE_WAYLAND",
            "LIBVA_DRIVER_NAME",
            "VDPAU_DRIVER",
            "SDL_VIDEODRIVER",
            "ZDOTDIR",
            "HYPRLAND_INSTANCE_SIGNATURE",
            "QT_QPA_PLATFORM",
            "XDG_SESSION_ID",
            "XDG_SEAT",
        ],
    )

    @classmethod

    def mode_name(cls) -> str:
        return cls._mode

    @classmethod

    def cleanup(cls) -> None:
        if cls._askpass_path is not None:
            with suppress(OSError):
                cls._askpass_path.unlink(missing_ok=True)

        cls._askpass_path = None
        cls._password = None
        cls._mode = "none"
        os.environ.pop("SUDO_ASKPASS", None)

    @classmethod

    def _write_askpass(cls, password: str) -> Path:
        ensure_dir(askpass_dir(), 0o700)
        encoded = base64.b64encode(password.encode("utf-8")).decode("ascii")
        interpreter = sys.executable or shutil.which("python3") or "/usr/bin/env python3"
        script = (
            f"#!{interpreter}\n"
            "import base64, sys\n"
            f"sys.stdout.write(base64.b64decode('{encoded}').decode('utf-8'))\n"
            "sys.stdout.write('\\n')\n"
        )

        prefix = GLOBAL_CONFIG.get("paths", {}).get("askpass_prefix", ".dusky_askpass_")
        fd, path = tempfile.mkstemp(prefix=prefix, dir=str(askpass_dir()))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        os.chmod(path, 0o700)
        return Path(path)

    @classmethod

    def set_password(cls, password: str) -> tuple[bool, str]:
        cls.cleanup()

        try:
            askpass = cls._write_askpass(password)
        except OSError as e:
            return False, f"Failed to create askpass helper: {e}"

        env = os.environ.copy()
        env["SUDO_ASKPASS"] = str(askpass)

        try:
            proc = subprocess.run(
                ["sudo", "-A", "-v"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            with suppress(OSError):
                askpass.unlink(missing_ok=True)
            return False, "sudo authentication timed out"
        except OSError as e:
            with suppress(OSError):
                askpass.unlink(missing_ok=True)
            return False, str(e)

        if proc.returncode == 0:
            cls._password = password
            cls._askpass_path = askpass
            cls._mode = "password"
            os.environ["SUDO_ASKPASS"] = str(askpass)
            if not cls._registered_atexit:
                atexit.register(cls.cleanup)
                cls._registered_atexit = True
            return True, ""

        err = (proc.stderr or "").strip()
        with suppress(OSError):
            askpass.unlink(missing_ok=True)
        return False, err or "sudo authentication failed"

    @classmethod

    def detect_nopasswd(cls) -> bool:
        if os.geteuid() == 0:
            cls._mode = "root"
            return True

        if not shutil.which("sudo"):
            return False

        with suppress(Exception):
            subprocess.run(
                ["sudo", "-k"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            proc = subprocess.run(
                ["sudo", "-n", "-v"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
            if proc.returncode == 0:
                cls._password = None
                cls._askpass_path = None
                cls._mode = "nopasswd"
                return True

        return False

    @classmethod

    def refresh_sync(cls) -> bool:
        if os.geteuid() == 0:
            cls._mode = "root"
            return True

        if not shutil.which("sudo"):
            return False

        if cls._mode == "nopasswd":
            cmd = ["sudo", "-n", "-v"]
            env = os.environ.copy()
        elif cls._mode == "password" and cls._askpass_path is not None:
            cmd = ["sudo", "-A", "-v"]
            env = os.environ.copy()
            env["SUDO_ASKPASS"] = str(cls._askpass_path)
        else:
            return cls.detect_nopasswd()

        try:
            proc = subprocess.run(
                cmd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
            )
            return proc.returncode == 0
        except Exception:
            return False

    @classmethod

    def sudo_prefix(cls) -> list[str]:
        if cls._mode == "root":
            return []
        if cls._mode == "nopasswd":
            return ["sudo", "-n", "--"]
        if cls._mode == "password" and cls._askpass_path is not None:
            return ["sudo", "-A", "--"]
        return ["sudo", "--"]

    @classmethod

    def preflight(
        cls,
        cli_password: str | None = None,
        password_file: Path | None = None,
    ) -> bool:
        if os.geteuid() == 0:
            cls._mode = "root"
            sys.stdout.write("[DUSKY PRE-FLIGHT] Running as root. No sudo escalation needed.\n")
            return True

        if not shutil.which("sudo"):
            sys.stderr.write("[FATAL] sudo is required but not installed.\n")
            return False

        sys.stdout.write("[DUSKY PRE-FLIGHT] Securing administrative privileges...\n")

        if cls.detect_nopasswd():
            sys.stdout.write("[DUSKY PRE-FLIGHT] Passwordless sudo detected.\n")
            return True

        password: str | None = cli_password
        if password is None and password_file is not None:
            with suppress(OSError):
                text = password_file.read_text(encoding="utf-8", errors="ignore")
                if text:
                    password = text.splitlines()[0].rstrip("\r\n")

        if password is not None:
            ok, err = cls.set_password(password)
            if ok:
                sys.stdout.write("[DUSKY PRE-FLIGHT] Sudo credentials cached for this session.\n")
                return True
            sys.stderr.write(f"[ERROR] Provided sudo password failed: {err}\n")

        if sys.stdin.isatty():
            import getpass

            target_user = target_user_pw().pw_name
            for attempt in range(1, 4):
                try:
                    password = getpass.getpass(f"[sudo] password for {target_user}: ")
                except (EOFError, KeyboardInterrupt):
                    sys.stderr.write("\n[FATAL] Sudo authentication cancelled.\n")
                    return False

                ok, err = cls.set_password(password)
                if ok:
                    sys.stdout.write("[DUSKY PRE-FLIGHT] Sudo credentials cached for this session.\n")
                    return True
                sys.stderr.write(f"[ERROR] Authentication failed ({attempt}/3): {err}\n")

        sys.stderr.write("[FATAL] Sudo authentication failed. Aborting.\n")
        return False

    @staticmethod

    async def maintain_heartbeat(error_callback=None) -> None:
        fail_count = 0
        interval = GLOBAL_CONFIG.get("sudo", {}).get("heartbeat_interval", 45)
        try:
            while True:
                await asyncio.sleep(interval)
                ok = await asyncio.to_thread(SudoEngine.refresh_sync)
                if ok:
                    fail_count = 0
                else:
                    fail_count += 1
                    if error_callback is not None and fail_count == 1:
                        error_callback("Sudo heartbeat failed. Admin credentials may need renewal.")
        except asyncio.CancelledError:
            pass


# ==============================================================================
# THEME
# ==============================================================================
def get_theme_path() -> Path:
    base_dir = user_home()
    theme_paths = GLOBAL_CONFIG.get(
        "ui",
        {},
    ).get(
        "theme_paths",
        [
            ".config/matugen/generated/dusky_tui.json",
            ".config/matugen/generated_fresh/dusky_tui.json",
        ],
    )

    for rel_path in theme_paths:
        p = Path(rel_path).expanduser()
        p = p if p.is_absolute() else base_dir / p
        if p.exists():
            return p

    fallback_p = (
        Path(theme_paths[0]).expanduser()
        if theme_paths
        else Path(".config/matugen/generated/dusky_tui.json")
    )
    return fallback_p if fallback_p.is_absolute() else base_dir / fallback_p


def _color_value(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("hex", "color", "value", "rgb"):
            v = value.get(key)
            if isinstance(v, str):
                return v.strip()
    return None


def _pick_color(data: dict, names: list[str], fallback: str) -> str:
    for name in names:
        if name in data:
            c = _color_value(data[name])
            if c and _HEX_COLOR_RE.match(c):
                return c
    return fallback


def load_palette() -> dict[str, str]:
    theme = {
        "bg": "#1a110e", "fg": "#f1dfd9", "accent": "#ffb59b",
        "warning": "#e7bdaf", "success": "#d5c68e", "muted": "#53433e", "error": "#ffb4ab",
    }
    for key, value in GLOBAL_CONFIG.get("ui", {}).get("default_palette", {}).items():
        if key in theme and _HEX_COLOR_RE.fullmatch(value):
            theme[key] = value

    theme_file = get_theme_path()
    if theme_file.is_file():
        try:
            data = json.loads(theme_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in theme:
                    value = _color_value(data.get(key))
                    if value and _HEX_COLOR_RE.fullmatch(value):
                        theme[key] = value
        except (json.JSONDecodeError, OSError):
            pass

    return theme

PALETTE = load_palette()


def build_app_css(p: dict[str, str]) -> str:
    return f"""
Screen, Tree, RichLog, ScrollBar, #left_pane {{
    background: {p['bg']};
    color: {p['fg']};
    scrollbar-color: {p['accent']} 50%;
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
    width: 38%;
    border-right: solid {p['muted']} 30%;
    background: {p['bg']};
    padding: 0;
    height: 100%;
}}

#right_pane {{
    width: 62%;
    height: 100%;
    layout: vertical;
    background: {p['bg']};
    padding: 0;
}}

#telemetry_box {{
    height: 5;
    border-bottom: solid {p['muted']};
    padding: 0 1;
    layout: vertical;
}}

#details_box {{
    height: auto;
    max-height: 8;
    border-bottom: solid {p['muted']};
    padding: 0 1;
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
}}

Tree {{
    background: {p['bg']};
    color: {p['fg']};
    scrollbar-size-vertical: 1;
    scrollbar-size-horizontal: 0;
    padding: 0;
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

TaskSearchScreen, ConflictModalScreen, ManualModalScreen, SudoPasswordScreen, ConfirmQuitScreen, HelpScreen, LogSearchScreen, FailureSummaryScreen, CompletionDialog {{
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
    padding: 1 2;
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

#modal_dialog, #manual_dialog, #sudo_dialog, #help_dialog, #summary_dialog, #completion_dialog {{
    width: 90;
    height: auto;
    background: {p['bg']};
    padding: 1 2;
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

#modal_dialog {{
    border: heavy {p['error']};
}}

#manual_dialog {{
    border: heavy {p['accent']};
}}

#sudo_dialog {{
    border: heavy {p['warning']};
}}

#confirm_dialog {{
    border: heavy {p['warning']};
}}

#help_dialog {{
    border: heavy {p['accent']};
    height: 70%;
}}

#summary_dialog {{
    border: heavy {p['warning']};
    height: 75%;
}}

#completion_dialog {{
    width: 72;
    border: heavy {p['accent']};
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

#completion_title {{
    color: {p['accent']};
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

#modal_title, #manual_title, #sudo_title, #confirm_title, #help_title, #summary_title, #completion_title {{
    text-align: center;
    text-style: bold;
    margin-bottom: 1;
}}

#modal_title {{
    color: {p['error']};
}}

#manual_title, #help_title {{
    color: {p['accent']};
}}

#sudo_title, #confirm_title, #summary_title {{
    color: {p['warning']};
}}

#error_details, #summary_details {{
    color: {p['warning']};
    margin-bottom: 1;
    max-height: 18;
    overflow-y: auto;
}}

#button_bar {{
    layout: horizontal;
    align: center middle;
    height: 3;
}}

Button {{
    height: 1;
    min-width: 16;
    border: none;
    outline: none;
    margin: 0 1;
    padding: 0;
    text-style: bold;
}}

Button:focus {{
    border: none;
    outline: none;
}}

Button.-primary {{
    background: {p['muted']};
    color: {p['fg']};
    border: none;
}}

Button.-primary:focus {{
    background: {p['accent']};
    color: {p['bg']};
    border: none;
}}

Button.-primary:hover {{
    background: {p['accent']};
    color: {p['bg']};
}}

Button.-error {{
    background: {p['error']};
    color: {p['bg']};
    border: none;
}}

Button.-error:focus {{
    background: {p['error']};
    color: {p['bg']};
    border: none;
}}

Button.-error:hover {{
    background: {p['fg']};
    color: {p['bg']};
}}

Input {{
    background: {p['bg']};
    border: tall {p['accent']};
    color: {p['fg']};
}}

AppFooter {{
    dock: bottom;
    height: 1;
    background: {p['bg']};
    color: {p['fg']};
    padding: 0 1;
}}

.footer-shortcut {{
    color: {p['accent']};
    margin-right: 2;
    text-style: bold;
}}

.footer-sep {{
    color: {p['muted']};
}}

#footer_status {{
    color: {p['warning']};
}}
"""


def build_selector_css(p: dict[str, str]) -> str:
    return f"""
Screen {{
    align: center middle;
    background: {p['bg']};
    color: {p['fg']};
}}

#selector_container {{
    width: 100;
    height: auto;
    border: heavy {p['accent']};
    background: {p['bg']};
    padding: 1 2;
}}

#title {{
    text-align: center;
    text-style: bold;
    color: {p['accent']};
    margin-bottom: 1;
}}

OptionList {{
    height: auto;
    max-height: 70%;
    border: none;
    background: {p['bg']};
    color: {p['fg']};
}}

.help_text {{
    text-align: center;
    color: {p['warning']};
    text-style: italic;
    margin-top: 1;
}}
"""


# ==============================================================================
# PROFILE PARSER
# ==============================================================================
def nonnegative_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return number


def task_bool(table: dict, name: str) -> bool:
    value = table.get(name, False)
    if not isinstance(value, bool):
        raise ValueError(f"Task field '{name}' must be a boolean")
    return value


def apply_task_flags(task: OrchestratorTask, flags: str) -> None:
    for raw in flags.split(","):
        flag = raw.strip()
        key = flag.lower()
        if not flag:
            continue
        if key in ("true", "ignore", "ignore-fail"):
            task.ignore_fail = True
        elif key in ("interactive", "tui", "prompt", "fullscreen", "tty", "suspend"):
            task.interactive = task.interactive_override = True
        elif key in ("no-interactive", "noninteractive", "inline", "embedded"):
            task.interactive = task.interactive_override = False
        elif key in ("force", "--force"):
            task.force_flag = True
        elif key in ("always", "always_run"):
            task.always = True
        elif key in ("once", "run_once", "sticky"):
            task.once = True
        elif key.startswith("once:"):
            value = key[5:]
            modes = {"content": "content", "hash": "content", "forever": "forever",
                     "exact": "forever", "permanent": "forever", "sealed": "sealed", "locked": "sealed"}
            scopes = {"profile": "profile", "local": "profile", "global": "global", "machine": "global"}
            if value in modes:
                task.once_mode = modes[value]
            elif value in scopes:
                task.once_scope = scopes[value]
            else:
                raise ValueError(f"Invalid task flag: {flag}")
            task.once = True
        elif key.startswith("if:"):
            condition = flag[3:]
            task.condition = f"{task.condition},{condition}" if task.condition else condition
        elif key.startswith("timeout:"):
            task.timeout = nonnegative_number(flag[8:], "timeout")
        elif key.startswith("retry:"):
            task.retry = int(flag[6:])
            if task.retry < 0:
                raise ValueError("retry must be nonnegative")
        elif key.startswith("retry_delay:"):
            task.retry_delay = nonnegative_number(flag[12:], "retry_delay")
        elif key.startswith("on_failure:"):
            task.on_failure = key[11:]
        else:
            raise ValueError(f"Unknown task flag: {flag}")
    if task.on_failure not in ("ask", "abort", "continue", "skip", "manual"):
        raise ValueError(f"Invalid on_failure policy: {task.on_failure}")
    if task.condition and not ConditionEvaluator.is_known(task.condition):
        raise ValueError(f"Invalid task condition: {task.condition}")


def parse_task_entry(raw_entry: str, index: int) -> OrchestratorTask:
    raw = raw_entry.strip()
    parts = [part.strip() for part in raw.split("|", 2)]
    if len(parts) == 1:
        mode, flags, cmd = "U", "", parts[0]
    elif len(parts) == 2:
        mode, cmd = parts
        flags = ""
    else:
        mode, flags, cmd = parts
    tokens = shlex.split(cmd)
    if tokens and tokens[0] == "true" and len(tokens) > 1:
        flags += ",ignore-fail"
        tokens = tokens[1:]
    if not tokens:
        raise ValueError(f"Empty command in entry: {raw_entry}")
    task = parse_task_table({"script": tokens[0], "args": tokens[1:], "mode": mode, "flags": flags}, index)
    task.raw_entry = raw
    return task


def parse_task_table(table: dict, index: int) -> OrchestratorTask:
    cmd = table.get("cmd") or table.get("script") or table.get("path") or ""
    if not isinstance(cmd, str) or not cmd.strip():
        raise ValueError(f"Task table at index {index} missing cmd/script/path")
    cmd = cmd.strip()
    args_raw = table.get("args", [])
    if isinstance(args_raw, str):
        args = shlex.split(args_raw)
    elif isinstance(args_raw, list) and all(isinstance(arg, str) for arg in args_raw):
        args = list(args_raw)
    else:
        raise ValueError("Task args must be a string or list of strings")
    # An explicit script/path field may contain spaces in its filename.
    if "cmd" in table:
        tokens = shlex.split(cmd)
        if not tokens:
            raise ValueError("Empty task command")
        cmd, args = tokens[0], tokens[1:] + args
    mode = str(table.get("mode", "U")).strip().upper()
    if mode not in ("U", "S"):
        raise ValueError(f"Invalid task mode: {mode}")
    retry = table.get("retry", 0)
    if type(retry) is not int or retry < 0:
        raise ValueError("Task retry must be a nonnegative integer")
    condition = table.get("condition")
    if condition is not None and not isinstance(condition, str):
        raise ValueError("Task condition must be a string")
    once_mode = str(table.get("once_mode", "content")).lower()
    if once_mode == "locked":
        once_mode = "sealed"
    once_scope = str(table.get("once_scope", "profile")).lower()
    if once_mode not in ("content", "forever", "sealed") or once_scope not in ("profile", "global"):
        raise ValueError("Invalid once_mode or once_scope")
    flags = table.get("flags", "")
    if not isinstance(flags, str):
        raise ValueError("Task flags must be a string")
    task = OrchestratorTask(
        raw_entry=json.dumps(table), mode=mode, script_name=cmd, args=args, index=index,
        ignore_fail=task_bool(table, "ignore_fail"), interactive=task_bool(table, "interactive"),
        interactive_override=task_bool(table, "interactive") if "interactive" in table else None,
        force_flag=task_bool(table, "force") or "--force" in args,
        always=task_bool(table, "always"), condition=condition.strip() if condition else None,
        timeout=nonnegative_number(table["timeout"], "timeout") if "timeout" in table else None,
        retry=retry, retry_delay=nonnegative_number(table.get("retry_delay", 1.0), "retry_delay"),
        on_failure=str(table.get("on_failure", "ask")).lower(),
        once=task_bool(table, "once"), once_mode=once_mode, once_scope=once_scope,
    )
    apply_task_flags(task, flags)
    return task


def repair_missing_commas(text: str) -> tuple[str, int]:
    """Insert missing commas inside array / inline-table literals.

    A single omitted comma inside any [] or {} literal makes the WHOLE profile
    unparseable. This tokenizer-based repairer inserts a comma wherever one
    value token is directly followed by another value token without a
    separator. Safety contract: only applied on a strict tomllib failure and
    only when the repaired text re-parses cleanly; strings, arrays, tables and
    bare words are handled such that valid files are returned byte-for-byte
    untouched and parseable output never changes meaning.

    Returns ``(repaired_text, number_of_fixes)``.
    """
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
                    if quote == '"' and text[i] == '\\':
                        i += 1
                    i += 1
                i = min(i + 3, n)
            else:
                i += 1
                while i < n and text[i] != quote:
                    if quote == '"' and text[i] == '\\':
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
            else:
                pending_value = False
                pending_is_word = False
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

        word_start_idx = i
        while i < n and (text[i].isalnum() or text[i] in _WORD_CHARS):
            i += 1
        if i > word_start_idx:
            out.append(text[word_start_idx:i])
            k = i
            while k < n and text[k] in ' \t':
                k += 1
            if depth and (k == n or text[k] != '='):
                pending_value = True
                pending_is_word = True
                value_end = len(out)
            else:
                pending_value = False
                pending_is_word = False
            continue

        out.append(c)
        i += 1

    return ''.join(out), fixes


def load_profile(filepath: Path) -> ProfileConfig:
    text = filepath.read_text(encoding="utf-8")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as raw_err:
        repaired, fixes = repair_missing_commas(text)
        if fixes == 0:
            raise raw_err
        try:
            data = tomllib.loads(repaired)
        except tomllib.TOMLDecodeError:
            raise raw_err
        sys.stderr.write(
            f"[WARN] Parsed '{filepath}' with {fixes} missing comma(s) repaired in memory. Fix the source file.\n"
        )

    for section in ("profile", "git", "search_dirs", "conflict_resolutions", "sequence", "policy"):
        if not isinstance(data.get(section, {}), dict):
            raise ValueError(f"Profile {filepath}: [{section}] must be a table")
    p_data = data.get("profile", {})
    g_data = data.get("git", {})
    s_data = data.get("search_dirs", {})
    c_data = data.get("conflict_resolutions", {})
    seq_data = data.get("sequence", {})
    policy_data = data.get("policy", {})

    for section, name, item_type in ((seq_data, "scripts", str), (seq_data, "tasks", dict), (s_data, "dirs", str)):
        values = section.get(name, [])
        if not isinstance(values, list) or any(not isinstance(item, item_type) for item in values):
            raise ValueError(f"Profile {filepath}: {name} has invalid entries")
    if not isinstance(g_data.get("enabled", False), bool):
        raise ValueError("git.enabled must be a boolean")
    for name in ("manual", "stop_on_fail", "force", "audio", "notify", "inhibit_sleep"):
        if name in policy_data and not isinstance(policy_data[name], bool):
            raise ValueError(f"policy.{name} must be a boolean")
    if "task_timeout" in policy_data:
        policy_data["task_timeout"] = nonnegative_number(policy_data["task_timeout"], "policy.task_timeout")
    tasks: list[OrchestratorTask] = []

    for line in seq_data.get("scripts", []):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tasks.append(parse_task_entry(line, len(tasks) + 1))

    offset = len(tasks) + 1
    for i, table in enumerate(seq_data.get("tasks", []), start=offset):
        if isinstance(table, dict):
            tasks.append(parse_task_table(table, i))

    try:
        post_delay = int(p_data.get("post_script_delay", 0))
    except Exception:
        post_delay = 0

    search_dirs: list[str] = []
    seen: set[str] = set()

    for d in s_data.get("dirs", []):
        resolved = str(resolve_home(str(d)))
        if resolved not in seen:
            seen.add(resolved)
            search_dirs.append(resolved)
            if not Path(resolved).exists():
                sys.stderr.write(f"[WARN] Search directory does not exist: {resolved}\n")

    policy = policy_data if isinstance(policy_data, dict) else {}

    default_repo_url = GLOBAL_CONFIG.get(
        "git",
        {},
    ).get("default_repo_url", "https://github.com/dusklinux/dusky")
    git_repo_url = str(
        g_data.get("url") or g_data.get("repo_url") or default_repo_url
    ).strip()

    return ProfileConfig(
        filepath=filepath,
        name=str(p_data.get("name", filepath.stem)).strip(),
        description=str(p_data.get("description", "")).strip(),
        post_script_delay=max(0, post_delay),
        git_enabled=bool(g_data.get("enabled", False)),
        git_dir=str(g_data.get("git_dir", "~/dusky")).strip(),
        git_work_tree=str(g_data.get("work_tree", "~/")).strip(),
        git_remote=str(g_data.get("remote", "origin")).strip(),
        git_repo_url=git_repo_url,
        search_dirs=search_dirs,
        conflict_resolutions={
            str(k).strip(): str(v).strip()
            for k, v in c_data.items()
            if str(k).strip() and str(v).strip()
        },
        tasks=tasks,
        policy=policy,
    )


def discover_profiles() -> list[ProfileConfig]:
    if not PROFILES_DIR.exists():
        sys.stderr.write(f"[FATAL] Profiles directory missing: {PROFILES_DIR}\n")
        sys.exit(1)

    profiles: list[ProfileConfig] = []
    errors: list[str] = []
    for f in sorted(PROFILES_DIR.glob("*.toml")):
        try:
            profiles.append(load_profile(f))
        except Exception as e:
            errors.append(f"{f.name}: {e}")

    if errors:
        raise ValueError("Invalid profile(s):\n  " + "\n  ".join(errors))

    names: set[str] = set()
    for profile in profiles:
        name = safe_filename(profile.name)
        if name in names:
            raise ValueError(f"Profiles share state database name: {name}")
        names.add(name)
    return profiles


# ==============================================================================
# SCRIPT DISCOVERY
# ==============================================================================
def _script_metadata(path: Path) -> tuple[bool, str, str]:
    try:
        with open(path, "rb") as f:
            data = f.read(16384)
    except OSError:
        return False, "", ""

    head = data[:4]
    text = data.decode("utf-8", errors="ignore")
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")

    first_line = text.splitlines()[0].strip() if text else ""
    return head == b"\x7fELF", first_line, text


def _short_home(path_str: str) -> str:
    s = str(path_str)
    home = str(user_home())
    if home and s == home:
        return "~"
    if home and s.startswith(home + os.sep):
        return "~" + s[len(home):]
    return s


def _script_description(path: Path) -> str:
    _, _, head = _script_metadata(path)
    fallback = ""
    for line in head.splitlines()[:20]:
        line = line.strip()
        if not line.startswith("#"):
            break
        if line.startswith("#!"):
            continue
        body = line[1:].strip()
        if not body:
            continue
        lowered = body.lower()
        if lowered.startswith(("d:", "desc:", "description:")):
            return body.split(":", 1)[1].strip()
        if any(tok in lowered for tok in ("coding:", "vim:", "noqa", "pylint", "flake8", "shellcheck", "shfmt")):
            continue
        if _INTERACTIVE_RE.search(line):
            continue
        if not fallback:
            fallback = body
    return fallback


def _interpreter_from_shebang(first_line: str) -> tuple[str, list[str]] | None:
    if not first_line.startswith("#!"):
        return None

    shebang = first_line[2:].strip()
    if not shebang:
        return None

    try:
        parts = shlex.split(shebang)
    except ValueError:
        parts = shebang.split()

    if not parts:
        return None

    if parts[0].endswith("/env") and len(parts) > 1:
        parts = parts[1:]
        if parts and parts[0] == "-S":
            parts = parts[1:]
        elif parts and (parts[0].startswith("-") or "=" in parts[0]):
            # An env invocation with options or assignments needs env itself
            # to preserve its exact meaning when a script is not executable.
            return "env", parts

    if not parts:
        return None

    return parts[0], parts[1:]


def resolve_and_validate_manifest(profile: ProfileConfig) -> bool:
    success = True
    search_dir_cache: dict[str, bool] = {}
    occurrence: dict[tuple[str, str, str], int] = {}

    for task in profile.tasks:
        task.resolved_path = None
        args_key = shlex.join(task.args)
        key_tuple = (task.mode, task.script_name, args_key)
        occ = occurrence.get(key_tuple, 0)
        occurrence[key_tuple] = occ + 1

        if "/" in task.script_name:
            cand = resolve_home(task.script_name)
            if cand.is_file():
                task.resolved_path = cand
        elif task.script_name in profile.conflict_resolutions:
            cand = resolve_home(profile.conflict_resolutions[task.script_name])
            if cand.is_file():
                task.resolved_path = cand

        if task.resolved_path is None:
            matches: list[Path] = []
            for d in profile.search_dirs:
                p = Path(d) / task.script_name
                key = str(p)
                exists = search_dir_cache.get(key)
                if exists is None:
                    exists = p.is_file()
                    search_dir_cache[key] = exists
                if exists and not any(p.samefile(match) for match in matches):
                    matches.append(p)

            if len(matches) == 1:
                task.resolved_path = matches[0]
            elif len(matches) > 1:
                if task.script_name in profile.conflict_resolutions:
                    cand = resolve_home(profile.conflict_resolutions[task.script_name])
                    if cand.is_file():
                        task.resolved_path = cand
                    else:
                        sys.stderr.write(f"[CONFLICT] Resolution for {task.script_name} is invalid: {cand}\n")
                        success = False
                else:
                    sys.stderr.write(f"[CONFLICT] Multiple versions of {task.script_name} found:\n")
                    for m in matches:
                        sys.stderr.write(f"  - {m}\n")
                    success = False

        if task.resolved_path is None:
            sys.stderr.write(f"[MISSING] Could not find {task.script_name} in search dirs.\n")
            success = False
            task.checksum = ""
            task.state_key = make_state_key(task, occ)
            continue

        task.checksum = file_checksum(task.resolved_path)
        if not task.checksum:
            sys.stderr.write(f"[ERROR] Cannot checksum script: {task.resolved_path}\n")
            success = False
        is_elf, first_line, full_head = _script_metadata(task.resolved_path)
        task.description = _script_description(task.resolved_path)

        metadata_interactive = False
        for line in full_head.splitlines()[:20]:
            if _INTERACTIVE_RE.search(line):
                metadata_interactive = True
                break

        if task.interactive_override is not None:
            task.interactive = task.interactive_override
        else:
            task.interactive = metadata_interactive

        shebang_command = _interpreter_from_shebang(first_line)
        executable = os.access(task.resolved_path, os.X_OK)
        task.interpreter_args = []

        if is_elf:
            task.interpreter = ""
            if not executable:
                sys.stderr.write(f"[INTERPRETER] Binary is not executable: {task.resolved_path}\n")
                success = False
        elif shebang_command:
            if executable:
                task.interpreter = ""
            else:
                task.interpreter, task.interpreter_args = shebang_command
        else:
            suffix = task.resolved_path.suffix.lower()
            ext_map = GLOBAL_CONFIG.get(
                "execution",
                {},
            ).get(
                "extension_interpreters",
                {
                    ".py": sys.executable,
                    ".sh": shutil.which("bash") or "bash",
                    ".fish": shutil.which("fish") or "fish",
                },
            )

            if suffix in ext_map:
                task.interpreter = ext_map[suffix]
            elif executable:
                task.interpreter = ""
            else:
                task.interpreter = GLOBAL_CONFIG.get(
                    "execution",
                    {},
                ).get("default_interpreter", shutil.which("bash") or "bash")

        check_interp = task.interpreter or (shebang_command[0] if shebang_command and not is_elf else "")
        if check_interp:
            interp = check_interp
            if interp.lower() in ("python", "python3") or interp.lower().startswith("python3."):
                if not (shutil.which(interp) or sys.executable):
                    sys.stderr.write(f"[INTERPRETER] No Python interpreter available for {task.script_name}\n")
                    success = False
            else:
                found = shutil.which(interp)
                if not found:
                    sys.stderr.write(f"[INTERPRETER] Missing interpreter '{interp}' for {task.script_name}\n")
                    success = False

        task.state_key = make_state_key(task, occ)

    return success


# ==============================================================================
# CONDITIONS
# ==============================================================================
class ConditionEvaluator:
    IMMUTABLE = {
        "wayland",
        "graphical",
        "ssh",
        "desktop",
        "battery",
        "btrfs",
        "vm",
        "baremetal",
        "gpu",
    }

    @staticmethod

    def is_known(condition: str | None) -> bool:
        if not isinstance(condition, str) or not condition.strip():
            return False
        parts = [part.strip() for part in condition.split(",")]
        if any(not part for part in parts):
            return False
        bare = {"always", "true", "yes", "never", "false", "no", "wayland",
                "graphical", "ssh", "desktop", "battery", "btrfs", "vm", "baremetal"}
        valued = {"command", "cmd", "path", "missing", "file", "dir", "package", "pkg",
                  "group", "gpu", "service_active", "service", "svc", "user_service_active",
                  "user_service", "user_svc", "env"}
        for part in parts:
            kind, sep, value = part.partition(":")
            if not sep and kind.lower() in bare:
                continue
            if sep and kind.lower() == "not" and ConditionEvaluator.is_known(value):
                continue
            if sep and kind.lower() in valued and value.strip():
                continue
            return False
        return True


    def __init__(self):
        self.cache: dict[str, bool] = {}

    def _volatile(self, condition: str | None) -> bool:
        if not condition:
            return False
        cond = condition.strip()
        if "," in cond:
            # A compound condition is volatile if ANY of its AND'ed parts is
            # volatile (e.g. "gpu:nvidia,command:sddm" must re-check sddm each
            # pass so an earlier task can install it mid-run).
            return any(self._volatile(part) for part in cond.split(","))
        if cond.lower() in ("always", "true", "yes", "never", "false", "no"):
            return False

        kind, _, value = cond.partition(":")
        kind = kind.strip().lower()
        value = value.strip()

        if kind == "not":
            return self._volatile(value)
        return kind not in self.IMMUTABLE

    def check(self, condition: str | None) -> bool:
        if not condition:
            return True

        cond = condition.strip()
        if cond.lower() in ("always", "true", "yes"):
            return True
        if cond.lower() in ("never", "false", "no"):
            return False

        if self._volatile(cond):
            return self._eval(cond)

        if cond in self.cache:
            return self.cache[cond]

        result = self._eval(cond)
        self.cache[cond] = result
        return result

    def _eval(self, cond: str) -> bool:
        if "," in cond:
            # Commas are a strict AND separator between sub-conditions. Values
            # are comma-free (see documented DSL contract), so NO token merging.
            parts: list[str] = [p.strip() for p in cond.split(",") if p.strip()]
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
        if kind == "graphical":
            return bool(os.environ.get("WAYLAND_DISPLAY"))
        if kind == "ssh":
            return bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))
        if kind == "desktop":
            session = os.environ.get("XDG_SESSION_TYPE", "").lower()
            if session == "wayland":
                return True
            return self.check("graphical") and not self.check("ssh")

        if kind == "battery":
            return self._has_battery()
        if kind == "btrfs":
            return self._root_is_btrfs()
        if kind == "vm":
            return self._is_vm()
        if kind == "baremetal":
            return not self._is_vm()

        if kind in ("command", "cmd"):
            return bool(shutil.which(value))
        if kind == "path":
            return Path(value).expanduser().exists()
        if kind == "missing":
            return not Path(value).expanduser().exists()
        if kind == "file":
            return Path(value).expanduser().is_file()
        if kind == "dir":
            return Path(value).expanduser().is_dir()

        if kind in ("package", "pkg"):
            return self._package_installed(value)
        if kind == "group":
            return self._user_in_group(value)
        if kind == "gpu":
            return self._gpu(value.lower())

        if kind in ("service_active", "service", "svc"):
            cmd = GLOBAL_CONFIG.get("conditions", {}).get(
                "service_active_cmd",
                ["systemctl", "is-active", "--quiet"],
            )
            return self._run(cmd + [value])
        if kind in ("user_service_active", "user_service", "user_svc"):
            cmd = GLOBAL_CONFIG.get("conditions", {}).get(
                "user_service_active_cmd",
                ["systemctl", "--user", "is-active", "--quiet"],
            )
            return self._run(cmd + [value])

        if kind == "env":
            return bool(os.environ.get(value))

        return False

    def _run(self, cmd: list[str]) -> bool:
        with suppress(Exception):
            return subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            ).returncode == 0
        return False

    def _output(self, cmd: list[str]) -> str:
        with suppress(Exception):
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            )
            if proc.returncode == 0:
                return proc.stdout.strip()
        return ""

    def _has_battery(self) -> bool:
        base = Path("/sys/class/power_supply")
        if not base.exists():
            return False
        with suppress(OSError):
            for entry in base.iterdir():
                type_file = entry / "type"
                if type_file.exists():
                    if type_file.read_text(errors="ignore").strip() == "Battery":
                        return True
        return False

    def _root_is_btrfs(self) -> bool:
        with suppress(OSError):
            for line in Path("/proc/mounts").read_text(errors="ignore").splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "/" and parts[2] == "btrfs":
                    return True
        return False

    def _is_vm(self) -> bool:
        if shutil.which("systemd-detect-virt"):
            with suppress(Exception):
                proc = subprocess.run(
                    ["systemd-detect-virt", "--vm", "--quiet"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                return proc.returncode == 0

        dmi = Path("/sys/class/dmi/id/sys_vendor")
        if dmi.exists():
            with suppress(OSError):
                vendor = dmi.read_text(errors="ignore").lower()
                return any(x in vendor for x in ("qemu", "kvm", "vmware", "virtualbox", "bochs"))

        return False

    def _package_installed(self, name: str) -> bool:
        pkg_cmd = GLOBAL_CONFIG.get("conditions", {}).get(
            "package_check_cmd",
            ["pacman", "-Qq"],
        )
        if not pkg_cmd or not shutil.which(pkg_cmd[0]):
            return False
        return self._run(pkg_cmd + [name])

    def _user_in_group(self, group: str) -> bool:
        user = target_user_pw().pw_name
        groups = self._output(["id", "-nG", user])
        return group in groups.split()

    def _gpu(self, kind: str) -> bool:
        if kind == "nvidia" and Path("/sys/module/nvidia").exists():
            return True
        if kind == "intel" and (Path("/sys/module/i915").exists() or Path("/sys/module/xe").exists()):
            return True
        if kind == "amd" and (Path("/sys/module/amdgpu").exists() or Path("/sys/module/radeon").exists()):
            return True

        drm_path = Path("/sys/class/drm")
        if drm_path.exists():
            vendor_map = GLOBAL_CONFIG.get(
                "conditions",
                {},
            ).get(
                "gpu_vendor_map",
                {
                    "nvidia": "0x10de",
                    "intel": "0x8086",
                    "amd": "0x1002",
                    "vmware": "0x15ad",
                    "virtio": "0x1af4",
                },
            )
            target_vendor = vendor_map.get(kind)
            if target_vendor:
                with suppress(OSError):
                    for card in drm_path.glob("card[0-9]*"):
                        device_dir = card / "device"
                        if not device_dir.exists():
                            continue
                        driver_link = device_dir / "driver"
                        if driver_link.exists():
                            with suppress(OSError):
                                if driver_link.resolve().name == "simpledrm":
                                    continue
                        vendor_file = device_dir / "vendor"
                        if vendor_file.exists():
                            if vendor_file.read_text(encoding="utf-8").strip().lower() == target_vendor:
                                return True

        if kind == "nvidia":
            return self._lspci_vga("nvidia")
        if kind == "intel":
            return self._lspci_vga("intel")
        if kind == "amd":
            return (
                self._lspci_vga("amd")
                or self._lspci_vga("ati")
                or self._lspci_vga("radeon")
                or self._lspci_vga("advanced micro devices")
            )
        if kind in ("vmware", "virtio", "qemu"):
            return self._lspci_vga(kind)
        return False

    def _lspci_vga(self, needle: str) -> bool:
        if not shutil.which("lspci"):
            return False
        out = self._output(["lspci"])
        needle_lower = needle.lower()
        pattern = re.compile(rf"\b{re.escape(needle_lower)}\b", re.IGNORECASE)
        for line in out.splitlines():
            line_lower = line.lower()
            if any(ctrl in line_lower for ctrl in ("vga", "3d", "display")):
                if pattern.search(line_lower):
                    return True
        return False


# ==============================================================================
# GIT SELF UPDATE
# ==============================================================================
GIT_UPSTREAM_BRANCH = GLOBAL_CONFIG.get("git", {}).get("upstream_branch", "main")
GIT_UPSTREAM_REF = GLOBAL_CONFIG.get(
    "git",
    {},
).get("upstream_ref", f"refs/dusky/upstream/{GIT_UPSTREAM_BRANCH}")


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    git_config = GLOBAL_CONFIG.get("git", {})

    strip_keys = git_config.get(
        "env_strip",
        [
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_LITERAL_PATHSPECS",
            "GIT_ASKPASS",
            "SSH_ASKPASS",
        ],
    )
    for key in strip_keys:
        env.pop(key, None)

    env_inject = git_config.get(
        "env_inject",
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_SSH_COMMAND": "ssh" if "SSH_AUTH_SOCK" in os.environ else "ssh -o BatchMode=yes",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "GIT_OPTIONAL_LOCKS": "0",
        },
    )
    env.update(env_inject)
    env["GIT_LITERAL_PATHSPECS"] = "1"
    return env


def _git_run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        env=_git_env(),
        capture_output=True,
        text=True, encoding="utf-8", errors="surrogateescape",
        timeout=timeout,
    )


def _git_check(cmd: list[str], timeout: int = 60) -> str:
    proc = _git_run(cmd, timeout=timeout)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, proc.stdout, proc.stderr)
    return proc.stdout if proc.stdout.endswith("\0") else proc.stdout.rstrip("\n")


def _delete_path(target: Path) -> None:
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    else:
        target.unlink(missing_ok=True)


def _iter_git_lock_files(git_dir: Path) -> list[Path]:
    locks: list[Path] = [
        git_dir / "index.lock",
        git_dir / "config.lock",
        git_dir / "packed-refs.lock",
        git_dir / "shallow.lock",
        git_dir / "HEAD.lock",
        git_dir / "ORIG_HEAD.lock",
        git_dir / "FETCH_HEAD.lock",
    ]

    refs_dir = git_dir / "refs"
    if refs_dir.is_dir():
        with suppress(OSError):
            locks.extend(refs_dir.rglob("*.lock"))

    return locks


def _clear_stale_git_locks(git_dir: Path) -> bool:
    for path in _iter_git_lock_files(git_dir):
        if path.exists() or path.is_symlink():
            sys.stderr.write(f"[ERROR] Git lock exists: {path}. Resolve it before updating.\n")
            return False
    return True


def _detect_git_operation_state(git_dir: Path) -> str:
    if (git_dir / "rebase-merge").is_dir() or (git_dir / "rebase-apply").is_dir():
        return "rebase"
    if (git_dir / "MERGE_HEAD").is_file():
        return "merge"
    if (git_dir / "CHERRY_PICK_HEAD").is_file():
        return "cherry-pick"
    if (git_dir / "REVERT_HEAD").is_file():
        return "revert"
    if (git_dir / "BISECT_LOG").is_file():
        return "bisect"
    return "none"


def _git_repo_status(base_cmd: list[str], git_dir: Path, work_tree: Path) -> str:
    if git_dir.is_symlink():
        sys.stderr.write(f"[ERROR] Git directory must not be a symlink: {git_dir}\n")
        return "invalid"

    if not git_dir.exists():
        return "absent"

    if not git_dir.is_dir():
        sys.stderr.write(f"[ERROR] Git path exists but is not a directory: {git_dir}\n")
        return "invalid"

    try:
        if git_dir.stat().st_uid != target_user_pw().pw_uid:
            sys.stderr.write(f"[ERROR] Git directory is not owned by the target user: {git_dir}\n")
            return "invalid"
    except OSError:
        sys.stderr.write(f"[ERROR] Cannot stat Git directory: {git_dir}\n")
        return "invalid"

    if not work_tree.is_dir() or not os.access(work_tree, os.W_OK):
        sys.stderr.write(f"[ERROR] Git work tree is missing or not writable: {work_tree}\n")
        return "invalid"

    if not _clear_stale_git_locks(git_dir):
        return "invalid"

    op = _detect_git_operation_state(git_dir)
    if op != "none":
        sys.stderr.write(f"[ERROR] Git {op} is in progress in {git_dir}. Resolve it before updating.\n")
        return "invalid"

    try:
        _git_check(base_cmd + ["rev-parse", "--git-dir"], timeout=20)
    except Exception:
        sys.stderr.write(f"[ERROR] Git repository metadata is invalid or corrupted: {git_dir}\n")
        return "invalid"

    return "valid"


def _nearest_existing_ancestor(path: Path) -> Path:
    p = path
    while not p.exists():
        if p.parent == p:
            break
        p = p.parent
    return p


def _free_bytes(path: Path) -> int:
    try:
        return shutil.disk_usage(_nearest_existing_ancestor(path)).free
    except OSError:
        return 0


def _ensure_free_space(path: Path, required_bytes: int, context: str) -> bool:
    if required_bytes <= 0:
        return True

    reserve = GLOBAL_CONFIG.get(
        "execution",
        {},
    ).get("disk_space_reserve_bytes", 64 * 1024 * 1024)
    free = _free_bytes(path)

    if free < required_bytes + reserve:
        need_mb = (required_bytes + reserve + 1_048_575) // 1_048_576
        free_mb = (free + 1_048_575) // 1_048_576
        sys.stderr.write(
            f"[ERROR] Insufficient disk space for {context}: "
            f"need {need_mb}MB, have {free_mb}MB at {path}\n"
        )
        return False

    return True


def _path_copy_size(path: Path) -> int:
    try:
        if path.is_dir() and not path.is_symlink():
            total = 0
            for root, _dirs, files in os.walk(path):
                for name in files:
                    fp = Path(root) / name
                    with suppress(OSError):
                        total += fp.lstat().st_size
            return total

        return path.lstat().st_size
    except OSError:
        return 0


def _write_text_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def _write_json_file(path: Path, value: object) -> None:
    _write_text_file(path, json.dumps(value, ensure_ascii=True, indent=2) + "\n")


def _write_backup_info(info_path: Path, lines: list[str]) -> None:
    _write_text_file(info_path, "\n".join(lines) + "\n")


def _move_to_backup(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() or dest.is_symlink():
        raise FileExistsError(f"Backup destination already exists: {dest}")
    shutil.move(src, dest)


def _copy_path_to_backup(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)

    if src.is_dir() and not src.is_symlink():
        shutil.copytree(src, dest, symlinks=True, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dest, follow_symlinks=False)


def _atomic_copy_file(src: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.dusky_tmp_{uuid.uuid4().hex}"
    try:
        if src.is_dir() and not src.is_symlink():
            shutil.copytree(src, tmp, symlinks=True)
        else:
            shutil.copy2(src, tmp, follow_symlinks=False)
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        os.replace(tmp, target)
    finally:
        if tmp.exists() or tmp.is_symlink():
            _delete_path(tmp)


def _is_null_oid(oid: str) -> bool:
    return not oid or oid.strip("0") == ""


def _backup_staged_index(base_cmd: list[str], backup_root: Path) -> Path | None:
    names = _git_check(base_cmd + ["diff", "--cached", "--name-only", "--no-renames",
                                   "-z", "HEAD", "--"], timeout=60)
    paths = [p for p in names.split("\0") if p]
    if not paths:
        return None
    root = backup_root / "staged_index"
    ensure_dir(root, 0o700)
    records: list[dict[str, str]] = []
    for path in paths:
        entries = _git_check(base_cmd + ["ls-files", "-s", "-z", "--", path], timeout=30)
        matching = [e for e in entries.split("\0") if e and e.split("\t", 1)[-1] == path]
        stage_zero = next((e for e in matching if e.split("\t", 1)[0].split()[-1] == "0"), None)
        record = {"path": path, "status": "deleted" if stage_zero is None else "staged"}
        if stage_zero is not None:
            mode, oid, _stage = stage_zero.split("\t", 1)[0].split()
            if mode == "160000":
                raise RuntimeError(f"Cannot preserve staged submodule {path!r} safely")
            proc = subprocess.run(base_cmd + ["cat-file", "blob", oid], env=_git_env(),
                                  capture_output=True, timeout=60)
            if proc.returncode != 0:
                raise RuntimeError(f"Cannot read staged blob for {path!r}: {proc.stderr!r}")
            blob_name = hashlib.blake2b(path.encode("utf-8", "surrogateescape"), digest_size=16).hexdigest() + ".blob"
            blob_path = root / blob_name
            with blob_path.open("xb") as out:
                out.write(proc.stdout)
                out.flush()
                os.fsync(out.fileno())
            record.update({"mode": mode, "oid": oid, "blob": blob_name})
        records.append(record)
        _write_json_file(root / "manifest.json", records)
    return root


def _clean_old_backups(base: Path, keep: int = 10) -> None:
    if not base.exists():
        return

    entries = sorted(
        [p for p in base.iterdir() if p.is_dir() and p.name.startswith("dusky_backup_")],
        reverse=True,
    )

    for old in entries[keep:]:
        if (old / "staged_index" / "manifest.json").exists():
            continue
        if (old / "untracked_collisions").exists() or (old / "failed_incoming").exists():
            continue
        merge_dir = old / "needs_merge"
        if merge_dir.is_dir() and any(merge_dir.rglob("*")):
            continue
        try:
            shutil.rmtree(old)
        except OSError as exc:
            sys.stderr.write(f"[WARN] Could not prune old backup {old}: {exc}\n")


def _collect_incoming_collisions(base_cmd: list[str], remote_ref: str, work_tree: Path,
                                 *, honor_head: bool = True) -> list[str]:
    tracked_out = _git_check(base_cmd + ["ls-files", "-z"], timeout=60)
    tracked = {x for x in tracked_out.split("\0") if x}
    if honor_head:
        head_out = _git_check(base_cmd + ["ls-tree", "-r", "-z", "--name-only", "HEAD"], timeout=60)
        tracked.update(x for x in head_out.split("\0") if x)
    incoming_out = _git_check(base_cmd + ["ls-tree", "-r", "-z", "--name-only", remote_ref], timeout=60)
    incoming = {x for x in incoming_out.split("\0") if x}

    candidates: set[str] = set()

    for inc in incoming:
        target = work_tree / inc

        if target.exists() or target.is_symlink():
            if target.is_dir() and not target.is_symlink():
                if any(item.startswith(inc + "/") for item in tracked):
                    raise RuntimeError(f"Incoming file conflicts with tracked directory: {inc}")
                candidates.add(inc)
            elif inc not in tracked:
                candidates.add(inc)

        for parent in Path(inc).parents:
            rel = str(parent)
            if rel in (".", "/"):
                break

            ancestor = work_tree / rel
            if (
                (ancestor.exists() or ancestor.is_symlink())
                and (ancestor.is_symlink() or not ancestor.is_dir())
                and rel not in tracked
            ):
                candidates.add(rel)
                break

    roots: set[str] = set()
    for cand in candidates:
        if any(cand != other and cand.startswith(other + "/") for other in candidates):
            continue
        roots.add(cand)

    return sorted(roots)


def _reject_protected_incoming(base_cmd: list[str], remote_ref: str, work_tree: Path,
                               git_dir: Path) -> None:
    protected: set[str] = set()
    for path in (git_dir, backups_dir(), logs_dir(), state_dir(), runtime_dir(), askpass_dir()):
        for candidate, root in ((path.absolute(), work_tree.absolute()),
                                (path.resolve(), work_tree.resolve())):
            try:
                rel = candidate.relative_to(root)
            except ValueError:
                continue
            if str(rel) != ".":
                protected.add(rel.as_posix())
    if not protected:
        return
    tree = _git_check(base_cmd + ["ls-tree", "-r", "-z", "--name-only", remote_ref], timeout=60)
    for path in (p for p in tree.split("\0") if p):
        if any(path == p or path.startswith(p + "/") or p.startswith(path + "/") for p in protected):
            raise RuntimeError(f"Incoming path {path!r} overlaps protected storage")


def _backup_collision_roots(work_tree: Path, roots: list[str], collision_dir: Path) -> Path | None:
    if not roots:
        return None

    ensure_dir(collision_dir, 0o700)

    required = sum(_path_copy_size(work_tree / rel) for rel in roots)
    if not _ensure_free_space(collision_dir.parent, required, "collision backup"):
        raise RuntimeError("Not enough disk space for collision backup")

    moved: list[str] = []
    manifest = collision_dir.parent / "untracked_collisions.json"
    _write_json_file(manifest, {"work_tree": str(work_tree), "moved": moved})
    try:
        for rel in roots:
            src = work_tree / rel
            dest = collision_dir / rel
            if not (src.exists() or src.is_symlink()):
                continue
            moved.append(rel)
            _write_json_file(manifest, {"work_tree": str(work_tree), "moved": moved})
            _move_to_backup(src, dest)
    except Exception:
        _restore_collision_dir(collision_dir, work_tree)
        raise

    _write_backup_info(
        collision_dir.with_name("untracked_collisions_INFO.txt"),
        [
            "Dusky untracked work-tree collision backup",
            f"Created: {now_iso()}",
            f"Work tree: {work_tree}",
            f"Moved paths: {len(moved)}",
        ],
    )

    _write_text_file(
        collision_dir.with_name("untracked_collisions_MOVED_PATHS.txt"),
        "\n".join(moved) + "\n",
    )

    return collision_dir


def _restore_collision_dir(collision_dir: Path | None, work_tree: Path,
                           quarantine_dir: Path | None = None) -> None:
    if collision_dir is None or not collision_dir.exists():
        return
    manifest = collision_dir.parent / "untracked_collisions.json"
    record = json.loads(manifest.read_text(encoding="utf-8"))
    if record.get("work_tree") != str(work_tree):
        raise RuntimeError(f"Collision manifest belongs to another work tree: {manifest}")
    for rel in record["moved"]:
        src = collision_dir / rel
        dest = work_tree / rel
        if not (src.exists() or src.is_symlink()):
            if dest.exists() or dest.is_symlink():
                continue  # transfer did not complete
            raise RuntimeError(f"Collision backup is missing: {src}")
        if dest.exists() or dest.is_symlink():
            if quarantine_dir is None:
                raise RuntimeError(f"Cannot restore {rel}: destination exists; backup retained at {src}")
            quarantine = quarantine_dir / rel
            _move_to_backup(dest, quarantine)
        dest.parent.mkdir(parents=True, exist_ok=True)
        _copy_path_to_backup(src, dest)


def _capture_tracked_changes(base_cmd: list[str]) -> dict[str, dict[str, str]]:
    with suppress(Exception):
        _git_check(base_cmd + ["update-index", "-q", "--refresh"], timeout=60)

    output = _git_check(
        base_cmd + ["diff-index", "--raw", "--no-renames", "-z", "HEAD"],
        timeout=120,
    )

    changes: dict[str, dict[str, str]] = {}
    if not output:
        return changes

    parts = output.split("\0")
    i = 0
    parsed = 0

    while i < len(parts) - 1:
        meta = parts[i]
        path = parts[i + 1]
        i += 2

        if not meta or not path:
            continue

        tokens = meta.split()
        if len(tokens) < 5:
            continue

        changes[path] = {
            "status": tokens[4][0],
            "old_mode": tokens[0].lstrip(":"),
            "new_mode": tokens[1],
            "old_oid": tokens[2],
            "new_oid": tokens[3],
        }
        parsed += 1

    if parsed == 0:
        raise RuntimeError("Git reported tracked changes, but the change parser found none.")

    return changes


def _git_head_path_meta(base_cmd: list[str], path: str) -> tuple[str, str]:
    out = _git_check(base_cmd + ["ls-tree", "-z", "HEAD", "--", path], timeout=30)
    for record in out.split("\0"):
        if record and record.split("\t", 1)[1] == path:
            tokens = record.split("\t", 1)[0].split()
            return tokens[0], tokens[2]
    return "", ""


def _backup_user_mods(
    work_tree: Path,
    changes: dict[str, dict[str, str]],
    backup_root: Path,
) -> Path | None:
    if not changes:
        return None

    user_mods_dir = backup_root / "user_mods"
    ensure_dir(user_mods_dir, 0o700)

    required = 0
    for path, info in changes.items():
        if info["status"] == "D":
            continue

        src = work_tree / path
        if src.exists() or src.is_symlink():
            required += _path_copy_size(src)

    if not _ensure_free_space(backup_root.parent, required, "modified-files backup"):
        raise RuntimeError("Not enough disk space for modified-files backup")

    manifest: list[dict[str, object]] = []

    for path, info in changes.items():
        src = work_tree / path
        has_copy = False

        if info["status"] != "D" and (src.exists() or src.is_symlink()):
            dest = user_mods_dir / path
            _copy_path_to_backup(src, dest)
            has_copy = True

        manifest.append({"status": info["status"], "old_mode": info["old_mode"],
                         "old_oid": info["old_oid"], "has_copy": has_copy, "path": path})

    _write_backup_info(
        user_mods_dir.with_name("user_mods_INFO.txt"),
        [
            "Dusky tracked-change backup",
            f"Created: {now_iso()}",
            f"Work tree: {work_tree}",
            f"Changes: {len(changes)}",
        ],
    )

    _write_json_file(user_mods_dir.with_name("user_mods_MANIFEST.json"), manifest)

    return user_mods_dir


def _backup_full_tracked_tree(base_cmd: list[str], work_tree: Path, backup_root: Path) -> Path | None:
    out = _git_check(base_cmd + ["ls-files", "-z"], timeout=60)
    files = [x for x in out.split("\0") if x]

    if not files:
        return None

    full_dir = backup_root / "full_tracked"
    ensure_dir(full_dir, 0o700)

    required = 0
    for rel in files:
        src = work_tree / rel
        if src.exists() or src.is_symlink():
            required += _path_copy_size(src)

    if not _ensure_free_space(backup_root.parent, required, "full tracked-tree backup"):
        raise RuntimeError("Not enough disk space for full tracked-tree backup")

    count = 0

    for rel in files:
        src = work_tree / rel
        if not (src.exists() or src.is_symlink()):
            continue

        dest = full_dir / rel
        _copy_path_to_backup(src, dest)
        count += 1

    _write_backup_info(
        full_dir.with_name("full_tracked_INFO.txt"),
        [
            "Dusky full tracked-tree backup",
            f"Created: {now_iso()}",
            f"Work tree: {work_tree}",
            f"Files: {count}",
        ],
    )

    return full_dir


def _restore_user_mods(
    base_cmd: list[str],
    work_tree: Path,
    changes: dict[str, dict[str, str]],
    user_mods_dir: Path | None,
    needs_merge_dir: Path,
) -> tuple[int, int, int]:
    if not changes or user_mods_dir is None:
        return 0, 0, 0

    restored = 0
    merged = 0
    deleted = 0

    for path, info in changes.items():
        status = info["status"]
        old_mode = info["old_mode"]
        old_oid = info["old_oid"]

        backup_file = user_mods_dir / path
        target = work_tree / path

        new_mode, new_oid = _git_head_path_meta(base_cmd, path)
        old_valid = not _is_null_oid(old_oid)

        same_oid = (new_oid.lower() == old_oid.lower()) if (new_oid and old_oid) else False
        same_mode = (new_mode.lstrip("0") == old_mode.lstrip("0")) if (new_mode and old_mode) else False
        same_meta = same_oid and same_mode

        if status == "D":
            if not new_oid:
                deleted += 1
                continue

            if old_valid and same_meta:
                _delete_path(target)
                deleted += 1
            else:
                ensure_dir(needs_merge_dir, 0o700)
                marker = needs_merge_dir / f"{path}.dusky_deleted"

                marker.parent.mkdir(parents=True, exist_ok=True)
                _write_json_file(marker.with_suffix(marker.suffix + ".json"), {
                    "path": path, "old_mode": old_mode, "old_oid": old_oid,
                    "new_mode": new_mode, "new_oid": new_oid,
                })

                merged += 1

            continue

        if not (backup_file.exists() or backup_file.is_symlink()):
            continue

        safe = False

        if old_valid:
            if same_meta:
                safe = True
        elif not new_oid:
            safe = True

        if safe:
            try:
                _atomic_copy_file(backup_file, target)
                restored += 1
            except OSError:
                ensure_dir(needs_merge_dir, 0o700)
                dest = needs_merge_dir / path
                _copy_path_to_backup(backup_file, dest)
                merged += 1
        else:
            ensure_dir(needs_merge_dir, 0o700)
            dest = needs_merge_dir / path
            _copy_path_to_backup(backup_file, dest)
            merged += 1

    return restored, merged, deleted


def _move_all_to_needs_merge(src_dir: Path | None, needs_merge_dir: Path) -> int:
    if src_dir is None or not src_dir.exists():
        return 0

    ensure_dir(needs_merge_dir, 0o700)

    count = 0

    for src in src_dir.rglob("*"):
        if not (src.is_file() or src.is_symlink()):
            continue

        rel = src.relative_to(src_dir)
        dest = needs_merge_dir / rel

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            _copy_path_to_backup(src, dest)
            count += 1
        except OSError as exc:
            raise RuntimeError(f"Cannot isolate local edit {src}: {exc}") from exc

    return count


def _print_update_preview(base_cmd: list[str], local_head: str, remote_head: str) -> None:
    commits = "?"
    files = 0

    with suppress(Exception):
        commits = _git_check(
            base_cmd + ["rev-list", "--count", f"{local_head}..{remote_head}"],
            timeout=30,
        )

    with suppress(Exception):
        out = _git_check(
            base_cmd + ["diff", "--name-only", "-z", f"{local_head}..{remote_head}"],
            timeout=60,
        )
        files = len([x for x in out.split("\0") if x])

    sys.stdout.write(f"[GIT] Upstream preview: {commits} commit(s), {files} file(s) changed.\n")


def _prompt_choice(
    lines: list[str],
    default: str = "1",
    assume_yes: bool = False,
    yes_choice: str = "2",
) -> str:
    if assume_yes:
        return yes_choice

    if not sys.stdin.isatty():
        sys.stderr.write("[WARN] Non-interactive environment detected. Defaulting to safe choice.\n")
        return default

    for line in lines:
        sys.stdout.write(line)

    sys.stdout.flush()

    r, _, _ = select.select([sys.stdin], [], [], 60)
    if r:
        choice = sys.stdin.readline().strip()
        return choice or default

    return default


def _fetch_upstream_main(base_cmd: list[str], remote: str) -> str:
    last_error: Exception | None = None
    max_attempts = GLOBAL_CONFIG.get("git", {}).get("fetch_max_attempts", 5)
    fetch_timeout = GLOBAL_CONFIG.get("git", {}).get("timeout_fetch", 90)

    for attempt in range(1, max_attempts + 1):
        try:
            _git_check(
                base_cmd
                + [
                    "fetch",
                    "--no-write-fetch-head",
                    remote,
                    f"+refs/heads/{GIT_UPSTREAM_BRANCH}:{GIT_UPSTREAM_REF}",
                ],
                timeout=fetch_timeout,
            )
            return GIT_UPSTREAM_REF
        except Exception as e:
            last_error = e
            if attempt < max_attempts:
                wait = 2 * attempt
                sys.stdout.write(f"[WARN] Fetch attempt {attempt}/{max_attempts} failed. Retrying in {wait}s...\n")
                time.sleep(wait)

    raise RuntimeError(f"git fetch failed after {max_attempts} attempts: {last_error}")


def validate_updated_sources(my_path: Path, wrapper_path: Path) -> None:
    compile(my_path.read_bytes(), str(my_path), "exec")

    if wrapper_path.is_file():
        subprocess.run(
            ["bash", "-n", str(wrapper_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=True,
        )

    config_path = PROFILES_DIR / "settings" / "orchestrator.toml"
    if config_path.is_file():
        with config_path.open("rb") as config_file:
            normalize_global_config(tomllib.load(config_file))
    if PROFILES_DIR.exists():
        for profile_file in PROFILES_DIR.glob("*.toml"):
            load_profile(profile_file)


def run_git_self_update(
    profile: ProfileConfig,
    update_only: bool = False,
    offline: bool = False,
    assume_yes: bool = False,
    preserve_profile: bool = False,
) -> bool:
    if offline or not profile.git_enabled:
        return False
    if _LOCK_FD is None:
        raise RuntimeError("Git update requires the orchestrator lock")
    if not shutil.which("git"):
        raise RuntimeError("git is required for self-update")
    git_dir = resolve_home(profile.git_dir)
    work_tree = resolve_home(profile.git_work_tree).resolve()
    my_path = Path(__file__).resolve()
    wrapper_path = my_path.with_name("orchestrator.sh")
    if not my_path.is_relative_to(work_tree):
        raise RuntimeError(f"Running orchestrator is outside Git work tree {work_tree}")
    base_cmd = ["git", "--no-optional-locks", "--no-advice",
                f"--git-dir={git_dir}", f"--work-tree={work_tree}"]
    repo_state = _git_repo_status(base_cmd, git_dir, work_tree)
    if repo_state == "invalid":
        raise RuntimeError("Git repository is not ready for an update")
    first_checkout = repo_state == "absent"
    if first_checkout:
        git_dir.parent.mkdir(parents=True, exist_ok=True)
        sys.stdout.write(f"[GIT] Cloning bare repository from {profile.git_repo_url}...\n")
        _git_check(["git", "clone", "--bare", "--origin", profile.git_remote,
                    "--branch", GIT_UPSTREAM_BRANCH, profile.git_repo_url, str(git_dir)], timeout=180)
        _git_check(["git", f"--git-dir={git_dir}", "config", f"remote.{profile.git_remote}.fetch",
                    "+refs/heads/*:refs/remotes/" + profile.git_remote + "/*"])
        remote_ref = f"refs/heads/{GIT_UPSTREAM_BRANCH}"
    else:
        sys.stdout.write("[GIT] Fetching upstream updates...\n")
        try:
            remote_ref = _fetch_upstream_main(base_cmd, profile.git_remote)
        except Exception as exc:
            if update_only:
                raise RuntimeError(f"Git fetch failed: {exc}") from exc
            sys.stderr.write(f"[WARN] Git fetch failed; continuing with local scripts: {exc}\n")
            return False
    remote_head = _git_check(base_cmd + ["rev-parse", remote_ref])
    local_head = ""
    if not first_checkout:
        with suppress(subprocess.CalledProcessError):
            local_head = _git_check(base_cmd + ["rev-parse", "--verify", "HEAD"])
    if local_head == remote_head:
        sys.stdout.write("[GIT] Already up to date; keeping local edits.\n")
        return False
    destructive = False
    if local_head:
        merge_base = ""
        with suppress(subprocess.CalledProcessError):
            merge_base = _git_check(base_cmd + ["merge-base", "HEAD", remote_head])
        if merge_base == remote_head:
            sys.stdout.write("[GIT] Local commits are ahead of upstream; keeping them.\n")
            return False
        destructive = merge_base != local_head
        if destructive:
            _print_update_preview(base_cmd, local_head, remote_head)
            choice = _prompt_choice(
                ["\n[DIVERGED OR UNRELATED HISTORY]\n",
                 "  1) Keep local repository [DEFAULT]\n",
                 "  2) Back up and reset to upstream\n",
                 "Choice [1-2] (default: 1): "],
                default="1", assume_yes=assume_yes, yes_choice="2",
            )
            if choice != "2":
                raise RuntimeError("Git update declined")
    _reject_protected_incoming(base_cmd, remote_ref, work_tree, git_dir)
    collision_roots = _collect_incoming_collisions(base_cmd, remote_ref, work_tree,
                                                  honor_head=bool(local_head))
    changes = _capture_tracked_changes(base_cmd) if local_head else {}
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = backups_dir() / f"dusky_backup_{timestamp}_{remote_head[:7]}_{uuid.uuid4().hex[:8]}"
    backup_root.mkdir(mode=0o700)
    collision_dir = backup_root / "untracked_collisions"
    needs_merge_dir = backup_root / "needs_merge"
    user_mods_dir = None
    checkout_started = False
    # Back up the index itself for exact staged-state restoration on rollback.
    index_path = git_dir / "index"
    index_backup = backup_root / "index"
    index_existed = index_path.exists()
    if index_existed:
        shutil.copy2(index_path, index_backup)
    try:
        user_mods_dir = _backup_user_mods(work_tree, changes, backup_root)
        if local_head:
            _backup_staged_index(base_cmd, backup_root)
            _git_check(base_cmd + ["branch", f"dusky/backup/{timestamp}_{uuid.uuid4().hex[:8]}", local_head])
        if destructive:
            _backup_full_tracked_tree(base_cmd, work_tree, backup_root)
        _backup_collision_roots(work_tree, collision_roots, collision_dir)
        checkout_started = True
        if first_checkout:
            _git_check(base_cmd + ["checkout", "-f", GIT_UPSTREAM_BRANCH], timeout=180)
        else:
            _git_check(base_cmd + ["reset", "--hard", remote_head], timeout=180)
        validate_updated_sources(my_path, wrapper_path)
        if changes:
            restored, merged, deleted = _restore_user_mods(
                base_cmd, work_tree, changes, user_mods_dir, needs_merge_dir,
            )
            sys.stdout.write(f"[GIT] Restored {restored} local edits and {deleted} deletions; "
                             f"{merged} edit(s) saved for manual merge at {needs_merge_dir}.\n")
            try:
                validate_updated_sources(my_path, wrapper_path)
            except Exception as exc:
                sys.stderr.write(f"[WARN] Local edits failed validation; isolating them: {exc}\n")
                _git_check(base_cmd + ["reset", "--hard", remote_head], timeout=180)
                _move_all_to_needs_merge(user_mods_dir, needs_merge_dir)
                validate_updated_sources(my_path, wrapper_path)
    except Exception as exc:
        try:
            if checkout_started and local_head:
                _git_check(base_cmd + ["reset", "--hard", local_head], timeout=180)
                if changes:
                    _restore_user_mods(base_cmd, work_tree, changes, user_mods_dir, needs_merge_dir)
            _restore_collision_dir(collision_dir, work_tree, backup_root / "failed_incoming")
            if index_existed:
                _atomic_copy_file(index_backup, index_path)
            elif checkout_started:
                index_path.unlink(missing_ok=True)
        except Exception as recovery_exc:
            raise RuntimeError(f"Git update failed ({exc}); recovery incomplete ({recovery_exc}). "
                               f"Backups: {backup_root}") from recovery_exc
        raise RuntimeError(f"Git update failed: {exc}. Backups: {backup_root}") from exc
    _clean_old_backups(backups_dir(), keep=GLOBAL_CONFIG.get("git", {}).get("backup_retention", 10))
    sys.stdout.write(f"[GIT] Update applied. Backups: {backup_root}\n")
    if update_only:
        return True
    SudoEngine.cleanup()
    args = list(sys.argv[1:])
    if "--no-git-update" not in args:
        args.append("--no-git-update")
    if preserve_profile and not any(arg == "--profile" or arg.startswith("--profile=") or arg == "-p" or arg.startswith("-p") for arg in args):
        args.extend(["--profile", profile.filepath.stem])
    sys.stdout.flush()
    sys.stderr.flush()
    release_lock()
    if wrapper_path.is_file():
        os.execv("/usr/bin/bash", ["bash", str(wrapper_path), *args])
    os.execv(sys.executable, [sys.executable, str(my_path), *args])
    return True


# ==============================================================================
# UI HELPERS
# ==============================================================================
def _status_badge(status: TaskStatus) -> Text:
    match status:
        case TaskStatus.COMPLETED:
            return Text(S("completed"), style=f"bold {PALETTE['success']}")
        case TaskStatus.RUNNING:
            return Text(S("running"), style=f"bold {PALETTE['accent']}")
        case TaskStatus.FAILED:
            return Text(S("failed"), style=f"bold {PALETTE['error']}")
        case TaskStatus.SKIPPED:
            return Text(S("skipped"), style=f"dim {PALETTE['warning']}")
        case _:
            return Text(S("pending"), style=f"dim {PALETTE['muted']}")


def _task_label(task: OrchestratorTask) -> Text:
    txt = Text()
    txt.append(" ")
    txt.append_text(_status_badge(task.status))
    txt.append("  ")

    match task.status:
        case TaskStatus.COMPLETED:
            script_style = f"bold {PALETTE['success']}"
        case TaskStatus.RUNNING:
            script_style = f"bold {PALETTE['fg']}"
        case TaskStatus.FAILED:
            script_style = f"bold {PALETTE['error']}"
        case TaskStatus.SKIPPED:
            script_style = f"dim {PALETTE['warning']}"
        case _:
            script_style = f"dim {PALETTE['muted']}"

    cmd_str = task.script_name
    if task.args:
        cmd_str += f" {' '.join(task.args)}"

    txt.append(cmd_str, style=script_style)

    if task.always:
        txt.append(" ⟳", style=f"bold {PALETTE['accent']}")
    if task.once:
        txt.append(" [once]", style=f"bold {PALETTE['accent']}")
    if task.duration > 0:
        secs = task.duration
        if secs < 60:
            txt.append(f" ({secs:.1f}s)", style=f"dim {PALETTE['warning']}")
        else:
            m = int(secs) // 60
            s = int(secs) % 60
            txt.append(f" ({m}m{s:02d}s)", style=f"dim {PALETTE['warning']}")

    txt.append("  ")
    if task.mode == "S":
        txt.append("SUDO", style=script_style)
    elif task.mode == "GIT":
        txt.append("GIT", style=script_style)
    else:
        txt.append("USER", style=script_style)

    return txt


class TaskSearchScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Dismiss", priority=True),
        Binding("ctrl+n", "cursor_down", "Down", priority=True),
        Binding("ctrl+p", "cursor_up", "Up", priority=True),
    ]

    def __init__(self, tasks: list[OrchestratorTask]):
        super().__init__()
        self.tasks = tasks
        self.results: list[str] = []

    def compose(self) -> ComposeResult:
        with Container(id="search_dialog"):
            yield Static(f"{S('logo')} Fuzzy Task Search", id="search_title")
            yield Input(placeholder="Search tasks...", id="search_input")
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

        limit = GLOBAL_CONFIG.get("ui", {}).get("search_result_limit", 200)

        if not query_lower:
            scored = [(0, t) for t in self.tasks[:limit]]
        else:
            scored_results: list[tuple[int, OrchestratorTask]] = []
            for item in self.tasks:
                target = item.script_name.lower()
                args_text = " ".join(item.args).lower()
                haystack = f"{target} {args_text}"
                score = 0

                if query_lower == target:
                    score += 100
                elif target.startswith(query_lower):
                    score += 50
                elif query_lower in target:
                    score += 30
                elif query_lower in haystack:
                    score += 18

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
                    else:
                        score += 15
                    score += 5

                if score > 0:
                    scored_results.append((score, item))

            scored_results.sort(key=lambda x: (-x[0], x[1].index))
            scored = scored_results

        options: list[Option] = []
        for _, item in scored[:limit]:
            txt = Text()
            txt.append(f"{item.index:03d} ")
            txt.append_text(_status_badge(item.status))
            txt.append(" ")
            txt.append(item.script_name, style="bold white")
            if item.args:
                txt.append(" " + shlex.join(item.args), style="dim")
            txt.append("  ")
            if item.mode == "S":
                txt.append("SUDO", style=f"bold {PALETTE['error']}")
            elif item.mode == "GIT":
                txt.append("GIT", style=f"bold {PALETTE['accent']}")
            else:
                txt.append("USER", style=f"bold {PALETTE['success']}")
            options.append(Option(txt, id=item.state_key))
            self.results.append(item.state_key)

        ol.add_options(options)

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

    def action_cursor_down(self) -> None:
        self.query_one(OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one(OptionList).action_cursor_up()

    @on(events.Click)

    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


class LogSearchScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Dismiss", priority=True),
        Binding("ctrl+n", "cursor_down", "Down", priority=True),
        Binding("ctrl+p", "cursor_up", "Up", priority=True),
    ]

    def __init__(self, title: str, lines: list[str]):
        super().__init__()
        self.title = title
        self.lines = lines

    def compose(self) -> ComposeResult:
        with Container(id="log_search_dialog"):
            yield Static(f"{S('logo')} Log Search: {self.title}", id="log_search_title")
            yield Input(placeholder="Search log...", id="log_search_input")
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

        limit = GLOBAL_CONFIG.get("ui", {}).get("search_result_limit", 200)

        options: list[Option] = []
        for i, line in enumerate(self.lines, start=1):
            if q in line.lower():
                txt = Text()
                txt.append(f"{i:05d} ", style="dim")
                txt.append(line[:300])
                options.append(Option(txt))
                if len(options) >= limit:
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


class ConflictModalScreen(ModalScreen[str]):

    def __init__(self, script_name: str, command: str, exit_code: int | None, error_msg: str):
        super().__init__()
        self.script_name = script_name
        self.command = command
        self.exit_code = exit_code
        self.error_msg = error_msg
        self._finished = False

    def compose(self) -> ComposeResult:
        with Container(id="modal_dialog"):
            yield Static(
                Text(f"{S('failed')} EXECUTION FAULT: {self.script_name}", style="bold red"),
                id="modal_title",
            )

            details = Text()
            details.append("Command:\n", style="bold")
            details.append(self.command + "\n", style="dim")
            details.append("Exit code: ", style="bold")
            details.append(str(self.exit_code) + "\n", style="red bold")
            details.append("Diagnostics:\n", style="bold")
            details.append(self.error_msg, style=f"{PALETTE['warning']}")

            yield Static(details, id="error_details")

            with Horizontal(id="button_bar"):
                yield Button("Retry [R]", variant="primary", id="btn_retry")
                yield Button("Manual TTY [M]", variant="warning", id="btn_manual")
                yield Button("Skip [S]", variant="error", id="btn_skip")
                yield Button("Abort [A]", variant="default", id="btn_abort")

    def on_mount(self) -> None:
        AudioNotifier.play("alert")

    def _done(self, value: str) -> None:
        if self._finished:
            return
        self._finished = True
        self.dismiss(value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "btn_retry":
                self._done("retry")
            case "btn_manual":
                self._done("manual")
            case "btn_skip":
                self._done("skip")
            case _:
                self._done("abort")

    def on_key(self, event: events.Key) -> None:
        key = event.key.lower()
        match key:
            case "r":
                self._done("retry")
            case "m":
                self._done("manual")
            case "s":
                self._done("skip")
            case "a" | "escape" | "q":
                self._done("abort")


class ManualModalScreen(ModalScreen[str]):

    def __init__(self, script_name: str, command: str):
        super().__init__()
        self.script_name = script_name
        self.command = command
        self._finished = False

    def compose(self) -> ComposeResult:
        with Container(id="manual_dialog"):
            yield Static(
                Text(f"{S('running')} MANUAL OVERRIDE: {self.script_name}", style=f"bold {PALETTE['accent']}"),
                id="manual_title",
            )

            details = Text()
            details.append("Command:\n", style="bold")
            details.append(self.command, style="dim")

            yield Static(details)

            with Horizontal(id="button_bar"):
                yield Button("Proceed [Y]", variant="success", id="btn_yes")
                yield Button("Skip [S]", variant="warning", id="btn_skip")
                yield Button("Quit [Q]", variant="error", id="btn_quit")

    def _done(self, value: str) -> None:
        if self._finished:
            return
        self._finished = True
        self.dismiss(value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "btn_yes":
                self._done("yes")
            case "btn_skip":
                self._done("skip")
            case _:
                self._done("quit")

    def on_key(self, event: events.Key) -> None:
        key = event.key.lower()
        match key:
            case "y":
                self._done("yes")
            case "s":
                self._done("skip")
            case "q" | "escape":
                self._done("quit")


class SudoPasswordScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "Cancel", priority=True)]

    def compose(self) -> ComposeResult:
        with Container(id="sudo_dialog"):
            yield Static(f"{S('logo')} Sudo Authentication Required", id="sudo_title")
            yield Input(placeholder="sudo password", password=True, id="sudo_password")
            yield Static("", id="sudo_error")
            with Horizontal(id="button_bar"):
                yield Button("Authenticate", variant="primary", id="btn_auth")
                yield Button("Cancel", variant="default", id="btn_cancel")

    def on_mount(self) -> None:
        self.query_one("#sudo_password", Input).focus()

    async def _submit(self) -> None:
        pw = self.query_one("#sudo_password", Input).value
        ok, err = await asyncio.to_thread(SudoEngine.set_password, pw)
        if ok:
            self.dismiss(True)
        else:
            self.query_one("#sudo_error", Static).update(
                Text(f"Authentication failed: {err}", style="red")
            )
            self.query_one("#sudo_password", Input).value = ""

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_auth":
            await self._submit()
        else:
            self.dismiss(False)

    @on(Input.Submitted)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        await self._submit()

    def action_cancel(self) -> None:
        self.dismiss(False)


class ConfirmQuitScreen(ModalScreen[str]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("y,a,enter", "confirm_abort", "Abort", priority=True),
        Binding("n,c,q", "cancel", "Cancel", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="confirm_dialog"):
            yield Static(f"{S('failed')}  ABORT ORCHESTRATOR?", id="confirm_title")
            yield Static("Are you sure you want to terminate the active sequence?", id="confirm_text")
            with Horizontal(id="button_bar"):
                yield Button(Text("Cancel [N]"), variant="primary", id="btn_cancel", flat=True)
                yield Button(Text("Abort [Y]"), variant="error", id="btn_abort", flat=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss("abort" if event.button.id == "btn_abort" else "cancel")

    def on_key(self, event: events.Key) -> None:
        key = event.key.lower()
        if key in ("a", "y", "enter", "space"):
            self.dismiss("abort")
        elif key in ("c", "n", "escape", "q"):
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
        Binding("escape", "dismiss", "Dismiss", priority=True),
        Binding("f1", "dismiss", "Dismiss", priority=True),
        Binding("question_mark", "dismiss", "Dismiss", priority=True),
        Binding("q", "dismiss", "Dismiss", priority=True),
        Binding("enter", "dismiss", "Dismiss", priority=True),
        Binding("space", "dismiss", "Dismiss", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="help_dialog"):
            yield Static(f"{S('logo')} Dusky Orchestrator Keybindings & Help", id="help_title")

            text = Text()
            text.append("Global Navigation & Shortcuts\n", style=f"bold {PALETTE['accent']}")
            text.append("  F1 / ?         Open / close (toggle) this Help screen\n")
            text.append("  Ctrl+F         Fuzzy search tasks\n")
            text.append("  Ctrl+L         Search current execution log\n")
            text.append("  F              Cycle filter (all/pending/running/completed/failed/skipped)\n")
            text.append("  q / Ctrl+Q / Ctrl+Z   Quit / Abort confirmation dialog\n\n")

            text.append("Pane Resizing & Layout\n", style=f"bold {PALETTE['accent']}")
            text.append("  Alt+Right / Alt+L / ]  Expand left sidebar width\n")
            text.append("  Alt+Left / Alt+H / [   Shrink left sidebar width\n")
            text.append("  Mouse Drag     Click and drag split border left or right\n\n")

            text.append("Tree & Item Selection\n", style=f"bold {PALETTE['accent']}")
            text.append("  j / k or Up/Down       Navigate tasks in left sidebar\n")
            text.append("  Enter                  Select task and open task log view\n")
            text.append("  y / a                  Confirm / Abort in modal dialogs\n")
            text.append("  n / c / Esc            Cancel in modal dialogs\n")

            yield Static(text)

            with Horizontal(id="button_bar"):
                yield Button("Close [F1/?]", variant="primary", id="btn_close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        key = event.key.lower()
        if key in ("escape", "f1", "question_mark", "q", "enter", "space", "?") or event.character in ("?", "q"):
            self.dismiss(None)
            event.stop()

    @on(events.Click)

    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(None)

    def action_dismiss(self) -> None:
        self.dismiss(None)


class FailureSummaryScreen(ModalScreen[str]):
    BINDINGS = [
        Binding("escape", "close", "Close", priority=True),
        Binding("c,q", "close", "Close", priority=True),
        Binding("r", "retry", "Retry", priority=True),
    ]

    def __init__(
        self,
        counters: dict[str, int],
        failed_tasks: list[OrchestratorTask],
        log_root: str,
    ):
        super().__init__()
        self.counters = counters
        self.failed_tasks = failed_tasks
        self.log_root = log_root

    def compose(self) -> ComposeResult:
        with Container(id="summary_dialog"):
            yield Static(f"{S('failed')} Execution Summary", id="summary_title")

            details = Text()
            details.append("Counters:\n", style="bold")
            for k, v in sorted(self.counters.items()):
                details.append(f"  {k}: {v}\n")

            details.append("\nFailed tasks:\n", style="bold red")
            if self.failed_tasks:
                for t in self.failed_tasks:
                    details.append(f"  {t.index:03d}. [{t.mode}] {t.script_name}\n", style=f"{PALETTE['warning']}")
            else:
                details.append("  none\n", style=f"{PALETTE['success']}")

            details.append(f"\nLogs: {self.log_root}\n", style="dim")

            yield Static(details, id="summary_details")

            with Horizontal(id="button_bar"):
                yield Button("Retry Failed [R]", variant="primary", id="btn_retry")
                yield Button("Close [C]", variant="default", id="btn_close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss("retry" if event.button.id == "btn_retry" else "close")

    def on_key(self, event: events.Key) -> None:
        key = event.key.lower()
        if key == "r":
            self.dismiss("retry")
        elif key in ("c", "escape", "q"):
            self.dismiss("close")

    def action_close(self) -> None:
        self.dismiss("close")

    def action_retry(self) -> None:
        self.dismiss("retry")


class CompletionDialog(ModalScreen[bool]):
    """Final dialog shown when the sequence finishes: review logs or quit."""

    BINDINGS = [
        Binding("escape", "dismiss_stay", "View Logs", priority=True),
        Binding("enter,space,v", "dismiss_stay", "View Logs", priority=True),
        Binding("q", "dismiss_quit", "Quit", priority=True),
    ]

    def __init__(
        self,
        title: str = "SEQUENCE COMPLETE",
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
                yield Button(" View Logs ", id="btn_completion_view")
                yield Button(" Quit ", variant="primary", id="btn_completion_quit")

    def on_mount(self) -> None:
        with suppress(Exception):
            self.query_one("#btn_completion_view", Button).focus()

    def action_dismiss_stay(self) -> None:
        self.dismiss(False)

    def action_dismiss_quit(self) -> None:
        self.dismiss(True)

    def on_key(self, event: events.Key) -> None:
        key = event.key.lower()
        if key in ("escape", "enter", "space", "v"):
            self.dismiss(False)
            event.stop()
        elif key == "q":
            self.dismiss(True)
            event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn_completion_quit")

    @on(events.Click)

    def on_background_click(self, event: events.Click) -> None:
        if event.control is self:
            self.dismiss(False)


class AppFooter(Horizontal):

    def compose(self) -> ComposeResult:
        yield Label("[Ctrl+F] Search", classes="footer-shortcut")
        yield Label("[Ctrl+L] Log", classes="footer-shortcut")
        yield Label("[F] Filter", classes="footer-shortcut")
        yield Label("[Ctrl+Q] Quit", classes="footer-shortcut")
        yield Label("[?] Help", classes="footer-shortcut")
        yield Label(f" {S('sep')} ", classes="footer-sep")
        yield Label("Engine: active", id="footer_status")


class ProfileSelectorApp(App):
    ENABLE_COMMAND_PALETTE = False
    CSS = ""

    def __init__(self, profiles: list[ProfileConfig]):
        super().__init__()
        self.profiles = profiles
        self.selected_profile: ProfileConfig | None = None

    def compose(self) -> ComposeResult:
        with Container(id="selector_container"):
            yield Static(f"{S('logo')} DUSKY ORCHESTRATOR PROFILES", id="title")

            options = []
            for i, p in enumerate(self.profiles):
                options.append(
                    Option(f"{i + 1:2d}. {p.name:<25} {p.description}", id=str(i))
                )

            yield OptionList(*options, id="profiles_list")
            yield Static("Enter select | 1-9 quick select | Esc quit", classes="help_text")

    @on(OptionList.OptionSelected)

    def on_selected(self, event: OptionList.OptionSelected) -> None:
        idx: int | None = None
        if event.option and event.option.id is not None:
            idx = int(str(event.option.id))
        elif event.option_index is not None:
            idx = event.option_index

        if idx is not None and 0 <= idx < len(self.profiles):
            self.selected_profile = self.profiles[idx]
            self.exit(return_code=0)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.exit(return_code=1)
            return

        if event.character and event.character in "123456789":
            idx = int(event.character) - 1
            if 0 <= idx < len(self.profiles):
                self.selected_profile = self.profiles[idx]
                self.exit(return_code=0)


# ==============================================================================
# MAIN APP
# ==============================================================================
FILTERS = ["all", "pending", "running", "completed", "failed", "skipped"]


_CHILD_LAUNCHER = """
import fcntl, os, signal, subprocess, sys, termios
if sys.argv[1] == "pty":
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
elif sys.argv[1] == "foreground":
    signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    os.tcsetpgrp(0, os.getpgrp())
    signal.signal(signal.SIGTTOU, signal.SIG_DFL)
if sys.argv[2] == "sudo":
    result = subprocess.run(["sudo", "-A", "-v"])
    if result.returncode:
        sys.exit(result.returncode)
os.execvpe(sys.argv[3], sys.argv[3:], os.environ)
"""


class DuskyOrchestratorApp(App):
    ENABLE_COMMAND_PALETTE = False
    CSS = ""

    BINDINGS = [
        Binding("ctrl+f", "open_search", "Search Tasks", priority=True),
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
        Binding("j", "tree_down", "Tree Down", priority=True),
        Binding("k", "tree_up", "Tree Up", priority=True),
        Binding("up", "scroll_preview_up", "Scroll Log Up", priority=True),
        Binding("down", "scroll_preview_down", "Scroll Log Down", priority=True),
        Binding("pageup", "scroll_preview_page_up", "Page Up", priority=True),
        Binding("pagedown", "scroll_preview_page_down", "Page Down", priority=True),
        Binding("home", "scroll_preview_home", "Home", priority=True),
        Binding("end", "scroll_preview_end", "End", priority=True),
        Binding("tab", "toggle_focus", "Switch Focus"),
        Binding("shift+tab", "toggle_focus", "Switch Focus"),
    ]

    def __init__(
        self,
        profile: ProfileConfig,
        has_sudo: bool,
        manual: bool,
        stop_on_fail: bool,
        force: bool,
        task_timeout: float,
        dry_run: bool = False,
    ):
        super().__init__()

        self.profile = profile
        self.tasks = profile.tasks
        self.has_sudo = has_sudo
        self.manual = manual
        self.stop_on_fail = stop_on_fail
        self.force_flag = force
        self.task_timeout = task_timeout
        self.dry_run = dry_run

        self.active_child_pid: int | None = None
        self.active_child_group: bool = False
        self._active_pty_proc: asyncio.subprocess.Process | None = None
        self.current_pty_master: int | None = None
        self.active_task: OrchestratorTask | None = None
        self.sudo_task: asyncio.Task | None = None

        self.run_id = uuid.uuid4().hex[:8]
        self.state = StateStore(profile)
        self.once_store = OnceStore()
        self.statuses = self.state.statuses()
        self.progressed: set[str] = set()
        self.conditions = ConditionEvaluator()

        self.tree_widget = Tree(f"{S('logo')} Execution Sequence")
        max_lines = GLOBAL_CONFIG.get("ui", {}).get("max_log_lines", 6000)
        self.log_widget = RichLog(
            id="pty_log",
            highlight=False,
            markup=False,
            wrap=True,
            max_lines=max_lines,
        )
        self.progress_bar = ProgressBar(show_eta=False, show_percentage=False, id="progress_bar")
        self.status_label = Label("Initializing orchestrator sequence...", id="status_label")
        self.speed_label = Label("Status: pre-flight | Elapsed: 00:00", id="speed_label")
        self.details_label = Static("No task selected.", id="details_label")

        self.start_time: float = time.monotonic()
        self.finished_time: float | None = None
        self._total_paused_time: float = 0.0
        self._pause_start: float | None = None
        self._prompt_pause_level: int = 0
        self.left_pane_width: int = GLOBAL_CONFIG.get("ui", {}).get("left_pane_width", 38)

        self.tree_nodes_map: dict[str, TreeNode] = {}
        self.logger = RunLogger(profile, self.run_id)

        self._log_widgets: dict[str | None, RichLog] = {}
        self._ui_buffer: list[tuple[str | None, Text]] = []
        self._ui_flush_timer = None

        self._telemetry: dict[str, str] = {}
        self._telemetry_timer = None

        self._log_lines: dict[str | None, deque[str]] = {}
        self.current_log_key: str | None = None
        self.filter_mode = "all"

        self._prompt_counts: dict[str, int] = {}
        self._prompt_last: dict[str, float] = {}
        self._prompt_buffer: str = ""
        self._prompt_retry_task: asyncio.Task | None = None
        self._pty_write_queue: deque[bytes] = deque()
        self._pty_write_event: asyncio.Event | None = None
        self._pty_writer_task: asyncio.Task | None = None
        self._pty_write_bytes = 0
        self.final_exit_code = 0

        self._durations: list[float] = []
        self._always_handled: set[str] = set()
        self._previous_signal_handlers: dict[int, Any] = {}

    def compose(self) -> ComposeResult:
        with Horizontal(id="top_header"):
            yield Static(f"{S('logo')} DUSKY ORCHESTRATOR  [{self.profile.name}]", id="header_title")

        with Horizontal(id="main_dashboard"):
            with Vertical(id="left_pane"):
                yield self.tree_widget

            with Vertical(id="right_pane"):
                with Container(id="telemetry_box"):
                    yield self.status_label
                    yield self.speed_label
                    yield self.progress_bar

                with Container(id="details_box"):
                    yield self.details_label

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

        yield AppFooter()

    def on_mount(self) -> None:
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGHUP):
            self._previous_signal_handlers[signum] = signal.getsignal(signum)
            loop.add_signal_handler(signum, self._terminate, signum)
        with suppress(Exception):
            self.query_one("#log_switcher", ContentSwitcher).current = "pty_log"

        stored_durations = self.state.durations()
        for t in self.tasks:
            if t.state_key in stored_durations and stored_durations[t.state_key] > 0:
                t.duration = stored_durations[t.state_key]

        self.progress_bar.total = max(1, len(self.tasks))
        self._rebuild_tree()

        self.log_system("Environment pre-flight validated. PTY engine online.")

        for t in self.tasks:
            status = self.statuses.get(t.state_key)
            if StateStore.is_done(status):
                if status == "skipped":
                    self.update_task_node_by_key(t.state_key, TaskStatus.SKIPPED)
                else:
                    self.update_task_node_by_key(t.state_key, TaskStatus.COMPLETED)
                self._mark_progress(t)
            elif status == "skipped_condition":
                self.update_task_node_by_key(t.state_key, TaskStatus.SKIPPED)

        self.set_interval(1.0, self._update_overall_status)
        self._update_overall_status()
        self.run_execution_pipeline()

    def _terminate(self, signum: int) -> None:
        self.log_system(f"Termination requested: {signal.Signals(signum).name}")
        self.exit(return_code=128 + signum)

    def _pause_stopwatch(self) -> None:
        if self._prompt_pause_level == 0:
            self._pause_start = time.monotonic()
        self._prompt_pause_level += 1

    def _resume_stopwatch(self) -> None:
        if self._prompt_pause_level > 0:
            self._prompt_pause_level -= 1
            if self._prompt_pause_level == 0 and self._pause_start is not None:
                end_t = self.finished_time if self.finished_time is not None else time.monotonic()
                self._total_paused_time += max(0.0, end_t - self._pause_start)
                self._pause_start = None

    def get_elapsed_seconds(self) -> float:
        end_t = self.finished_time if self.finished_time is not None else time.monotonic()
        current_pause = max(0.0, end_t - self._pause_start) if self._pause_start is not None else 0.0
        return max(0.0, end_t - self.start_time - self._total_paused_time - current_pause)

    @staticmethod

    def _format_elapsed(secs: float) -> str:
        total_seconds = int(secs)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    async def push_screen_wait(self, screen: Any) -> Any:
        self._pause_stopwatch()
        try:
            return await super().push_screen_wait(screen)
        finally:
            self._resume_stopwatch()

    def on_unmount(self) -> None:
        loop = asyncio.get_running_loop()
        for signum, previous in self._previous_signal_handlers.items():
            loop.remove_signal_handler(signum)
            signal.signal(signum, previous)
        self._previous_signal_handlers.clear()
        self._kill_active_child_sync()
        self.logger.close_all()
        self.state.close()
        self.once_store.close()
        SudoEngine.cleanup()

    def on_resize(self, event: events.Resize) -> None:
        if self.current_pty_master is not None:
            self._set_pty_size(self.current_pty_master)

    @on(Tree.NodeSelected)
    @on(Tree.NodeHighlighted)

    def on_node_selected(self, event: Tree.NodeSelected | Tree.NodeHighlighted) -> None:
        node = event.node
        switcher = self.query_one("#log_switcher", ContentSwitcher)

        if node.data == "REPORT":
            switcher.current = "log_report"
            self.current_log_key = "report"
            self._update_details(None)
        elif node == self.tree_widget.root or node.data == "MAIN":
            switcher.current = "pty_log"
            self.current_log_key = None
            self._update_details(None)
        elif node.data and isinstance(node.data, OrchestratorTask):
            switcher.current = f"log_{node.data.state_key}"
            self.current_log_key = node.data.state_key
            self._update_details(node.data)

    def action_open_search(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return

        def on_search_selected(state_key: str | None) -> None:
            if not state_key:
                return
            if node := self.tree_nodes_map.get(state_key):
                with suppress(Exception):
                    self.tree_widget.select_node(node)
                    self.tree_widget.scroll_to_node(node)
            for t in self.tasks:
                if t.state_key == state_key:
                    self.log_system(f"Fuzzy finder navigated to: {t.script_name}")
                    break

        self.push_screen(TaskSearchScreen(self.tasks), on_search_selected)

    def action_search_log(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return

        key = self.current_log_key
        title = "Main Engine Log"
        if key == "report":
            title = "Final Overview Report"
        elif key is not None:
            for t in self.tasks:
                if t.state_key == key:
                    title = t.script_name
                    break

        lines = list(self._log_lines.get(key, deque()))
        self.push_screen(LogSearchScreen(title, lines))

    def action_cycle_filter(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return

        idx = FILTERS.index(self.filter_mode)
        self.filter_mode = FILTERS[(idx + 1) % len(FILTERS)]
        self._rebuild_tree()
        self.log_system(f"Task filter: {self.filter_mode}")

        with suppress(Exception):
            self.query_one("#footer_status", Label).update(
                f"Engine: active | filter: {self.filter_mode}"
            )

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

    def _get_active_visible_log(self) -> RichLog | None:
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

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if isinstance(self.screen, ModalScreen):
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
        if self.finished_time is not None and not isinstance(self.screen, ModalScreen):
            self.exit(return_code=self.final_exit_code)
            return
        if isinstance(self.screen, HelpScreen):
            self.screen.dismiss(None)
            return

        if isinstance(self.screen, (TaskSearchScreen, LogSearchScreen, SudoPasswordScreen, FailureSummaryScreen)):
            self.screen.dismiss(None)
            return

        if isinstance(self.screen, (ConflictModalScreen, ManualModalScreen)):
            with suppress(Exception):
                self.screen.dismiss("abort")
            return

        if isinstance(self.screen, ConfirmQuitScreen):
            self.screen.dismiss("cancel")
            return

        if isinstance(self.screen, CompletionDialog):
            self.screen.dismiss(True)
            return

        if isinstance(self.screen, ModalScreen):
            with suppress(Exception):
                self.screen.dismiss(None)
            return

        async def on_quit_decision(result: str | None) -> None:
            if result == "abort":
                self.log_system("User requested sequence termination.", is_err=True)
                await self._kill_active_child_async()
                self.exit(return_code=130)

        self.push_screen(ConfirmQuitScreen(), on_quit_decision)

    def action_quit_app(self) -> None:
        self.action_request_quit()

    def _show_completion_dialog(self, title: str, message: str, level: str) -> None:
        self.push_screen(
            CompletionDialog(title=title, message=message, level=level),
            self._on_completion_reply,
        )

    def _on_completion_reply(self, quit_now: bool | None) -> None:
        if quit_now:
            self.exit(return_code=self.final_exit_code)
        else:
            self.current_log_key = "report"
            self._update_details(None)
            with suppress(Exception):
                if report_node := self.tree_nodes_map.get("__report__"):
                    self.tree_widget.select_node(report_node)
                    self.tree_widget.scroll_to_node(report_node)
                self.query_one("#log_switcher", ContentSwitcher).current = "log_report"

    def _render_final_overview_block(self) -> None:
        total_duration = self.get_elapsed_seconds()
        failed_tasks = [t for t in self.tasks if self.statuses.get(t.state_key) == "failed"]
        skipped_tasks = [t for t in self.tasks if self.statuses.get(t.state_key) in ("skipped", "skipped_condition")]

        if self.dry_run:
            v_title, v_color = "DRY-RUN", PALETTE["warning"]
        elif failed_tasks:
            v_title, v_color = "WARNINGS" if any(t.ignore_fail for t in failed_tasks) else "ABORTED", PALETTE["error"]
        else:
            v_title, v_color = "SUCCESS", PALETTE["success"]

        timed_tasks = sorted([t for t in self.tasks if t.duration > 0], key=lambda x: x.duration, reverse=True)
        if timed_tasks:
            top = timed_tasks[:3]
            slowest_str = ", ".join(f"{escape(t.script_name)} ({t.duration:.1f}s)" for t in top)
        else:
            slowest_str = "None recorded"

        modes = sorted(list({t.mode for t in self.tasks})) or ["USER", "SUDO"]
        matrix = {m: {"completed": 0, "failed": 0, "skipped": 0, "total": 0} for m in modes}
        for task in self.tasks:
            m = task.mode
            if m not in matrix:
                matrix[m] = {"completed": 0, "failed": 0, "skipped": 0, "total": 0}
            st = self.statuses.get(task.state_key, "pending")
            matrix[m]["total"] += 1
            if st in ("completed", "completed_once"):
                matrix[m]["completed"] += 1
            elif st == "failed":
                matrix[m]["failed"] += 1
            else:
                matrix[m]["skipped"] += 1

        tot_all = len(self.tasks)
        tot_succ = sum(matrix[m]["completed"] for m in matrix)
        tot_fail = sum(matrix[m]["failed"] for m in matrix)
        tot_skip = sum(matrix[m]["skipped"] for m in matrix)

        sep = ASCII_SYMBOLS.get('sep', '|') if ASCII_MODE else UNICODE_SYMBOLS.get('sep', '│')

        lines = [
            "════════════════════════════════════════════════════════════════════════════════",
            f" ◆ FINAL OVERVIEW {sep} [bold {PALETTE['fg']}]{escape(self.profile.name if self.profile else 'Master Profile')}[/] {sep} Verdict: [bold {v_color}]{v_title}[/]",
            "════════════════════════════════════════════════════════════════════════════════",
            "",
            f" {S('timing')} TIMING & PERFORMANCE",
            f"   Total Pipeline Duration : [bold {PALETTE['fg']}]{total_duration:.2f}s[/]",
            f"   • Top Bottlenecks               : {slowest_str}",
            "",
            f" {S('matrix')} SCRIPT EXECUTION MATRIX",
            "   ┌──────────┬──────────┬──────────┬──────────┬──────────┐",
            "   │ MODE     │ SUCCESS  │ FAILED   │ SKIPPED  │ TOTAL    │",
            "   ├──────────┼──────────┼──────────┼──────────┼──────────┤",
        ]

        for mode_name in sorted(matrix.keys()):
            r = matrix[mode_name]
            lines.append(
                f"   │ {mode_name:<8s} │    [bold {PALETTE['success']}]{r['completed']:2d}[/]    │    [bold {PALETTE['error']}]{r['failed']:2d}[/]    │    [dim {PALETTE['warning']}]{r['skipped']:2d}[/]    │    {r['total']:2d}    │"
            )

        lines.extend([
            "   ├──────────┼──────────┼──────────┼──────────┼──────────┤",
            f"   │ TOTAL    │    [bold {PALETTE['success']}]{tot_succ:2d}[/]    │    [bold {PALETTE['error']}]{tot_fail:2d}[/]    │    [dim {PALETTE['warning']}]{tot_skip:2d}[/]    │    {tot_all:2d}    │",
            "   └──────────┴──────────┴──────────┴──────────┴──────────┘",
            "",
        ])

        if failed_tasks:
            hard_failed = [t for t in failed_tasks if not t.ignore_fail]
            soft_failed = [t for t in failed_tasks if t.ignore_fail]

            if hard_failed:
                lines.append(f" [bold {PALETTE['error']}]✗ HARD FAILED TASKS ({len(hard_failed)}):[/]")
                for t in hard_failed:
                    lines.append(f"   • [{t.mode}] {escape(t.script_name)} [bold {PALETTE['error']}](Required - Aborted)[/]")

            if soft_failed:
                lines.append(f" [bold {PALETTE['warning']}]⚠ SOFT FAILED TASKS ({len(soft_failed)}):[/]")
                for t in soft_failed:
                    lines.append(f"   • [{t.mode}] {escape(t.script_name)} [dim {PALETTE['warning']}](Ignored / Allowed to Fail)[/dim]")

            failed_dirs = sorted({str(t.resolved_path.parent) for t in failed_tasks if t.resolved_path})
            if failed_dirs:
                lines.append("   [dim]Debug locations:[/dim]")
                for d in failed_dirs:
                    lines.append(f"     └─ [dim]{escape(d)}[/dim]")
        else:
            lines.append(" [dim]✗ FAILED TASKS     : None[/dim]")

        if skipped_tasks:
            lines.append(f" [bold {PALETTE['warning']}]- SKIPPED TASKS ({len(skipped_tasks)}):[/]")
            for t in skipped_tasks[:12]:
                reason = "condition false" if t.condition else ("once marker valid" if t.once else "ignored failure")
                lines.append(f"   • [{t.mode}] {escape(t.script_name)} [dim]({reason})[/dim]")
            if len(skipped_tasks) > 12:
                lines.append(f"   • ... and {len(skipped_tasks) - 12} more skipped task(s).")
        else:
            lines.append(" [dim]- SKIPPED TASKS    : None[/dim]")

        lines.extend([
            "",
            f" {S('preflight')} SYSTEM & PREFLIGHT",
            f"   • Sudo Mode    : {SudoEngine.mode_name()}",
            f"   • User / Home  : {escape(target_user_pw().pw_name)} ({escape(str(user_home()))})",
            f"   • Log File     : {escape(str(self.logger.root or logs_dir()))}",
            "════════════════════════════════════════════════════════════════════════════════\n",
        ])

        with suppress(Exception):
            rw = self.query_one("#log_report", RichLog)
            rw.clear()
            for line in lines:
                rw.write(Text.from_markup(line))

        self._log_lines["report"] = deque((Text.from_markup(line).plain for line in lines), maxlen=6000)

        for line in lines:
            self._queue_ui(Text.from_markup(line))

        self.current_log_key = "report"
        self._update_details(None)
        with suppress(Exception):
            if report_node := self.tree_nodes_map.get("__report__"):
                self.tree_widget.select_node(report_node)
                self.tree_widget.scroll_to_node(report_node)
            self.query_one("#log_switcher", ContentSwitcher).current = "log_report"

    def action_help(self) -> None:
        if isinstance(self.screen, HelpScreen):
            self.screen.dismiss(None)
            return
        if isinstance(self.screen, ModalScreen):
            return
        self.push_screen(HelpScreen())

    def on_key(self, event: events.Key) -> None:
        if isinstance(self.screen, ModalScreen):
            return

        # Forward keys to ANY live PTY child (non-interactive tasks can still
        # prompt for input, e.g. pacman's [Y/n]). Interactive tasks run via
        # _execute_suspended and never own a PTY master, so gating on
        # task.interactive here made manual answering impossible.
        if self.current_pty_master is not None and self.active_task:
            if event.key == "ctrl+f":
                self.action_open_search()
                event.stop()
                return

            if event.key == "ctrl+l":
                self.action_search_log()
                event.stop()
                return

            if event.key == "ctrl+q":
                self.log_system("Emergency abort requested from PTY session.", is_err=True)
                self.exit(return_code=1)
                event.stop()
                return

            data = self._pty_key_bytes(event)
            if data:
                self._enqueue_pty_input(data)
                event.stop()

    def _pty_key_bytes(self, event: events.Key) -> bytes:
        key = event.key

        if event.is_printable and event.character:
            return event.character.encode("utf-8")

        simple = {
            "enter": b"\r",
            "escape": b"\x1b",
            "tab": b"\t",
            "shift+tab": b"\x1b[Z",
            "backspace": b"\x7f",
            "delete": b"\x1b[3~",
            "home": b"\x1b[H",
            "end": b"\x1b[F",
            "pageup": b"\x1b[5~",
            "pagedown": b"\x1b[6~",
            "up": b"\x1b[A",
            "down": b"\x1b[B",
            "right": b"\x1b[C",
            "left": b"\x1b[D",
            "insert": b"\x1b[2~",
            "f1": b"\x1bOP",
            "f2": b"\x1bOQ",
            "f3": b"\x1bOR",
            "f4": b"\x1bOS",
            "f5": b"\x1b[15~",
            "f6": b"\x1b[17~",
            "f7": b"\x1b[18~",
            "f8": b"\x1b[19~",
            "f9": b"\x1b[20~",
            "f10": b"\x1b[21~",
            "f11": b"\x1b[23~",
            "f12": b"\x1b[24~",
        }

        if key in simple:
            return simple[key]

        if key.startswith("ctrl+"):
            rest = key[5:]
            if rest == "space" or rest == "@":
                return b"\x00"
            if rest == "[":
                return b"\x1b"
            if rest == "\\":
                return b"\x1c"
            if rest == "]":
                return b"\x1d"
            if rest == "^":
                return b"\x1e"
            if rest == "_":
                return b"\x1f"
            if len(rest) == 1 and rest.isalpha():
                return bytes([ord(rest.lower()) - 96])

        return b""

    def _task_visible(self, task: OrchestratorTask) -> bool:
        if self.filter_mode == "all":
            return True
        return task.status.name.lower() == self.filter_mode

    def _rebuild_tree(self) -> None:
        with suppress(Exception):
            self.tree_widget.root.remove_children()
        with suppress(Exception):
            self.tree_widget.clear()

        self.tree_widget.show_guides = False
        self.tree_widget.show_root = False
        self.tree_nodes_map.clear()
        self.tree_widget.root.label = f"{S('logo')} Sequence [{self.filter_mode}]"
        self.tree_widget.root.expand()

        main_node = self.tree_widget.root.add_leaf(
            Text.from_markup(f" [bold {PALETTE['accent']}]CORE[/] Main Engine Log")
        )
        main_node.data = "MAIN"
        self.tree_nodes_map["__main__"] = main_node

        for task in self.tasks:
            if not self._task_visible(task):
                continue
            node = self.tree_widget.root.add_leaf(_task_label(task))
            node.data = task
            self.tree_nodes_map[task.state_key] = node

        report_node = self.tree_widget.root.add_leaf(
            Text.from_markup(f" [bold {PALETTE['success']}]◆ REPORT[/] Final Overview")
        )
        report_node.data = "REPORT"
        self.tree_nodes_map["__report__"] = report_node

    def build_task_tree(self) -> None:
        self._rebuild_tree()

    def _mark_progress(self, task: OrchestratorTask) -> None:
        if task.state_key in self.progressed:
            return
        self.progressed.add(task.state_key)
        self.progress_bar.advance(1)

    def update_task_node_by_key(self, state_key: str, status: TaskStatus) -> None:
        target: OrchestratorTask | None = None

        for t in self.tasks:
            if t.state_key == state_key:
                target = t
                t.status = status
                break

        if target is None:
            return

        node = self.tree_nodes_map.get(state_key)
        if node is None and self._task_visible(target):
            self._rebuild_tree()
            node = self.tree_nodes_map.get(state_key)

        if node is not None:
            node.label = _task_label(target)

        if status == TaskStatus.RUNNING:
            if node is not None:
                with suppress(Exception):
                    self.tree_widget.select_node(node)
                    self.tree_widget.scroll_to_node(node)
            with suppress(Exception):
                self.query_one("#log_switcher", ContentSwitcher).current = f"log_{state_key}"
            self.current_log_key = state_key
            self._update_details(target)

    def _get_log_widget(self, key: str | None) -> RichLog | None:
        if key in self._log_widgets:
            return self._log_widgets[key]

        widget_id = "#pty_log" if key is None else f"#log_{key}"
        with suppress(Exception):
            w = self.query_one(widget_id, RichLog)
            self._log_widgets[key] = w
            return w

        return None

    def _queue_ui(self, text: Text, task_key: str | None = None) -> None:
        self._ui_buffer.append((None, text))
        self._append_log_line(None, text.plain)

        if task_key is not None:
            self._ui_buffer.append((task_key, text))
            self._append_log_line(task_key, text.plain)

        if len(self._ui_buffer) > 1200:
            self._flush_ui()
            return

        if self._ui_flush_timer is None:
            with suppress(Exception):
                self._ui_flush_timer = self.set_timer(0.03, self._flush_ui)

    def _append_log_line(self, key: str | None, line: str) -> None:
        dq = self._log_lines.get(key)
        if dq is None:
            max_deque = GLOBAL_CONFIG.get("ui", {}).get("max_deque_lines", 5000)
            dq = deque(maxlen=max_deque)
            self._log_lines[key] = dq
        dq.append(line.rstrip())

    def _flush_ui(self) -> None:
        if self._ui_flush_timer is not None:
            with suppress(Exception):
                self._ui_flush_timer.stop()
            self._ui_flush_timer = None

        items = self._ui_buffer
        self._ui_buffer = []

        for key, text in items:
            widget = self._get_log_widget(key)
            if widget is not None:
                with suppress(Exception):
                    widget.write(text)

    def _queue_telemetry(
        self,
        pct: str | None = None,
        speed: str | None = None,
        eta: str | None = None,
    ) -> None:
        if pct:
            self._telemetry["pct"] = pct

        if self._telemetry_timer is None:
            with suppress(Exception):
                self._telemetry_timer = self.set_timer(0.2, self._flush_telemetry)

    def _flush_telemetry(self) -> None:
        if self._telemetry_timer is not None:
            with suppress(Exception):
                self._telemetry_timer.stop()
            self._telemetry_timer = None

        pct = self._telemetry.get("pct")

        if pct and self.active_task:
            self.status_label.update(f"{S('running')} {self.active_task.script_name} ({pct})")

    def _update_overall_status(self) -> None:
        total = max(1, len(self.tasks))
        done = len(self.progressed)
        pct = int(done * 100 / total)
        elapsed_str = self._format_elapsed(self.get_elapsed_seconds())

        self.speed_label.update(f"Completed {done}/{total} ({pct}%) | Elapsed: {elapsed_str}")

    def _task_details(self, task: OrchestratorTask | None) -> Text:
        if task is None:
            txt = Text()
            txt.append("Profile: ", style="bold")
            txt.append(self.profile.name + "\n")
            txt.append("Run ID: ", style="bold")
            txt.append(self.run_id + "\n")
            txt.append("Log root: ", style="bold")
            txt.append(_short_home(str(self.logger.root or "disabled")))
            return txt

        total_tasks = len(self.tasks)
        txt = Text()
        txt.append(f"{task.index:03d}. {task.script_name}", style="bold")
        txt.append(f"  [{task.index:03d} / {total_tasks:03d}]\n", style=f"bold {PALETTE['accent']}")
        txt.append("Mode: ", style="bold")
        txt.append(task.mode + "  ")
        txt.append("Status: ", style="bold")
        txt.append(task.status.value + "\n")
        txt.append("Path: ", style="bold")
        txt.append(_short_home(str(task.resolved_path or "unresolved")) + "\n")
        if task.description:
            txt.append("Description: ", style="bold")
            txt.append(task.description + "\n")
        txt.append("Interpreter: ", style="bold")
        txt.append((task.interpreter or "direct") + "\n")
        txt.append("Args: ", style="bold")
        txt.append(shlex.join(task.args) + "\n")
        txt.append("Condition: ", style="bold")
        txt.append(task.condition or "always")
        txt.append("  Timeout: ", style="bold")
        txt.append(str(task.timeout if task.timeout is not None else self.task_timeout))
        txt.append("  Always: ", style="bold")
        txt.append(str(task.always).lower() + "\n")

        txt.append("Interactive: ", style="bold")
        txt.append(str(task.interactive).lower())
        if task.interactive_override is not None:
            txt.append(" (profile override)", style="dim")
        txt.append("\n")

        if task.duration > 0:
            secs = task.duration
            if secs < 60:
                dur_str = f"{secs:.2f}s"
            else:
                m = int(secs) // 60
                s = int(secs) % 60
                dur_str = f"{m}m {s:02d}s ({secs:.2f}s)"
            txt.append("Duration: ", style="bold")
            txt.append(dur_str + "\n", style=f"{PALETTE['warning']}")

        txt.append("Once: ", style="bold")
        if task.once:
            once_valid = self.once_store.marker_valid(task, self.profile.name)
            txt.append(f"true ({task.once_mode}/{task.once_scope})", style="bold")
            txt.append("  Once marker: ", style="bold")
            if once_valid:
                txt.append("valid", style=f"{PALETTE['success']}")
            else:
                txt.append("absent/mismatch", style=f"{PALETTE['warning']}")
            txt.append("\n")
        else:
            txt.append("false\n")

        txt.append("Retry: ", style="bold")
        txt.append(str(task.retry))
        txt.append("  On failure: ", style="bold")
        txt.append(task.on_failure + "\n")
        txt.append("Log: ", style="bold")
        txt.append(_short_home(str(self.logger.task_log_path(task))))
        return txt

    def _update_details(self, task: OrchestratorTask | None) -> None:
        with suppress(Exception):
            self.details_label.update(self._task_details(task))

    def log_system(self, msg: str, is_err: bool = False) -> None:
        prefix_style = "bold red" if is_err else "bold cyan"
        text = Text.assemble(("[SYSTEM] ", prefix_style), (msg, ""))
        self.logger.system(msg)
        self._queue_ui(text, self.active_task.state_key if self.active_task else None)

    def _enqueue_pty_input(self, data: bytes) -> None:
        if self.current_pty_master is None or not data:
            return
        if self._pty_write_bytes + len(data) > 1024 * 1024:
            self.log_system("PTY input queue is full; input was not sent.", is_err=True)
            return
        self._pty_write_queue.append(data)
        self._pty_write_bytes += len(data)
        if self._pty_write_event is not None:
            self._pty_write_event.set()


    async def _pty_writer(self, fd: int) -> None:
        loop = asyncio.get_running_loop()
        event = self._pty_write_event
        if event is None:
            return
        while self.current_pty_master == fd:
            await event.wait()
            while self._pty_write_queue:
                chunk = self._pty_write_queue[0]
                try:
                    count = os.write(fd, chunk)
                except BlockingIOError:
                    ready = loop.create_future()
                    loop.add_writer(fd, lambda: not ready.done() and ready.set_result(None))
                    try:
                        await ready
                    finally:
                        loop.remove_writer(fd)
                    continue
                except OSError as exc:
                    self.log_system(f"PTY input failed: {exc}", is_err=True)
                    return
                if count <= 0:
                    self.log_system("PTY input stopped making progress.", is_err=True)
                    return
                self._pty_write_bytes -= count
                if count == len(chunk):
                    self._pty_write_queue.popleft()
                else:
                    self._pty_write_queue[0] = chunk[count:]
            event.clear()


    async def _retry_prompt_after(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            self._prompt_retry_task = None
            self._maybe_respond_prompt("")
        except asyncio.CancelledError:
            raise
        finally:
            self._prompt_retry_task = None


    def _maybe_respond_prompt(self, text: str) -> None:
        if self.current_pty_master is None:
            return
        if self.active_task is None or self.active_task.interactive:
            return

        self._prompt_buffer = (self._prompt_buffer + ANSI_STRIP_REGEX.sub("", text))[-8192:]
        for _ in range(8):
            matches = [(found.start(), index, name, found, kind)
                       for index, (name, pattern, kind) in enumerate(PROMPT_RULES)
                       if (found := pattern.search(self._prompt_buffer)) is not None]
            if not matches:
                return
            _, _, name, match, kind = min(matches, key=lambda item: item[:2])
            count = self._prompt_counts.get(name, 0)
            max_count = 5 if name == "sudo_password" else 500
            if count >= max_count:
                self._prompt_buffer = self._prompt_buffer[match.end():]
                continue
            now = time.monotonic()
            last = self._prompt_last.get(name, 0.0)
            cooldown = GLOBAL_CONFIG.get("prompts", {}).get("cooldown", 0.35)
            remaining = cooldown - (now - last)
            if remaining > 0:
                if self._prompt_retry_task is None or self._prompt_retry_task.done():
                    self._prompt_retry_task = asyncio.create_task(self._retry_prompt_after(remaining))
                return

            response: bytes | None = None

            if kind == "password":
                if SudoEngine._password:
                    response = SudoEngine._password.encode("utf-8") + b"\r"
                else:
                    self.log_system("Password prompt needs manual input.")
            elif kind == "yes":
                response = b"y\r"
            elif kind == "no":
                response = b"n\r"
            elif kind == "enter":
                response = b"\r"
            if response is not None:
                self._enqueue_pty_input(response)
            self._prompt_counts[name] = count + 1
            self._prompt_last[name] = now
            self._prompt_buffer = self._prompt_buffer[match.end():]

            if response is not None and name != "sudo_password" and count < 5:
                self.log_system(f"Auto-responded to prompt: {name}")

    def handle_pty_line(self, line: str, last_lines: deque | None = None) -> None:
        clean = line.strip("\r\n")
        if not clean:
            return

        stripped = ANSI_STRIP_REGEX.sub("", clean) if "\x1b" in clean else clean

        if last_lines is not None and stripped.strip():
            last_lines.append(stripped.rstrip())

        if self.active_task is not None:
            self.logger.write_task(self.active_task, stripped)

        pct = speed = eta = None

        if "%" in stripped:
            if m := PCT_REGEX.search(stripped):
                pct = m.group(0)

        if "b/s" in stripped.lower():
            if m := SPEED_ETA_REGEX.search(stripped):
                speed, eta = m.group(1), m.group(2)
            elif m := ALT_SPEED_ETA_REGEX.search(stripped):
                speed, eta = m.group(1), m.group(2)

        if pct or speed:
            self._queue_telemetry(pct=pct, speed=speed, eta=eta)

        lower = stripped.lower()
        if "\x1b" not in clean and any(
            k in lower for k in ("error", "failed", "warning", "conflict", "exists in filesystem")
        ):
            text = Text(clean, style="bold red")
        else:
            try:
                text = Text.from_ansi(clean)
            except Exception:
                text = Text(stripped)

        self._queue_ui(text, self.active_task.state_key if self.active_task else None)

    @staticmethod

    def _set_pty_size(fd: int) -> None:
        try:
            size = os.get_terminal_size()
            winsize = struct.pack("HHHH", size.lines, size.columns, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except (OSError, ValueError):
            fallback_cols = GLOBAL_CONFIG.get("ui", {}).get("fallback_pty_columns", 120)
            fallback_lines = GLOBAL_CONFIG.get("ui", {}).get("fallback_pty_lines", 40)
            with suppress(OSError):
                winsize = struct.pack("HHHH", fallback_lines, fallback_cols, 0, 0)
                fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

    async def _kill_proc(self, proc: asyncio.subprocess.Process | None) -> None:
        if proc is None:
            return
        pid = proc.pid
        with suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(pid, signal.SIGTERM)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if proc.returncode is None:
                with suppress(TimeoutError, OSError):
                    await asyncio.wait_for(proc.wait(), timeout=0.1)
            try:
                os.killpg(pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.05)
        else:
            with suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(pid, signal.SIGKILL)
        if proc.returncode is None:
            with suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=2.0)

    def _kill_active_child_sync(self) -> None:
        pid = self.active_child_pid
        if pid is None:
            return

        if self.active_child_group:
            with suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(pid, signal.SIGTERM)
            time.sleep(0.2)
            with suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(pid, signal.SIGKILL)
        else:
            with suppress(ProcessLookupError, PermissionError, OSError):
                os.kill(pid, signal.SIGTERM)
            time.sleep(0.2)
            with suppress(ProcessLookupError, PermissionError, OSError):
                os.kill(pid, signal.SIGKILL)

    async def _kill_active_child_async(self) -> None:
        if self._active_pty_proc is not None:
            await self._kill_proc(self._active_pty_proc)
            return
        pid = self.active_child_pid
        if pid is None:
            return

        if self.active_child_group:
            with suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(pid, signal.SIGTERM)
            await asyncio.sleep(0.2)
            with suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(pid, signal.SIGKILL)
        else:
            with suppress(ProcessLookupError, PermissionError, OSError):
                os.kill(pid, signal.SIGTERM)
            await asyncio.sleep(0.2)
            with suppress(ProcessLookupError, PermissionError, OSError):
                os.kill(pid, signal.SIGKILL)

    def _task_env(self, task: OrchestratorTask) -> dict[str, str]:
        env = os.environ.copy()

        for k in (
            "LD_PRELOAD",
            "LD_AUDIT",
            "LD_DEBUG",
            "LD_ORIGIN_PATH",
            "LD_PROFILE",
            "LD_SHOW_AUXV",
            "LD_USE_LOAD_BIAS",
            "PYTHONSTARTUP",
            "PYTHONHOME",
            "PERL5LIB",
            "RUBYLIB",
            "NODE_OPTIONS",
        ):
            env.pop(k, None)

        pw = target_user_pw()
        home = str(Path(pw.pw_dir))
        shell = pw.pw_shell or "/bin/bash"

        env.update(
            {
                "HOME": home,
                "USER": pw.pw_name,
                "LOGNAME": pw.pw_name,
                "SHELL": shell,
                "TERM": env.get("TERM", "xterm-256color"),
                "COLORTERM": env.get("COLORTERM", "truecolor"),
                "PYTHONUNBUFFERED": "1",
                "PYTHONUTF8": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PAGER": "cat",
                "SYSTEMD_PAGER": "cat",
                "GIT_PAGER": "cat",
                "DUSKY_VERSION": VERSION,
                "DUSKY_RUN_ID": self.run_id,
                "DUSKY_PROFILE_NAME": self.profile.name,
                "DUSKY_PROFILE_FILE": str(self.profile.filepath),
                "DUSKY_TASK_SCRIPT": task.script_name,
                "DUSKY_TASK_PATH": str(task.resolved_path),
                "DUSKY_TASK_MODE": task.mode,
                "DUSKY_TASK_INDEX": str(task.index),
                "DUSKY_TASK_STATE_KEY": task.state_key,
                "DUSKY_TASK_LOG_FILE": str(self.logger.task_log_path(task)),
                "DUSKY_USER": pw.pw_name,
                "DUSKY_TARGET_USER": pw.pw_name,
                "DUSKY_USER_HOME": home,
                "DUSKY_LOG_DIR": str(self.logger.root or logs_dir()),
                "DUSKY_STATE_DIR": str(state_dir()),
                "DUSKY_BACKUP_DIR": str(backups_dir()),
                "DUSKY_FORCE": "1" if (self.force_flag or task.force_flag) else "0",
                "DUSKY_INTERACTIVE": "1" if task.interactive else "0",
                "DUSKY_ALWAYS": "1" if task.always else "0",
            }
        )

        if task.interactive:
            if not env.get("EDITOR"):
                env["EDITOR"] = shutil.which("nano") or shutil.which("vim") or "true"
            if not env.get("VISUAL"):
                env["VISUAL"] = env["EDITOR"]
        else:
            env["EDITOR"] = "true"
            env["VISUAL"] = "true"

        if SudoEngine._askpass_path is not None:
            env["SUDO_ASKPASS"] = str(SudoEngine._askpass_path)

        return env

    def _task_command(self, task: OrchestratorTask) -> list[str]:
        assert task.resolved_path is not None

        args = list(task.args)
        if (self.force_flag or task.force_flag) and "--force" not in args:
            args.append("--force")

        if task.interpreter:
            interp = task.interpreter
            if interp.lower() in ("python", "python3") or interp.lower().startswith("python3."):
                interp = shutil.which(interp) or sys.executable
            else:
                interp = shutil.which(interp) or interp

            if Path(interp).name in ("python", "python3", "bash", "sh", "zsh", "dash") or Path(interp).name.startswith("python3."):
                inner = [interp] + task.interpreter_args + ["--", str(task.resolved_path)] + args
            else:
                inner = [interp] + task.interpreter_args + [str(task.resolved_path)] + args
        else:
            inner = [str(task.resolved_path)] + args

        full_env = self._task_env(task)

        critical_keys = SudoEngine.ENV_KEEP

        env_pairs = [f"{k}={full_env[k]}" for k in critical_keys if k in full_env]

        for k, v in full_env.items():
            if k.startswith("DUSKY_") and not any(secret in k.upper() for secret in ("PASSWORD", "TOKEN", "SECRET")):
                env_pairs.append(f"{k}={v}")

        if task.mode == "S":
            prefix = SudoEngine.sudo_prefix()
            if prefix:
                return prefix + ["env"] + env_pairs + inner

        return inner

    def _task_display_command(self, task: OrchestratorTask) -> str:
        if task.resolved_path is None:
            return task.script_name

        args = list(task.args)
        if (self.force_flag or task.force_flag) and "--force" not in args:
            args.append("--force")

        parts: list[str] = []
        if task.mode == "S":
            parts.append("sudo")

        if task.interpreter:
            interp = task.interpreter
            if interp.lower() in ("python", "python3") or interp.lower().startswith("python3."):
                interp = shutil.which(interp) or sys.executable
            else:
                interp = shutil.which(interp) or interp

            if Path(interp).name in ("python", "python3", "bash", "sh", "zsh", "dash") or Path(interp).name.startswith("python3."):
                parts.extend([interp] + task.interpreter_args + ["--", str(task.resolved_path)])
            else:
                parts.extend([interp] + task.interpreter_args + [str(task.resolved_path)])
        else:
            parts.append(str(task.resolved_path))

        parts.extend(args)
        return shlex.join(parts)

    async def execute_pty_command(
        self,
        cmd: list[str],
        env: dict[str, str],
        timeout: float = 0.0,
        refresh_sudo: bool = False,
    ) -> tuple[bool, int | None, str]:
        try:
            master_fd, slave_fd = pty.openpty()
        except OSError as e:
            self.log_system(f"PTY allocation failed: {e}", is_err=True)
            return False, None, "PTY allocation failed"

        self.current_pty_master = master_fd
        os.set_blocking(master_fd, False)
        self._pty_write_queue.clear()
        self._pty_write_bytes = 0
        self._pty_write_event = asyncio.Event()
        self._prompt_buffer = ""
        self._prompt_counts.clear()
        self._prompt_last.clear()
        self._set_pty_size(slave_fd)

        transport: asyncio.Transport | None = None
        proc: asyncio.subprocess.Process | None = None
        file_obj = None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        last_lines: deque[str] = deque(maxlen=40)
        line_buffer = ""

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", _CHILD_LAUNCHER, "pty", "sudo" if refresh_sudo else "none", *cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                cwd=str(user_home()),
                close_fds=True,
                start_new_session=True,
            )

            with suppress(OSError):
                os.close(slave_fd)
            slave_fd = -1

            self.active_child_pid = proc.pid
            self.active_child_group = True
            self._active_pty_proc = proc
            self._pty_writer_task = asyncio.create_task(self._pty_writer(master_fd))

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
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            chunk = b""  # Linux PTY master reports EIO at normal slave EOF.
                        else:
                            raise RuntimeError(f"PTY output reader failed: {exc}") from exc
                    except Exception as exc:
                        raise RuntimeError(f"PTY output reader failed: {exc}") from exc

                    if not chunk:
                        line_buffer += decoder.decode(b"", final=True)
                        if line_buffer:
                            for line in BRACKET_NEWLINE_RE.split(line_buffer):
                                if line:
                                    self.handle_pty_line(line, last_lines)
                            line_buffer = ""
                        break

                    try:
                        text = decoder.decode(chunk)
                    except Exception:
                        text = chunk.decode("utf-8", errors="replace")

                    if text:
                        self._maybe_respond_prompt(text)

                    line_buffer += text

                    while len(line_buffer) > 32768 and not SINGLE_NEWLINE_RE.search(line_buffer[:32768]):
                        self.handle_pty_line(line_buffer[:32768], last_lines)
                        line_buffer = line_buffer[32768:]

                    while True:
                        m = SINGLE_NEWLINE_RE.search(line_buffer)
                        if not m:
                            break
                        idx = m.start()
                        line = line_buffer[:idx]
                        line_buffer = line_buffer[idx + 1:]
                        if line:
                            self.handle_pty_line(line, last_lines)

            read_task = asyncio.create_task(read_loop())
            wait_task = asyncio.create_task(proc.wait())

            try:
                async with asyncio.timeout(timeout if timeout > 0 else None):
                    done, _ = await asyncio.wait((wait_task, read_task), return_when=asyncio.FIRST_COMPLETED)
                    if read_task in done:
                        read_task.result()
                    code = await wait_task
                    try:
                        await asyncio.wait_for(asyncio.shield(read_task), timeout=2.0)
                    except (TimeoutError, asyncio.TimeoutError):
                        read_task.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await read_task
                    except Exception as exc:
                        raise RuntimeError(f"PTY output processing failed: {exc}") from exc

                    self._flush_ui()
                    return code == 0, code, "\n".join(last_lines)

            except (TimeoutError, asyncio.TimeoutError):
                await self._kill_proc(proc)
                read_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await read_task
                self._flush_ui()
                return False, 124, "\n".join(last_lines)

            except asyncio.CancelledError:
                await self._kill_proc(proc)
                read_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await read_task
                raise

            finally:
                if not wait_task.done():
                    wait_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await wait_task
                if not read_task.done():
                    read_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await read_task

        except asyncio.CancelledError:
            await self._kill_proc(proc)
            raise

        except Exception as e:
            self.log_system(f"PTY execution exception: {e}", is_err=True)
            await self._kill_proc(proc)
            return False, 127, "\n".join(last_lines)

        finally:
            if proc is not None:
                await self._kill_proc(proc)
            if self._pty_writer_task is not None:
                self._pty_writer_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await self._pty_writer_task
            self._pty_writer_task = None
            if self._prompt_retry_task is not None:
                self._prompt_retry_task.cancel()
                self._prompt_retry_task = None
            self._pty_write_queue.clear()
            self._pty_write_bytes = 0
            self._pty_write_event = None
            self.current_pty_master = None
            self.active_child_pid = None
            self.active_child_group = False
            self._active_pty_proc = None

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

    @contextmanager

    def _suspend_ui(self):
        error: BaseException | None = None
        # Keep the event loop responsive without repainting into the stopped
        # terminal writer. Resume the driver before propagating cancellation.
        with self.batch_update(), self.suspend():
            try:
                yield
            except BaseException as exc:
                error = exc
        if error is not None:
            raise error

    async def _execute_suspended(
        self, task: OrchestratorTask, cmd: list[str], env: dict[str, str],
        timeout: float | None = None,
        refresh_sudo: bool = False,
    ) -> tuple[bool, int | None, str]:
        self.log_system(f"Suspending TUI for interactive workflow: {task.script_name}...")
        limit = timeout if timeout is not None else (task.timeout if task.timeout is not None else self.task_timeout)
        proc: asyncio.subprocess.Process | None = None
        with self._suspend_ui():
            tty_fd = None
            old_attr = old_pgrp = None
            try:
                if sys.stdin.isatty():
                    tty_fd = sys.stdin.fileno()
                    old_attr = termios.tcgetattr(tty_fd)
                    old_pgrp = os.tcgetpgrp(tty_fd)
                print(f"\n--- INTERACTIVE WORKFLOW: {task.script_name} ---")
                print(f"Executing: {self._task_display_command(task)}\n", flush=True)
                launch_cmd = [sys.executable, "-c", _CHILD_LAUNCHER,
                              "foreground" if tty_fd is not None else "plain",
                              "sudo" if refresh_sudo else "none", *cmd]
                proc = await asyncio.create_subprocess_exec(
                    *launch_cmd, env=env, cwd=str(user_home()), process_group=0,
                )
                self.active_child_pid = proc.pid
                self.active_child_group = True
                self._active_pty_proc = proc
                try:
                    async with asyncio.timeout(limit if limit > 0 else None):
                        code = await proc.wait()
                except TimeoutError:
                    return False, 124, "interactive task timed out"
                if code in (-signal.SIGINT, 130):
                    return False, 130, "interactive session interrupted by user"
                return code == 0, code, "interactive session"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return False, 127, str(exc)
            finally:
                if proc is not None:
                    await self._kill_proc(proc)
                if tty_fd is not None:
                    if old_pgrp is not None:
                        self._set_foreground_group(tty_fd, old_pgrp)
                    if old_attr is not None:
                        with suppress(OSError, termios.error):
                            termios.tcsetattr(tty_fd, termios.TCSANOW, old_attr)
                self.active_child_pid = None
                self.active_child_group = False
                self._active_pty_proc = None

    @staticmethod

    def _set_foreground_group(fd: int, pgid: int) -> None:
        old_sigttou = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        try:
            os.tcsetpgrp(fd, pgid)
        finally:
            signal.signal(signal.SIGTTOU, old_sigttou)

    async def _ensure_sudo(self) -> bool:
        ok = await asyncio.to_thread(SudoEngine.refresh_sync)
        if ok:
            self.has_sudo = True
            if self.sudo_task is None or self.sudo_task.done():
                self.sudo_task = asyncio.create_task(
                    SudoEngine.maintain_heartbeat(
                        error_callback=lambda msg: self.log_system(msg, is_err=True)
                    )
                )
            return True

        self.log_system("Sudo credentials expired. Re-authentication required.", is_err=True)
        res = await self.push_screen_wait(SudoPasswordScreen())
        if res:
            self.has_sudo = True
            if self.sudo_task is None or self.sudo_task.done():
                self.sudo_task = asyncio.create_task(
                    SudoEngine.maintain_heartbeat(
                        error_callback=lambda msg: self.log_system(msg, is_err=True)
                    )
                )
            return True
        return False

    async def _execute_task_cmd(
        self,
        task: OrchestratorTask,
        cmd: list[str],
        env: dict[str, str],
    ) -> tuple[bool, int | None, str]:
        timeout = task.timeout if task.timeout is not None else self.task_timeout
        refresh_sudo = task.mode == "U" and SudoEngine.mode_name() == "password"
        if task.interactive:
            return await self._execute_suspended(task, cmd, env, timeout=timeout, refresh_sudo=refresh_sudo)
        return await self.execute_pty_command(cmd, env, timeout=timeout, refresh_sudo=refresh_sudo)

    async def _execute_timed_task(
        self, task: OrchestratorTask, cmd: list[str], env: dict[str, str],
        *, suspended: bool = False,
    ) -> tuple[bool, int | None, str]:
        start = time.monotonic()
        try:
            if suspended:
                return await self._execute_suspended(task, cmd, env)
            return await self._execute_task_cmd(task, cmd, env)
        finally:
            task.duration += time.monotonic() - start

    def finish_task(
        self,
        task: OrchestratorTask,
        status: str,
        exit_code: int | None = None,
        note: str = "",
    ) -> None:
        if task.once and status in ("completed", "manual"):
            try:
                self.once_store.mark_success(
                    task,
                    self.profile.name,
                    exit_code,
                    self.run_id,
                )
            except (OSError, sqlite3.DatabaseError) as exc:
                raise RuntimeError(f"Failed to persist once marker for {task.script_name}: {exc}") from exc

        self.state.mark(task, status, exit_code, note)
        self.statuses[task.state_key] = status

        if status in ("completed", "ignored", "manual", "completed_once"):
            self.update_task_node_by_key(task.state_key, TaskStatus.COMPLETED)
        elif status in ("skipped", "skipped_condition"):
            self.update_task_node_by_key(task.state_key, TaskStatus.SKIPPED)
        else:
            self.update_task_node_by_key(task.state_key, TaskStatus.FAILED)

        self._mark_progress(task)
        self.logger.close_task(task, status, exit_code, task.duration)

        if task.duration > 0 and status in ("completed", "ignored", "manual"):
            self._durations.append(task.duration)

        self._update_overall_status()

    def _compute_counters(self) -> dict[str, int]:
        counters: dict[str, int] = {}
        for t in self.tasks:
            status = self.statuses.get(t.state_key, "pending")
            counters[status] = counters.get(status, 0) + 1
        return counters

    async def _run_task_with_policy(self, task: OrchestratorTask) -> str:
        if task.always and task.state_key in self._always_handled:
            return "skipped"

        if task.resolved_path is None:
            self.update_task_node_by_key(task.state_key, TaskStatus.FAILED)
            self.log_system(f"Missing file: {task.script_name}", is_err=True)

            if self.stop_on_fail or task.on_failure == "abort":
                self.log_system("stop-on-fail/abort active. Aborting pipeline.", is_err=True)
                self.exit(return_code=1)
                return "abort"

            if task.on_failure == "skip":
                self.finish_task(task, "skipped", None, "missing file")
                return "skipped"

            if task.on_failure == "continue":
                self.finish_task(task, "failed", None, "missing file")
                return "failed"

            action = await self.push_screen_wait(
                ConflictModalScreen(
                    task.script_name,
                    "unresolved",
                    None,
                    "File missing from disk. Target could not be resolved.",
                )
            )

            if action == "skip":
                self.finish_task(task, "skipped", None, "missing file")
                return "skipped"

            self.log_system("User aborted execution sequence.", is_err=True)
            self.exit(return_code=1)
            return "abort"

        if self.manual:
            self.status_label.update(f"{S('running')} Pending manual approval: {task.script_name}")
            cmd_preview = self._task_display_command(task)
            action = await self.push_screen_wait(ManualModalScreen(task.script_name, cmd_preview))

            if action == "skip":
                self.finish_task(task, "skipped", None, "manual skip")
                return "skipped"

            if action != "yes":
                self.log_system("Manual override: aborting pipeline.", is_err=True)
                self.exit(return_code=1)
                return "abort"

        if task.mode == "S" and not await self._ensure_sudo():
            self.update_task_node_by_key(task.state_key, TaskStatus.FAILED)
            self.log_system("Sudo authentication unavailable.", is_err=True)

            if self.stop_on_fail or task.on_failure == "abort":
                self.exit(return_code=1)
                return "abort"

            if task.on_failure == "skip":
                self.finish_task(task, "skipped", None, "sudo unavailable")
                return "skipped"

            if task.on_failure == "continue":
                self.finish_task(task, "failed", None, "sudo unavailable")
                return "failed"

            action = await self.push_screen_wait(
                ConflictModalScreen(
                    task.script_name,
                    "sudo authentication",
                    None,
                    "Sudo authentication unavailable. Cannot run root task.",
                )
            )

            if action == "skip":
                self.finish_task(task, "skipped", None, "sudo unavailable")
                return "skipped"

            self.exit(return_code=1)
            return "abort"

        self.active_task = task
        self.update_task_node_by_key(task.state_key, TaskStatus.RUNNING)
        self.status_label.update(f"Executing: {task.script_name} [{task.mode}]")
        self._update_details(task)

        self.log_system(f">>> PROCESS INITIATED: {task.script_name}")

        cmd = self._task_command(task)
        env = self._task_env(task)
        self.logger.open_task(task, cmd)

        self._prompt_counts.clear()
        self._prompt_last.clear()

        retries_left = max(0, task.retry)
        task.duration = 0.0

        while True:
            success, code, last = await self._execute_timed_task(task, cmd, env)

            if success:
                self.finish_task(task, "completed", code, "")
                self.log_system(f"Successfully completed: {task.script_name}")
                self.active_task = None
                if task.always:
                    self._always_handled.add(task.state_key)
                return "completed"

            if task.ignore_fail:
                self.log_system(f"Task failed but marked ignore-fail. Continuing: {task.script_name}")
                self.finish_task(task, "ignored", code, last)
                self.active_task = None
                if task.always:
                    self._always_handled.add(task.state_key)
                return "ignored"

            if retries_left > 0:
                retries_left -= 1
                self.log_system(
                    f"Automatic retry for {task.script_name} ({retries_left} left) in {task.retry_delay:.1f}s..."
                )
                await asyncio.sleep(task.retry_delay)
                continue

            policy = task.on_failure
            if self.stop_on_fail:
                policy = "abort"

            if policy == "abort":
                self.finish_task(task, "failed", code, last)
                self.log_system("Failure policy: abort.", is_err=True)
                self.exit(return_code=1)
                self.active_task = None
                return "abort"

            if policy == "continue":
                self.finish_task(task, "failed", code, last)
                self.log_system("Failure policy: continue.", is_err=True)
                self.active_task = None
                return "failed"

            if policy == "skip":
                self.finish_task(task, "skipped", code, last)
                self.log_system("Failure policy: skip.")
                self.active_task = None
                return "skipped"

            if policy == "manual":
                self.log_system(f"Manual intervention TTY: {task.script_name}...")
                m_success, m_code, m_last = await self._execute_timed_task(task, cmd, env, suspended=True)
                if m_success:
                    self.finish_task(task, "manual", m_code, "manual override")
                    self.active_task = None
                    if task.always:
                        self._always_handled.add(task.state_key)
                    return "manual"

                code = m_code
                last = m_last
                self.log_system("Manual intervention failed.", is_err=True)

            # Default: ask
            while True:
                self.state.mark(task, "failed", code, last)
                self.update_task_node_by_key(task.state_key, TaskStatus.FAILED)

                error_msg = f"Last output:\n{last}" if last else "No captured output."
                action = await self.push_screen_wait(
                    ConflictModalScreen(task.script_name, self._task_display_command(task), code, error_msg)
                )

                match action:
                    case "retry":
                        self.log_system(f"Retrying task: {task.script_name}...")
                        self.update_task_node_by_key(task.state_key, TaskStatus.RUNNING)
                        success, code, last = await self._execute_timed_task(task, cmd, env)

                        if success:
                            self.finish_task(task, "completed", code, "")
                            self.log_system(f"Successfully completed: {task.script_name}")
                            self.active_task = None
                            if task.always:
                                self._always_handled.add(task.state_key)
                            return "completed"

                        if task.ignore_fail:
                            self.finish_task(task, "ignored", code, last)
                            self.active_task = None
                            if task.always:
                                self._always_handled.add(task.state_key)
                            return "ignored"

                        # loop back to modal

                    case "manual":
                        self.log_system(f"Manual intervention TTY: {task.script_name}...")
                        m_success, m_code, m_last = await self._execute_timed_task(task, cmd, env, suspended=True)

                        if m_success:
                            self.finish_task(task, "manual", m_code, "manual override")
                            self.active_task = None
                            if task.always:
                                self._always_handled.add(task.state_key)
                            return "manual"

                        code = m_code
                        last = m_last
                        self.log_system("Manual intervention failed.", is_err=True)
                        # loop back to modal

                    case "skip":
                        self.finish_task(task, "skipped", code, last)
                        self.active_task = None
                        return "skipped"

                    case _:
                        self.finish_task(task, "failed", code, last)
                        self.log_system("User aborted execution sequence.", is_err=True)
                        self.exit(return_code=1)
                        self.active_task = None
                        return "abort"

    @work(name="execution_pipeline", exclusive=True)

    async def run_execution_pipeline(self) -> None:
        if self.has_sudo:
            self.sudo_task = asyncio.create_task(
                SudoEngine.maintain_heartbeat(
                    error_callback=lambda msg: self.log_system(msg, is_err=True)
                )
            )

        try:
            while True:
                handled: set[str] = set()
                
                while True:
                    ran_this_pass = False

                    for task in self.tasks:
                        key = task.state_key

                        if key in handled:
                            continue

                        if task.once and not task.always and self.once_store:
                            once_status = self.once_store.check_marker_status(task, self.profile.name)
                            if once_status == "notify_sealed":
                                self.log_system(
                                    f"Run-once:sealed script modified since last run; not re-running: {task.script_name}"
                                )
                                DesktopNotifier.notify(
                                    "Dusky Orchestrator",
                                    f"Sealed script modified: {task.script_name}",
                                    "normal",
                                )
                                self.once_store.mark_sealed_notified(task, self.profile.name)
                                self.finish_task(task, "skipped", None, "run-once:sealed modified")
                                handled.add(key)
                                continue
                            if once_status == "skip":
                                self.log_system(
                                    f"Already completed once; skipping: {task.script_name}"
                                )
                                self.finish_task(task, "completed_once", None, "once marker")
                                handled.add(key)
                                continue

                        if task.always and key in self._always_handled:
                            handled.add(key)
                            continue

                        status = self.statuses.get(key)
                        if StateStore.is_done(status) and not task.always:
                            handled.add(key)
                            continue

                        if task.condition and not self.conditions.check(task.condition):
                            self.log_system(
                                f"Condition not met yet; deferring: {task.script_name} ({task.condition})"
                            )
                            continue

                        ran_this_pass = True

                        outcome = await self._run_task_with_policy(task)
                        handled.add(key)

                        if outcome == "abort":
                            return

                        if self.profile.post_script_delay > 0 and outcome in ("completed", "ignored", "manual"):
                            await asyncio.sleep(self.profile.post_script_delay)

                    if not ran_this_pass:
                        # Mark any remaining condition-blocked tasks as skipped.
                        for task in self.tasks:
                            key = task.state_key
                            if key in handled:
                                continue

                            status = self.statuses.get(key)
                            if StateStore.is_done(status) and not task.always:
                                handled.add(key)
                                continue

                            if task.condition and not self.conditions.check(task.condition):
                                self.finish_task(task, "skipped_condition", None, f"condition:{task.condition}")
                                handled.add(key)

                        break

                self.finished_time = time.monotonic()
                self.status_label.update(f"{S('completed')} Orchestrator sequence finished.")
                elapsed_str = self._format_elapsed(self.get_elapsed_seconds())
                self.speed_label.update(f"Status: complete | Total Elapsed: {elapsed_str}")

                with suppress(Exception):
                    self.query_one("#footer_status", Label).update("Engine: complete")

                self.log_system("Execution sequence finished.")
                counters = self._compute_counters()
                self.logger.write_report(self.profile, self.tasks, self.statuses, counters)
                self._render_final_overview_block()

                failed_tasks = [
                    t for t in self.tasks if self.statuses.get(t.state_key) == "failed"
                ]

                self.final_exit_code = int(bool(failed_tasks) or self.logger.failed_write)
                if failed_tasks:
                    AudioNotifier.play("alert")
                    DesktopNotifier.notify(
                        "Dusky Orchestrator",
                        f"{len(failed_tasks)} task(s) failed.",
                        "critical",
                    )

                    action = await self.push_screen_wait(
                        FailureSummaryScreen(
                            counters,
                            failed_tasks,
                            str(self.logger.root or logs_dir()),
                        )
                    )

                    if action == "retry":
                        self.finished_time = None
                        self.log_system("Retrying failed tasks...")
                        continue

                else:
                    AudioNotifier.play("complete")
                    DesktopNotifier.notify(
                        "Dusky Orchestrator",
                        "Setup completed successfully.",
                        "normal",
                    )

                completed = (
                    counters.get("completed", 0)
                    + counters.get("completed_once", 0)
                    + counters.get("ignored", 0)
                    + counters.get("manual", 0)
                )
                failed = counters.get("failed", 0)
                skipped = counters.get("skipped", 0) + counters.get("skipped_condition", 0)
                summary_lines = (
                    f"Profile: {self.profile.name}\n"
                    f"Completed: {completed}\n"
                    f"Failed: {failed}\n"
                    f"Skipped: {skipped}\n"
                    f"Elapsed: {self._format_elapsed(self.get_elapsed_seconds())}\n"
                    f"Logs: {self.logger.root or logs_dir()}\n\n"
                    "Choose how to continue:"
                )

                self._show_completion_dialog(
                    "SEQUENCE FINISHED" if failed_tasks else "SEQUENCE COMPLETE",
                    summary_lines,
                    "warning" if failed_tasks else "success",
                )

                break

        finally:
            if self.active_task is not None:
                self.finish_task(self.active_task, "failed", self.return_code or 130, "execution interrupted")
            self.active_task = None
            if self.finished_time is None:
                self.logger.write_report(self.profile, self.tasks, self.statuses, self._compute_counters())
            self._flush_ui()

            if self.sudo_task is not None:
                self.sudo_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.sudo_task


# ==============================================================================
# CLI
# ==============================================================================
def run_doctor() -> None:
    print("Dusky Orchestrator Doctor")
    print("=========================")
    print(f"Version:        {VERSION}")
    print(f"Python:         {sys.version.split()[0]}")
    print(f"Executable:     {sys.executable}")
    print(f"UID/EUID:       {os.getuid()}/{os.geteuid()}")
    print(f"Target user:    {target_user_pw().pw_name}")
    print(f"Home:           {user_home()}")
    print(f"State dir:      {state_dir_path()}")
    paths = GLOBAL_CONFIG.get("paths", {})
    docs = Path(paths.get("documents_dir", "Documents")).expanduser()
    docs = docs if docs.is_absolute() else user_home() / docs
    print(f"Logs dir:       {docs / paths.get('logs_subdir', 'logs')}")
    print(f"Backups dir:    {docs / paths.get('backups_subdir', 'dusky_backups')}")
    print(f"Cache dir:      {xdg_cache_home() / paths.get('namespace', 'dusky')}")
    print(f"Runtime dir:    {os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{target_user_pw().pw_uid}')}")
    print(f"Profiles dir:   {PROFILES_DIR}")

    try:
        import textual

        print(f"Textual:        {getattr(textual, '__version__', 'unknown')}")
    except Exception as e:
        print(f"Textual:        unavailable ({e})")

    try:
        import rich

        rich_ver = getattr(rich, "__version__", None)
        if not rich_ver:
            with suppress(Exception):
                rich_ver = importlib_metadata.version("rich")
        print(f"Rich:           {rich_ver or 'unknown'}")
    except Exception as e:
        print(f"Rich:           unavailable ({e})")

    print(f"git:            {shutil.which('git') or 'missing'}")
    print(f"sudo:           {shutil.which('sudo') or 'missing'}")
    print(f"pacman:         {shutil.which('pacman') or 'missing'}")
    print(f"systemctl:      {shutil.which('systemctl') or 'missing'}")
    print(f"notify-send:    {shutil.which('notify-send') or 'missing'}")
    print(f"pw-play:        {shutil.which('pw-play') or 'missing'}")
    print(f"paplay:         {shutil.which('paplay') or 'missing'}")

    if PROFILES_DIR.exists():
        profiles = sorted(PROFILES_DIR.glob("*.toml"))
        print(f"Profiles found: {len(profiles)}")
        for p in profiles:
            print(f"  - {p.name}")
            try:
                cfg = load_profile(p)
                print(f"    tasks: {len(cfg.tasks)}")
                missing_dirs = [d for d in cfg.search_dirs if not Path(d).exists()]
                if missing_dirs:
                    print(f"    missing search dirs: {len(missing_dirs)}")
            except Exception as e:
                print(f"    error: {e}")
    else:
        print("Profiles found: 0")


def print_explain(profile: ProfileConfig) -> None:
    temp_state = StateStore(profile, read_only=True)
    statuses = temp_state.statuses()
    temp_state.close()

    once_store = OnceStore(read_only=True)
    cond = ConditionEvaluator()

    print(f"Execution plan for {profile.name}:\n")

    for t in profile.tasks:
        status = statuses.get(t.state_key, "pending")
        condition_result = True if not t.condition else cond.check(t.condition)
        volatile = cond._volatile(t.condition)
        once_marker = once_store.marker_valid(t, profile.name) if t.once else False

        if t.once and once_marker and not t.always:
            action = "skip(once)"
        elif StateStore.is_done(status) and not t.always:
            action = "skip(done)"
        elif t.condition and not condition_result:
            action = "defer-or-skip"
        else:
            action = "run"

        print(f"{t.index:03d}. [{t.mode}] {t.script_name}")
        print(f"    path:        {t.resolved_path}")
        print(f"    interpreter: {t.interpreter or 'direct'}")
        print(f"    args:        {shlex.join(t.args)}")
        print(f"    interactive: {t.interactive}")
        print(f"    condition:   {t.condition or 'always'}")
        print(f"    cond_result: {condition_result}")
        print(f"    volatile:    {volatile}")
        print(f"    always:      {t.always}")
        print(f"    once:        {t.once}")

        if t.once:
            print(f"    once_mode:   {t.once_mode}")
            print(f"    once_scope:  {t.once_scope}")
            print(f"    once_marker: {once_marker}")

        print(f"    retry:       {t.retry}")
        print(f"    on_failure:  {t.on_failure}")
        print(f"    timeout:     {t.timeout if t.timeout is not None else 'global'}")
        print(f"    state:       {status}")
        print(f"    action:      {action}")
        print()

    once_store.close()


def main() -> None:
    args = EARLY_ARGS if EARLY_ARGS is not None else parse_command_line()

    global ASCII_MODE
    if args.ascii:
        ASCII_MODE = True

    if args.doctor:
        run_doctor()
        sys.exit(0)

    check_runtime_versions()
    ensure_not_root(args.allow_root)

    if args.list_once:
        store = OnceStore(read_only=True)
        try:
            store.print_list()
        finally:
            store.close()
        return

    if args.forget_once:
        if not acquire_lock():
            sys.exit(1)
        store = OnceStore()
        try:
            for script in args.forget_once:
                count = store.forget(script)
                print(f"Forgot {count} once marker(s) for: {script}")
        finally:
            store.close()
        return

    profiles = discover_profiles()
    if not profiles:
        Console(stderr=True).print("[bold yellow]:: No profiles found in profiles/ directory.[/bold yellow]")
        sys.exit(1)

    if args.list:
        for i, p in enumerate(profiles, start=1):
            print(f"{i:2d}. {p.filepath.stem}: {p.name} ({p.description})")
        sys.exit(0)

    profile_query = (args.profile or os.environ.get("DUSKY_PROFILE", "")).strip()

    def resolve_profile(query: str) -> ProfileConfig | None:
        if not query:
            return None
        if query.isdigit():
            idx = int(query) - 1
            if 0 <= idx < len(profiles):
                return profiles[idx]
        q_lower = query.lower()
        exact = [p for p in profiles if (
                p.filepath.stem.lower() == q_lower
                or p.name.lower() == q_lower
                or p.filepath.name.lower() == q_lower
            )]
        if len(exact) > 1:
            raise ValueError(f"Ambiguous profile '{query}': {', '.join(p.filepath.name for p in exact)}")
        if exact:
            return exact[0]
        partial = [p for p in profiles if (
                q_lower in p.filepath.stem.lower()
                or q_lower in p.name.lower()
            )]
        if len(partial) > 1:
            raise ValueError(f"Ambiguous profile '{query}': {', '.join(p.filepath.name for p in partial)}")
        if partial:
            return partial[0]
        return None

    selected_profile = resolve_profile(profile_query) if profile_query else None
    if profile_query and selected_profile is None:
        raise ValueError(f"Profile '{profile_query}' not found")
    inspection = args.dry_run or args.explain or args.list_scripts or args.reset
    if inspection and selected_profile is None:
        if len(profiles) == 1:
            selected_profile = profiles[0]
        else:
            raise ValueError("Specify --profile for this inspection command")

    palette = PALETTE
    if selected_profile is None and not args.git_update_only:
        ProfileSelectorApp.CSS = build_selector_css(palette)
        selector = ProfileSelectorApp(profiles)
        selector.run()
        selected_profile = selector.selected_profile
        if selected_profile is None:
            sys.exit(1)

    git_check_profile = selected_profile or next((p for p in profiles if p.git_enabled), None)

    if args.list_scripts:
        assert selected_profile is not None
        print(f"Sequence for {selected_profile.name}:")
        for t in selected_profile.tasks:
            print(f"{t.index:3d}. [{t.mode}] {t.script_name} {shlex.join(t.args)}".rstrip())
        return

    if args.explain or args.dry_run:
        assert selected_profile is not None
        if not resolve_and_validate_manifest(selected_profile):
            raise RuntimeError("Manifest validation failed")
        if args.explain:
            print_explain(selected_profile)
            return

    locked = False
    if not inspection or args.reset:
        if not acquire_lock():
            sys.exit(1)
        locked = True

    if args.git_update_only:
        if args.no_git_update:
            return  # post-update restart; never start installation
        if git_check_profile is None or not git_check_profile.git_enabled:
            raise RuntimeError("Selected profile has no Git update enabled")
        run_git_self_update(git_check_profile, update_only=True, offline=args.offline,
                            assume_yes=args.yes, preserve_profile=bool(profile_query))
        return

    if not inspection and not args.no_git_update and not args.offline and git_check_profile:
        run_git_self_update(git_check_profile, update_only=False, offline=False,
                            assume_yes=args.yes, preserve_profile=True)

    assert selected_profile is not None

    if args.reset or args.reset_and_run:
        if not locked:
            if not acquire_lock():
                sys.exit(1)
            locked = True
        reset_state_for_profile(selected_profile)
        if args.reset and not args.reset_and_run:
            sys.exit(0)

    if not locked and not inspection:
        if not acquire_lock():
            sys.exit(1)
        locked = True

    if not inspection and not resolve_and_validate_manifest(selected_profile):
        Console(stderr=True).print("[bold red]Manifest validation failed.[/bold red]")
        sys.exit(1)

    if args.dry_run:
        temp_state = StateStore(selected_profile, read_only=True)
        statuses = temp_state.statuses()
        temp_state.close()

        once_store = OnceStore(read_only=True)

        print("Dry-run validation complete.\n")
        for t in selected_profile.tasks:
            state = statuses.get(t.state_key, "pending")
            once_marker = once_store.marker_valid(t, selected_profile.name) if t.once else False

            print(f"{t.index:03d}. [{t.mode}] {t.script_name}")
            print(f"    path:        {t.resolved_path}")
            print(f"    interpreter: {t.interpreter or 'direct'}")
            print(f"    args:        {shlex.join(t.args)}")
            print(f"    interactive: {t.interactive}")

            if t.interactive_override is not None:
                print(f"    interactive_override: {t.interactive_override}")

            print(f"    condition:   {t.condition or 'always'}")
            print(f"    timeout:     {t.timeout if t.timeout is not None else args.task_timeout}")
            print(f"    checksum:    {t.checksum}")
            print(f"    always:      {t.always}")
            print(f"    once:        {t.once}")

            if t.once:
                print(f"    once_mode:   {t.once_mode}")
                print(f"    once_scope:  {t.once_scope}")
                print(f"    once_marker: {once_marker}")

            print(f"    retry:       {t.retry}")
            print(f"    on_failure:  {t.on_failure}")
            print(f"    state:       {state}")
            print()

        once_store.close()
        sys.exit(0)

    temp_state = StateStore(selected_profile)
    statuses = temp_state.statuses()
    temp_state.close()

    once_store = OnceStore()
    has_sudo = (
        any(t.mode == "S" for t in selected_profile.tasks)
        or any(
            not (t.once and not t.always and once_store.marker_valid(t, selected_profile.name))
            and (
                t.always
                or not StateStore.is_done(statuses.get(t.state_key))
            )
            for t in selected_profile.tasks
        )
    ) and bool(shutil.which("sudo"))
    once_store.close()

    if has_sudo:
        password_file = Path(args.sudo_password_file).expanduser() if args.sudo_password_file else None
        if not SudoEngine.preflight(cli_password=args.sudo_password, password_file=password_file):
            sys.exit(1)

    policy = selected_profile.policy

    manual = args.manual or bool(policy.get("manual", False))
    stop_on_fail = args.stop_on_fail or bool(policy.get("stop_on_fail", False))
    force = args.force or bool(policy.get("force", False))

    task_timeout = max(0.0, args.task_timeout)
    if task_timeout == 0.0:
        with suppress(Exception):
            task_timeout = max(0.0, float(policy.get("task_timeout", 0.0)))

    AudioNotifier.enabled = (not args.no_audio) and bool(policy.get("audio", True))
    DesktopNotifier.enabled = (not args.no_notify) and bool(policy.get("notify", True))
    inhibit_enabled = (not args.no_inhibit) and bool(policy.get("inhibit_sleep", True))

    inhibitor = SleepInhibitor(inhibit_enabled)

    try:
        DuskyOrchestratorApp.CSS = build_app_css(palette)

        app = DuskyOrchestratorApp(
            profile=selected_profile,
            has_sudo=has_sudo,
            manual=manual,
            stop_on_fail=stop_on_fail,
            force=force,
            task_timeout=task_timeout,
            dry_run=args.dry_run,
        )

        app.run()
        sys.exit(app.return_code)

    except KeyboardInterrupt:
        Console(stderr=True).print("\n[bold red]:: Interrupted by user.[/]")
        sys.exit(130)

    finally:
        inhibitor.close()
        SudoEngine.cleanup()


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        with suppress(Exception):
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        sys.exit(0)
    except (OSError, ValueError, RuntimeError, sqlite3.DatabaseError) as exc:
        sys.stderr.write(f"[ERROR] {exc}\n")
        sys.exit(1)
    except KeyboardInterrupt:
        Console(stderr=True).print("\n[bold red]:: Interrupted by user.[/bold red]")
        sys.exit(130)
