#!/usr/bin/env python3
"""
===============================================================================
DUSKY VOICE MEMOS — Wayland & PipeWire Audio Studio & Memo Manager
===============================================================================
Target Platform: Wayland Only | PipeWire 1.6+ | GStreamer 1.28+ | Python 3.15+
Arch Linux / Hyprland Native Architecture (Zero X11 / Zero legacy shims)

Design System: DuskyTUI (Integrated Matugen Palette, FlowContainer Shortcuts,
NoticeBox Status Banners, Papirus Desktop Icons, Zero Emojis).

Inspired by the best features of:
  - Reco: Multi-source capture (Mic, Desktop Audio monitor, or Simultaneous Both
          mixed via GStreamer audiomixer), live level metering, format encoding
          (Opus, FLAC, WAV, MP3, OGG), countdown pre-roll and auto-stop timers.
  - Vocalis: Voice memo library management, cached audio peak waveform extraction,
             interactive scrub/playback player, inline renaming, deletion,
             and Wayland desktop integration with Papirus icons.
  - DuskyTUI: Matugen dynamic theme synchronizer, unified frame, AppFooter shortcuts,
              NoticeBox banners, and keyboard-driven ergonomics.
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
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
import threading
import time
from typing import Any, Callable, Final

# Python 3.15 explicit lazy imports (PEP 810)
lazy import hashlib
lazy import json
lazy import math
lazy import os
lazy import re
lazy import shutil
lazy import subprocess
lazy import tempfile

# GStreamer PyGObject initialization
import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstPbutils", "1.0")
from gi.repository import Gst, GstPbutils, GLib

Gst.init(None)

# Textual 8.2+ TUI Framework & Rich
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.markup import escape

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.geometry import Offset, Region, Size, Spacing
from textual.layout import Layout, WidgetPlacement
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
# CONSTANTS & PATH DEFINITIONS
# =============================================================================

APP_NAME: Final[str] = "Dusky Voice Memos"
VERSION: Final[str] = "1.2.0"

HOME_DIR: Final[Path] = Path.home()
CONFIG_DIR: Final[Path] = (
    Path(os.environ.get("XDG_CONFIG_HOME") or HOME_DIR / ".config")
    / "dusky"
    / "settings"
    / "dusky_voice"
)
RECORDINGS_DIR: Final[Path] = CONFIG_DIR
CACHE_DIR: Final[Path] = RECORDINGS_DIR / ".waveform_cache"
THEME_FILE: Final[Path] = CONFIG_DIR.parents[2] / "matugen" / "generated" / "dusky_tui.json"

# Audio constants
SAMPLE_RATE: Final[int] = 48000
LEVEL_INTERVAL_NS: Final[int] = 50_000_000  # 50ms = 20 fps level telemetry
MIXER_LATENCY_NS: Final[int] = 1_000_000_000  # 1s buffer prevents audiomixer dropouts

UNICODE_BLOCKS: Final[list[str]] = [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]

# System Papirus Icons Search Paths
PAPIRUS_BASE_PATHS: Final[list[Path]] = [
    Path("/usr/share/icons/Papirus/48x48"),
    Path("/usr/share/icons/Papirus/32x32"),
    Path("/usr/share/icons/Papirus/24x24"),
    Path("/usr/share/icons/Papirus-Dark/48x48"),
    Path("/usr/share/icons/Papirus-Dark/32x32"),
]

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


# =============================================================================
# DATA MODELS & ENUMS
# =============================================================================

class RecordSource(StrEnum):
    MIC = "mic"
    SYSTEM = "system"
    BOTH = "both"

    @property
    def label(self) -> str:
        return {
            RecordSource.MIC: "Microphone Only",
            RecordSource.SYSTEM: "Desktop Audio (Monitor)",
            RecordSource.BOTH: "Both (Mic + Desktop Mixed)",
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
            AudioFormat.OPUS: "Opus (Voice Optimized)",
            AudioFormat.FLAC: "FLAC (Lossless Master)",
            AudioFormat.WAV: "WAV (Uncompressed PCM)",
            AudioFormat.MP3: "MP3 (High Quality VBR)",
            AudioFormat.OGG: "OGG Vorbis",
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
    duration_sec: float
    created_at: datetime
    format_name: str
    peaks: list[float] | None = None

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
        elif self.size_bytes < 1024 * 1024:
            return f"{self.size_bytes / 1024:.1f} KB"
        else:
            return f"{self.size_bytes / (1024 * 1024):.1f} MB"

    @property
    def formatted_date(self) -> str:
        return self.created_at.strftime("%Y-%m-%d %H:%M")


# =============================================================================
# ENVIRONMENT, THEME & PAPIRUS ICON RESOLUTION
# =============================================================================

def ensure_storage_environment() -> None:
    """Ensure recording and waveform cache directories exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def check_wayland() -> bool:
    """Verify active Wayland environment."""
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("WAYLAND_SOCKET"))


def load_matugen_theme() -> dict[str, str]:
    """Load user's system colors generated by Matugen."""
    if THEME_FILE.is_file():
        try:
            with open(THEME_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {key: value if isinstance(value := data.get(key), str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value) else default for key, default in DEFAULT_THEME.items()}
        except (OSError, ValueError):
            pass
    return DEFAULT_THEME.copy()


def find_papirus_icon(name: str) -> str:
    """Resolve absolute path to downloaded Papirus SVG icon or fallback to theme icon name."""
    for base in PAPIRUS_BASE_PATHS:
        for cat in ["devices", "actions", "status", "places", "apps", "categories"]:
            cand = base / cat / f"{name}.svg"
            if cand.is_file():
                return str(cand)
    return name


def copy_to_wayland_clipboard(text: str) -> bool:
    """Copy text or path to Wayland clipboard using wl-copy."""
    if not shutil.which("wl-copy"):
        return False
    try:
        subprocess.run(
            ["wl-copy", "--type", "text/plain", "--", text],
            check=True,
            timeout=2.0,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def send_desktop_notification(
    title: str,
    message: str,
    icon_name: str = "audio-input-microphone",
) -> None:
    """Send native desktop notification via notify-send using Papirus icon."""
    if not shutil.which("notify-send"):
        return
    icon_path = find_papirus_icon(icon_name)
    try:
        subprocess.Popen(
            [
                "notify-send",
                "-a", APP_NAME,
                "-i", icon_path,
                "-u", "normal",
                title,
                message,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def discover_pipewire_nodes() -> tuple[list[PipeWireNode], list[PipeWireNode]]:
    """Enumerate hardware audio sources and sinks via pw-dump."""
    sources: list[PipeWireNode] = []
    sinks: list[PipeWireNode] = []

    if not shutil.which("pw-dump"):
        return sources, sinks

    try:
        proc = subprocess.run(
            ["pw-dump", "--no-colors"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=True,
        )
        objects = json.loads(proc.stdout)
    except (subprocess.SubprocessError, OSError, ValueError):
        return sources, sinks

    default_source_name = ""
    default_sink_name = ""

    # Locate default metadata
    for obj in objects:
        if obj.get("props", {}).get("metadata.name") == "default":
            for item in obj.get("metadata", []):
                if item.get("subject") == 0:
                    key = item.get("key")
                    val = item.get("value")
                    name = val.get("name") if isinstance(val, dict) else val
                    if isinstance(name, str):
                        if key == "default.audio.source":
                            default_source_name = name
                        elif key == "default.audio.sink":
                            default_sink_name = name

    # Parse audio nodes
    for obj in objects:
        props = obj.get("info", {}).get("props", {})
        media_class = props.get("media.class", "")
        node_name = props.get("node.name", "")
        desc = props.get("node.description", node_name)
        node_id = obj.get("id", 0)

        if not isinstance(node_name, str) or not node_name:
            continue
        if node_name.endswith(".monitor"):
            continue

        if media_class == "Audio/Source":
            sources.append(
                PipeWireNode(
                    id=node_id,
                    name=node_name,
                    description=desc,
                    media_class=media_class,
                    is_default=(node_name == default_source_name),
                )
            )
        elif media_class == "Audio/Sink":
            sinks.append(
                PipeWireNode(
                    id=node_id,
                    name=node_name,
                    description=desc,
                    media_class=media_class,
                    is_default=(node_name == default_sink_name),
                )
            )

    return sources, sinks


# =============================================================================
# WAVEFORM GENERATOR & CACHING ENGINE
# =============================================================================

def clean_memo_stem(value: str) -> str:
    """Leave room for timestamps and extensions under Linux's filename byte limit."""
    value = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "", value).strip(" .")
    return value.encode("utf-8")[:180].decode("utf-8", errors="ignore").rstrip(" .")


def compute_waveform_peaks(file_path: Path, num_points: int = 120, cancelled: threading.Event | None = None) -> list[float]:
    """
    Extract or load audio amplitude peaks for a recording.
    Decode without clock synchronization; cache only complete waveforms.
    """
    if num_points <= 0 or (cancelled and cancelled.is_set()):
        return []
    try:
        stat = file_path.stat()
    except OSError:
        return []
    cache_key = hashlib.sha256(str(file_path.resolve()).encode()).hexdigest()
    cache_file = CACHE_DIR / f"{cache_key}_{stat.st_size}_{stat.st_mtime_ns}_{num_points}.json"
    if cache_file.is_file():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and len(data) == num_points and all(isinstance(x, (int, float)) and math.isfinite(x) and 0 <= x <= 1 for x in data):
                    return [float(x) for x in data]
        except (OSError, ValueError):
            pass

    pipe_str = (
        "uridecodebin name=decoder ! "
        f"audioconvert ! audio/x-raw,channels=1 ! "
        f"level interval={LEVEL_INTERVAL_NS} ! fakesink sync=false qos=false"
    )

    try:
        pipeline = Gst.parse_launch(pipe_str)
    except GLib.Error:
        return []

    try:
        pipeline.get_by_name("decoder").set_property("uri", file_path.resolve().as_uri())
        bus = pipeline.get_bus()
        pipeline.set_state(Gst.State.PLAYING)

        raw_peaks: list[float] = []
        complete = False
        deadline = time.monotonic() + 30.0

        while time.monotonic() < deadline and not (cancelled and cancelled.is_set()):
            msg = bus.timed_pop_filtered(
                100 * Gst.MSECOND,
                Gst.MessageType.ELEMENT | Gst.MessageType.EOS | Gst.MessageType.ERROR,
            )
            if not msg:
                continue
            if msg.type == Gst.MessageType.ELEMENT:
                s = msg.get_structure()
                if s and s.has_name("level"):
                    db = s.get_value("peak")[0]
                    frac = max(0.0, min(1.0, (float(db) - (-60.0)) / 60.0)) if db > -80.0 else 0.0
                    raw_peaks.append(frac)
            elif msg.type in (Gst.MessageType.EOS, Gst.MessageType.ERROR):
                complete = msg.type == Gst.MessageType.EOS
                break

    finally:
        pipeline.set_state(Gst.State.NULL)

    if not complete or not raw_peaks or (cancelled and cancelled.is_set()):
        return []

    if len(raw_peaks) == num_points:
        resampled = raw_peaks
    else:
        resampled = []
        for i in range(num_points):
            start = i * len(raw_peaks) // num_points
            end = max(start + 1, (i + 1) * len(raw_peaks) // num_points)
            resampled.append(max(raw_peaks[start:end]))

    temporary: Path | None = None
    try:
        current = file_path.stat()
        if (current.st_size, current.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
            return []
        # Readers must see either a complete old cache or a complete new one.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=CACHE_DIR, delete=False) as cache:
            temporary = Path(cache.name)
            json.dump(resampled, cache)
        temporary.replace(cache_file)
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
# DUSKY AUDIO RECORDING ENGINE (PIPEWIRE-PULSE)
# =============================================================================

class DuskyAudioEngine:
    """High-performance GStreamer PipeWire recording engine."""

    def __init__(self) -> None:
        self.state: RecordState = RecordState.IDLE
        self.pipeline: Gst.Pipeline | None = None
        self.output_path: Path | None = None
        self.start_time: float = 0.0
        self.pause_start: float = 0.0
        self.paused_duration: float = 0.0
        self.max_duration: float = 0.0
        self.stopped_by_timer: bool = False

        # Telemetry callbacks
        self.on_level_callback: Callable[[list[float]], None] | None = None
        self.on_state_change: Callable[[RecordState], None] | None = None
        self.on_finished: Callable[[Path, bool], None] | None = None
        self.on_error: Callable[[str], None] | None = None

    @property
    def elapsed_seconds(self) -> float:
        if self.state in (RecordState.IDLE, RecordState.COUNTDOWN):
            return 0.0
        if self.state == RecordState.PAUSED:
            return max(0.0, self.pause_start - self.start_time - self.paused_duration)
        return max(0.0, time.monotonic() - self.start_time - self.paused_duration)

    def build_pipeline_string(
        self,
        output_path: Path,
        source: RecordSource,
        format_: AudioFormat,
        channels: int,
        custom_source_node: str = "",
        custom_sink_node: str = "",
        sample_rate: int = SAMPLE_RATE,
    ) -> str:
        """Construct the optimized GStreamer pipeline for PipeWire."""
        encoder_str = {
            AudioFormat.OPUS: "opusenc bitrate=128000 ! oggmux",
            AudioFormat.FLAC: "flacenc",
            AudioFormat.WAV: "wavenc",
            AudioFormat.MP3: "lamemp3enc target=quality quality=2 ! id3v2mux",
            AudioFormat.OGG: "vorbisenc quality=0.6 ! oggmux",
        }[format_]

        if format_ == AudioFormat.OPUS:
            sample_rate = 48000  # Opus does not accept 44.1 kHz PCM.

        if source == RecordSource.MIC:
            return (
                "pulsesrc name=mic ! "
                f"audioconvert ! audioresample ! "
                f"audio/x-raw,channels={channels},rate={sample_rate} ! "
                f"level interval={LEVEL_INTERVAL_NS} name=lvl ! "
                f"{encoder_str} ! filesink name=output"
            )
        elif source == RecordSource.SYSTEM:
            return (
                "pulsesrc name=desktop ! "
                f"audioconvert ! audioresample ! "
                f"audio/x-raw,channels={channels},rate={sample_rate} ! "
                f"level interval={LEVEL_INTERVAL_NS} name=lvl ! "
                f"{encoder_str} ! filesink name=output"
            )
        else:  # RecordSource.BOTH
            return (
                f"audiomixer name=mix latency={MIXER_LATENCY_NS} ! "
                f"audioconvert ! audioresample ! "
                f"audio/x-raw,channels={channels},rate={sample_rate} ! "
                f"level interval={LEVEL_INTERVAL_NS} name=lvl ! "
                f"{encoder_str} ! filesink name=output "
                "pulsesrc name=mic ! queue ! audioconvert ! audioresample ! mix.sink_0 "
                "pulsesrc name=desktop ! queue ! audioconvert ! audioresample ! mix.sink_1"
            )

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
        """Prepare the pipeline; the TUI polls its bus."""
        if self.state != RecordState.IDLE or self.pipeline is not None:
            return False

        self.output_path = output_path
        self.max_duration = max_duration
        self.paused_duration = 0.0
        self.stopped_by_timer = False

        pipe_str = self.build_pipeline_string(
            output_path, source, format_, channels, custom_source_node, custom_sink_node, sample_rate
        )

        try:
            self.pipeline = Gst.parse_launch(pipe_str)
            self.pipeline.get_by_name("output").set_property("location", str(output_path.resolve()))
            mic = self.pipeline.get_by_name("mic")
            desktop = self.pipeline.get_by_name("desktop")
            if mic is not None:
                mic.set_property("device", custom_source_node or "@DEFAULT_SOURCE@")
            if desktop is not None:
                desktop.set_property("device", f"{custom_sink_node}.monitor" if custom_sink_node else "@DEFAULT_MONITOR@")
        except GLib.Error as e:
            if self.on_error:
                self.on_error(f"GStreamer pipeline initialization failed: {e.message}")
            return False

        tag_list = Gst.TagList.new_empty()
        tag_list.add_value(Gst.TagMergeMode.REPLACE, Gst.TAG_APPLICATION_NAME, APP_NAME)
        tag_list.add_value(Gst.TagMergeMode.REPLACE, Gst.TAG_ARTIST, "Dusky Voice")
        tag_list.add_value(
            Gst.TagMergeMode.REPLACE,
            Gst.TAG_TITLE,
            title or output_path.stem,
        )
        recorded_at = Gst.DateTime.new_now_local_time()
        tag_list.add_value(Gst.TagMergeMode.REPLACE, Gst.TAG_DATE_TIME, recorded_at)
        # Some muxers serialize the calendar date rather than the full timestamp.
        tag_list.add_value(
            Gst.TagMergeMode.REPLACE,
            Gst.TAG_DATE,
            GLib.Date.new_dmy(recorded_at.get_day(), GLib.DateMonth(recorded_at.get_month()), recorded_at.get_year()),
        )
        elements = self.pipeline.iterate_elements()
        while True:
            result, element = elements.next()
            if result != Gst.IteratorResult.OK:
                break
            if isinstance(element, Gst.TagSetter):
                Gst.TagSetter.merge_tags(element, tag_list, Gst.TagMergeMode.REPLACE)

        return True

    def start(self) -> bool:
        """Transition from prepare/countdown into active recording."""
        if not self.pipeline or self.state not in (RecordState.IDLE, RecordState.COUNTDOWN):
            return False

        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self.cancel()
            if self.on_error:
                self.on_error("Failed to activate recording pipeline.")
            return False

        self.start_time = time.monotonic()
        self.state = RecordState.RECORDING
        if self.on_state_change:
            self.on_state_change(self.state)
        return True

    def pause(self) -> bool:
        """Pause recording without terminating the file."""
        if self.state != RecordState.RECORDING or not self.pipeline:
            return False

        if self.pipeline.set_state(Gst.State.PAUSED) == Gst.StateChangeReturn.FAILURE:
            return False
        self.pause_start = time.monotonic()
        self.state = RecordState.PAUSED
        if self.on_state_change:
            self.on_state_change(self.state)
        return True

    def resume(self) -> bool:
        """Resume recording after pause."""
        if self.state != RecordState.PAUSED or not self.pipeline:
            return False

        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            return False
        self.paused_duration += time.monotonic() - self.pause_start
        self.state = RecordState.RECORDING
        if self.on_state_change:
            self.on_state_change(self.state)
        return True

    def stop(self, *, stopped_by_timer: bool = False) -> bool:
        """Stop recording cleanly with EOS flushing."""
        if self.state not in (RecordState.RECORDING, RecordState.PAUSED):
            return False

        self.stopped_by_timer = stopped_by_timer
        self.state = RecordState.FINALIZING
        if self.on_state_change:
            self.on_state_change(self.state)

        finalized = False
        if self.pipeline:
            self.pipeline.set_state(Gst.State.PLAYING)
            self.pipeline.send_event(Gst.Event.new_eos())

            bus = self.pipeline.get_bus()
            message = bus.timed_pop_filtered(5 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
            finalized = message is not None and message.type == Gst.MessageType.EOS
            self.pipeline.set_state(Gst.State.NULL)

        self._cleanup()
        self.state = RecordState.IDLE
        if self.on_state_change:
            self.on_state_change(self.state)

        if not finalized:
            if self.on_error:
                self.on_error("Finalization failed; the partial recording was retained for recovery.")
            return False
        if self.output_path and self.output_path.is_file():
            if self.on_finished:
                self.on_finished(self.output_path, self.stopped_by_timer)
            return True
        return False

    def cancel(self) -> bool:
        """Discard recording and delete partially written file."""
        if self.state == RecordState.IDLE and self.pipeline is None:
            return False

        self.stopped_by_timer = False

        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)

        self._cleanup()
        error: OSError | None = None
        try:
            if self.output_path:
                self.output_path.unlink(missing_ok=True)
        except OSError as exc:
            error = exc
        self.state = RecordState.IDLE
        if self.on_state_change:
            self.on_state_change(self.state)
        if error is not None:
            if self.on_error:
                self.on_error(f"Capture stopped, but could not discard {self.output_path}: {error}")
            return False
        return True

    def _cleanup(self) -> None:
        self.pipeline = None

    def poll(self) -> None:
        """Drain telemetry on the TUI thread; stop() exclusively consumes final EOS."""
        if not self.pipeline:
            return
        bus = self.pipeline.get_bus()
        while message := bus.pop_filtered(Gst.MessageType.ELEMENT | Gst.MessageType.ERROR | Gst.MessageType.EOS):
            if message.type == Gst.MessageType.ELEMENT:
                structure = message.get_structure()
                if structure and structure.has_name("level") and self.on_level_callback:
                    self.on_level_callback([float(x) for x in structure.get_value("peak")])
            else:
                error = message.parse_error()[0].message if message.type == Gst.MessageType.ERROR else "Audio source ended unexpectedly."
                self.pipeline.set_state(Gst.State.NULL)
                self._cleanup()
                self.state = RecordState.IDLE
                if self.on_state_change:
                    self.on_state_change(self.state)
                if self.on_error:
                    self.on_error(error + " Partial recording retained.")
                break


# =============================================================================
# DUSKY AUDIO PLAYER ENGINE (PLAYBIN NATIVE)
# =============================================================================

class DuskyAudioPlayer:
    """Interactive playback engine with accurate seeking & playhead."""

    def __init__(self) -> None:
        self.player: Gst.Element | None = None
        self.state: PlayerState = PlayerState.STOPPED
        self.current_path: Path | None = None
        self.duration_sec: float = 0.0

        self.on_state_change: Callable[[PlayerState], None] | None = None
        self.on_eos: Callable[[], None] | None = None
        self.on_error: Callable[[str], None] | None = None

        self.player = Gst.ElementFactory.make("playbin3", "audio_player")

    def load(self, file_path: Path, duration: float | None = None) -> bool:
        """Load an audio memo for playback and cache duration."""
        self.stop()
        self.current_path = file_path

        if duration is not None:
            self.duration_sec = duration
        else:
            try:
                disc = GstPbutils.Discoverer.new(Gst.SECOND)
                info = disc.discover_uri(file_path.resolve().as_uri())
                duration_ns = info.get_duration()
                self.duration_sec = duration_ns / Gst.SECOND if duration_ns != Gst.CLOCK_TIME_NONE else 0.0
            except GLib.Error:
                self.duration_sec = 0.0
        if self.player:
            self.player.set_property("uri", file_path.resolve().as_uri())
            return True
        return False

    def play(self) -> bool:
        """Start or resume playback."""
        if not self.player or not self.current_path:
            return False

        ret = self.player.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            return False

        self.state = PlayerState.PLAYING
        if self.on_state_change:
            self.on_state_change(self.state)

        return True

    def pause(self) -> bool:
        """Pause playback."""
        if not self.player or self.state != PlayerState.PLAYING:
            return False

        if self.player.set_state(Gst.State.PAUSED) == Gst.StateChangeReturn.FAILURE:
            return False
        self.state = PlayerState.PAUSED
        if self.on_state_change:
            self.on_state_change(self.state)
        return True

    def stop(self) -> None:
        """Stop playback and rewind."""
        if self.player:
            self.player.set_state(Gst.State.NULL)

        self.state = PlayerState.STOPPED
        if self.on_state_change:
            self.on_state_change(self.state)

    def seek_relative(self, delta_seconds: float) -> None:
        """Seek forward or backward relative to current position."""
        pos = self.get_position()
        target = max(0.0, min(self.duration_sec, pos + delta_seconds))
        self.seek_to(target)

    def seek_to(self, seconds: float) -> None:
        """Seek to absolute seconds."""
        if not self.player or self.duration_sec <= 0:
            return
        target_ns = int(max(0.0, min(self.duration_sec, seconds)) * 1e9)
        self.player.seek_simple(
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
            target_ns,
        )

    def get_position(self) -> float:
        """Query current playback position in seconds."""
        if not self.player or self.state == PlayerState.STOPPED:
            return 0.0
        success, pos_ns = self.player.query_position(Gst.Format.TIME)
        return pos_ns / 1e9 if success else 0.0

    def poll(self) -> None:
        if not self.player or self.state == PlayerState.STOPPED:
            return
        message = self.player.get_bus().pop_filtered(Gst.MessageType.EOS | Gst.MessageType.ERROR)
        if message:
            error = message.parse_error()[0].message if message.type == Gst.MessageType.ERROR else None
            self.stop()
            if error and self.on_error:
                self.on_error(error)
            elif self.on_eos:
                self.on_eos()


# =============================================================================
# VOICE MEMO LIBRARY REPOSITORY
# =============================================================================

def remove_waveform_cache(path: Path) -> None:
    """Cache cleanup must never turn a successful file operation into a failure."""
    key = hashlib.sha256(str(path.resolve()).encode()).hexdigest()
    try:
        for cache in CACHE_DIR.glob(f"{key}_*.json"):
            cache.unlink(missing_ok=True)
    except OSError:
        pass


class VoiceLibrary:
    """Manages voice memos in ~/.config/dusky/settings/dusky_voice/."""

    SUPPORTED_EXTS: Final[set[str]] = {".opus", ".flac", ".wav", ".mp3", ".ogg", ".m4a"}
    # Keep successful duration results only for files in the latest scan.
    _durations: dict[tuple[Path, int, int], float] = {}

    @classmethod
    def scan_memos(cls, exclude: Path | None = None) -> list[VoiceMemo]:
        """Scan directory and return sorted voice memos (newest first)."""
        ensure_storage_environment()
        memos: list[VoiceMemo] = []
        previous_durations = cls._durations
        durations: dict[tuple[Path, int, int], float] = {}
        discoverer: GstPbutils.Discoverer | None = None

        try:
            entries = list(RECORDINGS_DIR.iterdir())
        except OSError:
            return []

        for entry in entries:
            if entry == exclude or not entry.is_file():
                continue
            if entry.name.startswith("."):
                continue
            if entry.suffix.lower() not in cls.SUPPORTED_EXTS:
                continue

            try:
                stat = entry.stat()
            except OSError:
                continue
            size = stat.st_size
            created_at = datetime.fromtimestamp(stat.st_mtime)

            key = (entry, size, stat.st_mtime_ns)
            if key in previous_durations:
                duration = previous_durations[key]
                durations[key] = duration
            else:
                duration = 0.0
                try:
                    if discoverer is None:
                        discoverer = GstPbutils.Discoverer.new(Gst.SECOND)
                    info = discoverer.discover_uri(entry.resolve().as_uri())
                    duration_ns = info.get_duration()
                    if duration_ns != Gst.CLOCK_TIME_NONE:
                        duration = duration_ns / Gst.SECOND
                        if info.get_result() == GstPbutils.DiscovererResult.OK:
                            durations[key] = duration
                except GLib.Error:
                    pass

            memos.append(
                VoiceMemo(
                    path=entry,
                    name=entry.name,
                    stem=entry.stem,
                    ext=entry.suffix.lower(),
                    size_bytes=size,
                    duration_sec=duration,
                    created_at=created_at,
                    format_name=entry.suffix.lower().lstrip(".").upper(),
                )
            )

        cls._durations = durations
        memos.sort(key=lambda m: m.created_at, reverse=True)
        return memos

    @classmethod
    def rename_memo(cls, memo: VoiceMemo, new_stem: str) -> VoiceMemo | None:
        """Rename a memo; waveform keys are derived from its current identity."""
        clean_stem = clean_memo_stem(new_stem)
        if not clean_stem or clean_stem == memo.stem:
            return None

        new_path = memo.path.with_name(f"{clean_stem}{memo.ext}")
        if new_path.exists():
            return None

        try:
            memo.path.rename(new_path)
            remove_waveform_cache(memo.path)
        except OSError:
            return None

        return VoiceMemo(
            path=new_path,
            name=new_path.name,
            stem=new_path.stem,
            ext=memo.ext,
            size_bytes=memo.size_bytes,
            duration_sec=memo.duration_sec,
            created_at=memo.created_at,
            format_name=memo.format_name,
        )

    @classmethod
    def delete_memo(cls, memo: VoiceMemo) -> bool:
        """Delete the selected memo."""
        try:
            memo.path.unlink(missing_ok=True)
            remove_waveform_cache(memo.path)
            return True
        except OSError:
            return False


# =============================================================================
# DUSKYTUI DESIGN SYSTEM COMPONENTS (UI.PY PATTERNS)
# =============================================================================

class ShortcutFlowLayout(Layout):
    """Arrange shortcut items in a responsive flow layout before paint."""
    name = "shortcut_flow"

    def arrange(self, parent: Widget, children: list[Widget], size: Size, greedy: bool = True) -> list[WidgetPlacement]:
        placements: list[WidgetPlacement] = []
        x = y = 0
        for child in children:
            width = min(size.width, child.get_content_width(size, parent.screen.size) + child.styles.gutter.width)
            if x and x + width > size.width:
                x = 0
                y += 1
            placements.append(WidgetPlacement(Region(x, y, width, 1), Offset(), Spacing(), child))
            x += width
        return placements


class FlowContainer(Widget):
    """Flow layout container for responsive shortcut rows."""
    DEFAULT_CSS = "FlowContainer { height: auto; width: 100%; }"

    @property
    def layout(self) -> ShortcutFlowLayout:
        return self._flow_layout

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._flow_layout = ShortcutFlowLayout()
        super().__init__(*args, **kwargs)


class Shortcut(Label):
    """Clickable shortcut pill styled matching DuskyTUI."""

    def __init__(self, key_text: str, label: str, action_name: str | None = None, **kwargs: Any) -> None:
        super().__init__(classes="footer-shortcut", **kwargs)
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
        if self.action_name:
            await self.app.run_action(self.action_name)

    def blink(self) -> None:
        self.add_class("-active")
        self.refresh()
        self.set_timer(0.2, self._unblink)

    def _unblink(self) -> None:
        self.remove_class("-active")
        self.refresh()


class NoticeBox(Vertical):
    """Sleek alert banner with left accent border and tinted background."""

    def __init__(self, message: str, level: str = "info", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.message = message
        self.level = level
        self.add_class(f"-{level}")

    def compose(self) -> ComposeResult:
        yield Label(self.message, id="notice-label")

    def update_notice(self, message: str, level: str) -> None:
        self.message = message
        for c in ["-info", "-warning", "-danger", "-success"]:
            self.remove_class(c)
        self.add_class(f"-{level}")
        lbl = self.query_one("#notice-label", Label)
        lbl.update(message)


class AppFooter(Vertical):
    """Unified application footer with FlowContainer shortcuts & status bar."""

    status_msg = reactive("")
    status_level = reactive("info")

    def compose(self) -> ComposeResult:
        with FlowContainer(id="footer-shortcuts-container"):
            yield Shortcut("r", "Record", "toggle_record", id="sc-r")
            yield Shortcut("space", "Pause/Play", "toggle_pause_play", id="sc-space")
            yield Shortcut("c", "Discard", "cancel_record", id="sc-c")
            yield Shortcut("h/l", "Seek ±5s", "seek_fwd", id="sc-seek")
            yield Shortcut("s", "Stop", "stop_player", id="sc-s")
            yield Shortcut("y", "Copy Path", "copy_path", id="sc-y")
            yield Shortcut("e", "Rename", "rename_selected", id="sc-e")
            yield Shortcut("d", "Delete", "delete_selected", id="sc-d")
            yield Shortcut("/", "Search", "focus_search", id="sc-slash")
            yield Shortcut("q", "Quit", "quit_app", id="sc-q")

        with Horizontal(id="footer-bottom-row"):
            yield Label(f"󰈔 {RECORDINGS_DIR}", id="file-link")
            yield Label("│", classes="footer-sep")
            yield Label("PIPEWIRE-PULSE", id="mode-btn")
            yield Label("│", classes="footer-sep")
            yield Label("", id="status-bar")

    def watch_status_msg(self, new_val: str) -> None:
        try:
            bar = self.query_one("#status-bar", Label)
            tc = getattr(self.app, "theme_colors", DEFAULT_THEME)
            if new_val:
                txt = Text()
                txt.append("Status: ", style=tc["accent"])
                color = tc.get(self.status_level, tc["fg"])
                txt.append(new_val, style=color)
                bar.update(txt)
                bar.display = True
            else:
                bar.display = False
        except Exception:
            pass


# =============================================================================
# ELEGANT AUDIO VISUALIZATION WIDGETS
# =============================================================================

class LiveVuMeter(Static):
    """Calibrated audio decibel meter with true multi-channel support."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.channel_peaks: list[float] = [-80.0, -80.0]

    def update_levels(self, peaks: list[float]) -> None:
        self.channel_peaks = peaks if peaks else [-80.0]
        self.refresh()

    def render(self) -> RenderableType:
        width = max(14, self.size.width - 24)
        tc = getattr(self.app, "theme_colors", DEFAULT_THEME)

        # Calibrated dB scale header
        scale = Text()
        scale.append("     -60dB      -36dB    -18dB   -12dB   -6dB  -3dB  0dB", style=f"dim {tc['muted']}")

        lines = [scale]
        is_mono = len(self.channel_peaks) == 1

        for idx, peak_db in enumerate(self.channel_peaks[:2]):
            label = "MIC ╶" if is_mono else ("  L ╶" if idx == 0 else "  R ╶")
            clamped_peak = max(-60.0, min(0.0, peak_db))
            frac = (clamped_peak + 60.0) / 60.0
            filled = int(frac * width)

            line = Text()
            line.append(label, style=f"bold {tc['accent']}")

            green_len = int(width * 0.70)
            yellow_len = int(width * 0.88)
            for i in range(width):
                if i < filled:
                    if i < green_len:
                        line.append("━", style=tc["success"])
                    elif i < yellow_len:
                        line.append("━", style=tc["warning"])
                    else:
                        line.append("━", style=f"bold {tc['error']}")
                else:
                    line.append("─", style=f"dim {tc['muted']}")
            line.append("╴ ", style=tc["muted"])

            if peak_db <= -60.0:
                line.append("-∞ dB", style=f"dim {tc['muted']}")
            else:
                style = f"bold {tc['error']}" if peak_db > -3.0 else tc["fg"]
                line.append(f"{peak_db:5.1f} dB", style=style)
            if peak_db >= -0.5:
                line.append(" [CLIP]", style=f"bold {tc['error']} on {tc['bg']}")

            lines.append(line)

        res = Text()
        for i, l in enumerate(lines):
            res.append_text(l)
            if i < len(lines) - 1:
                res.append("\n")
        return res


class RollingOscilloscope(Static):
    """Dynamic real-time voice waveform strip matching music_recognition.py logic."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.history: deque[float] = deque(maxlen=100)

    def push_peak(self, peak_fraction: float) -> None:
        self.history.append(min(1.0, max(0.0, peak_fraction)))
        self.refresh()

    def clear(self) -> None:
        self.history.clear()
        self.refresh()

    def render(self) -> RenderableType:
        width = max(10, self.size.width - 4)
        height = 4

        if self.history.maxlen != width:
            old = list(self.history)
            self.history = deque(old, maxlen=width)

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
                    sub = total_steps - row_base
                    lines[r].append(UNICODE_BLOCKS[sub], style=bar_color)

        res = Text()
        for idx, line in enumerate(lines):
            res.append_text(line)
            if idx < height - 1:
                res.append("\n")

        return Panel(
            res,
            border_style=tc["muted"],
            title=f"[bold {tc['accent']}]󰮔  LIVE VOICE SPECTRUM[/]",
            title_align="left",
        )


class PlayerWaveformWidget(Static):
    """Full-width high-resolution waveform with illuminated playhead matching music_recognition.py."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.peaks: list[float] = []
        self.progress_fraction: float = 0.0

    def set_data(self, peaks: list[float], progress_fraction: float = 0.0) -> None:
        self.peaks = peaks
        self.progress_fraction = max(0.0, min(1.0, progress_fraction))
        self.refresh()

    def render(self) -> RenderableType:
        width = max(10, self.size.width - 4)
        height = 4
        tc = getattr(self.app, "theme_colors", DEFAULT_THEME)

        if not self.peaks:
            placeholder = Text(
                "\n  Select a voice memo to inspect audio waveform & playback  \n",
                style=f"italic dim {tc['muted']}",
            )
            return Panel(placeholder, border_style=tc["muted"], title="󰮔  AUDIO WAVEFORM", title_align="left")

        resampled: list[float] = []
        for i in range(width):
            idx = int(i * len(self.peaks) / width)
            resampled.append(self.peaks[min(idx, len(self.peaks) - 1)])

        playhead_col = int(self.progress_fraction * width)
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
                    sub = total_steps - row_base
                    lines[r].append(UNICODE_BLOCKS[sub], style=color)

        full = Text()
        for i, row in enumerate(lines):
            full.append_text(row)
            if i < height - 1:
                full.append("\n")

        pct = int(self.progress_fraction * 100)
        return Panel(
            full,
            border_style=tc["muted"],
            title=f"[bold {tc['accent']}]󰮔  AUDIO WAVEFORM[/] [dim]({pct}%)[/]",
            title_align="left",
        )


class DigitalTimerDisplay(Static):
    """Refined digital clock for recording elapsed time & auto-stop budget."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.elapsed: float = 0.0
        self.max_limit: float = 0.0

    def update_time(self, elapsed: float, max_limit: float = 0.0) -> None:
        self.elapsed = elapsed
        self.max_limit = max_limit
        self.refresh()

    def render(self) -> RenderableType:
        total = int(self.elapsed)
        minutes = total // 60
        seconds = total % 60
        tenths = int((self.elapsed - total) * 10)
        clock_str = f"{minutes:02d}:{seconds:02d}.{tenths}"

        tc = getattr(self.app, "theme_colors", DEFAULT_THEME)
        text = Text()
        text.append("TIMECODE  ", style=f"dim {tc['muted']}")
        text.append(clock_str, style=f"bold {tc['fg']}")

        if self.max_limit > 0:
            remaining = max(0.0, self.max_limit - self.elapsed)
            rem_total = int(remaining)
            rem_min = rem_total // 60
            rem_sec = rem_total % 60
            text.append(f"   󰔛 Limit: {rem_min:02d}:{rem_sec:02d} (-{int(remaining)}s)", style=tc["warning"])

        return text


# =============================================================================
# MODAL DIALOGS (MATCHING UI.PY CONFIRM/ALERT SPEC)
# =============================================================================

class RenameMemoModal(ModalScreen[str | None]):
    """Modal screen for renaming a recorded memo."""

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
    """Confirmation modal for permanently deleting a memo."""

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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-confirm")


# =============================================================================
# PRIMARY APPLICATION TUI (TEXTUAL DUSKYTUI SYSTEM)
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

    /* Top HUD Header */
    #hud-header {
        width: 100%;
        height: 1;
        margin-bottom: 1;
        background: transparent;
        padding: 0 1;
    }

    #hud-app-name {
        width: auto;
        color: $primary;
        text-style: bold;
    }

    #hud-status-badge {
        width: 1fr;
        content-align: center middle;
        text-style: bold;
    }

    #hud-stats {
        width: auto;
        color: $foreground 60%;
        text-style: dim;
    }

    /* Tab Bar (ui.py pattern) */
    #tab-bar {
        width: 100%;
        height: 1;
        margin-bottom: 1;
        background: transparent;
    }

    #tabs-container {
        width: 1fr;
        height: 1;
        align: center middle;
    }

    Tabs {
        width: auto;
        height: 1;
        background: transparent;
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

    /* Console Deck */
    #console_deck {
        height: auto;
        border: solid $secondary 40%;
        background: $surface 30%;
        padding: 0 1;
        margin-bottom: 0;
    }

    /* NoticeBox Status Banner */
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

    #timer_box {
        height: 1;
        content-align: center middle;
        margin-bottom: 0;
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
        height: 3;
        align: center middle;
        margin-top: 1;
    }

    .studio_btn {
        margin: 0 1;
        min-width: 18;
        height: 3;
        border: solid $secondary 40%;
        background: $surface;
        color: $foreground;
        text-style: bold;
    }

    .studio_btn:hover {
        border: solid $primary;
        background: $primary 25%;
        color: $foreground;
    }

    #btn_record.-recording {
        background: $error;
        color: $background;
        text-style: bold;
        border: solid $error;
    }

    #settings_card {
        height: auto;
        border: solid $secondary 40%;
        background: $surface 30%;
        padding: 1 1;
        margin-top: 1;
    }

    .config_col {
        width: 1fr;
        padding: 0 1;
    }

    .config_label {
        color: $primary;
        text-style: bold;
        margin-bottom: 1;
    }

    Select {
        border: none;
        border-bottom: solid $primary 60%;
        background: $primary 10%;
        color: $foreground;
        height: 3;
        margin-bottom: 1;
    }

    Select:focus {
        border: none;
        border-bottom: solid $primary;
    }

    Input {
        border: none;
        border-bottom: solid $primary 60%;
        background: $primary 10%;
        color: $foreground;
        height: 3;
        padding: 0 1;
    }

    Input:focus {
        border: none;
        border-bottom: solid $primary;
        background: $primary 20%;
    }

    #lbl_target_preview {
        height: 3;
        content-align: center middle;
        color: $primary;
        text-style: dim;
        background: $primary 10%;
        border-bottom: solid $primary 60%;
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
    #devices_info {
        height: auto;
        padding: 0 1;
    }

    /* AppFooter (ui.py pattern) */
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
        width: 55;
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
        Binding("ctrl+tab", "switch_tab", "Switch Tab"),
        Binding("1", "tab_studio", "Studio"),
        Binding("2", "tab_library", "Library"),
        Binding("3", "tab_devices", "Devices"),
        Binding("r", "toggle_record", "Record / Stop"),
        Binding("space", "toggle_pause_play", "Pause/Play"),
        Binding("c", "cancel_record", "Cancel"),
        Binding("left,h", "seek_back", "-5s"),
        Binding("right,l", "seek_fwd", "+5s"),
        Binding("s", "stop_player", "Stop"),
        Binding("y", "copy_path", "Copy Path"),
        Binding("e", "rename_selected", "Rename"),
        Binding("d", "delete_selected", "Delete"),
        Binding("slash", "focus_search", "Search"),
        Binding("q", "quit_app", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        ensure_storage_environment()
        self.theme_colors = load_matugen_theme()
        self.last_theme_mtime: float = 0.0

        self.engine = DuskyAudioEngine()
        self.player = DuskyAudioPlayer()

        self.memos: list[VoiceMemo] = []
        self.selected_memo: VoiceMemo | None = None
        self.countdown_remaining: int = 0
        self._countdown_timer: Any = None

        # Setup engine hooks
        self.engine.on_level_callback = self._on_engine_level
        self.engine.on_state_change = self._on_engine_state_change
        self.engine.on_finished = self._on_engine_finished
        self.engine.on_error = self._on_engine_error

        # Setup player hooks
        self.player.on_state_change = self._on_player_state_change
        self.player.on_eos = self._on_player_eos
        self.player.on_error = lambda message: self.notify(message, title="Playback Error", severity="error")
        self._closing = False
        self._library_refresh_pending = False
        self._library_scanning = False
        self._waveform_cancel = threading.Event()
        self._waveform_generation = 0

    def _safe_dispatch(self, func: Callable[..., Any], *args: Any) -> None:
        """Execute callback directly if already on main thread, else enqueue without blocking the worker."""
        if getattr(self, "_closing", False):
            return
        if threading.current_thread() is threading.main_thread():
            func(*args)
        else:
            try:
                self.call_later(func, *args)
            except RuntimeError:
                pass

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

    def compose(self) -> ComposeResult:
        with Container(id="main-box") as box:
            box.border_title = " 󰍬  DUSKY VOICE MEMOS "
            with Horizontal(id="hud-header"):
                yield Label("󰍬  DUSKY VOICE", id="hud-app-name")
                yield Label("● STANDBY", id="hud-status-badge")
                yield Label("0 memos • 0.0 KB", id="hud-stats")

            with Horizontal(id="tab-bar"):
                with Horizontal(id="tabs-container"):
                    yield Tabs(
                        Tab("󰍬  Studio Recorder", id="tab_studio"),
                        Tab("󰉋  Voice Memos", id="tab_library"),
                        Tab("󰋋  Audio Devices", id="tab_devices"),
                        id="app_tabs",
                    )

            with Container(id="content-area"):
                # Pane 1: Studio Recorder
                with Vertical(id="pane_studio", classes="tab_pane"):
                    with Vertical(id="console_deck"):
                        yield NoticeBox(
                            "󰐽  STANDBY • READY TO CAPTURE",
                            level="info",
                            id="banner_box",
                        )
                        yield DigitalTimerDisplay(id="timer_box")
                        yield LiveVuMeter(id="vu_meter")
                        yield RollingOscilloscope(id="oscilloscope")

                    with Horizontal(id="studio_controls"):
                        yield Button("󰐽  Record", id="btn_record", classes="studio_btn")
                        yield Button("󰏤  Pause", id="btn_pause", classes="studio_btn", disabled=True)
                        yield Button("󰅖  Cancel", id="btn_cancel", classes="studio_btn", disabled=True)

                    with Horizontal(id="settings_card"):
                        with Vertical(classes="config_col"):
                            yield Label("󰍬  INPUT SOURCE", classes="config_label")
                            yield Select(
                                [
                                    ("󰍬 Microphone Only", "mic"),
                                    ("󰋋 Desktop Audio", "system"),
                                    ("󰮔 Both (Mic + Desktop)", "both"),
                                ],
                                allow_blank=False,
                                value="mic",
                                id="select_source",
                            )
                            yield Select(
                                [("Stereo (2 Channels)", 2), ("Mono (1 Channel)", 1)],
                                allow_blank=False,
                                value=2,
                                id="select_channels",
                            )
                        with Vertical(classes="config_col"):
                            yield Label("󰋋  CODEC & FORMAT", classes="config_label")
                            yield Select(
                                [
                                    ("Opus (.opus) • 48 kHz", "opus"),
                                    ("FLAC (.flac) • Lossless", "flac"),
                                    ("WAV (.wav) • Raw PCM", "wav"),
                                    ("MP3 (.mp3) • Compatible", "mp3"),
                                    ("OGG (.ogg) • Vorbis", "ogg"),
                                ],
                                allow_blank=False,
                                value="opus",
                                id="select_format",
                            )
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
                            yield Label("󰔛  TIMERS & LIMITS", classes="config_label")
                            yield Select(
                                [
                                    ("Pre-roll: Instant (0s)", 0),
                                    ("Pre-roll: 3 Seconds", 3),
                                    ("Pre-roll: 5 Seconds", 5),
                                    ("Pre-roll: 10 Seconds", 10),
                                ],
                                allow_blank=False,
                                value=0,
                                id="select_delay",
                            )
                            yield Select(
                                [
                                    ("Limit: Unlimited", 0),
                                    ("Limit: 30 Seconds", 30),
                                    ("Limit: 1 Minute", 60),
                                    ("Limit: 5 Minutes", 300),
                                    ("Limit: 10 Minutes", 600),
                                    ("Limit: 30 Minutes", 1800),
                                ],
                                allow_blank=False,
                                value=0,
                                id="select_duration",
                            )
                        with Vertical(classes="config_col"):
                            yield Label("󰏫  MEMO METADATA", classes="config_label")
                            yield Input(placeholder="Title prefix (optional)", id="input_title")
                            yield Static("Target: ~/.config/dusky/settings/dusky_voice/", id="lbl_target_preview")

                # Pane 2: Memo Library
                with Vertical(id="pane_library", classes="tab_pane"):
                    with Horizontal(id="library_header_row"):
                        yield Input(placeholder="󰍉  Search voice memos by title... (Press / to focus)", id="search_input")
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
                    yield Label("󰋋  PIPEWIRE AUDIO GRAPH & HARDWARE NODES", classes="config_label")
                    yield Static(id="devices_info")

            yield AppFooter(id="footer")

    def on_mount(self) -> None:
        """App initialization on mount."""
        self.apply_current_theme()
        self.set_interval(0.1, self._clock_tick)
        self.set_interval(1.5, self._watch_theme_file)
        self._refresh_library()
        self._refresh_devices_view()
        self._show_pane("pane_studio")

    def _watch_theme_file(self) -> None:
        """Live synchronization with user's Matugen color palette changes."""
        if THEME_FILE.is_file():
            try:
                mtime = THEME_FILE.stat().st_mtime
                if mtime != self.last_theme_mtime:
                    self.last_theme_mtime = mtime
                    self.theme_colors = load_matugen_theme()
                    self.apply_current_theme()
                    self.refresh(layout=True)
            except OSError:
                pass

    def _show_pane(self, target_id: str) -> None:
        for pane_id in ("pane_studio", "pane_library", "pane_devices"):
            pane = self.query_one(f"#{pane_id}")
            pane.display = (pane_id == target_id)

    def set_status(self, msg: str, level: str = "info") -> None:
        """Update live status message in AppFooter."""
        footer = self.query_one("#footer", AppFooter)
        footer.status_level = level
        footer.status_msg = msg

    # -------------------------------------------------------------------------
    # TAB & ROUTING ACTIONS
    # -------------------------------------------------------------------------

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        tab_id = event.tab.id
        if tab_id == "tab_studio":
            self._show_pane("pane_studio")
        elif tab_id == "tab_library":
            self._show_pane("pane_library")
            self._refresh_library()
        elif tab_id == "tab_devices":
            self._show_pane("pane_devices")
            self._refresh_devices_view()

    def action_tab_studio(self) -> None:
        self.query_one("#app_tabs", Tabs).active = "tab_studio"

    def action_tab_library(self) -> None:
        self.query_one("#app_tabs", Tabs).active = "tab_library"

    def action_tab_devices(self) -> None:
        self.query_one("#app_tabs", Tabs).active = "tab_devices"

    def action_switch_tab(self) -> None:
        tabs = self.query_one("#app_tabs", Tabs)
        active = tabs.active
        if active == "tab_studio":
            tabs.active = "tab_library"
        elif active == "tab_library":
            tabs.active = "tab_devices"
        else:
            tabs.active = "tab_studio"

    def action_focus_search(self) -> None:
        self.action_tab_library()
        self.query_one("#search_input", Input).focus()

    # -------------------------------------------------------------------------
    # STUDIO RECORDER LOGIC
    # -------------------------------------------------------------------------

    def action_toggle_record(self) -> None:
        if self.engine.state == RecordState.IDLE:
            self._start_recording_flow()
        elif self.engine.state in (RecordState.RECORDING, RecordState.PAUSED):
            self.engine.stop(stopped_by_timer=False)
        elif self.engine.state == RecordState.COUNTDOWN:
            self._abort_countdown()

    def action_toggle_pause_play(self) -> None:
        tabs = self.query_one("#app_tabs", Tabs)
        if tabs.active == "tab_studio" and self.engine.state in (RecordState.RECORDING, RecordState.PAUSED):
            if self.engine.state == RecordState.RECORDING:
                self.engine.pause()
            else:
                self.engine.resume()
        else:
            self._toggle_playback()

    def action_cancel_record(self) -> None:
        if self.engine.state == RecordState.COUNTDOWN:
            self._abort_countdown()
        elif self.engine.state != RecordState.IDLE:
            if self.engine.cancel():
                self.set_status("Recording discarded", "warning")
                self.notify("Recording discarded.", title="Cancelled", severity="warning")

    def _start_recording_flow(self) -> None:
        if self.player.state == PlayerState.PLAYING:
            self.player.pause()

        src_val = str(self.query_one("#select_source", Select).value or "mic")
        source = {
            "mic": RecordSource.MIC,
            "system": RecordSource.SYSTEM,
            "both": RecordSource.BOTH,
        }.get(src_val, RecordSource.MIC)

        fmt_val = str(self.query_one("#select_format", Select).value or "opus")
        fmt = {
            "opus": AudioFormat.OPUS,
            "flac": AudioFormat.FLAC,
            "wav": AudioFormat.WAV,
            "mp3": AudioFormat.MP3,
            "ogg": AudioFormat.OGG,
        }.get(fmt_val, AudioFormat.OPUS)

        channels = int(self.query_one("#select_channels", Select).value or 2)
        delay_sec = int(self.query_one("#select_delay", Select).value or 0)
        max_duration = float(self.query_one("#select_duration", Select).value or 0)
        custom_title = self.query_one("#input_title", Input).value.strip()

        timestamp_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
        prefix = custom_title if custom_title else "Memo"
        clean_prefix = clean_memo_stem(prefix) or "Memo"
        filename = f"{clean_prefix}_{timestamp_str}{fmt.extension}"
        output_path = RECORDINGS_DIR / filename

        ok = self.engine.prepare(
            output_path=output_path,
            source=source,
            format_=fmt,
            channels=channels,
            max_duration=max_duration,
            sample_rate=int(self.query_one("#select_rate", Select).value),
            title=custom_title or f"Memo {timestamp_str}",
        )
        if not ok:
            return

        if delay_sec > 0:
            self.countdown_remaining = delay_sec
            self.engine.state = RecordState.COUNTDOWN
            self._on_engine_state_change(RecordState.COUNTDOWN)
            self._countdown_timer = self.set_interval(1.0, self._countdown_tick)
        else:
            self.engine.start()

    def _countdown_tick(self) -> None:
        self.countdown_remaining -= 1
        if self.countdown_remaining <= 0:
            if self._countdown_timer:
                self._countdown_timer.stop()
                self._countdown_timer = None
            self.engine.start()
        else:
            self._update_banner()

    def _abort_countdown(self) -> None:
        if self._countdown_timer:
            self._countdown_timer.stop()
            self._countdown_timer = None
        self.engine.cancel()
        self._update_banner()

    def _on_engine_level(self, peaks: list[float]) -> None:
        self._safe_dispatch(self._update_meters, peaks)

    def _update_meters(self, peaks: list[float]) -> None:
        vu = self.query_one("#vu_meter", LiveVuMeter)
        vu.update_levels(peaks)
        if self.engine.state == RecordState.RECORDING:
            max_peak = max(peaks) if peaks else -80.0
            fraction = max(0.0, min(1.0, (max_peak - (-60.0)) / 60.0)) if max_peak > -80.0 else 0.0
            osc = self.query_one("#oscilloscope", RollingOscilloscope)
            osc.push_peak(fraction)

    def _on_engine_state_change(self, state: RecordState) -> None:
        self._safe_dispatch(self._update_ui_state, state)

    def _update_ui_state(self, state: RecordState) -> None:
        self._update_banner()
        btn_rec = self.query_one("#btn_record", Button)
        btn_pause = self.query_one("#btn_pause", Button)
        btn_cancel = self.query_one("#btn_cancel", Button)

        if state == RecordState.RECORDING:
            btn_rec.label = "  Stop & Save"
            btn_rec.remove_class("-recording")
            btn_rec.add_class("-recording")
            btn_pause.disabled = False
            btn_pause.label = "󰏤  Pause"
            btn_cancel.disabled = False
            self.set_status("Recording in progress...", "error")
        elif state == RecordState.PAUSED:
            btn_rec.label = "  Stop & Save"
            btn_rec.remove_class("-recording")
            btn_rec.add_class("-recording")
            btn_pause.disabled = False
            btn_pause.label = "  Resume"
            btn_cancel.disabled = False
            self.set_status("Recording paused", "warning")
        elif state == RecordState.COUNTDOWN:
            btn_rec.label = "󰅖  Cancel Delay"
            btn_rec.remove_class("-recording")
            btn_pause.disabled = True
            btn_cancel.disabled = False
            self.set_status(f"Pre-roll delay: {self.countdown_remaining}s", "warning")
        else:  # IDLE or FINALIZING
            btn_rec.label = "󰐽  Record"
            btn_rec.remove_class("-recording")
            btn_pause.disabled = True
            btn_pause.label = "󰏤  Pause"
            btn_cancel.disabled = True
            self.query_one("#oscilloscope", RollingOscilloscope).clear()
            self.set_status("Ready", "info")

    def _update_banner(self) -> None:
        banner = self.query_one("#banner_box", NoticeBox)
        state = self.engine.state
        if state == RecordState.RECORDING:
            banner.update_notice("󰑋  RECORDING LIVE", level="danger")
        elif state == RecordState.PAUSED:
            banner.update_notice("󰏤  RECORDING PAUSED", level="warning")
        elif state == RecordState.COUNTDOWN:
            banner.update_notice(f"󰔚  STARTING IN {self.countdown_remaining}s...", level="warning")
        elif state == RecordState.FINALIZING:
            banner.update_notice("󰆓  FINALIZING & FLUSHING HEADERS...", level="success")
        else:
            banner.update_notice("󰐽  STANDBY • READY TO CAPTURE", level="info")

    def _clock_tick(self) -> None:
        """Periodic clock update for recording or playback."""
        self.engine.poll()
        self.player.poll()
        timer_box = self.query_one("#timer_box", DigitalTimerDisplay)
        if self.engine.state in (RecordState.RECORDING, RecordState.PAUSED):
            elapsed = self.engine.elapsed_seconds
            timer_box.update_time(elapsed, self.engine.max_duration)
            if self.engine.max_duration > 0 and elapsed >= self.engine.max_duration:
                self.engine.stop(stopped_by_timer=True)
        else:
            timer_box.update_time(0.0)

        if self.player.state == PlayerState.PLAYING and self.player.duration_sec > 0:
            pos = self.player.get_position()
            frac = pos / self.player.duration_sec
            wf = self.query_one("#player_waveform", PlayerWaveformWidget)
            wf.set_data(wf.peaks, frac)
            self._update_player_title(pos)

        self._update_hud()

    def _update_hud(self) -> None:
        try:
            badge = self.query_one("#hud-status-badge", Label)
            tc = self.theme_colors
            state = self.engine.state
            if state == RecordState.RECORDING:
                elapsed = self.engine.elapsed_seconds
                mins = int(elapsed) // 60
                secs = int(elapsed) % 60
                badge.update(Text(f"● RECORDING  {mins:02d}:{secs:02d}", style=f"bold {tc['error']}"))
            elif state == RecordState.PAUSED:
                elapsed = self.engine.elapsed_seconds
                mins = int(elapsed) // 60
                secs = int(elapsed) % 60
                badge.update(Text(f"󰏤 PAUSED  {mins:02d}:{secs:02d}", style=f"bold {tc['warning']}"))
            elif state == RecordState.COUNTDOWN:
                badge.update(Text(f"󰔛 COUNTDOWN: {self.countdown_remaining}s", style=f"bold {tc['warning']}"))
            elif self.player.state == PlayerState.PLAYING and self.selected_memo:
                pos = self.player.get_position()
                dur = self.player.duration_sec
                badge.update(Text(f" PLAYING  {int(pos)//60:02d}:{int(pos)%60:02d} / {int(dur)//60:02d}:{int(dur)%60:02d}", style=f"bold {tc['success']}"))
            else:
                badge.update(Text("● STANDBY", style=f"dim {tc['accent']}"))

            stats = self.query_one("#hud-stats", Label)
            total_size_kb = sum(m.size_bytes for m in self.memos) / 1024
            if total_size_kb >= 1024:
                size_str = f"{total_size_kb / 1024:.1f} MB"
            else:
                size_str = f"{total_size_kb:.1f} KB"
            stats.update(Text(f"{len(self.memos)} memos • {size_str}", style=f"dim {tc['muted']}"))
        except Exception:
            pass

    def _on_engine_finished(self, path: Path, stopped_by_timer: bool = False) -> None:
        self._safe_dispatch(self._handle_recording_saved, path, stopped_by_timer)

    def _handle_recording_saved(self, path: Path, stopped_by_timer: bool = False) -> None:
        try:
            size_kb = path.stat().st_size / 1024
        except OSError:
            self.notify("Saved recording is no longer accessible.", severity="warning")
            self._refresh_library()
            return
        self.set_status(f"Saved {path.name}", "success")

        if stopped_by_timer:
            self.notify(
                f"Auto-stop timer reached: {path.name} ({size_kb:.1f} KB)",
                title="Timer Limit Complete",
                severity="information",
            )
            send_desktop_notification(
                "Voice Memo Saved",
                f"Timer limit reached: {path.name}\nSize: {size_kb:.1f} KB",
                icon_name="audio-input-microphone",
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
        self.notify(message, title="Recording Error", severity="error")
        self.set_status(message, "error")

    # -------------------------------------------------------------------------
    # LIBRARY & PLAYBACK LOGIC
    # -------------------------------------------------------------------------

    def _refresh_library(self) -> None:
        self._library_refresh_pending = True
        if self._library_scanning:
            return
        self._library_scanning = True

        async def scan() -> None:
            try:
                while self._library_refresh_pending:
                    self._library_refresh_pending = False
                    exclude = self.engine.output_path if self.engine.pipeline is not None else None
                    memos = await asyncio.to_thread(VoiceLibrary.scan_memos, exclude)
                    if not self._closing:
                        self.memos = memos
                        if self.selected_memo and not any(m.path == self.selected_memo.path for m in memos):
                            self.player.stop()
                            self._waveform_cancel.set()
                            self.selected_memo = None
                            self.query_one("#player_waveform", PlayerWaveformWidget).set_data([])
                            self.query_one("#lbl_player_title", Label).update("No recording selected")
                        self._render_library()
            except OSError as error:
                if not self._closing:
                    self.notify(str(error), title="Library Error", severity="error")
            finally:
                self._library_scanning = False

        self.run_worker(scan(), group="library")

    def _render_library(self) -> None:
        table = self.query_one("#library_table", DataTable)
        with self.prevent(DataTable.RowHighlighted):
            table.clear(columns=True)
            table.add_columns("Status", "Title", "Format", "Duration", "Size", "Date Recorded")

            search_query = self.query_one("#search_input", Input).value.lower().strip()

            filtered = [
                m for m in self.memos if not search_query or search_query in m.name.lower()
            ]

            tc = self.theme_colors
            for m in filtered:
                is_active = (
                    self.player.state == PlayerState.PLAYING
                    and self.selected_memo
                    and m.path == self.selected_memo.path
                )
                if is_active:
                    status_cell = Text("  PLAY ", style=f"bold {tc['bg']} on {tc['success']}")
                else:
                    status_cell = Text(" 󰍬 MEMO ", style=f"dim {tc['muted']}")

                title_cell = Text(m.name, style=f"bold {tc['fg']}")

                fmt_color = tc["accent"]
                if m.format_name == "FLAC":
                    fmt_color = tc["warning"]
                elif m.format_name == "WAV":
                    fmt_color = tc["success"]

                format_cell = Text(f" {m.format_name} ", style=f"bold {tc['bg']} on {fmt_color}")
                dur_cell = Text(m.formatted_duration, style=tc["warning"])
                size_cell = Text(m.formatted_size, style=f"dim {tc['fg']}")
                date_cell = Text(m.formatted_date, style=f"dim {tc['muted']}")

                table.add_row(
                    status_cell,
                    title_cell,
                    format_cell,
                    dur_cell,
                    size_cell,
                    date_cell,
                    key=str(m.path),
                )

            if self.selected_memo:
                for row, memo in enumerate(filtered):
                    if memo.path == self.selected_memo.path:
                        table.move_cursor(row=row, scroll=False)
                        break

        try:
            self.query_one("#lbl_memo_count", Label).update(f" {len(filtered)} memos ")
        except Exception:
            pass

        if filtered and not self.selected_memo:
            self._select_memo(filtered[0])
        self._update_hud()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search_input":
            self._render_library()
        elif event.input.id == "input_title":
            self._update_target_preview()

    def _update_target_preview(self) -> None:
        try:
            val = self.query_one("#input_title", Input).value.strip()
            prefix = val if val else "Memo"
            lbl = self.query_one("#lbl_target_preview", Static)
            tc = self.theme_colors
            txt = Text()
            txt.append("Target: ", style=f"dim {tc['muted']}")
            txt.append(f"{prefix}_YYYY-MM-DD.ext", style=f"bold {tc['accent']}")
            lbl.update(txt)
        except Exception:
            pass

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
        if self.selected_memo and (self.selected_memo.path, self.selected_memo.size_bytes, self.selected_memo.created_at) == (memo.path, memo.size_bytes, memo.created_at):
            self.selected_memo = memo
            return
        self._waveform_cancel.set()
        self._waveform_cancel = threading.Event()
        cancelled = self._waveform_cancel
        self._waveform_generation += 1
        generation = self._waveform_generation
        self.selected_memo = memo
        self.player.load(memo.path, memo.duration_sec)
        self.query_one("#player_waveform", PlayerWaveformWidget).set_data([])
        self._update_player_title(0.0)

        def _load_peaks() -> None:
            peaks = compute_waveform_peaks(memo.path, cancelled=cancelled)
            if not cancelled.is_set():
                self._safe_dispatch(self._apply_waveform_peaks, memo.path, peaks, generation)

        threading.Thread(target=_load_peaks, daemon=True).start()

    def _apply_waveform_peaks(self, path: Path, peaks: list[float], generation: int) -> None:
        if self._closing or generation != self._waveform_generation or not self.selected_memo or self.selected_memo.path != path:
            return
        wf = self.query_one("#player_waveform", PlayerWaveformWidget)
        fraction = self.player.get_position() / self.player.duration_sec if self.player.duration_sec > 0 else 0.0
        wf.set_data(peaks, fraction)

    def _update_player_title(self, current_pos: float) -> None:
        if not self.selected_memo:
            return
        lbl = self.query_one("#lbl_player_title", Label)
        pos_str = f"{int(current_pos)//60:02d}:{int(current_pos)%60:02d}"
        dur_str = self.selected_memo.formatted_duration
        remaining = max(0.0, self.player.duration_sec - current_pos)
        rem_str = f"-{int(remaining)//60:02d}:{int(remaining)%60:02d}"
        pct = int((current_pos / self.player.duration_sec) * 100) if self.player.duration_sec > 0 else 0
        tc = self.theme_colors
        lbl.update(
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
        self._update_hud()

    def action_seek_fwd(self) -> None:
        self.player.seek_relative(5.0)

    def action_seek_back(self) -> None:
        self.player.seek_relative(-5.0)

    def action_stop_player(self) -> None:
        self.player.stop()
        wf = self.query_one("#player_waveform", PlayerWaveformWidget)
        wf.set_data(wf.peaks, 0.0)
        self._update_player_title(0.0)
        self.set_status("Playback stopped", "info")

    def action_copy_path(self) -> None:
        if not self.selected_memo:
            return
        path_str = str(self.selected_memo.path.resolve())
        if copy_to_wayland_clipboard(path_str):
            self.notify(f"Copied path to Wayland clipboard:\n{path_str}", title="Clipboard")
            self.set_status("Copied path to clipboard", "success")
        else:
            self.notify("Failed to invoke wl-copy.", title="Clipboard Error", severity="error")

    def action_rename_selected(self) -> None:
        if not self.selected_memo:
            return
        memo = self.selected_memo

        def _on_rename_done(new_stem: str | None) -> None:
            if new_stem:
                updated = VoiceLibrary.rename_memo(memo, new_stem)
                if updated:
                    self.player.stop()
                    self.notify(f"Renamed to: {updated.name}", title="Success")
                    self.set_status(f"Renamed to {updated.name}", "success")
                    self._refresh_library()
                    self._select_memo(updated)
                else:
                    self.notify("Rename failed (invalid or duplicate name).", severity="error")

        self.push_screen(RenameMemoModal(memo.stem), _on_rename_done)

    def action_delete_selected(self) -> None:
        if not self.selected_memo:
            return
        memo = self.selected_memo

        def _on_delete_done(confirmed: bool) -> None:
            if confirmed:
                self.player.stop()
                if not VoiceLibrary.delete_memo(memo):
                    self.notify("Could not delete memo.", severity="error")
                    return
                self.notify(f"Deleted: {memo.name}", title="Deleted", severity="warning")
                self.set_status(f"Deleted {memo.name}", "warning")
                self.selected_memo = None
                self._refresh_library()

        self.push_screen(DeleteMemoModal(memo), _on_delete_done)

    def _on_player_state_change(self, state: PlayerState) -> None:
        self._safe_dispatch(self._update_player_buttons, state)

    def _update_player_buttons(self, state: PlayerState) -> None:
        btn = self.query_one("#btn_player_play", Button)
        if state == PlayerState.PLAYING:
            btn.label = "  Pause"
        else:
            btn.label = "  Play"

    def _on_player_eos(self) -> None:
        self._safe_dispatch(self._handle_player_eos)

    def _handle_player_eos(self) -> None:
        self.action_stop_player()

    # -------------------------------------------------------------------------
    # HARDWARE & SETTINGS PANE
    # -------------------------------------------------------------------------

    def _refresh_devices_view(self) -> None:
        sources, sinks = discover_pipewire_nodes()
        tc = self.theme_colors

        table_in = Table(
            title="󰍬  PIPEWIRE INPUT CAPTURE NODES (SOURCES)",
            border_style=tc["muted"],
            title_justify="left",
            width=78,
        )
        table_in.add_column("ID", justify="right", style="bold", width=4)
        table_in.add_column("Default", justify="center", width=12)
        table_in.add_column("Description", style=f"bold {tc['fg']}")
        table_in.add_column("Node Name", style=f"dim {tc['muted']}")

        if not sources:
            table_in.add_row("-", "", "No input sources detected", "")
        for src in sources:
            def_str = "● DEFAULT" if src.is_default else ""
            table_in.add_row(str(src.id), def_str, src.description, src.name)

        table_out = Table(
            title="󰋋  PIPEWIRE OUTPUT PLAYBACK NODES (SINKS)",
            border_style=tc["muted"],
            title_justify="left",
            width=78,
        )
        table_out.add_column("ID", justify="right", style="bold", width=4)
        table_out.add_column("Default", justify="center", width=12)
        table_out.add_column("Description", style=f"bold {tc['fg']}")
        table_out.add_column("Node Name", style=f"dim {tc['muted']}")

        if not sinks:
            table_out.add_row("-", "", "No output sinks detected", "")
        for snk in sinks:
            def_str = "● DEFAULT" if snk.is_default else ""
            table_out.add_row(str(snk.id), def_str, snk.description, snk.name)

        topo_panel = Panel(Text(f"Capture: PipeWire PulseAudio server → GStreamer\nMixing: audiomixer • level telemetry every 50 ms\nStorage: {RECORDINGS_DIR}\nWaveforms: JSON cache"), border_style=tc["muted"])

        self.query_one("#devices_info", Static).update(
            Panel(
                Group(table_in, Text("\n"), table_out, Text("\n"), topo_panel),
                border_style="none",
            )
        )

    # -------------------------------------------------------------------------
    # BUTTON DISPATCHER
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

    def on_unmount(self) -> None:
        self.engine.on_state_change = None
        self.engine.on_finished = None
        self.engine.on_error = None
        self.player.on_state_change = None
        self._closing = True
        self._waveform_cancel.set()
        if self.engine.pipeline:
            if self.engine.state in (RecordState.RECORDING, RecordState.PAUSED):
                self.engine.stop()
            else:
                self.engine.cancel()
        self.player.stop()

    def action_quit_app(self) -> None:
        if self.engine.state == RecordState.COUNTDOWN:
            self._abort_countdown()
        if self.engine.state in (RecordState.RECORDING, RecordState.PAUSED):
            self.engine.stop()
        self.player.stop()
        self.exit()


# =============================================================================
# MAIN ENTRYPOINT
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Dusky Voice Memos — terminal audio recorder")
    parser.add_argument("--version", action="version", version=VERSION)
    parser.parse_args()
    DuskyVoiceApp().run()


if __name__ == "__main__":
    main()
