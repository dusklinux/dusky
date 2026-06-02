#!/usr/bin/env python3
"""
Dusky Quick Panal: Unified GTK3 Control Center & Sliders.

Target: Arch Linux + Hyprland + Python 3.14.5+
Features: Top-Aligned Metrics, 5x2 Glassy Grid, Battle-Tested Hardware Sliders, Full MPRIS.
GTK3-native with system theme integration and aggressive idle RAM reclamation.
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
from datetime import datetime
from pathlib import Path
from typing import Any, Final, override

if sys.version_info < (3, 14, 5):
    raise SystemExit("Dusky Quick Panal requires Python 3.14.5 or newer.")

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("Pango", "1.0")
    from gi.repository import Gdk, Gio, GLib, Gtk, Pango
except (ImportError, ValueError) as exc:
    raise SystemExit(f"Failed to load GTK3: {exc}") from exc


APP_ID: Final = "org.dusky.quickpanal"
HOME: Final = os.path.expanduser("~")

if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.WARNING,
        format=f"{APP_ID}: %(levelname)s: %(message)s",
    )

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
PLAYERCTL: Final = shutil.which("playerctl")

_RE_MAKO_BADGE: Final = re.compile(r'\d+')
_RE_UPDATES_TOTAL: Final = re.compile(r'Total:\s*(\d+)')

# ==============================================================================
try:
    _grab_lib_path = os.path.expanduser("~/user_scripts/dusky_system/click_away_to_dismiss/libwaylandgrab.so")
    LIBGRAB = ctypes.CDLL(_grab_lib_path)
    CB_TYPE = ctypes.CFUNCTYPE(None)
except OSError:
    LOG.warning(f"Failed to load Wayland Grab Library at {_grab_lib_path}. Outside click dismissal will not function.")
    LIBGRAB = None

# ==============================================================================
# IDLE RAM RECLAMATION
# ==============================================================================
_LIBC: Final = ctypes.CDLL("libc.so.6", use_errno=True)
_MADV_PAGEOUT: Final = 21

def _reclaim_idle_memory() -> None:
    """GC + freeze + malloc_trim + pageout idle data pages to zram."""
    re.purge()
    
    # Modern Python 3.14+ cache clearance mechanism
    if hasattr(sys, "_clear_internal_caches"):
        sys._clear_internal_caches()
    elif hasattr(sys, "_clear_type_cache"):
        sys._clear_type_cache()
        
    gc.collect()
    gc.collect()
    gc.freeze()
    try:
        _LIBC.malloc_trim(0)
    except Exception:
        pass
    _pageout_idle_pages()

def _pageout_idle_pages() -> None:
    """Use madvise(MADV_PAGEOUT) on all non-executable mappings to shrink VmRSS."""
    try:
        with open("/proc/self/maps", "r") as f:
            for line in f:
                parts = line.split(None, 5)
                if len(parts) < 2:
                    continue
                perms = parts[1]
                if "r" not in perms or "x" in perms or "p" not in perms:
                    continue
                path = parts[5].strip() if len(parts) > 5 else ""
                if path in ("[vdso]", "[vvar]", "[vsyscall]") or path.startswith("[stack"):
                    continue
                
                start_s, end_s = parts[0].split("-")
                start = int(start_s, 16)
                length = int(end_s, 16) - start
                if length > 0:
                    _LIBC.madvise(
                        ctypes.c_void_p(start),
                        ctypes.c_size_t(length),
                        _MADV_PAGEOUT,
                    )
    except Exception:
        pass


# ==============================================================================
# UTILITIES & STATE MANAGEMENT
# ==============================================================================

def clamp(value: float, lower: float, upper: float) -> float:
    if not math.isfinite(value):
        return lower
    return max(lower, min(upper, value))

def parse_float(text: str) -> float | None:
    try:
        value = float(text.strip())
    except ValueError:
        return None
    return value if math.isfinite(value) else None

def percent_int(value: float, lower: int = 0) -> int:
    return int(clamp(round(value), float(lower), 100.0))

def snap_to_step(value: float, lower: float, upper: float, step: float) -> float:
    if step <= 0.0:
        return clamp(value, lower, upper)

    scaled = (value - lower) / step
    snapped = lower + math.floor(scaled + 0.5 + 1e-12) * step
    return round(clamp(snapped, lower, upper), 10)

def kelvin_value(value: float) -> int:
    return int(clamp(round(value), 1000.0, 6000.0))

def start_thread(
    name: str,
    target: Callable[..., None],
    *args: object,
    daemon: bool = True,
) -> threading.Thread:
    thread = threading.Thread(
        name=name,
        target=target,
        args=args,
        daemon=daemon,
        context=contextvars.Context(),
    )
    thread.start()
    return thread

def run_command(
    args: Sequence[CommandArg],
    *,
    timeout: float,
    capture_stdout: bool = False,
) -> subprocess.CompletedProcess[str] | None:
    """Safely execute commands as process groups to ensure timeouts kill all children."""
    argv = [os.fspath(arg) for arg in args]
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=COMMAND_ENV,
            close_fds=True,
            start_new_session=True, # Critical to prevent zombie shell leaks
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        LOG.debug("Command failed to start: %r: %s", argv, exc)
        return None

    try:
        stdout, _ = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, None)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.communicate() # Reap
        return None
    except Exception as exc:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.communicate()
        return None

def execute_cmd(cmd: str) -> None:
    """Executes a command independently. Uncoupled from the panel's process tree."""
    try:
        subprocess.Popen(
            ["/usr/bin/bash", "-c", cmd],
            start_new_session=True,
            env=COMMAND_ENV,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as e:
        LOG.warning(f"Failed to execute command '{cmd}': {e}")

def fetch_json_output(cmd: str) -> dict[str, Any] | None:
    """Fetches JSON safely leveraging the improved run_command for memory safety."""
    args = shlex.split(cmd)
    r = run_command(args, timeout=1.5, capture_stdout=True)
    if r is not None and r.returncode == 0 and r.stdout.strip():
        try:
            return json.loads(r.stdout.strip())
        except json.JSONDecodeError:
            pass
    return None

def _resolve_state_dir() -> Path | None:
    candidates: list[Path] = []
    seen: set[str] = set()

    if (xdg_state_home := os.environ.get("XDG_STATE_HOME")):
        path = Path(xdg_state_home)
        if path.is_absolute():
            candidates.append(path / APP_ID)

    try:
        candidates.append(Path.home() / ".local" / "state" / APP_ID)
    except (OSError, RuntimeError):
        pass

    if (xdg_runtime_dir := os.environ.get("XDG_RUNTIME_DIR")):
        path = Path(xdg_runtime_dir)
        if path.is_absolute():
            candidates.append(path / APP_ID)

    candidates.append(Path(f"/run/user/{os.getuid()}") / APP_ID)
    candidates.append(Path(tempfile.gettempdir()) / f"{APP_ID}-{os.getuid()}")

    for path in candidates:
        key = os.fspath(path)
        if key in seen:
            continue
        seen.add(key)

        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError:
            pass

        if path.is_dir() and os.access(path, os.W_OK | os.X_OK):
            return path

    return None

STATE_DIR: Final = _resolve_state_dir()
if STATE_DIR is None:
    LOG.warning("Could not resolve a writable state directory. Settings will not persist.")

STATE_FILE: Final = None if STATE_DIR is None else STATE_DIR / "hyprsunset_state.txt"
DDCUTIL_CACHE_FILE: Final = None if STATE_DIR is None else STATE_DIR / "ddcutil_displays.json"

def fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return

    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)

def atomic_write_text(path: Path, text: str, *, durable: bool = True) -> bool:
    temp_path: Path | None = None

    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

        fd, raw_temp_path = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            text=True,
        )
        temp_path = Path(raw_temp_path)

        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            if durable:
                os.fsync(handle.fileno())

        os.replace(temp_path, path)
        if durable:
            fsync_directory(path.parent)
        temp_path = None
        return True
    except OSError as exc:
        LOG.warning("Failed to write %s: %s", path, exc)
        return False
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass


# ==============================================================================
# WORKERS & THREAD POOLING
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
                self._executor = ThreadPoolExecutor(
                    max_workers=self._max_workers,
                    thread_name_prefix="dusky-refresh",
                )
            try:
                return self._executor.submit(func, *args, **kwargs)
            except RuntimeError:
                return None

    def shutdown(self) -> None:
        with self._lock:
            if self._executor is not None:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None

class LatestValueWorker:
    __slots__ = (
        "_apply_func",
        "_busy",
        "_condition",
        "_name",
        "_pending",
        "_running",
        "_thread",
    )

    def __init__(self, name: str, apply_func: Callable[[float], None]) -> None:
        self._name = name
        self._apply_func = apply_func
        self._condition = threading.Condition()
        self._pending: float | object = NO_PENDING
        self._busy = False
        self._running = True
        self._thread: threading.Thread | None = None

        with self._condition:
            self._ensure_thread_locked()

    def submit(self, value: float) -> None:
        with self._condition:
            if not self._running:
                return
            self._pending = float(value)
            self._ensure_thread_locked()
            self._condition.notify()

    def flush(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout

        with self._condition:
            if self._pending is not NO_PENDING:
                self._ensure_thread_locked()

            while self._running and (self._busy or self._pending is not NO_PENDING):
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0.0:
                    return False
                self._condition.wait(remaining)

        return True

    def start(self) -> None:
        with self._condition:
            if self._running:
                return
            self._running = True
            self._ensure_thread_locked()

    def stop(self, timeout: float = 2.0) -> None:
        self.flush(timeout)

        with self._condition:
            self._running = False
            self._pending = NO_PENDING
            self._condition.notify_all()
            thread = self._thread

        if thread is None:
            return

        try:
            thread.join(timeout=timeout)
        except Exception as exc:
            LOG.debug("%s worker join failed during shutdown: %s", self._name, exc)
            return

        if thread.is_alive():
            LOG.warning("%s worker did not stop within %.1fs", self._name, timeout)

    def _ensure_thread_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = start_thread(f"{self._name}-worker", self._worker, daemon=True)

    def _worker(self) -> None:
        while True:
            with self._condition:
                while self._running and self._pending is NO_PENDING:
                    self._condition.wait()

                if not self._running:
                    return

                value = self._pending
                self._pending = NO_PENDING
                self._busy = True

            try:
                if value is not NO_PENDING:
                    self._apply_func(float(value))
            except Exception:
                LOG.exception("Unhandled exception in %s worker", self._name)
            finally:
                with self._condition:
                    self._busy = False
                    self._condition.notify_all()

class DebouncedValueWriter:
    __slots__ = (
        "_busy",
        "_condition",
        "_deadline",
        "_delay_seconds",
        "_latest",
        "_name",
        "_pending",
        "_running",
        "_thread",
        "_write_func",
    )

    def __init__(
        self,
        name: str,
        write_func: Callable[[float], None],
        *,
        delay_seconds: float,
    ) -> None:
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

        with self._condition:
            self._ensure_thread_locked()

    def schedule(self, value: float) -> None:
        with self._condition:
            if not self._running:
                return
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
                if remaining is not None and remaining <= 0.0:
                    return False
                self._condition.wait(remaining)

        return True

    def start(self) -> None:
        with self._condition:
            if self._running:
                return
            self._running = True
            self._ensure_thread_locked()

    def stop(self, timeout: float = 2.0) -> None:
        self.flush(timeout)

        with self._condition:
            self._running = False
            self._condition.notify_all()
            thread = self._thread

        if thread is None:
            return

        try:
            thread.join(timeout=timeout)
        except Exception as exc:
            LOG.debug("%s writer join failed during shutdown: %s", self._name, exc)
            return

        if thread.is_alive():
            LOG.warning("%s writer did not stop within %.1fs", self._name, timeout)

    def _ensure_thread_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = start_thread(f"{self._name}-writer", self._worker, daemon=True)

    def _worker(self) -> None:
        while True:
            with self._condition:
                while True:
                    if not self._running and not self._pending:
                        return

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

            try:
                self._write_func(value)
            except Exception:
                LOG.exception("Unhandled exception in %s writer", self._name)
            finally:
                with self._condition:
                    self._busy = False
                    self._condition.notify_all()


# ==============================================================================
# HARDWARE CONTROLLERS (BRIGHTNESS, VOLUME, SUNSET)
# ==============================================================================

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

_BACKLIGHT_DISCOVERY_TTL_SECONDS: Final = 5.0
_backlight_discovery_lock: Final = threading.Lock()
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

            candidates.append(
                BacklightDevice(
                    priority=_backlight_priority(entry.name),
                    maximum=maximum,
                    path=entry,
                )
            )

    candidates.sort(key=lambda device: (device.priority, device.maximum), reverse=True)
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

    read_path = (
        device.actual_brightness_path
        if device.actual_brightness_path.is_file()
        else device.brightness_path
    )

    try:
        current = parse_float(read_path.read_text(encoding="utf-8"))
        maximum = parse_float(device.max_brightness_path.read_text(encoding="utf-8"))
    except OSError:
        return None

    if current is None or maximum is None or maximum <= 0.0:
        return None

    value = clamp((current / maximum) * 100.0, 0.0, 100.0)
    LOG.debug("Brightness read via sysfs %s/%s: %.3f%%", device.path.name, read_path.name, value)
    return value

def _read_brightnessctl() -> float | None:
    if (base_cmd := _brightnessctl_command_base()) is None:
        return None

    result = run_command(
        [*base_cmd, "--machine-readable"],
        timeout=QUERY_TIMEOUT,
        capture_stdout=True,
    )
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

    value = clamp(value, 0.0, 100.0)
    LOG.debug("Brightness read via brightnessctl: %.3f%%", value)
    return value

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
    raw_value = max(1, min(maximum, int(round((brightness / 100.0) * maximum))))

    try:
        device.brightness_path.write_text(f"{raw_value}\n", encoding="utf-8")
    except OSError:
        return False

    return True

def apply_local_brightness(value: float) -> None:
    brightness = percent_int(value, lower=1)

    if _write_sysfs_brightness(brightness):
        return

    if (base_cmd := _brightnessctl_command_base()) is None:
        LOG.debug("Local brightness apply skipped: no writable sysfs and no brightnessctl.")
        return

    result = run_command(
        [*base_cmd, "--quiet", "set", f"{brightness}%"],
        timeout=CONTROL_TIMEOUT,
    )
    if result is None or result.returncode != 0:
        LOG.debug("brightnessctl failed to set brightness to %s%%", brightness)

@dataclass(slots=True)
class DdcDisplay:
    bus: int
    max_value: int = 100
    last_percent: float | None = None

_DDC_LOCK = threading.Lock()

class DdcManager:
    __slots__ = (
        "_cache_file",
        "_detect_thread",
        "_displays",
        "_last_requested",
        "_lock",
        "_started",
        "_workers",
        "_last_rescan_time",
    )

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
        if DDCUTIL is None:
            return

        with self._lock:
            if self._started:
                return
            self._started = True
            self._load_cache_locked()

        self.request_rescan()

    def request_rescan(self) -> None:
        if DDCUTIL is None:
            return

        with self._lock:
            now = time.monotonic()
            if now - self._last_rescan_time < 60.0:
                return
            self._last_rescan_time = now

            thread = self._detect_thread
            if thread is not None and thread.is_alive():
                return
            self._detect_thread = start_thread("ddcutil-detect", self._detect_worker, daemon=True)

    def submit(self, value: float) -> None:
        if DDCUTIL is None:
            return

        percent = float(percent_int(value, lower=1))
        with self._lock:
            self._last_requested = percent
            workers = tuple(self._workers.values())

        for worker in workers:
            worker.submit(percent)

    def current_percent(self) -> float | None:
        with self._lock:
            has_displays = bool(self._displays)
            last_requested = self._last_requested

            if not has_displays:
                should_rescan = self._started
            else:
                should_rescan = False

            if not has_displays:
                result = None
            elif last_requested is not None:
                result = last_requested
            else:
                result = NO_PENDING

        if should_rescan:
            self.request_rescan()

        if result is None:
            return None

        if result is not NO_PENDING:
            return float(result)

        with self._lock:
            if not self._displays:
                return None

            for bus in sorted(self._displays):
                if (value := self._displays[bus].last_percent) is not None:
                    return value

            return 50.0

    def has_displays(self) -> bool:
        with self._lock:
            return bool(self._displays)

    def stop(self, timeout: float = 1.5) -> None:
        with self._lock:
            self._started = False
            workers = tuple(self._workers.values())
            self._workers.clear()

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

        atomic_write_text(
            self._cache_file,
            json.dumps(records, separators=(",", ":")) + "\n",
            durable=False,
        )

    def _ensure_display_locked(
        self,
        bus: int,
        max_value: int,
        last_percent: float | None,
    ) -> None:
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
                lambda value, target_bus=bus: self._apply_bus(target_bus, value),
            )

    def _detect_worker(self) -> None:
        try:
            self._detect_worker_impl()
        except Exception:
            LOG.exception("Unhandled exception in ddcutil detection worker")

    def _detect_worker_impl(self) -> None:
        if DDCUTIL is None:
            return

        with _DDC_LOCK:
            result = run_command(
                [DDCUTIL, "detect", "--terse"],
                timeout=DDC_DETECT_TIMEOUT,
                capture_stdout=True,
            )
        
        if result is None or result.returncode != 0:
            return

        buses = self._parse_detect_buses(result.stdout)
        discovered: dict[int, DdcDisplay] = {}

        for bus in buses:
            display = self._query_display(bus)
            if display is not None:
                discovered[bus] = display

        removed_workers: list[LatestValueWorker] = []

        with self._lock:
            if not self._started:
                return

            old_buses = set(self._displays)
            new_buses = set(discovered)

            for bus in old_buses - new_buses:
                self._displays.pop(bus, None)
                if (worker := self._workers.pop(bus, None)) is not None:
                    removed_workers.append(worker)

            for bus, display in discovered.items():
                self._ensure_display_locked(bus, display.max_value, display.last_percent)

            last_requested = self._last_requested
            workers = tuple(self._workers.values())

        for worker in removed_workers:
            worker.stop(0.25)

        if last_requested is not None:
            for worker in workers:
                worker.submit(last_requested)

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

        with _DDC_LOCK:
            result = run_command(
                [DDCUTIL, "getvcp", "10", "--terse", "--bus", str(bus)],
                timeout=DDC_QUERY_TIMEOUT,
                capture_stdout=True,
            )
            
        if result is None or result.returncode != 0:
            return None

        parsed = self._parse_getvcp_brightness(result.stdout)
        if parsed is None:
            return None

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
                except ValueError:
                    return None
                if maximum > 0:
                    return current, maximum
        return None

    def _apply_bus(self, bus: int, value: float) -> None:
        if DDCUTIL is None:
            return

        percent = float(percent_int(value, lower=1))
        with self._lock:
            display = self._displays.get(bus)
            max_value = 100 if display is None else max(1, display.max_value)

        raw_value = max(1, min(max_value, int(round((percent / 100.0) * max_value))))

        with _DDC_LOCK:
            result = run_command(
                [DDCUTIL, "setvcp", "10", str(raw_value), "--bus", str(bus)],
                timeout=DDC_SET_TIMEOUT,
            )
            
        if result is None or result.returncode != 0:
            LOG.debug("ddcutil failed to set bus %s brightness to %.0f%%", bus, percent)
            return

        with self._lock:
            if (display := self._displays.get(bus)) is not None:
                display.last_percent = percent


DDC_MANAGER: Final = DdcManager(DDCUTIL_CACHE_FILE) if DDCUTIL is not None else None

HAS_VOLUME: Final = WPCTL is not None
HAS_LOCAL_BRIGHTNESS: Final = (
    _preferred_sysfs_backlight() is not None
    and (BRIGHTNESSCTL is not None or _has_writable_sysfs_backlight())
)
HAS_DDC_BRIGHTNESS: Final = DDCUTIL is not None
HAS_BRIGHTNESS: Final = HAS_LOCAL_BRIGHTNESS or HAS_DDC_BRIGHTNESS
HAS_SUNSET: Final = (
    HYPRCTL is not None
    and HYPRSUNSET is not None
    and bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))
)

def get_volume() -> float | None:
    if WPCTL is None:
        return None

    result = run_command(
        [WPCTL, "get-volume", "@DEFAULT_AUDIO_SINK@"],
        timeout=QUERY_TIMEOUT,
        capture_stdout=True,
    )
    if result is None or result.returncode != 0:
        return None

    parts = result.stdout.split()
    if len(parts) < 2:
        return None

    value = parse_float(parts[1])
    if value is None:
        return None

    return clamp(value * 100.0, 0.0, 100.0)

def apply_volume(value: float) -> None:
    if WPCTL is None:
        return

    volume = percent_int(value)

    result = run_command(
        [WPCTL, "set-volume", "@DEFAULT_AUDIO_SINK@", f"{volume}%"],
        timeout=CONTROL_TIMEOUT,
    )
    if result is None or result.returncode != 0:
        LOG.warning("Failed to set volume to %s%%", volume)
        return

    if volume <= 0:
        return

    result = run_command(
        [WPCTL, "set-mute", "@DEFAULT_AUDIO_SINK@", "0"],
        timeout=CONTROL_TIMEOUT,
    )
    if result is None or result.returncode != 0:
        LOG.warning("Failed to unmute audio sink after setting volume")

def get_brightness() -> float | None:
    if (value := _read_sysfs_brightness()) is not None:
        return value

    if (value := _read_brightnessctl()) is not None:
        return value

    if DDC_MANAGER is None:
        return None

    return DDC_MANAGER.current_percent()

def get_hyprsunset_state() -> float:
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
        "_fallback_process",
        "_process_lock",
        "_ready",
        "_state_writer",
        "_worker",
    )

    def __init__(self) -> None:
        self._state_writer = DebouncedValueWriter(
            "sunset-state",
            write_hyprsunset_state,
            delay_seconds=SUNSET_STATE_WRITE_DEBOUNCE_SECONDS,
        )
        self._worker = LatestValueWorker("sunset", self._apply)
        self._ready = threading.Event()
        self._process_lock = threading.Lock()
        self._fallback_process: subprocess.Popen[str] | None = None

    def submit(self, value: float) -> None:
        self._worker.submit(float(kelvin_value(value)))

    def start(self) -> None:
        self._worker.start()
        self._state_writer.start()

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

        if self._wait_until_applied(target, SUNSET_READY_TIMEOUT):
            return

        self._spawn_fallback_process(target)
        if self._wait_until_applied(target, SUNSET_FALLBACK_READY_TIMEOUT):
            return

        LOG.warning("Failed to apply hyprsunset temperature: %s", target)

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
        if HYPRCTL is None:
            return False

        result = run_command(
            [HYPRCTL, "hyprsunset", "temperature", str(target)],
            timeout=QUERY_TIMEOUT,
        )
        return result is not None and result.returncode == 0

    def _start_backend(self, target: int) -> None:
        if SYSTEMCTL is not None:
            result = run_command(
                [SYSTEMCTL, "--user", "start", "hyprsunset.service"],
                timeout=CONTROL_TIMEOUT,
            )
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

        result = run_command(
            [PGREP, "-u", str(os.getuid()), "-x", "hyprsunset"],
            timeout=QUERY_TIMEOUT,
        )
        return result is not None and result.returncode == 0

    def _spawn_fallback_process(self, target: int) -> None:
        if HYPRSUNSET is None:
            return

        with self._process_lock:
            proc = self._fallback_process
            if proc is not None:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                self._fallback_process = None

            try:
                new_proc = subprocess.Popen(
                    [HYPRSUNSET, "--temperature", str(target)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                    env=COMMAND_ENV,
                    text=True,
                )
            except OSError as exc:
                LOG.warning("Failed to start hyprsunset fallback process: %s", exc)
                return

            self._fallback_process = new_proc

        start_thread("hyprsunset-reaper", self._reap_fallback_process, new_proc, daemon=True)

    def _reap_fallback_process(self, proc: subprocess.Popen[str]) -> None:
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


# ==============================================================================
# GTK3 UI HELPERS
# ==============================================================================

def _add_css_class(widget: Gtk.Widget, cls: str) -> None:
    widget.get_style_context().add_class(cls)

def _remove_css_class(widget: Gtk.Widget, cls: str) -> None:
    widget.get_style_context().remove_class(cls)


# ==============================================================================
# GTK3 UI (SLIDERS COMPONENTS)
# ==============================================================================

class CompactSliderRow(Gtk.Box):
    def __init__(
        self,
        icon_text: str,
        css_class: str,
        min_value: float,
        max_value: float,
        step: float,
        fetch_cb: FloatGetter,
        submit_cb: FloatSubmitter,
        refresh_pool: RefreshPool,
        *,
        post_submit_refresh_grace_seconds: float = 0.0,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)

        self._fetch_cb = fetch_cb
        self._submit_cb = submit_cb
        self._refresh_pool = refresh_pool
        self._refresh_future: Future[float | None] | None = None
        self._refresh_token = 0
        self._user_revision = 0
        self._suppress_apply = False
        self._has_value = False
        self._post_submit_refresh_grace_seconds = max(0.0, post_submit_refresh_grace_seconds)
        self._pending_local_value: float | None = None
        self._pending_local_deadline = 0.0

        _add_css_class(self, "slider-row")

        self.icon = Gtk.Label(label=icon_text)
        _add_css_class(self.icon, "icon-label")
        _add_css_class(self.icon, f"icon-{css_class}")
        self.pack_start(self.icon, False, False, 0)

        self.adjustment = Gtk.Adjustment(
            value=min_value,
            lower=min_value,
            upper=max_value,
            step_increment=step,
            page_increment=step * 10.0,
        )

        self.scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            adjustment=self.adjustment,
        )
        self.scale.set_hexpand(True)
        self.scale.set_draw_value(False)
        self.scale.set_digits(0)
        self.scale.set_sensitive(False)
        _add_css_class(self.scale, "pill-scale")
        _add_css_class(self.scale, css_class)
        self.scale.connect("value-changed", self._on_value_changed)
        self.pack_start(self.scale, True, True, 0)

        self.value_label = Gtk.Label(label="…")
        self.value_label.set_width_chars(4)
        self.value_label.set_xalign(1.0)
        _add_css_class(self.value_label, "value-label")
        self.pack_start(self.value_label, False, False, 0)

        self.show_all()

    def refresh_async(self) -> None:
        if (
            self._pending_local_value is not None
            and time.monotonic() < self._pending_local_deadline
        ):
            return

        if self._refresh_future is not None and not self._refresh_future.done():
            return

        self._refresh_token += 1
        token = self._refresh_token
        user_revision = self._user_revision

        future = self._refresh_pool.submit(self._fetch_cb)
        if future is None:
            return

        self._refresh_future = future
        future.add_done_callback(
            lambda done_future: self._refresh_done(done_future, token, user_revision)
        )

    def _refresh_done(
        self,
        future: Future[float | None],
        token: int,
        user_revision: int,
    ) -> None:
        try:
            value = future.result()
        except CancelledError:
            return
        except Exception:
            LOG.exception("Unhandled exception while refreshing slider value")
            value = None

        GLib.idle_add(self._apply_refresh_result, token, user_revision, value)

    def _apply_refresh_result(
        self,
        token: int,
        user_revision: int,
        value: float | None,
    ) -> bool:
        if token == self._refresh_token:
            self._refresh_future = None

        if token != self._refresh_token or user_revision != self._user_revision:
            return GLib.SOURCE_REMOVE

        if value is None:
            if not self._has_value:
                self.scale.set_sensitive(False)
                self.value_label.set_label("…")
            self._clear_pending_local()
            return GLib.SOURCE_REMOVE

        clamped = snap_to_step(
            value,
            self.adjustment.get_lower(),
            self.adjustment.get_upper(),
            self.adjustment.get_step_increment(),
        )

        if self._pending_local_value is not None:
            tolerance = self._pending_local_tolerance()
            now = time.monotonic()

            if math.isclose(
                clamped,
                self._pending_local_value,
                rel_tol=0.0,
                abs_tol=tolerance,
            ):
                self._clear_pending_local()
            elif now < self._pending_local_deadline:
                return GLib.SOURCE_REMOVE
            else:
                self._clear_pending_local()

        self._suppress_apply = True
        try:
            self.adjustment.set_value(clamped)
            self.value_label.set_label(str(int(round(clamped))))
            self.scale.set_sensitive(True)
            self._has_value = True
        finally:
            self._suppress_apply = False

        return GLib.SOURCE_REMOVE

    def _pending_local_tolerance(self) -> float:
        return max(self.adjustment.get_step_increment() * 0.5, 1e-9)

    def _clear_pending_local(self) -> None:
        self._pending_local_value = None
        self._pending_local_deadline = 0.0

    def _on_value_changed(self, scale: Gtk.Scale) -> None:
        value = scale.get_value()
        snapped = snap_to_step(
            value,
            self.adjustment.get_lower(),
            self.adjustment.get_upper(),
            self.adjustment.get_step_increment(),
        )

        if not math.isclose(snapped, value, rel_tol=0.0, abs_tol=1e-9):
            self._suppress_apply = True
            try:
                self.adjustment.set_value(snapped)
            finally:
                self._suppress_apply = False

        self.value_label.set_label(str(int(round(snapped))))

        if self._suppress_apply:
            return

        if self._post_submit_refresh_grace_seconds > 0.0:
            self._pending_local_value = snapped
            self._pending_local_deadline = (
                time.monotonic() + self._post_submit_refresh_grace_seconds
            )
        else:
            self._clear_pending_local()

        self._user_revision += 1
        self._submit_cb(snapped)


# ==============================================================================
# MPRIS MEDIA STATE & LOGIC
# ==============================================================================

@dataclass
class MediaState:
    players: list[str]
    status: str | None
    title: str
    artist: str
    position: float
    length: float
    shuffle: bool
    loop: str

def fetch_media_state(player: str | None = None) -> MediaState | None:
    if PLAYERCTL is None: return None
    
    r_players = run_command([PLAYERCTL, "-l"], timeout=0.8, capture_stdout=True)
    current_players = []
    if r_players and r_players.returncode == 0 and r_players.stdout:
        current_players = [p.strip() for p in r_players.stdout.splitlines() if p.strip()]

    if not current_players: return None

    fmt = "{{playerName}}\x1f{{status}}\x1f{{title}}\x1f{{artist}}\x1f{{position}}\x1f{{mpris:length}}\x1f{{shuffle}}\x1f{{loop}}"
    args = [PLAYERCTL, "metadata", "--format", fmt]
    
    if player and player != "auto":
        args = [PLAYERCTL, "-p", player, "metadata", "--format", fmt]

    r_stat = run_command(args, timeout=1.5, capture_stdout=True)
    
    if not r_stat or r_stat.returncode != 0 or not r_stat.stdout.strip():
        fallback_args = [PLAYERCTL, "status"]
        if player and player != "auto":
            fallback_args = [PLAYERCTL, "-p", player, "status"]
            
        r_fallback = run_command(fallback_args, timeout=0.8, capture_stdout=True)
        if not r_fallback or r_fallback.returncode != 0 or r_fallback.stdout.strip() not in ("Playing", "Paused"):
            return None
            
        p_name = player if player and player != "auto" else current_players[0]
        return MediaState(current_players, r_fallback.stdout.strip(), "Unknown", "", -1.0, -1.0, False, "None")

    parts = r_stat.stdout.strip().split("\x1f")
    if len(parts) < 8:
        return None

    p_name = parts[0]
    status = parts[1]
    
    if status not in ("Playing", "Paused"):
        return None

    title = parts[2] or "Unknown"
    artist = parts[3]
    
    try: pos = float(parts[4]) / 1000000.0 if parts[4] else -1.0
    except ValueError: pos = -1.0
    
    try: length = float(parts[5]) / 1000000.0 if parts[5] else -1.0
    except ValueError: length = -1.0
    
    shuffle = parts[6].lower() in ("on", "true")
    loop = parts[7] or "None"

    return MediaState(current_players, status, title, artist, pos, length, shuffle, loop)

def _format_time(secs: float) -> str:
    s = int(max(0.0, secs))
    return f"{s // 60}:{s % 60:02d}"

# ==============================================================================
# GTK3 WIDGET ARCHITECTURE (CORE PANEL)
# ==============================================================================

class QuickIconToggle(Gtk.Overlay):
    def __init__(self, icon_name: str, tooltip: str, on_left: str = "", on_middle: str = "", on_right: str = ""):
        super().__init__()
        self.btn_box = Gtk.Button()
        self.btn_box.set_relief(Gtk.ReliefStyle.NONE)
        _add_css_class(self.btn_box, "quick-icon-toggle")
        self.btn_box.set_tooltip_text(tooltip)

        self._icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.LARGE_TOOLBAR)
        self._icon.set_pixel_size(24)
        self._icon.set_halign(Gtk.Align.CENTER)
        self._icon.set_valign(Gtk.Align.CENTER)
        self.btn_box.add(self._icon)

        self.add(self.btn_box)

        self.badge_lbl = Gtk.Label()
        _add_css_class(self.badge_lbl, "notification-badge")
        self.badge_lbl.set_halign(Gtk.Align.END)
        self.badge_lbl.set_valign(Gtk.Align.START)
        self.badge_lbl.set_xalign(0.5)
        self.badge_lbl.set_yalign(0.5)
        self.badge_lbl.set_visible(False)
        self.badge_lbl.set_no_show_all(True)
        self.add_overlay(self.badge_lbl)

        self.btn_box.connect("button-press-event", self._on_clicked)

        self.cmds = {1: on_left, 2: on_middle, 3: on_right}
        self.show_all()
        self.badge_lbl.hide()

    def _on_clicked(self, widget, event):
        if cmd := self.cmds.get(event.button):
            execute_cmd(cmd)
        return True

    def update_state(self, icon: str | None = None, css_class: str | None = None, tooltip: str | None = None, badge: str = ""):
        if icon:
            self._icon.set_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
            self._icon.set_pixel_size(24)
        if tooltip: self.btn_box.set_tooltip_text(tooltip)
        if css_class:
            for cls in ["normal", "active", "dnd-active", "power-saver-active"]:
                _remove_css_class(self.btn_box, cls)
            _add_css_class(self.btn_box, css_class)
        if badge and badge.strip() and badge != "0":
            self.badge_lbl.set_label(badge)
            self.badge_lbl.show()
        else:
            self.badge_lbl.hide()


class MetricPill(Gtk.EventBox):
    def __init__(self, icon: str | None, tooltip: str, on_click: str = "", small_text: bool = False):
        super().__init__()
        self.set_tooltip_text(tooltip)
        self.set_hexpand(True)
        self.set_visible_window(False)

        if on_click:
            _add_css_class(self, "clickable-pill")
            self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            self.connect("button-press-event", lambda *args: (execute_cmd(on_click), True)[1])

        self._box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        _add_css_class(self._box, "metric-pill")
        self._box.set_hexpand(True)

        self._inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._inner.set_halign(Gtk.Align.CENTER)
        self._inner.set_hexpand(True)

        if icon:
            self._icon = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU)
            self._icon.set_pixel_size(16)
            self._inner.pack_start(self._icon, False, False, 0)

        self._val_lbl = Gtk.Label(label="--")
        _add_css_class(self._val_lbl, "metric-value-small" if small_text else "metric-value")
        
        self._val_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self._val_lbl.set_max_width_chars(10)
        self._val_lbl.set_width_chars(1)

        self._inner.pack_start(self._val_lbl, False, False, 0)
        self._box.pack_start(self._inner, True, True, 0)
        self.add(self._box)
        self.show_all()

    def set_value(self, text: str):
        self._val_lbl.set_label(text)

    def apply_json(self, data: dict[str, Any] | None, hide_class: str = "empty"):
        if not data or data.get("class", "") == hide_class:
            self._val_lbl.set_label("--")
        else:
            text = str(data.get("text", "")).replace("\\n", " ").replace("\n", " ").strip()
            self._val_lbl.set_markup(text)


class MediaCard(Gtk.Box):
    def __init__(self, pool: RefreshPool):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._pool = pool
        self._refresh_future: Future | None = None
        self._refresh_token = 0
        self._suppress_seek = False
        self._pending_seek_deadline = 0.0
        self._cache_players: list[str] = []

        _add_css_class(self, "media-card")
        self.set_no_show_all(True)
        self.hide()

        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        meta_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        meta_box.set_hexpand(True)
        
        self.title_lbl = Gtk.Label(label=" ")
        self.title_lbl.set_halign(Gtk.Align.START)
        
        self.title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_lbl.set_max_width_chars(25)
        self.title_lbl.set_width_chars(1)
        _add_css_class(self.title_lbl, "media-title")
        
        self.artist_lbl = Gtk.Label(label=" ")
        self.artist_lbl.set_halign(Gtk.Align.START)
        
        self.artist_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.artist_lbl.set_max_width_chars(25)
        self.artist_lbl.set_width_chars(1)
        _add_css_class(self.artist_lbl, "media-artist")
        
        meta_box.pack_start(self.title_lbl, False, False, 0)
        meta_box.pack_start(self.artist_lbl, False, False, 0)
        header_box.pack_start(meta_box, True, True, 0)

        self.audio_btn = Gtk.Button()
        self.audio_btn.set_image(Gtk.Image.new_from_icon_name("audio-speakers-symbolic", Gtk.IconSize.BUTTON))
        _add_css_class(self.audio_btn, "flat")
        self.audio_btn.set_valign(Gtk.Align.CENTER)
        self.audio_btn.set_tooltip_text("Switch Audio Output")
        self.audio_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.audio_btn.connect("clicked", lambda _: execute_cmd(f"uwsm app -- {HOME}/user_scripts/audio/audio_switch.sh"))
        header_box.pack_start(self.audio_btn, False, False, 0)

        self.player_btn = Gtk.Button(label="Auto")
        _add_css_class(self.player_btn, "flat")
        self.player_btn.set_valign(Gtk.Align.CENTER)
        self.player_btn.set_tooltip_text("Click to cycle active media players")
        self.player_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.player_btn.connect("clicked", self._on_player_cycle)
        header_box.pack_start(self.player_btn, False, False, 0)
        self.pack_start(header_box, False, False, 0)
        self._current_player_idx = 0

        prog_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.elapsed_lbl = Gtk.Label(label="0:00")
        _add_css_class(self.elapsed_lbl, "media-time")
        self.elapsed_lbl.set_width_chars(5)
        self.seek_adj = Gtk.Adjustment(value=0, lower=0, upper=1, step_increment=1)
        self.seek_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.seek_adj)
        self.seek_scale.set_hexpand(True)
        self.seek_scale.set_draw_value(False)
        _add_css_class(self.seek_scale, "pill-scale")
        _add_css_class(self.seek_scale, "media-scale")
        self.seek_scale.connect("value-changed", self._on_seek)
        self.dur_lbl = Gtk.Label(label="0:00")
        _add_css_class(self.dur_lbl, "media-time")
        self.dur_lbl.set_width_chars(5)
        prog_box.pack_start(self.elapsed_lbl, False, False, 0)
        prog_box.pack_start(self.seek_scale, True, True, 0)
        prog_box.pack_start(self.dur_lbl, False, False, 0)
        self.pack_start(prog_box, False, False, 0)

        ctrl_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        ctrl_box.set_halign(Gtk.Align.CENTER)

        self.shuf_btn = self._btn("media-playlist-shuffle-symbolic", lambda _: self._cmd(["shuffle", "toggle"]))
        self.prev_btn = self._btn("media-skip-backward-symbolic", lambda _: self._cmd(["previous"]))

        self.play_btn = Gtk.Button()
        self.play_btn.set_image(Gtk.Image.new_from_icon_name("media-playback-start-symbolic", Gtk.IconSize.BUTTON))
        _add_css_class(self.play_btn, "flat")
        _add_css_class(self.play_btn, "media-play-btn")
        self.play_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.play_btn.connect("clicked", lambda _: self._cmd(["play-pause"]))

        self.next_btn = self._btn("media-skip-forward-symbolic", lambda _: self._cmd(["next"]))
        self.loop_btn = self._btn("media-playlist-repeat-symbolic", lambda _: self._cmd(["loop", {'None': 'Playlist', 'Playlist': 'Track', 'Track': 'None'}.get(self._loop_state, 'None')]))

        for b in (self.shuf_btn, self.prev_btn, self.play_btn, self.next_btn, self.loop_btn):
            ctrl_box.pack_start(b, False, False, 0)
        self.pack_start(ctrl_box, False, False, 0)
        self._loop_state = "None"

        header_box.show_all()
        prog_box.show_all()
        ctrl_box.show_all()

    def _btn(self, icon: str, cb: Callable) -> Gtk.Button:
        b = Gtk.Button()
        b.set_image(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON))
        _add_css_class(b, "flat")
        _add_css_class(b, "media-btn")
        b.set_relief(Gtk.ReliefStyle.NONE)
        b.connect("clicked", cb)
        return b

    def _on_player_cycle(self, _):
        if not self._cache_players: return
        self._current_player_idx = (self._current_player_idx + 1) % (len(self._cache_players) + 1)
        self.player_btn.set_label("Auto" if self._current_player_idx == 0 else self._cache_players[self._current_player_idx - 1].capitalize())

        self._refresh_token += 1
        self._refresh_future = None
        self.refresh_async()

    def _get_player(self) -> str | None:
        if self._current_player_idx == 0 or not self._cache_players: return None
        if self._current_player_idx <= len(self._cache_players):
            return self._cache_players[self._current_player_idx - 1]
        return None

    def _cmd(self, args: list[str]):
        player_name = self._get_player()
        cmd_args = [PLAYERCTL]
        if player_name and player_name != "auto":
            cmd_args.extend(["-p", player_name])
        cmd_args.extend(args)
        
        self._pool.submit(lambda: run_command(cmd_args, timeout=1.0))
        GLib.timeout_add(400, self._force_refresh)

    def _force_refresh(self):
        self._refresh_token += 1
        self._refresh_future = None
        self.refresh_async()
        return GLib.SOURCE_REMOVE

    def _on_seek(self, scale: Gtk.Scale):
        if self._suppress_seek: return
        val = scale.get_value()
        self._cmd(["position", str(val)])
        self.elapsed_lbl.set_label(_format_time(val))
        self._pending_seek_deadline = time.monotonic() + 1.25

    def refresh_async(self):
        if self._refresh_future and not self._refresh_future.done():
            return

        self._refresh_token += 1
        token = self._refresh_token
        self._refresh_future = self._pool.submit(lambda: fetch_media_state(self._get_player()))
        if self._refresh_future:
            self._refresh_future.add_done_callback(
                lambda f: self._on_refresh_done(f, token)
            )

    def _on_refresh_done(self, f: Future, token: int):
        try:
            state = f.result() if not f.cancelled() else None
        except Exception as e:
            LOG.error(f"Media refresh error: {e}")
            state = None
        GLib.idle_add(self._apply_state, state, token)

    def _apply_state(self, state: MediaState | None, token: int) -> bool:
        if token != self._refresh_token:
            return GLib.SOURCE_REMOVE

        self._refresh_future = None
        if not state:
            self.hide()
            return GLib.SOURCE_REMOVE

        self.show()
        if state.players != self._cache_players:
            cur = self._get_player()
            self._cache_players = state.players.copy()
            if cur in state.players:
                self._current_player_idx = state.players.index(cur) + 1
                self.player_btn.set_label(cur.capitalize())
            else:
                self._current_player_idx = 0
                self.player_btn.set_label("Auto")

        self.title_lbl.set_markup(f'<span weight="bold">{GLib.markup_escape_text(state.title or "Unknown")}</span>')
        self.artist_lbl.set_label(state.artist or " ")

        if time.monotonic() >= self._pending_seek_deadline:
            self._suppress_seek = True
            try:
                safe_length = state.length
                if safe_length < 0:
                    safe_length = max(state.position, 1.0)
                else:
                    safe_length = max(safe_length, 1.0)

                self.seek_adj.set_upper(safe_length)

                if state.position >= 0.0:
                    clamped_pos = min(state.position, safe_length)
                    self.seek_adj.set_value(clamped_pos)
                    self.elapsed_lbl.set_label(_format_time(clamped_pos))

                if state.length >= 0.0:
                    self.dur_lbl.set_label(_format_time(state.length))
                else:
                    self.dur_lbl.set_label("--:--")
            finally:
                self._suppress_seek = False

        image = self.play_btn.get_image()
        if isinstance(image, Gtk.Image):
            image.set_from_icon_name(
                "media-playback-pause-symbolic" if state.status == "Playing" else "media-playback-start-symbolic",
                Gtk.IconSize.BUTTON
            )
        else:
            self.play_btn.set_image(
                Gtk.Image.new_from_icon_name(
                    "media-playback-pause-symbolic" if state.status == "Playing" else "media-playback-start-symbolic",
                    Gtk.IconSize.BUTTON
                )
            )
        self.shuf_btn.set_opacity(1.0 if state.shuffle else 0.4)
        self._loop_state = state.loop
        image_loop = self.loop_btn.get_image()
        if isinstance(image_loop, Gtk.Image):
            image_loop.set_from_icon_name(
                "media-playlist-repeat-song-symbolic" if state.loop == "Track" else "media-playlist-repeat-symbolic",
                Gtk.IconSize.BUTTON
            )
        else:
            self.loop_btn.set_image(
                Gtk.Image.new_from_icon_name(
                    "media-playlist-repeat-song-symbolic" if state.loop == "Track" else "media-playlist-repeat-symbolic",
                    Gtk.IconSize.BUTTON
                )
            )
        self.loop_btn.set_opacity(0.4 if state.loop == "None" else 1.0)
        return GLib.SOURCE_REMOVE

# ==============================================================================
# MAIN APPLICATION WINDOW
# ==============================================================================

def _get_active_monitor_scaled_height() -> float:
    if HYPRCTL is None:
        return 1080.0
    try:
        r = run_command([HYPRCTL, "-j", "monitors"], timeout=1.0, capture_stdout=True)
        if r is not None and r.returncode == 0 and r.stdout:
            monitors = json.loads(r.stdout)
            for m in monitors:
                if m.get("focused"):
                    return float(m["height"]) / float(m.get("scale", 1.0))
    except Exception as e:
        LOG.debug("Failed to fetch monitor height: %s", e)
    return 1080.0

class QuickPanalWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, pool: RefreshPool,
                 volume_submit: FloatSubmitter | None,
                 brightness_submit: FloatSubmitter | None,
                 sunset_submit: FloatSubmitter | None):
        super().__init__(application=app)
        self.pool = pool
        self._timer_id: int | None = None
        self._cpu_last = (0, 0)
        self._updating_power = False
        self._slider_rows: list[CompactSliderRow] = []

        self.set_default_size(380, -1)
        self.set_resizable(False)
        self.set_decorated(False)
        _add_css_class(self, "panel-window")

        self.connect("delete-event", self._on_delete_event)
        self.connect("show", self._on_show)
        self.connect("hide", self._on_hide)
        self.connect("map", self._on_map)

        if LIBGRAB:
            self._grab_cb = CB_TYPE(self._on_grab_cleared)
        else:
            self._grab_cb = None

        self.connect("key-press-event", self._on_key_pressed)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        main_box.set_margin_start(18)
        main_box.set_margin_end(18)
        main_box.set_margin_top(18)
        main_box.set_margin_bottom(18)

        scaled_height = _get_active_monitor_scaled_height()
        if scaled_height < 864.0:
            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scrolled.add(main_box)
            scrolled.set_propagate_natural_width(True)
            scrolled.set_propagate_natural_height(True)
            scrolled.set_max_content_height(600)
            self.add(scrolled)
        else:
            self.add(main_box)

        # --- Header ---
        self.header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        self.weather_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        _add_css_class(self.weather_box, "weather-pill")
        self.weather_box.set_halign(Gtk.Align.START)
        self.weather_box.set_valign(Gtk.Align.CENTER)
        self.weather_icon = Gtk.Image.new_from_icon_name("weather-few-clouds-symbolic", Gtk.IconSize.MENU)
        self.weather_icon.set_pixel_size(16)
        
        self.weather_lbl = Gtk.Label()
        _add_css_class(self.weather_lbl, "weather-text")
        
        self.weather_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.weather_lbl.set_max_width_chars(15)
        self.weather_lbl.set_width_chars(1)
        
        self.weather_box.pack_start(self.weather_icon, False, False, 0)
        self.weather_box.pack_start(self.weather_lbl, False, False, 0)
        self.weather_box.set_no_show_all(True)
        self.weather_box.hide()

        self.power_btn = Gtk.Button()
        self.power_btn.set_image(Gtk.Image.new_from_icon_name("system-shutdown-symbolic", Gtk.IconSize.BUTTON))
        _add_css_class(self.power_btn, "power-header-btn")
        self.power_btn.set_valign(Gtk.Align.CENTER)
        self.power_btn.set_halign(Gtk.Align.END)
        self.power_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.power_btn.connect("clicked", lambda _: execute_cmd(f"{HOME}/user_scripts/wlogout/wlogout_scale.sh"))

        self.clock_event_box = Gtk.EventBox()
        self.clock_event_box.set_visible_window(False)
        self.clock_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.clock_box.set_halign(Gtk.Align.CENTER)
        self.clock_box.set_valign(Gtk.Align.CENTER)
        self.lbl_time = Gtk.Label()
        _add_css_class(self.lbl_time, "header-time")
        self.lbl_date = Gtk.Label()
        _add_css_class(self.lbl_date, "header-date")
        self.clock_box.pack_start(self.lbl_time, False, False, 0)
        self.clock_box.pack_start(self.lbl_date, False, False, 0)
        self.clock_event_box.add(self.clock_box)
        self.clock_event_box.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.clock_event_box.connect("button-press-event", lambda *args: (execute_cmd("gnome-clocks"), True)[1])

        self.header_box.pack_start(self.weather_box, False, False, 0)
        self.header_box.pack_end(self.power_btn, False, False, 0)
        self.header_box.set_center_widget(self.clock_event_box) 

        main_box.pack_start(self.header_box, False, False, 0)

        # --- Metrics Row ---
        self.metrics_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.metrics_row.set_homogeneous(True)
        self.pill_net = MetricPill(None, "Network Usage", small_text=True)
        self.pill_ram = MetricPill("media-memory-symbolic", "RAM Usage\nLMB: Open zramctl", on_click="kitty --class zramctl --hold zramctl")
        self.pill_cpu = MetricPill("cpu-symbolic", "CPU Usage\nLMB: Open btop", on_click="kitty --class btop btop")
        self.metrics_row.pack_start(self.pill_net, True, True, 0)
        self.metrics_row.pack_start(self.pill_ram, True, True, 0)
        self.metrics_row.pack_start(self.pill_cpu, True, True, 0)
        main_box.pack_start(self.metrics_row, False, False, 0)

        # --- Grid ---
        self.flow = Gtk.FlowBox()
        self.flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flow.set_valign(Gtk.Align.START)
        self.flow.set_halign(Gtk.Align.CENTER)
        self.flow.set_max_children_per_line(5)
        self.flow.set_min_children_per_line(5)
        self.flow.set_column_spacing(14)
        self.flow.set_row_spacing(14)

        self.tg_wifi = QuickIconToggle("network-wireless-symbolic", "Wi-Fi\nLMB: Network Manager", on_left=f"kitty --class dusky_network.sh {HOME}/user_scripts/network_manager/dusky_network.sh")
        self.tg_bt = QuickIconToggle("bluetooth-active-symbolic", "Bluetooth\nLMB: Blueman", on_left="blueman-manager")

        power_saver_toggle_cmd = (
            f"foot --app-id=power_saver.sh bash -c '"
            f"if [ \"$(cat {HOME}/.config/dusky/settings/power_saver_state 2>/dev/null)\" = \"true\" ]; then "
            f"{HOME}/user_scripts/battery/power_saver.sh --disable; "
            f"else {HOME}/user_scripts/battery/power_saver.sh --enable; fi'"
        )

        self.tg_perf = QuickIconToggle(
            "power-profile-performance-symbolic",
            "Power & Performance\nLMB: Toggle Power Saver | MMB: Monitor | RMB: Services",
            on_left=power_saver_toggle_cmd,
            on_middle=f"foot --app-id=services_and_process_terminator.sh {HOME}/user_scripts/performance/services_and_process_terminator.sh",
            on_right=f"foot --app-id=dusky_service_toggle.sh {HOME}/user_scripts/services/dusky_service_toggle.sh"
        )

        self.tg_idle = QuickIconToggle("timer-symbolic", "Hypridle\nLMB: Toggle | RMB: Lock Screen", on_left=f"{HOME}/user_scripts/waybar/toggle_hypridle.sh", on_right=f"{HOME}/user_scripts/hyprlock/lock.sh")
        self.tg_dnd = QuickIconToggle("notification-symbolic", "Do Not Disturb", on_left=f"{HOME}/user_scripts/rofi/rofi_mako.sh", on_middle=f"{HOME}/user_scripts/waybar/mako.sh --clear && pkill -RTMIN+8 waybar", on_right="makoctl mode -t do-not-disturb && pkill -RTMIN+8 waybar")
        self.tg_blur = QuickIconToggle("edit-opacity-symbolic", "Visuals\nLMB: Toggle Blur/Shadow", on_left=f"{HOME}/user_scripts/hypr/hypr_blur_opacity_shadow_toggle.sh toggle")
        self.tg_shader = QuickIconToggle("window-new-symbolic", "Glance & Shaders\nLMB: Glance Menu | RMB: Shader Selector",
            on_left=f"pkill rofi; {HOME}/user_scripts/rofi/dusky_glance.sh",
            on_right=f"pkill rofi; {HOME}/user_scripts/rofi/shader_menu.sh")
        self.tg_settings = QuickIconToggle("preferences-system-symbolic", "Control Center\nLMB: Open", on_left='gdbus call --session --dest com.github.dusky.controlcenter --object-path /com/github/dusky/controlcenter --method org.freedesktop.Application.Activate "{}"')
        self.tg_theme = QuickIconToggle("preferences-desktop-appearance-symbolic", "Matugen Themes\nLMB: Select Theme | RMB: Presets", on_left=f"pkill rofi; {HOME}/user_scripts/rofi/rofi_theme.sh", on_right=f"kitty --class dusky_matugen_presets.sh {HOME}/user_scripts/theme_matugen/dusky_matugen_presets.sh")
        self.tg_updates = QuickIconToggle("folder-download-symbolic", "Updates\nLMB: System Update | RMB: Dusky Update", on_left=f"kitty --class system_update.sh --hold sh -c '{HOME}/user_scripts/update_dusky/system_update.sh --all'", on_right=f"kitty --class update_dusky.sh --hold sh -c '{HOME}/user_scripts/update_dusky/update_dusky.sh'")

        for tg in (self.tg_wifi, self.tg_bt, self.tg_perf, self.tg_idle, self.tg_dnd, self.tg_blur, self.tg_shader, self.tg_settings, self.tg_theme, self.tg_updates):
            self.flow.add(tg)
        main_box.pack_start(self.flow, False, False, 0)

        # --- Power Management Row ---
        self.power_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        _add_css_class(self.power_container, "power-profile-row")

        power_icon = Gtk.Image.new_from_icon_name("power-profile-balanced-symbolic", Gtk.IconSize.BUTTON)
        _add_css_class(power_icon, "accent-icon")
        self.power_container.pack_start(power_icon, False, False, 0)

        power_label = Gtk.Label(label="Power Profile")
        _add_css_class(power_label, "power-label")
        power_label.set_halign(Gtk.Align.START)
        self.power_container.pack_start(power_label, True, True, 0)

        self.power_cmds = {
            "Balanced": "tlpctl balanced && notify-send 'Power Profile' 'Switched to Balanced'",
            "Performance": "tlpctl performance && notify-send 'Power Profile' 'Switched to Performance'",
            "Power Saver": "tlpctl power-saver && notify-send 'Power Profile' 'Switched to Power Saver'"
        }

        self.power_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.power_box.set_valign(Gtk.Align.CENTER)

        self.btn_save = Gtk.RadioButton()
        self.btn_save.set_mode(False)
        self.btn_save.set_image(Gtk.Image.new_from_icon_name("power-profile-power-saver-symbolic", Gtk.IconSize.BUTTON))
        self.btn_save.set_tooltip_text("Power Saver")
        self.btn_save.set_relief(Gtk.ReliefStyle.NONE)
        _add_css_class(self.btn_save, "power-ring-btn")

        self.btn_bal = Gtk.RadioButton.new_from_widget(self.btn_save)
        self.btn_bal.set_mode(False)
        self.btn_bal.set_image(Gtk.Image.new_from_icon_name("power-profile-balanced-symbolic", Gtk.IconSize.BUTTON))
        self.btn_bal.set_tooltip_text("Balanced")
        self.btn_bal.set_relief(Gtk.ReliefStyle.NONE)
        _add_css_class(self.btn_bal, "power-ring-btn")

        self.btn_perf = Gtk.RadioButton.new_from_widget(self.btn_save)
        self.btn_perf.set_mode(False)
        self.btn_perf.set_image(Gtk.Image.new_from_icon_name("power-profile-performance-symbolic", Gtk.IconSize.BUTTON))
        self.btn_perf.set_tooltip_text("Performance")
        self.btn_perf.set_relief(Gtk.ReliefStyle.NONE)
        _add_css_class(self.btn_perf, "power-ring-btn")

        self.btn_save.connect("toggled", self._on_power_toggled, "Power Saver")
        self.btn_bal.connect("toggled", self._on_power_toggled, "Balanced")
        self.btn_perf.connect("toggled", self._on_power_toggled, "Performance")

        for btn in (self.btn_save, self.btn_bal, self.btn_perf):
            self.power_box.pack_start(btn, False, False, 0)

        self.power_container.pack_end(self.power_box, False, False, 0)
        main_box.pack_start(self.power_container, False, False, 0)

        # --- Hardware Sliders Injection ---
        self.sliders_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        _add_css_class(self.sliders_box, "sliders-container")

        if HAS_VOLUME and volume_submit is not None:
            row = CompactSliderRow("", "volume", 0.0, 100.0, 1.0, get_volume, volume_submit, self.pool)
            self._slider_rows.append(row)
            self.sliders_box.pack_start(row, False, False, 0)

        if HAS_BRIGHTNESS and brightness_submit is not None:
            row = CompactSliderRow("󰃠", "brightness", 1.0, 100.0, 1.0, get_brightness, brightness_submit, self.pool, post_submit_refresh_grace_seconds=BRIGHTNESS_POST_SUBMIT_REFRESH_GRACE_SECONDS)
            self._slider_rows.append(row)
            self.sliders_box.pack_start(row, False, False, 0)

        if HAS_SUNSET and sunset_submit is not None:
            row = CompactSliderRow("󰡬", "sunset", 1000.0, 6000.0, 50.0, get_hyprsunset_state, sunset_submit, self.pool)
            self._slider_rows.append(row)
            self.sliders_box.pack_start(row, False, False, 0)

        if self._slider_rows:
            main_box.pack_start(self.sliders_box, False, False, 0)

        # --- Dynamic Sections ---
        if PLAYERCTL:
            self.media_module = MediaCard(self.pool)
            main_box.pack_start(self.media_module, False, False, 0)

    def _on_map(self, *args):
        self._activate_grab()

    def _activate_grab(self):
        if LIBGRAB and self.get_visible() and self._grab_cb:
            ptr_val = hash(self)
            # Guarantee a positive unsigned 64-bit bounds memory address pointer for c_void_p
            # Prevents C library validation failure if PyGObject passes a negative address representation
            if ptr_val < 0:
                ptr_val += 1 << (ctypes.sizeof(ctypes.c_void_p) * 8)
                
            window_ptr = ctypes.c_void_p(ptr_val)
            LIBGRAB.init_wayland_grab(window_ptr, self._grab_cb)

    def _on_grab_cleared(self):
        GLib.idle_add(self.hide)

    def _update_ui_state(self):
        now = datetime.now()
        self.lbl_time.set_label(now.strftime("%I:%M"))
        self.lbl_date.set_label(now.strftime("%A, %B %d"))

        self.pool.submit(self._fetch_weather)
        self.pool.submit(self._fetch_mako)
        self.pool.submit(self._fetch_idle)
        self.pool.submit(self._fetch_blur)
        self.pool.submit(self._fetch_power_profile)
        self.pool.submit(self._fetch_hardware_metrics)
        self.pool.submit(self._fetch_network)
        self.pool.submit(self._fetch_updates)
        self.pool.submit(self._fetch_power_saver)

        for row in self._slider_rows: row.refresh_async()
        if PLAYERCTL: self.media_module.refresh_async()

        return GLib.SOURCE_CONTINUE

    def _fetch_power_saver(self):
        state_file = f"{HOME}/.config/dusky/settings/power_saver_state"
        is_active = False
        try:
            with open(state_file, "r") as f:
                is_active = f.read().strip() == "true"
        except OSError:
            pass
        GLib.idle_add(self._apply_power_saver, is_active)

    def _apply_power_saver(self, is_active: bool):
        tooltip = "Power & Performance\nLMB: Toggle Power Saver\nMMB: Process Terminator | RMB: Services"
        if is_active:
            self.tg_perf.update_state(icon="battery-good-charging-symbolic", css_class="power-saver-active", tooltip=tooltip)
        else:
            self.tg_perf.update_state(icon="ac-adapter", css_class="normal", tooltip=tooltip)

    def _fetch_weather(self):
        try:
            data = fetch_json_output(f"python3 {HOME}/user_scripts/waybar/weather.py")
            if data:
                if data.get("text"):
                    GLib.idle_add(self._apply_weather, data.get("text").strip())
                else:
                    GLib.idle_add(lambda: self.weather_box.hide())
            else:
                GLib.idle_add(lambda: self.weather_box.hide())
        except Exception:
            GLib.idle_add(lambda: self.weather_box.hide())

    def _apply_weather(self, text: str):
        self.weather_lbl.set_label(text)
        self.weather_icon.show()
        self.weather_lbl.show()
        self.weather_box.show()

    def _fetch_mako(self):
        data = fetch_json_output(f"{HOME}/user_scripts/waybar/mako.sh --horizontal")
        if data: GLib.idle_add(self._apply_mako, data)

    def _fetch_idle(self):
        r = run_command(["pgrep", "-x", "hypridle"], timeout=0.8, capture_stdout=True)
        active = r is not None and r.returncode == 0
        GLib.idle_add(self._apply_idle, active)

    def _fetch_blur(self):
        try:
            with open(f"{HOME}/.config/dusky/settings/opacity_blur", "r") as f: state = f.read().strip().lower()
            GLib.idle_add(self._apply_blur, state == "true")
        except Exception: pass

    def _fetch_power_profile(self):
        try:
            r = run_command(["tlpctl", "get"], timeout=1.0, capture_stdout=True)
            if r is not None and r.returncode == 0 and r.stdout:
                GLib.idle_add(self._apply_power_profile, r.stdout.strip().lower())
        except Exception: pass

    def _fetch_hardware_metrics(self):
        try:
            with open("/proc/stat", "r") as f: parts = [int(p) for p in f.readline().split()[1:]]
            idle = parts[3] + parts[4]
            total = sum(parts)
            last_idle, last_total = self._cpu_last
            d_idle, d_total = idle - last_idle, total - last_total
            cpu_usage = 100 * (1.0 - d_idle / d_total) if d_total > 0 else 0
            self._cpu_last = (idle, total)

            mem_tot = mem_av = 0
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal:"): mem_tot = int(line.split()[1])
                    elif line.startswith("MemAvailable:"): mem_av = int(line.split()[1])
                    if mem_tot and mem_av: break
            ram_used = (mem_tot - mem_av) / 1048576

            GLib.idle_add(self.pill_cpu.set_value, f"{cpu_usage:.0f}%")
            GLib.idle_add(self.pill_ram.set_value, f"{ram_used:.1f} GB")
        except Exception: pass

    def _fetch_network(self):
        data = fetch_json_output(f"{HOME}/user_scripts/waybar/network/network_meter_calling.sh --horizontal")
        GLib.idle_add(self.pill_net.apply_json, data, "network-disconnected")

    def _fetch_updates(self):
        try:
            with open(f"{HOME}/.config/dusky/settings/waybar_update_counter_h", "r") as f: data = json.load(f)
            GLib.idle_add(self._apply_updates, data)
        except Exception: pass

    def _apply_updates(self, data: dict):
        css = data.get("class", "updated")
        base_tt = data.get("tooltip", "Updates")
        final_tt = f"{base_tt}\n\nLMB: System Update | RMB: Dusky Update"

        if css == "pending":
            match = _RE_UPDATES_TOTAL.search(base_tt)
            badge = match.group(1) if match else "!"
            self.tg_updates.update_state(icon="folder-download-symbolic", css_class="normal", tooltip=final_tt, badge=badge)
        else:
            self.tg_updates.update_state(icon="folder-download-symbolic", css_class="normal", tooltip=final_tt, badge="")

    def _apply_mako(self, data: dict):
        text = data.get("text", "")
        css = data.get("class", "empty")
        badge_match = _RE_MAKO_BADGE.search(text)
        badge = badge_match.group(0) if badge_match else ""
        base_tt = data.get("tooltip", "Notifications")
        final_tt = f"{base_tt}\nLMB: Open | MMB: Clear | RMB: Toggle DND"
        if css in ("dnd", "dnd-pending"): self.tg_dnd.update_state(icon="notifications-disabled-symbolic", css_class="dnd-active", tooltip=final_tt, badge=badge)
        else: self.tg_dnd.update_state(icon="notification-symbolic", css_class="normal", tooltip=final_tt, badge=badge)

    def _apply_idle(self, is_active: bool):
        if is_active: self.tg_idle.update_state(icon="timer-symbolic", css_class="normal", tooltip="Idle Allowed (Timer Active)\nLMB: Toggle | RMB: Lock Screen")
        else: self.tg_idle.update_state(icon="view-reveal-symbolic", css_class="active", tooltip="Idle Inhibited (Awake)\nLMB: Toggle | RMB: Lock Screen")

    def _apply_blur(self, is_active: bool):
        if is_active: self.tg_blur.update_state(icon="applications-graphics-symbolic", css_class="active", tooltip="Visuals: Blur & Shadow ON\nLMB: Toggle")
        else: self.tg_blur.update_state(icon="edit-opacity-symbolic", css_class="normal", tooltip="Visuals: Performance Mode\nLMB: Toggle")

    def _apply_power_profile(self, profile: str):
        mapping = {"balanced": self.btn_bal, "performance": self.btn_perf, "power-saver": self.btn_save}
        target_btn = mapping.get(profile)
        if target_btn and not target_btn.get_active():
            self._updating_power = True
            target_btn.set_active(True)
            self._updating_power = False

    def _on_power_toggled(self, button: Gtk.RadioButton, profile_name: str):
        if not button.get_active() or self._updating_power: return
        cmd = self.power_cmds.get(profile_name)
        if cmd: execute_cmd(cmd)

    def _on_delete_event(self, _window, _event) -> bool:
        self.hide()
        return True 

    def _on_show(self, *args):
        self._activate_grab()
        app = self.get_application()
        if app and hasattr(app, "resume_workers"):
            app.resume_workers()
        if self._timer_id is None:
            self._update_ui_state()
            self._timer_id = GLib.timeout_add(2000, self._update_ui_state)

    def _on_hide(self, *args):
        if LIBGRAB:
            LIBGRAB.destroy_wayland_grab()
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        app = self.get_application()
        if app and hasattr(app, "suspend_workers"):
            app.suspend_workers()
        GLib.timeout_add(500, self._deferred_reclaim)

    def _deferred_reclaim(self) -> bool:
        """Reclaim heap memory after the GTK event queue has settled."""
        if not self.get_visible():
            _reclaim_idle_memory()
        return GLib.SOURCE_REMOVE

    def _on_key_pressed(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return False

# ==============================================================================
# UNIFIED CSS STYLING — SYSTEM GTK3 THEME COLORS
# ==============================================================================

CSS: Final = """
window.panel-window {
    background-color: alpha(@theme_bg_color, 0.95);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 24px;
    box-shadow: 0 12px 36px rgba(0, 0, 0, 0.6);
}

scrolledwindow { background: transparent; }

* { outline: none; }
button { 
    outline: none; 
    transition: background-color 200ms ease, opacity 200ms ease, box-shadow 200ms ease; 
}

.header-time { font-size: 46px; font-weight: 800; letter-spacing: -2px; color: @theme_fg_color; }
.header-date { font-size: 14px; font-weight: 600; color: alpha(@theme_fg_color, 0.7); }

box.weather-pill { padding: 6px 4px; }
.weather-text { font-size: 13px; font-weight: 700; color: alpha(@theme_fg_color, 0.9); }

button.power-header-btn {
    min-width: 42px; min-height: 42px; border-radius: 21px;
    background-color: alpha(#ff453a, 0.6); color: #ff453a;
    border: 1px solid rgba(255, 255, 255, 0.05);
}
button.power-header-btn:hover { background-color: #ff453a; color: white; }

button.quick-icon-toggle {
    min-width: 52px; min-height: 52px; border-radius: 26px;
    background-color: rgba(255, 255, 255, 0.06);
    background-image: none;
    border: 1px solid rgba(255, 255, 255, 0.05);
    box-shadow: none;
    padding: 0;
    transition: all 0.2s cubic-bezier(0.25, 0.46, 0.45, 0.94);
}
button.quick-icon-toggle:hover {
    background-color: rgba(255, 255, 255, 0.12);
    background-image: none;
    box-shadow: none;
}
button.quick-icon-toggle.active {
    background-color: alpha(@theme_selected_bg_color, 0.3);
    background-image: none;
    border: 1px solid alpha(@theme_selected_bg_color, 0.5);
    box-shadow: none;
}
button.quick-icon-toggle.active:hover {
    background-color: alpha(@theme_selected_bg_color, 0.5);
    background-image: none;
    box-shadow: none;
}
button.quick-icon-toggle.active image { color: @theme_selected_bg_color; }
button.quick-icon-toggle.normal image { opacity: 1.0; }

button.quick-icon-toggle.power-saver-active {
    background-color: alpha(#a6e3a1, 0.3);
    background-image: none;
    border: 1px solid alpha(#a6e3a1, 0.5);
    box-shadow: none;
}
button.quick-icon-toggle.power-saver-active:hover {
    background-color: alpha(#a6e3a1, 0.5);
    background-image: none;
    box-shadow: none;
}
button.quick-icon-toggle.power-saver-active image { color: #a6e3a1; }

button.quick-icon-toggle.dnd-active {
    background-color: alpha(#ff453a, 0.3);
    background-image: none;
    border: 1px solid alpha(#ff453a, 0.5);
    box-shadow: none;
}
button.quick-icon-toggle.dnd-active:hover {
    background-color: alpha(#ff453a, 0.5);
    background-image: none;
    box-shadow: none;
}
button.quick-icon-toggle.dnd-active image { color: #ff453a; }

.notification-badge {
    background-color: @theme_selected_bg_color; color: black;
    font-size: 11px; font-weight: 900; border-radius: 12px;
    min-width: 24px; min-height: 24px; padding: 0; margin: 2px;
    border: 1px solid rgba(255, 255, 255, 0.2);
    box-shadow: 0 2px 5px rgba(0,0,0,0.5);
}

box.metric-pill {
    background-color: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 14px; padding: 10px 12px;
    transition: all 0.2s cubic-bezier(0.25, 0.46, 0.45, 0.94);
}
eventbox.clickable-pill:hover box.metric-pill { background-color: rgba(255, 255, 255, 0.12); }
eventbox.clickable-pill:active box.metric-pill { background-color: alpha(@theme_selected_bg_color, 0.3); border-color: alpha(@theme_selected_bg_color, 0.5); }
.metric-value, .metric-value-small { font-family: "JetBrainsMono Nerd Font", monospace; color: @theme_fg_color; font-weight: 700; }
.metric-value { font-size: 12px; }
.metric-value-small { font-size: 10px; letter-spacing: -0.5px; }

/* Power Profile Row */
.power-profile-row {
    background-color: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 14px;
    padding: 6px 12px;
}
.power-label { font-size: 14px; font-weight: 600; color: @theme_fg_color; }
.accent-icon { color: @theme_selected_bg_color; }

button.power-ring-btn {
    border: 2px solid transparent; border-radius: 18px;
    min-width: 36px; min-height: 36px;
    padding: 0; margin: 0;
    background-color: transparent;
    transition: all 0.2s cubic-bezier(0.25, 0.46, 0.45, 0.94);
    color: alpha(@theme_fg_color, 0.7);
}
button.power-ring-btn:hover { background-color: rgba(255, 255, 255, 0.08); }
button.power-ring-btn:checked {
    background-image: none;
    background-color: alpha(@theme_selected_bg_color, 0.15);
    border-color: @theme_selected_bg_color;
    color: @theme_selected_bg_color;
    box-shadow: none;
}

/* Media Card */
box.media-card { 
    background-color: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 16px; padding: 14px; 
}
.media-title { font-size: 14px; font-weight: 700; font-family: sans-serif; color: @theme_fg_color; }
.media-artist { font-size: 12px; font-weight: 500; opacity: 0.8; font-family: sans-serif; color: @theme_fg_color; }
.media-time { font-size: 11px; opacity: 0.7; font-family: "JetBrainsMono Nerd Font", monospace; color: @theme_fg_color; }
.media-btn { min-width: 38px; min-height: 38px; border-radius: 19px; padding: 0; transition: all 0.2s; color: @theme_fg_color; }
.media-btn:hover { background-color: rgba(255, 255, 255, 0.1); }

.media-play-btn {
    min-width: 44px; min-height: 44px; border-radius: 22px; padding: 0;
    background-color: alpha(@theme_selected_bg_color, 0.15);
    border: 2px solid alpha(@theme_selected_bg_color, 0.5);
    color: @theme_fg_color;
    transition: all 0.2s cubic-bezier(0.25, 0.46, 0.45, 0.94);
}
.media-play-btn:hover {
    background-color: alpha(@theme_selected_bg_color, 0.35);
    border-color: @theme_selected_bg_color;
}
.media-play-btn:active {
    background-color: alpha(@theme_selected_bg_color, 0.55);
}

/* Sliders */
.sliders-container {
    background-color: rgba(255, 255, 255, 0.06);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 16px;
    padding: 6px;
}
.slider-row { background-color: transparent; padding: 8px 10px; }

scale.pill-scale trough { min-height: 14px; border-radius: 7px; background-color: rgba(255, 255, 255, 0.08); }
scale.pill-scale highlight { min-height: 14px; border-radius: 7px; }
scale.pill-scale slider { min-width: 0px; min-height: 0px; margin: 0px; padding: 0px; background: transparent; border: none; box-shadow: none; }

scale.volume highlight { background-color: #89b4fa; }
scale.brightness highlight { background-color: #f9e2af; }
scale.sunset highlight { background-color: #fab387; }
scale.media-scale highlight { background-color: #cba6f7; min-height: 8px; border-radius: 4px; }
scale.media-scale trough { min-height: 8px; border-radius: 4px; background-color: rgba(255, 255, 255, 0.15); }

.icon-volume { color: #89b4fa; }
.icon-brightness { color: #f9e2af; }
.icon-sunset { color: #fab387; }
.icon-label { font-size: 18px; font-family: "Symbols Nerd Font", "JetBrainsMono Nerd Font", monospace; }
.value-label { font-size: 14px; font-weight: 700; opacity: 0.8; font-family: "JetBrainsMono Nerd Font", monospace; font-feature-settings: "tnum"; }
"""

class QuickPanalApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window: QuickPanalWindow | None = None
        self.pool: RefreshPool | None = None
        self._volume_worker: LatestValueWorker | None = None
        self._local_brightness_worker: LatestValueWorker | None = None
        self._sunset_controller: HyprsunsetController | None = None

    def submit_volume(self, value: float) -> None:
        if self._volume_worker is not None:
            self._volume_worker.submit(value)

    def submit_brightness(self, value: float) -> None:
        if self._local_brightness_worker is not None:
            self._local_brightness_worker.submit(value)
        if DDC_MANAGER is not None:
            DDC_MANAGER.submit(value)

    def submit_sunset(self, value: float) -> None:
        if self._sunset_controller is not None:
            self._sunset_controller.submit(value)

    def suspend_workers(self) -> None:
        LOG.debug("Suspending workers...")
        if self.pool is not None:
            self.pool.shutdown()
        if self._sunset_controller is not None:
            self._sunset_controller.stop()
        if self._local_brightness_worker is not None:
            self._local_brightness_worker.stop()
        if DDC_MANAGER is not None:
            DDC_MANAGER.stop()
        if self._volume_worker is not None:
            self._volume_worker.stop()
        _reclaim_idle_memory()

    def resume_workers(self) -> None:
        LOG.debug("Resuming workers...")
        gc.unfreeze()
        if DDC_MANAGER is not None:
            DDC_MANAGER.start()
        if self._volume_worker is not None:
            self._volume_worker.start()
        if self._local_brightness_worker is not None:
            self._local_brightness_worker.start()
        if self._sunset_controller is not None:
            self._sunset_controller.start()

    @override
    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()

        if DDC_MANAGER is not None: DDC_MANAGER.start()

        self.pool = RefreshPool(max_workers=4)
        self._volume_worker = LatestValueWorker("volume", apply_volume) if HAS_VOLUME else None
        self._local_brightness_worker = LatestValueWorker("local-brightness", apply_local_brightness) if HAS_LOCAL_BRIGHTNESS else None
        self._sunset_controller = HyprsunsetController() if HAS_SUNSET else None

        settings = Gtk.Settings.get_default()
        if settings:
            settings.set_property("gtk-application-prefer-dark-theme", True)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.window = QuickPanalWindow(
            self, self.pool,
            volume_submit=self.submit_volume if HAS_VOLUME else None,
            brightness_submit=self.submit_brightness if HAS_BRIGHTNESS else None,
            sunset_submit=self.submit_sunset if HAS_SUNSET else None
        )
        self.suspend_workers()

    @override
    def do_activate(self):
        if self.window:
            self.window.show_all()
            self.window.present()

    @override
    def do_shutdown(self):
        if self.window and self.window._timer_id is not None:
            GLib.source_remove(self.window._timer_id)
            self.window._timer_id = None
        self.suspend_workers()
        Gtk.Application.do_shutdown(self)


if __name__ == "__main__":
    app = QuickPanalApp()
    try:
        sys.exit(app.run(sys.argv))
    except KeyboardInterrupt:
        # Gracefully handle SIGINT triggers natively passed by PyGObject
        sys.exit(0)
