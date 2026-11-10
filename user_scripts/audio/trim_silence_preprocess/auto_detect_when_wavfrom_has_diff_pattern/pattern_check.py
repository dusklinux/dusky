#!/usr/bin/env python3
"""Automated audio waveform and speech pattern detector (CPython 3.15+ / FFmpeg 9.0+).

Identifies the transition between pre-show (music, intro bumpers, countdowns, dead air)
and the main episode talking stream in livestream recordings without writing uncompressed
audio to disk, preventing SSD write amplification.
"""

import argparse
from collections.abc import Sequence
import ctypes
from dataclasses import asdict, dataclass, fields
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any

# PEP 810 explicit lazy imports (CPython 3.15+)
lazy from concurrent.futures import ProcessPoolExecutor, as_completed
lazy import glob
lazy import json
lazy from multiprocessing import get_context
lazy import shlex
lazy import tempfile
lazy import tomllib
lazy import unicodedata

type Seconds = float
type Decibels = float

AUDIO_EXTENSIONS: frozenset[str] = frozenset({
    ".m4a", ".aac", ".mp3", ".wav", ".opus", ".ogg", ".flac", ".mkv", ".mp4", ".webm",
})

DEFAULT_PREPROCESS_SCRIPT = Path(__file__).resolve().parents[1] / "preprocess.py"
DEFAULT_DETECTIONS_PATH = (Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
                           / "dusky/settings/audio_detection/detections.json")
DEFAULT_CONFIG_PATH = DEFAULT_DETECTIONS_PATH.with_name("config.toml")

# Linux prctl constants for guaranteeing child FFmpeg termination on parent death
_PR_SET_PDEATHSIG = 1
_libc = ctypes.CDLL(None, use_errno=True) if sys.platform.startswith("linux") else None
if _libc is not None and hasattr(_libc, "prctl"):
    _libc.prctl.argtypes = (
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    )
    _libc.prctl.restype = ctypes.c_int


def _bind_to_parent(parent_pid: int) -> None:
    if _libc is not None and hasattr(_libc, "prctl"):
        if _libc.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0) != 0:
            os._exit(127)
    if os.getppid() != parent_pid:
        os._exit(127)


def child_setup():
    """Prepare before fork; close the parent-death race after installing prctl.

    These CLI workers are single-threaded. Do not use this preexec callback
    from a multithreaded embedding; Python forbids that usage of preexec_fn.
    """
    parent_pid = os.getpid()

    def setup() -> None:
        _bind_to_parent(parent_pid)

    return setup


@dataclass(slots=True, frozen=True)
class DetectionProfile:
    """User-facing thresholds; decoding remains 8 kHz with 25 ms frames."""
    min_speech_minutes: float = 10.0
    min_speech_pct: float = 60.0
    min_speech_or_quiet_pct: float = 70.0
    max_music_pct: float = 25.0
    abs_silence_threshold: float = 0.005
    min_abs_silence_pct: float = 6.0
    speech_min_db: float = -44.0
    dead_air_silence_pct: float = 85.0
    dead_air_max_db: float = -50.0
    min_pause_ms: int = 175
    min_pauses: int = 2
    max_pauses: int = 18
    music_min_db: float = -40.0
    music_max_cadence_pct: float = 6.5
    music_max_pauses: int = 1
    boundary_quiet_sec: int = 20
    music_boundary_sec: int = 30
    refine_window_sec: int = 70

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{field.name} must be a finite number")
            if type(field.default) is int and type(value) is not int:
                raise ValueError(f"{field.name} must be an integer")
            if field.name.endswith("_pct") and not 0 <= value <= 100:
                raise ValueError(f"{field.name} must be between 0 and 100")
            if field.name.endswith(("_sec", "_ms", "_minutes")) and value <= 0:
                raise ValueError(f"{field.name} must be positive")
        if not 0 < self.abs_silence_threshold < 1.0:
            raise ValueError("abs_silence_threshold must be between 0 and 1")
        if self.min_speech_pct <= 0 or self.min_speech_or_quiet_pct <= 0:
            raise ValueError("min_speech_pct and min_speech_or_quiet_pct must be positive")
        if self.min_abs_silence_pct >= self.dead_air_silence_pct:
            raise ValueError("min_abs_silence_pct must be strictly less than dead_air_silence_pct")
        if self.speech_min_db > 0 or self.dead_air_max_db > 0 or self.music_min_db > 0:
            raise ValueError("dBFS thresholds must be nonpositive (<= 0.0)")
        if self.dead_air_max_db >= self.speech_min_db:
            raise ValueError("dead_air_max_db must be strictly less than speech_min_db")
        if self.min_speech_or_quiet_pct < self.min_speech_pct:
            raise ValueError("min_speech_or_quiet_pct must be >= min_speech_pct")
        if self.min_pause_ms % 25 or self.min_pause_ms > 5000:
            raise ValueError("min_pause_ms must be a multiple of 25 and <= 5000")
        if not 0 <= self.min_pauses <= self.max_pauses or self.music_max_pauses < 0:
            raise ValueError("Pause counts must be nonnegative and min_pauses <= max_pauses")
        if self.min_pauses * self.min_pause_ms >= 8000:
            raise ValueError("min_pauses * min_pause_ms must be < 8000 ms to allow speech cadence")
        if self.refine_window_sec < max(20, 2 * self.boundary_quiet_sec + 4):
            raise ValueError("refine_window_sec must allow the quiet boundary and following audio")


DEFAULT_PROFILE = DetectionProfile()


def load_profile(path: Path, name: str, *, explicit: bool = False) -> DetectionProfile:
    """Merge common defaults with a named TOML profile; reject misspelled keys."""
    if not path.exists():
        if explicit or name != "default":
            raise ValueError(f"Configuration file not found: {path}")
        return DEFAULT_PROFILE
    with path.open("rb") as source:
        data = tomllib.load(source)
    if data.keys() - {"defaults", "profiles"}:
        raise ValueError("Configuration accepts only [defaults] and [profiles.NAME] tables")
    defaults = data.get("defaults", {})
    profiles = data.get("profiles", {})
    if not isinstance(defaults, dict) or not isinstance(profiles, dict):
        raise ValueError("defaults and profiles must be TOML tables")
    if name != "default" and name not in profiles:
        raise ValueError(f"Unknown audio profile: {name}")
    overrides = profiles.get(name, {})
    if not isinstance(overrides, dict):
        raise ValueError(f"Profile {name} must be a TOML table")
    settings = defaults | overrides
    unknown = settings.keys() - {field.name for field in fields(DetectionProfile)}
    if unknown:
        raise ValueError(f"Unknown profile setting(s): {', '.join(sorted(unknown))}")
    return DetectionProfile(**settings)


@dataclass(slots=True, frozen=True)
class BlockMetrics:
    """Acoustic and cadence metrics for a single coarse analysis block."""
    sec: Seconds
    dur: Seconds
    abs_silence_pct: float
    cadence_silence_pct: float
    pauses: int
    rms_db: Decibels
    is_dead_air: bool
    is_speech: bool
    is_music: bool


@dataclass(slots=True, frozen=True)
class EpisodeDetection:
    """Structured detection result for a single livestream episode."""
    filepath: str
    filename: str
    total_duration_sec: Seconds
    show_start_sec: Seconds
    show_start_timestamp: str
    preshow_duration_sec: Seconds
    show_duration_sec: Seconds
    confidence_pct: float
    transition_signature: str
    analysis_wall_sec: Seconds
    mpv_command: str
    preprocess_command: str
    profile_name: str
    detection_settings: dict[str, Any]


def format_hms(seconds: Seconds) -> str:
    """Format seconds into HH:MM:SS without rounding past speech onset."""
    if not math.isfinite(seconds) or seconds < 0.0:
        return "00:00:00"
    total_sec = int(math.floor(seconds + 1e-6))
    hh, rem = divmod(total_sec, 3600)
    mm, ss = divmod(rem, 60)
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


def rms_to_db(rms: float) -> Decibels:
    """Convert linear RMS amplitude to dBFS with a -120.0 dB floor and NaN safety."""
    if not math.isfinite(rms) or rms <= 1e-6:
        return -120.0
    return 20.0 * math.log10(rms)


def _grapheme_width(text: str) -> int:
    widths = [2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
              for ch in text if unicodedata.category(ch) not in ("Cf", "Mn", "Me", "Mc", "Cc")]
    if not widths:
        return 0
    if "\ufe0f" in text or "\u20e3" in text or 2 in widths:
        return 2
    if "\u200d" in text and any(ord(ch) >= 0x2600 for ch in text):
        return 2
    if len(widths) == 2 and all(0x1F1E6 <= ord(ch) <= 0x1F1FF for ch in text):
        return 2
    return widths[0]


def display_width(text: str) -> int:
    """CJK/combining/emoji-aware columns; ambiguous widths depend on the terminal."""
    return sum(_grapheme_width(text[part.start:part.end])
               for part in unicodedata.iter_graphemes(text))


def format_cell(text: str, target_width: int, *, ellipsis: str = "...") -> str:
    """Truncate and right-pad a string to an exact terminal display column width."""
    cur_width = display_width(text)
    if target_width <= 0:
        return ""
    if cur_width <= target_width:
        return text + (" " * (target_width - cur_width))

    ell_width = display_width(ellipsis)
    if ell_width > target_width:
        return "." * target_width
    limit = max(0, target_width - ell_width)
    out_chars: list[str] = []
    used = 0
    for part in unicodedata.iter_graphemes(text):
        ch = text[part.start:part.end]
        ch_w = _grapheme_width(ch)
        if used + ch_w > limit:
            break
        out_chars.append(ch)
        used += ch_w

    truncated = "".join(out_chars) + ellipsis
    pad = max(0, target_width - display_width(truncated))
    return truncated + (" " * pad)


def read_full_into(stream: Any, buf: memoryview) -> int:
    """Read from a binary stream into buf until buf is full or EOF is reached."""
    total = 0
    target = len(buf)
    while total < target:
        n_read = stream.readinto(buf[total:])
        if not n_read:
            break
        total += n_read
    return total


def probe_audio_stream(filepath: Path) -> dict[str, Any]:
    """Retrieve audio stream and container metadata using ffprobe 9.0+."""
    cmd = [
        "ffprobe",
        "-hide_banner",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels,duration:format=duration,size",
        "-of", "json",
        str(filepath.resolve()),
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30.0,
            check=True,
            preexec_fn=child_setup(),
        )
    except subprocess.TimeoutExpired as err:
        raise RuntimeError(f"ffprobe timed out for {filepath}") from err
    except subprocess.CalledProcessError as err:
        detail = (err.stderr or "").strip()
        raise RuntimeError(f"ffprobe failed ({err.returncode}) for {filepath}: {detail}") from err
    try:
        return json.loads(proc.stdout)
    except ValueError as err:
        raise RuntimeError(f"ffprobe returned invalid JSON for {filepath}") from err


def refine_boundary_fine(
    filepath: Path,
    coarse_sec: Seconds,
    *,
    is_immediate_start: bool = False,
    profile: DetectionProfile = DEFAULT_PROFILE,
) -> tuple[Seconds, str]:
    """Inspect audio second-by-second in a window around coarse_sec for exact speech onset."""
    search_start = max(0.0, coarse_sec - profile.refine_window_sec // 2)
    search_duration = profile.refine_window_sec
    rate = 8000
    frame_samples = 200  # 25ms @ 8000 Hz
    frames_per_sec = rate // frame_samples  # 40 frames/sec
    silence_thresh_sq = frame_samples * profile.abs_silence_threshold**2

    af_filter = "aresample=async=1:first_pts=0" if search_start == 0.0 else "aresample=async=1"
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-nostats",
        "-v", "error",
        "-threads", "1",
        "-filter_threads", "1",
        "-accurate_seek",
        "-ss", f"{search_start:.3f}",
        "-t", f"{search_duration:.3f}",
        "-i", str(filepath.resolve()),
        "-map", "0:a:0",
        "-vn", "-sn", "-dn",
        "-af", af_filter,
        "-ac", "1",
        "-ar", str(rate),
        "-f", "f32le",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30.0,
            check=False,
            preexec_fn=child_setup(),
        )
    except subprocess.TimeoutExpired:
        return coarse_sec, "coarse fallback (timeout)"

    raw = proc.stdout
    valid_bytes = len(raw) & ~3
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg refinement failed ({proc.returncode}): {filepath}")
    if valid_bytes < rate * 2 * 4:
        return coarse_sec, "coarse fallback"

    samples = memoryview(raw)[:valid_bytes].cast("f")
    n_secs = len(samples) // rate
    if n_secs < 2:
        return coarse_sec, "coarse fallback"

    # Per-second tuple: (timestamp_sec, rms_db, silence_pct)
    sec_stats: list[tuple[Seconds, Decibels, float]] = []
    for s in range(n_secs):
        sec_view = samples[s * rate : (s + 1) * rate]
        sec_energy = 0.0
        quiet_frames = 0
        for f in range(frames_per_sec):
            f_view = sec_view[f * frame_samples : (f + 1) * frame_samples]
            f_energy = math.sumprod(f_view, f_view)
            if not math.isfinite(f_energy) or f_energy < 0.0:
                raise RuntimeError(f"Invalid non-finite PCM energy during refinement: {filepath}")
            sec_energy += f_energy
            if f_energy < silence_thresh_sq:
                quiet_frames += 1

        rms = math.sqrt(sec_energy / rate)
        rms_db = rms_to_db(rms)
        sil_pct = (quiet_frames / frames_per_sec) * 100.0
        sec_stats.append((search_start + s, rms_db, sil_pct))

    # Priority 1: A breath or a bumper's brief pause is not a pre-show boundary.
    # Require at least boundary_quiet_sec of quiet before emergence into modulated sound.
    music_db_gate = max(-38.0, profile.music_min_db)
    quiet_run = 0
    for i in range(len(sec_stats) - 2):
        t_cur, db_cur, _ = sec_stats[i]
        t_next, db_next, _ = sec_stats[i + 1]
        _, db_after, _ = sec_stats[i + 2]
        quiet_run = quiet_run + 1 if db_cur < -42.0 else 0
        if quiet_run >= profile.boundary_quiet_sec:
            post_window = sec_stats[i + 1 : i + 5]
            is_unmodulated_music = (
                len(post_window) >= 3
                and all(s_sil < 5.0 and s_db >= music_db_gate for _, s_db, s_sil in post_window[:3])
            )
            if not is_unmodulated_music and (
                (db_next >= -33.0 and db_after >= -38.0)
                or (db_next >= -42.0 and db_after >= -32.0)
                or (
                    i + 3 < len(sec_stats)
                    and db_next >= -42.0
                    and db_after >= -38.0
                    and sec_stats[i + 3][1] >= -32.0
                )
                or (
                    i + 3 < len(sec_stats)
                    and db_next >= -33.0
                    and db_after < -42.0
                    and sec_stats[i + 3][1] >= -33.0
                )
            ):
                return t_next, f"silence-to-speech transition ({db_cur:.1f} dB -> {db_next:.1f} dB)"

    # Priority 1b: The beginning of the file itself bounds a short leading quiet run
    # when coarse_sec is within the opening boundary window.
    if search_start == 0.0 and coarse_sec <= profile.boundary_quiet_sec + 10:
        min_early_sec = 0.0 if coarse_sec <= profile.boundary_quiet_sec else coarse_sec - 10.0
        non_quiet_skips = 0
        for i, (t_sec, db_val, _) in enumerate(sec_stats):
            if i >= profile.boundary_quiet_sec:
                break
            if db_val < -42.0:
                continue
            modulated = sum(10.0 <= quiet <= 75.0 and db >= -35.0
                            for _, db, quiet in sec_stats[i:i + 3]) >= 2
            if t_sec >= min_early_sec and db_val >= -34.0 and (is_immediate_start or modulated):
                kind = "immediate speech start" if i == 0 else "early speech onset"
                return t_sec, f"{kind} ({db_val:.1f} dB)"
            if t_sec >= min_early_sec and i + 1 < len(sec_stats) and db_val >= -42.0 and sec_stats[i + 1][1] >= -34.0:
                mod_next = sum(10.0 <= quiet <= 75.0 and db >= -35.0
                               for _, db, quiet in sec_stats[i + 1:i + 4]) >= 2
                if mod_next:
                    return t_sec, f"early speech onset ({db_val:.1f} dB)"
            non_quiet_skips += 1
            next_is_quiet = i + 1 < len(sec_stats) and sec_stats[i + 1][1] < -42.0
            if non_quiet_skips > 1 or not next_is_quiet:
                break

    # Priority 2: Music-to-speech cadence transition near coarse_sec (avoid matching
    # brief vocal gaps deep inside pre-show music >15s before coarse_sec).
    music_db_gate = max(-38.0, profile.music_min_db)
    min_music_idx = max(2, int(coarse_sec - search_start) - 15)
    for i in range(min_music_idx, len(sec_stats) - 3):
        prev_dense = all(
            sec_stats[j][2] < 5.0 and sec_stats[j][1] >= music_db_gate
            for j in range(i - 2, i)
        )
        if not prev_dense and i >= 4:
            # Allow a 1-2s fade/dip between dense music and speech onset
            prev_dense = (
                all(sec_stats[j][2] < 5.0 and sec_stats[j][1] >= music_db_gate for j in range(i - 4, i - 2))
                and all(sec_stats[j][1] < -35.0 for j in range(i - 2, i))
            )
        next_modulated = (
            sum(1 for j in range(i, i + 3) if 10.0 <= sec_stats[j][2] <= 75.0 and sec_stats[j][1] >= -35.0)
            >= 2
        )
        candidate_modulated = 10.0 <= sec_stats[i][2] <= 75.0 and sec_stats[i][1] >= -35.0
        if prev_dense and candidate_modulated and next_modulated:
            t_pt, db_pt, _ = sec_stats[i]
            return t_pt, f"music-to-speech cadence onset ({db_pt:.1f} dB)"

    # Priority 3: A short run of absolute digital silence (< -60 dB) before sustained
    # audio, for pre-show gaps shorter than boundary_quiet_sec that the coarse block
    # cannot resolve. Require >=5s of near-silence and >=3 active seconds after it.
    # Keep only candidates within +/-10s of the coarse estimate; prefer the longest
    # gap, then the closest candidate.
    candidates: list[tuple[int, float]] = []  # (quiet_run_seconds, onset_sec)
    quiet_run = 0
    for i, (t_sec, db_val, _sil) in enumerate(sec_stats):
        if db_val < -60.0:
            quiet_run += 1
            continue
        if quiet_run >= 5:
            following = sec_stats[i : i + 5]
            active = sum(1 for _, db, _ in following if db >= -35.0)
            if active >= 3:
                candidates.append((quiet_run, t_sec))
        quiet_run = 0
    best = None
    for run_len, onset_sec in candidates:
        if abs(onset_sec - coarse_sec) > 10.0:
            continue
        if (
            best is None
            or run_len > best[0]
            or (run_len == best[0] and abs(onset_sec - coarse_sec) < abs(best[1] - coarse_sec))
        ):
            best = (run_len, onset_sec)
    if best is not None:
        return best[1], f"digital-silence-to-speech transition ({best[0]}s gap)"

    return coarse_sec, "coarse estimate"


def _has_preceding_sustained_speech(
    blocks: Sequence[BlockMetrics],
    idx: int,
    profile: DetectionProfile = DEFAULT_PROFILE,
    min_speech_minutes: float | None = None,
) -> bool:
    """Check whether blocks prior to idx contain a sustained speech segment (not a brief bumper)."""
    if min_speech_minutes is None:
        min_speech_minutes = profile.min_speech_minutes
    block_dur = blocks[0].dur if blocks and blocks[0].dur > 0 else 10.0
    confirm_blocks = max(1, math.ceil((min_speech_minutes * 60.0) / block_dur))
    min_floor = min(6, max(2, confirm_blocks))
    min_sustained = min(14, max(min_floor, math.ceil(confirm_blocks * (profile.min_speech_pct / 100.0) * 0.4)))
    if idx < min_sustained:
        return False

    lookback_len = max(18, min_sustained + 4)
    req_ratio = min(0.65, profile.min_speech_pct / 100.0)
    relaxed_speech_blocks = max(
        min_sustained,
        math.ceil(confirm_blocks * (profile.min_speech_pct / 100.0) * 0.75),
    )
    relaxed_sq_blocks = math.ceil(confirm_blocks * (profile.min_speech_or_quiet_pct / 100.0) * 0.75)
    max_win_music = math.floor(confirm_blocks * (profile.max_music_pct / 100.0))

    # Split blocks[:idx] at qualifying dead-air (>= boundary_quiet_sec) and music
    # (>= music_boundary_sec) breaks so disconnected pre-show bumpers cannot merge.
    segments: list[Sequence[BlockMetrics]] = []
    seg_start = 0
    pos = 0
    while pos < idx:
        b = blocks[pos]
        if b.is_dead_air:
            run_end = pos + 1
            while run_end < idx and blocks[run_end].is_dead_air:
                run_end += 1
            if sum(x.dur for x in blocks[pos:run_end]) >= profile.boundary_quiet_sec:
                if pos > seg_start:
                    segments.append(blocks[seg_start:pos])
                seg_start = run_end
            pos = run_end
        elif b.is_music or b.cadence_silence_pct < 6.0:
            run_end = pos + 1
            while run_end < idx and (blocks[run_end].is_music or blocks[run_end].cadence_silence_pct < 8.0):
                run_end += 1
            if sum(x.dur for x in blocks[pos:run_end]) >= profile.music_boundary_sec:
                if pos > seg_start:
                    segments.append(blocks[seg_start:pos])
                seg_start = run_end
            pos = run_end
        else:
            pos += 1
    if seg_start < idx:
        segments.append(blocks[seg_start:idx])

    for seg in segments:
        end = len(seg)
        while end > 0 and not seg[end - 1].is_speech:
            end -= 1
        trimmed = seg[:end]
        if len(trimmed) < min_sustained:
            continue

        win_len = min(len(trimmed), lookback_len)
        max_music = max(2, math.floor(win_len * (profile.max_music_pct / 100.0)))
        req_speech = max(min_sustained, math.ceil(win_len * req_ratio))
        for start in range(0, len(trimmed) - win_len + 1):
            sub = trimmed[start : start + win_len]
            speech_cnt = sum(1 for x in sub if x.is_speech)
            music_cnt = sum(1 for x in sub if x.is_music)
            if speech_cnt >= req_speech and music_cnt <= max_music:
                return True

        # A contiguous break-free span at least confirm_blocks long with >=75% of the
        # confirmation speech density establishes an earlier host-speech half.
        if len(trimmed) >= confirm_blocks:
            for start in range(0, len(trimmed) - confirm_blocks + 1):
                win = trimmed[start : start + confirm_blocks]
                w_speech = sum(1 for x in win if x.is_speech)
                w_sq = sum(1 for x in win if not x.is_music)
                w_music = sum(1 for x in win if x.is_music)
                if (
                    w_speech >= relaxed_speech_blocks
                    and w_sq >= relaxed_sq_blocks
                    and w_music <= max_win_music
                ):
                    return True

    return False


def _trace_back_to_onset(
    blocks: Sequence[BlockMetrics],
    profile: DetectionProfile = DEFAULT_PROFILE,
    min_speech_minutes: float | None = None,
) -> int:
    """Trace backwards from the confirmed speech window to find the exact coarse block onset."""
    # Start from the latest confirmed speech block
    back_idx = len(blocks) - 1
    while back_idx > 0 and not blocks[back_idx].is_speech:
        back_idx -= 1
    speech_onset_idx = back_idx

    while back_idx > 0:
        if blocks[back_idx].is_speech:
            speech_onset_idx = back_idx
        prev_idx = back_idx - 1
        prev = blocks[prev_idx]

        if prev.is_dead_air:
            k = prev_idx
            while k >= 0 and blocks[k].is_dead_air:
                if k == 0:
                    return speech_onset_idx
                k -= 1
            gap_sec = sum(block.dur for block in blocks[k + 1:prev_idx + 1])
            # Preserve a later break within already sustained host speech.
            if gap_sec >= profile.boundary_quiet_sec and not _has_preceding_sustained_speech(
                blocks, k + 1, profile, min_speech_minutes
            ):
                return speech_onset_idx
            back_idx = k + 1

        elif prev.is_music or prev.cadence_silence_pct < 6.0:
            k = prev_idx
            while k >= 0 and (blocks[k].is_music or blocks[k].cadence_silence_pct < 8.0):
                if k == 0:
                    music_sec = sum(block.dur for block in blocks[0:prev_idx + 1])
                    if music_sec >= profile.music_boundary_sec:
                        return speech_onset_idx
                    return 0
                k -= 1
            music_sec = sum(block.dur for block in blocks[k + 1:prev_idx + 1])
            if music_sec >= profile.music_boundary_sec and not _has_preceding_sustained_speech(
                blocks, k + 1, profile, min_speech_minutes
            ):
                return speech_onset_idx
            back_idx = k + 1

        elif prev.is_speech:
            back_idx -= 1
            speech_onset_idx = back_idx

        else:
            # Ambiguous transition/fade block: check if preceded by a qualifying boundary
            if back_idx >= 2:
                b2 = blocks[back_idx - 2]
                if b2.is_dead_air:
                    k = back_idx - 2
                    while k >= 0 and blocks[k].is_dead_air:
                        if k == 0:
                            return speech_onset_idx
                        k -= 1
                    gap_sec = sum(block.dur for block in blocks[k + 1:back_idx - 1])
                    if gap_sec >= profile.boundary_quiet_sec and not _has_preceding_sustained_speech(
                        blocks, k + 1, profile, min_speech_minutes
                    ):
                        return speech_onset_idx
                    back_idx = k + 1
                    continue
                elif b2.is_music or b2.cadence_silence_pct < 6.0:
                    k = back_idx - 2
                    while k >= 0 and (blocks[k].is_music or blocks[k].cadence_silence_pct < 8.0):
                        if k == 0:
                            music_sec = sum(block.dur for block in blocks[0:back_idx - 1])
                            if music_sec >= profile.music_boundary_sec:
                                return speech_onset_idx
                            return 0
                        k -= 1
                    music_sec = sum(block.dur for block in blocks[k + 1:back_idx - 1])
                    if music_sec >= profile.music_boundary_sec and not _has_preceding_sustained_speech(
                        blocks, k + 1, profile, min_speech_minutes
                    ):
                        return speech_onset_idx
                    back_idx = k + 1
                    continue
            back_idx -= 1

    return 0


def analyze_episode(
    filepath: Path,
    *,
    max_scan_hours: float = 0.0,
    min_speech_minutes: float | None = None,
    sample_rate: int = 8000,
    block_sec: Seconds = 10.0,
    preprocess_script: Path = DEFAULT_PREPROCESS_SCRIPT,
    ram_dir: Path = Path("/dev/shm"),
    verbose: bool = False,
    profile: DetectionProfile = DEFAULT_PROFILE,
    profile_name: str = "default",
) -> EpisodeDetection:
    """Analyze stream audio blocks in RAM to identify where pre-show ends and talking begins."""
    start_wall = time.monotonic()
    if min_speech_minutes is None:
        min_speech_minutes = profile.min_speech_minutes
    if not math.isfinite(max_scan_hours) or max_scan_hours < 0:
        raise ValueError("max_scan_hours must be finite and nonnegative")
    if not math.isfinite(min_speech_minutes) or min_speech_minutes <= 0:
        raise ValueError("min_speech_minutes must be positive and finite")
    blocks_per_25ms = block_sec * 40.0
    if (sample_rate <= 0 or sample_rate % 40 or not math.isfinite(block_sec)
            or block_sec < 0.025 or not math.isclose(blocks_per_25ms, round(blocks_per_25ms), abs_tol=1e-9)):
        raise ValueError("Use a positive sample rate divisible by 40 and whole 25ms blocks")
    resolved_path = filepath.resolve()
    meta = probe_audio_stream(resolved_path)
    streams = meta.get("streams", [])
    if not streams:
        raise ValueError(f"No audio stream in {resolved_path}")

    stream_info = streams[0]
    candidate_durations: list[float] = []
    for raw_dur in (stream_info.get("duration"), meta.get("format", {}).get("duration")):
        if raw_dur is not None:
            try:
                val = float(raw_dur)
                if math.isfinite(val) and val > 0.0:
                    candidate_durations.append(val)
            except (TypeError, ValueError):
                pass
    total_duration = max(candidate_durations, default=0.0)
    if total_duration <= 0.0:
        raise ValueError(f"Unknown or invalid duration for {resolved_path}")

    scan_limit_sec = total_duration
    if max_scan_hours > 0.0:
        scan_limit_sec = min(total_duration, max_scan_hours * 3600.0)

    # Exact integer frame math: 25ms sub-frames @ 8000 Hz = 200 samples
    frame_ms = 25
    min_pause_ms = profile.min_pause_ms
    frame_samples = (sample_rate * frame_ms) // 1000
    block_samples = int(round(block_sec * sample_rate))
    bytes_per_block = block_samples * 4
    abs_silence_threshold = profile.abs_silence_threshold
    min_pause_frames = min_pause_ms // frame_ms

    cmd = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-nostats",
        "-v", "error",
        "-threads", "2",
        "-filter_threads", "1",
        "-t", f"{scan_limit_sec:.3f}",
        "-i", str(resolved_path),
        "-map", "0:a:0",
        "-vn", "-sn", "-dn",
        "-af", "aresample=async=1:first_pts=0",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-f", "f32le",
        "-",
    ]

    blocks: list[BlockMetrics] = []
    current_sec: Seconds = 0.0
    confirmed_speech_start: Seconds | None = None
    is_immediate_start = False
    transition_signature = "Unknown"
    confidence = 50.0

    confirm_blocks_count = max(1, math.ceil((min_speech_minutes * 60.0) / block_sec))
    min_speech_blocks = math.ceil(confirm_blocks_count * profile.min_speech_pct / 100)
    min_speech_or_quiet_blocks = math.ceil(confirm_blocks_count * profile.min_speech_or_quiet_pct / 100)
    max_music_blocks = math.floor(confirm_blocks_count * profile.max_music_pct / 100)

    # Reusable zero-copy receive buffer and preallocated frame RMS scratch list
    raw_buf = bytearray(bytes_per_block)
    raw_view = memoryview(raw_buf)
    max_frames_per_block = block_samples // frame_samples
    frame_rms_list = [0.0] * max_frames_per_block

    with subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        preexec_fn=child_setup(),
    ) as proc:
        assert proc.stdout is not None
        try:
            while True:
                n_bytes = read_full_into(proc.stdout, raw_view)
                valid_bytes = n_bytes & ~3
                if valid_bytes == 0:
                    if proc.wait() != 0:
                        raise RuntimeError(f"FFmpeg analysis failed ({proc.returncode}): {filepath}")
                    break

                samples = raw_view[:valid_bytes].cast("f")
                n_samples = len(samples)
                n_frames = n_samples // frame_samples
                if n_frames == 0:
                    continue  # Still consume EOF and check the decoder's exit status.

                analyzed_samples = n_frames * frame_samples
                dur = analyzed_samples / sample_rate
                block_energy = 0.0
                abs_low_frames = 0

                # Single pass: C-level math.sumprod over zero-copy memoryview slices
                for f in range(n_frames):
                    offset = f * frame_samples
                    sub = samples[offset : offset + frame_samples]
                    energy = math.sumprod(sub, sub)
                    if not math.isfinite(energy) or energy < 0.0:
                        raise RuntimeError(f"Invalid non-finite PCM energy during analysis: {filepath}")
                    block_energy += energy
                    sub_rms = math.sqrt(energy / frame_samples)
                    frame_rms_list[f] = sub_rms
                    if sub_rms < abs_silence_threshold:
                        abs_low_frames += 1

                # Compute adaptive envelope dip threshold for speech-over-background-music
                sorted_rms = sorted(frame_rms_list[:n_frames])
                p85_rms = sorted_rms[int(n_frames * 0.85)]
                if p85_rms >= 0.020:
                    eff_threshold = max(abs_silence_threshold, min(0.035, p85_rms * 0.22))
                else:
                    eff_threshold = abs_silence_threshold

                cadence_low_frames = 0
                pauses = 0
                in_silence = False
                silence_frames = 0

                for f in range(n_frames):
                    if frame_rms_list[f] < eff_threshold:
                        cadence_low_frames += 1
                        silence_frames += 1
                        if silence_frames >= min_pause_frames and not in_silence:
                            in_silence = True
                            pauses += 1
                    else:
                        silence_frames = 0
                        in_silence = False

                abs_silence_pct = (abs_low_frames / n_frames) * 100.0
                cadence_silence_pct = (cadence_low_frames / n_frames) * 100.0
                block_rms = math.sqrt(block_energy / (n_frames * frame_samples))
                block_rms_db = rms_to_db(block_rms)

                # Mutually exclusive block classification
                is_dead_air = (abs_silence_pct >= profile.dead_air_silence_pct
                               or block_rms_db < profile.dead_air_max_db)
                is_speech = (
                    not is_dead_air
                    and block_rms_db >= profile.speech_min_db
                    and abs_silence_pct >= profile.min_abs_silence_pct
                    and (
                        (10.0 <= cadence_silence_pct < 80.0 and profile.min_pauses <= pauses <= profile.max_pauses)
                        or (8.0 <= cadence_silence_pct < 80.0 and profile.min_pauses + 1 <= pauses <= profile.max_pauses)
                    )
                )
                is_music = (
                    not is_dead_air
                    and not is_speech
                    and cadence_silence_pct < profile.music_max_cadence_pct
                    and pauses <= profile.music_max_pauses
                    and block_rms_db >= profile.music_min_db
                )

                blocks.append(
                    BlockMetrics(
                        sec=current_sec,
                        dur=dur,
                        abs_silence_pct=abs_silence_pct,
                        cadence_silence_pct=cadence_silence_pct,
                        pauses=pauses,
                        rms_db=block_rms_db,
                        is_dead_air=is_dead_air,
                        is_speech=is_speech,
                        is_music=is_music,
                    )
                )

                # Evaluate sliding confirmation window
                if len(blocks) >= confirm_blocks_count and confirmed_speech_start is None:
                    win_start_idx = len(blocks) - confirm_blocks_count
                    window = blocks[win_start_idx:]
                    speech_count = sum(1 for b in window if b.is_speech)
                    speech_or_quiet = sum(1 for b in window if b.is_speech or b.is_dead_air)
                    music_count = sum(1 for b in window if b.is_music)
                    tail_active = window[-1].is_speech or (len(window) >= 2 and window[-2].is_speech)

                    if (
                        tail_active
                        and speech_count >= min_speech_blocks
                        and speech_or_quiet >= min_speech_or_quiet_blocks
                        and music_count <= max_music_blocks
                    ):
                        onset_idx = _trace_back_to_onset(blocks, profile, min_speech_minutes)
                        post_onset_speech = sum(1 for b in blocks[onset_idx:] if b.is_speech)
                        if onset_idx <= win_start_idx or post_onset_speech >= min_speech_blocks:
                            at_eof = n_bytes < bytes_per_block or (
                                hasattr(proc.stdout, "peek") and not proc.stdout.peek(1)
                            )
                            if (at_eof and proc.wait() != 0) or proc.poll() not in (None, 0):
                                raise RuntimeError(f"FFmpeg analysis failed ({proc.returncode}): {filepath}")
                            confirmed_speech_start = blocks[onset_idx].sec
                            is_immediate_start = onset_idx == 0 and blocks[0].is_speech
                            confidence = 95.0

                            if verbose:
                                print(
                                    f"[{resolved_path.name[:40]}] Confirmed sustained speech at "
                                    f"~{confirmed_speech_start:.1f}s, stopping scan early."
                                )
                            break

                current_sec += dur
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()

    # Refine coarse timestamp to 1-second resolution
    if confirmed_speech_start is not None:
        refined_sec, sig = refine_boundary_fine(
            resolved_path,
            confirmed_speech_start,
            is_immediate_start=is_immediate_start,
            profile=profile,
        )
        final_start_sec = max(0.0, min(refined_sec, total_duration))
        transition_signature = sig
        if "silence-to-speech" in sig or "immediate speech" in sig:
            confidence = 98.0
        elif "early speech onset" in sig or "music-to-speech" in sig:
            confidence = 93.0
        else:
            confidence = 70.0
    else:
        final_start_sec = 0.0
        transition_signature = "No pre-show transition detected (fallback to 00:00:00)"
        confidence = 50.0

    preshow_dur = final_start_sec
    show_dur = max(0.0, total_duration - final_start_sec)
    hms = format_hms(final_start_sec)
    wall_elapsed = time.monotonic() - start_wall

    quoted_src = shlex.quote(str(resolved_path))
    quoted_script = shlex.quote(str(preprocess_script))
    quoted_out = shlex.quote(str(ram_dir / f"{resolved_path.stem}-max-plus.wav"))

    mpv_cmd = (f"mpv --cache-on-disk=no --script-opts-append=dusky_silence-enabled=no "
               f"--start={hms} {quoted_src}")
    preprocess_cmd = f"python3 {quoted_script} {quoted_src} {quoted_out} --start {hms}"

    return EpisodeDetection(
        filepath=str(resolved_path),
        filename=resolved_path.name,
        total_duration_sec=round(total_duration, 2),
        show_start_sec=round(final_start_sec, 2),
        show_start_timestamp=hms,
        preshow_duration_sec=round(preshow_dur, 2),
        show_duration_sec=round(show_dur, 2),
        confidence_pct=round(confidence, 1),
        transition_signature=transition_signature,
        analysis_wall_sec=round(wall_elapsed, 2),
        mpv_command=mpv_cmd,
        preprocess_command=preprocess_cmd,
        profile_name=profile_name,
        detection_settings={**asdict(profile), "min_speech_minutes": min_speech_minutes},
    )


def collect_target_files(inputs: Sequence[str], *, recursive: bool = False) -> list[Path]:
    """Collect audio files safely from literal paths, directories, or glob expressions."""
    found: dict[Path, None] = {}

    for item in inputs:
        try:
            p = Path(item).expanduser()
            is_file = p.is_file()
            is_dir = False if is_file else p.is_dir()
        except OSError:
            continue
        if is_file:
            if p.suffix.lower() in AUDIO_EXTENSIONS:
                try:
                    found[p.resolve()] = None
                except OSError:
                    continue
        elif is_dir:
            try:
                iterator = p.rglob("*") if recursive else p.iterdir()
                for sub in iterator:
                    try:
                        if sub.is_file() and sub.suffix.lower() in AUDIO_EXTENSIONS:
                            found[sub.resolve()] = None
                    except OSError:
                        continue
            except OSError:
                continue
        else:
            # Handle multi-segment glob expressions safely
            expanded = str(p)
            for match_str in glob.glob(expanded, recursive=recursive):
                match = Path(match_str)
                try:
                    if match.is_file() and match.suffix.lower() in AUDIO_EXTENSIONS:
                        found[match.resolve()] = None
                except OSError:
                    continue

    return sorted(found.keys(), key=lambda path: (path.name, str(path)))


def print_detection_table(detections: Sequence[EpisodeDetection]) -> None:
    """Format results; confidence_pct is a heuristic score, not a probability."""
    line_width = 124
    print("\n" + "=" * line_width)
    header = (
        f"{format_cell('EPISODE / FILE', 48)} | "
        f"{'TOTAL':<8} | "
        f"{'START':<8} | "
        f"{'SHOW DUR':<8} | "
        f"{'SCORE':<5} | "
        f"{'SIGNATURE'}"
    )
    print(header)
    print("-" * line_width)

    for d in detections:
        name_cell = format_cell(d.filename, 48)
        total_sec_int = max(0, int(round(d.total_duration_sec)))
        start_sec_int = max(0, int(math.floor(d.show_start_sec + 1e-6)))
        total_hms = format_hms(total_sec_int)
        show_hms = format_hms(max(0, total_sec_int - start_sec_int))
        conf_str = f"{d.confidence_pct:.0f}%"
        sig_cell = format_cell(d.transition_signature, 24, ellipsis="..")
        print(
            f"{name_cell} | {total_hms:<8} | {d.show_start_timestamp:<8} | "
            f"{show_hms:<8} | {conf_str:<5} | {sig_cell}"
        )

    print("=" * line_width)
    print(f"Total analyzed episodes: {len(detections)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detect actual show start timestamp in livestream recordings to bypass pre-shows."
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        default=["."],
        help="Audio file paths, directories, or glob patterns (default: current directory)",
    )
    parser.add_argument("-r", "--recursive", action="store_true", help="Search directories recursively")
    parser.add_argument(
        "--max-scan-hours",
        type=float,
        default=0.0,
        help="Max hours from beginning of file to scan for show start (default: 0.0, 0 = unlimited/full file)",
    )
    parser.add_argument(
        "--min-speech-minutes",
        type=float,
        help="Override profile confirmation window in minutes (default profile: 10.0, >=60%% speech)",
    )
    parser.add_argument("--limit", type=int, default=0, help="Limit number of files to process (0 = all)")
    parser.add_argument(
        "-j",
        "--workers",
        type=int,
        default=min(4, os.process_cpu_count() or 1),
        help="Worker processes (default: min(4, available CPUs))",
    )
    parser.add_argument("--json-out", type=Path, default=DEFAULT_DETECTIONS_PATH,
                        help="Save results JSON (default: %(default)s)")
    parser.add_argument("--config", type=Path,
                        help=f"Profile TOML file (default: {DEFAULT_CONFIG_PATH})")
    parser.add_argument("--profile", default="default", help="Named audio profile (default: %(default)s)")
    parser.add_argument("--ram-dir", type=Path, default=Path("/mnt/zram1") if Path("/mnt/zram1").is_dir() else Path("/dev/shm"),
                        help="RAM/ZRAM output directory for preprocess commands (default: %(default)s)")
    parser.add_argument(
        "--print-preprocess-commands",
        action="store_true",
        help="Print preprocess.py command for each file",
    )
    parser.add_argument(
        "--print-mpv-commands",
        action="store_true",
        help="Print mpv verification command for each file",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose progress logs")
    args = parser.parse_args()
    if not math.isfinite(args.max_scan_hours) or args.max_scan_hours < 0:
        parser.error("--max-scan-hours must be finite and nonnegative")
    if args.min_speech_minutes is not None and (
            not math.isfinite(args.min_speech_minutes) or args.min_speech_minutes <= 0):
        parser.error("--min-speech-minutes must be positive and finite")
    if args.workers < 1 or args.limit < 0:
        parser.error("--workers must be positive and --limit nonnegative")
    try:
        profile = load_profile((args.config or DEFAULT_CONFIG_PATH).expanduser(), args.profile,
                               explicit=args.config is not None)
    except (OSError, ValueError, OverflowError) as error:
        parser.error(str(error))

    files = collect_target_files(args.inputs, recursive=args.recursive)
    if args.limit > 0:
        files = files[: args.limit]
    if not files:
        print(f"No matching audio files found in: {args.inputs}", file=sys.stderr)
        sys.exit(1)

    workers = max(1, min(args.workers, len(files)))
    print(f"Discovered {len(files)} audio file(s) for pattern analysis. Workers: {workers}")

    detections: list[EpisodeDetection] = []

    if workers == 1:
        for idx, f in enumerate(files, 1):
            if args.verbose or len(files) > 1:
                print(f"[{idx}/{len(files)}] Analyzing: {format_cell(f.name, 60).strip()}...")
            try:
                det = analyze_episode(
                    f,
                    max_scan_hours=args.max_scan_hours,
                    min_speech_minutes=args.min_speech_minutes,
                    verbose=args.verbose,
                    profile=profile,
                    profile_name=args.profile,
                    ram_dir=args.ram_dir,
                )
                detections.append(det)
                if not args.verbose:
                    print(
                        f"  -> Detected start: {det.show_start_timestamp} "
                        f"({det.transition_signature}) [{det.analysis_wall_sec:.1f}s]"
                    )
            except Exception as exc:
                print(f"Error analyzing {f}: {exc}", file=sys.stderr)
    else:
        # Spawn gives workers this CLI as their real Linux parent. Forkserver
        # workers would survive its death, leaving their decoders running too.
        executor = ProcessPoolExecutor(max_workers=workers, mp_context=get_context("spawn"),
                                       initializer=_bind_to_parent, initargs=(os.getpid(),))
        try:
            future_to_file = {
                executor.submit(
                    analyze_episode,
                    f,
                    max_scan_hours=args.max_scan_hours,
                    min_speech_minutes=args.min_speech_minutes,
                    verbose=False,
                    profile=profile,
                    profile_name=args.profile,
                    ram_dir=args.ram_dir,
                ): f
                for f in files
            }
            completed = 0
            for future in as_completed(future_to_file):
                completed += 1
                orig_file = future_to_file[future]
                try:
                    det = future.result()
                    detections.append(det)
                    short_disp = format_cell(det.filename, 50).strip()
                    print(
                        f"[{completed}/{len(files)}] {short_disp}: "
                        f"Start @ {det.show_start_timestamp} [{det.analysis_wall_sec:.1f}s]"
                    )
                except Exception as exc:
                    print(f"[{completed}/{len(files)}] Failed {orig_file.name}: {exc}", file=sys.stderr)
        except BaseException:
            executor.terminate_workers()
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)

    detections.sort(key=lambda d: (d.filename, d.filepath))
    print_detection_table(detections)

    if len(detections) != len(files):
        sys.exit(1)  # Preserve the previous complete report on a failed batch.

    if args.json_out:
        json_path = args.json_out.expanduser().resolve()
        json_path.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(d) for d in detections]
        # Publish one complete report; interruption must not truncate settings or leak temp dirs.
        temp_dir = tempfile.TemporaryDirectory(prefix=".detections-", dir=json_path.parent)
        try:
            report = Path(temp_dir.name) / "detections.json"
            report.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            report.replace(json_path)
        finally:
            deferred_sig = False

            def _defer_sig(_signum: int, _frame: Any) -> None:
                nonlocal deferred_sig
                deferred_sig = True

            prev_int = signal.signal(signal.SIGINT, _defer_sig)
            prev_term = signal.signal(signal.SIGTERM, _defer_sig)
            try:
                temp_dir.cleanup()
            finally:
                signal.signal(signal.SIGINT, prev_int)
                signal.signal(signal.SIGTERM, prev_term)
            if deferred_sig:
                raise KeyboardInterrupt
        print(f"\nSaved structured results to: {json_path}")

    if args.print_mpv_commands:
        print("\nMPV Verification Commands:")
        print("-" * 80)
        for d in detections:
            print(d.mpv_command)
        print("-" * 80)

    if args.print_preprocess_commands:
        print("\nPreprocess Commands (copy/execute when verified):")
        print("-" * 80)
        for d in detections:
            print(d.preprocess_command)
        print("-" * 80)


if __name__ == "__main__":
    def _handle_termination(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handle_termination)
    signal.signal(signal.SIGTERM, _handle_termination)
    try:
        main()
    except KeyboardInterrupt:
        print("\nPattern analysis interrupted; worker processes terminated cleanly.", file=sys.stderr)
        sys.exit(130)
