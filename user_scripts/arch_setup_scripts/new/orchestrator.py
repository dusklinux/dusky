#!/usr/bin/env python3
# DUSKY_BOOTSTRAP_PACKAGES: python python-textual python-rich git
# dusky_interactive=true
# ==============================================================================
# DUSKY ARCH LINUX MASTER ORCHESTRATOR
# ==============================================================================
# Target: Arch Linux bleeding edge | Python 3.14+ | Textual 8.2.8+ | systemd 261+
# ==============================================================================
import sys

if sys.version_info < (3, 14):
    sys.stderr.write("[FATAL] Python 3.14+ is required.\n")
    sys.exit(1)

import argparse
import asyncio
import atexit
import base64
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
from contextlib import suppress, nullcontext, contextmanager
from dataclasses import dataclass, field
from enum import Enum
from importlib import metadata as importlib_metadata
from pathlib import Path

try:
    from rich.console import Console
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

VERSION = "19.0.0"
SCRIPT_DIR: Path = Path(__file__).resolve().parent
PROFILES_DIR: Path = SCRIPT_DIR / "profiles"
ASCII_MODE = False
MAX_DEFER_PASSES = 3

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
    if sys.version_info < (3, 14):
        sys.stderr.write("[FATAL] Python 3.14+ is required.\n")
        sys.exit(1)

    try:
        textual_version = importlib_metadata.version("textual")
        parsed = (version_tuple(textual_version) + (0, 0, 0))[:3]
        if parsed < (8, 2, 8):
            sys.stderr.write(
                f"[FATAL] Textual 8.2.8+ is required. Installed: {textual_version}\n"
            )
            sys.exit(1)
    except Exception:
        pass


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

    if os.geteuid() == 0 and pw.pw_uid != 0:
        candidates.append(Path(f"/run/user/{pw.pw_uid}") / "dusky")
    else:
        env = os.environ.get("XDG_RUNTIME_DIR")
        if env:
            candidates.append(Path(env) / "dusky")
        candidates.append(Path(f"/run/user/{pw.pw_uid}") / "dusky")

    candidates.append(Path(tempfile.gettempdir()) / f"dusky-{pw.pw_uid}" / "run")

    for candidate in candidates:
        try:
            return ensure_dir(candidate, 0o700)
        except OSError:
            continue

    return ensure_dir(Path.cwd() / ".dusky-run", 0o700)


@functools.cache
def documents_root() -> Path:
    root = user_home() / "Documents"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        sys.stderr.write(f"[FATAL] Cannot create Documents root {root}: {e}\n")
        sys.exit(1)
    return root


def _documents_subdir(name: str) -> Path:
    path = documents_root() / name
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
    return _documents_subdir("state")


@functools.cache
def logs_dir() -> Path:
    return _documents_subdir("logs")


@functools.cache
def backups_dir() -> Path:
    return _documents_subdir("dusky_backups")


@functools.cache
def cache_dir() -> Path:
    pw = target_user_pw()
    return safe_dir(
        xdg_cache_home() / "dusky",
        Path(tempfile.gettempdir()) / f"dusky-{pw.pw_uid}" / "cache",
    )


@functools.cache
def askpass_dir() -> Path:
    return ensure_dir(cache_dir() / "askpass", 0o700)


def lock_path() -> Path:
    return runtime_dir() / "orchestrator.lock"


# ==============================================================================
# REGEX
# ==============================================================================
_INTERACTIVE_RE = re.compile(
    r"^\s*#\s*dusky_interactive\s*=\s*(?:true|1)\b",
    re.IGNORECASE,
)
_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
ANSI_STRIP_REGEX = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x1b]*(?:\x07|\x1B\\))"
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

PROMPT_RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "sudo_password",
        re.compile(
            r"(?i)(\[sudo\] password for [^:]+:|^\s*Password:\s*$|sudo: a password is required|Password:\s*$)",
            re.MULTILINE,
        ),
        "password",
    ),
    (
        "pgp_import",
        re.compile(
            r"(?i)(::\s*Import PGP key.*\?\s*\[Y/n\]|::\s*Append key\?.*\[Y/n\]|Import PGP key.*\?\s*\[Y/n\])",
            re.MULTILINE,
        ),
        "yes",
    ),
    (
        "pacman_proceed",
        re.compile(
            r"(?i)::\s*(Proceed with (?:installation|download|upgrade)|Continue (?:installation|download|upgrade)).*\?\s*\[Y/n\]",
            re.MULTILINE,
        ),
        "yes",
    ),
    (
        "pacman_replace",
        re.compile(r"(?i)::\s*Replace\s+.*\?\s*\[Y/n\]", re.MULTILINE),
        "yes",
    ),
    (
        "pacman_remove_conflict",
        re.compile(r"(?i)::\s*Remove conflicting file.*\?\s*\[Y/n\]", re.MULTILINE),
        "yes",
    ),
    (
        "aur_proceed",
        re.compile(
            r"(?i)(Proceed with installation\?|Continue building\?|Continue installing\?|::\s*Proceed with (?:installation|download|build).*\?\s*\[Y/n\])",
            re.MULTILINE,
        ),
        "yes",
    ),
    (
        "generic_yes",
        re.compile(r"(?i)\[Y/n\]|\(Y/n\)|\[y/N\]|\(y/N\)", re.MULTILINE),
        "yes",
    ),
]


# ==============================================================================
# MODEL
# ==============================================================================
class TaskStatus(str, Enum):
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
    interpreter: str = "bash"
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
    search_dirs: list[str] = field(default_factory=list)
    conflict_resolutions: dict[str, str] = field(default_factory=dict)
    tasks: list[OrchestratorTask] = field(default_factory=list)
    policy: dict = field(default_factory=dict)


# ==============================================================================
# UTILITIES
# ==============================================================================
def resolve_home(path_str: str) -> Path:
    p = Path(os.path.expandvars(path_str.strip())).expanduser()
    if not p.is_absolute():
        p = SCRIPT_DIR / p
    return p


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", str(name)).strip("._")
    return cleaned or "unnamed"


def now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_ts() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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

    def __init__(self, profile: ProfileConfig):
        self.path = state_dir() / f"{safe_filename(profile.name)}.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute("PRAGMA busy_timeout=5000;")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS state (
                state_key TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                script TEXT,
                checksum TEXT,
                exit_code INTEGER,
                note TEXT,
                updated TEXT
            )
            """
        )
        self.conn.commit()

    def statuses(self) -> dict[str, str]:
        cur = self.conn.execute("SELECT state_key, status FROM state")
        return {str(k): str(v) for k, v in cur.fetchall()}

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
                (state_key, status, script, checksum, exit_code, note, updated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.state_key,
                status,
                task.script_name,
                task.checksum,
                exit_code,
                note,
                now_iso(),
            ),
        )
        self.conn.commit()

    def reset(self) -> None:
        with suppress(Exception):
            self.conn.close()
        self.path.unlink(missing_ok=True)

    def close(self) -> None:
        with suppress(Exception):
            self.conn.close()


def reset_state_for_profile(profile: ProfileConfig) -> None:
    path = state_dir() / f"{safe_filename(profile.name)}.db"
    path.unlink(missing_ok=True)
    print(f"Reset state for {profile.name} at {path}")


class OnceStore:
    def __init__(self) -> None:
        self.path = state_dir() / "once.db"
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute("PRAGMA busy_timeout=5000;")
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
        if not task.once:
            return False

        key = self.make_key(task, profile_name)
        cur = self.conn.execute(
            "SELECT checksum, once_mode FROM once_markers WHERE marker_key = ?",
            (key,),
        )
        row = cur.fetchone()
        if row is None:
            return False

        stored_checksum, stored_mode = row
        if task.once_mode == "forever" or stored_mode == "forever":
            return True

        return bool(task.checksum) and stored_checksum == task.checksum

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

        cur = self.conn.execute(
            """
DELETE FROM once_markers
WHERE script_name = ?
   OR resolved_path = ?
   OR script_name LIKE ?
""",
            (script, script, f"%/{script}"),
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
        self.enabled = False
        self.root: Path | None = None
        self.main_path: Path | None = None
        self._main = None
        self._task_files: dict[str, object] = {}
        self._task_counts: dict[str, int] = {}
        self.run_id = run_id

        try:
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.root = logs_dir() / f"{stamp}_{safe_filename(profile.name)}_{run_id}"
            ensure_dir(self.root, 0o700)
            self.main_path = self.root / "orchestrator.log"
            self._main = open(self.main_path, "a", encoding="utf-8", errors="replace")
            self.enabled = True
            self.system(f"Logging started for profile: {profile.name}")
            self.system(f"Run ID: {run_id}")
        except OSError as e:
            sys.stderr.write(f"[FATAL] Cannot create log directory or main log file under {logs_dir()}: {e}\n")
            sys.exit(1)

    def system(self, msg: str) -> None:
        if not self.enabled or self._main is None:
            return
        with suppress(OSError):
            self._main.write(f"[{now_ts()}] {msg}\n")
            self._main.flush()

    def task_log_path(self, task: OrchestratorTask) -> Path:
        if self.root is None:
            return Path("/dev/null")
        return self.root / f"{task.index:03d}_{safe_filename(task.script_name)}.log"

    def open_task(self, task: OrchestratorTask, cmd: list[str]) -> None:
        if not self.enabled:
            return

        if task.state_key in self._task_files:
            self.write_task(task, f"[{now_ts()}] RETRY")
            return

        with suppress(OSError):
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

    def write_task(self, task: OrchestratorTask, line: str) -> None:
        if not self.enabled:
            return
        f = self._task_files.get(task.state_key)
        if f is None:
            return
        with suppress(OSError):
            f.write(line + "\n")
            count = self._task_counts.get(task.state_key, 0) + 1
            self._task_counts[task.state_key] = count
            if count % 25 == 0:
                f.flush()

    def close_task(
        self,
        task: OrchestratorTask,
        status: str = "",
        exit_code: int | None = None,
        duration: float = 0.0,
    ) -> None:
        if not self.enabled:
            return
        f = self._task_files.pop(task.state_key, None)
        if f is None:
            return
        with suppress(OSError):
            f.write(f"\n[{now_ts()}] TASK END: {task.script_name}\n")
            f.write(f"[{now_ts()}] STATUS: {status}\n")
            f.write(f"[{now_ts()}] EXIT CODE: {exit_code}\n")
            f.write(f"[{now_ts()}] DURATION: {duration:.2f}s\n")
            f.flush()
            f.close()

    def write_report(
        self,
        profile: ProfileConfig,
        tasks: list[OrchestratorTask],
        statuses: dict[str, str],
        counters: dict[str, int],
    ) -> None:
        if not self.enabled or self.root is None:
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

        with suppress(OSError):
            (self.root / "report.json").write_text(
                json.dumps(report, indent=2, default=str),
                encoding="utf-8",
            )
            (self.root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

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
        for bin_name in ("pw-play", "paplay"):
            if p := shutil.which(bin_name):
                return p
        return None

    @classmethod
    def play(cls, sound_type: str = "alert") -> None:
        if not cls.enabled:
            return

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
        if not cls.enabled:
            return
        if not shutil.which("notify-send"):
            return
        with suppress(OSError):
            subprocess.Popen(
                [
                    "notify-send",
                    "--app-name=Dusky Orchestrator",
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
                    if fd_link.resolve() == real_lock:
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
        lock_path().unlink(missing_ok=True)
    except OSError:
        pass


def acquire_lock() -> bool:
    global _LOCK_FD
    lp = lock_path()

    with suppress(OSError):
        ensure_dir(lp.parent, 0o700)

    try:
        fd = os.open(str(lp), os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    except Exception as e:
        sys.stderr.write(f"[ERROR] Could not open lock file {lp}: {e}\n")
        return False

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
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


# ==============================================================================
# SUDO ENGINE
# ==============================================================================
class SudoEngine:
    _password: str | None = None
    _askpass_path: Path | None = None
    _sudoers_path: Path | None = None
    _mode: str = "none"  # none | root | nopasswd | password

    ENV_KEEP = [
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
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
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
    ]

    @classmethod
    def mode_name(cls) -> str:
        return cls._mode

    @classmethod
    def _remove_stale_askpass_files(cls) -> None:
        with suppress(OSError):
            for p in askpass_dir().glob(".dusky_askpass_*"):
                with suppress(OSError):
                    p.unlink(missing_ok=True)

    @classmethod
    def cleanup(cls) -> None:
        if cls._sudoers_path is not None:
            env = os.environ.copy()
            if cls._askpass_path is not None:
                env["SUDO_ASKPASS"] = str(cls._askpass_path)

            for cmd in (
                ["sudo", "-n", "rm", "-f", str(cls._sudoers_path)],
                ["sudo", "-A", "rm", "-f", str(cls._sudoers_path)],
            ):
                try:
                    res = subprocess.run(
                        cmd,
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                    if res.returncode == 0:
                        break
                except Exception:
                    pass

        if cls._askpass_path is not None:
            with suppress(OSError):
                cls._askpass_path.unlink(missing_ok=True)

        cls._askpass_path = None
        cls._sudoers_path = None

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

        fd, path = tempfile.mkstemp(prefix=".dusky_askpass_", dir=str(askpass_dir()))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        os.chmod(path, 0o700)
        return Path(path)

    @classmethod
    def _remove_stale_sudoers_files(cls, env: dict[str, str]) -> None:
        script = r"""
for f in /etc/sudoers.d/99_dusky_*; do
    [ -f "$f" ] || continue
    pid=$(sed -n 's/^# pid=//p' "$f" | head -n1)
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$f"
    fi
done
"""
        with suppress(Exception):
            subprocess.run(
                ["sudo", "-A", "sh"],
                input=script,
                text=True,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )

    @classmethod
    def _write_sudoers_dropin(cls, env: dict[str, str]) -> None:
        username = target_user_pw().pw_name
        safe_user = re.sub(r"[^A-Za-z0-9._-]", "_", username)
        path = Path(f"/etc/sudoers.d/99_dusky_{safe_user}_{os.getpid()}")
        env_vars = " ".join(cls.ENV_KEEP)
        content = (
            f"# pid={os.getpid()} ts={int(time.time())}\n"
            f"Defaults:{username} timestamp_type=global\n"
            f"Defaults:{username} env_keep += \"{env_vars} DUSKY_*\"\n"
        )

        shell_cmd = (
            "mkdir -p /etc/sudoers.d && "
            f"umask 077 && cat > {shlex.quote(str(path))} && "
            f"chmod 0440 {shlex.quote(str(path))}"
        )

        try:
            proc = subprocess.run(
                ["sudo", "-A", "sh", "-c", shell_cmd],
                input=content,
                text=True,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=10,
            )
            if proc.returncode != 0:
                return

            check = subprocess.run(
                ["sudo", "-A", "visudo", "-c", "-f", str(path)],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )

            if check.returncode == 0:
                cls._sudoers_path = path
            else:
                with suppress(Exception):
                    subprocess.run(
                        ["sudo", "-A", "rm", "-f", str(path)],
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
        except Exception:
            return

    @classmethod
    def set_password(cls, password: str) -> tuple[bool, str]:
        cls.cleanup()
        cls._remove_stale_askpass_files()

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
            atexit.register(cls.cleanup)
            cls._remove_stale_sudoers_files(env)
            cls._write_sudoers_dropin(env)
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
                cls._sudoers_path = None
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

            for attempt in range(1, 4):
                try:
                    password = getpass.getpass(f"[sudo] password for {getpass.getuser()}: ")
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
        try:
            while True:
                await asyncio.sleep(45)
                ok = SudoEngine.refresh_sync()
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
    generated = base_dir / ".config/matugen/generated/dusky_tui.json"
    if generated.exists():
        return generated

    generated_fresh = base_dir / ".config/matugen/generated_fresh/dusky_tui.json"
    if generated_fresh.exists():
        return generated_fresh

    return generated


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
    default_palette = {
        "bg": "#0a1612",
        "fg": "#d8e6df",
        "accent": "#00e0b8",
        "warning": "#a0d0cb",
        "success": "#8dd2da",
        "muted": "#1a2e28",
        "error": "#ffb4ab",
    }

    theme_file = get_theme_path()
    if not theme_file.exists():
        return default_palette

    try:
        raw = json.loads(theme_file.read_text(encoding="utf-8"))
    except Exception:
        return default_palette

    candidates: list[dict] = []
    if isinstance(raw, dict):
        candidates.append(raw)
        for key in ("dark", "colors", "palette", "tones", "theme"):
            value = raw.get(key)
            if isinstance(value, dict):
                candidates.append(value)

    merged: dict = {}
    for candidate in reversed(candidates):
        merged.update(candidate)

    if not merged:
        return default_palette

    return {
        "bg": _pick_color(merged, ["bg", "background", "base", "surface"], default_palette["bg"]),
        "fg": _pick_color(merged, ["fg", "foreground", "text", "on_surface"], default_palette["fg"]),
        "accent": _pick_color(merged, ["accent", "primary", "secondary"], default_palette["accent"]),
        "warning": _pick_color(merged, ["warning", "warn", "tertiary"], default_palette["warning"]),
        "success": _pick_color(merged, ["success", "green"], default_palette["success"]),
        "muted": _pick_color(merged, ["muted", "outline", "surface_variant"], default_palette["muted"]),
        "error": _pick_color(merged, ["error", "danger", "red"], default_palette["error"]),
    }


def build_app_css(p: dict[str, str]) -> str:
    return f"""
Screen {{
    background: {p['bg']};
    color: {p['fg']};
}}

#top_header {{
    height: 1;
    dock: top;
    background: {p['bg']};
    color: {p['accent']};
    text-style: bold;
    padding: 0 1;
}}

#main_dashboard {{
    layout: horizontal;
    height: 1fr;
}}

#left_pane {{
    width: 38%;
    border-right: solid {p['muted']};
    background: {p['bg']};
    padding: 0 1;
    height: 100%;
}}

#right_pane {{
    width: 62%;
    height: 100%;
    layout: vertical;
    background: {p['bg']};
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
    scrollbar-size: 1 1;
}}

Tree {{
    background: {p['bg']};
    color: {p['fg']};
}}

#footer {{
    height: 1;
    dock: bottom;
    background: {p['bg']};
    layout: horizontal;
    padding: 0 1;
}}

.footer-shortcut {{
    padding: 0 1;
    color: {p['accent']};
}}

.footer-sep {{
    color: {p['muted']};
}}

#footer_status {{
    color: {p['success']};
    text-style: italic;
}}

TaskSearchScreen, ConflictModalScreen, ManualModalScreen, SudoPasswordScreen, ConfirmQuitScreen, HelpScreen, LogSearchScreen, FailureSummaryScreen {{
    align: center middle;
    background: rgba(0,0,0,0.72);
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

#modal_dialog, #manual_dialog, #sudo_dialog, #confirm_dialog, #help_dialog, #summary_dialog {{
    width: 90;
    height: auto;
    background: {p['bg']};
    padding: 1 2;
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

#modal_title, #manual_title, #sudo_title, #confirm_title, #help_title, #summary_title {{
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
    margin: 0 1;
    padding: 0;
}}

Input {{
    background: {p['bg']};
    border: tall {p['accent']};
    color: {p['fg']};
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
def parse_task_entry(raw_entry: str, index: int) -> OrchestratorTask:
    raw = raw_entry.strip()
    parts = [p.strip() for p in raw.split("|")]

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
        elif f.startswith("if:"):
            condition = flag.strip()[3:]
        elif f.startswith("timeout:"):
            with suppress(ValueError):
                timeout = float(flag.strip()[8:])
        elif f.startswith("retry:"):
            with suppress(ValueError):
                retry = max(0, int(flag.strip()[6:]))
        elif f.startswith("retry_delay:"):
            with suppress(ValueError):
                retry_delay = max(0.0, float(flag.strip()[12:]))
        elif f.startswith("on_failure:"):
            val = flag.strip()[11:].lower()
            if val in ("ask", "abort", "continue", "skip", "manual"):
                on_failure = val

    cmd_tokens = shlex.split(cmd.strip())
    if not cmd_tokens:
        raise ValueError(f"Empty command in entry: {raw_entry}")

    if cmd_tokens[0] == "true" and len(cmd_tokens) > 1:
        ignore_fail = True
        cmd_tokens = cmd_tokens[1:]

    if "--force" in cmd_tokens:
        force_flag = True

    return OrchestratorTask(
        raw_entry=raw,
        mode=mode.strip().upper(),
        script_name=cmd_tokens[0],
        args=cmd_tokens[1:],
        ignore_fail=ignore_fail,
        interactive=interactive,
        interactive_override=interactive_override,
        force_flag=force_flag,
        condition=condition,
        timeout=timeout,
        index=index,
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
        args = []

    flags = str(table.get("flags", ""))
    ignore_fail = bool(table.get("ignore_fail", False))

    interactive_override: bool | None = None
    if "interactive" in table:
        interactive = bool(table.get("interactive"))
        interactive_override = interactive
    else:
        interactive = False

    force_flag = bool(table.get("force", False))
    always = bool(table.get("always", False))
    condition = table.get("condition")
    timeout = table.get("timeout")

    try:
        retry = max(0, int(table.get("retry", 0)))
    except Exception:
        retry = 0

    try:
        retry_delay = max(0.0, float(table.get("retry_delay", 1.0)))
    except Exception:
        retry_delay = 1.0

    on_failure = str(table.get("on_failure", "ask")).lower()
    if on_failure not in ("ask", "abort", "continue", "skip", "manual"):
        on_failure = "ask"

    once = bool(table.get("once", False))
    once_mode = str(table.get("once_mode", "content")).lower()
    if once_mode not in ("content", "forever"):
        once_mode = "content"

    once_scope = str(table.get("once_scope", "profile")).lower()
    if once_scope not in ("profile", "global"):
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
        elif f.startswith("if:"):
            condition = flag.strip()[3:]
        elif f.startswith("timeout:"):
            with suppress(ValueError):
                timeout = float(flag.strip()[8:])
        elif f.startswith("retry:"):
            with suppress(ValueError):
                retry = max(0, int(flag.strip()[6:]))
        elif f.startswith("retry_delay:"):
            with suppress(ValueError):
                retry_delay = max(0.0, float(flag.strip()[12:]))
        elif f.startswith("on_failure:"):
            val = flag.strip()[11:].lower()
            if val in ("ask", "abort", "continue", "skip", "manual"):
                on_failure = val

    if "--force" in args:
        force_flag = True

    try:
        timeout_value = float(timeout) if timeout is not None else None
    except Exception:
        timeout_value = None

    return OrchestratorTask(
        raw_entry=json.dumps(table, default=str),
        mode=str(table.get("mode", "U")).strip().upper(),
        script_name=cmd,
        args=args,
        ignore_fail=ignore_fail,
        interactive=interactive,
        interactive_override=interactive_override,
        force_flag=force_flag,
        condition=str(condition).strip() if condition else None,
        timeout=timeout_value,
        index=index,
        always=always,
        retry=retry,
        retry_delay=retry_delay,
        on_failure=on_failure,
        once=once,
        once_mode=once_mode,
        once_scope=once_scope,
    )


def load_profile(filepath: Path) -> ProfileConfig:
    with open(filepath, "rb") as f:
        data = tomllib.load(f)

    p_data = data.get("profile", {})
    g_data = data.get("git", {})
    s_data = data.get("search_dirs", {})
    c_data = data.get("conflict_resolutions", {})
    seq_data = data.get("sequence", {})
    policy_data = data.get("policy", {})

    tasks: list[OrchestratorTask] = []

    for i, line in enumerate(seq_data.get("scripts", []), start=1):
        line = str(line).strip()
        if not line or line.startswith("#"):
            continue
        tasks.append(parse_task_entry(line, i))

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

    return ProfileConfig(
        filepath=filepath,
        name=str(p_data.get("name", filepath.stem)).strip(),
        description=str(p_data.get("description", "")).strip(),
        post_script_delay=max(0, post_delay),
        git_enabled=bool(g_data.get("enabled", False)),
        git_dir=str(g_data.get("git_dir", "~/dusky")).strip(),
        git_work_tree=str(g_data.get("work_tree", "~/")).strip(),
        git_remote=str(g_data.get("remote", "origin")).strip(),
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
    for f in sorted(PROFILES_DIR.glob("*.toml")):
        try:
            profiles.append(load_profile(f))
        except Exception as e:
            sys.stderr.write(f"[ERROR] Failed to load profile {f.name}: {e}\n")

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


def _interpreter_from_shebang(first_line: str) -> str | None:
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
        while parts and parts[0].startswith("-"):
            parts = parts[1:]

    if not parts:
        return None

    prog = Path(parts[0]).name
    if "python" in prog:
        return "python"
    if prog in ("bash", "sh", "zsh", "dash", "fish"):
        return prog
    return prog


def resolve_and_validate_manifest(profile: ProfileConfig) -> bool:
    success = True
    search_dir_cache: dict[str, bool] = {}
    occurrence: dict[tuple[str, str, str], int] = {}

    for task in profile.tasks:
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
                if exists:
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
        is_elf, first_line, full_head = _script_metadata(task.resolved_path)

        metadata_interactive = False
        for line in full_head.splitlines()[:20]:
            if _INTERACTIVE_RE.search(line):
                metadata_interactive = True
                break

        if task.interactive_override is not None:
            task.interactive = task.interactive_override
        else:
            task.interactive = metadata_interactive

        shebang_interp = _interpreter_from_shebang(first_line)
        executable = os.access(task.resolved_path, os.X_OK)

        if is_elf:
            task.interpreter = ""
        elif shebang_interp:
            if executable and shebang_interp in ("bash", "sh", "zsh", "dash", "fish", "python"):
                task.interpreter = ""
            else:
                if shebang_interp == "python":
                    task.interpreter = sys.executable
                elif shebang_interp in ("bash", "sh", "zsh", "dash", "fish"):
                    task.interpreter = shutil.which(shebang_interp) or shebang_interp
                else:
                    task.interpreter = shebang_interp
        else:
            suffix = task.resolved_path.suffix.lower()
            if suffix == ".py":
                task.interpreter = sys.executable
            elif suffix == ".sh":
                task.interpreter = shutil.which("bash") or "bash"
            elif suffix == ".fish":
                task.interpreter = shutil.which("fish") or "fish"
            elif executable:
                task.interpreter = ""
            else:
                task.interpreter = shutil.which("bash") or "bash"

        if task.interpreter:
            interp = task.interpreter
            if interp.lower() in ("python", "python3"):
                if not sys.executable:
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
        "x11",
        "graphical",
        "ssh",
        "desktop",
        "battery",
        "btrfs",
        "vm",
        "baremetal",
        "gpu",
        "group",
        "env",
    }

    def __init__(self):
        self.cache: dict[str, bool] = {}

    def _volatile(self, condition: str | None) -> bool:
        if not condition:
            return False
        cond = condition.strip()
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
        if kind == "ssh":
            return bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))
        if kind == "desktop":
            session = os.environ.get("XDG_SESSION_TYPE", "").lower()
            if session in ("wayland", "x11", "mir"):
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

        if kind == "command":
            return bool(shutil.which(value))
        if kind == "path":
            return Path(value).expanduser().exists()
        if kind == "missing":
            return not Path(value).expanduser().exists()
        if kind == "file":
            return Path(value).expanduser().is_file()
        if kind == "dir":
            return Path(value).expanduser().is_dir()

        if kind == "package":
            return self._package_installed(value)
        if kind == "group":
            return self._user_in_group(value)
        if kind == "gpu":
            return self._gpu(value.lower())

        if kind == "service_active":
            return self._run(["systemctl", "is-active", "--quiet", value])
        if kind == "user_service_active":
            return self._run(["systemctl", "--user", "is-active", "--quiet", value])

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
        if not shutil.which("pacman"):
            return False
        return self._run(["pacman", "-Qq", name])

    def _user_in_group(self, group: str) -> bool:
        user = target_user_pw().pw_name
        groups = self._output(["id", "-nG", user])
        return group in groups.split()

    def _gpu(self, kind: str) -> bool:
        if kind == "nvidia":
            return Path("/sys/module/nvidia").exists() or self._lspci_contains("nvidia")
        if kind == "intel":
            return (
                Path("/sys/module/i915").exists()
                or Path("/sys/module/xe").exists()
                or self._lspci_contains("intel")
            )
        if kind == "amd":
            return Path("/sys/module/amdgpu").exists() or self._lspci_contains("amd")
        return False

    def _lspci_contains(self, needle: str) -> bool:
        if not shutil.which("lspci"):
            return False
        out = self._output(["lspci"])
        return needle.lower() in out.lower()


# ==============================================================================
# GIT SELF UPDATE
# ==============================================================================
GIT_UPSTREAM_BRANCH = "main"
GIT_UPSTREAM_REF = "refs/dusky/upstream/main"


def _git_env() -> dict[str, str]:
    env = os.environ.copy()

    for key in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_LITERAL_PATHSPECS",
        "GIT_ASKPASS",
        "SSH_ASKPASS",
    ):
        env.pop(key, None)

    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_SSH_COMMAND": "ssh -o BatchMode=yes",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    return env


def _git_run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        env=_git_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _git_check(cmd: list[str], timeout: int = 60) -> str:
    proc = _git_run(cmd, timeout=timeout)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, proc.stdout, proc.stderr)
    return proc.stdout.strip()


def _proc_holds_file(path: Path) -> bool:
    try:
        real = path.resolve()
    except Exception:
        return False

    proc_dir = Path("/proc")
    if not proc_dir.exists():
        return False

    for pid_dir in proc_dir.iterdir():
        if not pid_dir.name.isdigit():
            continue

        fd_dir = pid_dir / "fd"
        if not fd_dir.exists():
            continue

        with suppress(OSError):
            for fd_link in fd_dir.iterdir():
                with suppress(OSError):
                    if fd_link.resolve() == real:
                        return True

    return False


def _delete_path(target: Path) -> None:
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target, ignore_errors=True)
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
    for lock_file in _iter_git_lock_files(git_dir):
        if not lock_file.exists():
            continue

        if _proc_holds_file(lock_file):
            sys.stderr.write(
                f"[ERROR] Git lock {lock_file} is open by a live process. Aborting git update.\n"
            )
            return False

        try:
            age = time.time() - lock_file.stat().st_mtime
        except OSError as e:
            sys.stderr.write(f"[ERROR] Cannot stat Git lock {lock_file}: {e}\n")
            return False

        if age <= 60:
            sys.stderr.write(
                f"[ERROR] Git lock {lock_file} is too recent to safely auto-clear. Aborting.\n"
            )
            return False

        try:
            if lock_file.is_dir() and not lock_file.is_symlink():
                shutil.rmtree(lock_file, ignore_errors=True)
            else:
                lock_file.unlink(missing_ok=True)
        except OSError as e:
            sys.stderr.write(f"[ERROR] Failed to remove stale Git lock {lock_file}: {e}\n")
            return False

        if lock_file.exists():
            sys.stderr.write(f"[ERROR] Failed to remove stale Git lock {lock_file}.\n")
            return False

        sys.stdout.write(f"[GIT] Cleared stale Git lock: {lock_file.name}\n")

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

    reserve = 64 * 1024 * 1024
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
    with suppress(OSError):
        path.write_text(text, encoding="utf-8")


def _write_backup_info(info_path: Path, lines: list[str]) -> None:
    _write_text_file(info_path, "\n".join(lines) + "\n")


def _move_to_backup(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)

    if src.is_dir() and not src.is_symlink():
        shutil.copytree(src, dest, symlinks=True, dirs_exist_ok=True)
        shutil.rmtree(src, ignore_errors=True)
    else:
        shutil.move(src, dest)


def _copy_path_to_backup(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)

    if src.is_dir() and not src.is_symlink():
        shutil.copytree(src, dest, symlinks=True, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dest, follow_symlinks=False)


def _atomic_copy_file(src: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)

    if src.is_dir() and not src.is_symlink():
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists() or target.is_symlink():
            target.unlink(missing_ok=True)

        shutil.copytree(src, target, symlinks=True, dirs_exist_ok=True)
        return

    tmp = target.parent / f".{target.name}.dusky_tmp"

    if tmp.is_dir() and not tmp.is_symlink():
        shutil.rmtree(tmp, ignore_errors=True)
    elif tmp.exists() or tmp.is_symlink():
        tmp.unlink(missing_ok=True)

    shutil.copy2(src, tmp, follow_symlinks=False)

    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target, ignore_errors=True)

    os.replace(tmp, target)


def _is_null_oid(oid: str) -> bool:
    return not oid or oid.strip("0") == ""


def _clean_old_backups(base: Path, keep: int = 10) -> None:
    if not base.exists():
        return

    entries = sorted(
        [p for p in base.iterdir() if p.is_dir() and p.name.startswith("dusky_backup_")],
        reverse=True,
    )

    for old in entries[keep:]:
        with suppress(OSError):
            shutil.rmtree(old, ignore_errors=True)


def _collect_incoming_collisions(base_cmd: list[str], remote_ref: str, work_tree: Path) -> list[str]:
    tracked: set[str] = set()
    incoming: set[str] = set()

    with suppress(Exception):
        tracked_out = _git_check(base_cmd + ["ls-files", "-z"], timeout=60)
        tracked = {x for x in tracked_out.split("\0") if x}

    with suppress(Exception):
        incoming_out = _git_check(base_cmd + ["ls-tree", "-r", "-z", "--name-only", remote_ref], timeout=60)
        incoming = {x for x in incoming_out.split("\0") if x}

    candidates: set[str] = set()

    for inc in incoming:
        target = work_tree / inc

        if (target.exists() or target.is_symlink()) and inc not in tracked:
            candidates.add(inc)

        for parent in Path(inc).parents:
            rel = str(parent)
            if rel in (".", "/"):
                break

            ancestor = work_tree / rel
            if (
                (ancestor.exists() or ancestor.is_symlink())
                and not ancestor.is_dir()
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


def _backup_collision_roots(work_tree: Path, roots: list[str], collision_dir: Path) -> Path | None:
    if not roots:
        return None

    ensure_dir(collision_dir, 0o700)

    required = sum(_path_copy_size(work_tree / rel) for rel in roots)
    if not _ensure_free_space(collision_dir.parent, required, "collision backup"):
        raise RuntimeError("Not enough disk space for collision backup")

    moved: list[str] = []

    for rel in roots:
        src = work_tree / rel
        dest = collision_dir / rel

        if not (src.exists() or src.is_symlink()):
            continue

        _move_to_backup(src, dest)
        moved.append(rel)

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


def _restore_collision_dir(collision_dir: Path | None, work_tree: Path) -> None:
    if collision_dir is None or not collision_dir.exists():
        return

    for src in collision_dir.rglob("*"):
        if not (src.is_file() or src.is_symlink()):
            continue

        rel = src.relative_to(collision_dir)
        dest = work_tree / rel

        if dest.exists() or dest.is_symlink():
            continue

        with suppress(OSError):
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest, follow_symlinks=False)


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
    with suppress(Exception):
        out = _git_check(base_cmd + ["ls-tree", "-z", "HEAD", "--", path], timeout=30)
        if out:
            record = out.split("\0", 1)[0]
            meta = record.split("\t", 1)[0]
            tokens = meta.split()
            if len(tokens) >= 3:
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

    manifest: list[str] = []

    for path, info in changes.items():
        src = work_tree / path
        has_copy = False

        if info["status"] != "D" and (src.exists() or src.is_symlink()):
            dest = user_mods_dir / path
            _copy_path_to_backup(src, dest)
            has_copy = True

        manifest.append(
            f"status={info['status']} "
            f"old_mode={info['old_mode']} "
            f"old_oid={info['old_oid']} "
            f"has_copy={1 if has_copy else 0} "
            f"path={path}"
        )

    _write_backup_info(
        user_mods_dir.with_name("user_mods_INFO.txt"),
        [
            "Dusky tracked-change backup",
            f"Created: {now_iso()}",
            f"Work tree: {work_tree}",
            f"Changes: {len(changes)}",
        ],
    )

    _write_text_file(
        user_mods_dir.with_name("user_mods_MANIFEST.txt"),
        "\n".join(manifest) + "\n",
    )

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

        if status == "D":
            if not new_oid:
                deleted += 1
                continue

            if old_valid and new_oid == old_oid and new_mode == old_mode:
                with suppress(OSError):
                    _delete_path(target)
                deleted += 1
            else:
                ensure_dir(needs_merge_dir, 0o700)
                marker = needs_merge_dir / f"{path}.dusky_deleted"

                with suppress(OSError):
                    marker.parent.mkdir(parents=True, exist_ok=True)
                    marker.write_text(
                        "Tracked deletion requires manual review.\n"
                        f"path: {path}\n"
                        f"old_mode: {old_mode}\n"
                        f"old_oid: {old_oid}\n"
                        f"new_mode: {new_mode or '<absent>'}\n"
                        f"new_oid: {new_oid or '<absent>'}\n",
                        encoding="utf-8",
                    )

                merged += 1

            continue

        if not (backup_file.exists() or backup_file.is_symlink()):
            continue

        safe = False

        if old_valid:
            if new_oid == old_oid and new_mode == old_mode:
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
                with suppress(OSError):
                    _copy_path_to_backup(backup_file, dest)
                merged += 1
        else:
            ensure_dir(needs_merge_dir, 0o700)
            dest = needs_merge_dir / path
            with suppress(OSError):
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
        except OSError:
            pass

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

    for attempt in range(1, 6):
        try:
            _git_check(
                base_cmd
                + [
                    "fetch",
                    "--no-write-fetch-head",
                    remote,
                    f"+refs/heads/{GIT_UPSTREAM_BRANCH}:{GIT_UPSTREAM_REF}",
                ],
                timeout=90,
            )
            return GIT_UPSTREAM_REF
        except Exception as e:
            last_error = e
            if attempt < 5:
                wait = 2 * attempt
                sys.stdout.write(f"[WARN] Fetch attempt {attempt}/5 failed. Retrying in {wait}s...\n")
                time.sleep(wait)

    raise RuntimeError(f"git fetch failed after 5 attempts: {last_error}")


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

    if PROFILES_DIR.exists():
        for profile_file in PROFILES_DIR.glob("*.toml"):
            with open(profile_file, "rb") as f:
                tomllib.load(f)


def run_git_self_update(
    profile: ProfileConfig,
    update_only: bool = False,
    offline: bool = False,
    assume_yes: bool = False,
) -> bool:
    if offline or not profile.git_enabled:
        return False

    if not shutil.which("git"):
        sys.stdout.write("[WARN] git not installed. Skipping self-update.\n")
        return False

    git_dir = resolve_home(profile.git_dir)
    work_tree = resolve_home(profile.git_work_tree)
    base_cmd = [
        "git",
        "--no-optional-locks",
        "--no-advice",
        f"--git-dir={git_dir}",
        f"--work-tree={work_tree}",
    ]

    try:
        repo_state = _git_repo_status(base_cmd, git_dir, work_tree)
    except Exception as e:
        sys.stderr.write(f"[WARN] Git repository check failed: {e}\n")
        return False

    if repo_state == "absent":
        sys.stdout.write(f"[GIT] Bare repository not found at: {git_dir}\n")
        repo_url = getattr(profile, "git_repo_url", "https://github.com/dusklinux/dusky")
        if not repo_url:
            repo_url = "https://github.com/dusklinux/dusky"

        sys.stdout.write(f"[GIT] Cloning bare repository from {repo_url}...\n")
        try:
            ensure_dir(git_dir.parent, 0o700)
            clone_cmd = ["git", "clone", "--bare", "--branch", GIT_UPSTREAM_BRANCH, repo_url, str(git_dir)]
            clone_proc = subprocess.run(clone_cmd, capture_output=True, text=True, timeout=180)
            if clone_proc.returncode != 0:
                sys.stderr.write(f"[ERROR] Bare clone failed: {clone_proc.stderr}\n")
                return False

            fetch_cfg = ["git", f"--git-dir={git_dir}", "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"]
            subprocess.run(fetch_cfg, check=False)

            sys.stdout.write("[GIT] Checking out repository to work tree...\n")
            collision_roots = _collect_incoming_collisions(base_cmd, f"refs/heads/{GIT_UPSTREAM_BRANCH}", work_tree)
            if collision_roots:
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_root = backups_dir() / f"dusky_backup_{timestamp}_initial"
                collision_dir = backup_root / "untracked_collisions"
                _backup_collision_roots(work_tree, collision_roots, collision_dir)

            checkout_proc = subprocess.run(base_cmd + ["checkout", "-f", GIT_UPSTREAM_BRANCH], capture_output=True, text=True, timeout=120)
            if checkout_proc.returncode != 0:
                sys.stderr.write(f"[ERROR] Checkout failed: {checkout_proc.stderr}\n")
                return False

            sys.stdout.write("[GIT] First-time setup complete! Restarting orchestrator with updated code from GitHub...\n")
            sys.stdout.flush()
            sys.stderr.flush()

            SudoEngine.cleanup()

            my_path = Path(__file__).resolve()
            wrapper_path = my_path.with_suffix(".sh")
            if not wrapper_path.is_file():
                wrapper_path = my_path.with_name("orchestrator.sh")

            args = [a for a in sys.argv[1:] if a != "--git-update-only"]
            if "--no-git-update" not in args:
                args.append("--no-git-update")

            try:
                if wrapper_path.is_file():
                    with suppress(OSError):
                        os.chmod(wrapper_path, 0o755)
                    os.execv(str(wrapper_path), [str(wrapper_path)] + args)

                os.execv(sys.executable, [sys.executable] + args)
            except OSError as e:
                sys.stderr.write(f"[FATAL] Failed to restart orchestrator: {e}\n")
                sys.exit(1)

            return True
        except Exception as e:
            sys.stderr.write(f"[ERROR] Initial clone failed: {e}\n")
            return False

    if repo_state != "valid":
        sys.stderr.write("[WARN] Git repository is not healthy. Skipping self-update.\n")
        return False

    my_path = Path(__file__).resolve()

    wrapper_path = my_path.with_suffix(".sh")
    if not wrapper_path.is_file():
        wrapper_path = my_path.with_name("orchestrator.sh")

    if not my_path.is_relative_to(work_tree):
        sys.stderr.write(
            f"[ERROR] Running orchestrator is outside git work tree ({work_tree}). "
            "Skipping self-update.\n"
        )
        return False

    sys.stdout.write("[GIT] Fetching upstream updates...\n")

    try:
        remote_ref = _fetch_upstream_main(base_cmd, profile.git_remote)
        remote_head = _git_check(base_cmd + ["rev-parse", remote_ref])
    except Exception as e:
        sys.stderr.write(f"[ERROR] Git fetch failed: {e}\n")
        sys.stderr.write("[ERROR] Continuing without update.\n")
        return False

    try:
        local_head = ""
        with suppress(subprocess.CalledProcessError):
            local_head = _git_check(base_cmd + ["rev-parse", "HEAD"])

        if local_head and local_head == remote_head:
            sys.stdout.write("[GIT] Orchestrator is up to date.\n")
            return False

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_root = backups_dir() / f"dusky_backup_{timestamp}_{remote_head[:7]}"
        ensure_dir(backup_root, 0o700)
        _clean_old_backups(backups_dir(), keep=10)

        collision_dir = backup_root / "untracked_collisions"
        needs_merge_dir = backup_root / "needs_merge"

        user_mods_dir: Path | None = None
        full_dir: Path | None = None
        changes: dict[str, dict[str, str]] = {}
        destructive = False

        if not local_head:
            sys.stdout.write("[GIT] Local repository has no commits. Initializing from upstream...\n")

            collision_roots = _collect_incoming_collisions(base_cmd, remote_ref, work_tree)

            if collision_roots:
                choice = _prompt_choice(
                    [
                        "\n[UNBORN REPOSITORY]\n",
                        f"  This will initialize the work tree from {remote_ref}.\n",
                        f"  Untracked incoming collisions: {len(collision_roots)}\n",
                        "  1) Abort [DEFAULT]\n",
                        "  2) Backup collisions and initialize from upstream\n",
                        "Choice [1-2] (default: 1): ",
                    ],
                    default="1",
                    assume_yes=assume_yes,
                    yes_choice="2",
                )

                if choice != "2":
                    sys.stdout.write("Aborting update by user request.\n")
                    return False

            try:
                _backup_collision_roots(work_tree, collision_roots, collision_dir)

                with suppress(Exception):
                    _git_check(
                        base_cmd + ["symbolic-ref", "HEAD", f"refs/heads/{GIT_UPSTREAM_BRANCH}"],
                        timeout=30,
                    )

                _git_check(base_cmd + ["reset", "--hard", remote_head], timeout=180)
            except Exception:
                _restore_collision_dir(collision_dir, work_tree)
                raise

            try:
                validate_updated_sources(my_path, wrapper_path)
            except Exception as e:
                sys.stderr.write(f"[ERROR] Initialized orchestrator failed validation: {e}\n")
                _restore_collision_dir(collision_dir, work_tree)
                return False

        else:
            try:
                merge_base = _git_check(base_cmd + ["merge-base", "HEAD", remote_head])
            except Exception:
                merge_base = ""

            if merge_base == "":
                _print_update_preview(base_cmd, local_head, remote_head)

                choice = _prompt_choice(
                    [
                        "\n[UNRELATED HISTORY]\n",
                        "  Local repository does not share history with upstream.\n",
                        "  1) Abort (keep current state) [DEFAULT]\n",
                        "  2) Replace local repo contents with upstream [RECOMMENDED]\n",
                        "Choice [1-2] (default: 1): ",
                    ],
                    default="1",
                    assume_yes=assume_yes,
                    yes_choice="2",
                )

                if choice != "2":
                    sys.stdout.write("Aborting update by user request.\n")
                    return False

                destructive = True

            elif merge_base != local_head:
                _print_update_preview(base_cmd, local_head, remote_head)

                choice = _prompt_choice(
                    [
                        "\n[DIVERGED HISTORY]\n",
                        "  Local history diverges from upstream.\n",
                        "  1) Abort (keep current state) [DEFAULT]\n",
                        "  2) Reset to upstream [RECOMMENDED]\n",
                        "Choice [1-2] (default: 1): ",
                    ],
                    default="1",
                    assume_yes=assume_yes,
                    yes_choice="2",
                )

                if choice != "2":
                    sys.stdout.write("Aborting update by user request.\n")
                    return False

                destructive = True

            collision_roots = _collect_incoming_collisions(base_cmd, remote_ref, work_tree)
            changes = _capture_tracked_changes(base_cmd)

            if not destructive and (collision_roots or changes) and not assume_yes:
                choice = _prompt_choice(
                    [
                        "\n[LOCAL CHANGES DETECTED]\n",
                        f"  Modified tracked files: {len(changes)}\n",
                        f"  Untracked incoming collisions: {len(collision_roots)}\n",
                        "  1) Abort [DEFAULT]\n",
                        "  2) Backup and reset to upstream\n",
                        "Choice [1-2] (default: 1): ",
                    ],
                    default="1",
                    assume_yes=False,
                    yes_choice="2",
                )

                if choice != "2":
                    sys.stdout.write("Aborting update by user request.\n")
                    return False

            try:
                _backup_collision_roots(work_tree, collision_roots, collision_dir)
                user_mods_dir = _backup_user_mods(work_tree, changes, backup_root)

                if destructive:
                    full_dir = _backup_full_tracked_tree(base_cmd, work_tree, backup_root)
            except Exception:
                _restore_collision_dir(collision_dir, work_tree)
                raise

            with suppress(Exception):
                _git_check(base_cmd + ["branch", f"dusky/backup/{timestamp}", local_head], timeout=30)

            orch_backup = backup_root / "orchestrator.py"
            with suppress(OSError):
                shutil.copy2(my_path, orch_backup)

            sys.stdout.write(f"[GIT] Updating from {local_head[:7]} to {remote_head[:7]}...\n")

            try:
                _git_check(base_cmd + ["reset", "--hard", remote_head], timeout=180)
            except Exception:
                _restore_collision_dir(collision_dir, work_tree)
                _restore_user_mods(base_cmd, work_tree, changes, user_mods_dir, needs_merge_dir)
                raise

            try:
                validate_updated_sources(my_path, wrapper_path)
            except Exception as e:
                sys.stderr.write(f"[ERROR] Updated orchestrator failed validation: {e}\n")
                sys.stdout.write("[GIT] Rolling back to previous HEAD...\n")

                with suppress(Exception):
                    _git_check(base_cmd + ["reset", "--hard", local_head], timeout=180)

                if orch_backup.exists():
                    with suppress(OSError):
                        shutil.copy2(orch_backup, my_path)

                _restore_collision_dir(collision_dir, work_tree)
                _restore_user_mods(base_cmd, work_tree, changes, user_mods_dir, needs_merge_dir)

                return False

            if changes:
                restored, merged, deleted = _restore_user_mods(
                    base_cmd,
                    work_tree,
                    changes,
                    user_mods_dir,
                    needs_merge_dir,
                )

                if restored:
                    sys.stdout.write(f"[GIT] Restored {restored} safe local edits.\n")

                if deleted:
                    sys.stdout.write(f"[GIT] Preserved {deleted} tracked deletion(s).\n")

                if merged:
                    sys.stdout.write(
                        f"[WARN] {merged} local edit(s) need manual merge. Saved in: {needs_merge_dir}\n"
                    )

                try:
                    validate_updated_sources(my_path, wrapper_path)
                except Exception as e:
                    sys.stderr.write(f"[WARN] Restored local edits broke validation: {e}\n")
                    sys.stdout.write("[GIT] Keeping pristine upstream and isolating local edits...\n")

                    _git_check(base_cmd + ["reset", "--hard", remote_head], timeout=180)

                    isolated = _move_all_to_needs_merge(user_mods_dir, needs_merge_dir)
                    sys.stdout.write(f"[WARN] Isolated {isolated} local edit file(s) in: {needs_merge_dir}\n")

                    validate_updated_sources(my_path, wrapper_path)

        if full_dir:
            sys.stdout.write(f"[GIT] Full tracked-tree backup saved in: {full_dir}\n")

        sys.stdout.write("[GIT] Update applied. Restarting orchestrator...\n")
        sys.stdout.flush()
        sys.stderr.flush()

        SudoEngine.cleanup()

        args = [a for a in sys.argv[1:] if a != "--git-update-only"]
        if "--no-git-update" not in args:
            args.append("--no-git-update")

        try:
            if wrapper_path.is_file():
                with suppress(OSError):
                    os.chmod(wrapper_path, 0o755)
                os.execv(str(wrapper_path), [str(wrapper_path)] + args)

            os.execv(sys.executable, [sys.executable] + args)
        except OSError as e:
            sys.stderr.write(f"[FATAL] Failed to restart orchestrator after update: {e}\n")
            sys.exit(1)

        return True

    except subprocess.CalledProcessError as e:
        stderr = ""
        if e.stderr:
            stderr = str(e.stderr).strip()

        sys.stderr.write(f"[WARN] Git operation failed: {e}\n")

        if stderr:
            sys.stderr.write(stderr + "\n")

        return False

    except Exception as e:
        sys.stderr.write(f"[WARN] Git update failed: {e}\n")
        return False


# ==============================================================================
# UI HELPERS
# ==============================================================================
def _status_badge(status: TaskStatus) -> Text:
    match status:
        case TaskStatus.COMPLETED:
            return Text(S("completed"), style="green")
        case TaskStatus.RUNNING:
            return Text(S("running"), style="yellow")
        case TaskStatus.FAILED:
            return Text(S("failed"), style="red")
        case TaskStatus.SKIPPED:
            return Text(S("skipped"), style="dim")
        case _:
            return Text(S("pending"), style="dim")


def _task_label(task: OrchestratorTask) -> Text:
    txt = Text()
    txt.append(f"{task.index:03d} ")
    txt.append_text(_status_badge(task.status))
    txt.append(f" [{task.mode}] {task.script_name}")
    if task.always:
        txt.append(" ⟳", style="bold magenta")
    if task.once:
        txt.append(" [once]", style="bold blue")
    return txt


class TaskSearchScreen(ModalScreen[str | None]):
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

        if not query_lower:
            scored = [(0, t) for t in self.tasks[:200]]
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
        for _, item in scored[:200]:
            txt = Text()
            txt.append(f"{item.index:03d} ")
            txt.append_text(_status_badge(item.status))
            txt.append(f" [{item.mode}] ", style="bold yellow")
            txt.append(item.script_name, style="bold white")
            if item.args:
                txt.append(" " + shlex.join(item.args), style="dim")
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

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


class LogSearchScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss_modal", "Dismiss"),
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
            details.append(self.error_msg, style="yellow")

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
                Text(f"{S('running')} MANUAL OVERRIDE: {self.script_name}", style="bold cyan"),
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
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

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
        ok, err = SudoEngine.set_password(pw)
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
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Container(id="confirm_dialog"):
            yield Static(f"{S('failed')} Abort Orchestrator?", id="confirm_title")
            yield Static("This will terminate the active sequence.", id="confirm_text")
            with Horizontal(id="button_bar"):
                yield Button("Abort [A]", variant="error", id="btn_abort")
                yield Button("Cancel [C]", variant="primary", id="btn_cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss("abort" if event.button.id == "btn_abort" else "cancel")

    def on_key(self, event: events.Key) -> None:
        key = event.key.lower()
        if key == "a":
            self.dismiss("abort")
        elif key in ("c", "escape", "q"):
            self.dismiss("cancel")

    def action_cancel(self) -> None:
        self.dismiss("cancel")


class HelpScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss", "Dismiss")]

    def compose(self) -> ComposeResult:
        with Container(id="help_dialog"):
            yield Static(f"{S('logo')} Dusky Orchestrator Help", id="help_title")

            text = Text()
            text.append("Global Keys\n", style="bold")
            text.append("  Ctrl+F   Search tasks\n")
            text.append("  Ctrl+L   Search current log\n")
            text.append("  Ctrl+Q   Quit / abort\n")
            text.append("  F        Cycle task filter\n")
            text.append("  ?        Help\n\n")

            text.append("During Task Execution\n", style="bold")
            text.append("  Keys are forwarded to the running task.\n")
            text.append("  Ctrl+F opens search without stopping the task.\n")
            text.append("  Ctrl+L searches logs without stopping the task.\n")
            text.append("  Ctrl+Q aborts immediately.\n\n")

            text.append("Interactive Tasks\n", style="bold")
            text.append("  The TUI suspends and gives the task full control.\n")
            text.append("  When the task exits, the TUI returns.\n")

            yield Static(text)

            with Horizontal(id="button_bar"):
                yield Button("Close", variant="primary", id="btn_close")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_dismiss(self) -> None:
        self.dismiss(None)


class FailureSummaryScreen(ModalScreen[str]):
    BINDINGS = [Binding("escape", "close", "Close")]

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
                    details.append(f"  {t.index:03d}. [{t.mode}] {t.script_name}\n", style="yellow")
            else:
                details.append("  none\n", style="green")

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
            yield Static(f"{S('logo')} DUSKY ARCH MASTER ORCHESTRATOR", id="title")

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
            self.exit(0)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.exit(1)
            return

        if event.character and event.character in "123456789":
            idx = int(event.character) - 1
            if 0 <= idx < len(self.profiles):
                self.selected_profile = self.profiles[idx]
                self.exit(0)


# ==============================================================================
# MAIN APP
# ==============================================================================
FILTERS = ["all", "pending", "running", "completed", "failed", "skipped"]


class DuskyOrchestratorApp(App):
    ENABLE_COMMAND_PALETTE = False
    CSS = ""

    BINDINGS = [
        Binding("ctrl+f", "open_search", "Search Tasks", priority=True),
        Binding("ctrl+l", "search_log", "Search Log", priority=True),
        Binding("ctrl+q", "quit_app", "Quit", priority=True),
        Binding("f", "cycle_filter", "Filter"),
        Binding("question_mark", "help", "Help"),
    ]

    def __init__(
        self,
        profile: ProfileConfig,
        has_sudo: bool,
        manual: bool,
        stop_on_fail: bool,
        force: bool,
        task_timeout: float,
    ):
        super().__init__()

        self.profile = profile
        self.tasks = profile.tasks
        self.has_sudo = has_sudo
        self.manual = manual
        self.stop_on_fail = stop_on_fail
        self.force_flag = force
        self.task_timeout = task_timeout

        self.active_child_pid: int | None = None
        self.active_child_group: bool = False
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
        self.log_widget = RichLog(
            id="pty_log",
            highlight=False,
            markup=False,
            wrap=True,
            max_lines=6000,
        )
        self.progress_bar = ProgressBar(show_eta=False, show_percentage=False, id="progress_bar")
        self.status_label = Label("Initializing orchestrator sequence...", id="status_label")
        self.speed_label = Label("Status: pre-flight | ETA: --:--", id="speed_label")
        self.details_label = Static("No task selected.", id="details_label")

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

        self._durations: list[float] = []
        self._always_handled: set[str] = set()

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
                    for task in self.tasks:
                        yield RichLog(
                            id=f"log_{task.state_key}",
                            highlight=False,
                            markup=False,
                            wrap=True,
                            max_lines=6000,
                        )

        yield AppFooter(id="footer")

    def on_mount(self) -> None:
        with suppress(Exception):
            self.query_one("#log_switcher", ContentSwitcher).current = "pty_log"

        self.progress_bar.total = max(1, len(self.tasks))
        self._rebuild_tree()

        sudo_mode = SudoEngine.mode_name() if self.has_sudo else "none"
        log_root = str(self.logger.root) if self.logger.root else "disabled"

        with suppress(Exception):
            self.query_one("#footer_status", Label).update(
                f"Engine: active | sudo: {sudo_mode} | logs: {log_root} | filter: {self.filter_mode}"
            )

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

        self._update_overall_status()
        self.run_execution_pipeline()

    def on_unmount(self) -> None:
        self._kill_active_child_sync()
        self.logger.close_all()
        self.state.close()
        self.once_store.close()
        SudoEngine.cleanup()

    def on_resize(self, event: events.Resize) -> None:
        if self.current_pty_master is not None:
            self._set_pty_size(self.current_pty_master)

    @on(Tree.NodeSelected)
    def on_node_selected(self, event: Tree.NodeSelected) -> None:
        node = event.node
        switcher = self.query_one("#log_switcher", ContentSwitcher)

        if node == self.tree_widget.root:
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
        title = "global"
        if key is not None:
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

    async def action_quit_app(self) -> None:
        if self.active_task:
            resp = await self.push_screen_wait(ConfirmQuitScreen())
            if resp != "abort":
                return
            self.log_system("Quit requested. Terminating pipeline...", is_err=True)
            self.exit(1)
            return

        self.exit(0)

    def action_help(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        self.push_screen(HelpScreen())

    def on_key(self, event: events.Key) -> None:
        if isinstance(self.screen, ModalScreen):
            return

        if self.current_pty_master is not None:
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
                self.exit(1)
                event.stop()
                return

            data = self._pty_key_bytes(event)
            if data:
                with suppress(OSError):
                    os.write(self.current_pty_master, data)
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

        self.tree_nodes_map.clear()
        self.tree_widget.root.label = f"{S('logo')} Sequence [{self.filter_mode}]"
        self.tree_widget.root.expand()

        for task in self.tasks:
            if not self._task_visible(task):
                continue
            node = self.tree_widget.root.add_leaf(_task_label(task))
            node.data = task
            self.tree_nodes_map[task.state_key] = node

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
            self._ui_flush_timer = self.set_timer(0.03, self._flush_ui)

    def _append_log_line(self, key: str | None, line: str) -> None:
        dq = self._log_lines.get(key)
        if dq is None:
            dq = deque(maxlen=5000)
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
        if speed and eta:
            self._telemetry["speed"] = speed
            self._telemetry["eta"] = eta

        if self._telemetry_timer is None:
            self._telemetry_timer = self.set_timer(0.2, self._flush_telemetry)

    def _flush_telemetry(self) -> None:
        if self._telemetry_timer is not None:
            with suppress(Exception):
                self._telemetry_timer.stop()
            self._telemetry_timer = None

        pct = self._telemetry.get("pct")
        speed = self._telemetry.get("speed")
        eta = self._telemetry.get("eta")

        if pct and self.active_task:
            self.status_label.update(f"{S('running')} {self.active_task.script_name} ({pct})")

        if speed and eta:
            self.speed_label.update(f"Throughput: {speed} | ETA: {eta}")

    def _update_overall_status(self) -> None:
        total = max(1, len(self.tasks))
        done = len(self.progressed)
        pct = int(done * 100 / total)

        remaining = max(0, total - done)
        eta = "--:--"

        if self._durations and remaining:
            avg = sum(self._durations) / len(self._durations)
            secs = int(avg * remaining)
            eta = str(datetime.timedelta(seconds=secs))

        self.speed_label.update(f"Completed {done}/{total} ({pct}%) | ETA: {eta}")

    def _task_details(self, task: OrchestratorTask | None) -> Text:
        if task is None:
            txt = Text()
            txt.append("Profile: ", style="bold")
            txt.append(self.profile.name + "\n")
            txt.append("Run ID: ", style="bold")
            txt.append(self.run_id + "\n")
            txt.append("Log root: ", style="bold")
            txt.append(str(self.logger.root or "disabled"))
            return txt

        txt = Text()
        txt.append(f"{task.index:03d}. {task.script_name}\n", style="bold")
        txt.append("Mode: ", style="bold")
        txt.append(task.mode + "  ")
        txt.append("Status: ", style="bold")
        txt.append(task.status.value + "\n")
        txt.append("Path: ", style="bold")
        txt.append(str(task.resolved_path or "unresolved") + "\n")
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

        txt.append("Once: ", style="bold")
        if task.once:
            once_valid = self.once_store.marker_valid(task, self.profile.name)
            txt.append(f"true ({task.once_mode}/{task.once_scope})", style="bold")
            txt.append("  Once marker: ", style="bold")
            if once_valid:
                txt.append("valid", style="green")
            else:
                txt.append("absent/mismatch", style="yellow")
            txt.append("\n")
        else:
            txt.append("false\n")

        txt.append("Retry: ", style="bold")
        txt.append(str(task.retry))
        txt.append("  On failure: ", style="bold")
        txt.append(task.on_failure + "\n")
        txt.append("Log: ", style="bold")
        txt.append(str(self.logger.task_log_path(task)))
        return txt

    def _update_details(self, task: OrchestratorTask | None) -> None:
        with suppress(Exception):
            self.details_label.update(self._task_details(task))

    def log_system(self, msg: str, is_err: bool = False) -> None:
        prefix_style = "bold red" if is_err else "bold cyan"
        text = Text.assemble(("[SYSTEM] ", prefix_style), (msg, ""))
        self.logger.system(msg)
        self._queue_ui(text, self.active_task.state_key if self.active_task else None)

    def _maybe_respond_prompt(self, text: str) -> None:
        if self.current_pty_master is None:
            return
        if self.active_task is None or self.active_task.interactive:
            return

        tail = text[-1024:]

        for name, pattern, kind in PROMPT_RULES:
            if not pattern.search(tail):
                continue

            count = self._prompt_counts.get(name, 0)
            max_count = 5 if name == "sudo_password" else 500
            if count >= max_count:
                continue

            now = time.monotonic()
            last = self._prompt_last.get(name, 0.0)
            if now - last < 0.35:
                continue

            response: bytes | None = None

            if kind == "password":
                if SudoEngine._password:
                    response = SudoEngine._password.encode("utf-8") + b"\r"
                else:
                    self.log_system("Sudo password prompt detected, but no cached password is available.", is_err=True)
                    continue
            elif kind == "yes":
                response = b"y\r"
            else:
                response = b"\r"

            with suppress(OSError):
                os.write(self.current_pty_master, response)

            self._prompt_counts[name] = count + 1
            self._prompt_last[name] = now

            if name != "sudo_password" and count < 5:
                self.log_system(f"Auto-responded to prompt: {name}")

            break

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
            with suppress(OSError):
                winsize = struct.pack("HHHH", 40, 120, 0, 0)
                fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

    async def _kill_proc(self, proc: asyncio.subprocess.Process | None) -> None:
        if proc is None or proc.returncode is not None:
            return

        with suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(proc.pid, signal.SIGTERM)

        with suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=2.0)

        if proc.returncode is None:
            with suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(proc.pid, signal.SIGKILL)
            with suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=1.0)

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

    def _task_env(self, task: OrchestratorTask) -> dict[str, str]:
        env = os.environ.copy()

        for k in (
            "LD_PRELOAD",
            "LD_AUDIT",
            "LD_DEBUG",
            "LD_LIBRARY_PATH",
            "LD_ORIGIN_PATH",
            "LD_PROFILE",
            "LD_SHOW_AUXV",
            "LD_USE_LOAD_BIAS",
            "PYTHONSTARTUP",
            "PYTHONHOME",
            "PYTHONPATH",
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
            if interp.lower() in ("python", "python3"):
                interp = sys.executable
            else:
                interp = shutil.which(interp) or interp

            if Path(interp).name in ("python", "python3", "bash", "sh", "zsh", "dash"):
                inner = [interp, "--", str(task.resolved_path)] + args
            else:
                inner = [interp, str(task.resolved_path)] + args
        else:
            inner = [str(task.resolved_path)] + args

        full_env = self._task_env(task)

        critical_keys = [
            "HOME",
            "USER",
            "LOGNAME",
            "SHELL",
            "PATH",
            "TERM",
            "COLORTERM",
            "LANG",
            "LC_ALL",
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "XAUTHORITY",
            "XDG_RUNTIME_DIR",
            "XDG_CONFIG_HOME",
            "XDG_CACHE_HOME",
            "XDG_STATE_HOME",
            "XDG_DATA_HOME",
            "XDG_SESSION_TYPE",
            "XDG_CURRENT_DESKTOP",
            "DBUS_SESSION_BUS_ADDRESS",
            "SSH_AUTH_SOCK",
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
        ]

        env_pairs = [f"{k}={full_env[k]}" for k in critical_keys if k in full_env]

        for k, v in full_env.items():
            if k.startswith("DUSKY_"):
                env_pairs.append(f"{k}={v}")

        if task.mode == "S":
            prefix = SudoEngine.sudo_prefix()
            if prefix:
                return prefix + ["env"] + env_pairs + inner

        return inner

    async def execute_pty_command(
        self,
        cmd: list[str],
        env: dict[str, str],
        timeout: float = 0.0,
    ) -> tuple[bool, int | None, str]:
        try:
            master_fd, slave_fd = pty.openpty()
        except OSError as e:
            self.log_system(f"PTY allocation failed: {e}", is_err=True)
            return False, None, "PTY allocation failed"

        self.current_pty_master = master_fd
        self._set_pty_size(slave_fd)

        transport: asyncio.Transport | None = None
        proc: asyncio.subprocess.Process | None = None
        file_obj = None
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        last_lines: deque[str] = deque(maxlen=40)
        line_buffer = ""

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                env=env,
                close_fds=True,
                start_new_session=True,
            )

            with suppress(OSError):
                os.close(slave_fd)
            slave_fd = -1

            self.active_child_pid = proc.pid
            self.active_child_group = True

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
                            for line in BRACKET_NEWLINE_RE.split(line_buffer):
                                if line:
                                    with suppress(Exception):
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

                    if len(line_buffer) > 1_000_000:
                        with suppress(Exception):
                            self.handle_pty_line(line_buffer[:1_000_000], last_lines)
                        line_buffer = line_buffer[-100_000:]

                    while True:
                        m = SINGLE_NEWLINE_RE.search(line_buffer)
                        if not m:
                            break
                        idx = m.start()
                        line = line_buffer[:idx]
                        line_buffer = line_buffer[idx + 1:]
                        if line:
                            with suppress(Exception):
                                self.handle_pty_line(line, last_lines)

            read_task = asyncio.create_task(read_loop())

            try:
                async with asyncio.timeout(timeout if timeout > 0 else None):
                    code = await proc.wait()
                    try:
                        await asyncio.wait_for(asyncio.shield(read_task), timeout=2.0)
                    except (TimeoutError, asyncio.TimeoutError):
                        read_task.cancel()
                        with suppress(asyncio.CancelledError, Exception):
                            await read_task
                    except Exception:
                        pass

                    self._flush_ui()
                    return code == 0, code, "\n".join(last_lines)

            except (TimeoutError, asyncio.TimeoutError):
                await self._kill_proc(proc)
                read_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await read_task
                self._flush_ui()
                return False, None, "\n".join(last_lines)

            except asyncio.CancelledError:
                await self._kill_proc(proc)
                read_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await read_task
                raise

            finally:
                if not read_task.done():
                    read_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await read_task

        except asyncio.CancelledError:
            await self._kill_proc(proc)
            raise

        except Exception as e:
            self.log_system(f"PTY execution exception: {e}", is_err=True)
            return False, None, "\n".join(last_lines)

        finally:
            self.current_pty_master = None
            self.active_child_pid = None
            self.active_child_group = False

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
        suspend = getattr(self, "suspend", None)
        if callable(suspend):
            with suspend():
                yield
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

    async def _execute_suspended(
        self,
        task: OrchestratorTask,
        cmd: list[str],
        env: dict[str, str],
    ) -> tuple[bool, int | None, str]:
        self.log_system(f"Suspending TUI for interactive workflow: {task.script_name}...")

        with self._suspend_ui():
            sys.stdout.flush()
            sys.stderr.flush()

            old_attr = None
            old_pgrp = None
            stdin_fd = None
            new_group = False

            if sys.stdin.isatty():
                stdin_fd = sys.stdin.fileno()
                with suppress(termios.error, OSError):
                    old_attr = termios.tcgetattr(stdin_fd)
                with suppress(OSError):
                    old_pgrp = os.tcgetpgrp(stdin_fd)

            try:
                sys.stdout.write("\x1b[2J\x1b[H")
                sys.stdout.flush()

                print(f"\n--- INTERACTIVE WORKFLOW: {task.script_name} ---")
                print(f"Executing: {shlex.join(cmd)}\n")

                res = subprocess.run(cmd, env=env)
                code = res.returncode
                return code == 0, code, "interactive session"

            except Exception as e:
                return False, None, str(e)

            finally:
                if stdin_fd is not None and old_pgrp is not None:
                    with suppress(OSError):
                        os.tcsetpgrp(stdin_fd, old_pgrp)

                if old_attr is not None and stdin_fd is not None:
                    with suppress(termios.error, OSError):
                        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_attr)

                self.active_child_pid = None
                self.active_child_group = False

                await asyncio.sleep(0.4)

    async def _ensure_sudo(self) -> bool:
        if not self.has_sudo:
            return True

        ok = SudoEngine.refresh_sync()
        if ok:
            return True

        self.log_system("Sudo credentials expired. Re-authentication required.", is_err=True)
        return await self.push_screen_wait(SudoPasswordScreen())

    async def _execute_task_cmd(
        self,
        task: OrchestratorTask,
        cmd: list[str],
        env: dict[str, str],
    ) -> tuple[bool, int | None, str]:
        if task.interactive:
            return await self._execute_suspended(task, cmd, env)

        timeout = task.timeout if task.timeout is not None else self.task_timeout
        return await self.execute_pty_command(cmd, env, timeout=timeout)

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
            except Exception as e:
                self.log_system(f"Failed to write persistent once marker: {e}", is_err=True)

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
                self.exit(1)
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
            self.exit(1)
            return "abort"

        if self.manual:
            self.status_label.update(f"{S('running')} Pending manual approval: {task.script_name}")
            cmd_preview = shlex.join(self._task_command(task))
            action = await self.push_screen_wait(ManualModalScreen(task.script_name, cmd_preview))

            if action == "skip":
                self.finish_task(task, "skipped", None, "manual skip")
                return "skipped"

            if action == "quit":
                self.log_system("Manual override: aborting pipeline.", is_err=True)
                self.exit(1)
                return "abort"

        if task.mode == "S" and not await self._ensure_sudo():
            self.update_task_node_by_key(task.state_key, TaskStatus.FAILED)
            self.log_system("Sudo authentication unavailable.", is_err=True)

            if self.stop_on_fail or task.on_failure == "abort":
                self.exit(1)
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

            self.exit(1)
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

        while True:
            start = time.monotonic()
            success, code, last = await self._execute_task_cmd(task, cmd, env)
            task.duration = time.monotonic() - start

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
            if self.stop_on_fail and policy == "ask":
                policy = "abort"

            if policy == "abort":
                self.finish_task(task, "failed", code, last)
                self.log_system("Failure policy: abort.", is_err=True)
                self.exit(1)
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
                m_success, m_code, m_last = await self._execute_suspended(task, cmd, env)
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
                    ConflictModalScreen(task.script_name, shlex.join(cmd), code, error_msg)
                )

                match action:
                    case "retry":
                        self.log_system(f"Retrying task: {task.script_name}...")
                        self.update_task_node_by_key(task.state_key, TaskStatus.RUNNING)
                        start = time.monotonic()
                        success, code, last = await self._execute_task_cmd(task, cmd, env)
                        task.duration = time.monotonic() - start

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
                        m_success, m_code, m_last = await self._execute_suspended(task, cmd, env)

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
                        self.log_system("User aborted execution sequence.", is_err=True)
                        self.exit(1)
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
                ran_any = False

                for pass_idx in range(MAX_DEFER_PASSES):
                    ran_this_pass = False

                    for task in self.tasks:
                        key = task.state_key

                        if key in handled:
                            continue

                        if task.once and self.once_store.marker_valid(task, self.profile.name):
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
                            if pass_idx < MAX_DEFER_PASSES - 1:
                                self.log_system(
                                    f"Condition not met yet; deferring: {task.script_name} ({task.condition})"
                                )
                                continue

                            self.log_system(
                                f"Condition not met, skipping: {task.script_name} ({task.condition})"
                            )
                            self.finish_task(task, "skipped_condition", None, f"condition:{task.condition}")
                            handled.add(key)
                            continue

                        ran_this_pass = True
                        ran_any = True

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

                self.status_label.update(f"{S('completed')} Orchestrator sequence finished.")
                self.speed_label.update("Status: idle | ETA: 00:00")

                with suppress(Exception):
                    self.query_one("#footer_status", Label).update("Engine: complete")

                self.log_system("Execution sequence finished.")
                counters = self._compute_counters()
                self.logger.write_report(self.profile, self.tasks, self.statuses, counters)

                failed_tasks = [
                    t for t in self.tasks if self.statuses.get(t.state_key) == "failed"
                ]

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
                        self.log_system("Retrying failed tasks...")
                        continue

                else:
                    AudioNotifier.play("complete")
                    DesktopNotifier.notify(
                        "Dusky Orchestrator",
                        "Setup completed successfully.",
                        "normal",
                    )

                break

        finally:
            self.active_task = None
            self._flush_ui()

            if self.sudo_task is not None:
                self.sudo_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.sudo_task


# ==============================================================================
# CLI
# ==============================================================================
def parse_command_line() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dusky Arch Linux Orchestrator",
        epilog="Example: ./orchestrator.py --profile 01_main",
    )

    parser.add_argument("--profile", help="Execute specific profile (name, stem, or number)")
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

    return parser.parse_args()


def run_doctor() -> None:
    print("Dusky Orchestrator Doctor")
    print("=========================")
    print(f"Version:        {VERSION}")
    print(f"Python:         {sys.version.split()[0]}")
    print(f"Executable:     {sys.executable}")
    print(f"UID/EUID:       {os.getuid()}/{os.geteuid()}")
    print(f"Target user:    {target_user_pw().pw_name}")
    print(f"Home:           {user_home()}")
    print(f"State dir:      {state_dir()}")
    print(f"Logs dir:       {logs_dir()}")
    print(f"Backups dir:    {backups_dir()}")
    print(f"Cache dir:      {cache_dir()}")
    print(f"Runtime dir:    {runtime_dir()}")
    print(f"Profiles dir:   {PROFILES_DIR}")

    try:
        import textual

        print(f"Textual:        {getattr(textual, '__version__', 'unknown')}")
    except Exception as e:
        print(f"Textual:        unavailable ({e})")

    try:
        import rich

        print(f"Rich:           {getattr(rich, '__version__', 'unknown')}")
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
    temp_state = StateStore(profile)
    statuses = temp_state.statuses()
    temp_state.close()

    once_store = OnceStore()
    cond = ConditionEvaluator()

    print(f"Execution plan for {profile.name}:\n")

    for t in profile.tasks:
        status = statuses.get(t.state_key, "pending")
        condition_result = True if not t.condition else cond.check(t.condition)
        volatile = cond._volatile(t.condition)
        once_marker = once_store.marker_valid(t, profile.name) if t.once else False

        if t.once and once_marker:
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
    args = parse_command_line()

    global ASCII_MODE
    if args.ascii:
        ASCII_MODE = True

    if args.doctor:
        run_doctor()
        sys.exit(0)

    check_runtime_versions()
    ensure_not_root(args.allow_root)

    profiles = discover_profiles()
    if not profiles:
        Console(stderr=True).print("[bold yellow]:: No profiles found in profiles/ directory.[/bold yellow]")
        sys.exit(1)

    palette = load_palette()
    ProfileSelectorApp.CSS = build_selector_css(palette)

    if args.list:
        for i, p in enumerate(profiles, start=1):
            print(f"{i:2d}. {p.filepath.stem}: {p.name} ({p.description})")
        sys.exit(0)

    if args.list_once:
        store = OnceStore()
        store.print_list()
        store.close()
        sys.exit(0)

    if args.forget_once:
        if not acquire_lock():
            sys.exit(1)

        store = OnceStore()
        for script in args.forget_once:
            count = store.forget(script)
            print(f"Forgot {count} once marker(s) for: {script}")
        store.close()
        sys.exit(0)

    selected_profile: ProfileConfig | None = None

    if args.profile:
        if args.profile.isdigit():
            idx = int(args.profile) - 1
            if 0 <= idx < len(profiles):
                selected_profile = profiles[idx]
        else:
            for p in profiles:
                if p.name == args.profile or p.filepath.stem == args.profile:
                    selected_profile = p
                    break

        if selected_profile is None:
            Console(stderr=True).print(f"[bold red]Profile '{args.profile}' not found.[/bold red]")
            sys.exit(1)
    else:
        selector = ProfileSelectorApp(profiles)
        selector.run()
        selected_profile = selector.selected_profile
        if selected_profile is None:
            sys.exit(1)

    locked = False

    if args.reset or args.reset_and_run:
        if not locked:
            if not acquire_lock():
                sys.exit(1)
            locked = True
        reset_state_for_profile(selected_profile)
        if args.reset and not args.reset_and_run:
            sys.exit(0)

    if args.list_scripts:
        print(f"Sequence for {selected_profile.name}:")
        for t in selected_profile.tasks:
            print(f"{t.index:3d}. [{t.mode}] {t.script_name} {shlex.join(t.args)}".rstrip())
        sys.exit(0)

    if not locked:
        if not acquire_lock():
            sys.exit(1)
        locked = True

    if args.git_update_only:
        run_git_self_update(
            selected_profile,
            update_only=True,
            offline=args.offline,
            assume_yes=args.yes,
        )
        sys.exit(0)

    if not args.no_git_update and not args.offline:
        if run_git_self_update(
            selected_profile,
            update_only=False,
            offline=False,
            assume_yes=args.yes,
        ):
            sys.exit(0)

    if not resolve_and_validate_manifest(selected_profile):
        Console(stderr=True).print("[bold red]Manifest validation failed.[/bold red]")
        sys.exit(1)

    if args.explain:
        print_explain(selected_profile)
        sys.exit(0)

    if args.dry_run:
        temp_state = StateStore(selected_profile)
        statuses = temp_state.statuses()
        temp_state.close()

        once_store = OnceStore()

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
    has_sudo = any(
        t.mode == "S"
        and not StateStore.is_done(statuses.get(t.state_key))
        and not (t.once and once_store.marker_valid(t, selected_profile.name))
        for t in selected_profile.tasks
    )
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
        )

        app.run()
        sys.exit(app.return_code or 0)

    except KeyboardInterrupt:
        Console(stderr=True).print("\n[bold red]:: Interrupted by user.[/]")
        sys.exit(130)

    finally:
        inhibitor.close()
        SudoEngine.cleanup()


if __name__ == "__main__":
    main()
