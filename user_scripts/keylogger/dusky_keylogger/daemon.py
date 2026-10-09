"""Always-on Dusky Keylogger daemon.

Wires the evdev KeyListener to SQLite via a dedicated writer thread:
the asyncio loop never issues a blocking sqlite3 call. Designed to run
under systemd Type=notify, stopping cleanly on SIGINT/SIGTERM.
"""

import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
import signal
import socket
import sys
import time
from collections import deque
from pathlib import Path

from . import __version__
from .listener import KeyListener, KeyPress
from .storage import EventRow, EventWriter, KeyStore, row_from_press

logger = logging.getLogger(__name__)

DEFAULT_FLUSH_INTERVAL = 0.5
MAX_BUFFER = 256
MAX_PENDING_ROWS = 20_000


def resolve_path(raw: str | Path) -> Path:
    """Expand user/environment paths; relative config paths start at HOME."""
    p = Path(os.path.expandvars(str(raw))).expanduser()
    return p if p.is_absolute() else Path.home() / p


def default_data_dir() -> Path:
    """Resolve env > selected config > default without modifying config."""
    config = {}
    try:
        loaded = json.loads(default_config_path().read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            config = loaded
    except (OSError, ValueError):
        pass
    return get_data_dir(config)


def get_data_dir(config: dict | None = None) -> Path:
    """Resolve persistent data dir from config/env (helper for TUI)."""
    if config is None:
        # Avoid recursion: directly check env + config file minimally
        return default_data_dir()
    raw = os.environ.get("DUSKY_KEYLOGGER_DATA_DIR") or config.get("data_dir")
    if not isinstance(raw, (str, Path)) or not str(raw).strip():
        raw = DEFAULT_CONFIG["data_dir"]
    return resolve_path(raw)


def default_config_path() -> Path:
    """Canonical config path with intelligent fallback.

    New canonical (dusky ecosystem): ~/.config/dusky/settings/keylogger/config.json
    Legacy: ~/.config/dusky-keylogger/config.json (auto-migrated on first run).
    Env DUSKY_KEYLOGGER_CONFIG overrides both. Never hardcodes username (Path.home).
    The directory is auto-created on a fresh install if it doesn't already exist
    (see load_config).
    """
    env = os.environ.get("DUSKY_KEYLOGGER_CONFIG")
    if env:
        return resolve_path(env)
    new = Path.home() / ".config" / "dusky" / "settings" / "keylogger" / "config.json"
    old = Path.home() / ".config" / "dusky-keylogger" / "config.json"
    # Fresh install: neither exists -> return new (will be auto-created)
    # Migration: old exists but new doesn't -> return old for now (load_config will migrate)
    # Normal: new exists -> return new
    if new.exists():
        return new
    if old.exists() and not new.exists():
        return old
    return new


def _old_config_path() -> Path:
    return Path.home() / ".config" / "dusky-keylogger" / "config.json"


DEFAULT_CONFIG: dict = {
    "flush_interval": DEFAULT_FLUSH_INTERVAL,
    "log_level": "info",
    # Persistence: where SQLite DB lives (survives reboot, manual delete only).
    # Default is now ~/.config/dusky/settings/keylogger/data (per user request).
    # Override via env DUSKY_KEYLOGGER_DATA_DIR or config "data_dir".
    # Ephemeral: where transcripts go (cleared on reboot, e.g., /tmp).
    # Default is /tmp (per user request) — just these two places, nothing else.
    # Persistent counts/stats stay in DATA_DIR (not ephemeral); transcripts are ephemeral.
    "data_dir": "~/.config/dusky/settings/keylogger/data",
    "transcript_dir": "/tmp",
    "transcript_format": "text",  # "text" | "markdown"
    "persistent_enabled": True,  # master toggle for DB logging (persistent)
    "ephemeral_enabled": True,  # master toggle for transcript generation (ephemeral)
}


def load_config(path: Path | None = None) -> dict:
    cfg_path = path or default_config_path()
    config = dict(DEFAULT_CONFIG)
    try:
        if cfg_path.exists():
            loaded = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                config.update(loaded)
            else:
                logger.warning("Config %s is not a JSON object; using defaults", cfg_path)
        else:
            cfg_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700, parent_mode=0o700)
            try:
                fd = os.open(cfg_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(DEFAULT_CONFIG, fh, indent=2)
                    fh.write("\n")
            except FileExistsError:
                # Another process won creation; use its configuration.
                return load_config(cfg_path)
        # Existing files are read without backfilling or changing permissions:
        # readers must not replace a concurrent TUI edit with stale values.
        if path is None and not os.environ.get("DUSKY_KEYLOGGER_CONFIG") and cfg_path == _old_config_path():
            new_path = Path.home() / ".config/dusky/settings/keylogger/config.json"
            new_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700, parent_mode=0o700)
            try:
                fd = os.open(new_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(config, fh, indent=2)
                    fh.write("\n")
            except FileExistsError:
                return load_config(new_path)
    except (OSError, ValueError) as exc:
        logger.warning("Could not read config %s: %s", cfg_path, exc)

    try:
        fi = float(config["flush_interval"])
        config["flush_interval"] = max(0.05, min(fi, 5.0)) if math.isfinite(fi) else DEFAULT_FLUSH_INTERVAL
    except (TypeError, ValueError, OverflowError):
        config["flush_interval"] = DEFAULT_FLUSH_INTERVAL
    level = str(config["log_level"]).lower()
    config["log_level"] = level if level in {"debug", "info", "warning", "error"} else "info"
    for key in ("persistent_enabled", "ephemeral_enabled"):
        if not isinstance(config[key], bool):
            logger.warning("%s must be a JSON boolean; using default", key)
            config[key] = DEFAULT_CONFIG[key]
    config["data_dir"] = str(get_data_dir(config))
    config["transcript_dir"] = str(get_transcript_dir(config))
    config["transcript_format"] = get_transcript_format(config)
    return config


def get_transcript_dir(config: dict | None = None) -> Path:
    """Resolve transcript directory (env > config > /tmp).

    Never hardcodes a username; uses $HOME expansion if relative.
    Auto-creates on first use (caller should mkdir) but this helper just resolves.
    """
    cfg = config if config is not None else load_config()
    raw = cfg.get("transcript_dir")
    if not isinstance(raw, (str, Path)) or not str(raw).strip():
        raw = DEFAULT_CONFIG["transcript_dir"]
    # env already folded in load_config, but respect direct env if config was passed in
    env = os.environ.get("DUSKY_TRANSCRIPT_DIR")
    if env:
        raw = env
    return resolve_path(raw)


def get_transcript_format(config: dict | None = None) -> str:
    cfg = config if config is not None else load_config()
    fmt = str(cfg.get("transcript_format", "text")).lower()
    env = os.environ.get("DUSKY_TRANSCRIPT_FORMAT")
    if env:
        env = env.lower().strip()
        if env in {"text", "markdown", "md"}:
            return "markdown" if env in {"markdown", "md"} else "text"
    return "markdown" if fmt in {"markdown", "md"} else "text"


def sd_notify(message: str) -> None:
    """Send a systemd notification (READY / WATCHDOG / STOPPING / STATUS).

    No-op when not running under systemd (NOTIFY_SOCKET unset).
    """
    raw = os.environ.get("NOTIFY_SOCKET")
    if not raw:
        return
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC)
        try:
            addr = "\0" + raw[1:] if raw.startswith("@") else raw
            sock.connect(addr)
            sock.send(message.encode())
        finally:
            sock.close()
    except OSError:
        logger.debug("sd_notify(%r) failed", message, exc_info=True)


def _setup_logging(level: str, data_dir: Path) -> None:
    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    lvl = level_map.get(str(level).lower(), logging.INFO)
    # force=True closes prior handlers, including the rotating file handler.
    logging.basicConfig(
        level=lvl,
        force=True,
        format="%(asctime)s [%(levelname)-7s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stderr)],
    )
    root = logging.getLogger()
    try:
        log_dir = data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True, mode=0o700, parent_mode=0o700)
        file_handler = RotatingFileHandler(
            log_dir / "daemon.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(lvl)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)-7s] %(message)s", "%Y-%m-%d %H:%M:%S"
            )
        )
        root.addHandler(file_handler)
    except OSError as exc:
        logger.warning("File logging disabled: %s", exc)


class Daemon:
    """Keystroke logging daemon. Event loop never blocks on SQLite."""

    def __init__(
        self,
        data_dir: str | Path | None = None,
        config: dict | None = None,
    ) -> None:
        self._config = config if config is not None else load_config()
        self._data_dir = resolve_path(data_dir) if data_dir else get_data_dir(self._config)
        self._store = KeyStore(self._data_dir / "keys.db")
        self._writer = EventWriter(self._store)
        self._listener: KeyListener | None = None
        self._buffer: deque[EventRow] = deque(maxlen=MAX_PENDING_ROWS)
        self._discarded = 0
        # Clamp flush interval to avoid tight loops if config is malformed.
        try:
            fi = float(self._config.get("flush_interval", DEFAULT_FLUSH_INTERVAL))
        except Exception:
            fi = DEFAULT_FLUSH_INTERVAL
        self._flush_interval = max(0.05, min(fi, 5.0)) if math.isfinite(fi) else DEFAULT_FLUSH_INTERVAL
        self._stop = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started_at = time.monotonic()

    def _handle_press(self, press: KeyPress) -> None:
        # Intelligent persistence toggle: if disabled, skip DB logging entirely
        # (ephemeral transcripts still possible via CLI if enabled, but DB stays quiet).
        if not self._config.get("persistent_enabled", True):
            return
        row = row_from_press(press)
        if len(self._buffer) == MAX_PENDING_ROWS:
            self._discarded += 1
        self._buffer.append(row)
        # A rejected batch stays buffered. Retry it on the timer, rather than
        # copying and logging the growing backlog on every subsequent press.
        if len(self._buffer) == MAX_BUFFER:
            self._kick_flush()

    def _kick_flush(self) -> None:
        if not self._buffer:
            return
        if self._discarded:
            logger.error(
                "Producer buffer full -- discarded %d oldest events",
                self._discarded,
            )
            self._discarded = 0
        rows = list(self._buffer)
        if self._writer.submit(rows):
            self._buffer.clear()
        else:
            logger.error(
                "Writer queue saturated -- holding %d events in memory",
                len(rows),
            )

    async def _flush_loop(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self._flush_interval
                    )
                except TimeoutError:
                    self._kick_flush()
                    err = self._writer.last_error
                    if err is not None:
                        logger.error("Writer error: %s", err)
                    if not self._writer.is_alive:
                        raise RuntimeError("SQLite writer stopped unexpectedly") from err
        except asyncio.CancelledError:
            raise

    async def _watchdog_loop(self, interval: float) -> None:
        """Ping systemd's watchdog at half WatchdogSec when WATCHDOG_USEC is set."""
        while not self._stop.is_set():
            sd_notify("WATCHDOG=1")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                continue

    async def run(self) -> None:
        with self._store.collector_lock() as ownership_fd:
            await self._run(ownership_fd)

    async def _run(self, ownership_fd: int) -> None:
        _setup_logging(self._config.get("log_level", "info"), self._data_dir)
        self._store.init_db()
        self._writer.start(ownership_fd=ownership_fd)
        listener = None
        loop = asyncio.get_running_loop()
        self._loop = loop
        registered_signals: list[signal.Signals] = []
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self._stop.set)
                registered_signals.append(sig)
            listener = KeyListener()
            self._listener = listener
            listener.on_key = self._handle_press
            await listener.start()
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(self._flush_loop(), name="dusky-flush")
                wd_usec = int(os.environ.get("WATCHDOG_USEC", "0"))
                if wd_usec > 0:
                    tasks.create_task(
                        self._watchdog_loop(wd_usec / 2_000_000), name="dusky-watchdog"
                    )
                sd_notify(f"READY=1\nSTATUS=dusky v{__version__} listening\n")
                logger.info("Dusky Keylogger v%s started (data: %s)", __version__, self._store.path)
                await self._stop.wait()
                tasks.cancel()
        finally:
            sd_notify("STOPPING=1\nSTATUS=flushing\n")
            try:
                if listener is not None:
                    await listener.stop()
            finally:
                for sig in registered_signals:
                    loop.remove_signal_handler(sig)
                self._loop = None
                self._kick_flush()
                # A timed-out worker still owns its connection and retry rows.
                # Never start a competing synchronous flush in that case.
                closed = await asyncio.to_thread(self._writer.close, timeout=8.0)
                if not closed:
                    raise RuntimeError("SQLite writer did not stop; pending data may be lost")
                pending = self._writer.take_pending() + list(self._buffer)
                self._buffer.clear()
                final_written = 0
                if pending:
                    final_written = await asyncio.to_thread(self._store.insert_many, pending)
                elif self._writer.last_error is not None:
                    raise RuntimeError("SQLite writer failed") from self._writer.last_error
                logger.info(
                    "Shutdown complete: %d rows persisted, uptime %.1fs",
                    self._writer.written + final_written,
                    time.monotonic() - self._started_at,
                )

    async def stop(self) -> None:
        self.stop_sync()

    def stop_sync(self) -> None:
        """Synchronous stop for non-async callers / tests."""
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._stop.set)
        else:
            self._stop.set()
