#!/usr/bin/env python3
"""
Dusky Sliders: GTK4/Libadwaita controls for PipeWire volume, Linux backlight,
DDC/CI monitor brightness, Hyprland hyprsunset temperature, and MPRIS Media.

Target: Arch Linux + Hyprland + Python 3.14.4+
"""

from __future__ import annotations

import contextvars
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

if sys.version_info < (3, 14, 4):
    raise SystemExit("Dusky Sliders requires Python 3.14.4 or newer.")

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Pango", "1.0")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango
except (ImportError, ValueError) as exc:
    raise SystemExit(f"Failed to load GTK4/Libadwaita: {exc}") from exc


APP_ID: Final = "org.dusky.sliders"

if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.WARNING,
        format=f"{APP_ID}: %(levelname)s: %(message)s",
    )

LOG: Final = logging.getLogger(APP_ID)

COMMAND_ENV: Final = os.environ.copy()
COMMAND_ENV["LC_ALL"] = "C"
COMMAND_ENV["LANG"] = "C"

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
MEDIA_REFRESH_INTERVAL_SECONDS: Final = 1.0
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
    argv = [os.fspath(arg) for arg in args]
    try:
        return subprocess.run(
            argv,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            env=COMMAND_ENV,
            close_fds=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        LOG.debug("Command failed: %r: %s", argv, exc)
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

    value = parse_float(parts[4].rstrip("%"))
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

    LOG.debug(
        "Brightness written via sysfs %s: %s%% -> raw=%s/%s",
        device.path.name,
        brightness,
        raw_value,
        maximum,
    )
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


class DdcManager:
    __slots__ = (
        "_cache_file",
        "_detect_thread",
        "_displays",
        "_last_requested",
        "_lock",
        "_started",
        "_workers",
    )

    def __init__(self, cache_file: Path | None) -> None:
        self._cache_file = cache_file
        self._lock = threading.Lock()
        self._displays: dict[int, DdcDisplay] = {}
        self._workers: dict[int, LatestValueWorker] = {}
        self._last_requested: float | None = None
        self._started = False
        self._detect_thread: threading.Thread | None = None

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
            discovered[bus] = display if display is not None else DdcDisplay(bus=bus)

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
HAS_PLAYERCTL: Final = PLAYERCTL is not None

# ==============================================================================
# MPRIS MEDIA STATE
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

def _playerctl(cmd_args: list[str], player: str | None = None) -> str | None:
    if PLAYERCTL is None:
        return None
    cmd = [PLAYERCTL]
    if player and player != "auto":
        cmd.extend(["-p", player])
    cmd.extend(cmd_args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=0.2, env=COMMAND_ENV)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return None

def fetch_media_state(player: str | None = None) -> MediaState | None:
    if PLAYERCTL is None:
        return None

    try:
        r = subprocess.run([PLAYERCTL, "-l"], capture_output=True, text=True, timeout=0.2, env=COMMAND_ENV)
        current_players = [p.strip() for p in r.stdout.splitlines() if p.strip()]
    except Exception:
        current_players = []

    if not current_players:
        return None

    status = _playerctl(["status"], player)
    if status not in ("Playing", "Paused"):
        return None

    raw_meta = _playerctl(["metadata"], player)
    title, artist, length = "Unknown", "", 0.0
    if raw_meta:
        for line in raw_meta.splitlines():
            parts = line.split(None, 2)
            if len(parts) >= 3:
                key, val = parts[1], parts[2]
                if key == "xesam:title": title = val
                elif key == "xesam:artist": artist = val
                elif key == "mpris:length":
                    try: length = int(val) / 1_000_000.0
                    except ValueError: pass

    pos_str = _playerctl(["position"], player)
    pos = 0.0
    if pos_str:
        try: pos = float(pos_str)
        except ValueError: pass

    shuffle = (_playerctl(["shuffle"], player) or "").lower() == "on"
    loop = _playerctl(["loop"], player) or "None"

    return MediaState(
        players=current_players,
        status=status,
        title=title,
        artist=artist,
        position=pos,
        length=length,
        shuffle=shuffle,
        loop=loop
    )

def execute_player_cmd(cmd_list: list[str], player: str | None = None) -> None:
    def worker() -> None:
        _playerctl(cmd_list, player)
    start_thread("playerctl-cmd", worker, daemon=True)

def cycle_loop(current: str, player: str | None = None) -> None:
    cycle = {'None': 'Playlist', 'Playlist': 'Track', 'Track': 'None'}
    execute_player_cmd(['loop', cycle.get(current, 'None')], player)

def _format_time(secs: float) -> str:
    if secs < 0:
        secs = 0.0
    s = int(secs)
    return f"{s // 60}:{s % 60:02d}"


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
        self._fallback_process: subprocess.Popen[bytes] | None = None

    def submit(self, value: float) -> None:
        self._worker.submit(float(kelvin_value(value)))

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
                    env=COMMAND_ENV,
                )
            except OSError as exc:
                LOG.warning("Failed to start hyprsunset fallback process: %s", exc)
                return

            self._fallback_process = new_proc

        start_thread("hyprsunset-reaper", self._reap_fallback_process, new_proc, daemon=True)

    def _reap_fallback_process(self, proc: subprocess.Popen[bytes]) -> None:
        try:
            proc.wait()
        except Exception:
            LOG.exception("Unhandled exception while waiting for hyprsunset fallback")
        finally:
            was_active_backend = False
            with self._process_lock:
                if self._fallback_process is proc:
                    self._fallback_process = None
                    was_active_backend = True

            if was_active_backend and not self._is_hyprsunset_running():
                self._ready.clear()


class RefreshPool:
    __slots__ = ("_executor", "_shutdown")

    def __init__(self, max_workers: int = 3) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="dusky-refresh",
        )
        self._shutdown = False

    def submit(self, func: FloatGetter) -> Future[float | None] | None:
        if self._shutdown:
            return None
        try:
            return self._executor.submit(func)
        except RuntimeError:
            return None

    def submit_generic(self, func: Callable) -> Future | None:
        if self._shutdown:
            return None
        try:
            return self._executor.submit(func)
        except RuntimeError:
            return None

    def shutdown(self) -> None:
        self._shutdown = True
        self._executor.shutdown(wait=False, cancel_futures=True)


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

        self.add_css_class("slider-row")

        self.icon = Gtk.Label(label=icon_text)
        self.icon.add_css_class("icon-label")
        self.icon.add_css_class(f"icon-{css_class}")
        self.append(self.icon)

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
        self.scale.add_css_class("pill-scale")
        self.scale.add_css_class(css_class)
        self.scale.connect("value-changed", self._on_value_changed)
        self.append(self.scale)

        self.value_label = Gtk.Label(label="…")
        self.value_label.set_width_chars(4)
        self.value_label.set_xalign(1.0)
        self.value_label.add_css_class("value-label")
        self.append(self.value_label)

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


class MediaCard(Gtk.Box):
    def __init__(self, refresh_pool: RefreshPool):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._refresh_pool = refresh_pool
        self._refresh_future: Future | None = None
        self._suppress_seek = False
        self._pending_seek_deadline = 0.0
        self._current_players_cache: list[str] = []
        self._updating_model = False

        self.set_margin_top(8)
        self.set_margin_bottom(8)
        self.set_margin_start(14)
        self.set_margin_end(14)
        self.add_css_class("media-card")
        self.set_visible(False)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep.set_margin_bottom(8)
        self.append(sep)

        # -- Player Selection --
        self.player_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.player_label = Gtk.Label(label="Source:")
        self.player_label.add_css_class("value-label")
        self._player_model = Gtk.StringList.new(["Auto"])
        self.player_combo = Gtk.DropDown.new(model=self._player_model)
        self.player_combo.set_hexpand(True)
        self.player_combo.connect("notify::selected", self._on_player_selected)
        
        self.player_box.append(self.player_label)
        self.player_box.append(self.player_combo)
        self.append(self.player_box)

        # -- Metadata --
        self.meta_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.title_lbl = Gtk.Label(label=" ")
        self.title_lbl.set_halign(Gtk.Align.START)
        self.title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_lbl.add_css_class("media-title")
        self.meta_box.append(self.title_lbl)

        self.artist_lbl = Gtk.Label(label=" ")
        self.artist_lbl.set_halign(Gtk.Align.START)
        self.artist_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        self.artist_lbl.add_css_class("media-artist")
        self.meta_box.append(self.artist_lbl)
        self.append(self.meta_box)

        # -- Progress Bar --
        self.progress_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        
        # Alignment fixes applied here to stop UI clipping and layout jumping
        self.elapsed_lbl = Gtk.Label(label="0:00")
        self.elapsed_lbl.add_css_class("media-time")
        self.elapsed_lbl.set_valign(Gtk.Align.CENTER)
        self.elapsed_lbl.set_width_chars(5)
        self.elapsed_lbl.set_xalign(1.0)
        
        self.seek_adj = Gtk.Adjustment(value=0, lower=0, upper=1, step_increment=1, page_increment=5)
        self.seek_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.seek_adj)
        self.seek_scale.set_hexpand(True)
        self.seek_scale.set_draw_value(False)
        self.seek_scale.set_valign(Gtk.Align.CENTER)
        self.seek_scale.add_css_class("pill-scale")
        self.seek_scale.add_css_class("media-scale")
        self.seek_scale.connect("value-changed", self._on_seek_changed)
        
        self.duration_lbl = Gtk.Label(label="0:00")
        self.duration_lbl.add_css_class("media-time")
        self.duration_lbl.set_valign(Gtk.Align.CENTER)
        self.duration_lbl.set_width_chars(5)
        self.duration_lbl.set_xalign(0.0)
        
        self.progress_box.append(self.elapsed_lbl)
        self.progress_box.append(self.seek_scale)
        self.progress_box.append(self.duration_lbl)
        self.append(self.progress_box)

        # -- Controls --
        self.controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.controls_box.set_halign(Gtk.Align.CENTER)
        
        self.shuffle_btn = self._icon_btn("media-playlist-shuffle-symbolic", lambda _: execute_player_cmd(["shuffle", "toggle"], self._get_active_player()))
        self.prev_btn    = self._icon_btn("media-skip-backward-symbolic", lambda _: execute_player_cmd(["previous"], self._get_active_player()))
        self.play_btn    = self._icon_btn("media-playback-start-symbolic", lambda _: execute_player_cmd(["play-pause"], self._get_active_player()))
        self.next_btn    = self._icon_btn("media-skip-forward-symbolic", lambda _: execute_player_cmd(["next"], self._get_active_player()))
        self.loop_btn    = self._icon_btn("media-playlist-repeat-symbolic", self._on_loop_clicked)
        
        for btn in (self.shuffle_btn, self.prev_btn, self.play_btn, self.next_btn, self.loop_btn):
            self.controls_box.append(btn)
        self.append(self.controls_box)
        
        self._loop_state = "None"

    def _icon_btn(self, icon: str, callback: Callable) -> Gtk.Button:
        btn = Gtk.Button()
        btn.set_icon_name(icon)
        btn.add_css_class("flat")
        btn.add_css_class("media-btn")
        btn.connect("clicked", callback)
        return btn

    def _get_active_player(self) -> str | None:
        idx = self.player_combo.get_selected()
        if idx == 0 or idx > len(self._current_players_cache):
            return None # translates to "auto" in execution
        return self._current_players_cache[idx - 1]

    def _on_player_selected(self, *args) -> None:
        if self._updating_model:
            return
        self.refresh_async()

    def _on_seek_changed(self, scale: Gtk.Scale) -> None:
        if self._suppress_seek:
            return
        val = scale.get_value()
        execute_player_cmd(["position", str(val)], self._get_active_player())
        self.elapsed_lbl.set_label(_format_time(val))
        self._pending_seek_deadline = time.monotonic() + 1.25

    def _on_loop_clicked(self, _btn) -> None:
        cycle_loop(self._loop_state, self._get_active_player())

    def refresh_async(self) -> None:
        if self._refresh_future is not None and not self._refresh_future.done():
            return
            
        future = self._refresh_pool.submit_generic(lambda: fetch_media_state(self._get_active_player()))
        if future is None:
            return
            
        self._refresh_future = future
        future.add_done_callback(self._refresh_done)

    def _refresh_done(self, future: Future) -> None:
        try:
            state: MediaState | None = future.result()
        except CancelledError:
            return
        except Exception:
            LOG.exception("Unhandled exception while refreshing media state")
            state = None
            
        GLib.idle_add(self._apply_state, state)

    def _apply_state(self, state: MediaState | None) -> bool:
        self._refresh_future = None
        
        if state is None:
            if self.get_visible():
                self.set_visible(False)
            return GLib.SOURCE_REMOVE

        if not self.get_visible():
            self.set_visible(True)

        if state.players != self._current_players_cache:
            selected_player = self._get_active_player()
            self._current_players_cache = state.players.copy()
            new_items = ["Auto"] + [p.capitalize() for p in state.players]
            
            # Lock out the selection signal from triggering an immediate refresh
            self._updating_model = True
            self._player_model.splice(0, self._player_model.get_n_items(), new_items)
            
            if selected_player in state.players:
                self.player_combo.set_selected(state.players.index(selected_player) + 1)
            else:
                self.player_combo.set_selected(0)
                
            self._updating_model = False
            
        # Ensure label space is preserved even if there is no text
        title_text = state.title.strip() if state.title else "Unknown"
        self.title_lbl.set_markup(f'<span weight="bold">{GLib.markup_escape_text(title_text)}</span>')
        
        artist_text = state.artist.strip() if state.artist else " "
        self.artist_lbl.set_label(artist_text if artist_text else " ")
        
        if time.monotonic() >= self._pending_seek_deadline:
            self._suppress_seek = True
            try:
                self.seek_adj.set_upper(state.length if state.length > 0 else 1)
                self.seek_adj.set_value(state.position)
                self.elapsed_lbl.set_label(_format_time(state.position))
                self.duration_lbl.set_label(_format_time(state.length))
            finally:
                self._suppress_seek = False

        self.play_btn.set_icon_name("media-playback-pause-symbolic" if state.status == "Playing" else "media-playback-start-symbolic")
        self.shuffle_btn.set_opacity(1.0 if state.shuffle else 0.4)
        
        self._loop_state = state.loop
        self.loop_btn.set_icon_name("media-playlist-repeat-song-symbolic" if state.loop == "Track" else "media-playlist-repeat-symbolic")
        self.loop_btn.set_opacity(0.4 if state.loop == "None" else 1.0)
        
        return GLib.SOURCE_REMOVE


class SliderWindow(Adw.ApplicationWindow):
    def __init__(
        self,
        app: Adw.Application,
        refresh_pool: RefreshPool,
        *,
        volume_submit: FloatSubmitter | None,
        brightness_submit: FloatSubmitter | None,
        sunset_submit: FloatSubmitter | None,
    ) -> None:
        super().__init__(application=app)

        self._rows: list[CompactSliderRow] = []
        self._refresh_source_id: int | None = None
        self._media_refresh_source_id: int | None = None
        self._media_card: MediaCard | None = None

        self.set_default_size(340, -1)
        self.set_resizable(False)
        self.set_show_menubar(False)
        self.set_decorated(False)

        self.connect("close-request", self._on_close_request)
        self.connect("notify::visible", self._on_visible_changed)

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card_box.set_margin_start(14)
        card_box.set_margin_end(14)
        card_box.set_margin_top(14)
        card_box.set_margin_bottom(14)
        card_box.set_vexpand(True)
        card_box.set_valign(Gtk.Align.CENTER)
        main_box.append(card_box)

        if HAS_VOLUME and volume_submit is not None:
            row = CompactSliderRow(
                "",
                "volume",
                0.0,
                100.0,
                1.0,
                get_volume,
                volume_submit,
                refresh_pool,
            )
            self._rows.append(row)
            card_box.append(row)

        if HAS_BRIGHTNESS and brightness_submit is not None:
            row = CompactSliderRow(
                "󰃠",
                "brightness",
                1.0,
                100.0,
                1.0,
                get_brightness,
                brightness_submit,
                refresh_pool,
                post_submit_refresh_grace_seconds=BRIGHTNESS_POST_SUBMIT_REFRESH_GRACE_SECONDS,
            )
            self._rows.append(row)
            card_box.append(row)

        if HAS_SUNSET and sunset_submit is not None:
            row = CompactSliderRow(
                "󰡬",
                "sunset",
                1000.0,
                6000.0,
                50.0,
                get_hyprsunset_state,
                sunset_submit,
                refresh_pool,
            )
            self._rows.append(row)
            card_box.append(row)

        if not self._rows:
            empty = Gtk.Label(label="No supported controls available.")
            empty.add_css_class("value-label")
            empty.set_margin_top(12)
            empty.set_margin_bottom(12)
            card_box.append(empty)

        if HAS_PLAYERCTL:
            self._media_card = MediaCard(refresh_pool)
            main_box.append(self._media_card)

    def refresh_rows(self) -> None:
        for row in self._rows:
            row.refresh_async()

    def stop_refresh_timer(self) -> None:
        if self._refresh_source_id is not None:
            GLib.source_remove(self._refresh_source_id)
            self._refresh_source_id = None
        if self._media_refresh_source_id is not None:
            GLib.source_remove(self._media_refresh_source_id)
            self._media_refresh_source_id = None

    def _ensure_refresh_timer(self) -> None:
        if self._refresh_source_id is None and self._rows:
            self._refresh_source_id = GLib.timeout_add_seconds(
                LIVE_REFRESH_INTERVAL_SECONDS,
                self._on_refresh_timeout,
            )
        if HAS_PLAYERCTL and self._media_refresh_source_id is None and self._media_card is not None:
            self._media_refresh_source_id = GLib.timeout_add(
                int(MEDIA_REFRESH_INTERVAL_SECONDS * 1000),
                self._on_media_refresh_timeout,
            )

    def _on_refresh_timeout(self) -> bool:
        if not self.is_visible():
            self._refresh_source_id = None
            return GLib.SOURCE_REMOVE

        self.refresh_rows()
        return GLib.SOURCE_CONTINUE

    def _on_media_refresh_timeout(self) -> bool:
        if not self.is_visible():
            self._media_refresh_source_id = None
            return GLib.SOURCE_REMOVE

        if self._media_card:
            self._media_card.refresh_async()
        return GLib.SOURCE_CONTINUE

    def _on_visible_changed(self, _window: Gtk.Widget, _pspec: object) -> None:
        if self.is_visible():
            self._ensure_refresh_timer()
            self.refresh_rows()
            if self._media_card:
                self._media_card.refresh_async()
        else:
            self.stop_refresh_timer()

    def _on_close_request(self, _window: Gtk.Window) -> bool:
        self.set_visible(False)
        return True

    def _on_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
    ) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.set_visible(False)
            return True
        return False


CSS: Final = """
window {
    background-color: alpha(@window_bg_color, 0.95);
    border-radius: 8px;
}

.slider-row {
    background-color: transparent;
    padding: 10px 12px;
}

scale.pill-scale trough {
    min-height: 16px;
    border-radius: 8px;
    background-color: rgba(255, 255, 255, 0.08);
}

scale.pill-scale highlight {
    min-height: 16px;
    border-radius: 8px;
}

scale.pill-scale slider {
    min-width: 0px;
    min-height: 0px;
    margin: 0px;
    padding: 0px;
    background: transparent;
    border: none;
    box-shadow: none;
}

scale.volume highlight { background-color: #89b4fa; }
scale.brightness highlight { background-color: #f9e2af; }
scale.sunset highlight { background-color: #fab387; }
scale.media-scale highlight { background-color: #cba6f7; }

.icon-volume { color: #89b4fa; }
.icon-brightness { color: #f9e2af; }
.icon-sunset { color: #fab387; }

.icon-label {
    font-size: 18px;
    font-family: "Symbols Nerd Font", "JetBrainsMono Nerd Font", monospace;
}

.value-label {
    font-size: 14px;
    font-weight: 700;
    opacity: 0.8;
    font-family: "JetBrainsMono Nerd Font", monospace;
    font-variant-numeric: tabular-nums;
}

.media-title {
    font-size: 15px;
    font-family: sans-serif;
}

.media-artist {
    font-size: 13px;
    opacity: 0.8;
    font-family: sans-serif;
}

.media-time {
    font-size: 12px;
    opacity: 0.7;
    font-family: "JetBrainsMono Nerd Font", monospace;
    font-variant-numeric: tabular-nums;
}

.media-btn {
    min-width: 36px;
    min-height: 36px;
    border-radius: 18px;
    padding: 0;
}
"""


class SliderApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

        self._window: SliderWindow | None = None
        self._refresh_pool: RefreshPool | None = None
        self._volume_worker: LatestValueWorker | None = None
        self._local_brightness_worker: LatestValueWorker | None = None
        self._sunset_controller: HyprsunsetController | None = None

    def _submit_brightness(self, value: float) -> None:
        if self._local_brightness_worker is not None:
            self._local_brightness_worker.submit(value)
        if DDC_MANAGER is not None:
            DDC_MANAGER.submit(value)

    @override
    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        self.hold()

        if DDC_MANAGER is not None:
            DDC_MANAGER.start()

        if LOG.isEnabledFor(logging.DEBUG):
            if (name := _preferred_backlight_name()) is not None:
                LOG.debug("Selected backlight device: %s", name)

        self._refresh_pool = RefreshPool(max_workers=3)

        self._volume_worker = LatestValueWorker("volume", apply_volume) if HAS_VOLUME else None
        self._local_brightness_worker = (
            LatestValueWorker("local-brightness", apply_local_brightness)
            if HAS_LOCAL_BRIGHTNESS
            else None
        )
        self._sunset_controller = HyprsunsetController() if HAS_SUNSET else None

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_args: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])

        if (style_manager := Adw.StyleManager.get_default()) is not None:
            style_manager.set_color_scheme(Adw.ColorScheme.PREFER_DARK)

        css_provider = Gtk.CssProvider()
        css_provider.load_from_string(CSS)

        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display,
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

        self._window = SliderWindow(
            self,
            self._refresh_pool,
            volume_submit=self._volume_worker.submit if self._volume_worker else None,
            brightness_submit=self._submit_brightness if HAS_BRIGHTNESS else None,
            sunset_submit=self._sunset_controller.submit if self._sunset_controller else None,
        )
        self._window.set_visible(False)

    @override
    def do_activate(self) -> None:
        if self._window is None:
            return

        self._window.refresh_rows()
        if self._window._media_card:
            self._window._media_card.refresh_async()
        self._window.present()

    @override
    def do_shutdown(self) -> None:
        if self._window is not None:
            self._window.stop_refresh_timer()

        if self._refresh_pool is not None:
            self._refresh_pool.shutdown()

        if self._sunset_controller is not None:
            self._sunset_controller.stop()

        if self._local_brightness_worker is not None:
            self._local_brightness_worker.stop()

        if DDC_MANAGER is not None:
            DDC_MANAGER.stop()

        if self._volume_worker is not None:
            self._volume_worker.stop()

        Adw.Application.do_shutdown(self)


if __name__ == "__main__":
    app = SliderApp()
    raise SystemExit(app.run(sys.argv))
