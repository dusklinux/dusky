#!/usr/bin/env python3
"""
Backend module for Dusky Quick Panal.
Handles multithreading, process execution, memory reclamation, 
hardware interfaces, and notification DBUS states.
"""

from __future__ import annotations

import contextvars
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
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

APP_ID: Final = "org.dusky.quickpanal"
HOME: Final = os.path.expanduser("~")

if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.WARNING, format=f"{APP_ID}: %(levelname)s: %(message)s")

LOG: Final = logging.getLogger(APP_ID)

COMMAND_ENV: Final = os.environ.copy()
COMMAND_ENV["LC_ALL"] = "C.UTF-8"
COMMAND_ENV["LANG"] = "C.UTF-8"

type CommandArg = str | os.PathLike[str]
type FloatGetter = Callable[[], float | None]
type FloatSubmitter = Callable[[float], None]

DEFAULT_SUNSET: Final = 4500.0
QUERY_TIMEOUT: Final = 0.90
CONTROL_TIMEOUT: Final = 1.50
DDC_DETECT_TIMEOUT: Final = 15.0
DDC_QUERY_TIMEOUT: Final = 2.50
DDC_SET_TIMEOUT: Final = 2.75
SUNSET_READY_TIMEOUT: Final = 2.50
SUNSET_FALLBACK_READY_TIMEOUT: Final = 1.25
LIVE_REFRESH_INTERVAL_SECONDS: Final = 2
BRIGHTNESS_POST_SUBMIT_REFRESH_GRACE_SECONDS: Final = max(1.50, QUERY_TIMEOUT + 0.50)
SUNSET_STATE_WRITE_DEBOUNCE_SECONDS: Final = 0.40

NO_PENDING: Final = object()

WPCTL: Final = shutil.which("wpctl")
BRIGHTNESSCTL: Final = shutil.which("brightnessctl")
DDCUTIL: Final = shutil.which("ddcutil")
HYPRCTL: Final = shutil.which("hyprctl")
HYPRSUNSET: Final = shutil.which("hyprsunset")
PGREP: Final = shutil.which("pgrep")
SYSTEMCTL: Final = shutil.which("systemctl")

_RE_MAKO_BADGE: Final = re.compile(r'\d+')
_RE_UPDATES_TOTAL: Final = re.compile(r'Total:\s*(\d+)')

# ==============================================================================
# IDLE RAM RECLAMATION
# ==============================================================================
_LIBC: Final = ctypes.CDLL("libc.so.6", use_errno=True)
_MADV_PAGEOUT: Final = 21

def _reclaim_idle_memory() -> None:
    re.purge()
    if hasattr(sys, "_clear_internal_caches"):
        sys._clear_internal_caches()
    elif hasattr(sys, "_clear_type_cache"):
        sys._clear_type_cache()
    gc.collect()
    gc.freeze()
    try:
        _LIBC.malloc_trim(0)
    except Exception:
        pass
    _pageout_idle_pages()

def _pageout_idle_pages() -> None:
    try:
        with open("/proc/self/maps", "r") as f:
            for line in f:
                parts = line.split(None, 5)
                if len(parts) < 2: continue
                perms = parts[1]
                if "r" not in perms or "x" in perms or "p" not in perms: continue
                path = parts[5].strip() if len(parts) > 5 else ""
                if path in ("[vdso]", "[vvar]", "[vsyscall]") or path.startswith("[stack"): continue
                
                start_s, end_s = parts[0].split("-")
                start, length = int(start_s, 16), int(end_s, 16) - int(start_s, 16)
                if length > 0:
                    _LIBC.madvise(ctypes.c_void_p(start), ctypes.c_size_t(length), _MADV_PAGEOUT)
    except Exception:
        pass

# ==============================================================================
# UTILITIES
# ==============================================================================
def clamp(value: float, lower: float, upper: float) -> float:
    if not math.isfinite(value): return lower
    return max(lower, min(upper, value))

def parse_float(text: str) -> float | None:
    try: return float(text.strip()) if math.isfinite(float(text.strip())) else None
    except ValueError: return None

def percent_int(value: float, lower: int = 0) -> int:
    return int(clamp(round(value), float(lower), 100.0))

def snap_to_step(value: float, lower: float, upper: float, step: float) -> float:
    if step <= 0.0: return clamp(value, lower, upper)
    scaled = (value - lower) / step
    snapped = lower + math.floor(scaled + 0.5 + 1e-12) * step
    return round(clamp(snapped, lower, upper), 10)

def kelvin_value(value: float) -> int:
    return int(clamp(round(value), 1000.0, 6000.0))

def start_thread(name: str, target: Callable[..., None], *args: object, daemon: bool = True) -> threading.Thread:
    thread = threading.Thread(name=name, target=target, args=args, daemon=daemon, context=contextvars.Context())
    thread.start()
    return thread

def run_command(args: Sequence[CommandArg], *, timeout: float, capture_stdout: bool = False) -> subprocess.CompletedProcess[str] | None:
    argv = [os.fspath(arg) for arg in args]
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env=COMMAND_ENV, close_fds=True, start_new_session=True, text=True, encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        LOG.debug("Command failed to start: %r: %s", argv, exc)
        return None
    try:
        stdout, _ = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, None)
    except subprocess.TimeoutExpired:
        try: os.killpg(proc.pid, signal.SIGKILL)
        except OSError: pass
        proc.communicate()
        return None
    except Exception:
        try: os.killpg(proc.pid, signal.SIGKILL)
        except OSError: pass
        proc.communicate()
        return None

def execute_cmd(cmd: str) -> None:
    try:
        subprocess.Popen(["/usr/bin/bash", "-c", cmd], start_new_session=True, env=COMMAND_ENV, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    except OSError as e:
        LOG.warning(f"Failed to execute command '{cmd}': {e}")

def fetch_json_output(cmd: str) -> dict[str, Any] | None:
    r = run_command(shlex.split(cmd), timeout=1.5, capture_stdout=True)
    if r is not None and r.returncode == 0 and r.stdout.strip():
        try: return json.loads(r.stdout.strip())
        except json.JSONDecodeError: pass
    return None

def _resolve_state_dir() -> Path | None:
    candidates = []
    if (xdg_state := os.environ.get("XDG_STATE_HOME")): candidates.append(Path(xdg_state) / APP_ID)
    candidates.append(Path.home() / ".local" / "state" / APP_ID)
    if (xdg_runtime := os.environ.get("XDG_RUNTIME_DIR")): candidates.append(Path(xdg_runtime) / APP_ID)
    candidates.append(Path(f"/run/user/{os.getuid()}") / APP_ID)
    candidates.append(Path(tempfile.gettempdir()) / f"{APP_ID}-{os.getuid()}")

    for path in candidates:
        try: path.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError: pass
        if path.is_dir() and os.access(path, os.W_OK | os.X_OK): return path
    return None

STATE_DIR: Final = _resolve_state_dir()
STATE_FILE: Final = None if STATE_DIR is None else STATE_DIR / "hyprsunset_state.txt"
DDCUTIL_CACHE_FILE: Final = None if STATE_DIR is None else STATE_DIR / "ddcutil_displays.json"

def atomic_write_text(path: Path, text: str, *, durable: bool = True) -> bool:
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            if durable: os.fsync(handle.fileno())
        os.replace(temp_path, path)
        if durable:
            try:
                dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                os.fsync(dir_fd)
                os.close(dir_fd)
            except OSError: pass
        return True
    except OSError as exc:
        LOG.warning("Failed to write %s: %s", path, exc)
        return False

# ==============================================================================
# WORKERS & THREADING
# ==============================================================================
class RefreshPool:
    __slots__ = ("_executor", "_max_workers", "_lock")
    def __init__(self, max_workers: int = 4) -> None:
        self._max_workers = max_workers
        self._executor = None
        self._lock = threading.Lock()

    def submit(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Future[Any] | None:
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="dusky-refresh")
            try: return self._executor.submit(func, *args, **kwargs)
            except RuntimeError: return None

    def shutdown(self) -> None:
        with self._lock:
            if self._executor is not None:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None

class LatestValueWorker:
    __slots__ = ("_apply_func", "_busy", "_condition", "_name", "_pending", "_running", "_thread")
    def __init__(self, name: str, apply_func: Callable[[float], None]) -> None:
        self._name = name
        self._apply_func = apply_func
        self._condition = threading.Condition()
        self._pending: float | object = NO_PENDING
        self._busy = False
        self._running = True
        self._thread: threading.Thread | None = None
        with self._condition: self._ensure_thread_locked()

    def submit(self, value: float) -> None:
        with self._condition:
            if not self._running: return
            self._pending = float(value)
            self._ensure_thread_locked()
            self._condition.notify()

    def start(self) -> None:
        with self._condition:
            if self._running: return
            self._running = True
            self._ensure_thread_locked()

    def stop(self, timeout: float = 2.0) -> None:
        with self._condition:
            self._running = False
            self._pending = NO_PENDING
            self._condition.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    def _ensure_thread_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive(): return
        self._thread = start_thread(f"{self._name}-worker", self._worker, daemon=True)

    def _worker(self) -> None:
        while True:
            with self._condition:
                while self._running and self._pending is NO_PENDING:
                    self._condition.wait()
                if not self._running: return
                value = self._pending
                self._pending = NO_PENDING
                self._busy = True
            try:
                if value is not NO_PENDING:
                    self._apply_func(float(value))
            except Exception: LOG.exception("Exception in %s worker", self._name)
            finally:
                with self._condition:
                    self._busy = False
                    self._condition.notify_all()

class DebouncedValueWriter:
    __slots__ = ("_busy", "_condition", "_deadline", "_delay_seconds", "_latest", "_name", "_pending", "_running", "_thread", "_write_func")

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
        with self._condition: self._ensure_thread_locked()

    def schedule(self, value: float) -> None:
        with self._condition:
            if not self._running: return
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
            while self._running and (self._pending or self._busy):
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0.0: return False
                self._condition.wait(remaining)
        return True

    def stop(self, timeout: float = 2.0) -> None:
        self.flush(timeout)
        with self._condition:
            self._running = False
            self._condition.notify_all()
            thread = self._thread
        if thread is not None:
            try: thread.join(timeout=timeout)
            except Exception: pass

    def _ensure_thread_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive(): return
        self._thread = start_thread(f"{self._name}-writer", self._worker, daemon=True)

    def _worker(self) -> None:
        while True:
            with self._condition:
                while True:
                    if not self._running and not self._pending: return
                    if not self._pending:
                        self._condition.wait()
                        continue
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
            try: self._write_func(value)
            except Exception: pass
            finally:
                with self._condition:
                    self._busy = False
                    self._condition.notify_all()

# ==============================================================================
# NOTIFICATION SYSTEM (MAKO DBUS BRIDGE)
# ==============================================================================
@dataclass(slots=True, frozen=True)
class NotificationData:
    id: int
    app_name: str
    summary: str
    body: str
    source: str
    desktop_entry: str

def fetch_notifications() -> list[NotificationData]:
    """Fetch and merge active and history buffers from Mako, respecting blacklists."""
    bl_path = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "mako_rofi_blacklist"
    blacklist = set()
    if bl_path.is_file():
        try: blacklist = set(bl_path.read_text(encoding="utf-8").splitlines())
        except OSError: pass

    ignored_apps = {"OSD", "dusky-keys", "dusky-cava", "dusky-cava-alert", "dusky-glance", "dusky-glance-alert", "dusky-recorder", "Spotify"}

    def _fetch_mako_json(cmd: list[str]) -> list[dict]:
        r = run_command(cmd, timeout=1.0, capture_stdout=True)
        if r is not None and r.returncode == 0:
            try:
                parsed = json.loads(r.stdout)
                if isinstance(parsed, dict) and "data" in parsed:
                    data = parsed["data"]
                    if data and isinstance(data, list) and isinstance(data[0], list): return data[0]
                    if data and isinstance(data, list): return data
                if isinstance(parsed, list):
                    if len(parsed) > 0 and isinstance(parsed[0], list): return parsed[0]
                    return parsed
            except json.JSONDecodeError: pass
        return []

    active_items = _fetch_mako_json(["makoctl", "list", "-j"])
    history_items = _fetch_mako_json(["makoctl", "history", "-j"])

    combined = {}
    for src, items in [("history", history_items), ("active", active_items)]:
        for item in items:
            try:
                nid = int(item.get("id", -1))
                if nid < 0 or str(nid) in blacklist: continue
                app = item.get("app-name", item.get("app_name", ""))
                if app in ignored_apps: continue
                summary = item.get("summary", "")
                if not summary: continue
                
                combined[nid] = NotificationData(
                    id=nid,
                    app_name=app,
                    summary=summary,
                    body=item.get("body", ""),
                    source=src,
                    desktop_entry=item.get("desktop-entry", "")
                )
            except Exception: pass

    return sorted(combined.values(), key=lambda x: x.id, reverse=True)


# ==============================================================================
# HARDWARE CONTROL
# ==============================================================================
def get_volume() -> float | None:
    if WPCTL is None: return None
    r = run_command([WPCTL, "get-volume", "@DEFAULT_AUDIO_SINK@"], timeout=QUERY_TIMEOUT, capture_stdout=True)
    if r is None or r.returncode != 0: return None
    parts = r.stdout.split()
    if len(parts) < 2: return None
    val = parse_float(parts[1])
    return clamp(val * 100.0, 0.0, 100.0) if val is not None else None

def apply_volume(value: float) -> None:
    if WPCTL is None: return
    vol = percent_int(value)
    r = run_command([WPCTL, "set-volume", "@DEFAULT_AUDIO_SINK@", f"{vol}%"], timeout=CONTROL_TIMEOUT)
    if r is not None and r.returncode == 0 and vol > 0:
        run_command([WPCTL, "set-mute", "@DEFAULT_AUDIO_SINK@", "0"], timeout=CONTROL_TIMEOUT)

# --- Sysfs Backlight & Hardware Brightness Controls ---
@dataclass(frozen=True, slots=True)
class BacklightDevice:
    priority: int
    maximum: int
    path: Path
    @property
    def brightness_path(self) -> Path: return self.path / "brightness"
    @property
    def max_brightness_path(self) -> Path: return self.path / "max_brightness"
    @property
    def actual_brightness_path(self) -> Path: return self.path / "actual_brightness"

_BACKLIGHT_DISCOVERY_TTL_SECONDS: Final = 5.0
_backlight_discovery_lock: Final = threading.Lock()
_backlight_candidates_cache: tuple[float, tuple[BacklightDevice, ...]] | None = None

def _backlight_priority(name: str) -> int:
    lowered = name.lower()
    if lowered.startswith("intel_backlight"): return 400
    if lowered.startswith("amdgpu_bl"): return 350
    if lowered.startswith("nvidia"): return 300
    if lowered.startswith("ddcci"): return 250
    if "backlight" in lowered: return 200
    if lowered.startswith("acpi_video"): return 100
    return 0

def _sysfs_backlight_candidates() -> tuple[BacklightDevice, ...]:
    global _backlight_candidates_cache
    now = time.monotonic()
    with _backlight_discovery_lock:
        cached = _backlight_candidates_cache
        if cached is not None and now - cached[0] < _BACKLIGHT_DISCOVERY_TTL_SECONDS: return cached[1]

    base = Path("/sys/class/backlight")
    candidates: list[BacklightDevice] = []
    if base.is_dir():
        try: entries = tuple(base.iterdir())
        except OSError: entries = ()
        for entry in entries:
            if not entry.is_dir(): continue
            brightness_path = entry / "brightness"
            max_brightness_path = entry / "max_brightness"
            if not brightness_path.is_file() or not max_brightness_path.is_file(): continue
            try: maximum = int(max_brightness_path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError): continue
            if maximum <= 0: continue
            candidates.append(BacklightDevice(priority=_backlight_priority(entry.name), maximum=maximum, path=entry))
            
    candidates.sort(key=lambda device: (device.priority, device.maximum), reverse=True)
    result = tuple(candidates)
    with _backlight_discovery_lock:
        _backlight_candidates_cache = (time.monotonic(), result)
    return result

def _best_sysfs_backlight(*, require_writable: bool = False) -> BacklightDevice | None:
    for device in _sysfs_backlight_candidates():
        if require_writable and not os.access(device.brightness_path, os.W_OK): continue
        return device
    return None

def _preferred_sysfs_backlight() -> BacklightDevice | None:
    return _best_sysfs_backlight(require_writable=True) or _best_sysfs_backlight()

def _preferred_backlight_name() -> str | None:
    if (device := _preferred_sysfs_backlight()) is None: return None
    return device.path.name

def _brightnessctl_command_base() -> list[str] | None:
    if BRIGHTNESSCTL is None: return None
    args = [BRIGHTNESSCTL, "--class=backlight"]
    if (device_name := _preferred_backlight_name()) is not None: args.append(f"--device={device_name}")
    return args

def _has_writable_sysfs_backlight() -> bool:
    return _best_sysfs_backlight(require_writable=True) is not None

def _read_sysfs_brightness() -> float | None:
    if (device := _preferred_sysfs_backlight()) is None: return None
    read_path = device.actual_brightness_path if device.actual_brightness_path.is_file() else device.brightness_path
    try:
        current = parse_float(read_path.read_text(encoding="utf-8"))
        maximum = parse_float(device.max_brightness_path.read_text(encoding="utf-8"))
    except OSError: return None
    if current is None or maximum is None or maximum <= 0.0: return None
    return clamp((current / maximum) * 100.0, 0.0, 100.0)

def _read_brightnessctl() -> float | None:
    if (base_cmd := _brightnessctl_command_base()) is None: return None
    result = run_command([*base_cmd, "--machine-readable"], timeout=QUERY_TIMEOUT, capture_stdout=True)
    if result is None or result.returncode != 0: return None
    lines = result.stdout.splitlines()
    if not lines: return None
    parts = lines[0].split(",")
    if len(parts) < 5: return None
    value = parse_float(parts[4].rstrip("%"))
    if value is None: return None
    return clamp(value, 0.0, 100.0)

def _write_sysfs_brightness(value: float) -> bool:
    if (device := _best_sysfs_backlight(require_writable=True)) is None: return False
    try: maximum = int(device.max_brightness_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError): return False
    if maximum <= 0: return False
    brightness = percent_int(value, lower=1)
    raw_value = max(1, min(maximum, int(round((brightness / 100.0) * maximum))))
    try:
        device.brightness_path.write_text(f"{raw_value}\n", encoding="utf-8")
    except OSError: return False
    return True

# --- DDC Display Management ---
@dataclass(slots=True)
class DdcDisplay:
    bus: int
    max_value: int = 100
    last_percent: float | None = None

class DdcManager:
    __slots__ = ("_cache_file", "_detect_thread", "_displays", "_last_requested", "_lock", "_started", "_workers", "_last_rescan_time")

    def __init__(self, cache_file: Path | None) -> None:
        self._cache_file = cache_file
        self._lock = threading.Lock()
        self._displays: dict[int, DdcDisplay] = {}
        self._workers: dict[int, LatestValueWorker] = {}
        self._last_requested: float | None = None
        self._started = False
        self._detect_thread: threading.Thread | None = None
        self._last_rescan_time = 0.0

    def start(self) -> None:
        if DDCUTIL is None: return
        with self._lock:
            if self._started: return
            self._started = True
            self._load_cache_locked()
        self.request_rescan()

    def request_rescan(self) -> None:
        if DDCUTIL is None: return
        with self._lock:
            now = time.monotonic()
            if now - self._last_rescan_time < 60.0: return
            self._last_rescan_time = now
            thread = self._detect_thread
            if thread is not None and thread.is_alive(): return
            self._detect_thread = start_thread("ddcutil-detect", self._detect_worker, daemon=True)

    def submit(self, value: float) -> None:
        if DDCUTIL is None: return
        percent = float(percent_int(value, lower=1))
        with self._lock:
            self._last_requested = percent
            workers = tuple(self._workers.values())
        for worker in workers: worker.submit(percent)

    def current_percent(self) -> float | None:
        with self._lock:
            has_displays = bool(self._displays)
            last_requested = self._last_requested
            should_rescan = self._started if not has_displays else False
            if not has_displays: result = None
            elif last_requested is not None: result = last_requested
            else: result = NO_PENDING
            
        if should_rescan: self.request_rescan()
        if result is None: return None
        if result is not NO_PENDING: return float(result)
        
        with self._lock:
            if not self._displays: return None
            for bus in sorted(self._displays):
                if (value := self._displays[bus].last_percent) is not None: return value
            return 50.0

    def has_displays(self) -> bool:
        with self._lock: return bool(self._displays)

    def stop(self, timeout: float = 1.5) -> None:
        with self._lock:
            self._started = False
            workers = tuple(self._workers.values())
            self._workers.clear()
        for worker in workers: worker.stop(timeout)

    def _load_cache_locked(self) -> None:
        if self._cache_file is None or not self._cache_file.is_file(): return
        try: data = json.loads(self._cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError): return
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
                except (TypeError, ValueError): continue
                if bus >= 0: entries.append((bus, max(1, maximum)))
        for bus, maximum in entries: self._ensure_display_locked(bus, maximum, None)

    def _save_cache_snapshot(self) -> None:
        if self._cache_file is None: return
        with self._lock:
            records = [{"bus": display.bus, "max": display.max_value} for display in sorted(self._displays.values(), key=lambda item: item.bus)]
        atomic_write_text(self._cache_file, json.dumps(records, separators=(",", ":")) + "\n", durable=False)

    def _ensure_display_locked(self, bus: int, max_value: int, last_percent: float | None) -> None:
        max_value = max(1, int(max_value))
        if (display := self._displays.get(bus)) is None:
            display = DdcDisplay(bus=bus, max_value=max_value, last_percent=last_percent)
            self._displays[bus] = display
        else:
            display.max_value = max_value
            if last_percent is not None: display.last_percent = last_percent
        if bus not in self._workers:
            self._workers[bus] = LatestValueWorker(f"ddcutil-bus-{bus}", lambda value, target_bus=bus: self._apply_bus(target_bus, value))

    def _detect_worker(self) -> None:
        try: self._detect_worker_impl()
        except Exception: pass

    def _detect_worker_impl(self) -> None:
        if DDCUTIL is None: return
        result = run_command([DDCUTIL, "detect", "--terse"], timeout=DDC_DETECT_TIMEOUT, capture_stdout=True)
        if result is None or result.returncode != 0: return
        buses = self._parse_detect_buses(result.stdout)
        discovered: dict[int, DdcDisplay] = {}
        for bus in buses:
            display = self._query_display(bus)
            if display is not None: discovered[bus] = display
        removed_workers: list[LatestValueWorker] = []
        
        with self._lock:
            if not self._started: return
            old_buses = set(self._displays)
            new_buses = set(discovered)
            for bus in old_buses - new_buses:
                self._displays.pop(bus, None)
                if (worker := self._workers.pop(bus, None)) is not None: removed_workers.append(worker)
            for bus, display in discovered.items():
                self._ensure_display_locked(bus, display.max_value, display.last_percent)
            last_requested = self._last_requested
            workers = tuple(self._workers.values())
            
        for worker in removed_workers: worker.stop(0.25)
        if last_requested is not None:
            for worker in workers: worker.submit(last_requested)
        self._save_cache_snapshot()

    @staticmethod
    def _parse_detect_buses(stdout: str) -> tuple[int, ...]:
        buses: set[int] = set()
        for line in stdout.splitlines():
            for token in line.replace(":", " ").replace(",", " ").split():
                if token.startswith("/dev/i2c-"): suffix = token.rsplit("-", 1)[-1]
                elif token.startswith("i2c-"): suffix = token.rsplit("-", 1)[-1]
                else: continue
                if suffix.isdigit(): buses.add(int(suffix))
        return tuple(sorted(buses))

    def _query_display(self, bus: int) -> DdcDisplay | None:
        if DDCUTIL is None: return None
        result = run_command([DDCUTIL, "getvcp", "10", "--terse", "--bus", str(bus)], timeout=DDC_QUERY_TIMEOUT, capture_stdout=True)
        if result is None or result.returncode != 0: return None
        parsed = self._parse_getvcp_brightness(result.stdout)
        if parsed is None: return None
        current_raw, max_raw = parsed
        max_value = max(1, max_raw)
        current_percent = clamp((current_raw / max_value) * 100.0, 0.0, 100.0)
        return DdcDisplay(bus=bus, max_value=max_value, last_percent=current_percent)

    @staticmethod
    def _parse_getvcp_brightness(stdout: str) -> tuple[int, int] | None:
        for line in stdout.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0] == "VCP" and parts[2] == "C":
                try:
                    current = int(parts[3])
                    maximum = int(parts[4])
                except ValueError: return None
                if maximum > 0: return current, maximum
        return None

    def _apply_bus(self, bus: int, value: float) -> None:
        if DDCUTIL is None: return
        percent = float(percent_int(value, lower=1))
        with self._lock:
            display = self._displays.get(bus)
            max_value = 100 if display is None else max(1, display.max_value)
        raw_value = max(1, min(max_value, int(round((percent / 100.0) * max_value))))
        result = run_command([DDCUTIL, "setvcp", "10", str(raw_value), "--bus", str(bus)], timeout=DDC_SET_TIMEOUT)
        if result is None or result.returncode != 0: return
        with self._lock:
            if (display := self._displays.get(bus)) is not None: display.last_percent = percent


DDC_MANAGER: Final = DdcManager(DDCUTIL_CACHE_FILE) if DDCUTIL is not None else None

HAS_VOLUME: Final = WPCTL is not None
HAS_LOCAL_BRIGHTNESS: Final = _preferred_sysfs_backlight() is not None and (BRIGHTNESSCTL is not None or _has_writable_sysfs_backlight())
HAS_DDC_BRIGHTNESS: Final = DDCUTIL is not None
HAS_BRIGHTNESS: Final = HAS_LOCAL_BRIGHTNESS or HAS_DDC_BRIGHTNESS
HAS_SUNSET: Final = HYPRCTL is not None and HYPRSUNSET is not None and bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))

def get_brightness() -> float | None:
    if (value := _read_sysfs_brightness()) is not None: return value
    if (value := _read_brightnessctl()) is not None: return value
    if DDC_MANAGER is None: return None
    return DDC_MANAGER.current_percent()

def apply_local_brightness(value: float) -> None:
    brightness = percent_int(value, lower=1)
    if _write_sysfs_brightness(brightness): return
    if (base_cmd := _brightnessctl_command_base()) is None: return
    run_command([*base_cmd, "--quiet", "set", f"{brightness}%"], timeout=CONTROL_TIMEOUT)

def get_hyprsunset_state() -> float:
    if STATE_FILE is None: return DEFAULT_SUNSET
    try: value = parse_float(STATE_FILE.read_text(encoding="utf-8"))
    except OSError: return DEFAULT_SUNSET
    if value is None: return DEFAULT_SUNSET
    return clamp(value, 1000.0, 6000.0)

def write_hyprsunset_state(value: float) -> None:
    if STATE_FILE is not None:
        atomic_write_text(STATE_FILE, f"{kelvin_value(value)}\n", durable=True)

class HyprsunsetController:
    __slots__ = ("_fallback_process", "_process_lock", "_ready", "_state_writer", "_worker")

    def __init__(self) -> None:
        self._state_writer = DebouncedValueWriter("sunset-state", write_hyprsunset_state, delay_seconds=SUNSET_STATE_WRITE_DEBOUNCE_SECONDS)
        self._worker = LatestValueWorker("sunset", self._apply)
        self._ready = threading.Event()
        self._process_lock = threading.Lock()
        self._fallback_process: subprocess.Popen[bytes] | None = None

    def submit(self, value: float) -> None:
        self._worker.submit(float(kelvin_value(value)))

    def start(self) -> None:
        self._worker.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._worker.stop(timeout)
        self._state_writer.stop(timeout)

    def _apply(self, value: float) -> None:
        target = kelvin_value(value)
        if self._send_temperature(target):
            self._mark_applied(target)
            return
        self._ready.clear()
        self._start_backend(target)
        if self._wait_until_applied(target, SUNSET_READY_TIMEOUT): return
        self._spawn_fallback_process(target)
        if self._wait_until_applied(target, SUNSET_FALLBACK_READY_TIMEOUT): return

    def _mark_applied(self, target: int) -> None:
        self._ready.set()
        self._state_writer.schedule(float(target))

    def _wait_until_applied(self, target: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._send_temperature(target):
                self._mark_applied(target)
                return True
            time.sleep(0.08)
        return False

    def _send_temperature(self, target: int) -> bool:
        if HYPRCTL is None: return False
        result = run_command([HYPRCTL, "hyprsunset", "temperature", str(target)], timeout=QUERY_TIMEOUT)
        return result is not None and result.returncode == 0

    def _start_backend(self, target: int) -> None:
        if SYSTEMCTL is not None:
            result = run_command([SYSTEMCTL, "--user", "start", "hyprsunset.service"], timeout=CONTROL_TIMEOUT)
            if result is not None and result.returncode == 0: return
        if not self._is_hyprsunset_running():
            self._spawn_fallback_process(target)

    def _is_hyprsunset_running(self) -> bool:
        with self._process_lock:
            proc = self._fallback_process
            if proc is not None and proc.poll() is None: return True
        if PGREP is None: return False
        result = run_command([PGREP, "-u", str(os.getuid()), "-x", "hyprsunset"], timeout=QUERY_TIMEOUT)
        return result is not None and result.returncode == 0

    def _spawn_fallback_process(self, target: int) -> None:
        if HYPRSUNSET is None: return
        with self._process_lock:
            proc = self._fallback_process
            if proc is not None:
                if proc.poll() is None: return
                self._fallback_process = None
            try:
                new_proc = subprocess.Popen(
                    [HYPRSUNSET, "--temperature", str(target)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True, close_fds=True, env=COMMAND_ENV,
                )
            except OSError as exc: return
            self._fallback_process = new_proc
        start_thread("hyprsunset-reaper", self._reap_fallback_process, new_proc, daemon=True)

    def _reap_fallback_process(self, proc: subprocess.Popen[bytes]) -> None:
        try: proc.wait()
        except Exception: pass
        finally:
            was_active_backend = False
            with self._process_lock:
                if self._fallback_process is proc:
                    self._fallback_process = None
                    was_active_backend = True
            if was_active_backend and not self._is_hyprsunset_running():
                self._ready.clear()
