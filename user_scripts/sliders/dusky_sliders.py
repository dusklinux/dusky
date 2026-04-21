#!/usr/bin/env python3
"""
Master Slider Widget for Hyprland (Dusky Sliders)
Native GTK4 + Libadwaita custom card implementation.
Tuned for current Arch Linux + Python 3.14.

Includes hybrid sysfs + zero-lag async ddcutil support for external monitors.
Features detached execution queues for decoupled, zero-latency local panel updates.
"""

from __future__ import annotations

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
from pathlib import Path

try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk
except (ImportError, ValueError) as exc:
    raise SystemExit(f"Failed to load GTK4/Libadwaita: {exc}")

APP_ID = "org.dusky.sliders"

if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.WARNING,
        format=f"{APP_ID}: %(levelname)s: %(message)s",
    )

LOG = logging.getLogger(APP_ID)

COMMAND_ENV = dict(os.environ)
COMMAND_ENV["LC_ALL"] = "C"
COMMAND_ENV["LANG"] = "C"

type CommandArg = str | os.PathLike[str]

DEFAULT_SUNSET = 4500.0

QUERY_TIMEOUT = 1.0
CONTROL_TIMEOUT = 2.0
SUNSET_READY_TIMEOUT = 3.0
SUNSET_FALLBACK_READY_TIMEOUT = 1.5
LIVE_REFRESH_INTERVAL_SECONDS = 2
BRIGHTNESS_POST_SUBMIT_REFRESH_GRACE_SECONDS = max(1.5, QUERY_TIMEOUT + 0.5)


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def parse_float(text: str) -> float | None:
    try:
        value = float(text.strip())
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def snap_to_step(value: float, lower: float, upper: float, step: float) -> float:
    if step <= 0:
        return clamp(value, lower, upper)

    scaled = (value - lower) / step
    snapped = lower + math.floor(scaled + 0.5 + 1e-12) * step
    return round(clamp(snapped, lower, upper), 10)


def start_daemon_thread(name: str, target: Callable[..., None], *args: object) -> None:
    threading.Thread(target=target, args=args, daemon=True, name=name).start()


def run_command(
    args: Sequence[CommandArg],
    *,
    timeout: float,
    capture_stdout: bool = False,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            [os.fspath(arg) for arg in args],
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            env=COMMAND_ENV,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _resolve_state_dir() -> Path | None:
    candidates: list[Path] = []
    seen: set[str] = set()

    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    if xdg_state_home:
        path = Path(xdg_state_home)
        if path.is_absolute():
            candidates.append(path / APP_ID)

    try:
        candidates.append(Path.home() / ".local" / "state" / APP_ID)
    except (OSError, RuntimeError):
        pass

    xdg_runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime_dir:
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


STATE_DIR = _resolve_state_dir()
if STATE_DIR is None:
    LOG.warning("Could not resolve a writable state directory. Settings will not be persisted.")

STATE_FILE = None if STATE_DIR is None else STATE_DIR / "hyprsunset_state.txt"
DDCUTIL_CACHE_FILE = None if STATE_DIR is None else STATE_DIR / "ddcutil_buses.json"

WPCTL = shutil.which("wpctl")
BRIGHTNESSCTL = shutil.which("brightnessctl")
DDCUTIL = shutil.which("ddcutil")
HYPRCTL = shutil.which("hyprctl")
HYPRSUNSET = shutil.which("hyprsunset")
PGREP = shutil.which("pgrep")
SYSTEMCTL = shutil.which("systemctl")


# --- BACKLIGHT DISCOVERY ---
_BACKLIGHT_DISCOVERY_TTL_SECONDS = 5.0
_backlight_discovery_lock = threading.Lock()
_backlight_candidates_cache: tuple[float, tuple[tuple[int, int, Path], ...]] | None = None

def _sysfs_backlight_candidates() -> tuple[tuple[int, int, Path], ...]:
    global _backlight_candidates_cache

    now = time.monotonic()
    with _backlight_discovery_lock:
        cached = _backlight_candidates_cache
        if cached is not None and (now - cached[0]) < _BACKLIGHT_DISCOVERY_TTL_SECONDS:
            return cached[1]

    base = Path("/sys/class/backlight")
    if not base.is_dir():
        result: tuple[tuple[int, int, Path], ...] = ()
    else:
        try:
            entries = tuple(base.iterdir())
        except OSError:
            entries = ()

        candidates: list[tuple[int, int, Path]] = []

        for entry in entries:
            if not entry.is_dir():
                continue

            brightness_path = entry / "brightness"
            max_brightness_path = entry / "max_brightness"
            if not brightness_path.is_file() or not max_brightness_path.is_file():
                continue

            try:
                max_value = int(max_brightness_path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                continue

            if max_value <= 0:
                continue

            name = entry.name.lower()
            priority = 0
            if name.startswith("intel_backlight"):
                priority = 400
            elif name.startswith("amdgpu_bl"):
                priority = 350
            elif name.startswith("nvidia"):
                priority = 300
            elif name.startswith("ddcci"):
                priority = 250
            elif "backlight" in name:
                priority = 200
            elif name.startswith("acpi_video"):
                priority = 100

            candidates.append((priority, max_value, entry))

        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        result = tuple(candidates)

    with _backlight_discovery_lock:
        _backlight_candidates_cache = (now, result)
    return result


def _best_sysfs_backlight(*, require_writable: bool = False) -> tuple[Path, Path] | None:
    for _, _, entry in _sysfs_backlight_candidates():
        brightness_path = entry / "brightness"
        max_brightness_path = entry / "max_brightness"

        if require_writable and not os.access(brightness_path, os.W_OK):
            continue

        return brightness_path, max_brightness_path
    return None


def _preferred_sysfs_backlight() -> tuple[Path, Path] | None:
    return _best_sysfs_backlight(require_writable=True) or _best_sysfs_backlight()


def _preferred_backlight_name() -> str | None:
    sysfs_paths = _preferred_sysfs_backlight()
    if sysfs_paths is None:
        return None
    return sysfs_paths[0].parent.name


def _brightnessctl_command_base() -> list[str] | None:
    if BRIGHTNESSCTL is None:
        return None

    args = [BRIGHTNESSCTL, "-c", "backlight"]
    if (device_name := _preferred_backlight_name()) is not None:
        args.extend(["-d", device_name])
    return args


def _has_writable_sysfs_backlight() -> bool:
    return _best_sysfs_backlight(require_writable=True) is not None


def _has_hyprland_session() -> bool:
    return bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))


# Global capabilities flag encompasses both internal logic and ddcutil availability
HAS_VOLUME = WPCTL is not None
HAS_BRIGHTNESS = (_preferred_sysfs_backlight() is not None and (
    BRIGHTNESSCTL is not None or _has_writable_sysfs_backlight()
)) or (DDCUTIL is not None)
HAS_SUNSET = HYPRCTL is not None and HYPRSUNSET is not None and _has_hyprland_session()


# --- DDCUTIL ASYNC BACKGROUND INTEGRATION ---
_ddcutil_buses: list[int] = []
_ddcutil_lock = threading.Lock()
_ddcutil_last_known_brightness: float = 50.0
_ddcutil_user_overridden: bool = False

def _load_ddcutil_buses() -> None:
    global _ddcutil_buses
    if DDCUTIL_CACHE_FILE and DDCUTIL_CACHE_FILE.is_file():
        try:
            data = json.loads(DDCUTIL_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                with _ddcutil_lock:
                    _ddcutil_buses = [int(x) for x in data]
        except Exception:
            pass

    if DDCUTIL:
        start_daemon_thread("ddcutil-detect", _refresh_ddcutil_buses_worker)


def _refresh_ddcutil_buses_worker() -> None:
    if not DDCUTIL:
        return
    
    # Detect displays. Using timeout=15 as some I2C buses can be sluggish.
    result = run_command([DDCUTIL, "detect", "-t"], timeout=15.0, capture_stdout=True)
    if not result or result.returncode != 0:
        return

    buses = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("I2C bus:"):
            parts = line.split("i2c-")
            if len(parts) == 2 and parts[1].isdigit():
                buses.append(int(parts[1]))

    with _ddcutil_lock:
        _ddcutil_buses.clear()
        _ddcutil_buses.extend(buses)

    if buses:
        # Seed initial brightness from the first valid display
        vcp_res = run_command(
            [DDCUTIL, "getvcp", "10", "-t", "--bus", str(buses[0])], 
            timeout=5.0, 
            capture_stdout=True
        )
        if vcp_res and vcp_res.returncode == 0:
            parts = vcp_res.stdout.strip().split()
            if len(parts) >= 4 and parts[3].isdigit():
                with _ddcutil_lock:
                    # Prevent race where a fresh user input is stomped by the delayed detection
                    if not _ddcutil_user_overridden:
                        global _ddcutil_last_known_brightness
                        _ddcutil_last_known_brightness = float(parts[3])

    if DDCUTIL_CACHE_FILE:
        try:
            DDCUTIL_CACHE_FILE.write_text(json.dumps(buses), encoding="utf-8")
        except OSError:
            pass


def apply_ddcutil_brightness(value: float) -> None:
    global _ddcutil_last_known_brightness
    global _ddcutil_user_overridden
    
    with _ddcutil_lock:
        _ddcutil_user_overridden = True
        _ddcutil_last_known_brightness = float(int(clamp(round(value), 1, 100)))
        buses = list(_ddcutil_buses)
        
    if not buses or not DDCUTIL:
        return
        
    percent = int(_ddcutil_last_known_brightness)
    
    # Executed within a detached executor queue to prevent Head-of-Line blocking 
    # of the microsecond-latency sysfs local panel writes.
    for bus in buses:
        run_command(
            [DDCUTIL, "setvcp", "10", str(percent), "--bus", str(bus), "--noverify"], 
            timeout=3.0
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

    volume = int(clamp(round(value), 0, 100))

    result = run_command(
        [WPCTL, "set-volume", "@DEFAULT_AUDIO_SINK@", f"{volume}%"],
        timeout=CONTROL_TIMEOUT,
    )
    if result is None or result.returncode != 0:
        LOG.warning("Failed to set volume to %s%%", volume)
        return

    if volume > 0:
        result = run_command(
            [WPCTL, "set-mute", "@DEFAULT_AUDIO_SINK@", "0"],
            timeout=CONTROL_TIMEOUT,
        )
        if result is None or result.returncode != 0:
            LOG.warning("Failed to unmute audio sink after setting volume")


def _read_sysfs_brightness() -> float | None:
    sysfs_paths = _preferred_sysfs_backlight()
    if sysfs_paths is None:
        return None

    brightness_path, max_brightness_path = sysfs_paths
    
    actual_path = brightness_path.with_name("actual_brightness")
    read_path = actual_path if actual_path.is_file() else brightness_path

    try:
        current = parse_float(read_path.read_text(encoding="utf-8"))
        maximum = parse_float(max_brightness_path.read_text(encoding="utf-8"))
    except OSError:
        return None

    if current is None or maximum is None or maximum <= 0:
        return None

    value = clamp((current / maximum) * 100.0, 0.0, 100.0)
    LOG.debug(
        "Brightness read via sysfs (%s, source=%s): %.3f%%", 
        brightness_path.parent.name, 
        read_path.name, 
        value
    )
    return value


def _write_sysfs_brightness(value: float) -> bool:
    sysfs_paths = _best_sysfs_backlight(require_writable=True)
    if sysfs_paths is None:
        return False

    brightness_path, max_brightness_path = sysfs_paths
    try:
        maximum_text = max_brightness_path.read_text(encoding="utf-8")
        maximum = int(maximum_text.strip())
    except (OSError, ValueError):
        return False

    if maximum <= 0:
        return False

    percent = int(clamp(round(value), 1, 100))
    raw_value = int(round((percent / 100.0) * maximum))
    raw_value = max(1, min(maximum, raw_value))

    try:
        brightness_path.write_text(f"{raw_value}\n", encoding="utf-8")
    except OSError:
        return False

    LOG.debug(
        "Brightness written via sysfs (%s): %s%% -> raw=%s/%s",
        brightness_path.parent.name,
        percent,
        raw_value,
        maximum,
    )
    return True


def get_brightness() -> float | None:
    if (value := _read_sysfs_brightness()) is not None:
        return value

    if (base_cmd := _brightnessctl_command_base()) is not None:
        result = run_command(
            [*base_cmd, "-m"],
            timeout=QUERY_TIMEOUT,
            capture_stdout=True,
        )
        if result is not None and result.returncode == 0:
            lines = result.stdout.splitlines()
            if lines and len(parts := lines[0].split(",")) >= 5:
                percent_text = parts[4].rstrip("%")
                if (value := parse_float(percent_text)) is not None:
                    value = clamp(value, 0.0, 100.0)
                    LOG.debug("Brightness read via brightnessctl: %.3f%%", value)
                    return value

    # Desktop Fallback: If sysfs failed, check if we have DDC monitors.
    with _ddcutil_lock:
        has_ddc = bool(_ddcutil_buses)
        last_known = _ddcutil_last_known_brightness

    if has_ddc:
        return last_known

    return None


def apply_local_brightness(value: float) -> None:
    """Dedicated fast-path apply logic strictly for local sysfs/ACPI panels."""
    brightness = int(clamp(round(value), 1, 100))
    success = _write_sysfs_brightness(brightness)

    if not success and (base_cmd := _brightnessctl_command_base()) is not None:
        result = run_command(
            [*base_cmd, "-q", "set", f"{brightness}%"],
            timeout=CONTROL_TIMEOUT,
        )
        if result is not None and result.returncode == 0:
            LOG.debug("Brightness written via brightnessctl: %s%%", brightness)
            success = True

    if not success:
        LOG.debug("Local sysfs/brightnessctl apply failed or not applicable.")


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


def _fsync_directory(path: Path) -> None:
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


def atomic_write_state(value: float) -> bool:
    if STATE_FILE is None:
        return False

    temp_path: Path | None = None

    try:
        STATE_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

        fd, raw_temp_path = tempfile.mkstemp(
            dir=STATE_FILE.parent,
            prefix=".sunset_",
            suffix=".tmp",
            text=True,
        )
        temp_path = Path(raw_temp_path)

        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{int(clamp(round(value), 1000, 6000))}\n")
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temp_path, STATE_FILE)
        _fsync_directory(STATE_FILE.parent)
        temp_path = None
        return True
    except OSError as exc:
        LOG.warning("Failed to write hyprsunset state file: %s", exc)
        return False
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass


class LatestValueExecutor:
    def __init__(self, name: str, apply_func: Callable[[float], None]) -> None:
        self._name = name
        self._apply_func = apply_func
        self._condition = threading.Condition()
        self._pending: float | None = None
        self._running = True
        self._busy = False
        self._thread = threading.Thread(
            target=self._worker,
            daemon=True,
            name=f"{name}-worker",
        )
        self._thread.start()

    def submit(self, value: float) -> None:
        with self._condition:
            if not self._running:
                return
            self._pending = value
            self._condition.notify()

    def flush(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._running and (self._busy or self._pending is not None):
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)
        return True

    def stop(self, timeout: float = 2.0) -> None:
        self.flush(timeout)
        with self._condition:
            self._running = False
            self._pending = None # Drops jobs submitted during flush block
            self._condition.notify_all()
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            LOG.warning("%s worker did not stop within %.1fs", self._name, timeout)

    def _worker(self) -> None:
        while True:
            with self._condition:
                while self._running and self._pending is None:
                    self._condition.wait()

                if not self._running and self._pending is None:
                    return

                value = self._pending
                self._pending = None
                self._busy = True

            try:
                if value is not None:
                    self._apply_func(value)
            except Exception:
                LOG.exception("Unhandled exception in executor worker")
            finally:
                with self._condition:
                    self._busy = False
                    self._condition.notify_all()


class DebouncedStateWriter:
    def __init__(self, delay_seconds: float = 0.5) -> None:
        self._delay_seconds = delay_seconds
        self._condition = threading.Condition()
        self._latest = DEFAULT_SUNSET
        self._deadline: float | None = None
        self._pending = False
        self._busy = False
        self._running = True
        self._thread = threading.Thread(
            target=self._worker,
            daemon=True,
            name="sunset-state-writer",
        )
        self._thread.start()

    def schedule(self, value: float) -> None:
        with self._condition:
            if not self._running:
                return

            self._latest = float(int(clamp(round(value), 1000, 6000)))
            self._deadline = time.monotonic() + self._delay_seconds
            self._pending = True
            self._condition.notify()

    def flush(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout

        with self._condition:
            if self._pending:
                self._deadline = time.monotonic()
                self._condition.notify()

            while self._running and (self._pending or self._busy):
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)

        return True

    def stop(self, timeout: float = 2.0) -> None:
        self.flush(timeout)
        with self._condition:
            self._running = False
            self._condition.notify_all()
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            LOG.warning("sunset state writer did not stop within %.1fs", timeout)

    def _worker(self) -> None:
        while True:
            with self._condition:
                while True:
                    if not self._running and not self._pending:
                        return

                    if not self._pending:
                        self._condition.wait()
                        continue

                    wait_time = 0.0
                    if self._deadline is not None:
                        wait_time = self._deadline - time.monotonic()

                    if wait_time > 0:
                        self._condition.wait(wait_time)
                        continue

                    value = self._latest
                    self._pending = False
                    self._deadline = None
                    self._busy = True
                    break

            try:
                atomic_write_state(value)
            except Exception:
                LOG.exception("Unhandled exception while writing hyprsunset state")
            finally:
                with self._condition:
                    self._busy = False
                    self._condition.notify_all()


class HyprsunsetController:
    def __init__(self) -> None:
        self._state_writer = DebouncedStateWriter(delay_seconds=0.5)
        self._executor = LatestValueExecutor("sunset", self._apply)
        self._ready = threading.Event()
        self._process_lock = threading.Lock()
        self._fallback_process: subprocess.Popen[bytes] | None = None

    def submit(self, value: float) -> None:
        rounded = float(int(clamp(round(value), 1000, 6000)))
        self._executor.submit(rounded)

    def flush(self, timeout: float = 3.0) -> None:
        self._executor.flush(timeout)
        self._state_writer.flush(timeout)

    def stop(self, timeout: float = 3.0) -> None:
        self._executor.stop(timeout)
        self._state_writer.stop(timeout)

    def _apply(self, value: float) -> None:
        target = int(clamp(round(value), 1000, 6000))

        if self._ready.is_set() and self._send_temperature(target):
            self._state_writer.schedule(float(target))
            return

        self._ready.clear()
        self._start_daemon()

        if self._wait_until_applied(target, SUNSET_READY_TIMEOUT):
            return

        if not self._is_hyprsunset_running():
            self._spawn_fallback_process()
            if self._wait_until_applied(target, SUNSET_FALLBACK_READY_TIMEOUT):
                return

        LOG.warning("Failed to apply hyprsunset temperature: %s", target)

    def _wait_until_applied(self, target: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._send_temperature(target):
                self._ready.set()
                self._state_writer.schedule(float(target))
                return True
            time.sleep(0.10)
        return False

    def _send_temperature(self, value: int) -> bool:
        if HYPRCTL is None:
            return False

        result = run_command(
            [HYPRCTL, "hyprsunset", "temperature", str(value)],
            timeout=QUERY_TIMEOUT,
        )
        return result is not None and result.returncode == 0

    def _start_daemon(self) -> None:
        if SYSTEMCTL is not None:
            result = run_command(
                [SYSTEMCTL, "--user", "start", "hyprsunset.service"],
                timeout=CONTROL_TIMEOUT,
            )
            if result is not None and result.returncode == 0:
                return

        if self._is_hyprsunset_running():
            return

        self._spawn_fallback_process()

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

    def _spawn_fallback_process(self) -> None:
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
                    [HYPRSUNSET],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                )
            except OSError as exc:
                LOG.warning("Failed to start hyprsunset fallback process: %s", exc)
                return

            self._fallback_process = new_proc

        start_daemon_thread("hyprsunset-reaper", self._reap_fallback_process, new_proc)

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


class CompactSliderRow(Gtk.Box):
    def __init__(
        self,
        icon_text: str,
        css_class: str,
        min_value: float,
        max_value: float,
        step: float,
        fetch_cb: Callable[[], float | None],
        submit_cb: Callable[[float], None],
        *,
        post_submit_refresh_grace_seconds: float = 0.0,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)

        self._fetch_cb = fetch_cb
        self._submit_cb = submit_cb
        self._suppress_apply = False
        self._refresh_token = 0
        self._user_revision = 0
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
            page_increment=step * 10,
        )

        self.scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            adjustment=self.adjustment,
        )
        self.scale.set_hexpand(True)
        self.scale.set_draw_value(False)
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

    def _clear_pending_local(self) -> None:
        self._pending_local_value = None
        self._pending_local_deadline = 0.0

    def _pending_local_tolerance(self) -> float:
        return max(self.adjustment.get_step_increment() * 0.5, 1e-9)

    def refresh_async(self) -> None:
        if (
            self._pending_local_value is not None
            and time.monotonic() < self._pending_local_deadline
        ):
            return

        self._refresh_token += 1
        token = self._refresh_token
        user_revision = self._user_revision
        start_daemon_thread(
            f"refresh-{id(self)}",
            self._refresh_worker,
            token,
            user_revision,
        )

    def _refresh_worker(self, token: int, user_revision: int) -> None:
        try:
            value = self._fetch_cb()
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
        if token != self._refresh_token or user_revision != self._user_revision:
            return GLib.SOURCE_REMOVE

        if value is None:
            self.scale.set_sensitive(False)
            self.value_label.set_label("…")
            self._has_value = False
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

        value = snapped
        self.value_label.set_label(str(int(round(value))))

        if self._suppress_apply:
            return

        if self._post_submit_refresh_grace_seconds > 0.0:
            self._pending_local_value = value
            self._pending_local_deadline = (
                time.monotonic() + self._post_submit_refresh_grace_seconds
            )
        else:
            self._clear_pending_local()

        self._user_revision += 1
        self._submit_cb(value)


class SliderWindow(Adw.ApplicationWindow):
    def __init__(
        self,
        app: Adw.Application,
        *,
        volume_submit: Callable[[float], None] | None,
        brightness_submit: Callable[[float], None] | None,
        sunset_submit: Callable[[float], None] | None,
    ) -> None:
        super().__init__(application=app)

        self._rows: list[CompactSliderRow] = []
        self._refresh_source_id: int | None = None

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
                0,
                100,
                1,
                get_volume,
                volume_submit,
            )
            self._rows.append(row)
            card_box.append(row)

        if HAS_BRIGHTNESS and brightness_submit is not None:
            row = CompactSliderRow(
                "󰃠",
                "brightness",
                1,
                100,
                1,
                get_brightness,
                brightness_submit,
                post_submit_refresh_grace_seconds=BRIGHTNESS_POST_SUBMIT_REFRESH_GRACE_SECONDS,
            )
            self._rows.append(row)
            card_box.append(row)

        if HAS_SUNSET and sunset_submit is not None:
            row = CompactSliderRow(
                "󰡬",
                "sunset",
                1000,
                6000,
                50,
                get_hyprsunset_state,
                sunset_submit,
            )
            self._rows.append(row)
            card_box.append(row)

        if not self._rows:
            empty = Gtk.Label(label="No supported controls available.")
            empty.add_css_class("value-label")
            empty.set_margin_top(12)
            empty.set_margin_bottom(12)
            card_box.append(empty)

    def refresh_rows(self) -> None:
        for row in self._rows:
            row.refresh_async()

    def stop_refresh_timer(self) -> None:
        if self._refresh_source_id is not None:
            GLib.source_remove(self._refresh_source_id)
            self._refresh_source_id = None

    def _ensure_refresh_timer(self) -> None:
        if self._refresh_source_id is None and self._rows:
            self._refresh_source_id = GLib.timeout_add_seconds(
                LIVE_REFRESH_INTERVAL_SECONDS,
                self._on_refresh_timeout,
            )

    def _on_refresh_timeout(self) -> bool:
        if not self.is_visible():
            self._refresh_source_id = None
            return GLib.SOURCE_REMOVE

        self.refresh_rows()
        return GLib.SOURCE_CONTINUE

    def _on_visible_changed(self, _window: Gtk.Widget, _pspec: object) -> None:
        if self.is_visible():
            self._ensure_refresh_timer()
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


class SliderApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

        self._window: SliderWindow | None = None
        
        self._volume_executor = (
            LatestValueExecutor("volume", apply_volume) if HAS_VOLUME else None
        )
        
        # Fork the brightness pipeline into two Detached Queues to prevent Head-of-Line Blocking
        self._brightness_executor = (
            LatestValueExecutor("local_brightness", apply_local_brightness) if HAS_BRIGHTNESS else None
        )
        self._ddc_executor = (
            LatestValueExecutor("ddc_brightness", apply_ddcutil_brightness) if DDCUTIL else None
        )
        
        self._sunset_controller = HyprsunsetController() if HAS_SUNSET else None

    def _submit_brightness_combo(self, value: float) -> None:
        """Dispatches payload to decoupled execution threads instantly."""
        if self._brightness_executor is not None:
            self._brightness_executor.submit(value)
        if self._ddc_executor is not None:
            self._ddc_executor.submit(value)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        self.hold()
        
        # Initialize async DDC monitor detection
        _load_ddcutil_buses()

        if LOG.isEnabledFor(logging.DEBUG):
            if (name := _preferred_backlight_name()) is not None:
                LOG.debug("Selected backlight device: %s", name)

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_args: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])

        style_manager = Adw.StyleManager.get_default()
        style_manager.set_color_scheme(Adw.ColorScheme.PREFER_DARK)

        css_provider = Gtk.CssProvider()
        css_provider.load_from_string(
            """
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
            """
        )

        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display,
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

        self._window = SliderWindow(
            self,
            volume_submit=self._volume_executor.submit if self._volume_executor else None,
            brightness_submit=self._submit_brightness_combo if HAS_BRIGHTNESS else None,
            sunset_submit=self._sunset_controller.submit if self._sunset_controller else None,
        )
        self._window.set_visible(False)

    def do_activate(self) -> None:
        if self._window is None:
            return

        self._window.refresh_rows()
        self._window.present()

    def do_shutdown(self) -> None:
        if self._window is not None:
            self._window.stop_refresh_timer()

        if self._sunset_controller is not None:
            self._sunset_controller.stop()

        if self._brightness_executor is not None:
            self._brightness_executor.stop()
            
        if self._ddc_executor is not None:
            self._ddc_executor.stop()

        if self._volume_executor is not None:
            self._volume_executor.stop()

        Adw.Application.do_shutdown(self)


if __name__ == "__main__":
    app = SliderApp()
    raise SystemExit(app.run(sys.argv))
