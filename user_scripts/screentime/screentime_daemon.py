#!/usr/bin/env python3
"""Track focused Hyprland applications while the session is active.

Elapsed time uses a monotonic clock. Wayland idle notifications observe input
rather than changes to window titles. History retains the existing JSON schema.
"""

import fcntl
import json
import os
import signal
import sys
import tempfile
import time
from datetime import datetime, timedelta
from typing import Any

from desktop_resolver import DesktopResolver
from idle_monitor import IdleMonitor
from screentime_common import CONFIG_FILE, DATA_DIR, DATA_FILE, HyprlandIPC, validate_data, valid_number

DEFAULT_CONFIG = frozendict({
    "enabled": True,
    "save_interval_seconds": 5,
    "idle_threshold_seconds": 300,
    "ignore_classes": ("hyprlock", "swaylock", "gdm", "sddm"),
})
LOCKSCREEN_NAMES = {"hyprlock", "swaylock", "swaylock-effects", "gtklock", "waylock"}


class ScreentimeDaemon:
    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        # A second writer must not silently replace a running daemon's history.
        self._instance_lock = (DATA_DIR / "daemon.lock").open("a")
        try:
            fcntl.flock(self._instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._instance_lock.close()
            raise RuntimeError("a screentime daemon is already running") from None
        self.config = dict(DEFAULT_CONFIG)
        self.data: dict[str, dict[str, Any]] = {}
        self.resolver = DesktopResolver()
        self.ipc = HyprlandIPC()
        self.last_save_time = time.monotonic()
        self.last_window_key: tuple[str, str] | None = None
        self.running = True
        self._dirty = False
        self._config_stamp: tuple[int, int, int] | None = None
        self._idle_monitor: IdleMonitor | None = None
        self._idle_retry = 0.0
        self._dbus_bus: Any = None
        self._session_path: str | None = None
        self._dbus_retry = 0.0
        try:
            self._load_config()
            self._load_data()
        except BaseException:
            self._instance_lock.close()
            raise

    def _load_config(self) -> None:
        try:
            if not CONFIG_FILE.exists():
                CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=4) + "\n", encoding="utf-8")
            stat = CONFIG_FILE.stat()
            stamp = stat.st_mtime_ns, stat.st_size, stat.st_ino
            if stamp == self._config_stamp:
                return
            contents = CONFIG_FILE.read_text(encoding="utf-8")
            self._config_stamp = stamp
            user_config = json.loads(contents)
            if not isinstance(user_config, dict):
                raise ValueError("configuration must be an object")
            config = dict(DEFAULT_CONFIG)
            config["ignore_classes"] = list(config["ignore_classes"])
            config.update({key: value for key, value in user_config.items() if key in config})
            if type(config["enabled"]) is not bool:
                raise ValueError("enabled must be a boolean")
            for key in ("save_interval_seconds", "idle_threshold_seconds"):
                value = config[key]
                if not valid_number(value) or value <= 0:
                    raise ValueError(f"{key} must be positive and finite")
            if config["idle_threshold_seconds"] > 0xFFFFFFFF / 1000:
                raise ValueError("idle threshold exceeds the Wayland timeout range")
            ignored = config["ignore_classes"]
            if not isinstance(ignored, list) or not all(isinstance(cls, str) for cls in ignored):
                raise ValueError("ignore_classes must be an array of strings")
            config["ignore_classes"] = [cls.lower() for cls in ignored]
            if config["idle_threshold_seconds"] != self.config["idle_threshold_seconds"] and self._idle_monitor:
                self._idle_monitor.close()
                self._idle_monitor = None
                self._idle_retry = 0.0
            self.config = config
        except (OSError, ValueError) as error:
            print(f"[!] Keeping previous configuration: {error}", file=sys.stderr)

    def _load_data(self) -> None:
        if DATA_FILE.exists():
            # Fail visibly on corrupt data; never replace it with empty history.
            self.data = validate_data(json.loads(DATA_FILE.read_text(encoding="utf-8")))

    def _save_data_atomic(self) -> None:
        if not self._dirty:
            self.last_save_time = time.monotonic()
            return
        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=DATA_DIR, prefix=".screentime-", delete=False) as output:
                temp_path = output.name
                json.dump(self.data, output, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_path, DATA_FILE)
            # Persist the rename as well as the file contents across power loss.
            directory_fd = os.open(DATA_DIR, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            self._dirty = False
        except (OSError, ValueError) as error:
            print(f"[!] Error saving history: {error}", file=sys.stderr)
        finally:
            self.last_save_time = time.monotonic()
            if temp_path:
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass
                except OSError as error:
                    print(f"[!] Cannot remove save temporary file: {error}", file=sys.stderr)

    def get_active_window(self) -> dict[str, Any] | None:
        window = self.ipc.query("j/activewindow")
        if isinstance(window, dict) and isinstance(window.get("class"), str) and isinstance(window.get("title", ""), str):
            return window if window["class"].strip() else None
        return None

    def is_dpms_off(self) -> bool:
        monitors = self.ipc.query("j/monitors")
        # No usable outputs or an IPC failure means no trackable screen.
        return not isinstance(monitors, list) or not any(
            isinstance(monitor, dict) and monitor.get("dpmsStatus", False) and not monitor.get("disabled", False)
            for monitor in monitors
        )

    def is_locked(self) -> bool:
        # Lockers may not report LockedHint; inspect only this user's processes.
        uid = os.getuid()
        with os.scandir("/proc") as entries:
            for entry in entries:
                if not entry.name.isdigit():
                    continue
                try:
                    if entry.stat().st_uid != uid:
                        continue
                    with open(f"/proc/{entry.name}/comm", encoding="utf-8") as comm:
                        if comm.read().strip() in LOCKSCREEN_NAMES:
                            return True
                except OSError:
                    continue
        return False

    def is_session_inactive(self) -> bool:
        now = time.monotonic()
        if now < self._dbus_retry:
            return False
        try:
            from gi.repository import Gio, GLib
        except ImportError:
            self._dbus_retry = float("inf")
            print("[!] GIO bindings unavailable; using compositor idle and locker status", file=sys.stderr)
            return False
        try:
            if self._session_path is None:
                self._dbus_bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
                session_id = os.environ.get("XDG_SESSION_ID")
                if session_id:
                    path, = self._dbus_bus.call_sync(
                        "org.freedesktop.login1", "/org/freedesktop/login1",
                        "org.freedesktop.login1.Manager", "GetSession",
                        GLib.Variant("(s)", (session_id,)), GLib.VariantType.new("(o)"),
                        Gio.DBusCallFlags.NONE, 500, None,
                    ).unpack()
                else:
                    # User services live outside login sessions; use the user's graphical session.
                    display, = self._dbus_bus.call_sync(
                        "org.freedesktop.login1", f"/org/freedesktop/login1/user/_{os.getuid()}",
                        "org.freedesktop.DBus.Properties", "Get",
                        GLib.Variant("(ss)", ("org.freedesktop.login1.User", "Display")),
                        GLib.VariantType.new("(v)"), Gio.DBusCallFlags.NONE, 500, None,
                    ).unpack()
                    _, path = display
                    if path == "/":
                        raise RuntimeError("no graphical logind session")
                self._session_path = path
            props, = self._dbus_bus.call_sync(
                "org.freedesktop.login1", self._session_path,
                "org.freedesktop.DBus.Properties", "GetAll",
                GLib.Variant("(s)", ("org.freedesktop.login1.Session",)),
                GLib.VariantType.new("(a{sv})"), Gio.DBusCallFlags.NONE, 500, None,
            ).unpack()
            return bool(props.get("LockedHint") or props.get("IdleHint") or not props.get("Active", True))
        except (RuntimeError, GLib.Error) as error:
            self._session_path = None
            self._dbus_bus = None
            self._dbus_retry = now + 30.0
            print(f"[!] logind session status unavailable: {error}", file=sys.stderr)
            return False

    def is_idle(self) -> bool:
        now = time.monotonic()
        if self._idle_monitor is None and now < self._idle_retry:
            return True
        try:
            if self._idle_monitor is None:
                self._idle_monitor = IdleMonitor(self.config["idle_threshold_seconds"])
            return self._idle_monitor.poll()
        except (OSError, KeyError, RuntimeError) as error:
            if self._idle_monitor:
                self._idle_monitor.close()
            self._idle_monitor = None
            self._idle_retry = now + 5.0
            print(f"[!] Tracking paused: idle monitor unavailable: {error}", file=sys.stderr)
            return True

    def _record_tick(self, window: dict[str, Any], seconds: float = 1.0, timestamp: float | None = None) -> None:
        cls = window.get("class", "").strip()
        if not cls or cls.lower() in self.config["ignore_classes"]:
            self.last_window_key = None
            return
        title = window.get("title", "").strip() or cls
        timestamp = time.time() if timestamp is None else timestamp
        today = datetime.fromtimestamp(timestamp).date().isoformat()
        apps = self.data.setdefault(today, {})
        info = self.resolver.resolve(cls, title)
        new_record = cls not in apps
        if new_record:
            apps[cls] = {"duration": 0, "first_seen": int(timestamp), "sessions": 0, "titles": {}}
        record = apps[cls]
        record.update(name=info.name, category=info.category, icon=info.icon, last_active=int(timestamp + seconds))
        record["duration"] += seconds
        window_key = cls, window.get("address", "")
        if new_record or window_key != self.last_window_key:
            record["sessions"] = record.get("sessions", 0) + 1
        self.last_window_key = window_key
        titles = record["titles"]
        if title not in titles and len(titles) >= 49:
            title = "Other / Miscellaneous"
        titles[title] = titles.get(title, 0) + seconds
        self._dirty = True

    def _record_interval(self, window: dict[str, Any], start: float, end: float) -> None:
        while start < end:
            next_date = datetime.fromtimestamp(start).date() + timedelta(days=1)
            midnight = datetime.combine(next_date, datetime.min.time()).timestamp()
            segment_end = min(end, midnight)
            self._record_tick(window, segment_end - start, start)
            start = segment_end

    def run(self) -> None:
        print("[*] Dusky Screentime Daemon started.")
        previous_window: dict[str, Any] | None = None
        previous_time = time.clock_gettime(time.CLOCK_BOOTTIME)
        previous_awake = time.monotonic()
        previous_wall = time.time()
        try:
            while self.running:
                loop_start = time.monotonic()
                self._load_config()
                window = None
                if self.config["enabled"] and not self.is_idle() and not self.is_dpms_off() and not self.is_session_inactive() and not self.is_locked():
                    window = self.get_active_window()
                    if window and window["class"].strip().lower() in self.config["ignore_classes"]:
                        window = None
                now = time.clock_gettime(time.CLOCK_BOOTTIME)
                awake = time.monotonic()
                wall_end = time.time()
                elapsed = now - previous_time
                # BOOTTIME includes suspend. Discard long gaps and wall-clock corrections.
                continuous = (
                    0 < elapsed <= 2.0
                    and abs(elapsed - (awake - previous_awake)) <= 0.25
                    and abs((wall_end - previous_wall) - elapsed) <= 0.25
                )
                if previous_window and window and continuous:
                    self._record_interval(previous_window, wall_end - elapsed, wall_end)
                else:
                    self.last_window_key = None
                previous_window, previous_time = window, now
                previous_wall = wall_end
                previous_awake = awake
                if awake - self.last_save_time >= self.config["save_interval_seconds"]:
                    self._save_data_atomic()
                if self.running:
                    time.sleep(max(0.0, 1.0 - (time.monotonic() - loop_start)))
        finally:
            self._save_data_atomic()
            if self._idle_monitor:
                self._idle_monitor.close()
            self._instance_lock.close()
            if self._dirty:
                raise OSError("history remains unsaved; see the preceding save error")

    def stop(self, *_: Any) -> None:
        # Signal handlers never serialize data or acquire a save lock.
        self.running = False


def main() -> None:
    try:
        daemon = ScreentimeDaemon()
        signal.signal(signal.SIGINT, daemon.stop)
        signal.signal(signal.SIGTERM, daemon.stop)
        daemon.run()
    except (OSError, ValueError, RuntimeError) as error:
        print(f"[!] Screentime stopped: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
