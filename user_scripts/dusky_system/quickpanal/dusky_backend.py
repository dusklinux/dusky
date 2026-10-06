#!/usr/bin/env python3
"""
Dusky Backend - High-Performance Hardware Control, Subprocess Execution & Worker Pool Daemon
Target Specification: Arch Linux (Kernel 7.1+ / August 2026 Spec), Python 3.14.6+
Pure bleeding-edge implementation with zero legacy shims or backwards compatibility shims.
"""

from __future__ import annotations
import ctypes
import gc
import json
import logging
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import tomllib
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from gi.repository import Gio, GLib

# August 2026 Bleeding-Edge Constant declarations
APP_ID: Final[str] = "org.dusky.quickpanal"
HOME: Final[str] = os.path.expanduser("~")

# Thread-safe Logging Setup
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.WARNING,
        format=f"%(asctime)s - {APP_ID}: %(levelname)s: %(message)s"
    )
LOG: Final[logging.Logger] = logging.getLogger(APP_ID)

# Hardened and sandboxed environment variables for commands
COMMAND_ENV: Final[dict[str, str]] = os.environ.copy()
COMMAND_ENV["LC_ALL"] = "C.UTF-8"
COMMAND_ENV["LANG"] = "C.UTF-8"

# Python 3.14 PEP 695 Type Aliases
type CommandArg = str | os.PathLike[str]
type FloatGetter = Callable[[], float | None]
type FloatSubmitter = Callable[[float], None]

DEFAULT_SUNSET: Final[float] = 4500.0
QUERY_TIMEOUT: Final[float] = 0.8
CONTROL_TIMEOUT: Final[float] = 1.2
DDC_DETECT_TIMEOUT: Final[float] = 8.0
DDC_QUERY_TIMEOUT: Final[float] = 2.0
DDC_SET_TIMEOUT: Final[float] = 2.2
SUNSET_READY_TIMEOUT: Final[float] = 2.0
SUNSET_FALLBACK_READY_TIMEOUT: Final[float] = 1.0
LIVE_REFRESH_INTERVAL_SECONDS: Final[int] = 2
BRIGHTNESS_POST_SUBMIT_REFRESH_GRACE_SECONDS: Final[float] = max(CONTROL_TIMEOUT * 2, DDC_SET_TIMEOUT) + 0.3
SUNSET_STATE_WRITE_DEBOUNCE_SECONDS: Final[float] = 0.35
NO_PENDING: Final[object] = object()

# Binary resolution with strict path detection
WPCTL: Final[str | None] = shutil.which("wpctl")
BRIGHTNESSCTL: Final[str | None] = shutil.which("brightnessctl")
BUSCTL: Final[str | None] = shutil.which("busctl")
DDCUTIL: Final[str | None] = shutil.which("ddcutil")
HYPRCTL: Final[str | None] = shutil.which("hyprctl")
HYPRSUNSET: Final[str | None] = shutil.which("hyprsunset")
PGREP: Final[str | None] = shutil.which("pgrep")
SYSTEMCTL: Final[str | None] = shutil.which("systemctl")

_RE_MAKO_BADGE: Final[re.Pattern[str]] = re.compile(r"\d+")

def read_update_state(state_dir: Path) -> dict[str, Any]:
    """Combine cached package counts with the checker's current commit file."""
    counts: dict[str, int | None] = dict.fromkeys(('pacman', 'aur', 'dusky'))
    try:
        data = json.loads((state_dir / 'waybar_update_counter_h').read_text(encoding='utf-8'))
        cached = data.get('counts') if isinstance(data, dict) else None
        if isinstance(cached, dict):
            for category in ('pacman', 'aur'):
                value = cached.get(category)
                if type(value) is int and 0 <= value < 10**18:
                    counts[category] = value
    except (OSError, UnicodeError, ValueError):
        pass
    try:
        raw = (state_dir / 'dusky_update_behind_commit').read_text(encoding='utf-8').strip()
        if raw.isascii() and raw.isdecimal() and len(raw) <= 18:
            counts['dusky'] = int(raw)
    except (OSError, UnicodeError):
        pass
    total = sum(value for value in counts.values() if value is not None)
    unknown = any(value is None for value in counts.values())
    details = '\n'.join(
        f"{name}: {value if value is not None else 'unknown'}"
        for name, value in zip(('Pacman', 'AUR', 'Dusky commits (main)'), counts.values())
    )
    return {
        'badge': str(total) if total else '?' if unknown else '',
        'tooltip': f"Cached update counts\n{details}",
    }


# Return unused heap pages to the allocator's backing OS.
try:
    _LIBC: Final[ctypes.CDLL] = ctypes.CDLL("libc.so.6", use_errno=True)
    _LIBC.malloc_trim.argtypes = [ctypes.c_size_t]
    _LIBC.malloc_trim.restype = ctypes.c_int
except (OSError, AttributeError) as e:
    LOG.error(f"Failed to bind libc optimizations: {e}")
    _LIBC = None  # Fallback gracefully in simulated test suites

try:
    _PYTHONAPI = ctypes.pythonapi
    _PYTHONAPI.PyCapsule_GetPointer.restype = ctypes.c_void_p
    _PYTHONAPI.PyCapsule_GetPointer.argtypes = (ctypes.py_object, ctypes.c_char_p)
except Exception:
    _PYTHONAPI = None

def gi_object_c_pointer(gi_obj: object) -> ctypes.c_void_p | None:
    """Extract the underlying GObject* from a PyGObject wrapper."""
    if gi_obj is None:
        return None
    capsule = getattr(gi_obj, "__gpointer__", None)
    if capsule is None:
        return None
    if isinstance(capsule, int):
        return ctypes.c_void_p(capsule) if capsule else None
    if _PYTHONAPI is None:
        return None
    try:
        raw = _PYTHONAPI.PyCapsule_GetPointer(capsule, None)
    except Exception:
        return None
    return ctypes.c_void_p(raw) if raw else None

def _reclaim_idle_memory() -> None:
    """
    Active Memory Compaction & Trim
    Collects cycles and invokes malloc_trim to return free heap
    to the OS safely and efficiently without triggering swap churn.
    """
    gc.unfreeze()
    gc.collect()
    
    if _LIBC is not None:
        try:
            _LIBC.malloc_trim(0)
        except Exception as e:
            LOG.debug(f"malloc_trim failed: {e}")
            
    # Each child is reaped by its owner; waitpid(-1) steals subprocess results.

def clamp(value: float, lower: float, upper: float) -> float:
    if not math.isfinite(value):
        return lower
    return max(lower, min(upper, value))

def parse_float(text: str) -> float | None:
    try:
        return float(text.strip())
    except (ValueError, TypeError):
        return None

def percent_int(value: float, lower: int = 0) -> int:
    return int(max(lower, min(100, round(value))))

def snap_to_step(value: float, lower: float, upper: float, step: float) -> float:
    if not math.isfinite(value):
        return lower
    clamped = max(lower, min(upper, value))
    if step <= 0.0:
        return clamped
    snapped = round((clamped - lower) / step) * step + lower
    return max(lower, min(upper, snapped))

def kelvin_value(value: float) -> int:
    return int(max(1000, min(10000, round(value))))

def start_thread(name: str, target: Callable[..., None], *args: object, daemon: bool = True) -> threading.Thread:
    t = threading.Thread(name=name, target=target, args=args, daemon=daemon)
    t.start()
    return t

def run_command(
    args: Sequence[CommandArg],
    *,
    timeout: float = CONTROL_TIMEOUT,
    capture_stdout: bool = False,
    extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str] | None:
    env = COMMAND_ENV if extra_env is None else {**COMMAND_ENV, **extra_env}
    cmd_list = [os.fspath(a) for a in args]
    try:
        with subprocess.Popen(
            cmd_list,
            stdout=subprocess.PIPE if capture_stdout else None,
            stderr=subprocess.PIPE if capture_stdout else None,
            text=True,
            env=env,
            close_fds=True,
            start_new_session=True,
        ) as proc:
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                LOG.warning("Command '%s' timed out after %.2fs", cmd_list[0], timeout)
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError as exc:
                    if not isinstance(exc, ProcessLookupError):
                        LOG.warning("Could not stop process group for '%s': %s", cmd_list[0], exc)
                    if proc.poll() is None:
                        proc.kill()
                try:
                    proc.communicate(timeout=0.5)
                except subprocess.TimeoutExpired:
                    # A descendant that created another session may still hold
                    # the captured pipes; do not let it defeat this timeout.
                    for stream in (proc.stdout, proc.stderr):
                        if stream is not None:
                            stream.close()
                    proc.wait()
                return None
            return subprocess.CompletedProcess(cmd_list, proc.returncode, stdout, stderr)
    except FileNotFoundError:
        LOG.warning("Command '%s' not found on PATH", cmd_list[0])
        return None
    except OSError as e:
        LOG.error("Failed executing '%s': %s", cmd_list[0], e)
        return None

def execute_cmd(cmd: str, *, detached: bool = False) -> None:
    """Launch without blocking GTK or keeping a Python thread per child."""
    if not cmd or not cmd.strip():
        return
    try:
        # Let the shell handle quoting and tilde expansion. Launched applications
        # get their own scope, outside the panel's memory limit and stop lifecycle.
        argv = ['/usr/bin/bash', '-c', cmd]
        if detached:
            argv = ['/usr/bin/systemd-run', '--user', '--scope', '--collect',
                    '--quiet', '--expand-environment=no', '--', *argv]
        proc = Gio.Subprocess.new(
            argv, Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE
        )
        proc.wait_async(None, _command_finished)
    except GLib.Error as e:
        LOG.warning("Failed to fire-and-forget command '%s': %s", cmd, e)

def _command_finished(proc: Gio.Subprocess, result: Gio.AsyncResult) -> None:
    try:
        proc.wait_finish(result)
    except GLib.Error as exc:
        LOG.warning('Failed waiting for launched command: %s', exc)

def fetch_json_output(cmd: str) -> dict[str, Any] | None:
    r = run_command(shlex.split(cmd), timeout=1.2, capture_stdout=True)
    if r is not None and r.returncode == 0 and r.stdout.strip():
        try:
            data = json.loads(r.stdout.strip())
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass
    return None

def _resolve_state_dir() -> Path | None:
    candidates = []
    if xdg_state := os.environ.get("XDG_STATE_HOME"):
        candidates.append(Path(xdg_state) / APP_ID)
    candidates.append(Path.home() / ".local" / "state" / APP_ID)
    if xdg_runtime := os.environ.get("XDG_RUNTIME_DIR"):
        candidates.append(Path(xdg_runtime) / APP_ID)
    candidates.append(Path(f"/run/user/{os.getuid()}") / APP_ID)
    candidates.append(Path(tempfile.gettempdir()) / f"{APP_ID}-{os.getuid()}")
    for path in candidates:
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError:
            pass
        if path.is_dir() and os.access(path, os.W_OK | os.X_OK):
            return path
    return None

STATE_DIR: Final[Path | None] = _resolve_state_dir()
STATE_FILE: Final[Path | None] = None if STATE_DIR is None else STATE_DIR / "hyprsunset_state.txt"
DDCUTIL_CACHE_FILE: Final[Path | None] = None if STATE_DIR is None else STATE_DIR / "ddcutil_displays.json"

def atomic_write_text(path: Path, text: str, *, durable: bool = True) -> bool:
    temp_path: str | None = None
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            if durable:
                os.fsync(handle.fileno())
        os.replace(temp_path, path)
        if durable:
            try:
                dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        return True
    except OSError as exc:
        LOG.warning("Failed to atomically write %s: %s", path, exc)
        return False
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

class RefreshPool:
    __slots__ = ("_executor", "_max_workers", "_lock", "_suspended", "_pending")

    def __init__(self, max_workers: int = 4) -> None:
        self._max_workers = max_workers
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.RLock()
        self._suspended = False
        self._pending: dict[Callable[..., Any], Future[Any]] = {}

    def submit(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Future[Any] | None:
        with self._lock:
            if self._suspended:
                return None
            previous = self._pending.get(func)
            if previous is not None and not previous.done():
                return previous
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self._max_workers,
                    thread_name_prefix="dusky-refresh"
                )
            try:
                future = self._executor.submit(func, *args, **kwargs)
                self._pending[func] = future
                future.add_done_callback(lambda done: self._discard(func, done))
                return future
            except RuntimeError:
                return None

    def _discard(self, func: Callable[..., Any], future: Future[Any]) -> None:
        with self._lock:
            if self._pending.get(func) is future:
                del self._pending[func]

    def suspend(self) -> None:
        with self._lock:
            self._suspended = True
            if self._executor is not None:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None

    def resume(self) -> None:
        with self._lock:
            self._suspended = False

    def shutdown(self) -> None:
        self.suspend()

class LatestValueWorker:
    """
    Optimized modern hardware worker.
    Filters transient states to execute ONLY the latest submitted target, 
    preventing backlog overheads on continuous dragging interactions.
    """
    __slots__ = ("_apply_func", "_busy", "_condition", "_name", "_pending", "_running", "_thread")

    def __init__(self, name: str, apply_func: Callable[[float], None]) -> None:
        self._name = name
        self._apply_func = apply_func
        self._condition = threading.Condition()
        self._pending: float | object = NO_PENDING
        self._busy = False
        self._running = True
        self._thread: threading.Thread | None = None

    def submit(self, value: float) -> None:
        with self._condition:
            if not self._running:
                return
            self._pending = float(value)
            self._ensure_thread_locked()
            self._condition.notify()

    def start(self) -> None:
        with self._condition:
            if self._running:
                return
            self._running = True

    def is_busy(self) -> bool:
        with self._condition:
            return self._busy or self._pending is not NO_PENDING

    def stop(self, timeout: float = 1.5) -> None:
        with self._condition:
            self._running = False
            # Finish the last requested value even if the panel just closed.
            self._condition.notify_all()

    def _ensure_thread_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = start_thread(f"{self._name}-worker", self._worker, daemon=True)

    def _worker(self) -> None:
        while True:
            with self._condition:
                if self._pending is NO_PENDING:
                    self._thread = None
                    return
                value = self._pending
                self._pending = NO_PENDING
                self._busy = True
            try:
                if value is not NO_PENDING:
                    self._apply_func(float(value))
            except Exception:
                LOG.exception("Exception in %s worker execution", self._name)
            finally:
                with self._condition:
                    self._busy = False
                    self._condition.notify_all()

class DebouncedValueWriter:
    __slots__ = (
        "_busy", "_condition", "_deadline", "_delay_seconds", "_latest", 
        "_name", "_pending", "_running", "_thread", "_write_func"
    )

    def __init__(self, name: str, write_func: Callable[[float], None], *, delay_seconds: float) -> None:
        self._name = name
        self._write_func = write_func
        self._delay_seconds = max(0.0, delay_seconds)
        self._condition = threading.Condition()
        self._latest = 0.0
        self._deadline: float | None = None
        self._pending = False
        self._busy = False
        self._running = True
        self._thread: threading.Thread | None = None

    def schedule(self, value: float) -> None:
        with self._condition:
            self._running = True
            self._latest = float(value)
            self._deadline = time.monotonic() + self._delay_seconds
            self._pending = True
            self._ensure_thread_locked()
            self._condition.notify()

    def flush(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            if self._pending:
                self._deadline = time.monotonic()
                self._ensure_thread_locked()
                self._condition.notify()
            while self._pending or self._busy:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0.0:
                    return False
                self._condition.wait(remaining)
        return True

    def is_busy(self) -> bool:
        with self._condition:
            return self._busy or self._pending

    def stop(self, timeout: float = 1.5) -> None:
        with self._condition:
            self._running = False
            if self._pending:
                self._deadline = time.monotonic()
            self._condition.notify_all()

    def _ensure_thread_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = start_thread(f"{self._name}-writer", self._worker, daemon=True)

    def _worker(self) -> None:
        while True:
            with self._condition:
                while True:
                    if not self._pending:
                        self._thread = None
                        return
                    deadline = self._deadline
                    wait_time = 0.0 if deadline is None else deadline - time.monotonic()
                    if wait_time > 0.0:
                        self._condition.wait(wait_time)
                        continue
                    value = self._latest
                    self._pending = False
                    self._deadline = None
                    self._busy = True
                    break
            try:
                self._write_func(value)
            except Exception:
                pass
            finally:
                with self._condition:
                    self._busy = False
                    self._condition.notify_all()

@dataclass(slots=True, frozen=True)
class NotificationData:
    id: int
    app_name: str
    summary: str
    body: str
    source: str
    desktop_entry: str

NOTIF_CACHE_FILE: Final[Path] = Path(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir())) / "dusky_notif_times.json"
MAKO_BLACKLIST_FILE: Final[Path] = Path(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir())) / "mako_rofi_blacklist"
IGNORED_APPS_FILE: Final[Path] = Path(HOME) / "user_scripts" / "dusky_system" / "quickpanal" / "ignored_apps.toml"

def load_ignored_apps() -> set[str]:
    target = IGNORED_APPS_FILE
    if target.is_file():
        try:
            with open(target, "rb") as f:
                data = tomllib.load(f)
                if isinstance(data, dict) and "ignored" in data:
                    apps = data["ignored"].get("apps", [])
                    if isinstance(apps, list):
                        return {str(a) for a in apps if a}
        except Exception as e:
            LOG.error("Failed loading ignored apps %s: %s", target, e)
    return set()

def is_app_ignored(app_name: str, ignored_set: set[str]) -> bool:
    if not app_name:
        return False
    if app_name in ignored_set:
        return True
    for item in ignored_set:
        if item.endswith("*") and app_name.startswith(item[:-1]):
            return True
    return False

IGNORED_APPS_SET: Final[set[str]] = load_ignored_apps()

def fetch_notifications() -> list[NotificationData]:
    bl_path = MAKO_BLACKLIST_FILE
    blacklist = set()
    if bl_path.is_file():
        try:
            blacklist = set(bl_path.read_text(encoding="utf-8").splitlines())
        except OSError:
            pass
    ignored_apps = IGNORED_APPS_SET

    def _fetch_mako_json(cmd: list[str]) -> list[dict[str, Any]]:
        r = run_command(cmd, timeout=0.8, capture_stdout=True)
        if r is None or r.returncode != 0:
            raise RuntimeError(f"Could not fetch notifications with {' '.join(cmd)}")
        parsed = json.loads(r.stdout)
        items = parsed.get("data") if isinstance(parsed, dict) else parsed
        if isinstance(items, list) and items and isinstance(items[0], list):
            items = items[0]
        if not isinstance(items, list):
            raise ValueError("Unexpected makoctl notification response")
        return items

    active_items = _fetch_mako_json(["makoctl", "list", "-j"])
    history_items = _fetch_mako_json(["makoctl", "history", "-j"])
    combined: dict[int, NotificationData] = {}
    
    for src, items in [("history", history_items), ("active", active_items)]:
        for item in items:
            try:
                nid = int(item.get("id", -1))
                if nid < 0 or str(nid) in blacklist:
                    continue
                app = item.get("app_name") or item.get("app-name") or ""
                if is_app_ignored(app, ignored_apps):
                    continue
                summary = item.get("summary") or ""
                if not summary:
                    continue
                combined[nid] = NotificationData(
                    id=nid,
                    app_name=app,
                    summary=summary,
                    body=item.get("body") or "",
                    source=src,
                    desktop_entry=item.get("desktop_entry") or item.get("desktop-entry") or ""
                )
            except Exception:
                pass
    return sorted(combined.values(), key=lambda x: x.id, reverse=True)

def atomic_write_json(path: Path, data: Any) -> None:
    try:
        content = json.dumps(data, indent=2) + "\n"
        atomic_write_text(path, content, durable=False)
    except Exception as e:
        LOG.error("Failed atomic json write to %s: %s", path, e)

def get_volume() -> float | None:
    if WPCTL is None:
        return None
    r = run_command([WPCTL, "get-volume", "@DEFAULT_AUDIO_SINK@"], timeout=QUERY_TIMEOUT, capture_stdout=True)
    if r is None or r.returncode != 0:
        return None
    parts = r.stdout.split()
    if len(parts) < 2:
        return None
    val = parse_float(parts[1])
    return clamp(val * 100.0, 0.0, 100.0) if val is not None else None

def apply_volume(value: float) -> None:
    if WPCTL is None:
        return
    vol = percent_int(value)
    r = run_command([WPCTL, "set-volume", "@DEFAULT_AUDIO_SINK@", f"{vol}%"], timeout=CONTROL_TIMEOUT)
    if r is not None and r.returncode == 0 and vol > 0:
        run_command([WPCTL, "set-mute", "@DEFAULT_AUDIO_SINK@", "0"], timeout=CONTROL_TIMEOUT)

@dataclass(frozen=True, slots=True)
class BacklightDevice:
    priority: int
    maximum: int
    path: Path

    @property
    def brightness_path(self) -> Path:
        return self.path / "brightness"

    @property
    def max_brightness_path(self) -> Path:
        return self.path / "max_brightness"

    @property
    def actual_brightness_path(self) -> Path:
        return self.path / "actual_brightness"

_BACKLIGHT_DISCOVERY_TTL_SECONDS: Final[float] = 5.0
_backlight_discovery_lock: Final[threading.Lock] = threading.Lock()
_backlight_candidates_cache: tuple[float, tuple[BacklightDevice, ...]] | None = None

def _backlight_priority(name: str) -> int:
    lowered = name.lower()
    if lowered.startswith("intel_backlight"):
        return 400
    if lowered.startswith("amdgpu_bl"):
        return 350
    if lowered.startswith("nvidia"):
        return 300
    if lowered.startswith("ddcci"):
        return 250
    if "backlight" in lowered:
        return 200
    if lowered.startswith("acpi_video"):
        return 100
    return 0

def _sysfs_backlight_candidates() -> tuple[BacklightDevice, ...]:
    global _backlight_candidates_cache
    now = time.monotonic()
    with _backlight_discovery_lock:
        cached = _backlight_candidates_cache
        if cached is not None and now - cached[0] < _BACKLIGHT_DISCOVERY_TTL_SECONDS:
            return cached[1]
            
    base = Path("/sys/class/backlight")
    candidates: list[BacklightDevice] = []
    if base.is_dir():
        try:
            entries = tuple(base.iterdir())
        except OSError:
            entries = ()
        for entry in entries:
            if not entry.is_dir():
                continue
            brightness_path = entry / "brightness"
            max_brightness_path = entry / "max_brightness"
            if not brightness_path.is_file() or not max_brightness_path.is_file():
                continue
            try:
                maximum = int(max_brightness_path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                continue
            if maximum <= 0:
                continue
            candidates.append(BacklightDevice(
                priority=_backlight_priority(entry.name),
                maximum=maximum,
                path=entry
            ))
            
    candidates.sort(key=lambda d: (d.priority, d.maximum, d.path.name), reverse=True)
    result = tuple(candidates)
    with _backlight_discovery_lock:
        _backlight_candidates_cache = (time.monotonic(), result)
    return result

def _best_sysfs_backlight(*, require_writable: bool = False) -> BacklightDevice | None:
    for device in _sysfs_backlight_candidates():
        if require_writable and not os.access(device.brightness_path, os.W_OK):
            continue
        return device
    return None

def _preferred_sysfs_backlight() -> BacklightDevice | None:
    return _best_sysfs_backlight(require_writable=True) or _best_sysfs_backlight()

def _preferred_backlight_name() -> str | None:
    if (device := _preferred_sysfs_backlight()) is None:
        return None
    return device.path.name

def _brightnessctl_command_base() -> list[str] | None:
    if BRIGHTNESSCTL is None:
        return None
    args = [BRIGHTNESSCTL, "--class=backlight"]
    if (device_name := _preferred_backlight_name()) is not None:
        args.append(f"--device={device_name}")
    return args

def _has_writable_sysfs_backlight() -> bool:
    return _best_sysfs_backlight(require_writable=True) is not None

def _read_sysfs_brightness() -> float | None:
    if (device := _preferred_sysfs_backlight()) is None:
        return None
    read_path = device.actual_brightness_path if device.actual_brightness_path.is_file() else device.brightness_path
    try:
        current = parse_float(read_path.read_text(encoding="utf-8"))
        maximum = parse_float(device.max_brightness_path.read_text(encoding="utf-8"))
    except OSError:
        return None
    if current is None or maximum is None or maximum <= 0.0:
        return None
    return clamp(current / maximum * 100.0, 0.0, 100.0)

def _read_brightnessctl() -> float | None:
    if (base_cmd := _brightnessctl_command_base()) is None:
        return None
    result = run_command([*base_cmd, "--machine-readable"], timeout=QUERY_TIMEOUT, capture_stdout=True)
    if result is None or result.returncode != 0:
        return None
    lines = result.stdout.splitlines()
    if not lines:
        return None
    parts = lines[0].split(",")
    if len(parts) < 5:
        return None
    value = parse_float(parts[3].rstrip("%"))
    if value is None:
        return None
    return clamp(value, 0.0, 100.0)

def _write_sysfs_brightness(value: float) -> bool:
    if (device := _best_sysfs_backlight(require_writable=True)) is None:
        return False
    try:
        maximum = int(device.max_brightness_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if maximum <= 0:
        return False
    brightness = percent_int(value, lower=1)
    raw_value = max(1, min(maximum, int(round(brightness / 100.0 * maximum))))
    try:
        device.brightness_path.write_text(f"{raw_value}\n", encoding="utf-8")
    except OSError:
        return False
    return True

_logind_session_lock: Final[threading.Lock] = threading.Lock()
_logind_session_path: str | None = None

def _resolve_logind_session_path() -> str | None:
    global _logind_session_path
    with _logind_session_lock:
        if _logind_session_path is not None:
            return _logind_session_path
            
    if BUSCTL is None:
        return None
    session_id = os.environ.get("XDG_SESSION_ID")
    if not session_id:
        return None
    result = run_command(
        [BUSCTL, "call", "--system", "org.freedesktop.login1", "/org/freedesktop/login1", 
         "org.freedesktop.login1.Manager", "GetSession", "s", session_id], 
        timeout=QUERY_TIMEOUT, 
        capture_stdout=True
    )
    if result is None or result.returncode != 0:
        return None
    stdout = result.stdout.strip()
    if not stdout.startswith("o "):
        return None
    path = stdout[2:].strip().strip('"')
    if not path.startswith("/org/freedesktop/login1/session/"):
        return None
    with _logind_session_lock:
        _logind_session_path = path
    return path

def _write_logind_brightness(value: float) -> bool:
    if BUSCTL is None:
        return False
    device = _preferred_sysfs_backlight()
    if device is None:
        return False
    session_path = _resolve_logind_session_path()
    if session_path is None:
        return False
    try:
        maximum = int(device.max_brightness_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if maximum <= 0:
        return False
    brightness = percent_int(value, lower=1)
    raw_value = max(1, min(maximum, int(round(brightness / 100.0 * maximum))))
    result = run_command(
        [BUSCTL, "call", "--system", "org.freedesktop.login1", session_path, 
         "org.freedesktop.login1.Session", "SetBrightness", "ssu", "backlight", device.path.name, str(raw_value)], 
        timeout=CONTROL_TIMEOUT
    )
    return result is not None and result.returncode == 0

@dataclass(slots=True)
class DdcDisplay:
    bus: int
    max_value: int = 100
    last_percent: float | None = None

class DdcManager:
    """
    Highly parallelized August 2026 DDC/CI monitor brightness controller.
    Uses concurrent ThreadPoolExecutor workers to query and set brightnesses
    on multiple buses simultaneously, avoiding the massive sequential blockages of standard ddcutil.
    """
    __slots__ = (
        "_cache_file", "_detect_thread", "_displays",
        "_lock", "_started", "_workers", "_last_rescan_time", "_revision"
    )

    def __init__(self, cache_file: Path | None) -> None:
        self._cache_file = cache_file
        self._lock = threading.Lock()
        self._displays: dict[int, DdcDisplay] = {}
        self._workers: dict[int, LatestValueWorker] = {}
        self._started = False
        self._detect_thread: threading.Thread | None = None
        self._last_rescan_time = 0.0
        self._revision = 0

    def start(self) -> None:
        if DDCUTIL is None:
            return
        with self._lock:
            if self._started:
                return
            self._started = True
            if not self._displays:
                self._load_cache_locked()
            for worker in self._workers.values():
                worker.start()
        self.request_rescan()

    def request_rescan(self) -> None:
        if DDCUTIL is None:
            return
        with self._lock:
            now = time.monotonic()
            if not self._started or (self._last_rescan_time and now - self._last_rescan_time < 45.0):
                return
            thread = self._detect_thread
            if thread is not None and thread.is_alive():
                return
            self._last_rescan_time = now
            self._detect_thread = start_thread("ddcutil-detect", self._detect_worker, daemon=True)

    def submit(self, value: float) -> None:
        if DDCUTIL is None:
            return
        percent = float(percent_int(value, lower=1))
        with self._lock:
            self._revision += 1
            workers = tuple(worker for bus, worker in self._workers.items()
                            if self._displays[bus].last_percent is not None)
        for worker in workers:
            worker.submit(percent)

    def current_percent(self) -> float | None:
        self.request_rescan()
        with self._lock:
            if not self._displays:
                return None
            for bus in sorted(self._displays):
                if (value := self._displays[bus].last_percent) is not None:
                    return value
            return None

    def has_displays(self) -> bool:
        with self._lock:
            return bool(self._displays)

    def stop(self, timeout: float = 1.0) -> None:
        with self._lock:
            self._started = False
            workers = tuple(self._workers.values())
        for worker in workers:
            worker.stop(timeout)

    def _load_cache_locked(self) -> None:
        if self._cache_file is None or not self._cache_file.is_file():
            return
        try:
            data = json.loads(self._cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return
        entries: list[tuple[int, int]] = []
        if isinstance(data, list):
            for item in data:
                try:
                    if isinstance(item, dict):
                        bus = int(item.get("bus", -1))
                        maximum = int(item.get("max", 100))
                    else:
                        bus = int(item)
                        maximum = 100
                except (TypeError, ValueError):
                    continue
                if bus >= 0:
                    entries.append((bus, max(1, maximum)))
        for bus, maximum in entries:
            self._ensure_display_locked(bus, maximum, None)

    def _save_cache_snapshot(self) -> None:
        if self._cache_file is None:
            return
        with self._lock:
            records = [
                {"bus": display.bus, "max": display.max_value} 
                for display in sorted(self._displays.values(), key=lambda item: item.bus)
            ]
        atomic_write_text(self._cache_file, json.dumps(records, separators=(",", ":")) + "\n", durable=False)

    def _ensure_display_locked(self, bus: int, max_value: int, last_percent: float | None) -> None:
        max_value = max(1, int(max_value))
        if (display := self._displays.get(bus)) is None:
            display = DdcDisplay(bus=bus, max_value=max_value, last_percent=last_percent)
            self._displays[bus] = display
        else:
            display.max_value = max_value
            if last_percent is not None:
                display.last_percent = last_percent
        if bus not in self._workers:
            self._workers[bus] = LatestValueWorker(
                f"ddcutil-bus-{bus}", 
                lambda value, target_bus=bus: self._apply_bus(target_bus, value)
            )

    def _detect_worker(self) -> None:
        try:
            self._detect_worker_impl()
        except Exception as e:
            LOG.error("Failed DDC display discovery sequence: %s", e)
        finally:
            with self._lock:
                if not self._started:
                    self._last_rescan_time = 0.0

    def _detect_worker_impl(self) -> None:
        if DDCUTIL is None:
            return
        result = run_command([DDCUTIL, "detect", "--terse"], timeout=DDC_DETECT_TIMEOUT, capture_stdout=True)
        if result is None or result.returncode != 0:
            return
        with self._lock:
            if not self._started:
                return
            revision = self._revision
        buses = self._parse_detect_buses(result.stdout)
        
        # Bleeding-edge Optimization: Query all monitors concurrently via ThreadPool!
        discovered: dict[int, DdcDisplay] = {}
        futures: dict[Future[DdcDisplay | None], int] = {}
        
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="ddcutil-query") as pool:
            for bus in buses:
                fut = pool.submit(self._query_display, bus)
                futures[fut] = bus
            for fut in as_completed(futures):
                bus = futures[fut]
                try:
                    display = fut.result()
                    if display is not None:
                        discovered[bus] = display
                except Exception as e:
                    LOG.warning("Failed querying DDC monitor on bus %d: %s", bus, e)
                
        removed_workers: list[LatestValueWorker] = []
        with self._lock:
            if not self._started:
                return
            old_buses = set(self._displays)
            # A temporary getvcp failure must not remove a still-detected display.
            new_buses = set(buses)
            for bus in old_buses - new_buses:
                self._displays.pop(bus, None)
                if (worker := self._workers.pop(bus, None)) is not None:
                    removed_workers.append(worker)
            for bus, display in discovered.items():
                # Do not overwrite a slider change made while discovery ran.
                percent = display.last_percent if revision == self._revision or bus not in self._displays else None
                self._ensure_display_locked(bus, display.max_value, percent)
            
        for worker in removed_workers:
            worker.stop(0.25)
        self._save_cache_snapshot()

    @staticmethod
    def _parse_detect_buses(stdout: str) -> tuple[int, ...]:
        buses: set[int] = set()
        for line in stdout.splitlines():
            for token in line.replace(":", " ").replace(",", " ").split():
                if token.startswith("/dev/i2c-"):
                    suffix = token.rsplit("-", 1)[-1]
                elif token.startswith("i2c-"):
                    suffix = token.rsplit("-", 1)[-1]
                else:
                    continue
                if suffix.isdigit():
                    buses.add(int(suffix))
        return tuple(sorted(buses))

    def _query_display(self, bus: int) -> DdcDisplay | None:
        if DDCUTIL is None:
            return None
        result = run_command([DDCUTIL, "getvcp", "10", "--terse", "--bus", str(bus)], timeout=DDC_QUERY_TIMEOUT, capture_stdout=True)
        if result is None or result.returncode != 0:
            return None
        parsed = self._parse_getvcp_brightness(result.stdout)
        if parsed is None:
            return None
        current_raw, max_raw = parsed
        max_val = max(1, max_raw)
        current_percent = clamp(current_raw / max_val * 100.0, 0.0, 100.0)
        return DdcDisplay(bus=bus, max_value=max_val, last_percent=current_percent)

    @staticmethod
    def _parse_getvcp_brightness(stdout: str) -> tuple[int, int] | None:
        for line in stdout.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0] == "VCP" and parts[2] == "C":
                try:
                    current = int(parts[3])
                    maximum = int(parts[4])
                    if maximum > 0:
                        return (current, maximum)
                except ValueError:
                    return None
        return None

    def _apply_bus(self, bus: int, value: float) -> None:
        if DDCUTIL is None:
            return
        percent = float(percent_int(value, lower=1))
        with self._lock:
            display = self._displays.get(bus)
            max_value = 100 if display is None else max(1, display.max_value)
        raw_value = max(1, min(max_value, int(round(percent / 100.0 * max_value))))
        result = run_command([DDCUTIL, "setvcp", "10", str(raw_value), "--bus", str(bus)], timeout=DDC_SET_TIMEOUT)
        if result is None or result.returncode != 0:
            return
        with self._lock:
            if (display := self._displays.get(bus)) is not None:
                display.last_percent = percent

DDC_MANAGER: Final[DdcManager | None] = DdcManager(DDCUTIL_CACHE_FILE) if DDCUTIL is not None else None
HAS_VOLUME: Final[bool] = WPCTL is not None
HAS_LOCAL_BRIGHTNESS: Final[bool] = _preferred_sysfs_backlight() is not None and (
    BUSCTL is not None or BRIGHTNESSCTL is not None or _has_writable_sysfs_backlight()
)
HAS_DDC_BRIGHTNESS: Final[bool] = DDCUTIL is not None
HAS_BRIGHTNESS: Final[bool] = HAS_LOCAL_BRIGHTNESS or HAS_DDC_BRIGHTNESS
HAS_SUNSET: Final[bool] = HYPRCTL is not None and HYPRSUNSET is not None and bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))

def get_brightness() -> float | None:
    if DDC_MANAGER is not None:
        DDC_MANAGER.request_rescan()
    if (value := _read_sysfs_brightness()) is not None:
        return value
    if HAS_LOCAL_BRIGHTNESS and (value := _read_brightnessctl()) is not None:
        return value
    if DDC_MANAGER is None:
        return None
    return DDC_MANAGER.current_percent()

def apply_local_brightness(value: float) -> None:
    brightness = percent_int(value, lower=1)
    if _write_sysfs_brightness(brightness):
        return
    if _write_logind_brightness(brightness):
        return
    if (base_cmd := _brightnessctl_command_base()) is None:
        return
    run_command([*base_cmd, "--quiet", "set", f"{brightness}%"], timeout=CONTROL_TIMEOUT)

_SERVICE_ENABLED_CACHE: dict[str, bool] = {}
_SERVICE_ENABLED_LOCK = threading.Lock()
_SERVICE_ENABLED_REVISION = 0

def invalidate_service_enabled_cache() -> None:
    """UnitFilesChanged invalidates cached enablement without hidden polling."""
    global _SERVICE_ENABLED_REVISION
    with _SERVICE_ENABLED_LOCK:
        _SERVICE_ENABLED_CACHE.clear()
        _SERVICE_ENABLED_REVISION += 1

def _is_service_enabled(service: str, force_check: bool = False) -> bool:
    with _SERVICE_ENABLED_LOCK:
        cached = _SERVICE_ENABLED_CACHE.get(service)
        revision = _SERVICE_ENABLED_REVISION
    if cached is not None and not force_check:
        return cached
    if SYSTEMCTL is None:
        return False
    result = run_command([SYSTEMCTL, "--user", "is-enabled", service], timeout=0.5, capture_stdout=True)
    if result is None or not result.stdout.strip():
        return cached if cached is not None else False
    enabled = result.returncode == 0
    with _SERVICE_ENABLED_LOCK:
        # A query that overlapped an enable/disable must not refill a stale cache.
        if revision == _SERVICE_ENABLED_REVISION:
            _SERVICE_ENABLED_CACHE[service] = enabled
    return enabled

def is_dusky_notif_time_service_enabled(force_check: bool = False) -> bool:
    return _is_service_enabled("dusky_notif_time.service", force_check)

def is_hyprsunset_service_enabled(force_check: bool = False) -> bool:
    return HYPRSUNSET is not None and _is_service_enabled("hyprsunset.service", force_check)

def get_hyprsunset_state(controller: HyprsunsetController | None = None) -> float | None:
    if not is_hyprsunset_service_enabled():
        return None
    if controller is not None:
        if (target := controller.get_target_temperature()) is not None:
            return float(target)
    if STATE_FILE is None:
        return DEFAULT_SUNSET
    try:
        value = parse_float(STATE_FILE.read_text(encoding="utf-8"))
    except OSError:
        return DEFAULT_SUNSET
    if value is None:
        return DEFAULT_SUNSET
    return clamp(value, 1000.0, 6000.0)

def write_hyprsunset_state(value: float) -> None:
    if STATE_FILE is not None:
        atomic_write_text(STATE_FILE, f"{kelvin_value(value)}\n", durable=True)

class HyprsunsetController:
    __slots__ = (
        "_current_target", "_fallback_process", "_process_lock", 
        "_ready", "_state_writer", "_worker"
    )

    def __init__(self) -> None:
        self._current_target: float | None = None
        self._state_writer = DebouncedValueWriter("sunset-state", write_hyprsunset_state, delay_seconds=SUNSET_STATE_WRITE_DEBOUNCE_SECONDS)
        self._worker = LatestValueWorker("sunset", self._apply)
        self._ready = threading.Event()
        self._process_lock = threading.Lock()
        self._fallback_process: subprocess.Popen[bytes] | None = None

    def get_target_temperature(self) -> float | None:
        if self._worker.is_busy() or self._state_writer.is_busy():
            return self._current_target
        return None

    def submit(self, value: float) -> None:
        target = float(kelvin_value(value))
        self._current_target = target
        self._worker.submit(target)

    def start(self) -> None:
        self._worker.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._worker.stop(timeout)
        self._state_writer.stop(timeout)

    def _apply(self, value: float) -> None:
        target = kelvin_value(value)
        if self._send_temperature(target):
            self._mark_applied(target)
            return
            
        self._ready.clear()
        self._start_backend(target)
        if self._wait_until_applied(target, SUNSET_READY_TIMEOUT):
            return
            
        self._spawn_fallback_process(target)
        if self._wait_until_applied(target, SUNSET_FALLBACK_READY_TIMEOUT):
            return
        if self._current_target == float(target):
            self._current_target = None
        LOG.warning('Failed to apply sunset temperature %d K', target)

    def _mark_applied(self, target: int) -> None:
        self._ready.set()
        self._state_writer.schedule(float(target))

    def _wait_until_applied(self, target: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._send_temperature(target):
                self._mark_applied(target)
                return True
            time.sleep(0.05)
        return False

    def _send_temperature(self, target: int) -> bool:
        if HYPRCTL is None:
            return False
        result = run_command([HYPRCTL, "hyprsunset", "temperature", str(target)], timeout=QUERY_TIMEOUT)
        return result is not None and result.returncode == 0

    def _start_backend(self, target: int) -> None:
        if SYSTEMCTL is not None:
            result = run_command([SYSTEMCTL, "--user", "start", "hyprsunset.service"], timeout=CONTROL_TIMEOUT)
            if result is not None and result.returncode == 0:
                return
        if not self._is_hyprsunset_running():
            self._spawn_fallback_process(target)

    def _is_hyprsunset_running(self) -> bool:
        with self._process_lock:
            proc = self._fallback_process
            if proc is not None and proc.poll() is None:
                return True
        if PGREP is None:
            return False
        result = run_command([PGREP, "-u", str(os.getuid()), "-x", "hyprsunset"], timeout=QUERY_TIMEOUT)
        return result is not None and result.returncode == 0

    def _spawn_fallback_process(self, target: int) -> None:
        if HYPRSUNSET is None:
            return
        with self._process_lock:
            proc = self._fallback_process
            if proc is not None:
                if proc.poll() is None:
                    return
                self._fallback_process = None
            try:
                new_proc = subprocess.Popen(
                    [HYPRSUNSET, "--temperature", str(target)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                    env=COMMAND_ENV
                )
            except OSError:
                return
            self._fallback_process = new_proc
        start_thread("hyprsunset-reaper", self._reap_fallback_process, new_proc, daemon=True)

    def _reap_fallback_process(self, proc: subprocess.Popen[bytes]) -> None:
        try:
            proc.wait()
        except Exception:
            pass
        finally:
            was_active_backend = False
            with self._process_lock:
                if self._fallback_process is proc:
                    self._fallback_process = None
                    was_active_backend = True
            if was_active_backend and not self._is_hyprsunset_running():
                self._ready.clear()
