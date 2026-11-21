#!/usr/bin/env python3
# DUSKY_BOOTSTRAP_PACKAGES: python python-textual python-rich
# dusky_interactive=true
# ==============================================================================
#  ARCH LINUX ISO TEXTUAL ORCHESTRATOR (v19.0 - Async PTY Engine + Auto-Prompt)
# ==============================================================================
# Architecture: Asynchronous Non-Blocking PTY Stream Engine | Textual Split TUI
# Features: Progress Bar/Speed Extraction | Auto-Prompt Responder | State Persistence
# Compatibility: Python 3.14.7+ | Textual 8.2.8+ | Arch Linux ISO (2026+)
# ==============================================================================

import os
import sys
if sys.version_info < (3, 14, 7):
    sys.stderr.write("[FATAL] Python 3.14.7+ is required.\n")
    sys.exit(1)
import subprocess
import time

PROCESS_STARTED = time.monotonic()

import codecs
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
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum, auto
from importlib import metadata as importlib_metadata
from typing import Any
from contextlib import suppress, contextmanager

def parse_args():
    parser = argparse.ArgumentParser(description="Dusky Arch ISO Textual Orchestrator", allow_abbrev=False)
    parser.add_argument("--phase1", action="store_true", help="Run Phase 1 (ISO Environment)")
    parser.add_argument("--phase2", action="store_true", help="Run Phase 2 (Chroot Environment)")
    parser.add_argument("--reset", action="store_true", help="Reset execution state for the current phase")
    parser.add_argument("--dry-run", "-d", action="store_true", help="Dry run: validate scripts presence and exit")
    parser.add_argument("--force", action="store_true", help="Pass --force flag to subscripts")
    parser.add_argument("--manual", "-m", action="store_true", help="Manual mode: prompt before each script")
    parser.add_argument("--stop-on-fail", action="store_true", help="Halt execution if any script fails")
    parser.add_argument("--auto", action="store_true", help="Automatically decide orchestration prompts")
    parser.add_argument("--exit-on-complete", action="store_true", help="Exit after installation instead of showing the completion menu")
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
    if args.auto and args.manual:
        parser.error("--auto and --manual conflict")
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


EARLY_ARGS = parse_args() if __name__ == "__main__" else None
if EARLY_ARGS is not None and os.environ.get("DUSKY_VALIDATE_ARGS_ONLY") == "1":
    sys.exit(0)


try:
    from rich.console import Console
    from rich.markup import escape
    from rich.text import Text
    from rich import box

    from textual.app import App, ComposeResult
    from textual.containers import Container, Horizontal, Vertical
    from textual.widgets import Footer, Static, RichLog, ProgressBar, Button, Label, Tree, ContentSwitcher
    from textual.widgets.tree import TreeNode
    from textual.binding import Binding
    from textual.screen import ModalScreen
    from textual import work, on, events
except ImportError as exc:
    sys.stderr.write(f"[FATAL] Missing Python dependencies: {exc}\n")
    sys.stderr.write("Install: python-textual python-rich\n")
    sys.exit(8)

try:
    version_parts = (tuple(int(part) for part in re.findall(r"\d+", importlib_metadata.version("textual"))[:3]) + (0, 0, 0))[:3]
    if version_parts < (8, 2, 8):
        raise RuntimeError(f"Textual 8.2.8+ required; installed {importlib_metadata.version('textual')}")
except (importlib_metadata.PackageNotFoundError, RuntimeError) as exc:
    sys.stderr.write(f"[FATAL] {exc}\n")
    sys.exit(8)

# ==============================================================================
# CONSTANTS & CONFIGURATION LOAD
# ==============================================================================
VERSION = "19.0.3"
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


def validate_global_config(config: dict) -> dict:
    for name in ("ui", "paths", "logging", "execution", "conditions", "notifications", "prompts"):
        if not isinstance(config.get(name, {}), dict):
            raise ValueError(f"[{name}] must be a TOML table")
    fields = {
        "ui": {"ascii_mode": bool, "left_pane_width": int, "min_left_pane_width": int,
               "max_left_pane_width": int, "max_log_lines": int,
               "fallback_pty_columns": int, "fallback_pty_lines": int},
        "paths": {"documents_dir": str, "state_subdir": str, "logs_subdir": str},
        "logging": {"enabled": bool, "write_task_logs": bool, "write_reports": bool},
        "execution": {"db_busy_timeout": int, "default_interpreter": str},
        "notifications": {"audio_enabled": bool, "desktop_enabled": bool,
                          "app_name": str, "fallback_sound": str},
    }
    for section, specs in fields.items():
        for key, kind in specs.items():
            value = config.get(section, {}).get(key)
            if value is None:
                continue
            if type(value) is not kind or (kind is int and (value < 0 or (value == 0 and key != "db_busy_timeout"))) or (kind is str and not value):
                raise ValueError(f"[{section}].{key} has an invalid value")
    ui = config.get("ui", {})
    if not 1 <= ui.get("min_left_pane_width", 15) <= ui.get("left_pane_width", 27) <= ui.get("max_left_pane_width", 80) <= 99:
        raise ValueError("Invalid sidebar width limits")
    footer = ui.get("show_keybinds_footer", "auto")
    if type(footer) is not bool and footer != "auto":
        raise ValueError("[ui].show_keybinds_footer must be a boolean or 'auto'")
    for section, keys in {
        "conditions": ("package_check_cmd", "service_active_cmd"),
        "notifications": ("audio_players",),
    }.items():
        for key in keys:
            value = config.get(section, {}).get(key)
            if value is not None and (not isinstance(value, list) or (not value and key != "audio_players") or
                                      any(not isinstance(item, str) or not item for item in value)):
                raise ValueError(f"[{section}].{key} must be a nonempty command array")
    for section, keys in {
        "ui": ("unicode_symbols", "ascii_symbols"),
        "execution": ("extension_interpreters",),
        "conditions": ("gpu_vendor_map",),
        "notifications": ("sound_map",),
    }.items():
        for key in keys:
            value = config.get(section, {}).get(key)
            if value is not None and (not isinstance(value, dict) or
                                      any(not isinstance(v, str) for v in value.values())):
                raise ValueError(f"[{section}].{key} must be a string table")
    rules = config.get("prompts", {}).get("rules", [])
    if not isinstance(rules, list) or any(not isinstance(rule, dict) or
        not all(isinstance(rule.get(key), str) for key in ("name", "pattern", "kind")) for rule in rules):
        raise ValueError("[prompts].rules contains an invalid prompt rule")
    return config


try:
    GLOBAL_CONFIG = validate_global_config(load_global_config())
except (RuntimeError, ValueError) as exc:
    sys.stderr.write(f"[FATAL] {exc}\n")
    sys.exit(2)

ASCII_MODE = GLOBAL_CONFIG.get("ui", {}).get("ascii_mode", False)

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
    return syms.get(key, key)


# High-Performance Regexes
ANSI_STRIP_REGEX = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][0-2A-Z]|[@-Z\\-_])"
)
PCT_REGEX = re.compile(r"(?<!\d)(?:100(?:\.0+)?|\d{1,2}(?:\.\d+)?)%")
SPEED_ETA_REGEX = re.compile(r"(\d+(?:\.\d+)?\s*[KMG]?i?B/s)\s+([\d:]+)", re.IGNORECASE)
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
            resp = {"yes": "y\n", "y": "y\n", "no": "n\n", "enter": "\n"}.get(kind, f"{kind}\n")
        else:
            name, pattern, resp = item
        rules.append((name, re.compile(pattern, re.MULTILINE), resp))
    return rules


try:
    PROMPT_RULES = _build_prompt_rules()
except re.PatternError as exc:
    raise SystemExit(f"[FATAL] Invalid prompt regex: {exc}") from exc
_LOCK_FD: int | None = None

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
        elapsed: float,
        phase_duration: float,
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
            "elapsed_seconds": elapsed,
            "phase_duration_seconds": phase_duration,
            "tasks": [],
        }

        lines = [
            "# Dusky ISO Installer Report",
            "",
            f"- Run ID: `{self.run_id}`",
            f"- Generated: `{now_iso()}`",
            f"- Profile: `{profile_name}`",
            f"- Version: `{VERSION}`",
            f"- Installation elapsed: {elapsed:.2f}s",
            f"- Phase duration: {phase_duration:.2f}s",
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
                "duration_seconds": t.duration,
                "detail": t.error_msg,
            })
            lines.append(f"| {t.index} | `{t.script_name}` | {st} | {t.mode} | `{t.condition or 'always'}` |")

        try:
            (self.root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            (self.root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as exc:
            self.failed_write = True
            sys.stderr.write(f"[ERROR] Report write failed: {exc}\n")


    def close_all(self) -> None:
        files = list(self._task_files.values())
        self._task_files.clear()
        if self._main is not None:
            files.append(self._main)
            self._main = None
        for stream in files:
            try:
                stream.close()
            except OSError as exc:
                self.failed_write = True
                sys.stderr.write(f"[ERROR] Log close failed: {exc}\n")


# ==============================================================================
# CONDITION EVALUATOR
# ==============================================================================
class ConditionEvaluator:
    def check(self, cond: str | None) -> bool:
        if not cond or cond.strip().lower() in ("always", "true", "yes"):
            return True
        if cond.strip().lower() in ("never", "false", "no"):
            return False
        cond_clean = cond.strip()
        return self._eval(cond_clean)

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
        if kind == "graphical":
            return bool(os.environ.get("WAYLAND_DISPLAY"))
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
                return subprocess.run(pkg_cmd + [value], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode == 0
            except Exception:
                return False
        if kind in ("service_active", "service", "svc"):
            cmd = GLOBAL_CONFIG.get("conditions", {}).get(
                "service_active_cmd",
                ["systemctl", "is-active", "--quiet"],
            )
            try:
                return subprocess.run(cmd + [value], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode == 0
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
                        if vf.is_file() and vf.read_text().strip().lower() == target:
                            return True
            if shutil.which("lspci"):
                try:
                    out = subprocess.run(["lspci"], capture_output=True, text=True, timeout=10).stdout.lower()
                    if any(value.lower() in line and any(kind in line for kind in ("vga", "3d", "display")) for line in out.splitlines()):
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
    IGNORED = auto()


@dataclass
class OrchestratorTask:
    index: int
    script_name: str
    args: list[str]
    mode: str = "U"
    ignore_fail: bool = False
    interactive: bool = False
    interactive_override: bool | None = None
    force_flag: bool = False
    condition: str | None = None
    timeout: float | None = None
    interpreter: str = "bash"
    checksum: str = ""
    state_key: str = ""
    resolved_path: Path | None = None
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


@dataclass
class ProfileConfig:
    filepath: Path | None
    name: str
    description: str
    phase1_tasks: list[OrchestratorTask]
    phase2_tasks: list[OrchestratorTask]
    search_dirs: list[Path] = field(default_factory=list)
    conflict_resolutions: dict[str, str] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)


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
            if not part[4:].strip():
                raise ValueError(f"Empty negated condition in {condition!r}")
            validate_condition(part[4:])
            continue
        kind, sep, value = part.partition(":")
        kind = kind.strip().lower()
        if kind in ("always", "true", "yes", "never", "false", "no", "wayland", "graphical") and not sep:
            continue
        if kind in ("command", "cmd", "dir", "file", "path", "missing", "package", "pkg",
                    "service_active", "service", "svc", "gpu") and sep and value.strip():
            continue
        raise ValueError(f"Unsupported condition: {part}")


def parse_task_entry(raw_entry: str | dict, index: int = 1) -> OrchestratorTask:
    if isinstance(raw_entry, dict):
        return parse_task_table(raw_entry, index)
    parts = [part.strip() for part in raw_entry.strip().split("|", 2)]
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
    return parse_task_table({"script": tokens[0], "args": tokens[1:],
                             "mode": mode, "flags": flags}, index)


def parse_task_table(table: dict, index: int) -> OrchestratorTask:
    cmd = table.get("cmd") or table.get("script") or table.get("path") or ""
    if not isinstance(cmd, str) or not cmd.strip():
        raise ValueError(f"Task {index}: missing cmd/script/path")
    cmd = cmd.strip()
    args_raw = table.get("args", [])
    if isinstance(args_raw, str):
        args = shlex.split(args_raw)
    elif isinstance(args_raw, list) and all(isinstance(arg, str) for arg in args_raw):
        args = list(args_raw)
    else:
        raise ValueError(f"Task {index}: args must be a string or array of strings")
    if "cmd" in table:
        tokens = shlex.split(cmd)
        if not tokens:
            raise ValueError(f"Task {index}: empty command")
        cmd, args = tokens[0], tokens[1:] + args
    for key in ("ignore_fail", "interactive", "force", "always", "once"):
        if key in table and not isinstance(table[key], bool):
            raise ValueError(f"Task {index}: {key} must be a boolean")
    flags = table.get("flags", "")
    if not isinstance(flags, str):
        raise ValueError(f"Task {index}: flags must be a string")
    condition = table.get("condition")
    if condition is not None and not isinstance(condition, str):
        raise ValueError(f"Task {index}: condition must be a string")
    retry = table.get("retry", 0)
    if type(retry) is not int or retry < 0:
        raise ValueError(f"Task {index}: retry must be a nonnegative integer")
    task = OrchestratorTask(
        index=index, script_name=cmd, args=args,
        mode=str(table.get("mode", "U")).strip().upper(),
        ignore_fail=table.get("ignore_fail", False),
        interactive=table.get("interactive", False),
        interactive_override=table.get("interactive"),
        force_flag=table.get("force", False) or "--force" in args,
        condition=condition, timeout=table.get("timeout"),
        always=table.get("always", False), retry=retry,
        retry_delay=table.get("retry_delay", 1.0),
        on_failure=str(table.get("on_failure", "ask")).lower(),
        once=table.get("once", False),
        once_mode=str(table.get("once_mode", "content")).lower(),
        once_scope=str(table.get("once_scope", "profile")).lower(),
    )
    for raw_flag in flags.split(","):
        flag = raw_flag.strip()
        key = flag.lower()
        if not key:
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
                     "exact": "forever", "permanent": "forever"}
            scopes = {"profile": "profile", "local": "profile", "global": "global", "machine": "global"}
            if value in modes:
                task.once_mode = modes[value]
            elif value in scopes:
                task.once_scope = scopes[value]
            else:
                raise ValueError(f"Task {index}: invalid flag {flag}")
            task.once = True
        elif key.startswith(("condition:", "if:")):
            value = flag.partition(":")[2]
            task.condition = f"{task.condition},{value}" if task.condition else value
        elif key.startswith("timeout:"):
            task.timeout = float(flag[8:])
        elif key.startswith("retry:"):
            task.retry = int(flag[6:])
        elif key.startswith("retry_delay:"):
            task.retry_delay = float(flag[12:])
        elif key.startswith("on_failure:"):
            task.on_failure = key[11:]
        else:
            raise ValueError(f"Task {index}: unknown flag {flag}")
    for name in ("timeout", "retry_delay"):
        value = getattr(task, name)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"Task {index}: {name} must be nonnegative and finite")
            setattr(task, name, float(value))
    if task.retry < 0 or task.mode not in ("U", "S"):
        raise ValueError(f"Task {index}: invalid retry or mode")
    if task.on_failure not in ("ask", "abort", "continue", "skip", "manual"):
        raise ValueError(f"Task {index}: invalid failure policy")
    if task.once_mode not in ("content", "forever") or task.once_scope not in ("profile", "global"):
        raise ValueError(f"Task {index}: invalid once mode or scope")
    validate_condition(task.condition)
    return task


def repair_missing_commas(text: str) -> tuple[str, int]:
    """Repair omitted commas in memory after strict TOML parsing fails.

    The caller accepts the repair only if strict parsing then succeeds.
    Returns the repaired text and number of inserted commas.
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
    for name in ("name", "description"):
        if name in p_data and not isinstance(p_data[name], str):
            raise ValueError(f"{filepath.name}: [profile].{name} must be a string")
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

    conflict_resolutions: dict[str, str] = {}
    for key, val in cr_data.items():
        if isinstance(val, str):
            conflict_resolutions[key] = str(resolve_home(val))
        elif isinstance(val, dict):
            for sub_key, sub_val in val.items():
                if isinstance(sub_val, str):
                    conflict_resolutions[f"{key}.{sub_key}"] = str(resolve_home(sub_val))

    policy: dict[str, Any] = {}
    for key, val in pol_data.items():
        policy[key] = val

    search_dirs: list[Path] = []
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


def discover_profiles() -> list[ProfileConfig]:
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


def recover_iso_block_device() -> Path | None:
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
        r = subprocess.run(["lsblk", "--json", "--paths", "-o", "PATH,TYPE,FSTYPE"], capture_output=True, text=True, check=False)
        if r.stdout:
            data = json.loads(r.stdout)
            for dev in data.get("blockdevices", []):
                fstype = (dev.get("fstype") or "").lower()
                if fstype == "iso9660":
                    return Path(dev["path"])
                for child in dev.get("children", []) or []:
                    c_fstype = (child.get("fstype") or "").lower()
                    if c_fstype == "iso9660":
                        return Path(child["path"])
    except Exception:
        pass

    return None


def verify_offline_repo_fast(repo_dir: str | None = None) -> tuple[bool, str]:
    """
    Fast verification of offline package repository integrity across candidate paths.
    Checks archrepo.db metadata and package file sizes without reading package payloads.
    Pacman verifies package integrity during installation.
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

    r_path: Path | None = None
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

    expected_pkgs: dict[str, int | None] = {}
    try:
        with tarfile.open(db_path, "r:*") as tar:
            for member in tar.getmembers():
                if member.name.endswith("/desc"):
                    f = tar.extractfile(member)
                    if not f:
                        continue
                    lines = f.read().decode('utf-8', errors='ignore').splitlines()
                    filename = None
                    compressed_size = None
                    for i, line in enumerate(lines):
                        if line.strip() == "%FILENAME%" and i + 1 < len(lines):
                            filename = lines[i + 1].strip()
                        elif line.strip() == "%CSIZE%" and i + 1 < len(lines):
                            compressed_size = int(lines[i + 1].strip())
                    if filename:
                        expected_pkgs[filename] = compressed_size
    except Exception as e:
        return False, f"Failed to parse database '{db_path}': {e}"

    if not expected_pkgs:
        return False, "Repository database contains no valid package metadata."

    for filename, expected_size in expected_pkgs.items():
        pkg_file = r_path / filename
        if not pkg_file.is_file():
            return False, f"Missing offline package: {filename}"
        
        try:
            st = pkg_file.stat()
            if st.st_size == 0 or (expected_size is not None and st.st_size != expected_size):
                return False, f"Offline package size mismatch: {filename}"
        except Exception:
            return False, f"Cannot stat package file: {filename}"

    return True, "Offline repository metadata and package sizes verified."


# ==============================================================================
# ONCE-STORE (SQLITE)
# ==============================================================================
class OnceStore:
    def __init__(self, db_path: Path | None = None, read_only: bool = False):
        self.db_path = db_path or ((state_dir_path() if read_only else state_dir()) / "once.db")
        busy_timeout = int(GLOBAL_CONFIG.get("execution", {}).get("db_busy_timeout", 5000))
        if read_only and self.db_path.exists():
            self.conn = sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True)
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
                  args_key: str) -> str:
        material = f"once|{scope}|{profile_part}|{mode}|{script_name}|{args_key}"
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
        key = self._make_key(scope, profile_part, task.mode, task.script_name, args_key)
        row = self.conn.execute(
            "SELECT mode, once_mode, checksum, resolved_path FROM once_markers WHERE marker_key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return False
        db_mode, _, db_checksum, _ = row
        if db_mode != task.mode:
            return False
        if task.once_mode == "content":
            current_checksum = task.checksum or file_checksum(task.resolved_path) if task.resolved_path else ""
            if not db_checksum or not current_checksum or db_checksum != current_checksum:
                return False
        return True

    def mark_success(self, task: OrchestratorTask, profile_name: str, exit_code: int, run_id: str) -> None:
        if not task.once:
            return
        scope = self._scope_value(task.once_scope)
        profile_part = self._profile_part(profile_name, scope)
        args_key = shlex.join(task.args)
        key = self._make_key(scope, profile_part, task.mode, task.script_name, args_key)
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

    def print_list(self, profile_name: str | None = None) -> None:
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
    except OSError as exc:
        if isinstance(exc, BlockingIOError):
            sys.stderr.write(f"[ERROR] Another instance is already running on {lock_file}.\n")
        else:
            sys.stderr.write(f"[ERROR] Cannot acquire lock {lock_file}: {exc}\n")
        os.close(fd)
        return False


def release_lock(lock_file: Path | None = None) -> None:
    _cleanup_lock(lock_file)


def resolve_interpreter(script_path: Path) -> tuple[str, bool]:
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
    search_dirs: list[Path],
    conflict_resolutions: dict[str, str] | None = None,
) -> Path | None:
    if "/" in script_name:
        path = resolve_home(script_name)
        return path.resolve() if path.is_file() else None
    if conflict_resolutions and script_name in conflict_resolutions:
        path = Path(conflict_resolutions[script_name])
        return path.resolve() if path.is_file() else None
    roots = list(dict.fromkeys([SCRIPT_DIR, *search_dirs]))
    for root in roots:
        path = root / script_name
        if path.is_file():
            return path.resolve()
    matches = {path.resolve() for root in roots if root.is_dir()
               for path in root.rglob(script_name) if path.is_file()}
    if len(matches) > 1:
        raise ValueError(f"Ambiguous script {script_name}: " + ", ".join(map(str, sorted(matches))))
    return next(iter(matches), None)


def is_rich_or_ssh_terminal() -> bool:
    """
    Check if current execution is in a rich graphical terminal environment
    (e.g., Wayland, Kitty, Foot, Alacritty) or over an active SSH session,
    where terminal keybinds (like Alt+Left/Right) do not conflict with Linux VT console switching.
    Returns False in raw Linux console TTYs (/dev/tty1..N, TERM=linux).
    """
    if any(os.environ.get(k) for k in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")):
        return True

    if os.environ.get("WAYLAND_DISPLAY"):
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


def configure_live_console_font() -> None:
    """Apply the live VT console font before any Rich or Textual UI renders."""
    if os.geteuid() != 0 or is_in_chroot():
        return
    if any(os.environ.get(k) for k in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY", "WAYLAND_DISPLAY", "TERM_PROGRAM")):
        return
    if os.environ.get("COLORTERM", "") in ("truecolor", "24bit"):
        return
    term = os.environ.get("TERM", "")
    if any(t in term for t in ("kitty", "foot", "alacritty", "ghostty", "wezterm", "tmux", "screen")):
        return
    try:
        if not sys.stdout.isatty():
            return
        tty_name = os.ttyname(sys.stdout.fileno())
    except OSError:
        return
    if not re.match(r"^/dev/tty\d+$", tty_name):
        return
    for stream in (sys.stdin, sys.stderr):
        with suppress(OSError):
            if stream and stream.isatty() and os.ttyname(stream.fileno()).startswith("/dev/pts/"):
                return
    setfont_bin = shutil.which("setfont")
    if not setfont_bin:
        return
    with suppress(OSError):
        sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.flush()
    for font in ("ter-v22b", "ter-v24b", "latarcyrheb-sun24"):
        try:
            res = subprocess.run(
                [setfont_bin, "-C", tty_name, font],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            if res.returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            continue


def is_in_chroot() -> bool:
    """Detect if running inside an active chroot environment."""
    try:
        root_stat = os.stat("/")
        init_root_stat = os.stat("/proc/1/root")
        return (root_stat.st_dev, root_stat.st_ino) != (init_root_stat.st_dev, init_root_stat.st_ino)
    except Exception:
        return False


# arch-chroot mounts a temporary /tmp; use the payload directory so the
# outer wrapper can read the request after arch-chroot tears down its mounts.
AUTO_POWEROFF_MARKER = Path(__file__).resolve().with_name("dusky_auto_poweroff")


def set_auto_poweroff_marker() -> None:
    AUTO_POWEROFF_MARKER.touch()


def remove_auto_poweroff_marker() -> None:
    AUTO_POWEROFF_MARKER.unlink(missing_ok=True)


def graceful_unmount_and_poweroff(mnt_point: str = "/mnt") -> None:
    """For a directly launched Python UI; wrapper launches handle their own shutdown."""
    subprocess.run(["sync"], check=True)
    subprocess.run(["swapoff", "-a"], check=False)
    if subprocess.run(["mountpoint", "-q", mnt_point], check=False).returncode == 0:
        subprocess.run(["umount", "-R", mnt_point], check=True)
    subprocess.run(["poweroff"], check=True)


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
            yield Label(f"{S('failed')} TASK FAILED: {self.task_name}", markup=False, id="modal_title")
            yield Static(self.error_msg, markup=False, id="error_details")
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
            yield Static(f"About to execute: [bold white]{escape(self.task_name)}[/bold white]\nProceed with execution?", id="manual_details")
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
            yield Label(self.title_text, markup=False, id="completion_title")
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


CHILD_LAUNCHER = """
import fcntl, os, signal, sys, termios
if sys.argv[1] == "pty":
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
elif sys.argv[1] == "foreground":
    signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    os.tcsetpgrp(0, os.getpgrp())
    signal.signal(signal.SIGTTOU, signal.SIG_DFL)
os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
"""


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
        tasks: list[OrchestratorTask],
        phase_title: str,
        profile_name: str,
        state_file: Path,
        manual: bool,
        stop_on_fail: bool,
        force: bool,
        task_timeout: float = 0.0,
        once_store: OnceStore | None = None,
        dry_run: bool = False,
        is_final_phase: bool = True,
        auto_mode: bool = False,
        exit_on_complete: bool = False,
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
        self.auto_mode = auto_mode
        self.exit_on_complete = exit_on_complete
        self.phase_start_time = PROCESS_STARTED
        inherited_start = float(os.environ.get("DUSKY_INSTALL_STARTED_MONOTONIC", PROCESS_STARTED))
        if not math.isfinite(inherited_start) or inherited_start < 0 or inherited_start > time.monotonic():
            raise ValueError("Invalid installer start time")
        self.start_time = inherited_start
        self.finished_time: float | None = None
        self.current_pty_master: int | None = None
        self._previous_signal_handlers: dict[int, Any] = {}
        self._status_text = "Ready"
        self._speed_text = ""

        self.current_idx = 0
        self.completed_keys = set()
        self.task_statuses: dict[str, str] = {}
        self.counters = {"completed": 0, "failed": 0, "skipped": 0, "ignored": 0, "pending": len(tasks)}
        self.conditions = ConditionEvaluator()
        self.run_id = uuid.uuid4().hex[:8]
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

        self.active_task: OrchestratorTask | None = None
        self.current_log_key: str | None = None
        self._log_widgets: dict[str | None, RichLog] = {}
        self.tree_nodes_map: dict[str, TreeNode] = {}
        self.tree_widget = Tree(f"{S('logo')} Execution Sequence", id="tree_widget")

        if self.state_file.exists():
            try:
                self.completed_keys = set(self.state_file.read_text().splitlines())
            except OSError as exc:
                raise RuntimeError(f"Cannot load completion state: {exc}") from exc

        max_lines = GLOBAL_CONFIG.get("ui", {}).get("max_log_lines", 6000)
        self.log_widget = RichLog(id="pty_log", highlight=False, markup=False, wrap=True, max_lines=max_lines)
        self.progress_bar = ProgressBar(total=len(self.tasks), show_eta=False, id="progress_bar")
        self.header_title = Static(
            f"{S('logo')} DUSKY ARCH INSTALLER  [{self.phase_title}]  (Profile: {self.profile_name})",
            id="header_title", markup=False,
        )
        self.header_telemetry = Static("Status: Ready | Elapsed: 00:00", markup=False, id="header_telemetry")

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
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGHUP):
            self._previous_signal_handlers[signum] = signal.getsignal(signum)
            loop.add_signal_handler(signum, self._terminate, signum)
        with suppress(Exception):
            self.query_one("#log_switcher", ContentSwitcher).current = "pty_log"

        self._rebuild_tree()

        self._set_pane_widths(self.left_pane_width)
        for t in self.tasks:
            if t.state_key in self.completed_keys and not t.always:
                self.set_task_status(t, TaskStatus.COMPLETED)
        self.set_interval(1.0, self._refresh_telemetry)
        self._refresh_telemetry()

        self.log_system(f"Started Phase: {self.phase_title}")
        self.log_system(f"Active Profile: {self.profile_name}")
        self.log_system(f"Loaded Cached State: {len(self.completed_keys)} tasks completed")

        self.run_execution_loop()

    def _terminate(self, signum: int) -> None:
        self.log_system(f"Termination requested: {signal.Signals(signum).name}")
        self.exit(return_code=128 + signum)

    def on_unmount(self) -> None:
        loop = asyncio.get_running_loop()
        for signum, previous in self._previous_signal_handlers.items():
            loop.remove_signal_handler(signum)
            signal.signal(signum, previous)
        self._previous_signal_handlers.clear()

    def _render_final_overview_block(self) -> None:
        failed = self.counters["failed"] or self.persistence_failed or self.logger.failed_write
        pending = self.counters["pending"]
        verdict, color = ("FAILED", "#f85149") if failed else (
            ("INCOMPLETE", "#d29922") if pending else (
                ("WARNINGS", "#d29922") if self.counters["ignored"] else ("SUCCESS", "#3fb950")))
        lines = [
            "════════════════════════════════════════════════════════════════════════════════",
            f" FINAL OVERVIEW │ [bold #58a6ff]{escape(self.phase_title)}[/] │ [bold {color}]{verdict}[/]",
            "",
            f" Installation elapsed: [bold #58a6ff]{self.format_elapsed(self.elapsed_seconds())}[/] ({self.elapsed_seconds():.2f}s)",
            f" This phase: {((self.finished_time or time.monotonic()) - self.phase_start_time):.2f}s",
            "",
            " MODE   SUCCESS   FAILED   IGNORED   SKIPPED   PENDING   TOTAL",
        ]
        for mode in sorted({task.mode for task in self.tasks}):
            group = [task for task in self.tasks if task.mode == mode]
            counts = [sum(task.status == status for task in group) for status in
                      (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.IGNORED, TaskStatus.SKIPPED)]
            remaining = sum(task.status in (TaskStatus.PENDING, TaskStatus.RUNNING) for task in group)
            lines.append(f" {mode:<4}   {counts[0]:>5}   {counts[1]:>6}   {counts[2]:>7}   {counts[3]:>7}   {remaining:>7}   {len(group):>5}")
        timed = sorted((task for task in self.tasks if task.duration > 0), key=lambda t: t.duration, reverse=True)
        lines.extend(["", " Slowest tasks (all attempts):"])
        lines.extend(f" • {escape(task.script_name)}: {task.duration:.2f}s" for task in timed[:3])
        if not timed:
            lines.append(" • None recorded in this invocation")
        for status, label in ((TaskStatus.FAILED, "Failed"), (TaskStatus.IGNORED, "Ignored failure"),
                              (TaskStatus.SKIPPED, "Skipped")):
            for task in self.tasks:
                if task.status == status:
                    lines.append(f" {label}: {escape(task.script_name)} — {escape(task.error_msg or '')}")
        if self.persistence_failed:
            lines.append(" [bold #f85149]State persistence failed; inspect the engine log.[/]")
        if self.logger.failed_write:
            lines.append(" [bold #f85149]Log/report write failed.[/]")
        lines.extend(["", f" Logs: {escape(str(self.logger.root or 'disabled'))}",
                      "════════════════════════════════════════════════════════════════════════════════"])
        with suppress(Exception):
            report_widget = self.query_one("#log_report", RichLog)
            report_widget.clear()
            for line in lines:
                report_widget.write(Text.from_markup(line))
        self.current_log_key = "report"
        with suppress(Exception):
            if node := self.tree_nodes_map.get("__report__"):
                self.tree_widget.select_node(node)
                self.tree_widget.scroll_to_node(node)
            self.query_one("#log_switcher", ContentSwitcher).current = "log_report"

    def _task_label(self, task: OrchestratorTask) -> Text:
        if task.status == TaskStatus.COMPLETED:
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
        elif task.status == TaskStatus.IGNORED:
            icon = "[bold #d29922]![/]"
            name_style = "bold #d29922"
        elif task.status == TaskStatus.SKIPPED:
            icon = f"[bold #d29922]{S('skipped')}[/]"
            name_style = "dim #d29922"
        else:
            icon = f"[#8b949e]{S('pending')}[/]"
            name_style = "dim #8b949e"

        # Clean script name WITHOUT redundant "USER" moniker
        return Text.from_markup(f" {icon} [{name_style}]{escape(task.script_name)}[/]")

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
                ui = GLOBAL_CONFIG.get("ui", {})
                winsize = struct.pack("HHHH", ui.get("fallback_pty_lines", 40),
                                      ui.get("fallback_pty_columns", 120), 0, 0)
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

    def _get_log_widget(self, key: str | None) -> RichLog | None:
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

    def log_task(self, msg: str, task: OrchestratorTask | None = None):
        txt = Text.from_ansi(msg)
        if main_w := self._get_log_widget(None):
            main_w.write(txt)
        t = task or self.active_task
        if t:
            if task_w := self._get_log_widget(t.state_key):
                task_w.write(txt)

    def elapsed_seconds(self) -> float:
        end = self.finished_time if self.finished_time is not None else time.monotonic()
        return max(0.0, end - self.start_time)

    @staticmethod
    def format_elapsed(seconds: float) -> str:
        hours, remainder = divmod(int(seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

    def _refresh_telemetry(self) -> None:
        text = f"Status: {self._status_text} | Elapsed: {self.format_elapsed(self.elapsed_seconds())}"
        if self._speed_text:
            text += f" | Speed/ETA: {self._speed_text}"
        self.header_telemetry.update(text)

    def update_telemetry(self, status_str: str, speed_str: str = "") -> None:
        self._status_text, self._speed_text = status_str, speed_str
        self._refresh_telemetry()

    def set_task_status(self, task: OrchestratorTask, status: TaskStatus) -> None:
        self.update_task_status(task.index - 1, status)
        self.task_statuses[task.state_key] = status.name
        for key in self.counters:
            if key == "pending":
                self.counters[key] = sum(t.status in (TaskStatus.PENDING, TaskStatus.RUNNING) for t in self.tasks)
            else:
                self.counters[key] = sum(t.status.name.lower() == key for t in self.tasks)
        done = len(self.tasks) - self.counters["pending"]
        self.progress_bar.update(progress=done)

    def write_final_report(self) -> None:
        if self.finished_time is None:
            self.finished_time = time.monotonic()
        self.logger.write_report(self.profile_name, self.tasks, self.task_statuses,
                                 self.counters, self.elapsed_seconds(),
                                 self.finished_time - self.phase_start_time)
        self._refresh_telemetry()
        self._render_final_overview_block()

    @contextmanager
    def _suspend_ui(self):
        # Textual 8.2.8 resumes its driver only on a normal context exit.
        error: BaseException | None = None
        with self.batch_update(), self.suspend():
            try:
                yield
            except BaseException as exc:
                error = exc
        if error is not None:
            raise error

    @staticmethod
    async def _write_pty(fd: int, data: bytes) -> None:
        loop = asyncio.get_running_loop()
        while data:
            try:
                written = os.write(fd, data)
            except BlockingIOError:
                ready = loop.create_future()
                loop.add_writer(fd, lambda: not ready.done() and ready.set_result(None))
                try:
                    await ready
                finally:
                    loop.remove_writer(fd)
                continue
            if written <= 0:
                raise OSError("PTY input made no progress")
            data = data[written:]

    @staticmethod
    async def _stop_pty_child(proc: asyncio.subprocess.Process) -> None:
        # Give the whole group time to flush and exit, including descendants
        # whose leader has already stopped.
        deadline = time.monotonic() + 2
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), 2)
        except TimeoutError:
            pass
        while time.monotonic() < deadline:
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                return
            except PermissionError:
                break
            await asyncio.sleep(0.05)
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        if proc.returncode is None:
            with suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), 2)

    @staticmethod
    def _set_foreground_group(fd: int, pgid: int) -> None:
        old_handler = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
        try:
            os.tcsetpgrp(fd, pgid)
        finally:
            signal.signal(signal.SIGTTOU, old_handler)

    async def _run_interactive(self, cmd: list[str], timeout: float) -> int:
        with self._suspend_ui():
            tty_fd = sys.stdin.fileno() if sys.stdin.isatty() else None
            old_group = os.tcgetpgrp(tty_fd) if tty_fd is not None else None
            old_attrs = termios.tcgetattr(tty_fd) if tty_fd is not None else None
            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-c", CHILD_LAUNCHER,
                    "foreground" if tty_fd is not None else "plain", *cmd,
                    cwd=SCRIPT_DIR, process_group=0,
                )
                async with asyncio.timeout(timeout if timeout > 0 else None):
                    return await proc.wait()
            finally:
                if proc is not None:
                    await self._stop_pty_child(proc)
                if tty_fd is not None:
                    with suppress(OSError):
                        self._set_foreground_group(tty_fd, old_group)
                        termios.tcsetattr(tty_fd, termios.TCSANOW, old_attrs)

    async def _run_pty(self, task: OrchestratorTask, cmd: list[str], timeout: float) -> int:
        master_fd, slave_fd = pty.openpty()
        proc: asyncio.subprocess.Process | None = None
        transport = None
        file_obj = None
        read_task: asyncio.Task | None = None
        try:
            self.current_pty_master = master_fd
            os.set_blocking(master_fd, False)
            self._set_pty_size(master_fd)
            proc = await asyncio.create_subprocess_exec(sys.executable, "-c", CHILD_LAUNCHER,
                                                        "pty", *cmd, cwd=SCRIPT_DIR,
                                                        stdin=slave_fd, stdout=slave_fd,
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
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

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
                    text = decoder.decode(chunk)
                    prompt_buffer = (prompt_buffer + ANSI_STRIP_REGEX.sub("", text))[-4096:]
                    for _ in range(8):
                        matches = [(match.start(), i, name, match, response)
                                   for i, (name, pattern, response) in enumerate(PROMPT_RULES)
                                   if (match := pattern.search(prompt_buffer)) is not None]
                        if not matches:
                            break
                        _, _, name, match, response = min(matches, key=lambda item: item[:2])
                        await self._write_pty(master_fd, response.encode("utf-8"))
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
                line_buffer += decoder.decode(b"", final=True)
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
                    try:
                        await asyncio.wait_for(asyncio.shield(read_task), 2)
                    except TimeoutError:
                        # A finished child may leave a descendant holding the PTY.
                        read_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await read_task
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
                with suppress(asyncio.CancelledError, OSError):
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

    @work(name="installer_sequence", exclusive=True)
    async def run_execution_loop(self) -> None:
        try:
            while self.current_idx < len(self.tasks):
                task = self.tasks[self.current_idx]
                if task.state_key in self.completed_keys and not task.always:
                    self.current_idx += 1
                    continue
                if not task.resolved_path or not task.resolved_path.is_file():
                    self.set_task_status(task, TaskStatus.FAILED)
                    self.log_system(f"Missing script: {task.script_name}")
                    self.exit(return_code=1)
                    return
                if not task.always and task.once and self.once_store.marker_valid(task, self.profile_name):
                    self.task_skipped(task, "once marker valid")
                    continue
                if task.condition and not self.conditions.check(task.condition):
                    self.task_skipped(task, f"condition false: {task.condition}")
                    continue
                if self.manual and not self.auto_mode:
                    res = await self.push_screen_wait(ManualModalScreen(task.script_name))
                    if res == "skip":
                        self.task_skipped(task, "manual skip")
                        continue
                    if res != "yes":
                        self.exit(return_code=130)
                        return
                if not await self.execute_task(task):
                    return
                self.current_idx += 1
            await self.finish_phase()
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError, RuntimeError, sqlite3.DatabaseError) as exc:
            self.persistence_failed = True
            self.log_system(f"Installer execution failed: {exc}")
            self.exit(return_code=1)
        finally:
            self.active_task = None
            try:
                self.write_final_report()
            finally:
                self.logger.close_all()
                self.once_store.close()

    async def finish_phase(self) -> None:
        self.log_system("All tasks in this phase completed.")
        self.update_telemetry("Finished Phase")
        self.write_final_report()

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

        if self.exit_on_complete:
            self.exit(return_code=int(bool(failed_tasks) or self.persistence_failed or self.logger.failed_write))
            return

        # FINAL PHASE (Phase 2: Chroot / Full Installation Complete)
        if failed_tasks or self.counters["ignored"] or self.persistence_failed or self.logger.failed_write:
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
        elapsed_str = self.format_elapsed(self.elapsed_seconds())

        summary_lines = (
            f"Installation: {self.phase_title}\n"
            f"Profile: {self.profile_name}\n"
            f"Completed: {completed}\n"
            f"Failed: {failed}\n"
            f"Skipped: {skipped}\n"
            f"Ignored failures: {self.counters['ignored']}\n"
            f"Elapsed: {elapsed_str}\n"
            f"Logs: {self.logger.root or logs_dir()}\n\n"
            "Choose an action:"
        )

        res = await self.push_screen_wait(
            CompletionDialog(
                title="INSTALLATION FINISHED WITH WARNINGS" if failed_tasks or self.counters["ignored"] or self.persistence_failed or self.logger.failed_write else "INSTALLATION COMPLETE",
                message=summary_lines,
                level="warning" if failed_tasks or self.counters["ignored"] or self.persistence_failed or self.logger.failed_write else "success",
            )
        )
        if res == "poweroff":
            self.trigger_poweroff()
        elif res == "view_logs":
            remove_auto_poweroff_marker()
            self.log_system("Reviewing execution logs. Press 'q' when finished to exit.")

    async def execute_task(self, task: OrchestratorTask) -> bool:
        self.active_task = task
        self.select_task_node(task.state_key)
        args = list(task.args)
        if (self.force_flag or task.force_flag) and "--force" not in args:
            args.append("--force")
        cmd = [task.interpreter, str(task.resolved_path), *args]
        timeout = task.timeout if task.timeout is not None else self.task_timeout
        attempts_left = task.retry
        use_interactive = task.interactive
        while True:
            self.set_task_status(task, TaskStatus.RUNNING)
            self.log_system(f">>> PROCESS INITIATED: {task.script_name}")
            self.update_telemetry(f"Running {task.script_name}")
            self.logger.open_task(task, cmd)
            start = time.monotonic()
            rc, error_msg = 1, ""
            try:
                if use_interactive:
                    rc = await self._run_interactive(cmd, timeout or 0.0)
                else:
                    rc = await self._run_pty(task, cmd, timeout or 0.0)
                if rc != 0:
                    error_msg = f"Process exited with status code {rc}"
            except TimeoutError:
                rc, error_msg = 124, f"Timeout after {timeout}s"
            except asyncio.CancelledError:
                rc, error_msg = 130, "Execution cancelled"
                self.set_task_status(task, TaskStatus.FAILED)
                task.error_msg = error_msg
                raise
            except OSError as exc:
                rc, error_msg = 127, str(exc)
            finally:
                duration = time.monotonic() - start
                task.duration += duration
                self.logger.close_task(task, status="COMPLETED" if rc == 0 else "FAILED",
                                       exit_code=rc, duration=duration)
            if rc in (130, -signal.SIGINT):
                self.set_task_status(task, TaskStatus.FAILED)
                self.exit(return_code=130)
                return False
            if rc == 0:
                task.error_msg = None
                self.set_task_status(task, TaskStatus.COMPLETED)
                if task.once:
                    self.once_store.mark_success(task, self.profile_name, 0, self.run_id)
                with self.state_file.open("a", encoding="utf-8") as stream:
                    stream.write(task.state_key + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                self.completed_keys.add(task.state_key)
                self.log_system(">>> EXECUTION SUCCESSFUL")
                self.active_task = None
                return True
            task.error_msg = error_msg
            if task.ignore_fail:
                self.set_task_status(task, TaskStatus.IGNORED)
                self.log_system(f"Ignored failure (exit {rc}): {task.script_name}")
                self.active_task = None
                return True
            if attempts_left:
                attempts_left -= 1
                self.log_system(f"Retrying {task.script_name} in {task.retry_delay}s ({attempts_left} retries left)")
                await asyncio.sleep(task.retry_delay)
                continue
            self.set_task_status(task, TaskStatus.FAILED)
            self.log_system(f">>> EXECUTION FAILED: {error_msg}")
            NotificationManager.play_sound("alert")
            policy = "abort" if self.stop_on_fail else task.on_failure
            if policy == "manual" and not use_interactive and not self.auto_mode:
                use_interactive = True
                continue
            if policy == "skip":
                self.set_task_status(task, TaskStatus.SKIPPED)
                self.active_task = None
                return True
            if policy == "continue":
                self.active_task = None
                return True
            if policy == "abort" or self.auto_mode:
                self.exit(return_code=1)
                return False
            res = await self.push_screen_wait(FailureModalScreen(task.script_name, error_msg))
            if res == "retry":
                continue
            if res == "skip":
                self.set_task_status(task, TaskStatus.SKIPPED)
                self.active_task = None
                return True
            self.exit(return_code=1)
            return False

    def task_skipped(self, task: OrchestratorTask, reason: str) -> None:
        task.error_msg = reason
        self.set_task_status(task, TaskStatus.SKIPPED)
        self.log_system(f"Skipped {task.script_name}: {reason}")
        self.current_idx += 1

    def action_quit_app(self) -> None:
        failed = any(task.status == TaskStatus.FAILED for task in self.tasks)
        code = 1 if failed or self.persistence_failed or self.logger.failed_write else 0
        self.exit(return_code=code if self.current_idx >= len(self.tasks) else 130)

    def action_toggle_manual(self):
        self.manual = not self.manual
        if self.manual:
            self.auto_mode = False
        mode = "ENABLED" if self.manual else "DISABLED"
        self.log_system(f"Manual step confirmation mode {mode}")

    def action_reset_state(self):
        if self.state_file.exists():
            try:
                self.state_file.unlink()
                self.completed_keys.clear()
                self.log_system("Phase completion state reset.")
            except OSError as e:
                self.log_system(f"Failed to reset state: {e}")

    def trigger_poweroff(self):
        set_auto_poweroff_marker()
        failed_tasks = [t for t in self.tasks if self.task_statuses.get(t.state_key) == "FAILED"]
        exit_code = 1 if failed_tasks or self.persistence_failed or self.logger.failed_write else 0

        if is_in_chroot() or os.environ.get("DUSKY_INSTALL_WRAPPER") == "1":
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
    args = EARLY_ARGS if EARLY_ARGS is not None else parse_args()

    if args.list_once:
        store = OnceStore(read_only=True)
        try:
            store.print_list()
        finally:
            store.close()
        return

    if args.forget_once:
        lock_file = Path("/tmp/orchestrator_phase2.lock" if args.phase2 or (not args.phase1 and is_in_chroot()) else "/tmp/orchestrator_phase1.lock")
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
        phase2 = is_in_chroot()
        phase1 = not phase2

    selected_profile: ProfileConfig | None = None
    explicit_path = Path(args.profile).expanduser() if args.profile else None
    if explicit_path is not None and explicit_path.is_file():
        selected_profile = load_profile(explicit_path)
        profiles = [selected_profile]
    else:
        profiles = discover_profiles()
        if args.profile:
            for p in profiles:
                if p.filepath and (p.filepath.name.lower() == args.profile.lower()
                                   or p.filepath.stem.lower() == args.profile.lower()
                                   or p.name.lower() == args.profile.lower()):
                    selected_profile = p
                    break

    if args.profile and selected_profile is None:
        raise ValueError(f"Unknown installer profile: {args.profile}")
    inspection = args.list_scripts or args.doctor or args.explain or args.dry_run
    if not inspection and os.geteuid() != 0:
        raise RuntimeError("This installer orchestrator must be run as root")
    repo_valid, repo_reason = (True, "not required")
    if not inspection and (selected_profile is None or "offline" in selected_profile.name.lower()):
        repo_valid, repo_reason = verify_offline_repo_fast()

    if not inspection and phase1:
        configure_live_console_font()

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
                raise RuntimeError(f"Offline repository unavailable: {repo_reason}. Select --online explicitly for online installation.")
            if not selected_profile:
                raise RuntimeError("No offline installer profile available; select --profile explicitly")
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
                    status_str = "[bold red][UNAVAILABLE][/bold red]"
                    available = False
                elif is_offline:
                    status_str = "[bold green][METADATA/SIZES CHECKED][/bold green]"
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
                        sys.exit(130)

                    idx = int(choice) - 1
                    p, avail = profile_choices[idx]
                    if not avail:
                        console.print(f"[red]Profile '{p.name}' is unavailable because the offline repository is corrupted. Please choose another option.[/red]")
                        continue
                    selected_profile = p
                    break
                except KeyboardInterrupt:
                    console.print("\n[bold yellow]Installation cancelled (Ctrl+C). Returning to live shell.[/bold yellow]")
                    sys.exit(130)
                except EOFError:
                    console.print("\n[yellow]Profile selection ended (EOF).[/yellow]")
                    raise RuntimeError("Profile selection ended before a profile was chosen")

    if not selected_profile:
        sys.stderr.write(f"Error: No valid installer profile found in '{PROFILES_DIR}'. Installation aborted.\n")
        sys.exit(1)

    if not inspection and "offline" in selected_profile.name.lower() and not repo_valid:
        raise RuntimeError(f"Offline repository unavailable: {repo_reason}")

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

    tasks: list[OrchestratorTask] = []
    occurrence: dict[str, int] = {}
    for i, t in enumerate(raw_sequence, start=1):
        resolved_path = resolve_script(t.script_name, selected_profile.search_dirs, selected_profile.conflict_resolutions)

        interpreter = t.interpreter
        is_interactive = t.interactive
        if resolved_path:
            interpreter, file_interactive = resolve_interpreter(resolved_path)
            if t.interactive_override is None and file_interactive:
                is_interactive = True
            if not shutil.which(interpreter):
                raise RuntimeError(f"Missing interpreter {interpreter!r} for {resolved_path}")
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
    if not inspection and selected_profile.filepath:
        if phase1 and os.environ.get("DUSKY_INSTALL_WRAPPER") == "1":
            (SCRIPT_DIR / ".selected_profile").write_text(str(selected_profile.filepath.resolve()))
        p_name = selected_profile.filepath.name
        for saved_profile in (Path("/tmp/dusky_selected_profile.txt"),
                              Path("/mnt/etc/dusky_selected_profile.txt"),
                              Path("/mnt/root/dusky_selected_profile.txt")):
            if saved_profile.parent.is_dir():
                with suppress(OSError):
                    saved_profile.write_text(p_name)

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
        sys.exit(1 if missing_all else 0)

    if args.explain:
        print(f"=== EXPLAIN FOR {profile_name} ===")
        for t in tasks:
            reasons = []
            if not t.resolved_path:
                reasons.append("MISSING SCRIPT -> ABORT")
            else:
                if t.once and not t.always and once_store.marker_valid(t, profile_name):
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
            elif t.once and not t.always and once_store.marker_valid(t, profile_name):
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
            except OSError as e:
                raise RuntimeError(f"Failed to reset state: {e}") from e
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
            auto_mode=args.auto,
            exit_on_complete=args.exit_on_complete,
        )
        app.run()
        sys.exit(app.return_code if app.return_code is not None else 1)
    except KeyboardInterrupt:
        sys.exit(130)
    finally:
        if "app" in locals():
            app.logger.close_all()
        once_store.close()
        release_lock()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, subprocess.SubprocessError, OSError, sqlite3.DatabaseError) as exc:
        sys.stderr.write(f"[ERROR] {exc}\n")
        sys.exit(1)
