#!/usr/bin/env python3
"""
Dusky Notification Timestamp Tracking Daemon
Standalone background service that records first-observed timestamps for Mako desktop notifications
and caches them atomically to $XDG_RUNTIME_DIR/dusky_notif_times.json.
"""

import json
import os
import signal
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

def _signal_handler(signum: int, frame: Any) -> None:
    # Unwind sleep or subprocess.run immediately without locks in a signal handler.
    raise SystemExit(0)

def get_cache_file() -> Path:
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    base_dir = Path(xdg_runtime) if xdg_runtime else Path(tempfile.gettempdir())
    return base_dir / "dusky_notif_times.json"

def atomic_write_json(path: Path, data: Any) -> bool:
    """Safely write JSON data using atomic file replacement to prevent readers from seeing partial files."""
    tmp_path = path.with_suffix(f".tmp.{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
        return True
    except OSError:
        return False
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

def fetch_mako_notification_ids() -> set[int] | None:
    """Fetch active and history notification IDs from Mako without subprocess bloat."""
    ids = set()
    for cmd in (["makoctl", "list", "-j"], ["makoctl", "history", "-j"]):
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1.0)
            if res.returncode != 0 or not res.stdout:
                return None
            parsed = json.loads(res.stdout)
            items = parsed.get("data") if isinstance(parsed, dict) else parsed
            if isinstance(items, list) and items and isinstance(items[0], list):
                items = items[0]
            if not isinstance(items, list):
                return None
            for item in items:
                if isinstance(item, dict) and "id" in item:
                    try:
                        ids.add(int(item["id"]))
                    except (ValueError, TypeError):
                        pass
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return None
    return ids

def main() -> None:
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    cache_file = get_cache_file()
    cached_timestamps: dict[str, str] = {}

    # Load existing cache file on startup
    if cache_file.is_file():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        cached_timestamps[str(k)] = str(v)
        except Exception:
            pass

    while True:
        try:
            current_ids = fetch_mako_notification_ids()
            if current_ids is not None:
                now_str = datetime.now().strftime("%I:%M %p").lstrip("0")
                # Keep a stable set of the newest IDs. Re-adding and evicting
                # older IDs every poll caused endless writes above 500 entries.
                updated = {
                    str(nid): cached_timestamps.get(str(nid), now_str)
                    for nid in sorted(current_ids, reverse=True)[:500]
                }
                if updated != cached_timestamps and atomic_write_json(cache_file, updated):
                    cached_timestamps = updated
        except Exception:
            pass

        time.sleep(2.0)

if __name__ == "__main__":
    main()
