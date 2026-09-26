"""
Utility functions for the Dusky Control Center.

Thread-safe utility library for GTK4 control center on Arch Linux (Hyprland).
Persisted settings use an atomic batched buffer. Public helpers are thread-safe.
"""
from __future__ import annotations

import atexit
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import tomllib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Final, TypeVar, overload

from gi.repository import GLib

if TYPE_CHECKING:
    from gi.repository import Adw

__all__ = [
    "CACHE_DIR",
    "LABEL_NA",
    "SETTINGS_DIR",
    "execute_command",
    "get_cache_dir",
    "get_system_value",
    "load_config",
    "load_setting",
    "preflight_check",
    "register_toast_overlay",
    "save_setting",
    "toast",
]

log: logging.Logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# =============================================================================
# CONSTANTS & PATHS
# =============================================================================
LABEL_NA: Final[str] = "N/A"
_LEADING_ENV_ASSIGNMENT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*=.*"
)


def _get_xdg_path(env_var: str, default_suffix: str) -> Path:
    value = os.environ.get(env_var, "").strip()
    if value:
        candidate = Path(value)
        if candidate.is_absolute():
            return candidate
    return Path.home() / default_suffix


_XDG_CACHE_HOME: Final[Path] = _get_xdg_path("XDG_CACHE_HOME", ".cache")
_XDG_CONFIG_HOME: Final[Path] = _get_xdg_path("XDG_CONFIG_HOME", ".config")

CACHE_DIR: Final[Path] = _XDG_CACHE_HOME / "duskycc"
SETTINGS_DIR: Final[Path] = _XDG_CONFIG_HOME / "dusky" / "settings"

# Terminal emulator is user-configurable via default_apps.lua (same source the
# text-editor launcher reads). Resolved once and cached.
_TERMINAL_CONFIG_PATH: Final[Path] = (
    Path.home() / ".config" / "hypr" / "edit_here" / "source" / "default_apps.lua"
)
_TERMINAL_DEFAULT: Final[str] = "kitty"


_last_terminal_mtime: float = 0.0
_cached_terminal: str = _TERMINAL_DEFAULT


def _get_configured_terminal() -> str:
    """Return the user's configured terminal, falling back to a sane default."""
    global _last_terminal_mtime, _cached_terminal
    try:
        mtime = _TERMINAL_CONFIG_PATH.stat().st_mtime
        if mtime == _last_terminal_mtime:
            return _cached_terminal
        content = _TERMINAL_CONFIG_PATH.read_text(encoding="utf-8")
        _last_terminal_mtime = mtime
    except OSError:
        return _TERMINAL_DEFAULT

    # Skip Lua comment lines so a commented-out `-- terminal = "..."` never
    # shadows the real setting.
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        match = re.search(
            r"(?:local\s+)?terminal\s*=\s*['\"]([^'\"]+)['\"]",
            stripped,
        )
        if match:
            terminal = match.group(1).strip()
            if terminal:
                _cached_terminal = terminal
                return terminal
    _cached_terminal = _TERMINAL_DEFAULT
    return _TERMINAL_DEFAULT



# =============================================================================
# THREAD-SAFE STATE CONTAINERS
# =============================================================================
class _ResolvedDirectoryCache:
    __slots__ = ("_base_dir", "_lock", "_resolved")

    def __init__(self, base_dir: Path) -> None:
        self._base_dir: Final[Path] = base_dir
        self._lock: Final[threading.Lock] = threading.Lock()
        self._resolved: Path | None = None

    def get(self) -> Path:
        resolved = self._resolved
        if resolved is not None:
            return resolved

        with self._lock:
            if self._resolved is not None:
                return self._resolved
            try:
                self._base_dir.mkdir(parents=True, exist_ok=True)
                self._resolved = self._base_dir.resolve(strict=True)
            except OSError as e:
                log.error("Failed to resolve directory %s: %s", self._base_dir, e)
                return self._base_dir
            return self._resolved


class _ComputeOnceCache:
    __slots__ = ("_cache", "_in_flight", "_lock")

    def __init__(self) -> None:
        self._lock: Final[threading.Lock] = threading.Lock()
        self._cache: dict[str, object] = {}
        self._in_flight: dict[str, threading.Condition] = {}

    def get_or_compute(self, key: str, compute_fn: Callable[[], _T]) -> _T:
        with self._lock:
            while key in self._in_flight:
                cond = self._in_flight[key]
                cond.wait()
                if key in self._cache:
                    return self._cache[key]

            if key in self._cache:
                return self._cache[key]

            cond = threading.Condition(self._lock)
            self._in_flight[key] = cond

        try:
            value = compute_fn()
        except BaseException:
            with self._lock:
                del self._in_flight[key]
                cond.notify_all()
            raise

        with self._lock:
            self._cache[key] = value
            del self._in_flight[key]
            cond.notify_all()

        return value


_settings_dir_cache: Final = _ResolvedDirectoryCache(SETTINGS_DIR)
_cache_dir_cache: Final = _ResolvedDirectoryCache(CACHE_DIR)
_system_info_cache: Final = _ComputeOnceCache()


def get_cache_dir() -> Path:
    return _cache_dir_cache.get()


# =============================================================================
# CONFIGURATION LOADER
# =============================================================================
def load_config(config_path: Path) -> dict[str, object]:
    """Load and parse a TOML config file.

    Raises:
        FileNotFoundError: if the file does not exist.
        TOMLDecodeError:  if the file contains invalid TOML (message includes the
                          offending line/column so callers can render a useful
                          error instead of a misleading "missing keys" fallback).
    """
    content = config_path.read_text(encoding="utf-8")
    data = tomllib.loads(content)
    return data if isinstance(data, dict) else {}


# =============================================================================
# COMMAND RUNNER (NO UWSM)
# =============================================================================
def execute_command(
    cmd_string: str,
    title: str,
    run_in_terminal: bool,
    requires_root: bool = False
) -> bool:
    normalized_cmd = _normalize_command(cmd_string)
    if not normalized_cmd:
        return False

    safe_title = _sanitize_title(title)
    full_cmd = _build_command_list(normalized_cmd, safe_title, run_in_terminal, requires_root)

    if full_cmd is None:
        log.error("Failed to parse command: %r", cmd_string)
        return False

    if run_in_terminal:
        terminal = full_cmd[0]
        if shutil.which(terminal) is None:
            log.error("Terminal launcher '%s' was not found in PATH", terminal)
            return False
    elif not requires_root and full_cmd[0:2] != ["sh", "-c"]:
        executable = full_cmd[0]
        if shutil.which(executable) is None:
            log.error("Executable not found: %r", executable)
            return False

    spawn_cmd = full_cmd
    if shutil.which("dusky-run") and spawn_cmd and spawn_cmd[0] != "dusky-run":
        spawn_cmd = ["dusky-run", *spawn_cmd]

    try:
        GLib.spawn_async(
            spawn_cmd,
            flags=GLib.SpawnFlags.SEARCH_PATH,
        )
        return True
    except GLib.Error as e:
        log.error("Executable failed or not found: %s. (GLib Error: %s)", spawn_cmd, e.message)
        return False
    except Exception as e:
        log.error("Unexpected error executing %r: %s", cmd_string, e)
        return False


def execute_argv(argv: list[str]) -> bool:
    """Launch a configured argument vector without interpreting its data as shell code."""
    if not argv or not all(isinstance(arg, str) for arg in argv):
        return False
    expanded = [
        str(Path.home() / arg[2:]) if arg.startswith("~/") else
        str(Path.home() / arg[6:]) if arg.startswith("$HOME/") else arg
        for arg in argv
    ]
    if shutil.which(expanded[0]) is None and not Path(expanded[0]).is_file():
        log.error("Executable not found: %s", expanded[0])
        return False
    command = ["dusky-run", *expanded] if shutil.which("dusky-run") and expanded[0] != "dusky-run" else expanded
    try:
        GLib.spawn_async(command, flags=GLib.SpawnFlags.SEARCH_PATH)
        return True
    except GLib.Error as error:
        log.error("Could not launch %s: %s", expanded[0], error.message)
        return False


def _normalize_command(cmd_string: str) -> str:
    cmd = cmd_string.strip()
    # Expand a leading executable path without requiring an extra shell.
    for prefix in ("$HOME/", "~/"):
        if cmd.startswith(prefix):
            return shlex.quote(str(Path.home())) + "/" + cmd[len(prefix):]
    return cmd


def _sanitize_title(title: str | None) -> str:
    base = (title or "").strip() or "Dusky Terminal"
    sanitized = "".join(c if c.isprintable() and c not in "\n\r\t\x00" else " " for c in base)
    return " ".join(sanitized.split()) or "Dusky Terminal"


def _requires_shell(command: str, parsed_args: list[str]) -> bool:
    if _LEADING_ENV_ASSIGNMENT_PATTERN.fullmatch(parsed_args[0]) is not None:
        return True

    in_single = False
    in_double = False
    escaped = False
    token_start = True

    for index, ch in enumerate(command):
        if escaped:
            escaped = False
            token_start = False
            continue
        if in_single:
            if ch == "'":
                in_single = False
            continue
        if in_double:
            if ch == "\\":
                # shlex does not implement the shell's double-quoted escape
                # rules for dollar signs and backticks.
                return True
            elif ch == '"':
                in_double = False
            elif ch in "$`":
                return True
            continue
        if ch.isspace():
            if ch == "\n":
                return True
            token_start = True
            continue
        if ch == "\\":
            escaped = True
            token_start = False
            continue
        if ch == "'":
            in_single = True
            token_start = False
            continue
        if ch == '"':
            in_double = True
            token_start = False
            continue
        if ch in "|&;()<>`$*?[!]{}#":
            return True
        if ch == "~" and token_start:
            next_ch = command[index + 1] if index + 1 < len(command) else ""
            if not next_ch or next_ch.isspace() or next_ch == "/":
                return True
        token_start = False

    return False


def _build_terminal_wrapped(
    terminal: str,
    safe_title: str,
    inner_args: list[str],
) -> list[str] | None:
    """Wrap an inner command in the user's configured terminal emulator.

    Flag syntax differs per emulator; the known set mirrors the text-editor
    launcher. Unknown terminals fall back to the POSIX ``-e`` convention.
    """
    try:
        terminal_args = shlex.split(terminal)
    except ValueError:
        return None
    if not terminal_args:
        return None
    term = Path(terminal_args[0]).name.lower()

    if term in {"foot", "footclient"}:
        return [
            *terminal_args, "--app-id", "dusky-term", "--title", safe_title,
            "--hold", *inner_args
        ]

    if term == "kitty":
        return [
            *terminal_args, "--class", "dusky-term", "--title", safe_title,
            "--hold", *inner_args
        ]

    if term == "alacritty":
        return [
            *terminal_args, "--class", "dusky-term", "--title", safe_title,
            "--hold", "-e", *inner_args
        ]

    if term == "wezterm":
        return [
            *terminal_args, "start", "--class", "dusky-term",
            "--", *inner_args
        ]

    # Generic fallback: $TERM -e <cmd...>
    return [*terminal_args, "-e", *inner_args]


def _build_command_list(
    normalized_cmd: str, safe_title: str, run_in_terminal: bool, requires_root: bool
) -> list[str] | None:
    terminal = _get_configured_terminal() if run_in_terminal else ""

    if requires_root:
        if run_in_terminal:
            return _build_terminal_wrapped(
                terminal, safe_title, ["pkexec", "sh", "-c", normalized_cmd]
            )
        return ["pkexec", "sh", "-c", normalized_cmd]

    if run_in_terminal:
        return _build_terminal_wrapped(
            terminal, safe_title, ["sh", "-c", normalized_cmd]
        )

    try:
        parsed_args = shlex.split(normalized_cmd, posix=True)
    except ValueError:
        return ["sh", "-c", normalized_cmd]

    if not parsed_args:
        return None

    if _requires_shell(normalized_cmd, parsed_args):
        return ["sh", "-c", normalized_cmd]

    return parsed_args


# =============================================================================
# PRE-FLIGHT DEPENDENCY CHECK
# =============================================================================
def preflight_check() -> None:
    missing_deps: list[str] = []

    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
    except (ImportError, ValueError):
        missing_deps.append("python-gobject (GTK4/Libadwaita)")

    if missing_deps:
        msg = "FATAL: Dusky Control Center missing dependencies:\n" + "\n".join(f"  - {dep}" for dep in missing_deps)
        log.critical(msg)
        print(msg, file=sys.stderr)
        sys.exit(1)

    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        test_file = SETTINGS_DIR / ".write_test"
        test_file.touch()
        test_file.unlink()
    except OSError as e:
        log.warning("Settings directory %s is not writable: %s", SETTINGS_DIR, e)


# =============================================================================
# SYSTEM VALUE RETRIEVAL
# =============================================================================
def get_system_value(key: str) -> str:
    if key in {"memory_used"}:
        return _compute_system_value(key)
    return _system_info_cache.get_or_compute(key, lambda: _compute_system_value(key))


def _compute_system_value(key: str) -> str:
    match key:
        case "memory_total":
            return _get_memory_total()
        case "memory_used":
            return _get_memory_used()
        case "cpu_model":
            return _get_cpu_model()
        case "gpu_model":
            return _get_gpu_model()
        case "kernel_version":
            return os.uname().release
        case _:
            return LABEL_NA


def _get_memory_total() -> str:
    try:
        content = Path("/proc/meminfo").read_text(encoding="utf-8")
        for line in content.splitlines():
            if line.startswith("MemTotal:"):
                parts = line.split()
                if len(parts) >= 2:
                    kb = int(parts[1])
                    gb = round(kb / 1_048_576, 1)
                    return f"{gb} GB"
    except (OSError, ValueError, IndexError):
        pass
    return LABEL_NA


def _get_memory_used() -> str:
    try:
        content = Path("/proc/meminfo").read_text(encoding="utf-8")
        mem_total = 0
        mem_available = 0
        for line in content.splitlines():
            if line.startswith("MemTotal:"):
                mem_total = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                mem_available = int(line.split()[1])
        if mem_total and mem_available:
            used_kb = mem_total - mem_available
            used_gb = round(used_kb / 1_048_576, 1)
            return f"{used_gb} GB"
    except (OSError, ValueError, IndexError):
        pass
    return LABEL_NA


def _get_cpu_model() -> str:
    try:
        content = Path("/proc/cpuinfo").read_text(encoding="utf-8")
        for line in content.splitlines():
            if line.strip().lower().startswith("model name"):
                _, _, value = line.partition(":")
                return value.strip().split(" @")[0]
    except OSError:
        pass
    return LABEL_NA


def _get_gpu_model() -> str:
    try:
        res = subprocess.run(["lspci", "-mm"], capture_output=True, text=True, timeout=5, check=False)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                try:
                    fields = shlex.split(line, posix=True)
                except ValueError:
                    continue
                if len(fields) >= 4 and fields[1] in {"VGA compatible controller", "3D controller"}:
                    return f"{fields[2]} {fields[3]}".strip()
        res = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5, check=False)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if "VGA compatible controller" in line or "3D controller" in line:
                    parts = line.split(":", 2)
                    if len(parts) > 2:
                        return parts[2].strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return LABEL_NA


# =============================================================================
# SETTINGS PERSISTENCE (Batched Atomic File I/O)
# =============================================================================
def _validate_settings_path(key: str) -> Path | None:
    if not key or not isinstance(key, str) or "\0" in key:
        return None

    pure = PurePosixPath(key)
    if pure.is_absolute() or any(part in {"", ".."} for part in pure.parts):
        log.warning("Invalid settings path key: %r", key)
        return None

    base = _settings_dir_cache.get()
    target = base / key

    try:
        resolved = target.resolve(strict=False)
    except (OSError, RuntimeError) as error:
        log.warning("Invalid settings path %r: %s", key, error)
        return None
    if resolved == base or not resolved.is_relative_to(base):
        log.warning("Settings path escapes settings directory: %r", key)
        return None
    return resolved


def _write_to_disk_atomic(target: Path, value: str) -> bool:
    temp_fd: int | None = None
    temp_path: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_fd, temp_path_str = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
        temp_path = Path(temp_path_str)

        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            temp_fd = None
            f.write(value)
            f.flush()
            os.fsync(f.fileno())

        temp_path.rename(target)
        temp_path = None

        try:
            dir_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass

        return True
    except OSError as e:
        log.error("Save failed for %s: %s", target.name, e)
        return False
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


class _SettingsWriteBuffer:
    """Thread-safe write buffer that flushes sequentially to preserve SSD life."""
    __slots__ = ("_buffer", "_pending", "_lock", "_source_id", "_executor")
    _instance: _SettingsWriteBuffer | None = None

    def __new__(cls) -> _SettingsWriteBuffer:
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._buffer = {}
            inst._pending = {}
            inst._lock = threading.Lock()
            inst._source_id = 0
            inst._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dusky-io-batch")
            atexit.register(inst._flush_synchronously)
            cls._instance = inst
        return cls._instance

    def queue_write(self, key: str, value: str) -> bool:
        target = _validate_settings_path(key)
        if target is None:
            return False

        with self._lock:
            pending = (value,)
            self._buffer[target] = pending
            self._pending[target] = pending
            if self._source_id == 0:
                self._source_id = GLib.timeout_add(250, self._flush_buffer_cb)
        return True

    def _flush_buffer_cb(self) -> bool:
        with self._lock:
            batch = self._buffer.copy()
            self._buffer.clear()
            self._source_id = 0

        if batch:
            self._executor.submit(self._execute_batch, batch)
        return GLib.SOURCE_REMOVE

    def _execute_batch(self, batch: dict[Path, tuple[str]]) -> None:
        for target, pending in batch.items():
            saved = _write_to_disk_atomic(target, pending[0])
            with self._lock:
                if self._pending.get(target) is pending:
                    self._pending.pop(target, None)
            if not saved:
                # Safely notify UI on the main thread if background save fails
                GLib.idle_add(
                    toast,
                    _global_toast_overlay,
                    "Failed to save settings: Disk Error"
                )

    def _flush_synchronously(self) -> None:
        """Called automatically on exit to prevent data loss for pending writes."""
        with self._lock:
            batch = self._buffer.copy()
            self._buffer.clear()
            source_id = self._source_id
            self._source_id = 0

        if source_id:
            GLib.source_remove(source_id)
        # Finish older batches before writing the newest values.
        self._executor.shutdown(wait=True)
        if batch:
            self._execute_batch(batch)


def flush_settings() -> None:
    if (buffer := _SettingsWriteBuffer._instance) is not None:
        buffer._flush_synchronously()


def save_setting(key: str, value: bool | int | float | str) -> bool:
    return _SettingsWriteBuffer().queue_write(key, str(value))


@overload
def load_setting(key: str, default: bool, *, preserve_whitespace: bool = False) -> bool: ...

@overload
def load_setting(key: str, default: int, *, preserve_whitespace: bool = False) -> int: ...

@overload
def load_setting(key: str, default: float, *, preserve_whitespace: bool = False) -> float: ...

@overload
def load_setting(key: str, default: str, *, preserve_whitespace: bool = False) -> str: ...

@overload
def load_setting(key: str, default: None = None, *, preserve_whitespace: bool = False) -> str | None: ...

def load_setting(
    key: str,
    default: bool | int | float | str | None = None,
    *, preserve_whitespace: bool = False,
) -> bool | int | float | str | None:
    target = _validate_settings_path(key)
    if target is None:
        return default

    # Always check the dirty buffer first to prevent stale reads mid-flush
    buffer_inst = _SettingsWriteBuffer()
    with buffer_inst._lock:
        if target in buffer_inst._pending:
            raw = buffer_inst._pending[target][0]
            return _coerce_type(raw if preserve_whitespace else raw.strip(), default)

    try:
        raw = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return default

    return _coerce_type(raw if preserve_whitespace else raw.strip(), default)


def _coerce_type(raw: str, default: Any) -> Any:
    try:
        match default:
            case bool():
                return _parse_bool(raw)
            case int():
                return int(raw)
            case float():
                return float(raw)
            case _:
                return raw
    except ValueError:
        return default


def _parse_bool(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"true", "yes", "on", "1"}:
        return True
    if lowered in {"false", "no", "off", "0", ""}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


# =============================================================================
# UI HELPERS & REGISTRY
# =============================================================================
_global_toast_overlay: Adw.ToastOverlay | None = None

def register_toast_overlay(overlay: Adw.ToastOverlay) -> None:
    """Register the global toast overlay for background notifications."""
    global _global_toast_overlay
    _global_toast_overlay = overlay

def toast(toast_overlay: Adw.ToastOverlay | None, message: str, timeout: int = 2) -> None:
    if toast_overlay is None:
        return
    
    def _show() -> bool:
        try:
            from gi.repository import Adw as AdwLib
            t = AdwLib.Toast.new(message)
            t.set_timeout(timeout)
            toast_overlay.add_toast(t)
        except Exception:
            pass
        return GLib.SOURCE_REMOVE
    
    GLib.idle_add(_show)
