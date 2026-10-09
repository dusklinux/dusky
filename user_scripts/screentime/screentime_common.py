"""Shared XDG paths, data validation, and Hyprland IPC."""

import json
import math
import os
import socket
import time
from datetime import date
from pathlib import Path
from typing import Any


def xdg_path(variable: str, fallback: str) -> Path:
    value = os.environ.get(variable, "")
    return Path(value) if value.startswith("/") else Path.home() / fallback


DATA_DIR = xdg_path("XDG_DATA_HOME", ".local/share") / "dusky/screentime"
DATA_FILE = DATA_DIR / "screentime_data.json"
CONFIG_FILE = xdg_path("XDG_CONFIG_HOME", ".config") / "dusky/settings/screentime/screentime.json"
THEME_FILE = xdg_path("XDG_CONFIG_HOME", ".config") / "matugen/generated/dusky_tui.json"


def valid_number(value: Any) -> bool:
    if type(value) not in (int, float) or value < 0:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_data(data: Any) -> dict[str, dict[str, Any]]:
    """Reject malformed history before the writer can overwrite it."""
    if not isinstance(data, dict):
        raise ValueError("history must be an object")
    for day, apps in data.items():
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day or not isinstance(apps, dict):
            raise ValueError(f"invalid day: {day!r}")
        for cls, record in apps.items():
            if not isinstance(cls, str) or not cls.strip() or not isinstance(record, dict):
                raise ValueError(f"invalid application: {cls!r}")
            if not valid_number(record.get("duration")):
                raise ValueError(f"invalid duration: {day}/{cls}")
            for key in ("sessions", "first_seen", "last_active"):
                if key in record and not valid_number(record[key]):
                    raise ValueError(f"invalid {key}: {day}/{cls}")
            titles = record.get("titles", {})
            if not isinstance(titles, dict) or not all(isinstance(k, str) and valid_number(v) for k, v in titles.items()):
                raise ValueError(f"invalid titles: {day}/{cls}")
            record.setdefault("titles", {})
    return data


class HyprlandIPC:
    """Use this session's socket, or a single unambiguous runtime instance."""

    def __init__(self, timeout: float = 0.5) -> None:
        self.timeout = timeout

    def socket_path(self) -> Path | None:
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if not runtime:
            return None
        base = Path(runtime) / "hypr"
        signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
        if signature:
            return base / signature / ".socket.sock"
        candidates = list(base.glob("*/.socket.sock"))
        return candidates[0] if len(candidates) == 1 else None

    def query(self, command: str) -> Any:
        path = self.socket_path()
        if path is None:
            return None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                deadline = time.monotonic() + self.timeout
                connection.settimeout(self.timeout)
                connection.connect(str(path))
                connection.sendall(command.encode())
                response = bytearray()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    connection.settimeout(remaining)
                    chunk = connection.recv(65536)
                    if not chunk:
                        return json.loads(response)
                    response.extend(chunk)
        except (OSError, ValueError):
            return None
