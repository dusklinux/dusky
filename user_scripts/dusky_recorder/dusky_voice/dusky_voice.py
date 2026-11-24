#!/usr/bin/env python3
"""
===============================================================================
DUSKY VOICE MEMOS — Wayland & PipeWire Native Audio Studio & Memo Manager
===============================================================================
Target Platform: Arch Linux | Kernel 7.3+ | Wayland & Hyprland Exclusively
Stack: PipeWire 1.6+ | GStreamer 1.28+ | Python 3.15+ | Textual 8.2+

Design System: DuskyTUI (Matugen Live Palette, FlowContainer Shortcuts,
NoticeBox Status Banners, Papirus Desktop Icons, Zero Emojis).
===============================================================================
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 15):
    sys.stderr.write("Fatal: Python 3.15+ is required for Dusky Voice.\n")
    raise SystemExit(1)

import argparse
import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
import os
from pathlib import Path
import signal
import threading
import time
from typing import Any, Callable, Final

# Python 3.15 explicit lazy imports (PEP 810) for deferred runtime modules
lazy import hashlib
lazy import json
lazy import math
lazy import re
lazy import shutil
lazy import tempfile

# GStreamer PyGObject bindings (Gst.init is deferred to main() after argparse)
import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstPbutils", "1.0")
from gi.repository import GLib, Gst, GstPbutils

# Textual 8.2+ TUI Framework & Rich
from rich.console import Group, RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.events import Click
from textual.geometry import Offset, Region, Size, Spacing
from textual.layout import Layout, WidgetPlacement
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widget import Widget
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    Select,
    Static,
    Tab,
    Tabs,
)

# =============================================================================
# CONSTANTS & XDG PATH DEFINITIONS
# =============================================================================

APP_NAME: Final[str] = "Dusky Voice Memos"
VERSION: Final[str] = "2.0.0"


def _resolve_xdg_dir(env_var: str, default_rel: str) -> Path:
    raw = os.environ.get(env_var, "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / default_rel


HOME_DIR: Final[Path] = Path.home()
XDG_CONFIG_HOME: Final[Path] = _resolve_xdg_dir("XDG_CONFIG_HOME", ".config")
XDG_DATA_HOME: Final[Path] = _resolve_xdg_dir("XDG_DATA_HOME", ".local/share")

CONFIG_DIR: Final[Path] = XDG_CONFIG_HOME / "dusky" / "settings" / "dusky_voice"
RECORDINGS_DIR: Final[Path] = CONFIG_DIR
CACHE_DIR: Final[Path] = RECORDINGS_DIR / ".waveform_cache"
DURATION_CACHE_FILE: Final[Path] = CACHE_DIR / ".durations.json"
THEME_FILE: Final[Path] = XDG_CONFIG_HOME / "matugen" / "generated" / "dusky_tui.json"

# Audio constants
SAMPLE_RATE: Final[int] = 48000
LEVEL_INTERVAL_NS: Final[int] = 50_000_000          # 50 ms = 20 Hz telemetry
MIXER_LATENCY_NS: Final[int] = 60_000_000           # 60 ms low-latency live mixing
ENCODER_TOLERANCE_NS: Final[int] = 1_000_000_000_000_000  # 10^15 ns gapless pause/resume
GST_PLAY_FLAG_AUDIO: Final[int] = 0x00000002        # Audio-only playback flag

UNICODE_BLOCKS: Final[tuple[str, ...]] = (" ", " ", "▂", "▃", "▄", "▅", "▆", "▇", "█")

# System & user Papirus icon search roots (user data dir takes precedence)
PAPIRUS_BASE_PATHS: Final[tuple[Path, ...]] = (
    XDG_DATA_HOME / "icons" / "Papirus" / "48x48",
    XDG_DATA_HOME / "icons" / "Papirus-Dark" / "48x48",
    Path("/usr/share/icons/Papirus/48x48"),
    Path("/usr/share/icons/Papirus/32x32"),
    Path("/usr/share/icons/Papirus/24x24"),
    Path("/usr/share/icons/Papirus-Dark/48x48"),
    Path("/usr/share/icons/Papirus-Dark/32x32"),
)

# Fallback Dusky Theme Palette
DEFAULT_THEME: Final[dict[str, str]] = {
    "bg": "#1a1112",
    "fg": "#f0dedf",
    "accent": "#ffb2b8",
    "error": "#ffb4ab",
    "warning": "#e6bdbf",
    "success": "#e8c08e",
    "muted": "#524344",
}

_PAPIRUS_ICON_CACHE: dict[str, str] = {}


# =============================================================================
# DATA MODELS, ENUMS & RUNTIME CAPABILITIES
# =============================================================================

class RecordSource(StrEnum):
    MIC = "mic"
    SYSTEM = "system"
    BOTH = "both"

    @property
    def label(self) -> str:
        return {
            RecordSource.MIC: "󰍬 Microphone Only",
            RecordSource.SYSTEM: "󰋋 Desktop Audio (Monitor)",
            RecordSource.BOTH: "󰮔 Both (Mic + Desktop)",
        }[self]


class AudioFormat(StrEnum):
    OPUS = "opus"
    FLAC = "flac"
    WAV = "wav"
    MP3 = "mp3"
    OGG = "ogg"

    @property
    def extension(self) -> str:
        return f".{self.value}"

    @property
    def label(self) -> str:
        return {
            AudioFormat.OPUS: "Opus (.opus) • 48 kHz",
            AudioFormat.FLAC: "FLAC (.flac) • Lossless",
            AudioFormat.WAV: "WAV (.wav) • Raw PCM",
            AudioFormat.MP3: "MP3 (.mp3) • Compatible",
            AudioFormat.OGG: "OGG (.ogg) • Vorbis",
        }[self]


class RecordState(StrEnum):
    IDLE = "IDLE"
    COUNTDOWN = "COUNTDOWN"
    RECORDING = "RECORDING"
    PAUSED = "PAUSED"
    FINALIZING = "FINALIZING"


class PlayerState(StrEnum):
    STOPPED = "STOPPED"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"


@dataclass(slots=True)
class PipeWireNode:
    id: int
    name: str
    description: str
    media_class: str
    is_default: bool = False


@dataclass(slots=True)
class VoiceMemo:
    path: Path
    name: str
    stem: str
    ext: str
    size_bytes: int
    mtime_ns: int
    duration_sec: float
    created_at: datetime
    format_name: str

    @property
    def formatted_duration(self) -> str:
        total = int(self.duration_sec)
        minutes = total // 60
        seconds = total % 60
        tenths = int((self.duration_sec - total) * 10)
        return f"{minutes:02d}:{seconds:02d}.{tenths}"

    @property
    def formatted_size(self) -> str:
        if self.size_bytes < 1024:
            return f"{self.size_bytes} B"
        if self.size_bytes < 1024 * 1024:
            return f"{self.size_bytes / 1024:.1f} KB"
        return f"{self.size_bytes / (1024 * 1024):.1f} MB"

    @property
    def formatted_date(self) -> str:
        return self.created_at.strftime("%Y-%m-%d %H:%M")


@dataclass(slots=True)
class RuntimeCapabilities:
    capture_backend: str = "pipewiresrc"
    has_pipewiresink: bool = False
    playbin_element: str = "playbin3"
    available_formats: tuple[AudioFormat, ...] = field(default_factory=tuple)

    @property
    def backend_label(self) -> str:
        if self.capture_backend == "pipewiresrc":
            return "PIPEWIRE NATIVE"
        if self.capture_backend == "pulsesrc":
            return "PIPEWIRE-PULSE"
        return "NO CAPTURE PLUGIN"


FORMAT_PLUGIN_DEPS: Final[dict[AudioFormat, tuple[str, ...]]] = {
    AudioFormat.OPUS: ("opusenc", "oggmux"),
    AudioFormat.FLAC: ("flacenc",),
    AudioFormat.WAV: ("wavenc",),
    AudioFormat.MP3: ("lamemp3enc", "id3v2mux"),
    AudioFormat.OGG: ("vorbisenc", "oggmux"),
}


def probe_runtime_capabilities() -> RuntimeCapabilities:
    """Discover installed GStreamer capture, playback, and codec plugins at runtime."""
    def _has(name: str) -> bool:
        return Gst.ElementFactory.find(name) is not None

    if _has("pipewiresrc"):
        capture_backend = "pipewiresrc"
    elif _has("pulsesrc"):
        capture_backend = "pulsesrc"
    else:
        capture_backend = ""

    playbin_element = "playbin3" if _has("playbin3") else ("playbin" if _has("playbin") else "")
    available = tuple(
        fmt
        for fmt, deps in FORMAT_PLUGIN_DEPS.items()
        if all(_has(dep) for dep in deps)
    )
    return RuntimeCapabilities(
        capture_backend=capture_backend,
        has_pipewiresink=_has("pipewiresink"),
        playbin_element=playbin_element,
        available_formats=available,
    )


# =============================================================================
# KERNEL EXIT WATCHDOG, ENVIRONMENT, THEME & WAYLAND ASYNC HELPERS
# =============================================================================

def arm_exit_watchdog(seconds: int) -> None:
    """
    Arm a kernel-level SIGALRM kill timer so C-level GStreamer/GLib/PipeWire
    or PTY deadlocks can never leave a lingering background process on exit.
    """
    try:
        signal.signal(signal.SIGALRM, signal.SIG_DFL)
        signal.alarm(max(1, int(seconds)))
    except (OSError, ValueError):
        pass


def _silence_dead_stdio() -> None:
    """Redirect stdout/stderr to /dev/null after PTY hangup so teardown flushes cleanly."""
    try:
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        return
    try:
        os.dup2(devnull_fd, 1)
        os.dup2(devnull_fd, 2)
    except OSError:
        pass
    finally:
        try:
            os.close(devnull_fd)
        except OSError:
            pass



def ensure_storage_environment() -> None:
    """Ensure recording and waveform cache directories exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_matugen_theme() -> dict[str, str]:
    """Load user's dynamic system palette generated by Matugen."""
    if THEME_FILE.is_file():
        try:
            with open(THEME_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {
                    key: (
                        value
                        if isinstance(value := data.get(key), str)
                        and re.fullmatch(r"#[0-9a-fA-F]{6}", value)
                        else default
                    )
                    for key, default in DEFAULT_THEME.items()
                }
        except (OSError, ValueError):
            pass
    return DEFAULT_THEME.copy()


def find_papirus_icon(name: str) -> str:
    """Resolve and memoize Papirus SVG icon path, falling back to icon theme name."""
    cached = _PAPIRUS_ICON_CACHE.get(name)
    if cached is not None:
        return cached
    for base in PAPIRUS_BASE_PATHS:
        for cat in ("devices", "actions", "status", "places", "apps", "categories"):
            cand = base / cat / f"{name}.svg"
            if cand.is_file():
                resolved = str(cand)
                _PAPIRUS_ICON_CACHE[name] = resolved
                return resolved
    _PAPIRUS_ICON_CACHE[name] = name
    return name


async def copy_to_wayland_clipboard_async(text: str) -> bool:
    """
    Copy UTF-8 text to the Wayland clipboard via wl-copy without blocking
    the TUI loop or binding wl-copy's selection daemon to our process group.
    """
    if not shutil.which("wl-copy"):
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "wl-copy",
            "--type",
            "text/plain",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        assert proc.stdin is not None
        proc.stdin.write(text.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        await asyncio.wait_for(proc.wait(), timeout=2.0)
        return proc.returncode == 0
    except (OSError, TimeoutError, asyncio.CancelledError):
        return False


async def send_desktop_notification_async(
    title: str,
    message: str,
    icon_name: str = "audio-input-microphone",
) -> None:
    """Send native desktop notification via notify-send and reap child cleanly."""
    if not shutil.which("notify-send"):
        return
    icon_path = find_papirus_icon(icon_name)
    try:
        proc = await asyncio.create_subprocess_exec(
            "notify-send",
            "-a",
            APP_NAME,
            "-i",
            icon_path,
            "-u",
            "normal",
            title,
            message,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except (OSError, TimeoutError, asyncio.CancelledError):
        pass


def _parse_pipewire_dump(raw_json: str) -> tuple[list[PipeWireNode], list[PipeWireNode]]:
    """Parse pw-dump JSON into PipeWire audio sources and sinks (including virtual/duplex)."""
    sources: list[PipeWireNode] = []
    sinks: list[PipeWireNode] = []
    try:
        objects = json.loads(raw_json)
    except ValueError:
        return sources, sinks
    if not isinstance(objects, list):
        return sources, sinks

    default_source_name = ""
    default_sink_name = ""

    for obj in objects:
        if not isinstance(obj, dict):
            continue
        props = obj.get("props") or {}
        if props.get("metadata.name") == "default":
            for item in obj.get("metadata") or ():
                if isinstance(item, dict) and item.get("subject") == 0:
                    key = item.get("key")
                    val = item.get("value")
                    name = val.get("name") if isinstance(val, dict) else val
                    if isinstance(name, str):
                        if key == "default.audio.source":
                            default_source_name = name
                        elif key == "default.audio.sink":
                            default_sink_name = name

    for obj in objects:
        if not isinstance(obj, dict):
            continue
        info = obj.get("info") or {}
        props = info.get("props") or obj.get("props") or {}
        media_class = props.get("media.class", "")
        node_name = props.get("node.name", "")
        if not isinstance(media_class, str) or not isinstance(node_name, str) or not node_name:
            continue
        if node_name.endswith(".monitor"):
            continue
        desc = props.get("node.description") or props.get("node.nick") or node_name
        node_id = int(obj.get("id", 0))

        is_duplex = media_class.startswith("Audio/Duplex")
        if media_class.startswith("Audio/Source") or is_duplex:
            sources.append(
                PipeWireNode(
                    id=node_id,
                    name=node_name,
                    description=str(desc),
                    media_class=media_class,
                    is_default=(node_name == default_source_name),
                )
            )
        if media_class.startswith("Audio/Sink") or is_duplex:
            sinks.append(
                PipeWireNode(
                    id=node_id,
                    name=node_name,
                    description=str(desc),
                    media_class=media_class,
                    is_default=(node_name == default_sink_name),
                )
            )

    sources.sort(key=lambda n: (not n.is_default, n.description.lower()))
    sinks.sort(key=lambda n: (not n.is_default, n.description.lower()))
    return sources, sinks


async def discover_pipewire_nodes_async() -> tuple[list[PipeWireNode], list[PipeWireNode]]:
    """Asynchronously enumerate PipeWire audio sources and sinks via pw-dump."""
    if not shutil.which("pw-dump"):
        return [], []
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "pw-dump",
            "--no-colors",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.5)
        if proc.returncode != 0 or not stdout:
            return [], []
        return _parse_pipewire_dump(stdout.decode("utf-8", errors="replace"))
    except (OSError, TimeoutError, asyncio.CancelledError):
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except OSError:
                pass
        return [], []


# =============================================================================
# FILENAME, WAVEFORM GENERATOR & CACHING ENGINE
# =============================================================================

def clean_memo_stem(value: str) -> str:
    """Sanitize memo stem while staying safely under Linux's 255-byte filename cap."""
    cleaned = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "", value).strip(" .")
    return cleaned.encode("utf-8")[:180].decode("utf-8", errors="ignore").rstrip(" .")


def build_recording_path(prefix: str, fmt: AudioFormat) -> Path:
    """Construct a clean, collision-safe recording path in RECORDINGS_DIR."""
    clean_prefix = clean_memo_stem(prefix) or "Memo"
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base = f"{clean_prefix}_{stamp}"
    candidate = RECORDINGS_DIR / f"{base}{fmt.extension}"
    counter = 2
    while candidate.exists():
        candidate = RECORDINGS_DIR / f"{base}_{counter}{fmt.extension}"
        counter += 1
    return candidate


def _waveform_cache_prefix(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()


def remove_waveform_cache(path: Path) -> None:
    """Remove cached waveform files for a memo; ignore filesystem errors."""
    key = _waveform_cache_prefix(path)
    try:
        for cache in CACHE_DIR.glob(f"{key}_*.json"):
            cache.unlink(missing_ok=True)
    except OSError:
        pass


def prune_orphaned_waveform_caches(valid_paths: set[Path]) -> None:
    """Inline cleanup of waveform JSON caches whose source memo no longer exists."""
    valid_prefixes = {_waveform_cache_prefix(p) for p in valid_paths}
    try:
        for entry in CACHE_DIR.iterdir():
            if not entry.is_file() or entry.name.startswith(".") or entry.suffix != ".json":
                continue
            prefix = entry.name.split("_", 1)[0]
            if prefix not in valid_prefixes:
                entry.unlink(missing_ok=True)
    except OSError:
        pass


def compute_waveform_peaks(
    file_path: Path,
    duration_sec: float = 0.0,
    num_points: int = 120,
    cancelled: threading.Event | None = None,
) -> list[float]:
    """
    Extract or load cached audio amplitude peaks for a recording.
    Scales GStreamer level interval dynamically to keep long-file decoding fast.
    """
    if num_points <= 0 or (cancelled and cancelled.is_set()):
        return []
    try:
        stat = file_path.stat()
    except OSError:
        return []

    cache_key = _waveform_cache_prefix(file_path)
    cache_file = CACHE_DIR / f"{cache_key}_{stat.st_size}_{stat.st_mtime_ns}_{num_points}.json"
    if cache_file.is_file():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if (
                isinstance(data, list)
                and len(data) == num_points
                and all(isinstance(x, (int, float)) and math.isfinite(x) and 0.0 <= x <= 1.0 for x in data)
            ):
                return [float(x) for x in data]
        except (OSError, ValueError):
            pass

    # Target ~320 peak buckets across any duration (floored at 50 ms) to minimize bus traffic
    if duration_sec > 0.0:
        interval_ns = max(LEVEL_INTERVAL_NS, int((duration_sec * Gst.SECOND) / 320))
    else:
        interval_ns = LEVEL_INTERVAL_NS

    pipe_str = (
        "uridecodebin name=decoder ! "
        "audioconvert ! audio/x-raw,channels=1 ! "
        f"level interval={interval_ns} ! fakesink sync=false qos=false"
    )
    try:
        pipeline = Gst.parse_launch(pipe_str)
    except GLib.Error:
        return []

    raw_peaks: list[float] = []
    complete = False
    bus = pipeline.get_bus()
    try:
        decoder = pipeline.get_by_name("decoder")
        if decoder is None:
            return []
        decoder.set_property("uri", file_path.resolve().as_uri())
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            return []

        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline and not (cancelled and cancelled.is_set()):
            msg = bus.timed_pop_filtered(
                50 * Gst.MSECOND,
                Gst.MessageType.ELEMENT | Gst.MessageType.EOS | Gst.MessageType.ERROR,
            )
            if msg is None:
                continue
            if msg.type == Gst.MessageType.ELEMENT:
                s = msg.get_structure()
                if s and s.has_name("level"):
                    peaks = s.get_value("peak")
                    if peaks:
                        db = float(peaks[0])
                        frac = (
                            max(0.0, min(1.0, (db + 60.0) / 60.0))
                            if math.isfinite(db) and db > -80.0
                            else 0.0
                        )
                        raw_peaks.append(frac)
            elif msg.type == Gst.MessageType.EOS:
                complete = True
                break
            elif msg.type == Gst.MessageType.ERROR:
                break
    finally:
        if bus is not None:
            bus.set_flushing(True)
        pipeline.set_state(Gst.State.NULL)
        pipeline.get_state(250 * Gst.MSECOND)

    if not complete or not raw_peaks or (cancelled and cancelled.is_set()):
        return []

    if len(raw_peaks) == num_points:
        resampled = raw_peaks
    else:
        total = len(raw_peaks)
        resampled = [
            max(raw_peaks[i * total // num_points : max(i * total // num_points + 1, (i + 1) * total // num_points)])
            for i in range(num_points)
        ]

    temporary: Path | None = None
    try:
        current = file_path.stat()
        if (current.st_size, current.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
            return []
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=CACHE_DIR, delete=False) as cache:
            temporary = Path(cache.name)
            json.dump(resampled, cache)
        temporary.replace(cache_file)
        temporary = None
    except OSError:
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    return resampled


# =============================================================================
# GSTREAMER BUS & PIPELINE CLEANUP HELPER
# =============================================================================

def _teardown_pipeline(pipeline: Gst.Element | None, wait_ms: int = 400) -> None:
    """Flush bus and transition a GStreamer pipeline to NULL with a bounded wait."""
    if pipeline is None:
        return
    try:
        bus = pipeline.get_bus()
        if bus is not None:
            bus.set_flushing(True)
        pipeline.set_state(Gst.State.NULL)
        pipeline.get_state(wait_ms * Gst.MSECOND)
    except Exception:
        pass


# =============================================================================
# DUSKY AUDIO RECORDING ENGINE (NATIVE PIPEWIRE 1.6+ WITH PULSE FALLBACK)
# =============================================================================

class DuskyAudioEngine:
    """High-performance GStreamer PipeWire recording engine with gapless pause/resume."""

    def __init__(self, caps: RuntimeCapabilities) -> None:
        self.caps = caps
        self._lock = threading.Lock()
        self.state: RecordState = RecordState.IDLE
        self.pipeline: Gst.Pipeline | None = None
        self._valve: Gst.Element | None = None
        self._finalize_thread: threading.Thread | None = None

        self.output_path: Path | None = None
        self.start_time: float = 0.0
        self.pause_start: float = 0.0
        self.paused_duration: float = 0.0
        self._frozen_elapsed: float = 0.0
        self.max_duration: float = 0.0
        self.stopped_by_timer: bool = False

        # Telemetry & lifecycle callbacks
        self.on_level_callback: Callable[[list[float]], None] | None = None
        self.on_state_change: Callable[[RecordState], None] | None = None
        self.on_finished: Callable[[Path, bool], None] | None = None
        self.on_error: Callable[[str], None] | None = None

    @property
    def active_output_path(self) -> Path | None:
        """Return output path while a recording pipeline owns or is finalizing it."""
        with self._lock:
            if self.state != RecordState.IDLE or self.pipeline is not None:
                return self.output_path
            return None

    @property
    def elapsed_seconds(self) -> float:
        state = self.state
        if state in (RecordState.IDLE, RecordState.COUNTDOWN):
            return 0.0
        if state == RecordState.FINALIZING:
            return self._frozen_elapsed
        if state == RecordState.PAUSED:
            return max(0.0, self.pause_start - self.start_time - self.paused_duration)
        return max(0.0, time.monotonic() - self.start_time - self.paused_duration)

    def _build_pipeline_string(
        self,
        source: RecordSource,
        format_: AudioFormat,
        channels: int,
        sample_rate: int,
    ) -> str:
        encoder_str = {
            AudioFormat.OPUS: "opusenc name=enc bitrate=128000 ! oggmux",
            AudioFormat.FLAC: "flacenc name=enc",
            AudioFormat.WAV: "wavenc name=enc",
            AudioFormat.MP3: "lamemp3enc name=enc target=quality quality=2 ! id3v2mux",
            AudioFormat.OGG: "vorbisenc name=enc quality=0.6 ! oggmux",
        }[format_]

        if format_ == AudioFormat.OPUS:
            sample_rate = 48000  # Opus spec requires 48 kHz (or sub-multiples), never 44.1 kHz

        src_elem = self.caps.capture_backend
        tail = (
            f"audioconvert ! audioresample ! "
            f"audio/x-raw,channels={channels},rate={sample_rate} ! "
            f"valve name=pause_valve drop=false ! "
            f"level interval={LEVEL_INTERVAL_NS} name=lvl ! "
            f"{encoder_str} ! filesink name=output"
        )

        if source == RecordSource.MIC:
            return f"{src_elem} name=mic ! audio/x-raw ! {tail}"
        if source == RecordSource.SYSTEM:
            return f"{src_elem} name=desktop ! audio/x-raw ! {tail}"

        # Simultaneous dual capture via low-latency live audiomixer with leaky branch queues
        branch_q = (
            "audio/x-raw ! "
            "queue max-size-buffers=0 max-size-bytes=0 max-size-time=200000000 leaky=downstream ! "
            "audioconvert ! audioresample"
        )
        return (
            f"audiomixer name=mix latency={MIXER_LATENCY_NS} ! {tail} "
            f"{src_elem} name=mic ! {branch_q} ! mix.sink_0 "
            f"{src_elem} name=desktop ! {branch_q} ! mix.sink_1"
        )

    @staticmethod
    def _configure_pipewire_source(
        element: Gst.Element,
        *,
        is_desktop_monitor: bool,
        target_node: str,
    ) -> None:
        props = Gst.Structure.new_empty("props")
        props.set_value("media.type", "Audio")
        props.set_value("media.category", "Capture")
        props.set_value("media.role", "Production")
        if is_desktop_monitor:
            props.set_value("stream.capture.sink", "true")
        if element.find_property("stream-properties") is not None:
            element.set_property("stream-properties", props)
        if target_node and element.find_property("target-object") is not None:
            element.set_property("target-object", target_node)
        if element.find_property("do-timestamp") is not None:
            element.set_property("do-timestamp", True)

    def prepare(
        self,
        output_path: Path,
        source: RecordSource,
        format_: AudioFormat,
        channels: int = 2,
        max_duration: float = 0.0,
        title: str = "",
        custom_source_node: str = "",
        custom_sink_node: str = "",
        sample_rate: int = SAMPLE_RATE,
    ) -> bool:
        """Build and configure the capture pipeline without starting it."""
        with self._lock:
            if self.state != RecordState.IDLE or self.pipeline is not None:
                return False
            if not self.caps.capture_backend:
                err = "No GStreamer PipeWire capture plugin (pipewiresrc or pulsesrc) found."
            elif format_ not in self.caps.available_formats:
                err = f"Encoder plugins for {format_.value.upper()} are not installed."
            else:
                err = ""

        if err:
            if self.on_error:
                self.on_error(err)
            return False

        pipe_str = self._build_pipeline_string(source, format_, channels, sample_rate)
        pipeline: Gst.Pipeline | None = None
        try:
            parsed = Gst.parse_launch(pipe_str)
            if not isinstance(parsed, Gst.Pipeline):
                raise RuntimeError("GStreamer did not construct a Pipeline.")
            pipeline = parsed

            output = pipeline.get_by_name("output")
            if output is None:
                raise RuntimeError("Missing filesink element in capture pipeline.")
            output.set_property("location", str(output_path.resolve()))

            mic = pipeline.get_by_name("mic")
            desktop = pipeline.get_by_name("desktop")
            if self.caps.capture_backend == "pipewiresrc":
                if mic is not None:
                    self._configure_pipewire_source(
                        mic, is_desktop_monitor=False, target_node=custom_source_node
                    )
                if desktop is not None:
                    self._configure_pipewire_source(
                        desktop, is_desktop_monitor=True, target_node=custom_sink_node
                    )
            else:
                if mic is not None:
                    mic.set_property("device", custom_source_node or "@DEFAULT_SOURCE@")
                if desktop is not None:
                    desktop.set_property(
                        "device",
                        f"{custom_sink_node}.monitor" if custom_sink_node else "@DEFAULT_MONITOR@",
                    )


            enc = pipeline.get_by_name("enc")
            if enc is not None and enc.find_property("tolerance") is not None:
                enc.set_property("tolerance", ENCODER_TOLERANCE_NS)

            valve = pipeline.get_by_name("pause_valve")

            tag_list = Gst.TagList.new_empty()
            tag_list.add_value(Gst.TagMergeMode.REPLACE, Gst.TAG_APPLICATION_NAME, APP_NAME)
            tag_list.add_value(Gst.TagMergeMode.REPLACE, Gst.TAG_ARTIST, "Dusky Voice")
            tag_list.add_value(Gst.TagMergeMode.REPLACE, Gst.TAG_TITLE, title or output_path.stem)
            recorded_at = Gst.DateTime.new_now_local_time()
            tag_list.add_value(Gst.TagMergeMode.REPLACE, Gst.TAG_DATE_TIME, recorded_at)
            tag_list.add_value(
                Gst.TagMergeMode.REPLACE,
                Gst.TAG_DATE,
                GLib.Date.new_dmy(
                    recorded_at.get_day(),
                    GLib.DateMonth(recorded_at.get_month()),
                    recorded_at.get_year(),
                ),
            )
            elements = pipeline.iterate_elements()
            while True:
                res, elem = elements.next()
                if res != Gst.IteratorResult.OK:
                    break
                if isinstance(elem, Gst.TagSetter):
                    Gst.TagSetter.merge_tags(elem, tag_list, Gst.TagMergeMode.REPLACE)
        except (GLib.Error, RuntimeError, TypeError) as exc:
            _teardown_pipeline(pipeline)
            if self.on_error:
                self.on_error(f"Pipeline initialization failed: {exc}")
            return False

        with self._lock:
            self.pipeline = pipeline
            self._valve = valve
            self.output_path = output_path
            self.max_duration = max_duration
            self.paused_duration = 0.0
            self._frozen_elapsed = 0.0
            self.stopped_by_timer = False
        return True

    def start(self) -> bool:
        """Transition prepared/countdown pipeline into active recording."""
        with self._lock:
            if self.pipeline is None or self.state not in (RecordState.IDLE, RecordState.COUNTDOWN):
                return False
            pipeline = self.pipeline

        ret = pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self.cancel()
            if self.on_error:
                self.on_error("Failed to activate PipeWire recording pipeline.")
            return False

        with self._lock:
            self.start_time = time.monotonic()
            self.state = RecordState.RECORDING
        if self.on_state_change:
            self.on_state_change(RecordState.RECORDING)
        return True

    def pause(self) -> bool:
        """Pause capture cleanly by dropping buffers at pause_valve while PipeWire clocks stay live."""
        with self._lock:
            if self.state != RecordState.RECORDING or self.pipeline is None or self._valve is None:
                return False
            self._valve.set_property("drop", True)
            self.pause_start = time.monotonic()
            self.state = RecordState.PAUSED

        if self.on_level_callback:
            self.on_level_callback([-80.0, -80.0])
        if self.on_state_change:
            self.on_state_change(RecordState.PAUSED)
        return True

    def resume(self) -> bool:
        """Resume capture seamlessly; GstAudioEncoder tolerance keeps output timestamps contiguous."""
        with self._lock:
            if self.state != RecordState.PAUSED or self.pipeline is None or self._valve is None:
                return False
            self.paused_duration += time.monotonic() - self.pause_start
            self._valve.set_property("drop", False)
            self.state = RecordState.RECORDING

        if self.on_state_change:
            self.on_state_change(RecordState.RECORDING)
        return True

    def stop(
        self,
        *,
        stopped_by_timer: bool = False,
        blocking: bool = False,
        eos_timeout_sec: float = 4.0,
    ) -> bool:
        """
        Finalize recording with clean EOS flushing.
        When blocking=False, flushes on a daemon thread so the TUI paints FINALIZING immediately.
        """
        with self._lock:
            if self.state not in (RecordState.RECORDING, RecordState.PAUSED):
                return False
            if self.state == RecordState.PAUSED:
                self._frozen_elapsed = max(0.0, self.pause_start - self.start_time - self.paused_duration)
                self.paused_duration += time.monotonic() - self.pause_start
            else:
                self._frozen_elapsed = max(0.0, time.monotonic() - self.start_time - self.paused_duration)

            self.stopped_by_timer = stopped_by_timer
            self.state = RecordState.FINALIZING
            pipeline = self.pipeline
            valve = self._valve
            output_path = self.output_path
            # Detach pipeline from poll() so finalizer owns bus exclusively
            self.pipeline = None
            self._valve = None

        if self.on_state_change:
            self.on_state_change(RecordState.FINALIZING)

        if blocking:
            return self._finalize_worker(pipeline, valve, output_path, stopped_by_timer, eos_timeout_sec)

        worker = threading.Thread(
            target=self._finalize_worker,
            args=(pipeline, valve, output_path, stopped_by_timer, eos_timeout_sec),
            name="dusky-finalize",
            daemon=True,
        )
        with self._lock:
            self._finalize_thread = worker
        worker.start()
        return True

    def _finalize_worker(
        self,
        pipeline: Gst.Pipeline | None,
        valve: Gst.Element | None,
        output_path: Path | None,
        stopped_by_timer: bool,
        eos_timeout_sec: float,
    ) -> bool:
        finalized = False
        if pipeline is not None:
            try:
                # Close valve so zero stray audio leaks while flushing; push EOS on valve's src pad
                # so stop-while-paused always delivers EOS to encoder/muxer/filesink.
                if valve is not None:
                    valve.set_property("drop", True)
                    src_pad = valve.get_static_pad("src")
                else:
                    src_pad = None

                if src_pad is None or not src_pad.push_event(Gst.Event.new_eos()):
                    pipeline.send_event(Gst.Event.new_eos())

                bus = pipeline.get_bus()
                if bus is not None:
                    deadline = time.monotonic() + eos_timeout_sec
                    while time.monotonic() < deadline:
                        rem_ns = max(1, int((deadline - time.monotonic()) * Gst.SECOND))
                        msg = bus.timed_pop_filtered(
                            min(rem_ns, 200 * Gst.MSECOND),
                            Gst.MessageType.EOS | Gst.MessageType.ERROR,
                        )
                        if msg is None:
                            continue
                        finalized = msg.type == Gst.MessageType.EOS
                        break
            finally:
                _teardown_pipeline(pipeline, wait_ms=500)

        with self._lock:
            self.state = RecordState.IDLE
            if self._finalize_thread is threading.current_thread():
                self._finalize_thread = None

        if self.on_state_change:
            self.on_state_change(RecordState.IDLE)

        if not finalized:
            if output_path is not None:
                try:
                    if output_path.is_file() and output_path.stat().st_size == 0:
                        output_path.unlink(missing_ok=True)
                except OSError:
                    pass
            if self.on_error:
                self.on_error("Finalization timed out; any non-empty partial recording was retained.")
            return False

        if output_path is not None and output_path.is_file():
            try:
                size = output_path.stat().st_size
            except OSError:
                size = 0
            if size == 0:
                try:
                    output_path.unlink(missing_ok=True)
                except OSError:
                    pass
                if self.on_error:
                    self.on_error("Recording was too short (0 bytes) and was discarded.")
                return False
            if self.on_finished:
                self.on_finished(output_path, stopped_by_timer)
            return True
        return False

    def cancel(self) -> bool:
        """Abort capture or countdown immediately and remove the partial file."""
        with self._lock:
            if self.state == RecordState.IDLE and self.pipeline is None:
                return False
            pipeline = self.pipeline
            output_path = self.output_path
            self.pipeline = None
            self._valve = None
            self.stopped_by_timer = False
            self.state = RecordState.IDLE

        _teardown_pipeline(pipeline, wait_ms=400)

        unlink_error: OSError | None = None
        if output_path is not None:
            try:
                output_path.unlink(missing_ok=True)
            except OSError as exc:
                unlink_error = exc

        if self.on_state_change:
            self.on_state_change(RecordState.IDLE)
        if unlink_error is not None:
            if self.on_error:
                self.on_error(f"Capture stopped, but could not remove {output_path.name}: {unlink_error}")
            return False
        return True

    def shutdown(self, eos_timeout_sec: float = 1.5) -> None:
        """Bounded synchronous teardown for application exit."""
        with self._lock:
            state = self.state
            finalize_thread = self._finalize_thread

        if state in (RecordState.RECORDING, RecordState.PAUSED):
            self.stop(stopped_by_timer=False, blocking=True, eos_timeout_sec=eos_timeout_sec)
        elif state == RecordState.FINALIZING:
            if finalize_thread is not None and finalize_thread.is_alive():
                finalize_thread.join(timeout=eos_timeout_sec)
        else:
            self.cancel()

    def poll(self) -> None:
        """Drain the entire GstBus on the TUI thread to prevent C message queue leaks."""
        with self._lock:
            pipeline = self.pipeline
            state = self.state
        if pipeline is None or state == RecordState.FINALIZING:
            return
        bus = pipeline.get_bus()
        if bus is None:
            return

        latest_peaks: list[float] | None = None
        while (msg := bus.pop()) is not None:
            mtype = msg.type
            if mtype == Gst.MessageType.ELEMENT:
                structure = msg.get_structure()
                if structure and structure.has_name("level"):
                    raw_peaks = structure.get_value("peak")
                    if raw_peaks:
                        latest_peaks = [float(x) for x in raw_peaks]
            elif mtype in (Gst.MessageType.ERROR, Gst.MessageType.EOS):
                err_text = (
                    msg.parse_error()[0].message
                    if mtype == Gst.MessageType.ERROR
                    else "PipeWire audio source disconnected unexpectedly."
                )
                with self._lock:
                    if self.pipeline is not pipeline:
                        break
                    self.pipeline = None
                    self._valve = None
                    self.state = RecordState.IDLE
                _teardown_pipeline(pipeline, wait_ms=300)
                if self.on_state_change:
                    self.on_state_change(RecordState.IDLE)
                if self.on_error:
                    self.on_error(f"{err_text} Non-empty partial recording retained.")
                return

        if latest_peaks is not None and self.on_level_callback and self.state == RecordState.RECORDING:
            self.on_level_callback(latest_peaks)


# =============================================================================
# DUSKY AUDIO PLAYER ENGINE (PLAYBIN3 NATIVE WITH NON-BLOCKING SEEK)
# =============================================================================

class DuskyAudioPlayer:
    """Interactive playback engine with lazy playbin3 init, audio-only flags, and deadlock-free scrubbing."""

    def __init__(self, caps: RuntimeCapabilities) -> None:
        self.caps = caps
        self.player: Gst.Element | None = None
        self.state: PlayerState = PlayerState.STOPPED
        self.current_path: Path | None = None
        self.duration_sec: float = 0.0

        self._is_prerolled: bool = False
        self._pending_seek_sec: float | None = None
        self._seek_hold_target: float = 0.0
        self._seek_hold_until: float = 0.0

        self.on_state_change: Callable[[PlayerState], None] | None = None
        self.on_eos: Callable[[], None] | None = None
        self.on_error: Callable[[str], None] | None = None
        self.on_position_changed: Callable[[float], None] | None = None

    def _ensure_player(self) -> Gst.Element | None:
        if self.player is not None:
            return self.player
        if not self.caps.playbin_element:
            return None
        elem = Gst.ElementFactory.make(self.caps.playbin_element, "dusky_audio_player")
        if elem is None:
            return None
        if elem.find_property("flags") is not None:
            elem.set_property("flags", GST_PLAY_FLAG_AUDIO)
        if self.caps.has_pipewiresink and elem.find_property("audio-sink") is not None:
            sink = Gst.ElementFactory.make("pipewiresink", "dusky_pw_sink")
            if sink is not None:
                elem.set_property("audio-sink", sink)
        self.player = elem
        return elem

    def _set_muted(self, muted: bool) -> None:
        if self.player is not None and self.player.find_property("mute") is not None:
            self.player.set_property("mute", muted)

    def load(self, file_path: Path, duration: float = 0.0) -> bool:
        """Bind a memo URI to the playback engine without blocking on media discovery."""
        self.stop()
        self.current_path = file_path
        self.duration_sec = max(0.0, float(duration))
        player = self._ensure_player()
        if player is None:
            return False
        player.set_property("uri", file_path.resolve().as_uri())
        return True

    def play(self) -> bool:
        """Start or resume playback, applying any deferred scrub seek once PLAYING."""
        player = self._ensure_player()
        if player is None or self.current_path is None:
            return False

        has_pending = self._pending_seek_sec is not None
        if has_pending:
            self._set_muted(True)

        ret = player.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self._set_muted(False)
            return False

        self.state = PlayerState.PLAYING
        if ret == Gst.StateChangeReturn.SUCCESS:
            self._is_prerolled = True
            if has_pending:
                self._flush_pending_seek()
        else:
            self._is_prerolled = False

        if self.on_state_change:
            self.on_state_change(self.state)
        return True

    def pause(self) -> bool:
        """Pause active playback."""
        if self.player is None or self.state != PlayerState.PLAYING:
            return False
        if self.player.set_state(Gst.State.PAUSED) == Gst.StateChangeReturn.FAILURE:
            return False
        self.state = PlayerState.PAUSED
        if self.on_state_change:
            self.on_state_change(self.state)
        return True

    def stop(self) -> None:
        """Stop playback, flush bus, clear deferred seeks, and rewind."""
        was_active = self.state != PlayerState.STOPPED
        self._is_prerolled = False
        self._pending_seek_sec = None
        self._seek_hold_until = 0.0
        self._set_muted(False)
        if self.player is not None:
            bus = self.player.get_bus()
            if bus is not None:
                bus.set_flushing(True)
            self.player.set_state(Gst.State.NULL)
            self.player.get_state(250 * Gst.MSECOND)
            if bus is not None:
                bus.set_flushing(False)
        self.state = PlayerState.STOPPED
        if was_active and self.on_state_change:
            self.on_state_change(self.state)

    def shutdown(self) -> None:
        """Release playbin completely on application exit."""
        self._is_prerolled = False
        self._pending_seek_sec = None
        self._seek_hold_until = 0.0
        _teardown_pipeline(self.player, wait_ms=300)
        self.player = None
        self.state = PlayerState.STOPPED

    def seek_relative(self, delta_seconds: float) -> float:
        """Seek relative to current playhead and return clamped target position."""
        target = max(0.0, min(self.duration_sec, self.get_position() + delta_seconds))
        self.seek_to(target)
        return target

    def seek_to(self, seconds: float, *, auto_play_if_stopped: bool = False) -> float:
        """
        Seek to an absolute timestamp.
        Executes immediately when PLAYING; defers cleanly while STOPPED or PAUSED
        to avoid GStreamer playbin3/oggdemux PAUSED-preroll deadlocks.
        """
        if self.current_path is None or self.duration_sec <= 0.0:
            return 0.0
        target = max(0.0, min(self.duration_sec, seconds))
        self._seek_hold_target = target
        self._seek_hold_until = time.monotonic() + 0.25

        if self.state == PlayerState.PLAYING and self._is_prerolled:
            self._pending_seek_sec = None
            self._apply_seek_now(target)
        else:
            self._pending_seek_sec = target
            if self.state == PlayerState.STOPPED and auto_play_if_stopped:
                self.play()

        if self.on_position_changed:
            self.on_position_changed(target)
        return target

    def _flush_pending_seek(self) -> None:
        if self._pending_seek_sec is None:
            return
        pending = self._pending_seek_sec
        self._pending_seek_sec = None
        self._seek_hold_target = pending
        self._seek_hold_until = time.monotonic() + 0.25
        try:
            self._apply_seek_now(pending)
        finally:
            self._set_muted(False)

    def _apply_seek_now(self, seconds: float) -> None:
        if self.player is None:
            return
        target_ns = int(max(0.0, min(self.duration_sec, seconds)) * Gst.SECOND)
        self.player.seek_simple(
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
            target_ns,
        )

    def get_position(self) -> float:
        """Return current or parked playback position in seconds."""
        if self._pending_seek_sec is not None:
            return self._pending_seek_sec
        if self.player is None or self.state == PlayerState.STOPPED:
            return 0.0
        if time.monotonic() < self._seek_hold_until:
            return self._seek_hold_target
        ok, pos_ns = self.player.query_position(Gst.Format.TIME)
        if not ok or pos_ns < 0:
            return 0.0
        return min(self.duration_sec, pos_ns / Gst.SECOND) if self.duration_sec > 0 else pos_ns / Gst.SECOND

    def poll(self) -> None:
        """Drain the entire playback bus so no GStreamer messages accumulate."""
        if self.player is None or self.state == PlayerState.STOPPED:
            return
        bus = self.player.get_bus()
        if bus is None:
            return

        while (msg := bus.pop()) is not None:
            mtype = msg.type
            if mtype == Gst.MessageType.STATE_CHANGED and msg.src == self.player:
                _, new_state, pending_state = msg.parse_state_changed()
                if new_state == Gst.State.PLAYING and pending_state == Gst.State.VOID_PENDING:
                    self._is_prerolled = True
                    self._flush_pending_seek()
            elif mtype == Gst.MessageType.ASYNC_DONE:
                _, cur_state, _ = self.player.get_state(0)
                if cur_state == Gst.State.PLAYING:
                    self._is_prerolled = True
                    self._flush_pending_seek()
                if self.duration_sec <= 0.0:
                    ok, dur_ns = self.player.query_duration(Gst.Format.TIME)
                    if ok and dur_ns > 0:
                        self.duration_sec = dur_ns / Gst.SECOND
            elif mtype == Gst.MessageType.DURATION_CHANGED:
                ok, dur_ns = self.player.query_duration(Gst.Format.TIME)
                if ok and dur_ns > 0:
                    self.duration_sec = dur_ns / Gst.SECOND
            elif mtype == Gst.MessageType.ERROR:
                err_msg = msg.parse_error()[0].message
                self.stop()
                if self.on_error:
                    self.on_error(err_msg)
                return
            elif mtype == Gst.MessageType.EOS:
                self.stop()
                if self.on_eos:
                    self.on_eos()
                return


# =============================================================================
# VOICE MEMO LIBRARY REPOSITORY & METADATA CACHE
# =============================================================================

class VoiceLibrary:
    """Manages voice memos and persistent duration metadata in RECORDINGS_DIR."""

    SUPPORTED_EXTS: Final[frozenset[str]] = frozenset({".opus", ".flac", ".wav", ".mp3", ".ogg", ".m4a"})
    _cache_lock = threading.Lock()
    _durations: dict[str, tuple[int, int, float]] = {}
    _cache_loaded: bool = False

    @classmethod
    def _load_duration_cache_locked(cls) -> None:
        if cls._cache_loaded:
            return
        cls._cache_loaded = True
        if not DURATION_CACHE_FILE.is_file():
            return
        try:
            with open(DURATION_CACHE_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                parsed: dict[str, tuple[int, int, float]] = {}
                for name, entry in raw.items():
                    if (
                        isinstance(name, str)
                        and isinstance(entry, list)
                        and len(entry) == 3
                        and isinstance(entry[0], int)
                        and isinstance(entry[1], int)
                        and isinstance(entry[2], (int, float))
                    ):
                        parsed[name] = (entry[0], entry[1], float(entry[2]))
                cls._durations = parsed
        except (OSError, ValueError):
            cls._durations = {}

    @classmethod
    def _save_duration_cache_locked(cls) -> None:
        temporary: Path | None = None
        try:
            ensure_storage_environment()
            payload = {k: [v[0], v[1], v[2]] for k, v in cls._durations.items()}
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=CACHE_DIR, delete=False) as tmp:
                temporary = Path(tmp.name)
                json.dump(payload, tmp)
            temporary.replace(DURATION_CACHE_FILE)
            temporary = None
        except OSError:
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    @classmethod
    def scan_memos(
        cls,
        exclude: Path | None = None,
        cancelled: threading.Event | None = None,
    ) -> list[VoiceMemo]:
        """Scan RECORDINGS_DIR for audio memos using persistent duration caching."""
        ensure_storage_environment()
        exclude_resolved = exclude.resolve() if exclude is not None else None

        with cls._cache_lock:
            cls._load_duration_cache_locked()
            previous = dict(cls._durations)

        try:
            entries = list(RECORDINGS_DIR.iterdir())
        except OSError:
            return []

        memos: list[VoiceMemo] = []
        updated_cache: dict[str, tuple[int, int, float]] = {}
        valid_paths: set[Path] = set()
        discoverer: GstPbutils.Discoverer | None = None
        cache_dirty = False

        for entry in entries:
            if cancelled and cancelled.is_set():
                return []
            if entry.name.startswith(".") or not entry.is_file():
                continue
            ext = entry.suffix.lower()
            if ext not in cls.SUPPORTED_EXTS:
                continue
            if exclude_resolved is not None and entry.resolve() == exclude_resolved:
                continue

            try:
                stat = entry.stat()
            except OSError:
                continue
            if stat.st_size == 0:
                continue

            valid_paths.add(entry)
            cached = previous.get(entry.name)
            if cached is not None and cached[0] == stat.st_size and cached[1] == stat.st_mtime_ns:
                duration = cached[2]
                updated_cache[entry.name] = cached
            else:
                duration = 0.0
                try:
                    if discoverer is None:
                        discoverer = GstPbutils.Discoverer.new(Gst.SECOND)
                    info = discoverer.discover_uri(entry.resolve().as_uri())
                    dur_ns = info.get_duration()
                    if dur_ns != Gst.CLOCK_TIME_NONE:
                        duration = dur_ns / Gst.SECOND
                        if info.get_result() == GstPbutils.DiscovererResult.OK:
                            updated_cache[entry.name] = (stat.st_size, stat.st_mtime_ns, duration)
                            cache_dirty = True
                except GLib.Error:
                    pass

            memos.append(
                VoiceMemo(
                    path=entry,
                    name=entry.name,
                    stem=entry.stem,
                    ext=ext,
                    size_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    duration_sec=duration,
                    created_at=datetime.fromtimestamp(stat.st_mtime),
                    format_name=ext.lstrip(".").upper(),
                )
            )

        if cancelled and cancelled.is_set():
            return []

        if exclude_resolved is None:
            # Prune removed entries and orphaned waveform cache files when full scan completes
            if cache_dirty or set(updated_cache.keys()) != set(previous.keys()):
                with cls._cache_lock:
                    cls._durations = updated_cache
                    cls._save_duration_cache_locked()
            prune_orphaned_waveform_caches(valid_paths)
        elif cache_dirty:
            with cls._cache_lock:
                cls._durations.update(updated_cache)
                cls._save_duration_cache_locked()

        memos.sort(key=lambda m: m.mtime_ns, reverse=True)
        return memos

    @classmethod
    def rename_memo(cls, memo: VoiceMemo, new_stem: str) -> VoiceMemo | None:
        """Rename a memo, migrate its duration index entry, and clear stale waveform cache."""
        clean_stem = clean_memo_stem(new_stem)
        if not clean_stem or clean_stem == memo.stem:
            return None

        new_path = memo.path.with_name(f"{clean_stem}{memo.ext}")
        if new_path.exists():
            return None

        try:
            memo.path.rename(new_path)
            remove_waveform_cache(memo.path)
            stat = new_path.stat()
        except OSError:
            return None

        with cls._cache_lock:
            cls._load_duration_cache_locked()
            cls._durations.pop(memo.name, None)
            cls._durations[new_path.name] = (stat.st_size, stat.st_mtime_ns, memo.duration_sec)
            cls._save_duration_cache_locked()

        return VoiceMemo(
            path=new_path,
            name=new_path.name,
            stem=new_path.stem,
            ext=memo.ext,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            duration_sec=memo.duration_sec,
            created_at=datetime.fromtimestamp(stat.st_mtime),
            format_name=memo.format_name,
        )

    @classmethod
    def delete_memo(cls, memo: VoiceMemo) -> bool:
        """Delete a memo and clean up its duration and waveform caches."""
        try:
            memo.path.unlink(missing_ok=True)
            remove_waveform_cache(memo.path)
        except OSError:
            return False

        with cls._cache_lock:
            cls._load_duration_cache_locked()
            if memo.name in cls._durations:
                cls._durations.pop(memo.name, None)
                cls._save_duration_cache_locked()
        return True


# =============================================================================
# DUSKYTUI DESIGN SYSTEM WIDGETS & MODALS
# =============================================================================

class ShortcutFlowLayout(Layout):
    """Arrange shortcut items in a responsive wrapping flow row."""
    name = "shortcut_flow"

    def arrange(
        self,
        parent: Widget,
        children: list[Widget],
        size: Size,
        greedy: bool = True,
    ) -> list[WidgetPlacement]:
        placements: list[WidgetPlacement] = []
        x = y = 0
        for child in children:
            width = min(
                size.width,
                child.get_content_width(size, parent.screen.size) + child.styles.gutter.width,
            )
            if x and x + width > size.width:
                x = 0
                y += 1
            placements.append(WidgetPlacement(Region(x, y, width, 1), Offset(), Spacing(), child))
            x += width
        return placements


class FlowContainer(Widget):
    """Flow layout container for responsive footer shortcut pills."""
    DEFAULT_CSS = "FlowContainer { height: auto; width: 100%; }"

    @property
    def layout(self) -> ShortcutFlowLayout:
        return self._flow_layout

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._flow_layout = ShortcutFlowLayout()
        super().__init__(*args, **kwargs)


class Shortcut(Label):
    """Clickable DuskyTUI shortcut pill with accurate flow sizing and blink feedback."""

    def __init__(self, key_text: str, label: str, action_name: str | None = None, **kwargs: Any) -> None:
        # Pass plain text to Label so ShortcutFlowLayout measures exact pill width
        super().__init__(f"[{key_text}] {label}", classes="footer-shortcut", **kwargs)
        self.key_text = key_text
        self.label_text = label
        self.action_name = action_name

    def render(self) -> Text:
        txt = Text()
        tc = getattr(self.app, "theme_colors", DEFAULT_THEME)
        if self.has_class("-active"):
            contrast = tc["bg"]
            txt.append(f"[{self.key_text}] ", style=f"bold {contrast}")
            txt.append(self.label_text, style=f"bold {contrast}")
        else:
            txt.append(f"[{self.key_text}] ", style=tc["accent"])
            txt.append(self.label_text, style=tc["fg"])
        return txt

    async def on_click(self) -> None:
        self.blink()
        if self.action_name:
            await self.app.run_action(self.action_name)

    def blink(self) -> None:
        self.add_class("-active")
        self.refresh()
        self.set_timer(0.18, self._unblink)

    def _unblink(self) -> None:
        self.remove_class("-active")
        self.refresh()


class FooterPathLink(Label):
    """Clickable storage path label in AppFooter that copies RECORDINGS_DIR."""

    async def on_click(self) -> None:
        await self.app.run_action("copy_recordings_dir")


class NoticeBox(Vertical):
    """DuskyTUI alert banner with left accent border and subtle tinted background."""

    def __init__(self, message: str, level: str = "info", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.message = message
        self.level = level
        self.add_class(f"-{level}")

    def compose(self) -> ComposeResult:
        yield Label(self.message, id="notice-label")

    def update_notice(self, message: str, level: str) -> None:
        if message == self.message and level == self.level:
            return
        self.message = message
        self.level = level
        for cls_name in ("-info", "-warning", "-danger", "-success"):
            self.remove_class(cls_name)
        self.add_class(f"-{level}")
        self.query_one("#notice-label", Label).update(message)


class AppFooter(Vertical):
    """Unified DuskyTUI footer with FlowContainer shortcuts, storage link & status readout."""

    status_msg = reactive("")
    status_level = reactive("info")

    def __init__(self, backend_label: str = "PIPEWIRE NATIVE", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._backend_label = backend_label

    def compose(self) -> ComposeResult:
        with FlowContainer(id="footer-shortcuts-container"):
            yield Shortcut("r", "Record", "toggle_record", id="sc-r")
            yield Shortcut("Space", "Pause/Play", "toggle_pause_play", id="sc-space")
            yield Shortcut("c", "Discard", "cancel_record", id="sc-c")
            yield Shortcut("e", "Rename", "rename_selected", id="sc-e")
            yield Shortcut("d", "Delete", "delete_selected", id="sc-d")
            yield Shortcut("y", "Copy Path", "copy_path", id="sc-y")
            yield Shortcut("/", "Search", "focus_search", id="sc-slash")
            yield Shortcut("q", "Quit", "quit_app", id="sc-q")

        with Horizontal(id="footer-bottom-row"):
            yield FooterPathLink(f"󰈔 {RECORDINGS_DIR}", id="file-link")
            yield Label("│", classes="footer-sep")
            yield Label(self._backend_label, id="mode-btn")
            yield Label("│", classes="footer-sep")
            yield Label("", id="status-bar")

    def watch_status_msg(self, new_val: str) -> None:
        if not self.is_mounted:
            return
        bar = self.query_one("#status-bar", Label)
        tc = getattr(self.app, "theme_colors", DEFAULT_THEME)
        if new_val:
            txt = Text()
            txt.append("Status: ", style=tc["accent"])
            txt.append(new_val, style=tc.get(self.status_level, tc["fg"]))
            bar.update(txt)
            bar.display = True
        else:
            bar.display = False


class LiveVuMeter(Static):
    """Width-calibrated decibel meter with stereo/mono support and integrated timecode."""

    _DB_TICKS: Final[tuple[tuple[float, str], ...]] = (
        (-60.0, "-60"),
        (-48.0, "-48"),
        (-36.0, "-36"),
        (-24.0, "-24"),
        (-12.0, "-12"),
        (-6.0, "-6"),
        (0.0, "0dB"),
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.channel_peaks: list[float] = [-80.0, -80.0]
        self.elapsed: float = 0.0
        self.max_limit: float = 0.0
        self._last_clock_key: tuple[int, int] = (-1, -1)

    def update_levels(self, peaks: list[float]) -> None:
        new_peaks = peaks if peaks else [-80.0]
        if new_peaks != self.channel_peaks:
            self.channel_peaks = new_peaks
            self.refresh()

    def update_time(self, elapsed: float, max_limit: float = 0.0) -> None:
        clock_key = (int(elapsed * 10), int(max_limit))
        if clock_key != self._last_clock_key:
            self._last_clock_key = clock_key
            self.elapsed = elapsed
            self.max_limit = max_limit
            self.refresh()

    def render(self) -> RenderableType:
        full_width = self.size.width if self.size.width > 24 else 80
        prefix_len = 5   # "  L ╶" or "MIC ╶"
        suffix_len = 18  # "╴ -12.3 dB [CLIP]"
        bar_width = max(14, full_width - prefix_len - suffix_len)
        tc = getattr(self.app, "theme_colors", DEFAULT_THEME)

        # Build proportionally aligned dB scale header
        scale_chars = [" "] * (prefix_len + bar_width)
        for db_val, label in self._DB_TICKS:
            frac = (db_val + 60.0) / 60.0
            pos = prefix_len + int(frac * (bar_width - 1))
            if pos + len(label) > len(scale_chars):
                pos = max(prefix_len, len(scale_chars) - len(label))
            if pos >= prefix_len and all(c == " " for c in scale_chars[pos : pos + len(label)]):
                for offset, ch in enumerate(label):
                    scale_chars[pos + offset] = ch
        scale_str = "".join(scale_chars).rstrip()

        # Integrated timecode on far right
        total = int(self.elapsed)
        tenths = int((self.elapsed - total) * 10)
        clock_str = f"{total // 60:02d}:{total % 60:02d}.{tenths}"

        time_part = Text()
        time_part.append("TIMECODE  ", style=f"bold {tc['accent']}")
        time_part.append(clock_str, style=f"bold {tc['fg']}")
        if self.max_limit > 0:
            rem_total = int(max(0.0, self.max_limit - self.elapsed))
            time_part.append(f"  (-{rem_total // 60:02d}:{rem_total % 60:02d})", style=tc["warning"])

        header = Text()
        max_scale = max(0, full_width - len(time_part.plain) - 2)
        trimmed_scale = scale_str[:max_scale]
        header.append(trimmed_scale, style=f"bold {tc['accent']}")
        header.append(" " * max(2, full_width - len(trimmed_scale) - len(time_part.plain)))
        header.append_text(time_part)

        lines = [header]
        is_mono = len(self.channel_peaks) == 1
        green_len = int(bar_width * 0.70)
        yellow_len = int(bar_width * 0.88)

        for idx, peak_db in enumerate(self.channel_peaks[:2]):
            label = "MIC ╶" if is_mono else ("  L ╶" if idx == 0 else "  R ╶")
            clamped = max(-60.0, min(0.0, peak_db))
            filled = int(((clamped + 60.0) / 60.0) * bar_width)

            line = Text()
            line.append(label, style=f"bold {tc['fg']}")
            if filled > 0:
                g_count = min(filled, green_len)
                y_count = max(0, min(filled, yellow_len) - green_len)
                r_count = max(0, filled - yellow_len)
                if g_count:
                    line.append("━" * g_count, style=tc["success"])
                if y_count:
                    line.append("━" * y_count, style=tc["warning"])
                if r_count:
                    line.append("━" * r_count, style=f"bold {tc['error']}")
            unfilled = bar_width - filled
            if unfilled > 0:
                line.append("─" * unfilled, style=tc["muted"])
            line.append("╴ ", style=tc["muted"])

            if peak_db <= -60.0:
                line.append("  -∞ dB", style=f"bold {tc['muted']}")
            else:
                style = f"bold {tc['error']}" if peak_db > -3.0 else f"bold {tc['fg']}"
                line.append(f"{peak_db:5.1f} dB", style=style)
            if peak_db >= -0.5:
                line.append(" [CLIP]", style=f"bold {tc['error']} on {tc['bg']}")
            lines.append(line)

        if is_mono:
            lines.append(Text("     Mono capture (1 channel)", style=f"dim {tc['muted']}"))

        res = Text()
        for i, line in enumerate(lines):
            res.append_text(line)
            if i < len(lines) - 1:
                res.append("\n")
        return res


class RollingOscilloscope(Static):
    """Real-time rolling voice spectrum strip."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.history: deque[float] = deque(maxlen=100)

    def push_peak(self, peak_fraction: float) -> None:
        self.history.append(min(1.0, max(0.0, peak_fraction)))
        self.refresh()

    def clear(self) -> None:
        if self.history:
            self.history.clear()
            self.refresh()

    def render(self) -> RenderableType:
        width = max(10, self.size.width - 4)
        height = 4

        if self.history.maxlen != width:
            self.history = deque(self.history, maxlen=width)

        data = list(self.history)
        if len(data) < width:
            data = [0.0] * (width - len(data)) + data

        tc = getattr(self.app, "theme_colors", DEFAULT_THEME)
        lines = [Text() for _ in range(height)]

        for frac in data:
            total_steps = int(frac * height * 8)
            if frac >= 0.88:
                bar_color = f"bold {tc['error']}"
            elif frac >= 0.65:
                bar_color = tc["warning"]
            elif frac > 0.08:
                bar_color = tc["accent"]
            else:
                bar_color = f"dim {tc['muted']}"

            for r in range(height):
                row_from_bottom = (height - 1) - r
                row_base = row_from_bottom * 8
                if total_steps >= row_base + 8:
                    lines[r].append("█", style=bar_color)
                elif total_steps <= row_base:
                    if row_from_bottom == 0:
                        lines[r].append("·", style=f"dim {tc['muted']}")
                    else:
                        lines[r].append(" ", style="dim")
                else:
                    lines[r].append(UNICODE_BLOCKS[total_steps - row_base], style=bar_color)

        res = Text()
        for idx, line in enumerate(lines):
            res.append_text(line)
            if idx < height - 1:
                res.append("\n")

        return Panel(
            res,
            padding=(0, 1),
            border_style=tc["muted"],
            title=f"[bold {tc['accent']}]󰮔  LIVE VOICE SPECTRUM[/]",
            title_align="left",
        )


class PlayerWaveformWidget(Static):
    """Full-width interactive audio waveform with illuminated playhead and click-to-scrub."""

    class SeekRequested(Message):
        """Posted when the user clicks along the waveform bar to seek."""

        def __init__(self, fraction: float) -> None:
            super().__init__()
            self.fraction = fraction

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.peaks: list[float] = []
        self.progress_fraction: float = 0.0
        self._is_loading_peaks: bool = False

    def set_data(self, peaks: list[float], progress_fraction: float = 0.0, *, loading: bool = False) -> None:
        self.peaks = peaks
        self.progress_fraction = max(0.0, min(1.0, progress_fraction))
        self._is_loading_peaks = loading
        self.refresh()

    def on_click(self, event: Click) -> None:
        if not self.peaks:
            return
        inner_width = max(1, self.size.width - 4)
        # Panel border (1) + horizontal padding (1) = 2 cells left inset
        rel_x = event.x - 2
        if 0 <= rel_x < inner_width:
            frac = rel_x / max(1, inner_width - 1)
            self.post_message(self.SeekRequested(max(0.0, min(1.0, frac))))

    def render(self) -> RenderableType:
        width = max(10, self.size.width - 4)
        height = 4
        tc = getattr(self.app, "theme_colors", DEFAULT_THEME)

        if not self.peaks:
            msg = (
                "\n  Extracting audio waveform peaks...  \n"
                if self._is_loading_peaks
                else "\n  Select a voice memo to inspect audio waveform & playback  \n"
            )
            placeholder = Text(msg, style=f"italic dim {tc['muted']}")
            return Panel(
                placeholder,
                padding=(0, 1),
                border_style=tc["muted"],
                title="󰮔  AUDIO WAVEFORM",
                title_align="left",
            )

        peak_count = len(self.peaks)
        resampled = [self.peaks[min(int(i * peak_count / width), peak_count - 1)] for i in range(width)]
        playhead_col = min(width - 1, max(0, int(self.progress_fraction * width)))
        lines = [Text() for _ in range(height)]

        for x, frac in enumerate(resampled):
            if x == playhead_col:
                for r in range(height):
                    lines[r].append("│", style=f"bold {tc['bg']} on {tc['accent']}")
                continue

            color = tc["success"] if x < playhead_col else f"dim {tc['muted']}"
            total_steps = int(frac * height * 8)
            for r in range(height):
                row_from_bottom = (height - 1) - r
                row_base = row_from_bottom * 8
                if total_steps >= row_base + 8:
                    lines[r].append("█", style=color)
                elif total_steps <= row_base:
                    if row_from_bottom == 0:
                        lines[r].append("·", style=f"dim {tc['muted']}")
                    else:
                        lines[r].append(" ", style="dim")
                else:
                    lines[r].append(UNICODE_BLOCKS[total_steps - row_base], style=color)

        full = Text()
        for i, row in enumerate(lines):
            full.append_text(row)
            if i < height - 1:
                full.append("\n")

        pct = int(self.progress_fraction * 100)
        return Panel(
            full,
            padding=(0, 1),
            border_style=tc["muted"],
            title=f"[bold {tc['accent']}]󰮔  AUDIO WAVEFORM[/] [dim]({pct}% • click to scrub)[/]",
            title_align="left",
        )


class RenameMemoModal(ModalScreen[str | None]):
    """Modal dialog for renaming a recorded voice memo."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, current_name: str) -> None:
        super().__init__()
        self.current_name = current_name

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-dialog"):
            yield Label("󰏫  Rename Voice Memo", id="modal-title")
            yield Input(value=self.current_name, id="rename-input")
            with Horizontal(classes="modal-btn-container"):
                yield Button("Cancel", id="btn-cancel", classes="modal-cancel-btn")
                yield Button("Save", id="btn-save", classes="modal-close-btn")

    def on_mount(self) -> None:
        inp = self.query_one("#rename-input", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save":
            val = self.query_one("#rename-input", Input).value.strip()
            self.dismiss(val if val else None)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        self.dismiss(val if val else None)


class DeleteMemoModal(ModalScreen[bool]):
    """Confirmation modal for permanently deleting a voice memo."""

    BINDINGS = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(self, memo: VoiceMemo) -> None:
        super().__init__()
        self.memo = memo

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog", classes="-danger"):
            yield Label("󰆴  Delete Voice Memo?", id="modal-title")
            yield Label(
                f"Permanently delete '{self.memo.name}'?\n"
                f"Duration: {self.memo.formatted_duration}  •  Size: {self.memo.formatted_size}",
                id="confirm-message",
            )
            with Horizontal(classes="modal-btn-container"):
                yield Button("Cancel", id="btn-cancel", classes="modal-cancel-btn")
                yield Button("Delete", id="btn-confirm", classes="modal-close-btn")

    def on_mount(self) -> None:
        self.query_one("#btn-cancel", Button).focus()

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-confirm")

# =============================================================================
# PRIMARY APPLICATION TUI (TEXTUAL 8.2+ / DUSKYTUI DESIGN SYSTEM)
# =============================================================================

class DuskyVoiceApp(App[None]):
    """Dusky Voice Memos Textual Application (DuskyTUI Design System)."""

    TITLE = "DUSKY VOICE MEMOS"
    SUB_TITLE = "Wayland & PipeWire Native Studio"

    CSS = """
    Screen {
        background: $background;
        color: $foreground;
    }

    #main-box {
        width: 100%;
        height: 100%;
        border: solid $primary 50%;
        border-title-color: $primary;
        border-title-style: bold;
        border-title-align: center;
        background: transparent;
        padding: 0 1;
    }

    /* Consolidated Top HUD Header & Tabs Bar */
    #hud-header {
        width: 100%;
        height: 1;
        margin-bottom: 1;
        background: transparent;
        padding: 0 1;
        align: center middle;
    }

    #hud-app-name {
        width: auto;
        color: $primary;
        text-style: bold;
        margin-right: 2;
    }

    #tabs-container {
        width: 1fr;
        height: 1;
        overflow-x: auto;
        scrollbar-size: 0 0;
        align: center middle;
    }

    #hud-telemetry {
        width: auto;
        height: 1;
        align: right middle;
        margin-left: 2;
    }

    #hud-status-badge {
        width: auto;
        text-style: bold;
    }

    #hud-status-sep {
        width: auto;
        color: $secondary;
        margin: 0 1;
    }

    #hud-stats {
        width: auto;
        color: $foreground 80%;
        text-style: bold;
    }

    Tabs {
        width: auto;
        height: 1;
        background: transparent;
    }

    Tabs #tabs-scroll,
    Tabs #tabs-list-bar,
    Tabs #tabs-list {
        width: auto;
        height: 1;
        min-width: 0;
    }

    Tabs Underline {
        display: none;
    }

    Tab {
        height: 1;
        padding: 0 2;
        color: $primary 60%;
        background: transparent;
        border: none;
    }

    Tab:hover {
        color: $foreground;
        background: $primary 25%;
    }

    Tab.-active {
        color: $background;
        background: $primary;
        text-style: bold;
        border: none;
    }

    #content-area {
        height: 1fr;
        padding: 0 1;
    }

    .tab_pane {
        height: 1fr;
        overflow-y: auto;
    }

    /* Console Deck & NoticeBox Banner */
    #console_deck {
        height: auto;
        border: solid $secondary 40%;
        background: $surface 30%;
        padding: 1 1 0 1;
        margin-bottom: 0;
    }

    NoticeBox {
        width: 100%;
        height: auto;
        padding: 0 1;
        margin: 0 0 1 0;
        background: transparent;
    }

    NoticeBox.-info {
        border-left: solid $primary;
        background: $primary 10%;
    }

    NoticeBox.-warning {
        border-left: solid $warning;
        background: $warning 10%;
    }

    NoticeBox.-danger {
        border-left: solid $error;
        background: $error 15%;
    }

    NoticeBox.-success {
        border-left: solid $success;
        background: $success 10%;
    }

    #notice-label {
        color: $foreground;
        text-style: bold;
    }

    #vu_meter {
        height: 3;
        margin: 0 1;
    }

    #oscilloscope {
        height: 6;
        margin: 0;
    }

    #studio_controls {
        height: auto;
        align: center middle;
        margin-top: 1;
        margin-bottom: 0;
    }

    .studio_btn {
        margin: 0 1;
        min-width: 18;
        height: 3;
        border: solid $secondary 50%;
        background: $surface;
        color: $foreground;
        text-style: bold;
        outline: none;
        box-sizing: border-box;
    }

    .studio_btn:focus {
        border: solid $primary;
        background: $primary 20%;
        color: $foreground;
        text-style: bold;
        outline: none;
    }

    .studio_btn:hover {
        border: solid $primary;
        background: $primary 25%;
        color: $foreground;
        outline: none;
    }

    #btn_record.-recording {
        background: $error;
        color: $background;
        text-style: bold;
        border: solid $error;
        outline: none;
    }

    /* Balanced 2-Row Studio Configuration Card */
    #settings_card {
        height: auto;
        border: solid $secondary 40%;
        background: $surface 30%;
        padding: 1 1 0 1;
        margin-top: 1;
    }

    .config_row {
        height: auto;
        width: 100%;
    }

    .config_col {
        width: 1fr;
        height: auto;
        padding: 0 1;
    }

    .config_label {
        color: $primary;
        text-style: bold;
        margin-bottom: 0;
    }

    #metadata_row {
        height: auto;
        width: 100%;
        margin-top: 0;
    }

    Select {
        width: 100%;
        height: auto;
        margin-bottom: 1;
        border: none;
        background: transparent;
        box-sizing: border-box;
    }

    Select > SelectCurrent {
        width: 100%;
        height: 3;
        border: solid $secondary 55%;
        background: $surface;
        color: $foreground;
        padding: 0 1;
        align: left middle;
        box-sizing: border-box;
    }

    Select > SelectCurrent > Static#label {
        width: 1fr;
        height: 1;
        color: $foreground;
        content-align: left middle;
        overflow-x: hidden;
        text-overflow: ellipsis;
    }

    Select > SelectCurrent > .arrow {
        width: auto;
        height: 1;
        color: $primary;
        text-style: bold;
        padding-left: 1;
        content-align: right middle;
    }

    Select:hover > SelectCurrent {
        border: solid $primary 80%;
        background: $primary 15%;
        color: $foreground;
    }

    Select:focus > SelectCurrent,
    Select.-expanded > SelectCurrent {
        border: solid $primary;
        background: $primary 22%;
        color: $foreground;
        text-style: bold;
    }

    SelectOverlay {
        width: 100%;
        border: solid $primary;
        background: $background;
        color: $foreground;
        max-height: 12;
        padding: 0;
        box-sizing: border-box;
    }

    SelectOverlay > .option-list--option {
        padding: 0 1;
        color: $foreground;
    }

    SelectOverlay > .option-list--option-hover {
        background: $primary 18%;
        color: $foreground;
    }

    SelectOverlay > .option-list--option-highlighted {
        background: $primary 32%;
        color: $foreground;
        text-style: bold;
    }

    Input {
        width: 100%;
        height: 3;
        border: solid $secondary 55%;
        background: $surface;
        color: $foreground;
        padding: 0 1;
        box-sizing: border-box;
    }

    Input:hover {
        border: solid $primary 80%;
        background: $primary 12%;
    }

    Input:focus {
        border: solid $primary;
        background: $primary 20%;
        color: $foreground;
    }

    #input_title {
        margin-bottom: 1;
    }

    #lbl_target_preview {
        width: 100%;
        height: 3;
        border: solid $secondary 40%;
        background: $surface 60%;
        color: $primary;
        padding: 0 1;
        margin-bottom: 1;
        content-align: left middle;
        overflow-x: hidden;
        text-overflow: ellipsis;
        box-sizing: border-box;
    }

    /* Library Styling */
    #library_header_row {
        height: 3;
        margin-bottom: 1;
    }

    #search_input {
        width: 1fr;
        margin-right: 1;
    }

    #lbl_memo_count {
        height: 3;
        content-align: center middle;
        color: $primary;
        text-style: bold;
        margin-right: 1;
    }

    #library_table {
        height: 1fr;
        min-height: 8;
        background: transparent;
        border: solid $secondary 40%;
    }

    #library_table > .datatable--cursor {
        background: $primary 25%;
        color: $foreground;
        text-style: bold;
    }

    #player_panel {
        height: auto;
        border: solid $secondary 40%;
        background: $surface 30%;
        padding: 1 1;
        margin-top: 1;
    }

    #lbl_player_title {
        content-align: center middle;
        margin-bottom: 1;
        text-style: bold;
    }

    #player_waveform {
        height: 6;
        margin: 0;
    }

    #player_controls {
        height: 3;
        align: center middle;
        margin-top: 1;
    }

    .player_btn {
        margin: 0 1;
        min-width: 13;
        height: 3;
        border: solid $secondary 40%;
        background: $surface;
        color: $foreground;
    }

    .player_btn:hover {
        border: solid $primary;
        background: $primary 25%;
        color: $foreground;
    }

    .player_btn.-primary {
        border: solid $primary;
        background: $primary 20%;
        color: $primary;
        text-style: bold;
    }

    .player_btn.-primary:hover {
        background: $primary;
        color: $background;
    }

    .player_btn.-danger {
        border: solid $error 50%;
        color: $error;
    }

    .player_btn.-danger:hover {
        background: $error;
        color: $background;
    }

    /* Devices View */
    #devices_header_row {
        height: 3;
        width: 100%;
        align: left middle;
        padding: 0 1;
        margin-bottom: 1;
    }

    #devices_title_label {
        width: 1fr;
        color: $primary;
        text-style: bold;
    }

    #devices_info {
        height: auto;
        padding: 0 1;
    }

    /* AppFooter */
    #footer {
        width: 100%;
        height: auto;
        min-height: 2;
        border-top: solid $secondary;
        padding: 0 1;
        background: transparent;
        margin-top: 1;
    }

    #footer-shortcuts-container {
        height: auto;
        width: 100%;
    }

    .footer-shortcut {
        padding: 0 1;
        background: transparent;
    }

    .footer-shortcut:hover {
        text-style: bold;
        color: $foreground;
        background: $primary 25%;
    }

    .footer-shortcut.-active {
        text-style: bold;
        color: $background;
        background: $primary;
    }

    #footer-bottom-row {
        width: 100%;
        height: 1;
        margin-top: 0;
    }

    .footer-sep {
        color: $secondary;
    }

    #file-link {
        padding: 0 1;
        color: $primary;
        text-style: bold;
    }

    #file-link:hover {
        background: $primary 25%;
    }

    #mode-btn {
        padding: 0 1;
        color: $foreground;
    }

    #status-bar {
        padding: 0 1;
    }

    /* Modal Screens */
    RenameMemoModal, DeleteMemoModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }

    #modal-dialog, #confirm-dialog {
        width: 56;
        height: auto;
        background: $background;
        border: solid $primary;
        padding: 1 2;
    }

    #modal-title {
        color: $primary;
        margin-bottom: 1;
        text-style: bold;
        border-bottom: solid $secondary;
        content-align: center middle;
        width: 100%;
    }

    #confirm-message {
        color: $foreground;
        margin-bottom: 1;
    }

    .modal-btn-container {
        width: 100%;
        height: auto;
        align: center middle;
        margin-top: 1;
        background: transparent;
    }

    .modal-close-btn {
        background: $primary;
        color: $background;
        text-style: bold;
        padding: 0 2;
        height: 1;
        margin: 0 1;
    }

    .modal-cancel-btn {
        background: $secondary;
        color: $foreground;
        text-style: bold;
        padding: 0 2;
        height: 1;
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit_app", "Quit", show=False, priority=True),
        Binding("ctrl+q", "quit_app", "Quit", show=False, priority=True),
        Binding("ctrl+tab", "switch_tab", "Switch Tab", show=False),
        Binding("1", "tab_studio", "Studio", show=False),
        Binding("2", "tab_library", "Library", show=False),
        Binding("3", "tab_devices", "Devices", show=False),
        Binding("r", "toggle_record", "Record / Stop", show=False),
        Binding("space", "toggle_pause_play", "Pause / Play", show=False),
        Binding("c", "cancel_record", "Discard", show=False),
        Binding("left", "seek_back", "-5s", show=False),
        Binding("h", "seek_back", "-5s", show=False),
        Binding("right", "seek_fwd", "+5s", show=False),
        Binding("l", "seek_fwd", "+5s", show=False),
        Binding("s", "stop_player", "Stop", show=False),
        Binding("y", "copy_path", "Copy Path", show=False),
        Binding("e", "rename_selected", "Rename", show=False),
        Binding("d", "delete_selected", "Delete", show=False),
        Binding("slash", "focus_search", "Search", show=False),
        Binding("q", "quit_app", "Quit", show=False),
    ]

    _CONFIG_WIDGET_IDS: Final[tuple[str, ...]] = (
        "#select_source",
        "#select_mic_device",
        "#select_sink_device",
        "#select_format",
        "#select_channels",
        "#select_rate",
        "#select_delay",
        "#select_duration",
        "#input_title",
    )

    def __init__(self, caps: RuntimeCapabilities) -> None:
        super().__init__()
        self.caps = caps
        self.theme_colors = load_matugen_theme()
        self.last_theme_mtime_ns: int = 0

        self.engine = DuskyAudioEngine(caps)
        self.player = DuskyAudioPlayer(caps)

        self.memos: list[VoiceMemo] = []
        self.selected_memo: VoiceMemo | None = None
        self._active_badge_row_key: str | None = None
        self.pw_sources: list[PipeWireNode] = []
        self.pw_sinks: list[PipeWireNode] = []

        self.countdown_remaining: int = 0
        self._countdown_timer: Any = None
        self._poll_timer: Any = None
        self._poll_interval: float = 0.0
        self._theme_timer: Any = None

        self._last_hud_badge: tuple[Any, ...] = ()
        self._last_hud_stats: tuple[int, int] = (-1, -1)
        self._devices_refreshing: bool = False

        # Shutdown & worker coordination (zero ThreadPoolExecutor / non-daemon threads)
        self._app_shutting_down: bool = False
        self._cleaned_up: bool = False
        self._pty_closed: bool = False
        self._shutdown_event = threading.Event()

        # Single coalesced daemon thread for library scanning
        self._library_trigger = threading.Event()
        self._library_cancel = threading.Event()
        self._library_thread: threading.Thread | None = None

        # Single latest-wins daemon thread for waveform peak extraction
        self._waveform_lock = threading.Lock()
        self._waveform_trigger = threading.Event()
        self._waveform_cancel = threading.Event()
        self._waveform_generation: int = 0
        self._waveform_request: tuple[Path, float, int] | None = None
        self._waveform_thread: threading.Thread | None = None

        # Wire engine & player hooks
        self.engine.on_level_callback = self._on_engine_level
        self.engine.on_state_change = self._on_engine_state_change
        self.engine.on_finished = self._on_engine_finished
        self.engine.on_error = self._on_engine_error

        self.player.on_state_change = self._on_player_state_change
        self.player.on_eos = self._on_player_eos
        self.player.on_error = self._on_player_error
        self.player.on_position_changed = self._on_player_position_changed

    # -------------------------------------------------------------------------
    # THREAD MARSHALLING & PERSISTENT DAEMON WORKERS
    # -------------------------------------------------------------------------

    def _safe_dispatch(self, func: Callable[..., Any], *args: Any) -> None:
        """Run callback directly on main thread, or marshal safely if off-thread."""
        if self._app_shutting_down:
            return
        if threading.current_thread() is threading.main_thread():
            func(*args)
        else:
            try:
                self.call_from_thread(func, *args)
            except RuntimeError:
                pass

    def _start_daemon_workers(self) -> None:
        self._library_thread = threading.Thread(
            target=self._library_worker_loop,
            name="dusky-library",
            daemon=True,
        )
        self._library_thread.start()

        self._waveform_thread = threading.Thread(
            target=self._waveform_worker_loop,
            name="dusky-waveform",
            daemon=True,
        )
        self._waveform_thread.start()

    def _library_worker_loop(self) -> None:
        while not self._shutdown_event.is_set():
            self._library_trigger.wait()
            if self._shutdown_event.is_set():
                break
            self._library_trigger.clear()
            self._library_cancel.clear()

            exclude = self.engine.active_output_path
            memos = VoiceLibrary.scan_memos(exclude=exclude, cancelled=self._library_cancel)
            if self._shutdown_event.is_set() or self._library_cancel.is_set():
                continue
            self._safe_dispatch(self._apply_scanned_library, memos)

    def _waveform_worker_loop(self) -> None:
        while not self._shutdown_event.is_set():
            self._waveform_trigger.wait()
            if self._shutdown_event.is_set():
                break
            self._waveform_trigger.clear()

            with self._waveform_lock:
                req = self._waveform_request
                self._waveform_request = None
                self._waveform_cancel.clear()

            if req is None:
                continue
            path, duration_sec, generation = req
            peaks = compute_waveform_peaks(
                path,
                duration_sec=duration_sec,
                cancelled=self._waveform_cancel,
            )
            if self._shutdown_event.is_set() or self._waveform_cancel.is_set():
                continue
            self._safe_dispatch(self._apply_waveform_peaks, path, peaks, generation)

    # -------------------------------------------------------------------------
    # THEME & COMPOSITION
    # -------------------------------------------------------------------------

    def apply_current_theme(self) -> None:
        """Register and apply dynamic Matugen theme."""
        tc = self.theme_colors
        dusky_theme = Theme(
            name="dusky",
            primary=tc["accent"],
            secondary=tc["muted"],
            accent=tc["accent"],
            foreground=tc["fg"],
            background=tc["bg"],
            success=tc["success"],
            warning=tc["warning"],
            error=tc["error"],
            surface=tc["bg"],
            panel=tc["bg"],
        )
        self.register_theme(dusky_theme)
        self.theme = "dusky"

    def _format_select_options(self) -> tuple[list[tuple[str, str]], str]:
        if self.caps.available_formats:
            options = [(fmt.label, fmt.value) for fmt in self.caps.available_formats]
            default = AudioFormat.OPUS.value if AudioFormat.OPUS in self.caps.available_formats else options[0][1]
            return options, default
        return [("No GStreamer Encoders Found", "")], ""

    def compose(self) -> ComposeResult:
        fmt_options, fmt_default = self._format_select_options()

        with Container(id="main-box") as box:
            box.border_title = " 󰍬  DUSKY VOICE MEMOS "
            with Horizontal(id="hud-header"):
                yield Label("󰍬  DUSKY VOICE", id="hud-app-name")
                with Horizontal(id="tabs-container"):
                    yield Tabs(
                        Tab("󰍬  Studio Recorder", id="tab_studio"),
                        Tab("󰉋  Voice Memos", id="tab_library"),
                        Tab("󰋋  Audio Devices", id="tab_devices"),
                        id="app_tabs",
                    )
                with Horizontal(id="hud-telemetry"):
                    yield Label("● STANDBY", id="hud-status-badge")
                    yield Label("•", id="hud-status-sep")
                    yield Label("0 memos • 0.0 KB", id="hud-stats")

            with Container(id="content-area"):
                # Pane 1: Studio Recorder
                with Vertical(id="pane_studio", classes="tab_pane"):
                    with Vertical(id="console_deck"):
                        yield NoticeBox("󰐽  STANDBY • READY TO CAPTURE", level="info", id="banner_box")
                        yield LiveVuMeter(id="vu_meter")
                        yield RollingOscilloscope(id="oscilloscope")

                    with Horizontal(id="studio_controls"):
                        yield Button("󰐽  Record", id="btn_record", classes="studio_btn")
                        yield Button("󰏤  Pause", id="btn_pause", classes="studio_btn", disabled=True)
                        yield Button("󰅖  Cancel", id="btn_cancel", classes="studio_btn", disabled=True)

                    with Vertical(id="settings_card"):
                        with Horizontal(classes="config_row"):
                            with Vertical(classes="config_col"):
                                yield Label("󰍬  INPUT SOURCE", classes="config_label")
                                yield Select(
                                    [(src.label, src.value) for src in RecordSource],
                                    allow_blank=False,
                                    value=RecordSource.MIC.value,
                                    id="select_source",
                                )
                            with Vertical(classes="config_col"):
                                yield Label("󰍬  MICROPHONE NODE", classes="config_label")
                                yield Select(
                                    [("System Default", "")],
                                    allow_blank=False,
                                    value="",
                                    id="select_mic_device",
                                )
                            with Vertical(classes="config_col"):
                                yield Label("󰋋  DESKTOP MONITOR", classes="config_label")
                                yield Select(
                                    [("System Default", "")],
                                    allow_blank=False,
                                    value="",
                                    id="select_sink_device",
                                )
                            with Vertical(classes="config_col"):
                                yield Label("󰋋  CODEC & FORMAT", classes="config_label")
                                yield Select(
                                    fmt_options,
                                    allow_blank=False,
                                    value=fmt_default,
                                    id="select_format",
                                )

                        with Horizontal(classes="config_row"):
                            with Vertical(classes="config_col"):
                                yield Label("󰮔  CHANNELS", classes="config_label")
                                yield Select(
                                    [("Stereo (2 Channels)", 2), ("Mono (1 Channel)", 1)],
                                    allow_blank=False,
                                    value=2,
                                    id="select_channels",
                                )
                            with Vertical(classes="config_col"):
                                yield Label("󰓃  SAMPLE RATE", classes="config_label")
                                yield Select(
                                    [
                                        ("48 kHz • Studio Master", 48000),
                                        ("44.1 kHz • CD Standard", 44100),
                                    ],
                                    allow_blank=False,
                                    value=48000,
                                    id="select_rate",
                                )
                            with Vertical(classes="config_col"):
                                yield Label("󰔛  PRE-ROLL DELAY", classes="config_label")
                                yield Select(
                                    [
                                        ("Instant (0s)", 0),
                                        ("3 Seconds", 3),
                                        ("5 Seconds", 5),
                                        ("10 Seconds", 10),
                                    ],
                                    allow_blank=False,
                                    value=0,
                                    id="select_delay",
                                )
                            with Vertical(classes="config_col"):
                                yield Label("󰔛  AUTO-STOP LIMIT", classes="config_label")
                                yield Select(
                                    [
                                        ("Unlimited", 0),
                                        ("30 Seconds", 30),
                                        ("1 Minute", 60),
                                        ("5 Minutes", 300),
                                        ("10 Minutes", 600),
                                        ("30 Minutes", 1800),
                                    ],
                                    allow_blank=False,
                                    value=0,
                                    id="select_duration",
                                )

                        with Horizontal(id="metadata_row"):
                            with Vertical(classes="config_col"):
                                yield Label("󰏫  MEMO TITLE PREFIX", classes="config_label")
                                yield Input(placeholder="Optional title prefix (default: Memo)", id="input_title")
                            with Vertical(classes="config_col"):
                                yield Label("󰈔  OUTPUT TARGET PREVIEW", classes="config_label")
                                yield Static("", id="lbl_target_preview")

                # Pane 2: Memo Library
                with Vertical(id="pane_library", classes="tab_pane"):
                    with Horizontal(id="library_header_row"):
                        yield Input(
                            placeholder="󰍉  Search voice memos by title... (Press / to focus)",
                            id="search_input",
                        )
                        yield Label(" 0 memos ", id="lbl_memo_count")
                        yield Button("󰑕  Refresh", id="btn_refresh_lib", classes="player_btn")

                    yield DataTable(id="library_table", cursor_type="row")

                    with Vertical(id="player_panel"):
                        yield Label("No recording selected", id="lbl_player_title")
                        yield PlayerWaveformWidget(id="player_waveform")
                        with Horizontal(id="player_controls"):
                            yield Button("  Play", id="btn_player_play", classes="player_btn -primary")
                            yield Button("  -5s", id="btn_player_bwd", classes="player_btn")
                            yield Button("  +5s", id="btn_player_fwd", classes="player_btn")
                            yield Button("  Stop", id="btn_player_stop", classes="player_btn")
                            yield Button("󰆏  Copy Path", id="btn_player_copy", classes="player_btn")
                            yield Button("󰏫  Rename", id="btn_player_rename", classes="player_btn")
                            yield Button("󰆴  Delete", id="btn_player_delete", classes="player_btn -danger")

                # Pane 3: Devices & Hardware Graph
                with VerticalScroll(id="pane_devices", classes="tab_pane"):
                    with Horizontal(id="devices_header_row"):
                        yield Label("󰋋  PIPEWIRE AUDIO GRAPH & RUNTIME CAPABILITIES", id="devices_title_label")
                        yield Button("󰑕  Refresh Nodes", id="btn_refresh_devices", classes="player_btn")
                    yield Static("Discovering PipeWire nodes...", id="devices_info")

            yield AppFooter(backend_label=self.caps.backend_label, id="footer")

    # -------------------------------------------------------------------------
    # LIFECYCLE, SIGNALS & ADAPTIVE POLLING
    # -------------------------------------------------------------------------

    def on_mount(self) -> None:
        self.apply_current_theme()
        if THEME_FILE.is_file():
            try:
                self.last_theme_mtime_ns = THEME_FILE.stat().st_mtime_ns
            except OSError:
                pass

        # Initialize DataTable columns once
        table = self.query_one("#library_table", DataTable)
        table.add_column("Status", key="status")
        table.add_column("Title", key="title")
        table.add_column("Format", key="format")
        table.add_column("Duration", key="duration")
        table.add_column("Size", key="size")
        table.add_column("Date Recorded", key="date")

        self._install_signal_handlers()
        self._start_daemon_workers()
        self._theme_timer = self.set_interval(1.5, self._watch_theme_file)

        self._sync_config_controls()
        self._update_target_preview()
        self._show_pane("pane_studio")

        if not self.caps.capture_backend or not self.caps.available_formats:
            self.query_one("#btn_record", Button).disabled = True
            self.query_one("#banner_box", NoticeBox).update_notice(
                "󰅖  MISSING GSTREAMER CAPTURE OR ENCODER PLUGINS",
                level="danger",
            )
            self.set_status("Install gst-plugin-pipewire and gst-plugins-good/base", "error")
        else:
            self.set_status("Ready", "info")

        self._refresh_library()
        self._refresh_devices_async()
        self._update_hud()

    def _install_signal_handlers(self) -> None:
        """Bridge POSIX SIGINT/SIGTERM/SIGHUP (e.g. Hyprland closing foot) into clean teardown."""
        if self.is_headless:
            return
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            pty_closed = sig == signal.SIGHUP
            try:
                loop.add_signal_handler(sig, self._initiate_shutdown, pty_closed)
            except (NotImplementedError, RuntimeError, ValueError):
                pass

    def _sync_poll_timer(self) -> None:
        """Run telemetry polling at 50 ms when active, 200 ms when paused/finalizing, off when idle."""
        if self._app_shutting_down:
            if self._poll_timer is not None:
                self._poll_timer.stop()
                self._poll_timer = None
                self._poll_interval = 0.0
            return

        active_fast = (
            self.engine.state == RecordState.RECORDING
            or self.player.state == PlayerState.PLAYING
        )
        active_slow = (
            self.engine.state in (RecordState.PAUSED, RecordState.FINALIZING)
            or self.engine.pipeline is not None
            or self.player.state == PlayerState.PAUSED
        )
        desired = 0.05 if active_fast else (0.20 if active_slow else 0.0)
        if desired == self._poll_interval:
            return
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        self._poll_interval = desired
        if desired > 0.0:
            self._poll_timer = self.set_interval(desired, self._clock_tick)

    def _watch_theme_file(self) -> None:
        """Live synchronization with Matugen dynamic palette updates."""
        if self._app_shutting_down or not THEME_FILE.is_file():
            return
        try:
            mtime_ns = THEME_FILE.stat().st_mtime_ns
            if mtime_ns != self.last_theme_mtime_ns:
                self.last_theme_mtime_ns = mtime_ns
                self.theme_colors = load_matugen_theme()
                self.apply_current_theme()
                self._last_hud_badge = ()
                self._last_hud_stats = (-1, -1)
                self._update_hud()
                self._update_target_preview()
                self._render_library()
                self._render_devices_view()
                if self.selected_memo is not None:
                    self._update_player_title(self.player.get_position())
                self.refresh(layout=True)
        except OSError:
            pass

    def _show_pane(self, target_id: str) -> None:
        for pane_id in ("pane_studio", "pane_library", "pane_devices"):
            self.query_one(f"#{pane_id}").display = (pane_id == target_id)

    def set_status(self, msg: str, level: str = "info") -> None:
        if self._app_shutting_down or not self.is_mounted:
            return
        footer = self.query_one("#footer", AppFooter)
        footer.status_level = level
        footer.status_msg = msg

    def _blink_shortcut(self, widget_id: str) -> None:
        if self._app_shutting_down or not self.is_mounted:
            return
        try:
            self.query_one(f"#{widget_id}", Shortcut).blink()
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # CONTEXT-AWARE BINDING GATING & ROUTING ACTIONS
    # -------------------------------------------------------------------------

    def _active_tab_id(self) -> str:
        try:
            return self.query_one("#app_tabs", Tabs).active or "tab_studio"
        except Exception:
            return "tab_studio"

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if isinstance(self.screen, ModalScreen):
            return action == "quit_app"

        focused = self.focused
        in_input = isinstance(focused, Input)

        if action in ("seek_back", "seek_fwd"):
            if self._active_tab_id() != "tab_library":
                return False
            if isinstance(focused, (Input, Select, Tabs)):
                return False
            return self.selected_memo is not None

        if action == "toggle_pause_play":
            if isinstance(focused, (Input, Button, Select)):
                return False
            tab = self._active_tab_id()
            if tab == "tab_studio":
                return self.engine.state in (RecordState.RECORDING, RecordState.PAUSED)
            if tab == "tab_library":
                return self.selected_memo is not None
            return False

        if action in ("stop_player", "copy_path", "rename_selected", "delete_selected"):
            if in_input or self._active_tab_id() != "tab_library":
                return False
            return self.selected_memo is not None

        if action in ("toggle_record", "cancel_record", "focus_search", "tab_studio", "tab_library", "tab_devices"):
            if in_input:
                return False

        return True

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        tab_id = event.tab.id
        if tab_id == "tab_studio":
            self._show_pane("pane_studio")
        elif tab_id == "tab_library":
            self._show_pane("pane_library")
            self._refresh_library()
        elif tab_id == "tab_devices":
            self._show_pane("pane_devices")
            self._refresh_devices_async()

    def action_tab_studio(self) -> None:
        self.query_one("#app_tabs", Tabs).active = "tab_studio"

    def action_tab_library(self) -> None:
        self.query_one("#app_tabs", Tabs).active = "tab_library"

    def action_tab_devices(self) -> None:
        self.query_one("#app_tabs", Tabs).active = "tab_devices"

    def action_switch_tab(self) -> None:
        tabs = self.query_one("#app_tabs", Tabs)
        order = ("tab_studio", "tab_library", "tab_devices")
        cur = tabs.active if tabs.active in order else "tab_studio"
        tabs.active = order[(order.index(cur) + 1) % len(order)]

    def action_focus_search(self) -> None:
        self._blink_shortcut("sc-slash")
        self.action_tab_library()
        self.query_one("#search_input", Input).focus()

    # -------------------------------------------------------------------------
    # STUDIO RECORDER LOGIC
    # -------------------------------------------------------------------------

    def _sync_config_controls(self) -> None:
        """Synchronize Studio config disabled states (recording lock, Opus 48 kHz lock, source mode)."""
        if self._app_shutting_down or not self.is_mounted:
            return
        locked = self.engine.state != RecordState.IDLE
        for widget_id in self._CONFIG_WIDGET_IDS:
            self.query_one(widget_id).disabled = locked

        if locked:
            return

        src_val = str(self.query_one("#select_source", Select).value or RecordSource.MIC.value)
        self.query_one("#select_mic_device", Select).disabled = (src_val == RecordSource.SYSTEM.value)
        self.query_one("#select_sink_device", Select).disabled = (src_val == RecordSource.MIC.value)

        fmt_val = str(self.query_one("#select_format", Select).value or "")
        rate_sel = self.query_one("#select_rate", Select)
        if fmt_val == AudioFormat.OPUS.value:
            if rate_sel.value != 48000:
                rate_sel.value = 48000
            rate_sel.disabled = True
        else:
            rate_sel.disabled = False

    def _update_target_preview(self) -> None:
        if self._app_shutting_down or not self.is_mounted:
            return
        raw_title = self.query_one("#input_title", Input).value.strip()
        prefix = clean_memo_stem(raw_title) if raw_title else "Memo"
        if not prefix:
            prefix = "Memo"
        fmt_val = str(self.query_one("#select_format", Select).value or "")
        ext = f".{fmt_val}" if fmt_val else ".opus"

        try:
            dir_display = f"~/{RECORDINGS_DIR.relative_to(HOME_DIR)}"
        except ValueError:
            dir_display = str(RECORDINGS_DIR)

        tc = self.theme_colors
        txt = Text()
        txt.append(f"{dir_display}/", style=f"dim {tc['muted']}")
        txt.append(f"{prefix}_YYYY-MM-DD_HH-MM-SS{ext}", style=f"bold {tc['accent']}")
        self.query_one("#lbl_target_preview", Static).update(txt)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id in ("select_source", "select_format"):
            self._sync_config_controls()
            self._update_target_preview()

    def action_toggle_record(self) -> None:
        self._blink_shortcut("sc-r")
        state = self.engine.state
        if state == RecordState.IDLE:
            self._start_recording_flow()
        elif state in (RecordState.RECORDING, RecordState.PAUSED):
            self.engine.stop(stopped_by_timer=False, blocking=False)
        elif state == RecordState.COUNTDOWN:
            self._abort_countdown()

    def action_toggle_pause_play(self) -> None:
        self._blink_shortcut("sc-space")
        tab = self._active_tab_id()
        if tab == "tab_studio":
            if self.engine.state == RecordState.RECORDING:
                self.engine.pause()
            elif self.engine.state == RecordState.PAUSED:
                self.engine.resume()
        elif tab == "tab_library":
            self._toggle_playback()

    def action_cancel_record(self) -> None:
        self._blink_shortcut("sc-c")
        if self.engine.state == RecordState.COUNTDOWN:
            self._abort_countdown()
        elif self.engine.state in (RecordState.RECORDING, RecordState.PAUSED):
            if self.engine.cancel():
                self.set_status("Recording discarded", "warning")
                self.notify("Recording discarded.", title="Cancelled", severity="warning")

    def _start_recording_flow(self) -> None:
        if not self.caps.capture_backend or not self.caps.available_formats:
            self.notify("No GStreamer capture or encoder plugins available.", severity="error")
            return

        if self.player.state == PlayerState.PLAYING:
            self.player.pause()

        src_val = str(self.query_one("#select_source", Select).value or RecordSource.MIC.value)
        source = RecordSource(src_val) if src_val in RecordSource._value2member_map_ else RecordSource.MIC

        fmt_val = str(self.query_one("#select_format", Select).value or "")
        if fmt_val not in AudioFormat._value2member_map_:
            self.notify("Select a valid audio codec before recording.", severity="error")
            return
        fmt = AudioFormat(fmt_val)

        mic_node = str(self.query_one("#select_mic_device", Select).value or "")
        sink_node = str(self.query_one("#select_sink_device", Select).value or "")
        channels = int(self.query_one("#select_channels", Select).value or 2)
        sample_rate = int(self.query_one("#select_rate", Select).value or SAMPLE_RATE)
        delay_sec = int(self.query_one("#select_delay", Select).value or 0)
        max_duration = float(self.query_one("#select_duration", Select).value or 0)
        custom_title = self.query_one("#input_title", Input).value.strip()

        output_path = build_recording_path(custom_title or "Memo", fmt)
        ok = self.engine.prepare(
            output_path=output_path,
            source=source,
            format_=fmt,
            channels=channels,
            max_duration=max_duration,
            title=custom_title or output_path.stem,
            custom_source_node=mic_node,
            custom_sink_node=sink_node,
            sample_rate=sample_rate,
        )
        if not ok:
            return

        if delay_sec > 0:
            self.countdown_remaining = delay_sec
            self.engine.state = RecordState.COUNTDOWN
            self._update_ui_state(RecordState.COUNTDOWN)
            self._countdown_timer = self.set_interval(1.0, self._countdown_tick)
        else:
            self.engine.start()

    def _countdown_tick(self) -> None:
        if self._app_shutting_down:
            return
        self.countdown_remaining -= 1
        if self.countdown_remaining <= 0:
            if self._countdown_timer is not None:
                self._countdown_timer.stop()
                self._countdown_timer = None
            self.engine.start()
        else:
            self._update_banner()
            self.set_status(f"Pre-roll delay: {self.countdown_remaining}s", "warning")
            self._update_hud()

    def _abort_countdown(self) -> None:
        if self._countdown_timer is not None:
            self._countdown_timer.stop()
            self._countdown_timer = None
        self.countdown_remaining = 0
        self.engine.cancel()
        self.set_status("Pre-roll cancelled", "warning")

    def _on_engine_level(self, peaks: list[float]) -> None:
        self._safe_dispatch(self._update_meters, peaks)

    def _update_meters(self, peaks: list[float]) -> None:
        if self._app_shutting_down:
            return
        sanitized = [p if math.isfinite(p) else -80.0 for p in peaks]
        self.query_one("#vu_meter", LiveVuMeter).update_levels(sanitized)
        if self.engine.state == RecordState.RECORDING:
            max_peak = max(sanitized) if sanitized else -80.0
            fraction = max(0.0, min(1.0, (max_peak + 60.0) / 60.0)) if max_peak > -80.0 else 0.0
            self.query_one("#oscilloscope", RollingOscilloscope).push_peak(fraction)

    def _on_engine_state_change(self, state: RecordState) -> None:
        self._safe_dispatch(self._update_ui_state, state)

    def _update_ui_state(self, state: RecordState) -> None:
        if self._app_shutting_down or not self.is_mounted:
            return
        with self.batch_update():
            self._update_banner()
            self._sync_config_controls()

            btn_rec = self.query_one("#btn_record", Button)
            btn_pause = self.query_one("#btn_pause", Button)
            btn_cancel = self.query_one("#btn_cancel", Button)

            if state == RecordState.RECORDING:
                btn_rec.disabled = False
                btn_rec.label = "  Stop & Save"
                btn_rec.add_class("-recording")
                btn_pause.disabled = False
                btn_pause.label = "󰏤  Pause"
                btn_cancel.disabled = False
                self.set_status("Recording live via PipeWire...", "error")
            elif state == RecordState.PAUSED:
                btn_rec.disabled = False
                btn_rec.label = "  Stop & Save"
                btn_rec.add_class("-recording")
                btn_pause.disabled = False
                btn_pause.label = "  Resume"
                btn_cancel.disabled = False
                self.set_status("Recording paused", "warning")
            elif state == RecordState.COUNTDOWN:
                btn_rec.disabled = False
                btn_rec.label = "󰅖  Cancel Delay"
                btn_rec.remove_class("-recording")
                btn_pause.disabled = True
                btn_cancel.disabled = False
                self.set_status(f"Pre-roll delay: {self.countdown_remaining}s", "warning")
            elif state == RecordState.FINALIZING:
                btn_rec.disabled = True
                btn_rec.label = "󰆓  Saving..."
                btn_rec.remove_class("-recording")
                btn_pause.disabled = True
                btn_cancel.disabled = True
                self.set_status("Flushing audio headers...", "warning")
            else:  # IDLE
                btn_rec.disabled = not (self.caps.capture_backend and self.caps.available_formats)
                btn_rec.label = "󰐽  Record"
                btn_rec.remove_class("-recording")
                btn_pause.disabled = True
                btn_pause.label = "󰏤  Pause"
                btn_cancel.disabled = True
                self.query_one("#oscilloscope", RollingOscilloscope).clear()
                vu = self.query_one("#vu_meter", LiveVuMeter)
                vu.update_levels([-80.0, -80.0])
                vu.update_time(0.0, 0.0)
                self.set_status("Ready", "info")

        self._sync_poll_timer()
        self._update_hud()

    def _update_banner(self) -> None:
        if self._app_shutting_down or not self.is_mounted:
            return
        banner = self.query_one("#banner_box", NoticeBox)
        state = self.engine.state
        if state == RecordState.RECORDING:
            banner.update_notice("󰑋  RECORDING LIVE • CAPTURE ACTIVE", level="danger")
        elif state == RecordState.PAUSED:
            banner.update_notice("󰏤  RECORDING PAUSED • SPACE TO RESUME", level="warning")
        elif state == RecordState.COUNTDOWN:
            banner.update_notice(f"󰔛  STARTING IN {self.countdown_remaining}s...", level="warning")
        elif state == RecordState.FINALIZING:
            banner.update_notice("󰆓  FINALIZING & FLUSHING AUDIO HEADERS...", level="success")
        else:
            banner.update_notice("󰐽  STANDBY • READY TO CAPTURE", level="info")

    def _clock_tick(self) -> None:
        """Adaptive telemetry tick for active recording or playback."""
        if self._app_shutting_down:
            return
        self.engine.poll()
        self.player.poll()

        rec_state = self.engine.state
        if rec_state in (RecordState.RECORDING, RecordState.PAUSED, RecordState.FINALIZING):
            elapsed = self.engine.elapsed_seconds
            self.query_one("#vu_meter", LiveVuMeter).update_time(elapsed, self.engine.max_duration)
            if (
                rec_state == RecordState.RECORDING
                and self.engine.max_duration > 0.0
                and elapsed >= self.engine.max_duration
            ):
                self.engine.stop(stopped_by_timer=True, blocking=False)

        if self.player.state == PlayerState.PLAYING and self.player.duration_sec > 0.0:
            pos = self.player.get_position()
            frac = pos / self.player.duration_sec
            wf = self.query_one("#player_waveform", PlayerWaveformWidget)
            wf.set_data(wf.peaks, frac)
            self._update_player_title(pos)

        self._update_hud()
        self._sync_poll_timer()

    def _update_hud(self) -> None:
        """Dirty-tracked HUD badge & stats update to avoid idle repaints."""
        if self._app_shutting_down or not self.is_mounted:
            return
        tc = self.theme_colors
        rec_state = self.engine.state

        if rec_state == RecordState.RECORDING:
            sec = int(self.engine.elapsed_seconds)
            badge_key: tuple[Any, ...] = ("rec", sec)
            badge_text = Text(f"● RECORDING  {sec // 60:02d}:{sec % 60:02d}", style=f"bold {tc['error']}")
        elif rec_state == RecordState.PAUSED:
            sec = int(self.engine.elapsed_seconds)
            badge_key = ("rec_pause", sec)
            badge_text = Text(f"󰏤 PAUSED  {sec // 60:02d}:{sec % 60:02d}", style=f"bold {tc['warning']}")
        elif rec_state == RecordState.COUNTDOWN:
            badge_key = ("countdown", self.countdown_remaining)
            badge_text = Text(f"󰔛 COUNTDOWN: {self.countdown_remaining}s", style=f"bold {tc['warning']}")
        elif rec_state == RecordState.FINALIZING:
            badge_key = ("finalizing",)
            badge_text = Text("󰆓 SAVING...", style=f"bold {tc['success']}")
        elif self.player.state == PlayerState.PLAYING and self.selected_memo:
            pos_s = int(self.player.get_position())
            dur_s = int(self.player.duration_sec)
            badge_key = ("play", pos_s, dur_s)
            badge_text = Text(
                f" PLAYING  {pos_s // 60:02d}:{pos_s % 60:02d} / {dur_s // 60:02d}:{dur_s % 60:02d}",
                style=f"bold {tc['success']}",
            )
        elif self.player.state == PlayerState.PAUSED and self.selected_memo:
            pos_s = int(self.player.get_position())
            dur_s = int(self.player.duration_sec)
            badge_key = ("play_pause", pos_s, dur_s)
            badge_text = Text(
                f" PAUSED  {pos_s // 60:02d}:{pos_s % 60:02d} / {dur_s // 60:02d}:{dur_s % 60:02d}",
                style=f"bold {tc['warning']}",
            )
        else:
            badge_key = ("standby",)
            badge_text = Text("● STANDBY", style=f"bold {tc['accent']}")

        if badge_key != self._last_hud_badge:
            self._last_hud_badge = badge_key
            self.query_one("#hud-status-badge", Label).update(badge_text)

        total_bytes = sum(m.size_bytes for m in self.memos)
        stats_key = (len(self.memos), total_bytes)
        if stats_key != self._last_hud_stats:
            self._last_hud_stats = stats_key
            total_kb = total_bytes / 1024.0
            size_str = f"{total_kb / 1024.0:.1f} MB" if total_kb >= 1024.0 else f"{total_kb:.1f} KB"
            self.query_one("#hud-stats", Label).update(
                Text(f"{len(self.memos)} memos • {size_str}", style=f"bold {tc['fg']}")
            )

    def _on_engine_finished(self, path: Path, stopped_by_timer: bool = False) -> None:
        self._safe_dispatch(self._handle_recording_saved, path, stopped_by_timer)

    def _handle_recording_saved(self, path: Path, stopped_by_timer: bool = False) -> None:
        if self._app_shutting_down:
            return
        try:
            size_kb = path.stat().st_size / 1024.0
        except OSError:
            self.notify("Saved recording is no longer accessible.", severity="warning")
            self._refresh_library()
            return

        self.set_status(f"Saved {path.name}", "success")
        if stopped_by_timer:
            self.notify(
                f"Auto-stop limit reached: {path.name} ({size_kb:.1f} KB)",
                title="Timer Limit Complete",
                severity="information",
            )
            self.run_worker(
                send_desktop_notification_async(
                    "Voice Memo Saved",
                    f"Timer limit reached: {path.name}\nSize: {size_kb:.1f} KB",
                    icon_name="audio-input-microphone",
                ),
                group="notify",
            )
        else:
            self.notify(
                f"Saved: {path.name} ({size_kb:.1f} KB)",
                title="Recording Saved",
                severity="information",
                timeout=2.5,
            )
        self._refresh_library()

    def _on_engine_error(self, message: str) -> None:
        self._safe_dispatch(self._handle_engine_error, message)

    def _handle_engine_error(self, message: str) -> None:
        if self._app_shutting_down:
            return
        self.notify(message, title="Recording Error", severity="error")
        self.set_status(message, "error")
        self.query_one("#banner_box", NoticeBox).update_notice(f"󰅖  {message}", level="danger")
        self._refresh_library()

    # -------------------------------------------------------------------------
    # LIBRARY & PLAYBACK LOGIC
    # -------------------------------------------------------------------------

    def _refresh_library(self) -> None:
        """Signal the single coalesced daemon library worker."""
        if self._app_shutting_down:
            return
        self._library_cancel.set()
        self._library_trigger.set()

    def _apply_scanned_library(self, memos: list[VoiceMemo]) -> None:
        if self._app_shutting_down or not self.is_mounted:
            return
        self.memos = memos
        if self.selected_memo is not None:
            match = next((m for m in memos if m.path == self.selected_memo.path), None)
            if match is None:
                self._clear_selection()
            else:
                self.selected_memo = match
        self._render_library()

    def _clear_selection(self) -> None:
        """Reset selected memo, playback state, waveform, and player title."""
        self.player.stop()
        with self._waveform_lock:
            self._waveform_cancel.set()
            self._waveform_request = None
            self._waveform_generation += 1
        self.selected_memo = None
        if not self._app_shutting_down and self.is_mounted:
            self.query_one("#player_waveform", PlayerWaveformWidget).set_data([])
            self.query_one("#lbl_player_title", Label).update("No recording selected")
            self._update_table_playback_badge()
            self._sync_poll_timer()
            self._update_hud()

    def _status_badge_cell(self, memo_path: Path) -> Text:
        tc = self.theme_colors
        if self.selected_memo and memo_path == self.selected_memo.path:
            if self.player.state == PlayerState.PLAYING:
                return Text("  PLAY ", style=f"bold {tc['bg']} on {tc['success']}")
            if self.player.state == PlayerState.PAUSED:
                return Text("  PAUS ", style=f"bold {tc['bg']} on {tc['warning']}")
        return Text(" 󰍬 MEMO ", style=f"dim {tc['muted']}")

    def _update_table_playback_badge(self) -> None:
        """Update only affected Status cells in DataTable without rebuilding rows."""
        if self._app_shutting_down or not self.is_mounted:
            return
        table = self.query_one("#library_table", DataTable)
        new_active_key = (
            str(self.selected_memo.path)
            if self.selected_memo and self.player.state != PlayerState.STOPPED
            else None
        )
        tc = self.theme_colors
        if self._active_badge_row_key and self._active_badge_row_key != new_active_key:
            try:
                table.update_cell(
                    self._active_badge_row_key,
                    "status",
                    Text(" 󰍬 MEMO ", style=f"dim {tc['muted']}"),
                )
            except KeyError:
                pass
        if new_active_key and self.selected_memo is not None:
            try:
                table.update_cell(
                    new_active_key,
                    "status",
                    self._status_badge_cell(self.selected_memo.path),
                )
            except KeyError:
                pass
        self._active_badge_row_key = new_active_key

    def _render_library(self) -> None:
        if self._app_shutting_down or not self.is_mounted:
            return
        table = self.query_one("#library_table", DataTable)
        search_query = self.query_one("#search_input", Input).value.lower().strip()
        filtered = [m for m in self.memos if not search_query or search_query in m.name.lower()]

        tc = self.theme_colors
        with self.prevent(DataTable.RowHighlighted):
            table.clear(columns=False)
            for m in filtered:
                fmt_color = tc["accent"]
                if m.format_name == "FLAC":
                    fmt_color = tc["warning"]
                elif m.format_name == "WAV":
                    fmt_color = tc["success"]

                table.add_row(
                    self._status_badge_cell(m.path),
                    Text(m.name, style=f"bold {tc['fg']}"),
                    Text(f" {m.format_name} ", style=f"bold {tc['bg']} on {fmt_color}"),
                    Text(m.formatted_duration, style=tc["warning"]),
                    Text(m.formatted_size, style=f"dim {tc['fg']}"),
                    Text(m.formatted_date, style=f"dim {tc['muted']}"),
                    key=str(m.path),
                )

            if self.selected_memo is not None:
                row_idx = next((i for i, m in enumerate(filtered) if m.path == self.selected_memo.path), None)
                if row_idx is not None:
                    table.move_cursor(row=row_idx, scroll=False)
                else:
                    self._clear_selection()

        self.query_one("#lbl_memo_count", Label).update(f" {len(filtered)} memos ")

        if filtered and self.selected_memo is None:
            self._select_memo(filtered[0])
        elif not filtered and self.selected_memo is not None:
            self._clear_selection()

        self._active_badge_row_key = (
            str(self.selected_memo.path)
            if self.selected_memo and self.player.state != PlayerState.STOPPED
            else None
        )
        self._update_hud()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search_input":
            self._render_library()
        elif event.input.id == "input_title":
            self._update_target_preview()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row_key = event.row_key.value
        for m in self.memos:
            if str(m.path) == row_key:
                self._select_memo(m)
                self._toggle_playback()
                break

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if not event.row_key:
            return
        row_key = event.row_key.value
        for m in self.memos:
            if str(m.path) == row_key:
                if self.selected_memo and self.selected_memo.path == m.path:
                    break
                self._select_memo(m)
                break

    def _select_memo(self, memo: VoiceMemo) -> None:
        if (
            self.selected_memo is not None
            and (self.selected_memo.path, self.selected_memo.size_bytes, self.selected_memo.mtime_ns)
            == (memo.path, memo.size_bytes, memo.mtime_ns)
        ):
            self.selected_memo = memo
            return

        self.selected_memo = memo
        self.player.load(memo.path, memo.duration_sec)
        self.query_one("#player_waveform", PlayerWaveformWidget).set_data([], 0.0, loading=True)
        self._update_player_title(0.0)
        self._update_table_playback_badge()
        self._sync_poll_timer()

        with self._waveform_lock:
            self._waveform_generation += 1
            gen = self._waveform_generation
            self._waveform_request = (memo.path, memo.duration_sec, gen)
            self._waveform_cancel.set()
        self._waveform_trigger.set()

    def _apply_waveform_peaks(self, path: Path, peaks: list[float], generation: int) -> None:
        if (
            self._app_shutting_down
            or generation != self._waveform_generation
            or self.selected_memo is None
            or self.selected_memo.path != path
        ):
            return
        wf = self.query_one("#player_waveform", PlayerWaveformWidget)
        frac = self.player.get_position() / self.player.duration_sec if self.player.duration_sec > 0.0 else 0.0
        wf.set_data(peaks, frac, loading=False)

    def _update_player_title(self, current_pos: float) -> None:
        if self._app_shutting_down or not self.selected_memo or not self.is_mounted:
            return
        dur = self.player.duration_sec if self.player.duration_sec > 0.0 else self.selected_memo.duration_sec
        pos_str = f"{int(current_pos) // 60:02d}:{int(current_pos) % 60:02d}"
        dur_str = self.selected_memo.formatted_duration
        remaining = max(0.0, dur - current_pos)
        rem_str = f"-{int(remaining) // 60:02d}:{int(remaining) % 60:02d}"
        pct = int((current_pos / dur) * 100) if dur > 0.0 else 0
        tc = self.theme_colors
        self.query_one("#lbl_player_title", Label).update(
            Text.from_markup(
                f"󰎆 [bold {tc['fg']}]{escape(self.selected_memo.name)}[/]   "
                f"[bold {tc['bg']} on {tc['accent']}] {self.selected_memo.format_name} [/]   "
                f"[{tc['success']}]{pos_str}[/] / [{tc['muted']}]{dur_str}[/] [dim]({rem_str})[/]   "
                f"[bold {tc['accent']}][ {pct:2d}% ][/]   "
                f"[dim]• {self.selected_memo.formatted_size}[/]"
            )
        )

    def _toggle_playback(self) -> None:
        if not self.selected_memo:
            return
        if self.engine.state != RecordState.IDLE:
            self.notify("Cannot play audio while recording is active.", severity="warning")
            return
        if self.player.state == PlayerState.PLAYING:
            if not self.player.pause():
                self.notify("Could not pause playback.", severity="error")
                return
            self.set_status(f"Paused {self.selected_memo.name}", "warning")
        else:
            if not self.player.play():
                self.notify("Could not start playback.", severity="error")
                return
            self.set_status(f"Playing {self.selected_memo.name}", "success")
        self._sync_poll_timer()
        self._update_hud()

    def on_player_waveform_widget_seek_requested(
        self,
        event: PlayerWaveformWidget.SeekRequested,
    ) -> None:
        if not self.selected_memo or self.player.duration_sec <= 0.0:
            return
        if self.engine.state != RecordState.IDLE:
            return
        target = event.fraction * self.player.duration_sec
        self.player.seek_to(target, auto_play_if_stopped=False)
        self._sync_poll_timer()

    def _on_player_position_changed(self, pos: float) -> None:
        self._safe_dispatch(self._render_seek_position, pos)

    def _render_seek_position(self, pos: float) -> None:
        if self._app_shutting_down or not self.selected_memo or not self.is_mounted:
            return
        frac = pos / self.player.duration_sec if self.player.duration_sec > 0.0 else 0.0
        wf = self.query_one("#player_waveform", PlayerWaveformWidget)
        wf.set_data(wf.peaks, frac)
        self._update_player_title(pos)
        self._update_hud()

    def action_seek_fwd(self) -> None:
        if not self.selected_memo or self.engine.state != RecordState.IDLE:
            return
        self.player.seek_relative(5.0)
        self._sync_poll_timer()

    def action_seek_back(self) -> None:
        if not self.selected_memo or self.engine.state != RecordState.IDLE:
            return
        self.player.seek_relative(-5.0)
        self._sync_poll_timer()

    def action_stop_player(self) -> None:
        self.player.stop()
        if not self._app_shutting_down and self.is_mounted:
            wf = self.query_one("#player_waveform", PlayerWaveformWidget)
            wf.set_data(wf.peaks, 0.0)
            self._update_player_title(0.0)
            self.set_status("Playback stopped", "info")
            self._update_table_playback_badge()
            self._sync_poll_timer()
            self._update_hud()

    def action_copy_path(self) -> None:
        if not self.selected_memo:
            return
        self._blink_shortcut("sc-y")
        path_str = str(self.selected_memo.path.resolve())

        async def _copy() -> None:
            ok = await copy_to_wayland_clipboard_async(path_str)
            if self._app_shutting_down:
                return
            if ok:
                self.notify(f"Copied path to Wayland clipboard:\n{path_str}", title="Clipboard")
                self.set_status("Copied memo path to clipboard", "success")
            else:
                self.notify("Failed to invoke wl-copy.", title="Clipboard Error", severity="error")

        self.run_worker(_copy(), group="clipboard")

    def action_copy_recordings_dir(self) -> None:
        path_str = str(RECORDINGS_DIR.resolve())

        async def _copy() -> None:
            ok = await copy_to_wayland_clipboard_async(path_str)
            if self._app_shutting_down:
                return
            if ok:
                self.notify(f"Copied recordings directory:\n{path_str}", title="Clipboard")
                self.set_status("Copied recordings directory to clipboard", "success")
            else:
                self.notify("Failed to invoke wl-copy.", title="Clipboard Error", severity="error")

        self.run_worker(_copy(), group="clipboard")

    def action_rename_selected(self) -> None:
        if not self.selected_memo:
            return
        self._blink_shortcut("sc-e")
        memo = self.selected_memo

        def _on_rename_done(new_stem: str | None) -> None:
            if not new_stem or self._app_shutting_down:
                return
            self.player.stop()
            updated = VoiceLibrary.rename_memo(memo, new_stem)
            if updated is None:
                self.notify("Rename failed (invalid or duplicate name).", severity="error")
                return
            self.memos = [updated if m.path == memo.path else m for m in self.memos]
            self.selected_memo = None
            self._select_memo(updated)
            self._render_library()
            self.notify(f"Renamed to: {updated.name}", title="Success")
            self.set_status(f"Renamed to {updated.name}", "success")

        self.push_screen(RenameMemoModal(memo.stem), _on_rename_done)

    def action_delete_selected(self) -> None:
        if not self.selected_memo:
            return
        self._blink_shortcut("sc-d")
        memo = self.selected_memo

        def _on_delete_done(confirmed: bool) -> None:
            if not confirmed or self._app_shutting_down:
                return
            self._clear_selection()
            if not VoiceLibrary.delete_memo(memo):
                self.notify("Could not delete memo.", severity="error")
                return
            self.memos = [m for m in self.memos if m.path != memo.path]
            self._render_library()
            self.notify(f"Deleted: {memo.name}", title="Deleted", severity="warning")
            self.set_status(f"Deleted {memo.name}", "warning")

        self.push_screen(DeleteMemoModal(memo), _on_delete_done)

    def _on_player_state_change(self, state: PlayerState) -> None:
        self._safe_dispatch(self._handle_player_state_change, state)

    def _handle_player_state_change(self, state: PlayerState) -> None:
        if self._app_shutting_down or not self.is_mounted:
            return
        btn = self.query_one("#btn_player_play", Button)
        btn.label = "  Pause" if state == PlayerState.PLAYING else "  Play"
        self._update_table_playback_badge()
        self._sync_poll_timer()
        self._update_hud()

    def _on_player_eos(self) -> None:
        self._safe_dispatch(self._handle_player_eos)

    def _handle_player_eos(self) -> None:
        if self._app_shutting_down or not self.is_mounted:
            return
        # player.poll() already stopped the pipeline; only reset UI state once
        wf = self.query_one("#player_waveform", PlayerWaveformWidget)
        wf.set_data(wf.peaks, 0.0)
        self._update_player_title(0.0)
        self.set_status("Playback finished", "info")
        self._update_table_playback_badge()
        self._sync_poll_timer()
        self._update_hud()

    def _on_player_error(self, message: str) -> None:
        self._safe_dispatch(self._handle_player_error, message)

    def _handle_player_error(self, message: str) -> None:
        if self._app_shutting_down:
            return
        self.notify(message, title="Playback Error", severity="error")
        self.set_status(message, "error")
        self._update_table_playback_badge()
        self._sync_poll_timer()
        self._update_hud()

    # -------------------------------------------------------------------------
    # ASYNC PIPEWIRE DEVICE DISCOVERY & DEVICES PANE
    # -------------------------------------------------------------------------

    def _refresh_devices_async(self) -> None:
        if self._app_shutting_down or self._devices_refreshing:
            return
        self._devices_refreshing = True

        async def _discover() -> None:
            try:
                sources, sinks = await discover_pipewire_nodes_async()
                if not self._app_shutting_down:
                    self.pw_sources = sources
                    self.pw_sinks = sinks
                    self._sync_device_selectors()
                    self._render_devices_view()
            finally:
                self._devices_refreshing = False

        self.run_worker(_discover(), group="devices")

    def _sync_device_selectors(self) -> None:
        if self._app_shutting_down or not self.is_mounted:
            return
        with self.prevent(Select.Changed):
            def _apply(select_id: str, nodes: list[PipeWireNode]) -> None:
                sel = self.query_one(select_id, Select)
                prev = str(sel.value or "")
                options: list[tuple[str, str]] = [("System Default", "")]
                valid_values = {""}
                for node in nodes:
                    tag = "● " if node.is_default else ""
                    options.append((f"{tag}{node.description}", node.name))
                    valid_values.add(node.name)
                sel.set_options(options)
                sel.value = prev if prev in valid_values else ""

            _apply("#select_mic_device", self.pw_sources)
            _apply("#select_sink_device", self.pw_sinks)

    def _render_devices_view(self) -> None:
        if self._app_shutting_down or not self.is_mounted:
            return
        tc = self.theme_colors

        table_in = Table(
            title="󰍬  PIPEWIRE INPUT CAPTURE NODES (SOURCES & DUPLEX)",
            border_style=tc["muted"],
            title_justify="left",
            expand=True,
        )
        table_in.add_column("ID", justify="right", style="bold", width=5)
        table_in.add_column("Default", justify="center", width=11)
        table_in.add_column("Description", style=f"bold {tc['fg']}", ratio=2)
        table_in.add_column("Class", style=tc["accent"], width=20)
        table_in.add_column("Node Name", style=f"dim {tc['muted']}", ratio=2)

        if not self.pw_sources:
            table_in.add_row("-", "", "No input sources detected", "", "")
        for src in self.pw_sources:
            def_str = "● DEFAULT" if src.is_default else ""
            table_in.add_row(str(src.id), def_str, src.description, src.media_class, src.name)

        table_out = Table(
            title="󰋋  PIPEWIRE OUTPUT PLAYBACK NODES (SINKS & MONITORS)",
            border_style=tc["muted"],
            title_justify="left",
            expand=True,
        )
        table_out.add_column("ID", justify="right", style="bold", width=5)
        table_out.add_column("Default", justify="center", width=11)
        table_out.add_column("Description", style=f"bold {tc['fg']}", ratio=2)
        table_out.add_column("Class", style=tc["accent"], width=20)
        table_out.add_column("Node Name", style=f"dim {tc['muted']}", ratio=2)

        if not self.pw_sinks:
            table_out.add_row("-", "", "No output sinks detected", "", "")
        for snk in self.pw_sinks:
            def_str = "● DEFAULT" if snk.is_default else ""
            table_out.add_row(str(snk.id), def_str, snk.description, snk.media_class, snk.name)

        codecs_str = ", ".join(f.value.upper() for f in self.caps.available_formats) or "None"
        sink_str = "pipewiresink (native)" if self.caps.has_pipewiresink else "autoaudiosink"
        topo_panel = Panel(
            Text(
                f"Capture Backend : {self.caps.backend_label} ({self.caps.capture_backend or 'missing'})\n"
                f"Playback Engine : {self.caps.playbin_element or 'missing'} -> {sink_str}\n"
                f"Live Mixing     : audiomixer (60 ms latency, force-live, leaky branch queues)\n"
                f"Pause Strategy  : core valve + GstAudioEncoder tolerance (zero-gap resume)\n"
                f"Available Codecs: {codecs_str}\n"
                f"Storage Path    : {RECORDINGS_DIR}\n"
                f"Cache Directory : {CACHE_DIR}"
            ),
            title=f"[bold {tc['accent']}]󰒓  RUNTIME AUDIO TOPOLOGY[/]",
            title_align="left",
            border_style=tc["muted"],
        )

        self.query_one("#devices_info", Static).update(
            Group(table_in, Text(""), table_out, Text(""), topo_panel)
        )

    # -------------------------------------------------------------------------
    # BUTTON DISPATCHER & FAIL-SAFE SHUTDOWN STATE MACHINE
    # -------------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn_record":
            self.action_toggle_record()
        elif btn_id == "btn_pause":
            self.action_toggle_pause_play()
        elif btn_id == "btn_cancel":
            self.action_cancel_record()
        elif btn_id == "btn_player_play":
            self._toggle_playback()
        elif btn_id == "btn_player_bwd":
            self.action_seek_back()
        elif btn_id == "btn_player_fwd":
            self.action_seek_fwd()
        elif btn_id == "btn_player_stop":
            self.action_stop_player()
        elif btn_id == "btn_player_copy":
            self.action_copy_path()
        elif btn_id == "btn_player_rename":
            self.action_rename_selected()
        elif btn_id == "btn_player_delete":
            self.action_delete_selected()
        elif btn_id == "btn_refresh_lib":
            self._refresh_library()
        elif btn_id == "btn_refresh_devices":
            self._refresh_devices_async()

    def _initiate_shutdown(self, pty_closed: bool = False) -> None:
        """Idempotent shutdown entry point for q, Ctrl+C, Ctrl+Q, SIGINT, SIGTERM, and SIGHUP."""
        if self._app_shutting_down:
            return
        recording_active = self.engine.state in (
            RecordState.RECORDING,
            RecordState.PAUSED,
            RecordState.FINALIZING,
        )
        if not self.is_headless:
            if pty_closed:
                _silence_dead_stdio()
            # Arm kernel-level SIGALRM watchdog so C-level/PTY deadlocks can never linger
            arm_exit_watchdog(4 if recording_active else 2)

            # Restore default POSIX signal handlers so a repeated signal exits immediately
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                try:
                    asyncio.get_running_loop().remove_signal_handler(sig)
                except (NotImplementedError, RuntimeError, ValueError):
                    pass
                try:
                    signal.signal(sig, signal.SIG_DFL)
                except (OSError, ValueError):
                    pass

        self.force_cleanup(eos_timeout_sec=1.0 if pty_closed else 1.5)
        self.exit()

    def force_cleanup(self, *, eos_timeout_sec: float = 1.5) -> None:
        """Idempotent bounded release of timers, callbacks, GStreamer pipelines, and daemon workers."""
        if self._cleaned_up:
            return
        self._cleaned_up = True
        self._app_shutting_down = True

        # 1. Detach all callbacks so teardown never schedules UI mutations or rescans
        self.engine.on_level_callback = None
        self.engine.on_state_change = None
        self.engine.on_finished = None
        self.engine.on_error = None
        self.player.on_state_change = None
        self.player.on_eos = None
        self.player.on_error = None
        self.player.on_position_changed = None

        # 2. Stop all Textual timers
        for timer_attr in ("_countdown_timer", "_poll_timer", "_theme_timer"):
            timer = getattr(self, timer_attr, None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    pass
                setattr(self, timer_attr, None)

        # 3. Signal all daemon workers to abort immediately
        self._shutdown_event.set()
        self._library_cancel.set()
        self._library_trigger.set()
        self._waveform_cancel.set()
        self._waveform_trigger.set()

        # 4. Release GStreamer playback and recording pipelines with bounded waits
        self.player.shutdown()
        self.engine.shutdown(eos_timeout_sec=eos_timeout_sec)

        # 5. Bounded join on daemon workers so GStreamer C cleanup finishes before Py_FinalizeEx
        for worker in (self._waveform_thread, self._library_thread):
            if worker is not None and worker.is_alive():
                worker.join(timeout=0.25)

    def on_unmount(self) -> None:
        self.force_cleanup()

    def action_quit_app(self) -> None:
        self._initiate_shutdown(pty_closed=False)


# =============================================================================
# MAIN ENTRYPOINT
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Dusky Voice Memos — Wayland & PipeWire native studio")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    parser.parse_args()

    Gst.init(None)
    ensure_storage_environment()
    caps = probe_runtime_capabilities()
    app = DuskyVoiceApp(caps)

    try:
        app.run()
    finally:
        # Arm tight kernel alarm during final teardown + Py_FinalizeEx to prevent lingering processes
        arm_exit_watchdog(2)
        app.force_cleanup(eos_timeout_sec=1.0)
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except OSError:
            _silence_dead_stdio()
            for stream in (sys.stdout, sys.stderr):
                try:
                    stream.flush()
                except OSError:
                    pass


if __name__ == "__main__":
    main()
