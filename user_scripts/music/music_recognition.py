#!/usr/bin/env python3
"""
Dusky Music Recognition
=======================

Native PipeWire capture with a real audio-level meter, SongRec recognition,
Rich presentation, persistent history, and optional desktop integrations.

Required:
    Python 3.14+
    python-rich
    songrec
    pw-record
    pw-dump

Optional:
    notify-send    Desktop notifications
    wl-copy        Wayland clipboard
    xdg-open       Open song links
    fzf            Interactive history search

Configuration:
    $XDG_CONFIG_HOME/dusky/settings/music_recognition/config.json
    or ~/.config/dusky/settings/music_recognition/config.json

Timeout:
    --timeout covers capture and SongRec analysis.
    Setup, child-process cleanup, cover downloading, notification delivery,
    and optional interactive actions are outside that budget.

Exit codes:
    0    Successful operation / identification
    1    Operational failure or recognition timeout
    2    No match, or CLI usage error
    3    Another recognition session is active
    128 + signal number
         Interrupted

Mako placement is configured in Mako, not by this script.
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 14):
    sys.stderr.write("Python 3.14+ is required.\n")
    raise SystemExit(1)

import argparse
import csv
import fcntl
import hashlib
import html
import io
import json
import logging
import math
import os
import re
import select
import shlex
import shutil
import signal
import subprocess
import tempfile
import termios
import time
import tty
import urllib.parse
import urllib.request
import wave

from array import array
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

try:
    from rich import box
    from rich.align import Align
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.style import Style
    from rich.table import Table
    from rich.text import Text
    from rich.theme import Theme
except ImportError:
    sys.stderr.write(
        "python-rich is required.\n"
        "Install: sudo pacman -S --needed python-rich\n"
    )
    raise SystemExit(1)


VERSION = "2.2.0"

OK = 0
ERROR = 1
NO_MATCH = 2
BUSY = 3

DISCOVERY_TIMEOUT = 4.0
CHILD_STOP_TIMEOUT = 1.5
HISTORY_LOCK_TIMEOUT = 5.0

PCM_RATE = 44_100
PCM_CHANNELS = 2
PCM_WIDTH = 2
PCM_FRAME_BYTES = PCM_CHANNELS * PCM_WIDTH

CAPTURE_STARTUP_TIMEOUT = 5.0
CAPTURE_STALL_TIMEOUT = 3.0
CAPTURE_FINISH_ALLOWANCE = 3.0
MIN_ANALYSIS_BUDGET = 0.5

METER_INTERVAL = 0.1
METER_WINDOW_BYTES = int(PCM_RATE * 0.1) * PCM_FRAME_BYTES
METER_COLUMNS = 32
METER_FLOOR_DBFS = -60.0

MAX_DIAGNOSTIC_BYTES = 16_384
MAX_COVER_BYTES = 5 * 1024 * 1024


# =============================================================================
# Paths, logging, errors
# =============================================================================

def config_home() -> Path:
    value = os.environ.get("XDG_CONFIG_HOME")
    if value and os.path.isabs(value):
        return Path(value)
    return Path.home() / ".config"


CONFIG_HOME = config_home()
STATE_DIR = CONFIG_HOME / "dusky/settings/music_recognition"
CONFIG_FILE = STATE_DIR / "config.json"
HISTORY_FILE = STATE_DIR / "history.json"
COVERS_DIR = STATE_DIR / "covers"
LOCK_FILE = STATE_DIR / "music_recognition.lock"
LOG_FILE = STATE_DIR / "music_recognition.log"
THEME_FILE = CONFIG_HOME / "matugen/generated/dusky_tui.json"

logger = logging.getLogger("dusky.music")
logger.addHandler(logging.NullHandler())
logger.propagate = False


class AppError(RuntimeError):
    pass


class RecognitionTimeout(AppError):
    pass


class BusyError(AppError):
    pass


def setup_logging() -> None:
    logger.setLevel(logging.INFO)
    try:
        handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=1_000_000,
            backupCount=2,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s: %(message)s"
        ))
        logger.addHandler(handler)
    except OSError as exc:
        sys.stderr.write(f"Warning: log file unavailable: {exc}\n")


def clean_text(value: str) -> str:
    """Keep ordinary text without terminal control characters."""
    return "".join(
        ch for ch in value
        if ch in "\n\t"
        or (ord(ch) >= 32 and not 0x7F <= ord(ch) <= 0x9F)
    )


def one_line(value: str) -> str:
    return " ".join(clean_text(value).split())


def reject_nonfinite(value: str) -> Any:
    raise ValueError(f"Invalid non-finite JSON number: {value}")


def parse_json(value: str) -> Any:
    return json.loads(value, parse_constant=reject_nonfinite)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream, parse_constant=reject_nonfinite)


def emit_json(value: Any) -> None:
    print(json.dumps(
        value,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    ), flush=True)


def atomic_write(path: Path, payload: bytes) -> None:
    """Publish a complete file atomically and request filesystem durability."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(temporary, path)
        temporary = None

        directory_fd = os.open(
            path.parent,
            os.O_RDONLY | os.O_DIRECTORY,
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove temporary file %s", temporary)


def atomic_json(path: Path, value: Any) -> None:
    atomic_write(
        path,
        (
            json.dumps(
                value,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8"),
    )


# =============================================================================
# Theme
# =============================================================================

@dataclass(slots=True)
class AppTheme:
    bg: str = "#11140f"
    fg: str = "#e1e4da"
    accent: str = "#a5d395"
    error: str = "#ffb4ab"
    warning: str = "#bbcbb2"
    success: str = "#a0cfd2"
    muted: str = "#858d80"

    @classmethod
    def load(cls) -> AppTheme:
        theme = cls()
        try:
            data = read_json(THEME_FILE)
        except (OSError, ValueError):
            return theme

        if isinstance(data, dict):
            for item in fields(cls):
                value = data.get(item.name)
                if (
                    isinstance(value, str)
                    and re.fullmatch(r"#[0-9a-fA-F]{6}", value)
                ):
                    setattr(theme, item.name, value)
        return theme

    def rich_theme(self) -> Theme:
        return Theme({
            "accent": f"bold {self.accent}",
            "fg": self.fg,
            "muted": self.muted,
            "warning": self.warning,
            "success": self.success,
            "error": self.error,
            "header": f"bold {self.accent}",
        })

    def fzf_colors(self) -> str:
        return ",".join((
            f"bg:{self.bg}",
            f"bg+:{self.bg}",
            f"fg:{self.fg}",
            f"fg+:{self.accent}",
            f"hl:{self.success}",
            f"hl+:{self.success}",
            f"border:{self.muted}",
            f"header:{self.accent}",
            f"info:{self.warning}",
            f"prompt:{self.accent}",
            f"pointer:{self.accent}",
            f"marker:{self.success}",
            f"spinner:{self.accent}",
            f"label:{self.accent}",
        ))


THEME = AppTheme.load()
console = Console(theme=THEME.rich_theme(), highlight=False)
err_console = Console(
    stderr=True,
    theme=THEME.rich_theme(),
    highlight=False,
)


# =============================================================================
# Configuration and metadata
# =============================================================================

@dataclass(slots=True)
class AppConfig:
    record_duration: int = 5
    timeout: int = 30
    notifications: bool = True
    auto_copy: bool = False
    default_source: str = "system"
    max_history: int = 500
    download_covers: bool = True

    def validate(self) -> None:
        for name in ("record_duration", "timeout", "max_history"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise AppError(
                    f"Configuration '{name}' must be a positive integer."
                )

        for name in ("notifications", "auto_copy", "download_covers"):
            if type(getattr(self, name)) is not bool:
                raise AppError(f"Configuration '{name}' must be true or false.")

        if self.default_source not in ("system", "mic"):
            raise AppError(
                "Configuration 'default_source' must be 'system' or 'mic'."
            )

    @classmethod
    def load(cls, *, create: bool = True) -> AppConfig:
        try:
            data = read_json(CONFIG_FILE)
        except FileNotFoundError:
            config = cls()
            if create:
                atomic_json(CONFIG_FILE, asdict(config))
            return config
        except (OSError, ValueError) as exc:
            raise AppError(f"Cannot read {CONFIG_FILE}: {exc}") from exc

        if not isinstance(data, dict):
            raise AppError("Configuration must contain a JSON object.")

        names = {item.name for item in fields(cls)}
        unknown = set(data) - names
        if unknown:
            logger.warning(
                "Unknown configuration fields: %s",
                ", ".join(sorted(unknown)),
            )

        config = cls(**{
            key: value for key, value in data.items() if key in names
        })
        config.validate()
        return config


@dataclass(slots=True)
class Song:
    id: str
    title: str
    artist: str
    album: str = ""
    release_year: str = ""
    genres: list[str] = field(default_factory=list)
    cover_url: str = ""
    local_cover_path: str = ""
    shazam_url: str = ""
    apple_music_url: str = ""
    spotify_url: str = ""
    youtube_search_url: str = ""
    lyrics: list[str] = field(default_factory=list)
    timestamp: str = ""
    epoch: float = 0.0
    source: str = "system"
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any) -> Song:
        if not isinstance(data, dict):
            raise ValueError("Song record must be an object.")

        names = {item.name for item in fields(cls)}
        try:
            song = cls(**{
                key: value for key, value in data.items() if key in names
            })
        except TypeError as exc:
            raise ValueError(f"Invalid song fields: {exc}") from exc

        for name in names - {"genres", "lyrics", "epoch", "raw"}:
            if not isinstance(getattr(song, name), str):
                raise ValueError(f"'{name}' must be a string.")

        if not song.id or not song.title.strip():
            raise ValueError("Song id and title must not be empty.")

        for name in ("genres", "lyrics"):
            value = getattr(song, name)
            if (
                not isinstance(value, list)
                or not all(isinstance(item, str) for item in value)
            ):
                raise ValueError(f"'{name}' must be a list of strings.")

        if (
            type(song.epoch) not in (int, float)
            or not math.isfinite(song.epoch)
            or song.epoch < 0
        ):
            raise ValueError("Song epoch must be finite and nonnegative.")

        if not isinstance(song.raw, dict):
            raise ValueError("Song raw metadata must be an object.")

        return song


def spotify_target(song: Song) -> str:
    return song.spotify_url or (
        "https://open.spotify.com/search/"
        + urllib.parse.quote(f"{song.title} {song.artist}".strip(), safe="")
    )


def youtube_target(song: Song) -> str:
    return song.youtube_search_url or (
        "https://www.youtube.com/results?"
        + urllib.parse.urlencode({
            "search_query": f"{song.title} {song.artist}".strip(),
        })
    )


# =============================================================================
# Locking and history
# =============================================================================

@contextmanager
def history_lock(path: Path, *, exclusive: bool) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    deadline = time.monotonic() + HISTORY_LOCK_TIMEOUT

    try:
        while True:
            try:
                fcntl.flock(fd, operation | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise AppError(f"Timed out waiting for history lock: {path}")
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


@contextmanager
def recognition_lock() -> Iterator[None]:
    fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)

    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BusyError(
                "Another music recognition session is running."
            ) from exc

        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        yield
    finally:
        os.close(fd)


class History:
    def __init__(self, maximum: int = 500):
        self.maximum = maximum
        self.lock_path = HISTORY_FILE.with_suffix(".lock")

    def _read(self) -> list[Song]:
        try:
            data = read_json(HISTORY_FILE)
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            raise AppError(
                f"Cannot read history: {exc}. Existing history was not modified."
            ) from exc

        if not isinstance(data, list):
            raise AppError(
                "History must contain a JSON array. Existing history was not modified."
            )

        songs = []
        for index, item in enumerate(data, 1):
            try:
                songs.append(Song.from_dict(item))
            except (ValueError, TypeError) as exc:
                raise AppError(
                    f"Invalid history record #{index}: {exc}. "
                    "Existing history was not modified."
                ) from exc
        return songs

    def all(
        self,
        query: str | None = None,
        limit: int | None = None,
    ) -> list[Song]:
        with history_lock(self.lock_path, exclusive=False):
            songs = self._read()

        if query:
            needle = query.casefold()
            songs = [
                song for song in songs
                if any(
                    needle in value.casefold()
                    for value in (
                        song.title,
                        song.artist,
                        song.album,
                        *song.genres,
                        *song.lyrics,
                    )
                )
            ]
        return songs if limit is None else songs[:limit]

    def latest(self) -> Song | None:
        songs = self.all(limit=1)
        return songs[0] if songs else None

    def add(self, song: Song) -> int:
        with history_lock(self.lock_path, exclusive=True):
            songs = self._read()

            if (
                songs
                and songs[0].title.casefold() == song.title.casefold()
                and songs[0].artist.casefold() == song.artist.casefold()
            ):
                songs[0] = song
            else:
                songs.insert(0, song)

            songs = songs[:self.maximum]
            atomic_json(HISTORY_FILE, [asdict(item) for item in songs])
            return len(songs)

    def clear(self) -> None:
        # Explicit clearing can also reset malformed history after confirmation.
        with history_lock(self.lock_path, exclusive=True):
            atomic_json(HISTORY_FILE, [])

    def export(self, destination: Path) -> int:
        destination = destination.expanduser().resolve()
        protected = {
            path.resolve()
            for path in (
                CONFIG_FILE, HISTORY_FILE, self.lock_path, LOCK_FILE, LOG_FILE
            )
        }
        if destination in protected:
            raise AppError("Cannot export over an application state or lock file.")

        songs = self.all()
        fmt = destination.suffix.lstrip(".").casefold() or "json"

        if fmt == "json":
            atomic_json(destination, [asdict(song) for song in songs])
            return len(songs)

        output = io.StringIO(newline="")

        if fmt == "csv":
            writer = csv.writer(output)
            writer.writerow([
                "Timestamp", "Title", "Artist", "Album", "Year", "Genres",
                "Shazam URL", "Apple Music URL", "Spotify URL", "YouTube URL",
            ])
            for song in songs:
                writer.writerow([
                    song.timestamp, song.title, song.artist, song.album,
                    song.release_year, "; ".join(song.genres),
                    song.shazam_url, song.apple_music_url,
                    song.spotify_url, song.youtube_search_url,
                ])

        elif fmt in ("md", "markdown"):
            def cell(value: str) -> str:
                result = html.escape(clean_text(value), quote=False)
                for char in "\\|*_`[]":
                    result = result.replace(char, f"&#{ord(char)};")
                return result.replace("\n", "<br>")

            output.write("# Music Recognition History\n\n")
            output.write("| Time | Title | Artist | Album | Links |\n")
            output.write("| :--- | :--- | :--- | :--- | :--- |\n")

            for song in songs:
                links = []
                for label, url in (
                    ("Shazam", song.shazam_url),
                    ("Apple Music", song.apple_music_url),
                    ("Spotify", song.spotify_url),
                    ("YouTube", song.youtube_search_url),
                ):
                    if url:
                        encoded = urllib.parse.quote(
                            url, safe=":/?#@!$&'*+,;=%-._~"
                        )
                        links.append(f"[{label}](<{encoded}>)")

                output.write(
                    f"| {cell(song.timestamp)} | {cell(song.title)} "
                    f"| {cell(song.artist)} | {cell(song.album)} "
                    f"| {' • '.join(links)} |\n"
                )
        else:
            raise AppError("Export supports .json, .csv, .md, and .markdown.")

        atomic_write(destination, output.getvalue().encode("utf-8"))
        return len(songs)


# =============================================================================
# Subprocess utilities
# =============================================================================

def stop_child(
    proc: subprocess.Popen[Any],
    *,
    recorder: bool = False,
) -> None:
    if proc.poll() is not None:
        return

    try:
        proc.send_signal(signal.SIGINT if recorder else signal.SIGTERM)
    except ProcessLookupError:
        pass

    try:
        proc.wait(timeout=CHILD_STOP_TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        proc.wait()


def run_command(
    command: list[str],
    *,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    if timeout <= 0:
        raise subprocess.TimeoutExpired(command, timeout)

    proc = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(
            command, proc.returncode, stdout, stderr
        )
    finally:
        if proc.poll() is None:
            stop_child(proc)
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()


def copy_text(value: str) -> bool:
    if not shutil.which("wl-copy"):
        return False

    try:
        subprocess.run(
            ["wl-copy", "--type", "text/plain;charset=utf-8"],
            input=value,
            text=True,
            encoding="utf-8",
            timeout=3,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Clipboard failed: %s", exc)
        return False


def open_url(url: str) -> bool:
    if not url or not shutil.which("xdg-open"):
        return False

    try:
        subprocess.Popen(
            ["xdg-open", url],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        return True
    except OSError as exc:
        logger.warning("Browser launch failed: %s", exc)
        return False


# =============================================================================
# Notifications and cover art
# =============================================================================

NOTIFICATION_HELPER = r"""
import json
import subprocess
import sys

payload = json.loads(sys.argv[1])
try:
    result = subprocess.run(
        payload["command"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
except (OSError, subprocess.SubprocessError):
    raise SystemExit(0)

if result.returncode == 0:
    url = payload["actions"].get(result.stdout.strip())
    if url:
        try:
            subprocess.Popen(
                ["xdg-open", url],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            pass
"""


class Notifier:
    APP_NAME = "dusky-music-recognition"

    @classmethod
    def base(cls, duration: int = 4000) -> list[str]:
        return [
            "notify-send",
            "--app-name", cls.APP_NAME,
            "--urgency", "normal",
            "--expire-time", str(duration),
            "--hint",
            "string:x-canonical-private-synchronous:dusky-music",
        ]

    @classmethod
    def simple(cls, title: str, body: str) -> None:
        if not shutil.which("notify-send"):
            return
        try:
            run_command(
                cls.base() + ["--", one_line(title), html.escape(one_line(body))],
                timeout=3,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Notification failed: %s", exc)

    @classmethod
    def detected(cls, song: Song) -> None:
        if not shutil.which("notify-send"):
            return

        command = cls.base(8000)
        if song.local_cover_path:
            try:
                if Path(song.local_cover_path).is_file():
                    command.extend(["--icon", song.local_cover_path])
            except OSError:
                pass

        actions = {}
        if shutil.which("xdg-open"):
            actions = {
                "default": youtube_target(song),
                "spotify": spotify_target(song),
            }
            command.extend([
                "--wait",
                "--action=default=Open on YouTube",
                "--action=spotify=Open on Spotify",
            ])

        details = one_line(song.artist)
        if song.album:
            details += f" • {one_line(song.album)}"
        if song.release_year:
            details += f" ({one_line(song.release_year)})"

        command.extend(["--", one_line(song.title), html.escape(details)])

        try:
            if actions:
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        NOTIFICATION_HELPER,
                        json.dumps({
                            "command": command,
                            "actions": actions,
                        }),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                )
            else:
                run_command(command, timeout=3)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("Song notification failed: %s", exc)


def download_cover(song: Song) -> str:
    if not song.cover_url:
        return ""

    key = hashlib.sha256(
        f"{song.id}\0{song.cover_url}".encode("utf-8")
    ).hexdigest()

    # Extension does not imply conversion; consumers inspect image contents.
    destination = COVERS_DIR / f"{key}.jpg"

    try:
        if destination.is_file():
            if 0 < destination.stat().st_size <= MAX_COVER_BYTES:
                return str(destination)

        request = urllib.request.Request(
            song.cover_url,
            headers={
                "User-Agent": f"Dusky-Music/{VERSION}",
                "Accept": "image/*",
            },
        )
        deadline = time.monotonic() + 5
        payload = bytearray()

        with urllib.request.urlopen(request, timeout=4) as response:
            if response.status != 200:
                raise ValueError(f"HTTP status {response.status}")
            if not response.headers.get_content_type().startswith("image/"):
                raise ValueError("Cover response is not an image.")

            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Cover download time budget exhausted.")

                chunk = response.read1(65_536)
                if not chunk:
                    break
                payload.extend(chunk)

                if len(payload) > MAX_COVER_BYTES:
                    raise ValueError("Cover exceeds 5 MiB.")

        if not payload:
            raise ValueError("Empty cover image.")

        atomic_write(destination, bytes(payload))
        return str(destination)

    except (OSError, ValueError) as exc:
        logger.warning("Cover download failed: %s", exc)
        return ""


# =============================================================================
# Real audio metering
# =============================================================================

@dataclass(slots=True)
class Levels:
    seconds: float = 0.0
    rms: float | None = None
    peak: float | None = None
    clipping: bool = False
    receiving: bool = False


@dataclass(slots=True)
class Capture:
    seconds: float
    rms: float | None
    peak: float | None

    @property
    def silent(self) -> bool:
        return self.peak is None


def sample_statistics(data: bytes | bytearray) -> tuple[int, int, int]:
    """Return sample count, sum of squares, and absolute peak for native s16."""
    samples = array("h")
    samples.frombytes(data)

    total = 0
    peak = 0
    for value in samples:
        total += value * value
        magnitude = abs(value)
        if magnitude > peak:
            peak = magnitude

    return len(samples), total, peak


def db_levels(
    count: int,
    squares: int,
    peak: int,
) -> tuple[float | None, float | None]:
    if not count or not peak:
        return None, None
    rms = math.sqrt(squares / count)
    return (
        20 * math.log10(rms / 32768),
        20 * math.log10(peak / 32768),
    )


def meter_fraction(dbfs: float | None) -> float:
    if dbfs is None:
        return 0.0
    return max(0.0, min(1.0, (dbfs - METER_FLOOR_DBFS) / -METER_FLOOR_DBFS))


def db_text(value: float | None) -> str:
    return "−∞" if value is None else f"{value:.1f}"


def capture_panel(
    levels: Levels,
    recent: deque[float],
    *,
    source: str,
    elapsed: float,
    timeout: int,
    duration: int,
    attempt: int,
) -> Panel:
    if not levels.receiving:
        state, style = "Connecting to audio source", "warning"
    elif levels.peak is None:
        state, style = "Audio stream active · digital silence", "warning"
    elif levels.clipping:
        state, style = "Audio detected · possible clipping", "error"
    elif levels.rms is not None and levels.rms < -45:
        state, style = "Audio detected · quiet signal", "warning"
    else:
        state, style = "Audio detected", "success"

    glyphs = "▁▂▃▄▅▆▇█"
    graph = "".join(
        "·" if value == 0 else glyphs[min(7, int(value * 8))]
        for value in recent
    ).rjust(METER_COLUMNS, "·")

    width = 26
    filled = round(meter_fraction(levels.rms) * width)

    content = Text()
    content.append(f"♫  {source}\n", style="accent")
    content.append(f"{state}\n\n", style=style)
    content.append(graph + "\n", style=THEME.accent)
    content.append("━" * filled, style=style)
    content.append("─" * (width - filled), style="muted")
    content.append(
        f"\nRMS {db_text(levels.rms)} dBFS"
        f"   Peak {db_text(levels.peak)} dBFS\n",
        style="muted",
    )
    content.append(
        f"\nClip {levels.seconds:.1f}/{duration}s"
        f"   Session {elapsed:.1f}/{timeout}s"
        f"   Attempt {attempt}",
        style="fg",
    )

    return Panel(
        Align.center(content),
        title=Text("Listening", style="accent"),
        subtitle=Text("Measured audio-level history", style="muted"),
        border_style=THEME.muted,
        box=box.ROUNDED,
        padding=(1, 2),
    )


# =============================================================================
# PipeWire capture
# =============================================================================

class AudioEngine:
    checked = False

    @classmethod
    def check_interface(cls) -> None:
        if cls.checked:
            return

        try:
            result = run_command(
                ["pw-record", "--help"],
                timeout=DISCOVERY_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise AppError("pw-record --help timed out.") from exc

        help_text = result.stdout + "\n" + result.stderr
        if result.returncode != 0:
            raise AppError(
                "Cannot inspect pw-record: " + one_line(help_text)[:2000]
            )

        missing = [
            option for option in (
                "--raw", "--format", "--rate", "--channels", "--target"
            )
            if option not in help_text
        ]
        if missing:
            raise AppError(
                "Installed pw-record lacks advertised required options: "
                + ", ".join(missing)
                + ". Check pw-record --help."
            )

        # The requested 's16' format and array('h') are both native-endian.
        if array("h").itemsize != PCM_WIDTH:
            raise AppError("This platform does not provide 16-bit native shorts.")

        cls.checked = True

    @staticmethod
    def default_source(kind: str) -> str:
        try:
            result = run_command(["pw-dump"], timeout=DISCOVERY_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            raise AppError("PipeWire source discovery timed out.") from exc

        if result.returncode != 0:
            raise AppError(
                "pw-dump failed: "
                + (
                    one_line(result.stderr or result.stdout)[:2000]
                    or f"exit status {result.returncode}"
                )
            )

        try:
            objects = parse_json(result.stdout)
        except ValueError as exc:
            raise AppError("pw-dump returned invalid JSON.") from exc

        if not isinstance(objects, list):
            raise AppError("pw-dump did not return a JSON array.")

        key = f"default.audio.{kind}"

        for obj in objects:
            if not isinstance(obj, dict):
                continue

            info = obj.get("info")
            info = info if isinstance(info, dict) else {}
            props = obj.get("props")
            if not isinstance(props, dict):
                props = info.get("props")

            if (
                not isinstance(props, dict)
                or props.get("metadata.name") != "default"
            ):
                continue

            entries = obj.get("metadata")
            if not isinstance(entries, list):
                continue

            for entry in entries:
                if not isinstance(entry, dict) or entry.get("key") != key:
                    continue
                if entry.get("subject", 0) != 0:
                    continue

                value = entry.get("value")
                if isinstance(value, str):
                    try:
                        value = parse_json(value)
                    except ValueError:
                        continue

                if isinstance(value, dict):
                    name = value.get("name")
                    if isinstance(name, str) and name.strip():
                        return name

        raise AppError(
            f"No active default PipeWire audio {kind}. "
            "Check wpctl status and your selected device."
        )

    @classmethod
    def record(
        cls,
        source: str,
        duration: int,
        destination: Path,
        *,
        monitor: bool,
        deadline: float,
        callback: Callable[[Levels], None] | None = None,
    ) -> Capture:
        """
        Read actual PCM frames, stream them into our WAV writer, and meter them.

        A recorder intentionally stopped after sufficient PCM was received is
        not rejected solely because its shutdown exit status is nonzero.
        """
        target_frames = duration * PCM_RATE
        target_bytes = target_frames * PCM_FRAME_BYTES

        command = [
            "pw-record",
            "--raw",
            "--target", source,
            "--rate", str(PCM_RATE),
            "--channels", str(PCM_CHANNELS),
            "--format", "s16",
        ]
        if monitor:
            command.extend(["-P", "{ stream.capture.sink = true }"])
        command.append("-")

        logger.info("Capture command: %s", shlex.join(command))
        destination.unlink(missing_ok=True)

        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            bufsize=0,
        )

        assert proc.stdout is not None
        assert proc.stderr is not None

        stdout_fd = proc.stdout.fileno()
        stderr_fd = proc.stderr.fileno()

        diagnostic = bytearray()
        pending = bytearray()
        meter_window = bytearray()

        written = 0
        sample_count = 0
        sum_squares = 0
        absolute_peak = 0

        started = time.monotonic()
        first_audio: float | None = None
        last_audio: float | None = None
        last_meter = 0.0

        def save_diagnostic(chunk: bytes) -> None:
            diagnostic.extend(chunk)
            if len(diagnostic) > MAX_DIAGNOSTIC_BYTES:
                del diagnostic[:-MAX_DIAGNOSTIC_BYTES]

        def detail() -> str:
            return one_line(diagnostic.decode("utf-8", errors="replace"))

        def update(now: float, *, force: bool = False) -> None:
            nonlocal last_meter
            if callback is None:
                return
            if not force and now - last_meter < METER_INTERVAL:
                return

            count, squares, peak = sample_statistics(meter_window)
            rms_db, peak_db = db_levels(count, squares, peak)

            callback(Levels(
                seconds=written / (PCM_RATE * PCM_FRAME_BYTES),
                rms=rms_db,
                peak=peak_db,
                clipping=peak >= 32760,
                receiving=written > 0,
            ))
            last_meter = now

        try:
            os.set_blocking(stdout_fd, False)
            os.set_blocking(stderr_fd, False)
            open_fds = {stdout_fd, stderr_fd}

            with wave.open(str(destination), "wb") as output:
                output.setnchannels(PCM_CHANNELS)
                output.setsampwidth(PCM_WIDTH)
                output.setframerate(PCM_RATE)
                output.setnframes(target_frames)

                update(started, force=True)

                while written < target_bytes:
                    now = time.monotonic()

                    if now >= deadline:
                        raise RecognitionTimeout(
                            "Recognition budget expired during capture. "
                            f"Received {written / (PCM_RATE * PCM_FRAME_BYTES):.2f}s "
                            f"of {duration}s."
                        )

                    if first_audio is None:
                        if now - started >= CAPTURE_STARTUP_TIMEOUT:
                            raise AppError(
                                "No audio frames arrived within "
                                f"{CAPTURE_STARTUP_TIMEOUT:.0f}s. "
                                f"Target: {source!r}. "
                                f"Recorder: {detail() or '(no diagnostic)'}"
                            )
                    else:
                        assert last_audio is not None
                        if now - last_audio >= CAPTURE_STALL_TIMEOUT:
                            raise AppError(
                                "The audio stream stopped delivering samples. "
                                f"Target: {source!r}. "
                                f"Recorder: {detail() or '(no diagnostic)'}"
                            )
                        if (
                            now - first_audio
                            > duration + CAPTURE_FINISH_ALLOWANCE
                        ):
                            raise AppError(
                                "The audio stream delivered too little data "
                                "for the elapsed recording time."
                            )

                    if stdout_fd not in open_fds:
                        raise AppError(
                            "pw-record closed its output before capture completed "
                            f"(status {proc.poll()}). "
                            f"Received {written}/{target_bytes} bytes. "
                            f"Recorder: {detail() or '(no diagnostic)'}. "
                            f"See {LOG_FILE}."
                        )

                    ready, _, _ = select.select(
                        list(open_fds),
                        [],
                        [],
                        min(METER_INTERVAL, max(0.0, deadline - now)),
                    )
                    # Collect an accompanying stderr message before stdout EOF.
                    ready.sort(key=lambda fd: fd != stderr_fd)

                    for fd in ready:
                        try:
                            chunk = os.read(fd, 65_536)
                        except BlockingIOError:
                            continue

                        if not chunk:
                            open_fds.discard(fd)
                            continue

                        if fd == stderr_fd:
                            save_diagnostic(chunk)
                            continue

                        received_at = time.monotonic()
                        if first_audio is None:
                            first_audio = received_at
                        last_audio = received_at

                        pending.extend(chunk)
                        usable = min(
                            len(pending) - len(pending) % PCM_FRAME_BYTES,
                            target_bytes - written,
                        )
                        if not usable:
                            continue

                        block = bytes(pending[:usable])
                        del pending[:usable]

                        count, squares, peak = sample_statistics(block)
                        sample_count += count
                        sum_squares += squares
                        absolute_peak = max(absolute_peak, peak)

                        # wave expects native-endian data and handles conversion
                        # to the WAV file's little-endian sample representation.
                        output.writeframesraw(block)
                        written += len(block)

                        meter_window.extend(block)
                        if len(meter_window) > METER_WINDOW_BYTES:
                            del meter_window[:-METER_WINDOW_BYTES]

                    update(time.monotonic())

                stop_child(proc, recorder=True)

                # Drain final diagnostics after the intentionally requested stop.
                while True:
                    try:
                        chunk = os.read(stderr_fd, 65_536)
                    except BlockingIOError:
                        break
                    if not chunk:
                        break
                    save_diagnostic(chunk)

                update(time.monotonic(), force=True)

            with wave.open(str(destination), "rb") as check:
                if (
                    check.getnchannels() != PCM_CHANNELS
                    or check.getsampwidth() != PCM_WIDTH
                    or check.getframerate() != PCM_RATE
                    or check.getnframes() != target_frames
                ):
                    raise AppError("Generated WAV failed format validation.")

            expected_size = target_bytes
            if destination.stat().st_size < expected_size:
                raise AppError("Generated WAV is smaller than its PCM payload.")

        except (AppError, OSError, EOFError, wave.Error):
            logger.error(
                "Capture failed: command=%s status=%s bytes=%s/%s stderr=%r",
                shlex.join(command),
                proc.poll(),
                written,
                target_bytes,
                detail(),
            )
            raise
        finally:
            if proc.poll() is None:
                stop_child(proc)
            proc.stdout.close()
            proc.stderr.close()

        rms, peak = db_levels(sample_count, sum_squares, absolute_peak)

        logger.info(
            "Captured %ss, RMS=%s dBFS, peak=%s dBFS, "
            "shutdown_status=%s, stderr=%r",
            duration, db_text(rms), db_text(peak), proc.returncode, detail(),
        )
        return Capture(float(duration), rms, peak)


# =============================================================================
# SongRec
# =============================================================================

class RecognitionEngine:
    @staticmethod
    def parse(raw: str, source: str) -> Song | None:
        try:
            data = parse_json(raw)
        except ValueError as exc:
            raise AppError("SongRec returned invalid JSON.") from exc

        if not isinstance(data, dict):
            raise AppError("SongRec returned an unexpected JSON structure.")

        track = data.get("track")
        if track is None:
            matches = data.get("matches")
            if isinstance(matches, list) and not matches:
                return None
            raise AppError(
                "SongRec returned neither a track nor an explicit empty matches "
                "list. Check the installed SongRec interface and application log."
            )

        if not isinstance(track, dict):
            raise AppError("SongRec returned an invalid track object.")

        def obj(value: Any) -> dict[str, Any]:
            return value if isinstance(value, dict) else {}

        def records(value: Any) -> list[dict[str, Any]]:
            return (
                [item for item in value if isinstance(item, dict)]
                if isinstance(value, list)
                else []
            )

        def text(value: Any) -> str:
            return value.strip() if isinstance(value, str) else ""

        title = one_line(text(track.get("title")))
        artist = one_line(text(track.get("subtitle")))
        if not title:
            raise AppError("SongRec returned a track without a title.")

        key = track.get("key")
        song_id = (
            str(key)
            if isinstance(key, (str, int))
            and not isinstance(key, bool)
            and str(key).strip()
            else hashlib.sha256(
                f"{title}\0{artist}".casefold().encode("utf-8")
            ).hexdigest()
        )

        images = obj(track.get("images"))
        share = obj(track.get("share"))

        album = ""
        year = ""
        lyrics = []
        primary = text(obj(track.get("genres")).get("primary"))
        genres = [one_line(primary)] if primary else []

        for section in records(track.get("sections")):
            kind = text(section.get("type")).upper()
            if kind == "SONG":
                for item in records(section.get("metadata")):
                    label = text(item.get("title")).casefold()
                    value = one_line(text(item.get("text")))
                    if label == "album":
                        album = value
                    elif label == "released":
                        year = value
            elif kind == "LYRICS":
                lines = section.get("text")
                if isinstance(lines, list):
                    lyrics = [
                        clean_text(line).strip()
                        for line in lines
                        if isinstance(line, str) and line.strip()
                    ]

        hub = obj(track.get("hub"))
        actions = records(hub.get("actions"))
        for item in records(hub.get("options")) + records(hub.get("providers")):
            actions.extend(records(item.get("actions")))

        spotify = ""
        apple = ""

        for action in actions:
            uri = text(action.get("uri"))
            if not uri:
                continue
            if uri.startswith("spotify:track:"):
                track_id = uri.rsplit(":", 1)[-1]
                if track_id:
                    spotify = (
                        "https://open.spotify.com/track/"
                        + urllib.parse.quote(track_id, safe="")
                    )
                continue

            try:
                parsed = urllib.parse.urlsplit(uri)
                host = (parsed.hostname or "").casefold()
            except ValueError:
                continue

            if parsed.scheme not in ("http", "https"):
                continue
            if host == "open.spotify.com":
                spotify = uri
            elif host == "music.apple.com":
                apple = uri

        now = datetime.now(timezone.utc)
        query = f"{title} {artist}".strip()

        return Song(
            id=song_id,
            title=title,
            artist=artist,
            album=album,
            release_year=year,
            genres=genres,
            cover_url=(
                text(images.get("coverarthq"))
                or text(images.get("coverart"))
                or text(share.get("image"))
            ),
            shazam_url=text(track.get("url")) or text(share.get("href")),
            apple_music_url=apple,
            spotify_url=spotify or (
                "https://open.spotify.com/search/"
                + urllib.parse.quote(query, safe="")
            ),
            youtube_search_url=(
                "https://www.youtube.com/results?"
                + urllib.parse.urlencode({"search_query": query})
            ),
            lyrics=lyrics,
            timestamp=now.strftime("%Y-%m-%d %H:%M:%S UTC"),
            epoch=now.timestamp(),
            source=source,
            raw=data,
        )

    @classmethod
    def recognize(
        cls,
        path: Path,
        source: str,
        timeout: float,
    ) -> Song | None:
        try:
            result = run_command(
                ["songrec", "audio-file-to-recognized-song", str(path)],
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RecognitionTimeout(
                "SongRec exceeded the remaining recognition budget."
            ) from exc

        if result.returncode != 0:
            raise AppError(
                f"SongRec failed (status {result.returncode}): "
                + (
                    one_line(result.stderr or result.stdout)[:2000]
                    or "no diagnostic output"
                )
            )

        if not result.stdout.strip():
            raise AppError("SongRec returned no JSON output.")

        try:
            return cls.parse(result.stdout, source)
        except AppError:
            logger.error("Unexpected SongRec output: %s", result.stdout[:4000])
            raise


# =============================================================================
# Presentation and interactive history
# =============================================================================

def song_links(song: Song) -> Text:
    result = Text()
    for label, url, color in (
        ("Shazam", song.shazam_url, THEME.accent),
        ("Spotify", song.spotify_url, THEME.success),
        ("YouTube", song.youtube_search_url, THEME.warning),
        ("Apple Music", song.apple_music_url, THEME.accent),
    ):
        if not url:
            continue
        if result.plain:
            result.append("  •  ", style="muted")
        result.append(label, style=Style(color=color, link=url))
    return result


def song_card(
    song: Song,
    *,
    saved: bool = True,
    count: int | None = None,
) -> Panel:
    content = Text()
    content.append(f"♫  {one_line(song.title)}\n", style=f"bold {THEME.fg}")
    content.append(
        f"   {one_line(song.artist) or 'Unknown artist'}",
        style="accent",
    )

    metadata = [
        one_line(value)
        for value in (
            song.album,
            song.release_year,
            song.genres[0] if song.genres else "",
        )
        if value
    ]
    if metadata:
        content.append("\n\n" + "  •  ".join(metadata), style="fg")

    links = song_links(song)
    if links.plain:
        content.append("\n\n")
        content.append_text(links)

    if song.lyrics:
        preview = " / ".join(one_line(line) for line in song.lyrics[:2])
        content.append(f"\n\n“{preview}”", style=f"italic {THEME.muted}")

    footer = (
        "Saved to history" if saved else "History save could not be confirmed"
    )
    if saved and count is not None:
        footer += f" · {count} records"

    return Panel(
        content,
        title=Text("✓ Song Identified", style="accent"),
        subtitle=Text(footer, style="muted" if saved else "warning"),
        subtitle_align="right",
        border_style=THEME.accent,
        box=box.ROUNDED,
        padding=(1, 2),
    )


def history_table(songs: list[Song], query: str | None = None) -> Table:
    title = f"♫ Recognition History · {len(songs)} records"
    if query is not None:
        title += f" · {one_line(query)!r}"

    table = Table(
        title=Text(title),
        title_style="header",
        header_style="header",
        border_style=THEME.muted,
        box=box.ROUNDED,
    )
    table.add_column("#", justify="right", style="muted", width=4)
    table.add_column("Time (UTC)", style="muted", width=16)
    table.add_column("Title", style=f"bold {THEME.fg}", min_width=12)
    table.add_column("Artist", style=THEME.accent, min_width=10)
    table.add_column("Album", style=THEME.fg, min_width=8)
    table.add_column("Links", min_width=10)

    for index, song in enumerate(songs, 1):
        table.add_row(
            Text(str(index)),
            Text(one_line(song.timestamp).removesuffix(" UTC")[:16]),
            Text(one_line(song.title)),
            Text(one_line(song.artist)),
            Text(one_line(song.album) or "—"),
            song_links(song),
        )
    return table


def is_interactive() -> bool:
    return sys.stdin.isatty() and console.is_terminal


def read_key() -> str:
    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)

    try:
        tty.setcbreak(fd)
        first = os.read(fd, 1)
        if not first:
            return ""

        sequence = bytearray(first)
        if first == b"\x1b":
            deadline = time.monotonic() + 0.05
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                ready, _, _ = select.select([fd], [], [], remaining)
                if not ready:
                    break
                chunk = os.read(fd, 32)
                if not chunk:
                    break
                sequence.extend(chunk)

        return sequence.decode("utf-8", errors="replace").lower()
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, original)


def browse_history(history: History) -> None:
    songs = history.all()
    if not songs:
        console.print(Text("Recognition history is empty.", style="warning"))
        return

    if not shutil.which("fzf") or not is_interactive():
        console.print(history_table(songs))
        return

    lookup = {}
    lines = []
    for index, song in enumerate(songs, 1):
        key = str(index)
        lookup[key] = song
        lines.append(
            f"{key}\t"
            + one_line(
                f"{song.title} — {song.artist} "
                f"({song.album or 'Single'}) [{song.timestamp}]"
            )
        )

    env = os.environ.copy()
    env["FZF_DEFAULT_OPTS"] = ""
    env.pop("FZF_DEFAULT_OPTS_FILE", None)

    command = [
        "fzf",
        f"--color={THEME.fzf_colors()}",
        "--no-multi",
        "--delimiter=\t",
        "--with-nth=2..",
        "--header=ENTER YouTube · CTRL-S Spotify · CTRL-Y Copy · ESC Exit",
        "--prompt=♫ History > ",
        "--border=rounded",
        "--border-label= Music History ",
        "--border-label-pos=0:top",
        "--layout=reverse",
        "--height=60%",
        "--expect=ctrl-y,ctrl-s",
    ]

    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    try:
        output, _ = proc.communicate("\n".join(lines) + "\n")
    finally:
        if proc.poll() is None:
            stop_child(proc)
        if proc.stdin is not None:
            proc.stdin.close()
        if proc.stdout is not None:
            proc.stdout.close()

    if proc.returncode in (1, 130):
        return
    if proc.returncode != 0:
        raise AppError(f"fzf exited with status {proc.returncode}.")

    selected = output.splitlines()
    if len(selected) < 2:
        raise AppError("fzf returned an unexpected selection format.")

    song = lookup.get(selected[1].split("\t", 1)[0])
    if song is None:
        raise AppError("fzf returned an unknown record.")

    if selected[0] == "ctrl-y":
        if not copy_text(f"{song.title} - {song.artist}"):
            raise AppError("Clipboard copy failed.")
    else:
        url = (
            spotify_target(song)
            if selected[0] == "ctrl-s"
            else youtube_target(song)
        )
        if not open_url(url):
            raise AppError("Could not launch the browser.")


def interactive_actions(song: Song | None, history: History) -> None:
    if not is_interactive():
        return

    target = song

    while True:
        prompt = Text("\n")
        for index, (key, label) in enumerate((
            ("H", "History"),
            ("F", "Search"),
            ("O", "YouTube"),
            ("S", "Spotify"),
            ("C", "Copy"),
            ("Q", "Exit"),
        )):
            if index:
                prompt.append("   ")
            prompt.append(f"[{key}]", style="accent")
            prompt.append(f" {label}", style="fg")
        console.print(prompt)

        key = read_key()
        if key in ("q", "\x1b", "\r", "\n", "\x04", ""):
            return

        try:
            if key == "h":
                songs = history.all()
                console.print(history_table(songs))
                target = songs[0] if songs else None
                continue

            if key == "f":
                browse_history(history)
                return

            if key not in ("o", "s", "c"):
                continue

            target = target or history.latest()
            if target is None:
                console.print(Text("No song is available.", style="warning"))
                continue

            if key == "c":
                if not copy_text(f"{target.title} - {target.artist}"):
                    raise AppError("Clipboard copy failed.")
                console.print(Text("Copied to clipboard.", style="success"))
            else:
                url = (
                    spotify_target(target) if key == "s" else youtube_target(target)
                )
                if not open_url(url):
                    raise AppError("Could not launch the browser.")
            return

        except (AppError, OSError, subprocess.SubprocessError) as exc:
            logger.warning("Interactive action failed: %s", exc)
            console.print(Text(str(exc), style="warning"))


# =============================================================================
# Recognition workflow
# =============================================================================

@dataclass(slots=True)
class SessionResult:
    song: Song | None
    elapsed: float
    attempts: int
    analyses: int
    silent_clips: int


def identify(
    *,
    source_type: str,
    source_name: str,
    duration: int,
    timeout: int,
    show_live: bool,
) -> SessionResult:
    started = time.monotonic()
    deadline = started + timeout
    attempts = 0
    analyses = 0
    silent_clips = 0
    song = None

    live_context = (
        Live(console=console, refresh_per_second=10, transient=True)
        if show_live else nullcontext(None)
    )

    with tempfile.TemporaryDirectory(prefix="dusky_songrec_") as directory:
        audio_path = Path(directory) / "recording.wav"

        with live_context as live:
            while deadline - time.monotonic() > duration + MIN_ANALYSIS_BUDGET:
                attempts += 1
                recent: deque[float] = deque(maxlen=METER_COLUMNS)

                def update(levels: Levels) -> None:
                    if live is None:
                        return
                    recent.append(meter_fraction(levels.rms))
                    live.update(capture_panel(
                        levels,
                        recent,
                        source=(
                            "Microphone" if source_type == "mic" else "System Audio"
                        ),
                        elapsed=time.monotonic() - started,
                        timeout=timeout,
                        duration=duration,
                        attempt=attempts,
                    ))

                capture = AudioEngine.record(
                    source_name,
                    duration,
                    audio_path,
                    monitor=source_type == "system",
                    deadline=deadline,
                    callback=update if live is not None else None,
                )

                if capture.silent:
                    silent_clips += 1
                    logger.info("Attempt %s contained digital silence.", attempts)
                    continue

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RecognitionTimeout(
                        "Recognition budget exhausted after recording."
                    )

                if live is not None:
                    content = Text()
                    content.append("Analyzing audio fingerprint…\n", style="accent")
                    content.append(
                        f"\nCaptured {capture.seconds:.1f}s"
                        f" · RMS {db_text(capture.rms)} dBFS"
                        f" · Peak {db_text(capture.peak)} dBFS",
                        style="muted",
                    )
                    live.update(Panel(
                        Align.center(content),
                        title=Text("Recognizing", style="accent"),
                        border_style=THEME.muted,
                        box=box.ROUNDED,
                        padding=(1, 2),
                    ))

                song = RecognitionEngine.recognize(
                    audio_path, source_type, remaining
                )
                analyses += 1
                if song is not None:
                    break

    if analyses == 0:
        if silent_clips:
            raise AppError(
                f"Capture worked, but all {silent_clips} clips contained digital "
                "silence. Check playback, the selected device, application "
                "routing, or microphone mute state."
            )
        raise RecognitionTimeout(
            "The budget was insufficient to complete a recognition attempt."
        )

    return SessionResult(
        song, time.monotonic() - started, attempts, analyses, silent_clips
    )


def run_recognition(
    config: AppConfig,
    *,
    source_type: str,
    duration: int,
    timeout: int,
    notify: bool,
    auto_copy: bool,
    json_output: bool,
    interactive: bool,
) -> int:
    history = History(config.max_history)
    warnings = []
    saved = False
    count = None

    try:
        with recognition_lock():
            missing = [
                name for name in ("songrec", "pw-record", "pw-dump")
                if not shutil.which(name)
            ]
            if missing:
                raise AppError("Missing required binaries: " + ", ".join(missing))

            AudioEngine.check_interface()
            source_name = AudioEngine.default_source(
                "source" if source_type == "mic" else "sink"
            )

            logger.info(
                "Session: type=%s target=%r clip=%ss timeout=%ss",
                source_type, source_name, duration, timeout,
            )

            if notify and not json_output:
                Notifier.simple(
                    "Music Recognition",
                    "Listening to microphone..."
                    if source_type == "mic"
                    else "Listening to system audio...",
                )

            result = identify(
                source_type=source_type,
                source_name=source_name,
                duration=duration,
                timeout=timeout,
                show_live=not json_output and console.is_terminal,
            )

            song = result.song
            if song is not None:
                if config.download_covers and song.cover_url:
                    song.local_cover_path = download_cover(song)
                    if not song.local_cover_path:
                        warnings.append("Cover art was unavailable.")

                try:
                    count = history.add(song)
                    saved = True
                except (AppError, OSError, ValueError) as exc:
                    warning = f"History update could not be confirmed: {exc}"
                    warnings.append(warning)
                    logger.error("%s", warning)

    except BusyError:
        raise
    except (AppError, OSError, subprocess.SubprocessError) as exc:
        if notify and not json_output:
            Notifier.simple("Music Recognition Failed", str(exc)[:500])
        raise

    # No singleton lock is held during interactive actions.
    if song is None:
        if json_output:
            emit_json({
                "error": "No match found.",
                "kind": "no_match",
                "elapsed": round(result.elapsed, 3),
                "attempts": result.attempts,
                "completed_analyses": result.analyses,
                "silent_clips": result.silent_clips,
            })
        else:
            if notify:
                Notifier.simple(
                    "Music Recognition",
                    f"No match found after {result.elapsed:.1f}s.",
                )

            content = Text("No Match Found\n", style="bold fg")
            content.append(
                f"\n{result.analyses} completed analyses"
                f" · {result.elapsed:.1f}s elapsed\n",
                style="muted",
            )
            content.append(
                "\nAudio was captured, but the service did not identify a song.",
                style="warning",
            )
            console.print(Panel(
                Align.center(content),
                border_style=THEME.muted,
                box=box.ROUNDED,
                padding=(1, 2),
            ))
            if interactive:
                interactive_actions(None, history)
        return NO_MATCH

    if auto_copy and not copy_text(f"{song.title} - {song.artist}"):
        warnings.append("Automatic clipboard copy failed.")

    logger.info("Identified: %s — %s", song.title, song.artist)

    if json_output:
        payload = asdict(song)
        payload.update({
            "history_saved": saved,
            "elapsed": round(result.elapsed, 3),
            "attempts": result.attempts,
        })
        if warnings:
            payload["warnings"] = warnings
        emit_json(payload)
    else:
        if notify:
            Notifier.detected(song)

        console.print(song_card(song, saved=saved, count=count))
        for warning in warnings:
            console.print(Text(warning, style="warning"))

        if interactive:
            interactive_actions(song, history)

    return OK if saved else ERROR


# =============================================================================
# Status and CLI
# =============================================================================

def show_status() -> int:
    table = Table(
        title=Text("Dusky Music Recognition · Status"),
        title_style="header",
        header_style="header",
        border_style=THEME.muted,
        box=box.ROUNDED,
    )
    table.add_column("Component", style=f"bold {THEME.fg}")
    table.add_column("Result", style=THEME.accent)

    healthy = True
    table.add_row("Version", Text(VERSION))
    table.add_row("Python", Text(sys.version.split()[0]))
    table.add_row("Configuration", Text(str(CONFIG_FILE)))
    table.add_row("Log", Text(str(LOG_FILE)))

    selected_source = None
    try:
        config = AppConfig.load(create=False)
        selected_source = config.default_source
        table.add_row("Settings", Text(
            f"source={config.default_source}, clip={config.record_duration}s, "
            f"budget={config.timeout}s, history={config.max_history}"
        ))
        if config.timeout <= config.record_duration:
            healthy = False
            table.add_row(
                "Default timing",
                Text("Budget must exceed clip duration.", style="error"),
            )
    except (AppError, OSError, ValueError) as exc:
        healthy = False
        table.add_row("Settings", Text(str(exc), style="error"))

    try:
        table.add_row("History", Text(f"{len(History().all())} records"))
    except (AppError, OSError, ValueError) as exc:
        healthy = False
        table.add_row("History", Text(str(exc), style="error"))

    required = {"songrec", "pw-record", "pw-dump"}
    for binary in (
        "songrec", "pw-record", "pw-dump",
        "notify-send", "wl-copy", "xdg-open", "fzf",
    ):
        path = shutil.which(binary)
        if binary in required and not path:
            healthy = False
        table.add_row(
            binary,
            Text(
                path or (
                    "Missing — required" if binary in required else "Not installed — optional"
                ),
                style="success" if path else "warning",
            ),
        )

    if shutil.which("pw-record"):
        try:
            AudioEngine.check_interface()
            table.add_row(
                "Raw recorder interface",
                Text("Required options advertised", style="success"),
            )
        except (AppError, OSError, subprocess.SubprocessError) as exc:
            healthy = False
            table.add_row("Raw recorder interface", Text(str(exc), style="error"))

    if shutil.which("pw-dump"):
        for kind, label, mode in (
            ("sink", "Default playback", "system"),
            ("source", "Default microphone", "mic"),
        ):
            try:
                table.add_row(label, Text(AudioEngine.default_source(kind)))
            except (AppError, OSError, subprocess.SubprocessError) as exc:
                if selected_source == mode:
                    healthy = False
                table.add_row(label, Text(str(exc), style="warning"))

    table.add_row("Scope", Text(
        "Checks settings, history, binary availability, recorder options, "
        "and active defaults. Actual audio and Shazam connectivity require "
        "a recognition test.",
        style="muted",
    ))
    console.print(table)
    return OK if healthy else ERROR


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="music_recognition",
        description="Dusky Music Recognition — PipeWire audio and SongRec",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  music_recognition.py
  music_recognition.py --mic
  music_recognition.py --system -d 8 -t 45
  music_recognition.py --no-interactive
  music_recognition.py --json
  music_recognition.py history
  music_recognition.py -s "Daft Punk"
  music_recognition.py fzf
  music_recognition.py last
  music_recognition.py --export ~/songs.csv
  music_recognition.py clear --yes
  music_recognition.py status

Configuration:
  {CONFIG_FILE}

The level display measures audio samples; it is not a simulated animation.
""",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=(
            "listen", "history", "log", "fzf",
            "last", "status", "clear", "export",
        ),
        help="Command; default: listen",
    )

    recording = parser.add_argument_group("Recognition")
    source = recording.add_mutually_exclusive_group()
    source.add_argument("-m", "--mic", action="store_true", help="Use microphone")
    source.add_argument("--system", action="store_true", help="Use system playback")

    recording.add_argument(
        "-d", "--duration", type=positive_int,
        help="Clip duration in seconds; default: configuration",
    )
    recording.add_argument(
        "-t", "--timeout", type=positive_int,
        help="Capture-and-analysis budget in seconds",
    )
    recording.add_argument(
        "-c", "--copy", action="store_true",
        help="Copy identified title and artist",
    )
    recording.add_argument(
        "--no-notify", action="store_true",
        help="Disable desktop notifications",
    )
    recording.add_argument(
        "--no-interactive", action="store_true",
        help="Exit without waiting for post-recognition actions",
    )
    recording.add_argument(
        "--json", action="store_true",
        help="JSON recognition output; disables UI and notifications",
    )

    history = parser.add_argument_group("History")
    history.add_argument("-H", "--history", action="store_true")
    history.add_argument("-F", "--fzf", action="store_true")
    history.add_argument("-s", "--search", metavar="QUERY")
    history.add_argument("-l", "--last", action="store_true")
    history.add_argument(
        "--limit", type=positive_int,
        help="History display limit; default: 50",
    )
    history.add_argument("--clear-history", action="store_true")
    history.add_argument(
        "--yes", action="store_true",
        help="Confirm history clearing without prompting",
    )
    history.add_argument(
        "--export", metavar="FILE",
        help="Export history as JSON, CSV, or Markdown",
    )

    parser.add_argument("--status", action="store_true")
    parser.add_argument(
        "-v", "--version", action="version",
        version=f"%(prog)s {VERSION}",
    )
    return parser


def resolve_command(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> str:
    requested = []
    if args.command:
        requested.append("history" if args.command == "log" else args.command)

    for enabled, name in (
        (args.status, "status"),
        (args.clear_history, "clear"),
        (args.export is not None, "export"),
        (args.fzf, "fzf"),
        (args.last, "last"),
        (args.history or args.search is not None, "history"),
    ):
        if enabled:
            requested.append(name)

    if len(set(requested)) > 1:
        parser.error("Choose only one command/action.")

    command = requested[0] if requested else "listen"

    if command != "listen" and any((
        args.mic, args.system, args.duration is not None,
        args.timeout is not None, args.copy, args.no_notify,
        args.no_interactive, args.json,
    )):
        parser.error("Recognition options apply only to listen.")

    if args.limit is not None and command != "history":
        parser.error("--limit applies only to history.")
    if args.yes and command != "clear":
        parser.error("--yes applies only to clear.")

    return command


def dispatch(
    args: argparse.Namespace,
    command: str,
    parser: argparse.ArgumentParser,
) -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    setup_logging()

    if command == "status":
        return show_status()

    history = History()

    if command == "clear":
        if not args.yes and not is_interactive():
            parser.error("Noninteractive clearing requires --yes.")

        confirmed = args.yes
        if not confirmed:
            try:
                answer = console.input(
                    "[warning]Clear recognition history? "
                    "Cover cache is retained. (y/N): [/warning]"
                )
            except EOFError:
                answer = ""
            confirmed = answer.strip().casefold() in ("y", "yes")

        if confirmed:
            history.clear()
            console.print(Text("History cleared.", style="success"))
        else:
            console.print(Text("Aborted.", style="muted"))
        return OK

    if command == "export":
        destination = Path(
            args.export or str(STATE_DIR / "history_export.csv")
        ).expanduser().resolve()
        count = history.export(destination)
        console.print(Text(
            f"Exported {count} records to {destination}",
            style="success",
        ))
        return OK

    if command == "fzf":
        browse_history(history)
        return OK

    if command == "last":
        songs = history.all()
        if songs:
            console.print(song_card(songs[0], count=len(songs)))
        else:
            console.print(Text("History is empty.", style="warning"))
        return OK

    if command == "history":
        songs = history.all(
            query=args.search,
            limit=args.limit if args.limit is not None else 50,
        )
        if songs:
            console.print(history_table(songs, args.search))
        else:
            console.print(Text(
                f"No songs matching {args.search!r}."
                if args.search else "History is empty.",
                style="warning",
            ))
        return OK

    config = AppConfig.load()
    source_type = (
        "mic" if args.mic else "system" if args.system else config.default_source
    )
    duration = config.record_duration if args.duration is None else args.duration
    timeout = config.timeout if args.timeout is None else args.timeout

    if timeout <= duration:
        parser.error("--timeout must exceed --duration to allow analysis.")

    return run_recognition(
        config,
        source_type=source_type,
        duration=duration,
        timeout=timeout,
        notify=config.notifications and not args.no_notify,
        auto_copy=args.copy or config.auto_copy,
        json_output=args.json,
        interactive=(
            not args.json and not args.no_interactive and is_interactive()
        ),
    )


def install_signal_handlers() -> None:
    def terminate(signum: int, _frame: Any) -> None:
        # Avoid a repeated termination signal interrupting child/TTY cleanup.
        for item in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(item, signal.SIG_IGN)
        raise SystemExit(128 + signum)

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, terminate)


def main() -> int:
    install_signal_handlers()
    parser = build_parser()
    args = parser.parse_args()
    command = resolve_command(args, parser)

    try:
        return dispatch(args, command, parser)
    except BrokenPipeError:
        raise
    except BusyError as exc:
        kind, status = "busy", BUSY
        message = str(exc)
    except RecognitionTimeout as exc:
        kind, status = "timeout", ERROR
        message = str(exc)
    except (
        AppError,
        OSError,
        ValueError,
        EOFError,
        wave.Error,
        subprocess.SubprocessError,
        termios.error,
    ) as exc:
        kind, status = "operation_failed", ERROR
        message = str(exc)

    logger.error("%s: %s", kind, message)
    if args.json:
        emit_json({"error": message, "kind": kind})
    else:
        err_console.print(Text(
            message,
            style="warning" if status == BUSY else "error",
        ))
    return status


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        try:
            fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(fd, sys.stdout.fileno())
            finally:
                os.close(fd)
        except OSError:
            pass
        raise SystemExit(ERROR)
