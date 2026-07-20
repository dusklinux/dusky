#!/usr/bin/env python3
"""
===============================================================================
DUSKY SCREENTIME: BACKGROUND DAEMON
===============================================================================
Zero-fork, high-performance Wayland screentime tracking daemon.
Connects directly to Hyprland UNIX domain sockets to monitor active windows,
resolves applications via `DesktopResolver` (matching Rofi behavior), and
atomically persists daily metrics to `~/.local/share/dusky/screentime/screentime_data.json`.
"""

import json
import os
import signal
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure local imports work regardless of working directory
SCRIPT_DIR = Path(__file__).parent.resolve()
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from desktop_resolver import AppInfo, DesktopResolver
except ImportError:
    # If imported from another path
    from python.desktop_resolver import AppInfo, DesktopResolver


DATA_DIR = Path(os.path.expanduser("~/.local/share/dusky/screentime"))
DATA_FILE = DATA_DIR / "screentime_data.json"
CONFIG_DIR = Path(os.path.expanduser("~/.config/dusky/settings/screentime"))
CONFIG_FILE = CONFIG_DIR / "screentime.json"

DEFAULT_CONFIG = {
    "enabled": True,
    "save_interval_seconds": 5,
    "idle_threshold_seconds": 300,
    "ignore_classes": ["hyprlock", "swaylock", "gdm", "sddm"],
}


class ScreentimeDaemon:
    def __init__(self):
        self.running = False
        self.config = DEFAULT_CONFIG.copy()
        self.data: Dict[str, Dict[str, Any]] = {}
        self.resolver = DesktopResolver()
        self.last_save_time = time.time()
        self.last_active_time = time.time()
        self.last_window_key = ""
        self.lock = threading.Lock()

        self._ensure_directories()
        self._load_config()
        self._load_data()

    def _ensure_directories(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    def _load_config(self) -> None:
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    user_conf = json.load(f)
                    for k, v in user_conf.items():
                        if k in self.config:
                            self.config[k] = v
            except Exception as e:
                print(f"[!] Error loading config: {e}", file=sys.stderr)
        else:
            self._save_config()

    def _save_config(self) -> None:
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4)
        except Exception:
            pass

    def _load_data(self) -> None:
        if DATA_FILE.exists():
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:
                print(f"[!] Error loading data file: {e}", file=sys.stderr)
                self.data = {}
        else:
            self.data = {}

    def _save_data_atomic(self) -> None:
        with self.lock:
            try:
                temp_file = DATA_FILE.with_suffix(".json.tmp")
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, indent=2)
                os.replace(temp_file, DATA_FILE)
                self.last_save_time = time.time()
            except Exception as e:
                print(f"[!] Error saving data: {e}", file=sys.stderr)

    def _hypr_query_socket(self, cmd: str) -> Optional[str]:
        """
        Send a query to the Hyprland UNIX socket (`.socket.sock`) with zero subprocess forks.
        """
        xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
        sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        if not xdg_runtime or not sig:
            return None
        socket_path = Path(xdg_runtime) / "hypr" / sig / ".socket.sock"
        if not socket_path.exists():
            return None

        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect(str(socket_path))
                s.sendall(cmd.encode("utf-8"))
                response = bytearray()
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    response.extend(chunk)
                return response.decode("utf-8", errors="ignore")
        except Exception:
            return None

    def get_active_window(self) -> Optional[Dict[str, Any]]:
        """
        Retrieve active window metadata (`class`, `title`, `pid`) via socket.
        """
        raw = self._hypr_query_socket("j/activewindow")
        if not raw:
            return None
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("class"):
                return data
        except Exception:
            pass
        return None

    def is_dpms_off(self) -> bool:
        """
        Check if monitors are sleeping/off (`dpmsStatus` is false for all).
        """
        raw = self._hypr_query_socket("j/monitors")
        if not raw:
            return False
        try:
            monitors = json.loads(raw)
            if isinstance(monitors, list) and monitors:
                # If any monitor is on (`dpmsStatus` true or missing), return False
                for m in monitors:
                    if m.get("dpmsStatus", True):
                        return False
                return True
        except Exception:
            pass
        return False

    def is_locked(self) -> bool:
        """
        Check if lockscreen process (`hyprlock` or `swaylock`) is active.
        We check via `/proc` quickly without spawning `ps` or `pgrep`.
        """
        try:
            for pdir in os.listdir("/proc"):
                if not pdir.isdigit():
                    continue
                try:
                    with open(f"/proc/{pdir}/comm", "r", encoding="utf-8") as f:
                        comm = f.read().strip()
                        if comm in (
                            "hyprlock",
                            "swaylock",
                            "swaylock-effects",
                            "i3lock",
                        ):
                            return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def _record_tick(self, win: Dict[str, Any]) -> None:
        cls = win.get("class", "").strip()
        if not cls or cls.lower() in self.config["ignore_classes"]:
            return

        title = win.get("title", "").strip() or cls
        today = datetime.now().strftime("%Y-%m-%d")

        with self.lock:
            if today not in self.data:
                self.data[today] = {}

            # Resolve app metadata
            info = self.resolver.resolve(cls, title)

            if cls not in self.data[today]:
                self.data[today][cls] = {
                    "name": info.name,
                    "category": info.category,
                    "icon": info.icon,
                    "duration": 0,
                    "first_seen": int(time.time()),
                    "last_active": int(time.time()),
                    "sessions": 1,
                    "titles": {},
                }
            else:
                # Update existing record
                rec = self.data[today][cls]
                # Keep resolved metadata fresh
                rec["name"] = info.name
                rec["category"] = info.category
                rec["icon"] = info.icon
                rec["last_active"] = int(time.time())

            rec = self.data[today][cls]
            rec["duration"] += 1
            if title:
                # Limit stored unique titles per app to 50 to prevent unbounded growth
                if title not in rec["titles"] and len(rec["titles"]) >= 50:
                    title = "Other / Miscellaneous"
                rec["titles"][title] = rec["titles"].get(title, 0) + 1

            # Check if this is a new focus session
            if cls != self.last_window_key:
                rec["sessions"] = rec.get("sessions", 0) + 1
                self.last_window_key = cls

    def run(self) -> None:
        self.running = True
        print("[*] Dusky Screentime Daemon started.")

        while self.running:
            start_t = time.time()

            if self.config.get("enabled", True):
                # Check for idle/lock states
                if not self.is_locked() and not self.is_dpms_off():
                    win = self.get_active_window()
                    if win and win.get("class"):
                        self._record_tick(win)
                        self.last_active_time = time.time()
                    else:
                        self.last_window_key = ""
                else:
                    self.last_window_key = ""

            # Periodic saving
            if time.time() - self.last_save_time >= self.config.get(
                "save_interval_seconds", 5
            ):
                self._save_data_atomic()

            # Maintain exactly 1.0 second loop interval
            elapsed = time.time() - start_t
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)

    def stop(self, *args) -> None:
        print("[*] Stopping Dusky Screentime Daemon...")
        self.running = False
        self._save_data_atomic()
        sys.exit(0)


if __name__ == "__main__":
    daemon = ScreentimeDaemon()
    signal.signal(signal.SIGINT, daemon.stop)
    signal.signal(signal.SIGTERM, daemon.stop)
    daemon.run()
