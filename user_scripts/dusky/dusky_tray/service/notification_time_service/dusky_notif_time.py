#!/usr/bin/env python3
"""
Dusky Notification Timestamp Tracking Daemon
Standalone background service that records first-observed timestamps for Mako
desktop notifications and caches them atomically to
$XDG_RUNTIME_DIR/dusky_notif_times.json.
"""

from datetime import datetime
import json
import os
from pathlib import Path
import select
lazy import shutil
import signal
import subprocess
lazy import tempfile
import time
from typing import Any

SWEEP_INTERVAL_MS = 60_000
DEBOUNCE_SECONDS = 0.15
FALLBACK_POLL_SECONDS = 2.0
MONITOR_RETRY_SECONDS = 5.0
MAX_ENTRIES = 500


def _signal_handler(_signum: int, _frame: Any) -> None:
    # Unwind poll/sleep immediately without locks in a signal handler.
    raise SystemExit(0)


def get_cache_file() -> Path:
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime and Path(xdg_runtime).is_absolute():
        base_dir = Path(xdg_runtime)
    else:
        base_dir = Path(tempfile.gettempdir())
    return base_dir / "dusky_notif_times.json"


def atomic_write_json(path: Path, data: dict[str, str]) -> bool:
    """Publish compact JSON atomically so readers never observe partial files."""
    tmp_path = path.with_suffix(f".tmp.{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, separators=(",", ":"))
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
    """Fetch active and history notification IDs from Mako."""
    ids: set[int] = set()
    for cmd in (["makoctl", "list", "-j"], ["makoctl", "history", "-j"]):
        try:
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=1.0,
                check=False,
            )
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


def load_cache(cache_file: Path) -> dict[str, str]:
    cached: dict[str, str] = {}
    if not cache_file.is_file():
        return cached
    try:
        with cache_file.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, dict):
            for key, val in raw.items():
                cached[str(key)] = str(val)
    except (OSError, ValueError):
        pass
    return cached


def sync_timestamps(cache_file: Path, cached: dict[str, str]) -> dict[str, str]:
    current_ids = fetch_mako_notification_ids()
    if current_ids is None:
        return cached
    now_str = datetime.now().strftime("%I:%M %p").lstrip("0")
    # Keep a stable set of the newest IDs to avoid endless churn above MAX_ENTRIES.
    updated = {
        str(nid): cached.get(str(nid), now_str)
        for nid in sorted(current_ids, reverse=True)[:MAX_ENTRIES]
    }
    if updated != cached and atomic_write_json(cache_file, updated):
        return updated
    return cached


def start_busctl_monitor() -> tuple[subprocess.Popen[bytes], select.poll, int] | None:
    busctl = shutil.which("busctl")
    if not busctl:
        return None
    try:
        proc = subprocess.Popen(
            [
                busctl,
                "--user",
                "monitor",
                "--json=short",
                "--match",
                "type='method_call',interface='org.freedesktop.Notifications',member='Notify'",
                "--match",
                "type='signal',interface='org.freedesktop.Notifications',member='NotificationClosed'",
                "--match",
                "type='method_call',interface='fr.emersion.Mako',member='DismissNotifications'",
                "--match",
                "type='method_call',interface='fr.emersion.Mako',member='RestoreNotification'",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None
    if proc.stdout is None:
        stop_monitor(proc)
        return None
    fd = proc.stdout.fileno()
    os.set_blocking(fd, False)
    poller = select.poll()
    poller.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR)
    return proc, poller, fd


def stop_monitor(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=0.5)
    except OSError:
        pass
    if proc.stdout is not None:
        try:
            proc.stdout.close()
        except OSError:
            pass


def drain_fd(fd: int) -> tuple[bool, bool]:
    """Drain all readable bytes from a non-blocking fd. Returns (saw_data, eof)."""
    saw_data = False
    while True:
        try:
            chunk = os.read(fd, 65_536)
        except BlockingIOError:
            return saw_data, False
        except OSError:
            return saw_data, True
        if not chunk:
            return saw_data, True
        saw_data = True


def main() -> None:
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    cache_file = get_cache_file()
    cached_timestamps = sync_timestamps(cache_file, load_cache(cache_file))

    monitor: subprocess.Popen[bytes] | None = None
    poller: select.poll | None = None
    monitor_fd = -1
    monitor_retry_at = 0.0

    try:
        while True:
            if monitor is None and time.monotonic() >= monitor_retry_at:
                started = start_busctl_monitor()
                if started is not None:
                    monitor, poller, monitor_fd = started
                else:
                    monitor_retry_at = time.monotonic() + MONITOR_RETRY_SECONDS

            if monitor is not None and poller is not None:
                events = poller.poll(SWEEP_INTERVAL_MS)
                eof = False
                if events:
                    mask = events[0][1]
                    saw_data = False
                    if mask & select.POLLIN:
                        saw_data, eof = drain_fd(monitor_fd)
                    if mask & (select.POLLHUP | select.POLLERR | select.POLLNVAL):
                        eof = True
                    # Collapse rapid notification bursts into a single Mako query.
                    if saw_data and not eof:
                        debounce_end = time.monotonic() + DEBOUNCE_SECONDS
                        while not eof:
                            remaining = debounce_end - time.monotonic()
                            if remaining <= 0:
                                break
                            burst = poller.poll(max(1, int(remaining * 1000)))
                            if not burst:
                                break
                            bmask = burst[0][1]
                            if bmask & select.POLLIN:
                                _, eof = drain_fd(monitor_fd)
                            if bmask & (select.POLLHUP | select.POLLERR | select.POLLNVAL):
                                eof = True
                if eof:
                    stop_monitor(monitor)
                    monitor = None
                    poller = None
                    monitor_fd = -1
                    monitor_retry_at = time.monotonic() + MONITOR_RETRY_SECONDS
            else:
                time.sleep(FALLBACK_POLL_SECONDS)

            try:
                cached_timestamps = sync_timestamps(cache_file, cached_timestamps)
            except Exception:
                pass
    finally:
        stop_monitor(monitor)


if __name__ == "__main__":
    main()
