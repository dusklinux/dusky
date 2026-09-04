#!/usr/bin/env python3
"""
Dusky Universal Media Downloader
Platform: Arch Linux (rolling) | Python 3.14+ | yt-dlp 2026.08+ | FFmpeg 9+
Architecture: Port of Open Video Downloader (OVD) engine — TUI edition.

Verified against (Sep 2026):
- yt-dlp 2026.08.19 (`--progress-template` RAW protocol, `-S` format-sort,
  `--merge-output-format`/`--remux-video`, `--embed-metadata`/`--embed-chapters`)
- FFmpeg n9.0.1 (mp4 muxer defaults: h264 video / aac audio)
- Python 3.14.7 (`subprocess.Popen(process_group=0)` == setpgid(0,0) isolation)

Storage policy: writes exclusively to /mnt/zram1/dusky_ytdlp with /dev/shm
fallback. No username is ever hardcoded; paths are absolute system paths.
"""

from __future__ import annotations

import importlib.util
import os
import queue
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable, Final
import uuid

# ==============================================================================
# PHASE 1: DEPENDENCY VERIFICATION & ARCH LINUX AUTO-ELEVATION
# ==============================================================================

# fzf is OPTIONAL (used for picker UX when present, never required).
REQUIRED_SYSTEM_BINARIES: Final[dict[str, str]] = {
    "ffmpeg": "ffmpeg",
    "ffprobe": "ffmpeg",  # ffprobe ships inside the Arch `ffmpeg` package
}

REQUIRED_PYTHON_MODULES: Final[dict[str, str]] = {
    "rich": "python-rich",
    "yt_dlp": "yt-dlp",
    "prompt_toolkit": "python-prompt_toolkit",
}

MIN_PYTHON: Final[tuple[int, int]] = (3, 14)


def bootstrap_dependencies() -> None:
    """Detects missing Arch Linux packages and auto-elevates via pacman."""
    if sys.version_info < MIN_PYTHON:
        sys.stderr.write(
            f"[-] Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required, "
            f"found {sys.version.split()[0]}.\n"
        )
        sys.exit(1)

    missing: list[str] = []

    for binary, pkg in REQUIRED_SYSTEM_BINARIES.items():
        if shutil.which(binary) is None and pkg not in missing:
            missing.append(pkg)

    for mod, pkg in REQUIRED_PYTHON_MODULES.items():
        if importlib.util.find_spec(mod) is None and pkg not in missing:
            missing.append(pkg)

    if not missing:
        return

    print("\n[!] Missing system packages detected on Arch Linux:")
    for pkg in missing:
        print(f"    - {pkg}")
    print("[*] Escalating privileges to execute: sudo pacman -S --needed\n")

    cmd = ["sudo", "pacman", "-S", "--needed", *missing]
    try:
        res = subprocess.run(cmd, check=False)
        if res.returncode != 0:
            sys.stderr.write("\n[-] Pacman installation was cancelled or failed.\n")
            sys.exit(res.returncode)
    except Exception as err:
        sys.stderr.write(f"\n[-] Elevation error: {err}\n")
        sys.exit(1)

    print("[+] Dependencies resolved. Initializing script environment...\n")
    os.execv(sys.executable, [sys.executable, *sys.argv])


bootstrap_dependencies()

# ==============================================================================
# PHASE 2: MODULE IMPORTS & LIFECYCLE MANAGEMENT
# ==============================================================================

import argparse
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qs, unquote, urlsplit

import yt_dlp
from yt_dlp.utils import PlaylistEntries
from rich import box
from rich.align import Align
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.prompt import Prompt
from rich.table import Table

try:
    from prompt_toolkit import prompt as pt_prompt
    from prompt_toolkit.application.current import get_app
    from prompt_toolkit.completion import PathCompleter
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.history import FileHistory, InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings

    _PT_KEYS = KeyBindings()

    @_PT_KEYS.add("c-d", filter=Condition(lambda: get_app().current_buffer.text == ""))
    def _pt_eof_on_empty(event) -> None:
        event.app.exit(exception=EOFError())

    _PT_AVAILABLE = True
except ImportError:  # pragma: no cover — bootstrap installs it via pacman
    _PT_AVAILABLE = False

console: Final[Console] = Console()

# ==============================================================================
# USER CONFIG — tune these defaults to taste (wizard prompt + CLI flags can
# still override per run).
# ==============================================================================
# How many files download at once when the queue holds several items
# (playlist / multi-URL / batch file). The wizard asks each time; `-N` /
# `--concurrent` overrides non-interactively. Capped interactively at
# MAX_CONCURRENT_DOWNLOADS to protect ZRAM/RAM (each job holds its own
# yt-dlp + FFmpeg children); an explicit CLI flag may exceed the cap up to
# the queue length.
DEFAULT_CONCURRENT_DOWNLOADS: Final[int] = 3
MAX_CONCURRENT_DOWNLOADS: Final[int] = 8

PRIMARY_ZRAM_TARGET: Final[Path] = Path("/mnt/zram1/dusky_ytdlp")
RAM_TMPFS_FALLBACK: Final[Path] = Path("/dev/shm/dusky_ytdlp")

ACTIVE_PROCESS_GROUPS: set[int] = set()
_ACTIVE_PG_LOCK: Final[threading.Lock] = threading.Lock()

# --- Mid-run skip/abort state (see run_queue + control thread below) ---
# pgids the user asked to skip (per-worker keys or single Ctrl-C). Workers
# whose process dies with their pgid in this set report Skipped, not Failed.
USER_SKIPPED_PGIDS: set[int] = set()
_SKIP_LOCK: Final[threading.Lock] = threading.Lock()
# Set on double Ctrl-C / `q` key: running jobs are killed, pending futures
# cancelled, and main exits 130 after printing the partial Extraction Log.
ABORT_ALL_EVENT: Final[threading.Event] = threading.Event()
# True only inside run_queue's download phase (wizard prompts keep the old
# kill-everything-and-exit behaviour). Guarded by _ACTIVE_PG_LOCK.
_QUEUE_RUNNING: bool = False
_LAST_SIGINT_NS: int = 0
# Double Ctrl-C window: second SIGINT within this many seconds aborts all.
ABORT_WINDOW_SECS: Final[float] = 3.0
# Live slot bookkeeping for per-worker skip keys: ordered task ids (worker
# number = position+1) plus pgid/title per slot. Guarded by _SLOT_LOCK.
_SLOT_PGIDS: dict[object, int] = {}
_SLOT_TITLES: dict[object, str] = {}
_SLOT_LOCK: Final[threading.Lock] = threading.Lock()
# One-shot skip flags per worker NUMBER (1-based). Set by digit keys / `s` /
# single Ctrl-C; consumed at the next spawn gate or discarded when the current
# occupant ends — the invariant is a flag NEVER survives its occupant, so an
# innocent later job can never inherit a skip. Guarded by _SLOT_LOCK.
_SLOT_SKIP: set[int] = set()
# Worker numbers currently holding a slot (claimed, maybe still probing with
# no yt-dlp process yet). Lets `s`/Ctrl-C mark starting jobs instead of
# reporting "nothing active" while they escape. Guarded by _SLOT_LOCK.
_BUSY_SLOT_NOS: set[int] = set()
# Fallback one-shot for the truly idle instant (no active pgid, no busy slot
# — e.g. a lone sequential probe): the next spawn without a slot number
# consumes it and reports Skipped. Concurrent slots use _SLOT_SKIP instead.
SKIP_NEXT_ONE_SHOT: Final[threading.Event] = threading.Event()


def _sigwrite(msg: str) -> None:
    """Write to stderr without touching Rich locks (async-signal-safe).

    A signal handler runs in the main thread at an arbitrary bytecode — the
    main thread may be *inside* `console.print` holding its lock, so the
    handler must never call `console.print` (deadlock). `os.write` is safe.
    """
    try:
        os.write(2, (msg + "\n").encode("utf-8", errors="replace"))
    except OSError:
        pass


def _snapshot_pgids() -> list[int]:
    with _ACTIVE_PG_LOCK:
        return list(ACTIVE_PROCESS_GROUPS)


def _kill_pgids(pgids: list[int], sig: int = signal.SIGTERM) -> int:
    """SIGTERM a list of process groups; returns how many signals were sent."""
    sent = 0
    for pgid in pgids:
        if pgid <= 1:
            continue
        try:
            os.killpg(pgid, sig)
            sent += 1
        except OSError:
            pass
    return sent


def request_skip_pgids(pgids: list[int]) -> int:
    """Mark pgids as user-skipped and SIGTERM them. Returns signals sent."""
    with _SKIP_LOCK:
        USER_SKIPPED_PGIDS.update(pgids)
    return _kill_pgids(pgids)


def request_abort_all() -> None:
    """Flag a full abort and SIGTERM everything currently downloading."""
    ABORT_ALL_EVENT.set()
    with _SKIP_LOCK:
        USER_SKIPPED_PGIDS.update(_snapshot_pgids())
    _kill_pgids(_snapshot_pgids())


def skip_slot_by_number(slots: list[object], worker_no: int) -> str:
    """Skip worker `worker_no` (1-based); returns a human status message.

    One-shot and escape-free: the slot number is flagged AND any live process
    group is SIGTERMed. A job still probing (no pgid yet) hits the flag at
    its spawn gate and reports Skipped without starting; a live one dies and
    maps to Skipped. Pure lookup + signal logic (no TTY reads), so it is
    unit-testable and shared by the key-reader thread.
    """
    if worker_no < 1 or worker_no > len(slots):
        return f"No worker {worker_no} (1–{len(slots)})."
    slot = slots[worker_no - 1]
    with _SLOT_LOCK:
        _SLOT_SKIP.add(worker_no)  # sticky until this occupant ends
        pgid = _SLOT_PGIDS.get(slot)
        title = _SLOT_TITLES.get(slot, "")
        busy = worker_no in _BUSY_SLOT_NOS
    label = f" ({title})" if title else ""
    if pgid is not None:
        n = request_skip_pgids([pgid])
        if n:
            return f"Skipping worker {worker_no}{label}."
        # Process vanished between lookup and kill: the sticky flag still
        # guards the gate, but report honestly.
        return f"Worker {worker_no}{label} just finished; marked if it restarts."
    if busy:
        return f"Worker {worker_no}{label} is starting — will skip it."
    # Slot idle: nothing to target, so drop the flag we just added.
    with _SLOT_LOCK:
        _SLOT_SKIP.discard(worker_no)
    return f"Worker {worker_no} is idle — nothing to skip."


def skip_all_active() -> str:
    """Skip every active download plus every starting (probing) one.

    Shared by the `s` key and single Ctrl-C. Returns a status message; the
    multi-skip variant carries the tip about per-worker digits exactly when
    the user pays for the broadcast (skipped >1) — i.e. precisely when the
    tip is relevant.
    """
    pgids = _snapshot_pgids()
    with _SLOT_LOCK:
        busy = sorted(_BUSY_SLOT_NOS)
        _SLOT_SKIP.update(busy)
    if pgids:
        request_skip_pgids(pgids)
    if not pgids and not busy:
        SKIP_NEXT_ONE_SHOT.set()
        return "Nothing running right now — next download will be skipped."
    bits = []
    if pgids:
        bits.append(f"{len(pgids)} active")
    starting = [b for b in busy]
    if starting:
        bits.append(f"{len(starting)} starting")
    msg = f"Skipping {' + '.join(bits)} download(s)."
    if len(pgids) > 1:
        msg += " (Tip: press 1, 2, 3... to skip just one worker.)"
    return msg


def global_signal_handler(signum: int, frame: object) -> None:
    """Single Ctrl-C skips the current wave; double (within 3s) aborts all.

    Outside the queue phase (wizard prompts, probing setup) there is nothing
    skippable, so the legacy kill-everything-and-exit-130 applies. Inside the
    queue phase the handler NEVER calls sys.exit — not even when no process
    group exists yet (all workers probing): it flags starting slots / arms
    the one-shot instead, letting run_queue unwind naturally (partial log,
    exit 130 on abort). This closes both the probe-gap kill-all and the old
    `SystemExit`-during-interpreter-shutdown traceback.
    """
    global _LAST_SIGINT_NS
    now_ns = time.monotonic_ns()
    if signum == signal.SIGTERM:
        pgids = _snapshot_pgids()
        _kill_pgids(pgids)
        _sigwrite("\n[!] Terminated: process tree killed.")
        sys.exit(130)

    with _ACTIVE_PG_LOCK:
        running = _QUEUE_RUNNING
    if not running:
        # Wizard/input phase: nothing to skip.
        _kill_pgids(_snapshot_pgids())
        _sigwrite("\n[!] Interrupted: terminating process tree...")
        sys.exit(130)

    elapsed = (now_ns - _LAST_SIGINT_NS) / 1e9
    if elapsed < 0.25:
        # Debounce duplicate SIGINT / character events from the same physical keypress
        return
    _LAST_SIGINT_NS = now_ns
    if elapsed < ABORT_WINDOW_SECS:
        request_abort_all()
        _sigwrite("\n[!] Aborting everything (2nd Ctrl-C)...")
        return
    _sigwrite(f"\n[!] {skip_all_active()} (Ctrl-C again within 3s aborts all.)")


signal.signal(signal.SIGINT, global_signal_handler)
signal.signal(signal.SIGTERM, global_signal_handler)


def has_fzf() -> bool:
    return shutil.which("fzf") is not None


def fzf_pick(prompt: str, choices: list[str], default: str) -> str:
    """Single-select via fzf when interactive, else Rich Prompt. Never crashes.

    fzf requires a real TTY; with piped/redirected stdin (or no TTY at all)
    it would silently return the default WITHOUT consuming stdin, shifting
    every subsequent wizard prompt. Gate on isatty so scripts/pipes behave.

    No pre-filled query: all options are shown and Enter selects the
    highlighted (first) entry.
    """
    if has_fzf() and sys.stdin.isatty() and sys.stdout.isatty():
        try:
            proc = subprocess.run(
                ["fzf", "--prompt", f"{prompt}> ", "--height", "40%",
                 "--reverse", "--no-multi"],
                input="\n".join(choices) + "\n",
                stdout=subprocess.PIPE,
                text=True,
                timeout=120,
            )
            if proc.returncode == 130:
                console.print("\n[bold red][!] Interrupted.[/]")
                sys.exit(130)
            picked = (proc.stdout or "").strip()
            if picked and picked in choices:
                return picked
            # User escaped fzf (non-zero) -> fall through to default
            if not picked:
                return default
        except Exception:
            pass
    return Prompt.ask(
        f"\n[bold green]?[/] {prompt}",
        choices=choices,
        default=default,
    )


def fzf_multi_pick(prompt: str, choices: list[str]) -> list[str] | None:
    """Multi-select via `fzf --multi`; None when fzf/TTY is unavailable.

    Returns the subset of `choices` the user picked (possibly empty when
    they confirm with nothing selected). Returns None when fzf cannot run
    (missing binary, no TTY) or crashes — caller falls back to typed
    range syntax. Esc (empty output + non-zero exit) yields [] meaning
    "nothing picked", which callers treat as "skip nothing".

    Matching is whitespace-tolerant: fzf echoes lines verbatim, but callers
    must not depend on exact padding (a past bug stripped the pick while the
    choice kept its `str.format` padding, silently discarding every mark).
    """
    if not (has_fzf() and sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    try:
        proc = subprocess.run(
            ["fzf", "--prompt", f"{prompt}> ", "--height", "60%",
             "--reverse", "--multi", "--ansi",
             "--header", "TAB to mark, ENTER to confirm, ESC for none"],
            input="\n".join(choices) + "\n",
            stdout=subprocess.PIPE,
            text=True,
            timeout=300,
        )
        if proc.returncode == 130:
            console.print("\n[bold red][!] Interrupted.[/]")
            sys.exit(130)
        # Keep lines verbatim (only drop blanks); match tolerantly below.
        picked = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        by_exact = set(choices)
        by_stripped: dict[str, str] = {}
        for c in choices:
            by_stripped.setdefault(c.strip(), c)
        out: list[str] = []
        for p in picked:
            if p in by_exact:
                out.append(p)
            elif p.strip() in by_stripped:
                out.append(by_stripped[p.strip()])
        # De-dupe, preserve fzf order.
        return list(dict.fromkeys(out))
    except Exception:
        return None


def label_from_url(url: str) -> str:
    """Human-readable label derived from a URL slug (no network needed).

    Batch entries skip upfront probing (700 probes would take forever), so
    their titles would otherwise read "Batch Item N". The URL slug usually
    carries the real name (`.../v7eyp8a-america-first-ep.-1742.html` →
    "america first ep. 1742"): use it. Rumble-style leading video ids
    (`v<id>-`) are stripped; generic URLs fall back to host + path.
    """
    try:
        parts = urlsplit(url)
        q = parse_qs(parts.query)
        if "v" in q and q["v"] and q["v"][0].strip():
            return f"watch?v={q['v'][0].strip()}"

        seg = (parts.path or "").rstrip("/").rsplit("/", 1)[-1]
        seg = unquote(seg)
        seg = re.sub(
            r"\.(html?|php|aspx?|m3u8|mpd|mp4|mkv|webm|mov|flv|m4a|mp3|opus)$",
            "", seg, flags=re.IGNORECASE,
        )
        m = re.match(r"^[A-Za-z]?\d+[A-Za-z0-9]*-(.+)$", seg)
        if m and m.group(1).strip(" -_."):
            seg = m.group(1)
        label = re.sub(r"[-_+]+", " ", seg).strip()
        label = re.sub(r"\s+", " ", label)
        if label and label.lower() not in ("watch", "video", "embed", "v"):
            return label
        host = parts.netloc or url
        return f"{host}{parts.path[:60]}".rstrip()
    except Exception:
        return url


# ==============================================================================
# Sophisticated line input (prompt_toolkit) with graceful degradation.
#
# Why not Rich's Prompt / bare input() here: Rich prints its styled prompt and
# then reads through a bare input(), so readline never learns the true prompt
# width — cursor tracking desyncs (backspace/arrows corrupt the line) and,
# with no completer installed, Tab just inserts whitespace. prompt_toolkit
# owns the whole render+edit loop: exact cursor model, Tab path-completion,
# persistent history, Ctrl-C/D handling. Questions/hints are printed as plain
# console lines first (kernel-script pattern); the editable line itself is
# always exactly `>:` so nothing can desync.
# ==============================================================================

def config_state_dir() -> Path | None:
    """Persistent state dir: ~/.config/dusky/settings/dusky_ytdlp (XDG-aware).

    Lives on real disk (NOT zram) so resume/skip state survives reboots.
    No username is hardcoded — resolved from $XDG_CONFIG_HOME or $HOME.
    Returns None if the directory cannot be created (caller runs stateless).
    """
    try:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
        state_dir = base / "dusky" / "settings" / "dusky_ytdlp"
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir
    except (OSError, PermissionError, RuntimeError):
        return None


_PT_HISTORY: object | None = None


def _input_history() -> object:
    """Persistent input history in the state dir; in-memory if unwritable."""
    global _PT_HISTORY
    if _PT_HISTORY is None:
        state_dir = config_state_dir()
        try:
            _PT_HISTORY = FileHistory(str(state_dir / "input_history.txt")) if state_dir else InMemoryHistory()
        except Exception:
            _PT_HISTORY = InMemoryHistory()
    return _PT_HISTORY


def ask_text(prompt: str = ">: ", default: str = "", *, path_complete: bool = False) -> str:
    """Read one line with full editing, history, and optional Tab path-completion.

    Empty input returns `default` (kernel-script `ask()` semantics). Ctrl-D
    also yields `default`; Ctrl-C exits 130 like the global signal handler.
    With no TTY (pipes) or no prompt_toolkit, degrades to a plain read.
    """
    if _PT_AVAILABLE and sys.stdin.isatty():
        try:
            completer = PathCompleter(expanduser=True) if path_complete else None
            text = pt_prompt(
                prompt,
                completer=completer,
                history=_input_history(),  # type: ignore[arg-type]
                key_bindings=_PT_KEYS,
                enable_history_search=True,
            )
            return text.strip() or default
        except EOFError:
            console.print("")
            return default
        except KeyboardInterrupt:
            console.print("\n[bold red][!] Interrupted.[/]")
            sys.exit(130)
    try:
        return input(prompt).strip() or default
    except EOFError:
        return default
    except KeyboardInterrupt:
        sys.exit(130)


def ask_confirm(question: str, default: bool = False) -> bool:
    """Yes/no question with validation loop (kernel-script `ask_yes()` semantics)."""
    hint = "Y/n" if default else "y/N"
    console.print(f"[bold green]?[/] {question} [dim]({hint})[/]")
    while True:
        val = ask_text(">: ").lower()
        if not val:
            return default
        if val in ("y", "yes"):
            return True
        if val in ("n", "no"):
            return False
        console.print("[bold red]Answer y or n.[/]")


# ==============================================================================
# PHASE 3: CORE DATA MODELS
# ==============================================================================


class TargetFormat(StrEnum):
    VIDEO_BEST = "video-best"
    VIDEO = "video"
    VIDEO_AV1 = "video-av1"
    VIDEO_VP9 = "video-vp9"
    VIDEO_MKV = "video-mkv"
    AUDIO_BEST = "audio-best"
    AUDIO_OPUS = "audio-opus"
    AUDIO_MP3 = "audio-mp3"
    AUDIO_FLAC = "audio-flac"
    AUDIO_M4A = "audio-m4a"
    AUDIO_WAV = "audio-wav"

    @property
    def is_video(self) -> bool:
        return self in (
            TargetFormat.VIDEO,
            TargetFormat.VIDEO_BEST,
            TargetFormat.VIDEO_AV1,
            TargetFormat.VIDEO_VP9,
            TargetFormat.VIDEO_MKV,
        )

    @property
    def label(self) -> str:
        labels = {
            TargetFormat.AUDIO_BEST: "Audio: Best (Native Lossless/Opus/AAC)",
            TargetFormat.AUDIO_OPUS: "Audio: Opus (High Quality)",
            TargetFormat.AUDIO_MP3: "Audio: MP3 (320 kbps)",
            TargetFormat.AUDIO_FLAC: "Audio: FLAC (Lossless)",
            TargetFormat.AUDIO_M4A: "Audio: M4A / AAC",
            TargetFormat.AUDIO_WAV: "Audio: WAV (Lossless PCM)",
            TargetFormat.VIDEO_BEST: "Video: Best Quality (Native AV1/VP9/Highest)",
            TargetFormat.VIDEO: "Video: MP4 (H.264 / AAC Universal)",
            TargetFormat.VIDEO_AV1: "Video: AV1 (Modern Next-Gen Codec)",
            TargetFormat.VIDEO_VP9: "Video: VP9 / WebM (Google Open Media)",
            TargetFormat.VIDEO_MKV: "Video: MKV (Lossless Multi-Track)",
        }
        return labels.get(self, self.value)


# Wizard order: audio-best sits on top so plain Enter picks it.
FORMAT_CHOICES: Final[list[str]] = [
    "audio-best",
    "audio-opus",
    "audio-mp3",
    "audio-flac",
    "audio-m4a",
    "audio-wav",
    "video-best",
    "video",
    "video-av1",
    "video-vp9",
    "video-mkv",
]
DEFAULT_FORMAT: Final[str] = "audio-best"

# Standard video caps offered in the quality picker.
QUALITY_CAPS: Final[list[int]] = [2160, 1440, 1080, 720, 480, 360]
QUALITY_LABELS: Final[dict[int, str]] = {
    2160: "2160p · 4K",
    1440: "1440p · QHD",
    1080: "1080p · Full HD",
    720: "720p · HD",
}


def format_duration(total_secs: float | int | None) -> str:
    if total_secs is None:
        return "--:--"
    secs = int(total_secs)
    hours, secs = divmod(max(secs, 0), 3600)
    mins, secs = divmod(secs, 60)
    return f"{hours}:{mins:02d}:{secs:02d}" if hours else f"{mins}:{secs:02d}"


def quality_label(cap: int | None, available_max: int | None = None) -> str:
    if cap is None:
        suffix = f" (up to {available_max}p)" if available_max else ""
        return f"Best available{suffix}"
    return QUALITY_LABELS.get(cap, f"{cap}p")


def build_quality_choices(heights: list[int]) -> list[tuple[str, int | None]]:
    """Quality picker entries from the heights a link actually offers.

    Falls back to the full standard cap list when the link exposes nothing
    (probe failed, audio-only source, playlist/batch context).
    """
    available_max = max(heights) if heights else None
    caps = [c for c in QUALITY_CAPS if available_max is None or c <= available_max]
    if available_max is not None and not caps:
        caps = [available_max]
    choices = [(quality_label(None, available_max), None)]
    choices.extend((quality_label(c), c) for c in caps)
    return choices


class ProgressStage(StrEnum):
    INITIALIZING = "Initializing"
    DOWNLOADING = "Downloading"
    MERGING = "Merging"
    REMUXING = "Remuxing"
    REENCODING = "Reencoding"
    FINALIZING = "Finalizing"


@dataclass(slots=True)
class MediaProgress:
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    percentage: float = 0.0
    speed_bps: float | None = None
    eta_secs: int | None = None
    stage: ProgressStage = ProgressStage.INITIALIZING
    destination_file: str | None = None
    already_archived: bool = False


# ==============================================================================
# PHASE 4: OVD STREAMING PROGRESS PARSER (Ported from ytdlp_progress.rs)
# ==============================================================================

RAW_PROGRESS_TEMPLATE: Final[str] = (
    "RAW|"
    "%(progress.percent|)s|"
    "%(progress._percent_str|)s|"
    "%(progress.speed|)s|"
    "%(progress.eta|)s|"
    "%(progress.downloaded_bytes|)s|"
    "%(progress.total_bytes|)s|"
    "%(progress.total_bytes_estimate|)s|"
    "%(progress.fragment_index|)s|"
    "%(progress.fragment_count|)s"
)


def _parse_opt_int(raw: str) -> int | None:
    """Parse ints that yt-dlp may render as floats ('125952.0')."""
    t = raw.strip()
    if not t or t.lower() == "na":
        return None
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return int(float(t))
    except ValueError:
        return None


def _parse_opt_float(raw: str) -> float | None:
    t = raw.strip()
    if not t or t.lower() == "na":
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _parse_opt_pct(raw: str) -> float | None:
    t = raw.strip().removesuffix("%").strip()
    if not t or t.lower() == "na":
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _clamp01pct(value: float) -> float:
    if value != value or value in (float("inf"), float("-inf")):  # NaN/inf guard
        return 0.0
    return max(0.0, min(100.0, value))


class YtdlpProgressParser:
    """Parses yt-dlp stdout lines and tracks byte counts and stages in real time.

    Faithful port of OVD's `try_progress_update`: percentage is derived from
    (1) `progress.percent`, else (2) `progress._percent_str`, else (3) bytes
    ratio, else (4) fragment ratio — clamped to [0, 100]. Live yt-dlp output
    (verified Sep 2026) always leaves field 0 empty and renders totals/ETA as
    floats, so float-tolerant parsing is mandatory.
    """

    def __init__(self) -> None:
        self.current_stage = ProgressStage.INITIALIZING

    def parse_line(self, line: str, progress_state: MediaProgress) -> None:
        line_clean = line.strip()
        if not line_clean:
            return

        # 1. Postprocess stages (check before generic destination handling)
        if line_clean.startswith("[VideoRemuxer]"):
            self.current_stage = ProgressStage.REMUXING
            progress_state.stage = self.current_stage
            dest = self._extract_destination(line_clean)
            if dest:
                progress_state.destination_file = dest
            return
        if line_clean.startswith("[VideoConvertor]"):
            self.current_stage = ProgressStage.REENCODING
            progress_state.stage = self.current_stage
            dest = self._extract_destination(line_clean)
            if dest:
                progress_state.destination_file = dest
            return

        # 2. Already-downloaded / already-archived fast paths (no bytes arrive)
        if "has already been recorded in the archive" in line_clean:
            progress_state.already_archived = True
            if self.current_stage != ProgressStage.FINALIZING:
                self.current_stage = ProgressStage.FINALIZING
                progress_state.stage = self.current_stage
            return

        if "has already been downloaded" in line_clean:
            self.current_stage = ProgressStage.FINALIZING
            progress_state.stage = self.current_stage
            # "[download] /path/file.mp4 has already been downloaded"
            rest = line_clean.split("[download]", 1)[-1]
            path_part = rest.split("has already been downloaded", 1)[0].strip()
            if path_part:
                progress_state.destination_file = Path(path_part).name
                if progress_state.total_bytes:
                    progress_state.downloaded_bytes = progress_state.total_bytes
                    progress_state.percentage = 100.0
            return

        # 3. Destination tracking
        if "[download] Destination:" in line_clean:
            self.current_stage = ProgressStage.DOWNLOADING
            progress_state.stage = self.current_stage
            dest = line_clean.split("Destination:", 1)[1].strip()
            progress_state.destination_file = Path(dest).name
            return

        if line_clean.startswith("[Merger] Merging formats into"):
            self.current_stage = ProgressStage.MERGING
            progress_state.stage = self.current_stage
            target = line_clean.replace("[Merger] Merging formats into", "").strip().strip('"')
            progress_state.destination_file = Path(target).name
            return

        if line_clean.startswith("[ExtractAudio]"):
            dest = self._extract_destination(line_clean)
            if dest:
                progress_state.destination_file = dest
            return

        # 4. Finalizing triggers
        if any(t in line_clean for t in ("[ffmpeg]", "[Fixup]", "Deleting original file")):
            if self.current_stage != ProgressStage.FINALIZING:
                self.current_stage = ProgressStage.FINALIZING
                progress_state.stage = self.current_stage
            return

        # 5. RAW progress protocol metrics
        if line_clean.startswith("RAW|"):
            self._parse_raw(line_clean, progress_state)

    @staticmethod
    def _extract_destination(line_clean: str) -> str | None:
        if "Destination:" not in line_clean:
            return None
        dest = line_clean.split("Destination:", 1)[1].strip().strip('"')
        return Path(dest).name if dest else None

    def _parse_raw(self, line_clean: str, progress_state: MediaProgress) -> None:
        parts = line_clean[4:].split("|")
        while len(parts) < 9:
            parts.append("")

        pct_num = _parse_opt_pct(parts[0])
        pct_str = _parse_opt_pct(parts[1])
        speed = _parse_opt_float(parts[2])
        eta_raw = _parse_opt_float(parts[3])
        eta = int(eta_raw) if eta_raw is not None and eta_raw >= 0 else None
        dl_bytes = _parse_opt_int(parts[4])
        total_bytes = _parse_opt_int(parts[5])
        estimate = _parse_opt_int(parts[6])
        frag_i = _parse_opt_int(parts[7])
        frag_n = _parse_opt_int(parts[8])

        if dl_bytes is not None:
            progress_state.downloaded_bytes = dl_bytes
        resolved_total = total_bytes or estimate
        if resolved_total is not None and resolved_total > 0:
            progress_state.total_bytes = resolved_total
        if speed is not None:
            progress_state.speed_bps = speed
        if eta is not None:
            progress_state.eta_secs = eta

        # Percentage derivation mirrors OVD: explicit -> bytes ratio -> fragments.
        pct: float | None = pct_num if pct_num is not None else pct_str
        if pct is None:
            total = progress_state.total_bytes
            if total and total > 0 and progress_state.downloaded_bytes is not None:
                pct = (progress_state.downloaded_bytes / total) * 100.0
        if pct is None and frag_i is not None and frag_n:
            pct = (frag_i / frag_n) * 100.0
        if pct is not None:
            progress_state.percentage = _clamp01pct(pct)


# ==============================================================================
# PHASE 5: STORAGE MANAGEMENT & RUNNER COMPILER
# ==============================================================================


def download_archive_path(mode: TargetFormat) -> Path | None:
    """Per-format yt-dlp download-archive file (the resume/skip state file).

    One archive per delivery format so grabbing audio of a link never marks
    its video as done (and vice versa). yt-dlp records `extractor id` lines
    here and skips them on later runs — re-running a batch continues where
    it stopped instead of re-downloading.
    """
    state_dir = config_state_dir()
    return state_dir / f"archive-{mode.value}.txt" if state_dir else None


def resolve_storage_pool(custom_path: Path | None = None) -> Path:
    """Ensures media writes occur strictly in memory (ZRAM or tmpfs)."""
    candidates = [custom_path] if custom_path else [PRIMARY_ZRAM_TARGET, RAM_TMPFS_FALLBACK]

    for path in candidates:
        if path is None:
            continue
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / f".probe_{uuid.uuid4().hex[:6]}"
            probe.touch()
            probe.unlink()

            stats = shutil.disk_usage(path)
            if (stats.free / (1024 * 1024)) < 500:
                console.print(f"[bold yellow]![/] Warning: Storage pool {path} has under 500 MB remaining.")
            return path
        except (OSError, PermissionError):
            continue

    fallback = Path.cwd() / "dusky_downloads"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
    except (OSError, PermissionError):
        pass

    last_resort = Path(tempfile.gettempdir()) / "dusky_downloads"
    last_resort.mkdir(parents=True, exist_ok=True)
    return last_resort


class YtdlpRunner:
    """Compiles yt-dlp arguments and manages isolated process execution."""

    def __init__(
        self,
        mode: TargetFormat,
        output_dir: Path,
        url: str,
        max_height: int | None = None,
        cookies: Path | None = None,
        cookies_from_browser: str | None = None,
    ):
        self.mode = mode
        self.output_dir = output_dir
        self.url = url
        self.max_height = max_height
        self.cookies = cookies
        self.cookies_from_browser = cookies_from_browser

        # Keeps original title; `%(title).180B` byte-truncates for 255B FAT32/
        # eCryptfs limits. `--windows-filenames` + `--trim-filenames 180`
        # provide defense-in-depth for MTP/Android transfers.
        output_template = str(output_dir / "%(title).180B [%(id)s].%(ext)s")

        self.args: list[str] = [
            "--encoding", "utf-8",
            "--newline",
            "--progress",
            "--no-color",
            "--no-mtime",
            "--progress-template", RAW_PROGRESS_TEMPLATE,
            "--progress-delta", "0.5",
            # Our queue expands playlists manually -> each job must be single.
            "--no-playlist",
            # Multi-connection & fragment recovery (verified flags, yt-dlp 2026.08)
            "--concurrent-fragments", "4",
            "--retries", "30",
            "--fragment-retries", "30",
            "--file-access-retries", "10",
            "--extractor-retries", "10",
            "--retry-sleep", "fragment:exp=1:20",
            "--retry-sleep", "http:exp=1:15",
            "--socket-timeout", "30",
            # YouTube & CDN Anti-Throttling & Speed Optimization (yt-dlp 2026)
            "--throttled-rate", "100K",
            "--buffer-size", "16K",
            # Additional JavaScript challenge solver runtime fallback
            "--js-runtimes", "node",
            # Replace invalid FAT32/Android characters for safe phone transfer
            "--windows-filenames",
            "--trim-filenames", "180",
            "--output-na-placeholder", "",
            # Preserve embedded metadata tags + chapters (canonical 2026 flags;
            # `--add-metadata` is merely an alias of `--embed-metadata`)
            "--embed-metadata",
            "--embed-chapters",
        ]

        # Cookie handling: explicit file, browser extraction, or auto-detected state file
        if self.cookies is not None and Path(self.cookies).is_file():
            self.args.extend(["--cookies", str(self.cookies)])
        elif self.cookies_from_browser:
            self.args.extend(["--cookies-from-browser", self.cookies_from_browser])
        else:
            state_dir = config_state_dir()
            if state_dir and (state_dir / "cookies.txt").is_file():
                self.args.extend(["--cookies", str(state_dir / "cookies.txt")])

        # Resume/skip state: per-format download archive on persistent disk.
        # Re-running the same URLs/batch skips finished items and continues
        # where the queue stopped. Omitted only if no state dir is writable.
        archive = download_archive_path(mode)
        if archive is not None:
            self.args.extend(["--download-archive", str(archive)])
        self._compile_format(output_template)

    def _compile_format(self, output_template: str) -> None:
        match self.mode:
            case TargetFormat.AUDIO_BEST:
                self.args.extend([
                    "-f", "bestaudio/best",
                    "-x", "--audio-format", "best",
                ])
            case TargetFormat.AUDIO_OPUS:
                self.args.extend([
                    "-f", "bestaudio[ext=opus]/bestaudio[acodec=opus]/bestaudio/best",
                    "-x", "--audio-format", "opus", "--audio-quality", "0",
                ])
            case TargetFormat.AUDIO_MP3:
                self.args.extend([
                    "-f", "bestaudio/best",
                    "-x", "--audio-format", "mp3", "--audio-quality", "0",
                ])
            case TargetFormat.AUDIO_FLAC:
                self.args.extend([
                    "-f", "bestaudio/best",
                    "-x", "--audio-format", "flac", "--audio-quality", "0",
                ])
            case TargetFormat.AUDIO_M4A:
                self.args.extend([
                    "-f", "bestaudio/best",
                    "-x", "--audio-format", "m4a", "--audio-quality", "0",
                ])
            case TargetFormat.AUDIO_WAV:
                self.args.extend([
                    "-f", "bestaudio/best",
                    "-x", "--audio-format", "wav",
                ])
            case TargetFormat.VIDEO_BEST:
                if self.max_height is not None:
                    cap = self.max_height
                    selector = (
                        f"bv*[height<={cap}]+ba/b[height<={cap}]"
                        f"/bv*[height<={cap}]+ba/b"
                    )
                else:
                    selector = "bv*+ba/b"
                self.args.extend([
                    "-f", selector,
                    "-S", "res,fps,quality,hdr:12",
                    "--merge-output-format", "mkv/mp4",
                ])
            case TargetFormat.VIDEO:
                if self.max_height is not None:
                    cap = self.max_height
                    selector = (
                        f"bv*[height<={cap}]+ba/b[height<={cap}]"
                        f"/bv*[height<={cap}]+ba/b"
                    )
                else:
                    selector = "bv*+ba/b"
                self.args.extend([
                    "-f", selector,
                    "-S", "res,fps,vcodec:h264,acodec:aac,vext:mp4,lang,quality,hdr:12",
                    "--merge-output-format", "mp4",
                    "--remux-video", "mp4",
                ])
            case TargetFormat.VIDEO_AV1:
                if self.max_height is not None:
                    cap = self.max_height
                    selector = (
                        f"bv*[height<={cap}][vcodec^=av01]+ba/bv*[height<={cap}]+ba/b[height<={cap}]/b"
                    )
                else:
                    selector = "bv*[vcodec^=av01]+ba/bv*+ba/b"
                self.args.extend([
                    "-f", selector,
                    "-S", "vcodec:av01,res,fps,quality,hdr:12",
                    "--merge-output-format", "mkv/mp4",
                ])
            case TargetFormat.VIDEO_VP9:
                if self.max_height is not None:
                    cap = self.max_height
                    selector = (
                        f"bv*[height<={cap}][vcodec^=vp9]+ba/bv*[height<={cap}]+ba/b[height<={cap}]/b"
                    )
                else:
                    selector = "bv*[vcodec^=vp9]+ba/bv*+ba/b"
                self.args.extend([
                    "-f", selector,
                    "-S", "vcodec:vp9,res,fps,quality,hdr:12",
                    "--merge-output-format", "mkv/webm/mp4",
                ])
            case TargetFormat.VIDEO_MKV:
                if self.max_height is not None:
                    cap = self.max_height
                    selector = (
                        f"bv*[height<={cap}]+ba/b[height<={cap}]"
                        f"/bv*[height<={cap}]+ba/b"
                    )
                else:
                    selector = "bv*+ba/b"
                self.args.extend([
                    "-f", selector,
                    "-S", "res,fps,quality,hdr:12",
                    "--merge-output-format", "mkv",
                    "--embed-subs",
                ])

        self.args.extend(["-o", output_template, self.url])

    def spawn(self) -> tuple[subprocess.Popen, int]:
        """Spawns yt-dlp in a distinct process group (setpgid(0,0)).

        `process_group=0` (Python 3.11+) is the modern equivalent of the OVD
        Rust `pre_exec(setpgid(0,0))`: the child becomes its own group leader
        (pgid == pid), so `os.killpg` reaps yt-dlp + FFmpeg children together.
        stdin is DEVNULL so yt-dlp can never block on an interactive prompt;
        both stdout AND stderr are piped (stderr must be drained concurrently
        to avoid 64 KiB pipe-buffer deadlock).
        """
        cmd = ["yt-dlp", *self.args]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,  # never allow interactive prompts to hang
            process_group=0,  # isolate process tree
        )
        pgid = proc.pid
        with _ACTIVE_PG_LOCK:
            ACTIVE_PROCESS_GROUPS.add(pgid)
        return proc, pgid


# ==============================================================================
# PHASE 6: DOWNLOAD PIPELINE
# ==============================================================================


@dataclass(slots=True)
class MediaJob:
    title: str
    url: str
    mode: TargetFormat
    max_height: int | None = None
    needs_probe: bool = False
    cookies: Path | None = None
    cookies_from_browser: str | None = None


@dataclass(slots=True)
class JobReport:
    title: str
    status: str
    saved_file: str = "--"
    size_mb: float = 0.0
    error: str | None = None


_BATCH_COMMENT_PREFIXES: Final[tuple[str, ...]] = ("#", ";", "]", "//")


def parse_batch_file(path: Path) -> list[str]:
    """Parse a yt-dlp-style batch file: one URL per line.

    Lines starting with `#`, `;` or `]` are comments (yt-dlp convention);
    `//` is also accepted. Blank lines are skipped.
    """
    urls: list[str] = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            clean = line.strip().strip("'\"")
            if not clean or clean.startswith(_BATCH_COMMENT_PREFIXES):
                continue
            urls.append(clean)
    return urls


# Compact port of OVD's diagnostic rules: raw yt-dlp errors stay visible, but
# the most common actionable failures get a plain-English tail hint.
_ERROR_HINTS: Final[tuple[tuple[str, str], ...]] = (
    ("sign in to confirm your age", "requires login/age verification — supply cookies or retry signed in"),
    ("sign in required", "requires login — supply cookies or retry signed in"),
    ("not a bot", "YouTube bot check — wait a while or supply cookies and retry"),
    ("members-only content", "members-only content — a membership login is required"),
    ("not available in your country", "geo-blocked in your region"),
    ("not available from your location", "geo-blocked in your region"),
    ("http error 429", "rate-limited — wait before retrying"),
    ("too many requests", "rate-limited — wait before retrying"),
    ("http error 403", "access forbidden — login/cookies may help"),
    ("premieres in", "not premiered yet — retry after it airs"),
    ("will begin in", "livestream has not started yet"),
    ("requested format is not available", "that quality does not exist here — retry with best"),
    ("the playlist does not exist", "playlist is missing or private"),
    ("private video", "video is private or removed"),
    ("video has not been found", "video is private or removed"),
    ("video unavailable", "video is private or removed"),
    ("sign in if you've been granted access", "requires login/permission — supply cookies or retry signed in"),
    ("allowed_segment_extensions", "ffmpeg format error — consider updating ffmpeg"),
    ("unable to download webpage", "network error or site unreachable"),
    ("connection refused", "connection refused by server"),
)


def translate_error(err_msg: str, mode: TargetFormat) -> str:
    """Append a plain-English hint to known yt-dlp failure signatures."""
    lowered = err_msg.lower()
    # The source simply carries no audio track (e.g. a video-only clip), so
    if not mode.is_video and "unable to obtain file audio codec" in lowered:
        return err_msg + " — source has no audio track; retry with a video format"
    for signature, hint in _ERROR_HINTS:
        if signature in lowered:
            return f"{err_msg} — {hint}"
    return err_msg


def resolve_job_title(job: MediaJob) -> None:
    """Just-in-time title for batch-sourced jobs (which skip upfront probing).

    Runs one isolated flat probe right before download so Processing lines and
    the final log show the real video/collection name instead of "Batch Item
    N". Never raises: on any failure the generic title stays and the download
    stage — with its retries and translated errors — reports the verdict.
    Playlist lines keep single-video (`--no-playlist`) semantics; they are
    simply labelled with the collection name.
    """
    job.needs_probe = False
    try:
        found, is_collection, label, _ = probe_media_target(
            job.url, cookies=job.cookies, cookies_from_browser=job.cookies_from_browser
        )
    except Exception:
        return
    if is_collection:
        job.title = label
    elif found:
        job.title = found[0][0]


def _short_title(title: str, width: int = 45) -> str:
    """Truncate a job title for the live progress bar (full title stays in logs).

    Bars must stay compact or multi-worker rows overflow the terminal; the
    full title is always printed on pickup/completion lines and the final log.
    Whitespace and newlines are normalized to preserve clean terminal layouts.
    """
    clean = " ".join(title.split())
    return (clean[: width - 2] + "..") if len(clean) > width else clean


def make_progress() -> Progress:
    """Shared factory so sequential and concurrent runs render identically."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold yellow]{task.fields[title]}[/]"),
        BarColumn(bar_width=24),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        TextColumn("[bold cyan]{task.description}[/]"),
        console=console,
        transient=True,
    )


def _pump_progress_loop(
    proc: subprocess.Popen,
    pgid: int,
    progress_ui: Progress,
    task_id: object,
    progress_state: MediaProgress,
    started_ns: int,
    timeout_secs: float | None,
) -> None:
    """Drives one live bar until the yt-dlp process exits (thread-safe).

    Shared by the sequential path (own Progress) and the concurrent path
    (shared Progress): `Progress.update` holds its own lock, so any number
    of worker threads may pump their own task_id concurrently.

    Also honours ABORT_ALL_EVENT (double Ctrl-C / `q` key): kills the child
    promptly instead of waiting out the download. Escalate to SIGKILL if
    a process group ignores SIGTERM during skip/abort.
    """
    skip_requested_at: float | None = None
    while proc.poll() is None:
        if ABORT_ALL_EVENT.is_set():
            try:
                if pgid > 1:
                    os.killpg(pgid, signal.SIGTERM)
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    if pgid > 1:
                        os.killpg(pgid, signal.SIGKILL)
                except OSError:
                    pass
                proc.wait()
            break

        with _SKIP_LOCK:
            was_skip = pgid in USER_SKIPPED_PGIDS
        if was_skip:
            now = time.monotonic()
            if skip_requested_at is None:
                skip_requested_at = now
            elif now - skip_requested_at > 5.0:
                try:
                    if pgid > 1:
                        os.killpg(pgid, signal.SIGKILL)
                except OSError:
                    pass
                proc.wait()
                break

        if timeout_secs is not None and (time.monotonic_ns() - started_ns) / 1e9 > timeout_secs:
            try:
                if pgid > 1:
                    os.killpg(pgid, signal.SIGTERM)
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    if pgid > 1:
                        os.killpg(pgid, signal.SIGKILL)
                except OSError:
                    pass
                proc.wait()
            break
        # Prefer byte-accurate totals; fall back to OVD-derived % for
        # fragment-only (HLS) streams where no total exists.
        if progress_state.total_bytes:
            completed: float | int = progress_state.downloaded_bytes
            total: float | None = float(progress_state.total_bytes)
        elif progress_state.percentage > 0:
            completed = progress_state.percentage
            total = 100.0
        else:
            completed = progress_state.downloaded_bytes
            total = None
        try:
            progress_ui.update(
                task_id,  # type: ignore[arg-type]
                completed=completed,
                total=total,
                description=f"[{progress_state.stage}]",
            )
        except Exception:
            pass
        time.sleep(0.1)


def execute_download(
    job: MediaJob,
    output_dir: Path,
    *,
    timeout_secs: float | None = None,
    progress: Progress | None = None,
    task_id: object | None = None,
    slot_no: int | None = None,
) -> JobReport:
    """Download one job; optionally joins a shared live `Progress` display.

    Sequential callers omit `progress` (a private transient bar is created).
    Concurrent workers pass the shared `progress`, their claimed worker
    `task_id` slot, and its 1-based `slot_no` (rendered as `W<n>`).

    User-skip awareness: a sticky one-shot flag for this slot (digit key /
    `s` / single Ctrl-C arriving while the job probed) is consumed at the
    spawn gate — the job reports Skipped without starting yt-dlp. A SIGTERMed
    live process maps to Skipped; a full abort maps to Failed("aborted...").
    Completed files are never touched by any skip/abort path (only the
    child's partial `.part`, which yt-dlp resumes on retry, is left behind).
    """
    if ABORT_ALL_EVENT.is_set():
        return JobReport(title=job.title, status="Failed", error="aborted by user (Ctrl-C)")

    runner = YtdlpRunner(
        job.mode,
        output_dir,
        job.url,
        job.max_height,
        cookies=job.cookies,
        cookies_from_browser=job.cookies_from_browser,
    )
    parser = YtdlpProgressParser()
    progress_state = MediaProgress()
    started_ns = time.monotonic_ns()
    # Snapshot before spawn so the post-run file search can diff "what this
    # job created" instead of trusting mtimes — mandatory under concurrency
    # where several jobs land files in the same second.
    try:
        before_names: set[str] = {p.name for p in output_dir.iterdir() if p.is_file()}
    except OSError:
        before_names = set()

    if slot_no is not None:
        with _SLOT_LOCK:
            gated = slot_no in _SLOT_SKIP
            _SLOT_SKIP.discard(slot_no)
        if gated:
            return JobReport(title=job.title, status="Skipped", error="skipped by user")
    elif SKIP_NEXT_ONE_SHOT.is_set():
        SKIP_NEXT_ONE_SHOT.clear()
        return JobReport(title=job.title, status="Skipped", error="skipped by user")

    proc: subprocess.Popen | None = None
    pgid: int | None = None
    try:
        proc, pgid = runner.spawn()
    except FileNotFoundError:
        return JobReport(title=job.title, status="Failed", error="yt-dlp binary not found in PATH")
    except Exception as err:
        return JobReport(title=job.title, status="Failed", error=str(err))

    assert proc is not None and pgid is not None
    if progress is not None and task_id is not None:
        with _SLOT_LOCK:
            _SLOT_PGIDS[task_id] = pgid
            _SLOT_TITLES[task_id] = job.title

    assert proc.stdout is not None and proc.stderr is not None
    stderr_lines: list[str] = []
    stderr_lock = threading.Lock()

    def drain_stream(stream: object, is_stdout: bool) -> None:
        # Reads byte-by-byte splits on \n/\r so `--newline` progress lines
        # and \r-style FFmpeg updates are both handled without blocking.
        buf = bytearray()
        read1 = getattr(stream, "read", None)
        try:
            while True:
                chunk = read1(1) if callable(read1) else None
                if not chunk:
                    break
                byte = chunk[0] if isinstance(chunk, (bytes, bytearray)) else ord(chunk)
                if byte in (10, 13):  # \n or \r
                    if buf:
                        text = bytes(buf).decode("utf-8", errors="replace")
                        del buf[:]
                        if is_stdout:
                            try:
                                parser.parse_line(text, progress_state)
                            except Exception:
                                pass
                        else:
                            try:
                                parser.parse_line(text, progress_state)
                            except Exception:
                                pass
                            with stderr_lock:
                                stderr_lines.append(text)
                                if len(stderr_lines) > 200:
                                    del stderr_lines[: len(stderr_lines) - 200]
                    continue
                buf.append(byte)
        except Exception:
            pass
        finally:
            if buf:
                text = bytes(buf).decode("utf-8", errors="replace")
                try:
                    parser.parse_line(text, progress_state)
                except Exception:
                    pass
                if not is_stdout:
                    with stderr_lock:
                        stderr_lines.append(text)

    stdout_thread = threading.Thread(target=drain_stream, args=(proc.stdout, True), daemon=True)
    stderr_thread = threading.Thread(target=drain_stream, args=(proc.stderr, False), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    display_title = f"W{slot_no} {_short_title(job.title)}" if slot_no else _short_title(job.title)

    if progress is None:
        try:
            with make_progress() as progress_ui:
                owned_task = progress_ui.add_task("Initializing", total=None, title=display_title)
                _pump_progress_loop(proc, pgid, progress_ui, owned_task, progress_state, started_ns, timeout_secs)
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
            try:
                proc.stderr.close()
            except Exception:
                pass
            stdout_thread.join(timeout=2.0)
            stderr_thread.join(timeout=2.0)
            with _ACTIVE_PG_LOCK:
                ACTIVE_PROCESS_GROUPS.discard(pgid)
    else:
        try:
            if task_id is None:
                task_id = progress.add_task("Initializing", total=None, title=display_title)
            else:
                try:
                    progress.update(task_id, title=display_title)  # type: ignore[arg-type]
                except Exception:
                    pass
            _pump_progress_loop(proc, pgid, progress, task_id, progress_state, started_ns, timeout_secs)
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
            try:
                proc.stderr.close()
            except Exception:
                pass
            stdout_thread.join(timeout=2.0)
            stderr_thread.join(timeout=2.0)
            with _ACTIVE_PG_LOCK:
                ACTIVE_PROCESS_GROUPS.discard(pgid)
            if task_id is not None:
                with _SLOT_LOCK:
                    _SLOT_PGIDS.pop(task_id, None)
                    _SLOT_TITLES.pop(task_id, None)
                try:
                    progress.update(task_id, description=f"[{progress_state.stage}]")  # type: ignore[arg-type]
                except Exception:
                    pass

    exit_code = proc.returncode if proc.returncode is not None else 1
    if exit_code != 0:
        if ABORT_ALL_EVENT.is_set():
            for stream in (proc.stdout, proc.stderr):
                try:
                    stream.close()
                except Exception:
                    pass
            return JobReport(title=job.title, status="Failed", error="aborted by user (Ctrl-C)")
        with _SKIP_LOCK:
            was_skip = pgid in USER_SKIPPED_PGIDS
            USER_SKIPPED_PGIDS.discard(pgid)
        if was_skip:
            for stream in (proc.stdout, proc.stderr):
                try:
                    stream.close()
                except Exception:
                    pass
            return JobReport(title=job.title, status="Skipped", error="skipped by user")
        with stderr_lock:
            tail = [ln for ln in stderr_lines if ln.strip()][-3:]
        if tail:
            err_msg = " | ".join(ln.strip()[:300] for ln in tail)
        else:
            err_msg = f"yt-dlp error code {exit_code}"
        err_msg = translate_error(err_msg, job.mode)
        try:
            proc.stdout.close()
        except Exception:
            pass
        try:
            proc.stderr.close()
        except Exception:
            pass
        return JobReport(title=job.title, status="Failed", error=err_msg)

    try:
        proc.stdout.close()
    except Exception:
        pass
    try:
        proc.stderr.close()
    except Exception:
        pass

    # Archive skip: yt-dlp recorded this id on an earlier run — nothing to
    # locate on disk (it may have been a different directory), so report the
    # honest Skipped state instead of attributing a stranger's file to it.
    if progress_state.already_archived:
        return JobReport(
            title=job.title, status="Skipped", saved_file="--", error="already in archive",
        )

    # Locate output: prefer parser-tracked destination; else diff against the
    # pre-spawn snapshot so concurrent jobs never claim each other's files.
    dest_file = progress_state.destination_file or "--"
    size_mb = 0.0
    actual_path: Path | None = None
    if dest_file != "--":
        actual_path = output_dir / dest_file
        if not actual_path.exists():
            actual_path = _resolve_output_file(output_dir, started_ns, dest_file, before_names) or actual_path
            if actual_path is not None and actual_path.exists():
                dest_file = actual_path.name
    else:
        actual_path = _resolve_output_file(output_dir, started_ns, None, before_names)
        if actual_path is not None:
            dest_file = actual_path.name

    if actual_path is not None and actual_path.exists():
        # Fix trailing space before extension: e.g. "title .m4a" -> "title.m4a"
        clean_name = re.sub(r"\s+\.([a-zA-Z0-9]+)$", r".\1", actual_path.name)
        if clean_name != actual_path.name:
            cleaned_path = actual_path.with_name(clean_name)
            try:
                actual_path.replace(cleaned_path)
            except OSError:
                pass
            else:
                actual_path = cleaned_path
                dest_file = clean_name

        try:
            size_mb = actual_path.stat().st_size / (1024 * 1024)
        except OSError:
            size_mb = 0.0
        return JobReport(title=job.title, status="Success", saved_file=dest_file, size_mb=size_mb)

    return JobReport(title=job.title, status="Failed", saved_file="--", error="no output file produced")


def _newest_file_since(directory: Path, started_ns: int) -> Path | None:
    """Newest regular file in `directory` modified after `started_ns`."""
    newest: Path | None = None
    newest_mtime = -1.0
    started_s = started_ns / 1e9 - 1.0  # 1s grace for clock skew
    try:
        entries = list(directory.iterdir())
    except OSError:
        return None
    for entry in entries:
        try:
            if not entry.is_file() or entry.name.startswith(".probe_"):
                continue
            if entry.suffix in (".part", ".ytdl", ".temp", ".aria2"):
                continue
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime >= started_s and mtime > newest_mtime:
            newest = entry
            newest_mtime = mtime
    return newest


def _resolve_output_file(
    directory: Path,
    started_ns: int,
    destination_file: str | None,
    before_names: set[str],
) -> Path | None:
    """Find the file this job produced, safe under concurrent downloads.

    Strategy: diff the directory against the pre-spawn snapshot and consider
    only genuinely new files (ignoring `.part`/`.ytdl`/probe files). When the
    parser-tracked name is known, prefer the new file sharing its stem (the
    merge/remux step often only swaps the extension, e.g. `.webm` → `.mp4`);
    otherwise take the newest new file. Falls back to the legacy
    mtime-based `_newest_file_since` when no new file exists (e.g. the
    "already downloaded" fast path where yt-dlp wrote nothing).
    """
    try:
        current = [p for p in directory.iterdir() if p.is_file()]
    except OSError:
        return None
    new_files = [
        p for p in current
        if p.name not in before_names
        and not p.name.startswith(".probe_")
        and p.suffix not in (".part", ".ytdl", ".temp", ".aria2")
    ]
    if new_files:
        if destination_file:
            want_stem = Path(destination_file).stem
            for p in new_files:
                if p.stem == want_stem:
                    return p
        try:
            return max(new_files, key=lambda p: p.stat().st_mtime)
        except OSError:
            return new_files[0]
    return _newest_file_since(directory, started_ns)


def clamp_concurrent(requested: int, n_jobs: int) -> int:
    """Clamp an explicit concurrency request to the sane range 1..n_jobs."""
    if n_jobs <= 0:
        return 1
    return max(1, min(int(requested), n_jobs))


def _spawn_control_thread(
    stop_event: threading.Event, slots: list[object], workers: int
) -> threading.Thread | None:
    """Single-key live controls while the queue runs (TTY only, best effort).

    Keys (no Enter needed): `1`–`N` skip that worker's current download,
    `s` skip ALL active downloads, `q` abort everything, `h` reprint help.
    Ctrl-C once == `s`, twice (within 3s) == abort — same semantics for
    pipes/SSH where raw keys are unavailable.

    Implemented with termios cbreak + select on stdin in a daemon thread;
    Rich's live display never reads stdin, so there is no contention, and
    terminal attributes are always restored (try/finally + daemon fallback).
    Returns None when no TTY/control is possible — the queue runs the same.
    """
    if not sys.stdin.isatty():
        return None
    try:
        import select
        import termios
        import tty as _tty

        fd = sys.stdin.fileno()
        orig = termios.tcgetattr(fd)
    except Exception:
        return None

    help_line = (
        f"Keys: 1–{workers} skip worker · s skip active · q abort · "
        "Ctrl-C once/twice = same"
    )

    def _loop() -> None:
        try:
            _tty.setcbreak(fd)
            while not stop_event.is_set():
                try:
                    ready, _, _ = select.select([fd], [], [], 0.2)
                except (OSError, ValueError):
                    break
                if not ready:
                    continue
                try:
                    ch = os.read(fd, 1).decode("utf-8", errors="ignore")
                except OSError:
                    break
                if not ch or stop_event.is_set():
                    continue
                if ch in ("h", "H", "?"):
                    console.print(f"[dim]{help_line}[/]")
                elif ch in ("q", "Q"):
                    request_abort_all()
                    console.print("[bold red][!] Aborting everything (q)...[/]")
                elif ch in ("s", "S", "x", "X"):
                    console.print(f"[bold yellow][!][/] {escape(skip_all_active())}")
                elif ch.isdigit() and ch != "0":
                    if slots:
                        msg = skip_slot_by_number(slots, int(ch))
                        style = "yellow" if "kip" in msg else "dim"
                        console.print(f"[bold {style}][!][/] {escape(msg)}")
                    else:  # sequential run: no slots, digit == skip all
                        console.print(f"[bold yellow][!][/] {escape(skip_all_active())}")
                elif ch == "\x03":  # Ctrl-C in cbreak arrives here, not as SIGINT
                    global_signal_handler(signal.SIGINT, None)
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, orig)
            except Exception:
                pass

    thread = threading.Thread(target=_loop, name="dusky-keys", daemon=True)
    thread.start()
    return thread


def run_queue(
    jobs: list[MediaJob],
    destination: Path,
    max_workers: int = 1,
) -> list[JobReport]:
    """Download the whole queue, sequentially or with N concurrent workers.

    Native `ThreadPoolExecutor` model: jobs are I/O-bound subprocesses
    (yt-dlp + FFmpeg), so threads — not processes — are the correct native
    primitive (no pickling, shared Rich display, GIL released during I/O).
    Per-format `--download-archive` appends are small O_APPEND writes, safe
    across the worker processes. Reports return in queue order.

    Display uses one bar per *worker slot* (not per job), so a 700-item
    batch shows 3 live bars — never 700 rows. Slots are pre-created on the
    main thread and claimed via a queue; workers only ever call the
    thread-safe `Progress.update`. Full titles print on pickup/completion
    lines (bars stay compact by necessity).

    Live controls on a TTY: `1`–`N` skip exactly that worker's job (bars and
    pickup lines are tagged `W1`…`WN`), `s` skips the whole current wave,
    `q` aborts; single Ctrl-C == `s`, double == abort. Skip flags are sticky
    one-shots: a job still probing is skipped at its spawn gate and can never
    escape, and a flag never leaks onto the slot's next job.
    """
    global _QUEUE_RUNNING
    workers = clamp_concurrent(max_workers, len(jobs))
    if workers <= 1 or len(jobs) <= 1:
        ABORT_ALL_EVENT.clear()
        _QUEUE_RUNNING = True
        stop = threading.Event()
        key_thread = _spawn_control_thread(stop, [], 1)
        if key_thread is not None:
            console.print("[dim]Keys: s skip · q abort (Ctrl-C once/twice = same)[/]")
        try:
            reports: list[JobReport] = []
            for idx, job in enumerate(jobs):
                if ABORT_ALL_EVENT.is_set():
                    reports.append(JobReport(title=job.title, status="Failed", error="aborted by user (Ctrl-C)"))
                    continue
                if job.needs_probe:
                    resolve_job_title(job)
                console.print(
                    f"[bold blue]•[/] [{idx + 1}/{len(jobs)}] Processing: "
                    f"[bold yellow]{escape(job.title)}[/]"
                )
                reports.append(execute_download(job, destination))
            return reports
        finally:
            stop.set()
            if key_thread is not None:
                # Block briefly so the thread restores termios BEFORE any
                # sys.exit unwinds the interpreter (else the shell keeps
                # cbreak/no-echo). Daemon flag is only a crash fallback.
                key_thread.join(timeout=2.0)
            with _ACTIVE_PG_LOCK:
                _QUEUE_RUNNING = False

    ABORT_ALL_EVENT.clear()
    _QUEUE_RUNNING = True
    console.print(
        f"[bold green]➜[/] Downloading [yellow]{len(jobs)}[/] items "
        f"with [cyan]{workers}[/] concurrent workers…"
    )
    console.print(
        f"[dim]Keys: 1–{workers} skip worker · s skip active · q abort · "
        "Ctrl-C once/twice = same (TTY only)[/]" if sys.stdin.isatty()
        else "[dim]Tip: Ctrl-C once skips active downloads, twice aborts.[/]"
    )
    ordered: list[JobReport | None] = [None] * len(jobs)

    def _one(idx: int, job: MediaJob, shared: Progress, slots: queue.SimpleQueue) -> JobReport:
        if ABORT_ALL_EVENT.is_set():
            return JobReport(title=job.title, status="Failed", error="aborted by user (Ctrl-C)")
        slot, slot_no = slots.get()
        with _SLOT_LOCK:
            _BUSY_SLOT_NOS.add(slot_no)
            _SLOT_TITLES[slot] = job.title
        try:
            if job.needs_probe:
                try:
                    shared.update(slot, title=f"W{slot_no} [{idx + 1}/{len(jobs)}] probing…")  # type: ignore[arg-type]
                except Exception:
                    pass
                resolve_job_title(job)
                with _SLOT_LOCK:
                    _SLOT_TITLES[slot] = job.title
            if ABORT_ALL_EVENT.is_set():
                return JobReport(title=job.title, status="Failed", error="aborted by user (Ctrl-C)")
            # Full title to scrollback (bars truncate by necessity); worker
            # print is safe during Live (Rich renders it above the bars).
            # The (W<n>) tag is the digit to press to skip exactly this job.
            console.print(f"[dim][{idx + 1}/{len(jobs)}] (W{slot_no}) ▶ {escape(job.title)}[/]")
            try:
                shared.update(  # type: ignore[arg-type]
                    slot, title=f"W{slot_no} [{idx + 1}/{len(jobs)}] {_short_title(job.title)}",
                )
            except Exception:
                pass
            return execute_download(
                job, destination,
                progress=shared, task_id=slot, slot_no=slot_no,
            )
        finally:
            try:
                shared.update(  # type: ignore[arg-type]
                    slot, title=f"W{slot_no}", description="[idle]",
                    completed=0, total=None,
                )
            except Exception:
                pass
            with _SLOT_LOCK:
                # The flag must never survive its occupant: consume here as
                # the backstop (spawn gate + kill path consume earlier).
                _SLOT_SKIP.discard(slot_no)
                _BUSY_SLOT_NOS.discard(slot_no)
            slots.put((slot, slot_no))

    stop_event = threading.Event()
    key_thread: threading.Thread | None = None
    try:
        with make_progress() as shared:
            slot_ids: list[object] = [
                shared.add_task("[idle]", total=None, title=f"W{w + 1}")
                for w in range(workers)
            ]
            slots: queue.SimpleQueue = queue.SimpleQueue()
            for w, sid in enumerate(slot_ids, start=1):
                slots.put((sid, w))
            key_thread = _spawn_control_thread(stop_event, slot_ids, workers)
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dusky-dl") as pool:
                future_to_idx = {
                    pool.submit(_one, idx, job, shared, slots): idx
                    for idx, job in enumerate(jobs)
                }
                done = 0
                try:
                    for fut in as_completed(future_to_idx):
                        idx = future_to_idx[fut]
                        try:
                            ordered[idx] = fut.result()
                        except CancelledError:
                            ordered[idx] = JobReport(title=jobs[idx].title, status="Failed", error="aborted by user (Ctrl-C)")
                        except Exception as err:  # never let one job kill the queue
                            ordered[idx] = JobReport(title=jobs[idx].title, status="Failed", error=str(err))
                        done += 1
                        rep = ordered[idx]
                        console.print(
                            f"[dim][{done}/{len(jobs)}] {escape(rep.title if rep else jobs[idx].title)} "
                            f"— {rep.status if rep else 'Failed'}[/]"
                        )
                        if ABORT_ALL_EVENT.is_set():
                            for f2 in future_to_idx:
                                f2.cancel()
                finally:
                    # Prompt handoff: the pool's context shutdown(wait=True)
                    # reaps killed workers (fast — their procs are SIGTERMed).
                    pass
    finally:
        stop_event.set()
        if key_thread is not None:
            # Restore the terminal before unwinding to the partial log /
            # sys.exit(130) — otherwise the shell inherits cbreak/no-echo.
            key_thread.join(timeout=2.0)
        with _ACTIVE_PG_LOCK:
            _QUEUE_RUNNING = False
    return [r if r is not None else JobReport(title=jobs[i].title, status="Failed", error="worker lost") for i, r in enumerate(ordered)]


# ==============================================================================
# PHASE 7: TARGET PROBING & INTERACTIVE TUI
# ==============================================================================


@dataclass(slots=True)
class VideoDetails:
    title: str
    duration_secs: int | None
    uploader: str | None
    heights: list[int]


class _ProbeLogger:
    """Logger hook to capture real-time playlist extraction events and pass to callbacks."""

    def __init__(
        self,
        progress_cb: Callable[[str, int | None, int | None], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ):
        self.progress_cb = progress_cb
        self.cancel_check = cancel_check
        self.playlist_title: str | None = None
        self.total_items: int | None = None
        self._item_pat = re.compile(r"Downloading item\s+(\d+)(?:\s+of\s+(\d+))?", re.IGNORECASE)
        self._items_total_pat = re.compile(r"Downloading\s+(\d+)\s+items", re.IGNORECASE)
        self._playlist_pat = re.compile(r"Downloading playlist:\s*(.+)", re.IGNORECASE)

    def debug(self, msg: str) -> None:
        if self.cancel_check and self.cancel_check():
            raise KeyboardInterrupt("Probing cancelled by user.")
        if not self.progress_cb:
            return
        clean = re.sub(r"^\[.*?\]\s*", "", msg).strip()
        m_pl = self._playlist_pat.search(clean)
        if m_pl:
            self.playlist_title = m_pl.group(1).strip('"\' ')
            self.progress_cb(f"Found collection: {self.playlist_title}", None, self.total_items)
            return
        m_tot = self._items_total_pat.search(clean)
        if m_tot:
            try:
                self.total_items = int(m_tot.group(1))
                prefix = f"{self.playlist_title}: " if self.playlist_title else ""
                self.progress_cb(f"{prefix}Fetching {self.total_items} items...", None, self.total_items)
            except ValueError:
                pass
            return
        m_item = self._item_pat.search(clean)
        if m_item:
            curr_str = m_item.group(1)
            tot_str = m_item.group(2)
            try:
                curr = int(curr_str)
                if tot_str:
                    self.total_items = int(tot_str)
                prefix = f"{self.playlist_title}: " if self.playlist_title else ""
                tot_display = f" of {self.total_items}" if self.total_items else ""
                self.progress_cb(f"{prefix}Fetching item {curr}{tot_display}...", curr, self.total_items)
            except ValueError:
                pass
            return
        if "Downloading webpage" in clean:
            prefix = f"{self.playlist_title}: " if self.playlist_title else ""
            self.progress_cb(f"{prefix}Querying collection webpage...", None, self.total_items)

    def info(self, msg: str) -> None:
        self.debug(msg)

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


def probe_media_target(
    url: str,
    cookies: Path | None = None,
    cookies_from_browser: str | None = None,
    progress_cb: Callable[[str, int | None, int | None], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[list[tuple[str, str]], bool, str, VideoDetails | None]:
    """Universal flat extraction probe across any media endpoint with live progress reporting."""
    opts: dict[str, object] = {
        "extract_flat": "in_playlist",
        "skip_download": True,
        "quiet": False if progress_cb else True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 5,
        "ignoreerrors": "only_download",
    }
    if progress_cb or cancel_check:
        opts["logger"] = _ProbeLogger(progress_cb, cancel_check)

    if cookies is not None and Path(cookies).is_file():
        opts["cookiefile"] = str(cookies)
    elif cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    else:
        state_dir = config_state_dir()
        if state_dir and (state_dir / "cookies.txt").is_file():
            opts["cookiefile"] = str(state_dir / "cookies.txt")

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise ValueError("No metadata returned by extractor.")

    entries = info.get("entries")
    if entries:
        items: list[tuple[str, str]] = []
        for e in entries:
            if not e:
                continue
            item_url = e.get("url") or e.get("webpage_url")
            if not item_url:
                # Flat YouTube entries carry a bare video id; rebuild a
                # directly-downloadable watch URL instead of passing the id.
                vid = e.get("id")
                if vid and e.get("ie_key") in ("Youtube", "YoutubeTab", "youtube"):
                    item_url = f"https://www.youtube.com/watch?v={vid}"
                elif vid:
                    item_url = vid
            if item_url:
                items.append((e.get("title") or item_url, item_url))
        if not items:
            raise ValueError("Playlist contained no downloadable entries.")
        if len(items) > 500:
            console.print(
                f"[bold yellow]![/] Large collection: {len(items)} items. "
                "Consider a narrow range to save time/RAM."
            )
        return items, True, info.get("title") or "Collection / Feed", None

    single_url = info.get("webpage_url") or info.get("original_url") or url
    single_title = info.get("title") or single_url

    heights = sorted(
        {
            f.get("height")
            for f in (info.get("formats") or [])
            if isinstance(f.get("height"), int) and f.get("height")
        },
        reverse=True,
    )
    raw_dur = info.get("duration")
    duration = int(raw_dur) if isinstance(raw_dur, (int, float)) else None
    uploader = info.get("uploader") or info.get("channel") or info.get("extractor_key")
    details = VideoDetails(
        title=single_title,
        duration_secs=duration,
        uploader=str(uploader) if uploader else None,
        heights=heights,
    )
    return [(single_title, single_url)], False, single_title, details


def probe_video_details(
    url: str,
    cookies: Path | None = None,
    cookies_from_browser: str | None = None,
) -> VideoDetails | None:
    """Full-metadata inspect of a single video: duration, uploader, heights.

    Returns None on any failure (caller falls back to generic options).
    Playlists/collections are never inspected here — pass.
    """
    opts: dict[str, object] = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 5,
    }
    if cookies is not None and Path(cookies).is_file():
        opts["cookiefile"] = str(cookies)
    elif cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    else:
        state_dir = config_state_dir()
        if state_dir and (state_dir / "cookies.txt").is_file():
            opts["cookiefile"] = str(state_dir / "cookies.txt")

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return None
    if not info or info.get("_type") == "playlist":
        return None
    heights = sorted(
        {
            f.get("height")
            for f in (info.get("formats") or [])
            if isinstance(f.get("height"), int) and f.get("height")
        },
        reverse=True,
    )
    raw_dur = info.get("duration")
    duration = int(raw_dur) if isinstance(raw_dur, (int, float)) else None
    uploader = info.get("uploader") or info.get("channel") or info.get("extractor_key")
    return VideoDetails(
        title=info.get("title") or url,
        duration_secs=duration,
        uploader=str(uploader) if uploader else None,
        heights=heights,
    )


def expand_selection_spec(spec: str, total: int) -> list[int]:
    """Parse a yt-dlp -I style spec into 0-based unique indices in order.

    Supports: 'all', '*', 'none', single numbers ('5', '-1'), ranges ('1-3', '1:3'),
    steps ('1:10:2', '::-1'), negative indices ('-3:', ':-3', '-5--2', '-1'),
    and comma-separated combinations ('1,3,5-7', '-3, -1').
    """
    s = (spec or "").strip().lower()
    if not s or s in ("none", "no", "skip-none", "-"):
        return []
    if s in ("all", "*"):
        return list(range(total))
    if total <= 0:
        return []

    chunks = [c.strip() for c in s.split(",") if c.strip()]
    if not chunks:
        return []
    clean_spec = ",".join(chunks)

    info = {"_type": "playlist", "entries": list(range(total))}
    pe = PlaylistEntries(yt_dlp.YoutubeDL({"quiet": True}), info)

    indices: list[int] = []
    seen: set[int] = set()
    try:
        for segment in PlaylistEntries.parse_playlist_items(clean_spec):
            for i, _ in pe[segment]:
                idx = i - 1
                if 0 <= idx < total and idx not in seen:
                    seen.add(idx)
                    indices.append(idx)
    except Exception as err:
        raise ValueError(f"Invalid range or item specification {spec!r}: {err}") from err

    return indices


def select_playlist_items(
    discovered: list[tuple[str, str]],
    range_val: str,
) -> list[tuple[str, str]]:
    """Select items using yt-dlp `-I` flavour syntax.

    Supports: `all`, `5`, `1-3`, `1:5`, `1:10:2`, comma lists (`1,3,5-7`),
    negatives (`-3:` = last three). Out-of-range indices are ignored;
    an empty selection raises ValueError.
    """
    total = len(discovered)
    if not total:
        return []
    idxs = expand_selection_spec(range_val, total)
    selected = [discovered[i] for i in idxs if 0 <= i < total]
    if not selected:
        raise ValueError(f"No items matched range {range_val!r} (playlist has {total}).")
    return selected


def parse_skip_indices(spec: str, total: int) -> set[int]:
    """Parse a skip spec (same `-I` flavour as ranges) into 0-based indices.

    `""`/`"none"` → empty (skip nothing); `"all"`/`"*"` → everything.
    Invalid chunks are ignored; out-of-range indices are dropped.
    """
    if not total:
        return set()
    try:
        return set(expand_selection_spec(spec, total))
    except Exception:
        return set()


def filter_skipped_items(
    items: list[tuple[str, str]], skip_spec: str
) -> tuple[list[tuple[str, str]], set[int]]:
    """Split `items` into (kept, skipped_indices) per a skip spec."""
    skip_idx = parse_skip_indices(skip_spec, len(items))
    kept = [it for i, it in enumerate(items) if i not in skip_idx]
    return kept, skip_idx


def show_queue_preview(items: list[tuple[str, str]], *, limit: int = 50) -> None:
    """Print a numbered preview of the queue (capped for huge playlists).

    Full titles/URLs are passed to Rich (which ellipsizes per terminal
    width) — never pre-truncated, so nothing is silently hidden.
    """
    total = len(items)
    table = Table(box=box.ROUNDED, header_style="bold cyan", expand=True, show_header=True)
    table.add_column("#", justify="right", width=6)
    table.add_column("Title", style="yellow", ratio=3, overflow="ellipsis")
    table.add_column("URL", style="dim", ratio=4, overflow="ellipsis")
    if total <= limit:
        rows = list(enumerate(items, start=1))
    else:
        head = list(enumerate(items[:20], start=1))
        tail_start = total - 10 + 1
        tail = list(enumerate(items[-10:], start=tail_start))
        rows = head + [(-1, (f"{total - 30} more items hidden — use ranges or fzf", "…"))] + tail  # type: ignore[list-item]
    for num, (title, url) in rows:
        if num == -1:
            table.add_row("…", f"[dim]{escape(title)}[/]", f"[dim]{escape(url)}[/]")
        else:
            table.add_row(str(num), escape(title), escape(url))
    console.print(table)
    console.print(f"[dim]{total} item(s). Numbers are 1-based for skip ranges.[/]")


def fzf_skip_choices(items: list[tuple[str, str]]) -> tuple[list[str], dict[str, int]]:
    """Build fzf lines plus a tolerant line→index map for skip selection.

    The informative label comes FIRST (episode names live at the END of
    URLs, exactly where terminal truncation would eat them). Lines are never
    truncated here — fzf scrolls. Numbering is zero-padded without leading
    blanks so strip-tolerant matching cannot desync (regression test covers
    the bug where TAB marks were silently discarded).
    """
    width = len(str(len(items)))
    choices = [f"{i:0{width}d}. {t} — {u}" for i, (t, u) in enumerate(items, start=1)]
    mapping = {c.strip(): i - 1 for i, c in enumerate(choices, start=1)}
    return choices, mapping


def interactive_skip_prompt(
    items: list[tuple[str, str]], *, context: str = "queue",
) -> tuple[list[tuple[str, str]], set[int]]:
    """Let the user drop items from a playlist/batch/multi-URL queue.

    Most native available UX wins: `fzf --multi` (fuzzy checklist) when a TTY
    + fzf exists, otherwise the typed `-I`-flavour range syntax (`1,3,5-7`,
    `1:10:2`, `-3:`) — the same language the range picker already speaks.
    Empty input / Esc keeps everything. Loops until the spec parses; typing
    `list` re-prints the preview. Never returns an empty kept list without an
    explicit confirmation (avoids nuking a 700-item queue by typo).
    """
    total = len(items)
    if total <= 1:
        return list(items), set()
    show_queue_preview(items)

    while True:  # fzf round (re-loops instead of recursing on decline)
        display_choices, mapping = fzf_skip_choices(items)
        picked = fzf_multi_pick(f"Mark items to SKIP ({context})", display_choices)
        if picked is None:
            break  # no fzf/TTY (or crashed) -> typed ranges below
        skip_idx = {mapping[p.strip()] for p in picked if p.strip() in mapping}
        kept = [it for i, it in enumerate(items) if i not in skip_idx]
        if not kept and skip_idx:
            console.print("[bold red]That skips everything.[/]")
            if ask_confirm("Really skip ALL items and exit?", default=False):
                return kept, skip_idx
            continue  # re-run fzf instead of recursing
        if skip_idx:
            skipped_names = ", ".join(f"#{i + 1}" for i in sorted(skip_idx)[:10])
            more = f" +{len(skip_idx) - 10} more" if len(skip_idx) > 10 else ""
            console.print(
                f"[yellow]![/] Skipping [yellow]{len(skip_idx)}[/] item(s) "
                f"({escape(skipped_names)}{more}), keeping [green]{len(kept)}[/]."
            )
        else:
            console.print("[green]✓[/] Keeping everything.")
        return kept, skip_idx

    while True:
        console.print(
            f"[bold green]?[/] Skip any {escape(context)} items? "
            "[dim](ranges like '1,3,5-7' / '-3:' / 'none', 'list' to re-show)[/]"
        )
        spec = ask_text(">: ", default="none")
        if spec.strip().lower() == "list":
            show_queue_preview(items)
            continue
        skip_idx = parse_skip_indices(spec, total)
        # Detect garbage specs: non-empty text that parsed to nothing and is
        # not an explicit "none" — almost certainly a typo worth re-asking.
        txt = spec.strip().lower()
        if txt and txt not in ("none", "no", "-", "all", "*") and not skip_idx:
            # Check whether ANY chunk parsed; reuse the filter to decide.
            kept_test, _ = filter_skipped_items(items, spec)
            if len(kept_test) == total:
                console.print(f"[bold red]No items matched {spec!r} — nothing skipped. Re-type or 'none'.[/]")
                continue
        kept = [it for i, it in enumerate(items) if i not in skip_idx]
        if not kept:
            console.print("[bold red]That skips everything.[/]")
            if ask_confirm("Really skip ALL items and exit?", default=False):
                return kept, skip_idx
            continue
        if skip_idx:
            console.print(f"[yellow]![/] Skipping [yellow]{len(skip_idx)}[/] item(s), keeping [green]{len(kept)}[/].")
        else:
            console.print("[green]✓[/] Keeping everything.")
        return kept, skip_idx


def batch_url_line_numbers(path: Path) -> list[int]:
    """1-based file line numbers holding URLs, in `parse_batch_file` order."""
    linenos: list[int] = []
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            for lineno, line in enumerate(f, start=1):
                clean = line.strip()
                if not clean or clean.startswith(_BATCH_COMMENT_PREFIXES):
                    continue
                linenos.append(lineno)
    except OSError:
        pass
    return linenos


def persist_batch_skips(path: Path, skipped_parsed_idx: set[int]) -> int:
    """Comment out skipped entries in a batch file so they stay skipped.

    Mapping is positional (parsed-URL index → file URL-line), so duplicate
    URLs are handled exactly — only the lines the user skipped get `# `.
    Already-commented lines are never touched. Returns lines commented.
    """
    if not skipped_parsed_idx:
        return 0
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as err:
        console.print(f"[bold red]Cannot update batch file:[/] {err}")
        return 0
    lines = text.splitlines(keepends=True)
    url_linenos = batch_url_line_numbers(path)
    targets = {url_linenos[i] for i in skipped_parsed_idx if 0 <= i < len(url_linenos)}
    if not targets:
        return 0
    changed = 0
    for lineno in targets:
        idx = lineno - 1
        if 0 <= idx < len(lines) and not lines[idx].lstrip().startswith(("#", ";", "]", "//")):
            stripped = lines[idx].lstrip()
            indent = lines[idx][: len(lines[idx]) - len(stripped)]
            lines[idx] = f"{indent}# SKIPPED {stripped}"
            changed += 1
    if changed:
        try:
            path.write_text("".join(lines), encoding="utf-8")
        except OSError as err:
            console.print(f"[bold red]Cannot write batch file:[/] {err}")
            return 0
    return changed


def ask_concurrent_downloads(n_jobs: int, explicit: int | None = None) -> int:
    """Resolve how many files download at once (1..n_jobs).

    An explicit CLI `--concurrent` value wins (clamped to the queue). Without
    one, a TTY gets asked (default min(DEFAULT, n)); pipes/scripts take the
    default silently so automation never blocks.
    """
    if explicit is not None:
        try:
            return clamp_concurrent(int(explicit), n_jobs)
        except (TypeError, ValueError):
            pass
    default = max(1, min(DEFAULT_CONCURRENT_DOWNLOADS, n_jobs))
    cap = max(1, min(MAX_CONCURRENT_DOWNLOADS, n_jobs))
    if n_jobs <= 1:
        return 1
    if not sys.stdin.isatty():
        return default
    console.print(
        f"[bold green]?[/] Concurrent downloads? "
        f"[dim](1–{cap}, default {default}; {n_jobs} queued)[/]"
    )
    while True:
        raw = ask_text(">: ", default=str(default)).strip()
        if not raw:
            return default
        try:
            n = int(raw)
        except ValueError:
            console.print(f"[bold red]Enter a number 1–{cap}.[/]")
            continue
        if 1 <= n <= cap:
            return n
        # Above the interactive cap but within the queue: confirm (ZRAM guard).
        if cap < n <= n_jobs:
            console.print(
                f"[bold yellow]![/] {n} exceeds the safe interactive cap ({cap}) "
                "for RAM/ZRAM pools."
            )
            if ask_confirm(f"Use {n} concurrent downloads anyway?", default=False):
                return n
            continue
        console.print(f"[bold red]Enter a number 1–{n_jobs}.[/]")


_URL_SPLIT_PATTERN: Final[re.Pattern] = re.compile(r"(?:,\s*|\s+)(?=['\"]?https?://)", re.IGNORECASE)


def split_url_list(raw: str) -> list[str]:
    """Split pasted multi-URL input on commas or whitespace preceding http(s)://.

    Commas embedded inside a single URL are preserved. Surrounding quotes
    and whitespace are cleanly stripped.
    """
    return [frag.strip().strip("'\"") for frag in _URL_SPLIT_PATTERN.split(raw.strip()) if frag.strip()]


def collect_targets(
    raw_urls: list[str],
    playlist_items: str = "all",
    cookies: Path | None = None,
    cookies_from_browser: str | None = None,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Probe each URL independently; one bad link never kills the queue.

    Playlists expand per URL (range-filtered); singles pass through. Returns
    (items, errors) so callers can warn yet continue with what resolved.
    """
    items: list[tuple[str, str]] = []
    errors: list[str] = []
    for url in raw_urls:
        try:
            found, is_collection, _, _ = probe_media_target(
                url, cookies=cookies, cookies_from_browser=cookies_from_browser
            )
            if is_collection:
                try:
                    found = select_playlist_items(found, playlist_items)
                except ValueError as err:
                    errors.append(f"{url}: {err}")
                    continue
            items.extend(found)
        except Exception as err:
            errors.append(f"{url}: {err}")
    return items, errors


def run_interactive_wizard() -> tuple[list[MediaJob], Path, int]:
    console.print(
        Panel.fit(
            Align.center("[bold cyan]Dusky YT-DLP[/]"),
            border_style="cyan",
            box=box.DOUBLE,
        ),
        justify="center",
    )

    # 1. Link(s) first — everything else depends on what they point at.
    #    Accepts one link, several comma-separated links, a playlist, or a
    #    batch file. Each link is probed on its own: one dead URL never kills
    #    the rest of the queue.
    batch_urls: list[str] | None = None
    batch_source_path: Path | None = None
    discovered: list[tuple[str, str]] = []
    is_collection = False
    label = ""
    multi_mode = False
    details: VideoDetails | None = None

    while True:
        # Hint on its own line (kernel-script pattern): Rich prints the prompt
        # then reads via bare input(), so a multi-line prompt string corrupts
        # readline's cursor tracking and line editing eats characters. A
        # single-line ask keeps navigation/redraw exact.
        console.print("\n[bold green]?[/] Enter media link(s), playlist URL, or batch file path")
        console.print("[dim]Several links? Separate them with commas. Tab completes file paths.[/]")
        raw_target = ask_text(">: ", path_complete=True)
        if not raw_target:
            continue

        raw_urls = split_url_list(raw_target)
        target_path = Path(raw_target.strip().strip("'\"")).expanduser()
        if len(raw_urls) <= 1 and target_path.is_file():
            local_file = target_path
            try:
                urls = parse_batch_file(local_file)
            except OSError as err:
                console.print(f"[bold red]Cannot read batch file:[/] {err}")
                continue
            if urls:
                batch_urls = urls
                batch_source_path = local_file
                break
            console.print("[bold red]Batch file contained no valid URLs.[/]")
            continue

        if len(raw_urls) == 1:
            try:
                with console.status("[bold cyan]Probing remote endpoint...[/]", spinner="dots"):
                    discovered, is_collection, label, details = probe_media_target(raw_urls[0])
                break
            except Exception as err:
                console.print(Panel(f"[bold red]Probe failed:[/] {escape(str(err))}", border_style="red"))
            continue

        multi_mode = True
        with console.status(f"[bold cyan]Probing {len(raw_urls)} links...[/]", spinner="dots"):
            discovered, probe_errors = collect_targets(raw_urls)
        if probe_errors:
            console.print(
                Panel(
                    "[bold yellow]Some links failed (skipped):[/]\n"
                    + "\n".join(escape(e) for e in probe_errors),
                    border_style="yellow",
                )
            )
        if discovered:
            console.print(
                f"[green]✓[/] Resolved [yellow]{len(discovered)}[/] item(s) "
                f"from [yellow]{len(raw_urls)}[/] link(s)."
            )
            break
        console.print("[bold red]No link resolved to anything downloadable.[/]")

    # 2. Show what the link(s) actually offer, then offer matching options.
    if batch_urls is None:
        if multi_mode:
            console.print(
                Panel(
                    f"[yellow]{len(discovered)}[/] item(s) queued — best match "
                    "is picked per link at download time.",
                    title="[green]Sources[/]",
                    border_style="green",
                )
            )
        elif not is_collection:
            title, link = discovered[0]
            if details is None:
                with console.status("[bold cyan]Inspecting available formats...[/]", spinner="dots"):
                    details = probe_video_details(link)
            show_title = details.title if details else title
            meta_bits: list[str] = []
            if details and details.uploader:
                meta_bits.append(details.uploader)
            if details:
                meta_bits.append(format_duration(details.duration_secs))
            if details and details.heights:
                meta_bits.append(f"up to {details.heights[0]}p")
            meta_line = f"\n[dim]{escape(' · '.join(meta_bits))}[/]" if meta_bits else ""
            console.print(
                Panel(
                    f"[bold yellow]{escape(show_title)}[/]{meta_line}",
                    title="[green]Source[/]",
                    border_style="green",
                )
            )
        else:
            total = len(discovered)
            console.print(
                Panel(
                    f"[bold yellow]{escape(label)}[/]\n[dim]{total} items[/]",
                    title="[green]Collection[/]",
                    border_style="green",
                )
            )

    # 3. Delivery format (audio-best on top; Enter takes it).
    fmt_choice = fzf_pick("Select delivery format", FORMAT_CHOICES, DEFAULT_FORMAT)
    mode = TargetFormat(fmt_choice)

    # 4. Video quality — capped to what the link really provides.
    max_height: int | None = None
    if mode.is_video:
        q_choices = build_quality_choices(details.heights if details else [])
        q_labels = [label for label, _ in q_choices]
        q_pick = fzf_pick("Select video quality", q_labels, q_labels[0])
        max_height = dict(q_choices)[q_pick]

    # 5. Build the queue (skip-aware).
    jobs: list[MediaJob] = []
    skipped_parsed_idx: set[int] = set()
    if batch_urls is not None:
        # Slug-derived labels (no network): "Batch Item N" tells the user
        # nothing when picking skips — the URL slug usually holds the real
        # episode name. JIT probing still replaces these with exact titles.
        batch_items = [(label_from_url(u) or f"Batch Item {idx}", u) for idx, u in enumerate(batch_urls, start=1)]
        if len(batch_items) > 1:
            kept, skipped_parsed_idx = interactive_skip_prompt(batch_items, context="batch file")
            if not kept:
                console.print("[bold red]All batch items skipped — nothing to do.[/]")
                sys.exit(0)
            batch_items = kept
            if skipped_parsed_idx and batch_source_path is not None:
                # Native batch-file skip memory: `#`-comments are yt-dlp's own
                # ignore convention (parse_batch_file already skips them), so
                # commenting lines doubles as "mark as already downloaded".
                if ask_confirm("Remember skips in the batch file (comment out those lines)?", default=False):
                    n = persist_batch_skips(batch_source_path, skipped_parsed_idx)
                    console.print(f"[green]✓[/] Commented out [yellow]{n}[/] line(s) in {escape(str(batch_source_path))}.")
        jobs = [
            MediaJob(title=t, url=u, mode=mode, max_height=max_height, needs_probe=True)
            for t, u in batch_items
        ]
        console.print(f"[green]✓[/] Queued [yellow]{len(jobs)}[/] item(s) from batch file.")
    elif multi_mode:
        if len(discovered) > 1:
            kept, _ = interactive_skip_prompt(discovered, context="link list")
            if not kept:
                console.print("[bold red]All items skipped — nothing to do.[/]")
                sys.exit(0)
            discovered = kept
        jobs = [MediaJob(title=item[0], url=item[1], mode=mode, max_height=max_height) for item in discovered]
        console.print(f"[green]✓[/] Queued [yellow]{len(jobs)}[/] item(s).")
    elif not is_collection:
        title, link = discovered[0]
        jobs = [MediaJob(title=details.title if details else title, url=link, mode=mode, max_height=max_height)]
    else:
        total = len(discovered)
        if total > 1 and ask_confirm("Invert order? (Oldest ➔ Newest)", default=False):
            discovered.reverse()

        while True:
            console.print("[bold green]?[/] Range ('all', '5', '1-3' or '1:10:2') [dim][all][/]")
            range_val = ask_text(">: ", default="all")
            try:
                picked = select_playlist_items(discovered, range_val)
                break
            except ValueError as err:
                console.print(f"[bold red]{escape(str(err))}[/]")

        if len(picked) > 1:
            kept, _ = interactive_skip_prompt(picked, context="playlist")
            if not kept:
                console.print("[bold red]All playlist items skipped — nothing to do.[/]")
                sys.exit(0)
            picked = kept
        jobs = [MediaJob(title=item[0], url=item[1], mode=mode, max_height=max_height) for item in picked]
        console.print(f"[green]✓[/] Queued [yellow]{len(jobs)}[/] item(s).")

    # 6. Concurrency: only meaningful for multi-item queues; single items
    #    always run alone. The prompt defaults to min(DEFAULT, n).
    max_workers = ask_concurrent_downloads(len(jobs)) if len(jobs) > 1 else 1
    if max_workers > 1:
        console.print(f"[green]✓[/] [cyan]{max_workers}[/] concurrent download(s).")

    default_dir = resolve_storage_pool()
    console.print(f"[bold green]?[/] Target directory (ZRAM) [dim](default: {escape(str(default_dir))})[/]")
    custom_dir = ask_text(">: ", default=str(default_dir), path_complete=True)
    destination = resolve_storage_pool(Path(custom_dir).expanduser())
    if destination != Path(custom_dir).expanduser():
        console.print(f"[bold yellow]![/] Requested path unusable; using [cyan]{destination}[/].")

    return jobs, destination, max_workers


def main() -> None:
    parser = argparse.ArgumentParser(description="Dusky Universal Media Downloader.")
    parser.add_argument(
        "target",
        nargs="*",
        help="URL(s), comma-separated URLs, playlist(s) and/or batch file(s). "
             "Each link is probed on its own; one bad link never kills the queue.",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=FORMAT_CHOICES,
        default="audio-best",
        help="Delivery format (default: audio-best)",
    )
    parser.add_argument(
        "-q",
        "--quality",
        choices=["best", "2160", "1440", "1080", "720", "480", "360"],
        default="best",
        help="Video quality cap (default: best). Applies to video formats only.",
    )
    parser.add_argument("-o", "--output-dir", type=Path, help="Storage directory override")
    parser.add_argument(
        "-I", "--playlist-items",
        default="all",
        help="Playlist selection: 'all', '5', '1-3', '1:10:2', '1,3,5-7' (default: all)",
    )
    parser.add_argument(
        "-N", "--concurrent",
        type=int,
        default=None,
        help=f"Concurrent downloads for multi-item queues (default: prompt when TTY, else {DEFAULT_CONCURRENT_DOWNLOADS})",
    )
    parser.add_argument(
        "--skip-items",
        default=None,
        help="Skip queue positions using -I syntax: '1,3,5-7', '-3:' (default: none). "
             "Applies to the final combined queue order.",
    )
    parser.add_argument(
        "--cookies",
        type=Path,
        default=None,
        help="Netscape formatted file to read cookies from",
    )
    parser.add_argument(
        "--cookies-from-browser",
        default=None,
        help="The name of the browser to load cookies from (e.g. chrome, firefox, brave)",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch Dusky Downloader GTK graphical user interface",
    )

    args = parser.parse_args()

    if args.gui:
        from dusky_downloader_gui import main as gui_main
        gui_main()
        return

    max_workers = 1
    if not args.target:
        jobs, destination, max_workers = run_interactive_wizard()
    else:
        destination = resolve_storage_pool(args.output_dir.expanduser() if args.output_dir else None)
        mode = TargetFormat(args.format)
        max_height = None if args.quality == "best" else int(args.quality)
        if max_height is not None and not mode.is_video:
            console.print("[bold yellow]![/] --quality only applies to video; ignoring.")
            max_height = None

        # Mixed bag allowed: batch files expand in place, everything else is
        # split on commas (only before http(s)://, so in-URL commas survive).
        batch_urls: list[str] = []
        link_urls: list[str] = []
        for raw_arg in args.target:
            arg_path = Path(raw_arg.strip("'\"")).expanduser()
            if arg_path.is_file():
                try:
                    batch_urls.extend(parse_batch_file(arg_path))
                except OSError as err:
                    console.print(f"[bold red]Cannot read batch file {raw_arg}:[/] {err}")
            else:
                link_urls.extend(split_url_list(raw_arg))

        discovered: list[tuple[str, str]] = []
        if link_urls:
            with console.status(f"[bold cyan]Probing {len(link_urls)} link(s)...[/]", spinner="dots"):
                found, probe_errors = collect_targets(
                    link_urls,
                    args.playlist_items,
                    cookies=args.cookies,
                    cookies_from_browser=args.cookies_from_browser,
                )
            for err in probe_errors:
                console.print(f"[bold yellow]![/] Skipped: {escape(err)}")
            discovered.extend(found)
        jobs = [
            MediaJob(
                title=label_from_url(u) or f"Item {idx}",
                url=u,
                mode=mode,
                max_height=max_height,
                needs_probe=True,
                cookies=args.cookies,
                cookies_from_browser=args.cookies_from_browser,
            )
            for idx, u in enumerate(batch_urls, start=1)
        ]
        jobs.extend(
            MediaJob(
                title=item[0],
                url=item[1],
                mode=mode,
                max_height=max_height,
                cookies=args.cookies,
                cookies_from_browser=args.cookies_from_browser,
            )
            for item in discovered
        )

        # CLI-side skipping: same -I syntax, applied to final queue order.
        if args.skip_items and jobs:
            skip_idx = parse_skip_indices(args.skip_items, len(jobs))
            if skip_idx:
                kept_jobs = [j for i, j in enumerate(jobs) if i not in skip_idx]
                console.print(
                    f"[yellow]![/] --skip-items drops [yellow]{len(skip_idx)}[/] item(s), "
                    f"keeping [green]{len(kept_jobs)}[/]."
                )
                jobs = kept_jobs

        # CLI-side concurrency: explicit flag wins; otherwise prompt on TTY
        # when the queue is multi-item (mirrors the wizard), else default.
        max_workers = ask_concurrent_downloads(len(jobs), explicit=args.concurrent)

    if not jobs:
        if args.skip_items:
            console.print("[bold yellow]![/] All items were skipped by --skip-items.")
            sys.exit(0)
        console.print("[bold red]No download targets queued.[/]")
        sys.exit(1)

    fmt_label = jobs[0].mode.upper()
    if jobs[0].mode.is_video and jobs[0].max_height is not None:
        fmt_label += f" ≤{jobs[0].max_height}P"
    workers_label = f" | Workers: [cyan]{max_workers}[/]" if len(jobs) > 1 else ""
    console.print(
        f"\n[bold green]➜[/] Storage Pool: [cyan]{destination}[/] | Queue: [yellow]{len(jobs)}[/] | Format: [magenta]{fmt_label}[/]{workers_label}\n"
    )

    reports: list[JobReport] = run_queue(jobs, destination, max_workers)

    n_ok = sum(1 for r in reports if r.status == "Success")
    n_skip = sum(1 for r in reports if r.status == "Skipped")
    n_fail = sum(1 for r in reports if r.status not in ("Success", "Skipped"))
    console.print(
        f"[green]✓[/] {n_ok} downloaded · [yellow]{n_skip} skipped[/] · "
        f"[{'green' if not n_fail else 'red'}]{n_fail} failed[/]"
    )

    table = Table(
        title="Extraction Log",
        box=box.ROUNDED,
        header_style="bold cyan",
        title_style="bold green",
        expand=True,
    )
    table.add_column("Title", style="yellow", ratio=4, overflow="ellipsis")
    table.add_column("Status", justify="center", width=10)
    table.add_column("Size", justify="right", width=12)
    table.add_column("Filename", style="dim", ratio=5, overflow="ellipsis")

    for r in reports:
        if r.status == "Success":
            status_str = "[bold green]Success[/]"
        elif r.status == "Skipped":
            status_str = "[bold yellow]Skipped[/]"
        else:
            status_str = "[bold red]Failed[/]"
        size_str = f"{r.size_mb:.2f} MB" if r.status == "Success" else "--"
        detail = r.saved_file if r.status == "Success" else (r.error or "failed")
        clean_title = " ".join(r.title.split())
        table.add_row(escape(clean_title), status_str, size_str, escape(detail))

    console.print("\n")
    console.print(table)
    archive = download_archive_path(jobs[0].mode)
    skip_line = f"\n[dim]Skip-state: {escape(str(archive))}[/]" if archive else ""
    if ABORT_ALL_EVENT.is_set():
        skip_line += "\n[dim]Queue aborted by user — pending items were not attempted.[/]"
    console.print(
        Panel(
            f"[bold green]Location:[/] [cyan]{escape(str(destination))}[/]{skip_line}\n"
            f"[dim]Media downloaded with native titles directly into RAM/ZRAM.[/]",
            border_style="green",
        )
    )

    if ABORT_ALL_EVENT.is_set():
        sys.exit(130)
    if any(r.status not in ("Success", "Skipped") for r in reports):
        sys.exit(1)


if __name__ == "__main__":
    main()
