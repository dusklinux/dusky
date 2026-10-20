#!/usr/bin/python
"""Dusky Universal Media Downloader: Arch Linux / Python 3.14.7+ TUI."""

from __future__ import annotations

import argparse
from collections import deque
from collections.abc import Iterator
from concurrent.futures import CancelledError, FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Final
from urllib.parse import parse_qs, unquote, urlsplit


MIN_PYTHON: Final = (3, 14, 7)
MIN_YTDLP: Final = (2026, 8, 19)
MIN_FFMPEG: Final = (9, 0)
MIN_KERNEL: Final = (7, 3)
MAX_INTERACTIVE_WORKERS: Final = 8
DEFAULT_WORKERS: Final = 3
ZRAM_MOUNT: Final = Path("/mnt/zram1")
SHM_MOUNT: Final = Path("/dev/shm")
POOLS: Final = (ZRAM_MOUNT / "dusky_ytdlp", SHM_MOUNT / "dusky_ytdlp")
MODULE_PACKAGES: Final = {
    "rich": "python-rich",
    "prompt_toolkit": "python-prompt_toolkit",
    "yt_dlp": "yt-dlp",
    "yt_dlp_ejs": "yt-dlp-ejs",
}
BINARY_PACKAGES: Final = {
    "ffmpeg": "ffmpeg", "ffprobe": "ffmpeg", "yt-dlp": "yt-dlp", "node": "nodejs",
}


def bootstrap_dependencies() -> None:
    if (sys.platform != "linux" or platform.python_implementation() != "CPython"
            or sys.version_info[:3] < MIN_PYTHON):
        raise SystemExit("Requires Linux and CPython 3.14.7 or newer.")
    if os.geteuid() == 0:
        raise SystemExit("Run as your regular user, not root. Only pacman needs sudo.")
    if sys.prefix != sys.base_prefix:
        raise SystemExit("Use the Arch system Python, not a virtual environment.")
    missing = set()
    for executable, package in BINARY_PACKAGES.items():
        if shutil.which(executable) is None:
            missing.add(package)
    for module, package in MODULE_PACKAGES.items():
        if importlib.util.find_spec(module) is None:
            missing.add(package)
    if not missing:
        return
    packages = sorted(missing)
    command = ["sudo", "pacman", "-S", "--needed", *packages]
    if os.environ.get("DUSKY_BOOTSTRAPPED") == "1":
        raise SystemExit(f"Dependencies still missing after pacman: {', '.join(packages)}. Check /usr/bin/python.")
    if not Path("/etc/arch-release").exists() or shutil.which("pacman") is None:
        raise SystemExit("Arch Linux/pacman is required. Missing: " + ", ".join(packages))
    if not sys.stdin.isatty() or shutil.which("sudo") is None:
        raise SystemExit("Install first: " + " ".join(command))
    print("Missing Arch packages: " + ", ".join(packages), file=sys.stderr)
    result = subprocess.run(command, check=False)
    if result.returncode:
        raise SystemExit(f"pacman failed with exit status {result.returncode}")
    environment = dict(os.environ, DUSKY_BOOTSTRAPPED="1")
    os.execve(sys.executable, [sys.executable, *sys.argv], environment)


bootstrap_dependencies()

from yt_dlp.version import __version__ as YTDLP_API_VERSION
from prompt_toolkit import prompt as toolkit_prompt
from prompt_toolkit.completion import PathCompleter
from prompt_toolkit.history import FileHistory
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

console: Final = Console()


def version_line(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=True)
    return result.stdout.splitlines()[0].strip()


def check_versions() -> None:
    release = platform.release()
    kernel = re.match(r"^(\d+)\.(\d+)", release)
    if kernel is None or tuple(map(int, kernel.groups())) < MIN_KERNEL:
        raise ValueError(f"Booted kernel {release} is below 7.3; boot a current Arch kernel.")
    ytdlp = version_line(["yt-dlp", "--version"])
    if not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", ytdlp) or tuple(map(int, ytdlp.split("."))) < MIN_YTDLP:
        raise ValueError(f"yt-dlp {ytdlp} is too old; update the whole system with sudo pacman -Syu.")
    if ytdlp != YTDLP_API_VERSION:
        raise ValueError(f"yt-dlp CLI {ytdlp} differs from Python package {YTDLP_API_VERSION}; use /usr/bin/python.")
    ffmpeg = version_line(["ffmpeg", "-version"])
    match = re.search(r"^ffmpeg version n?(\d+)\.(\d+)", ffmpeg)
    if match is None or tuple(map(int, match.groups())) < MIN_FFMPEG:
        raise ValueError(f"FFmpeg 9.0+ required; found: {ffmpeg}. Update with sudo pacman -Syu.")
    probe_version = version_line(["ffprobe", "-version"])
    probe_match = re.search(r"^ffprobe version n?(\d+)\.(\d+)", probe_version)
    if probe_match is None or tuple(map(int, probe_match.groups())) < MIN_FFMPEG:
        raise ValueError(f"FFprobe 9.0+ required; found: {probe_version}.")
    node = version_line(["node", "--version"])
    major = re.match(r"^v(\d+)", node)
    if major is None or int(major.group(1)) < 22:
        raise ValueError(f"Node.js 22+ required for yt-dlp EJS; found {node}.")


def private_dir(path: Path) -> Path:
    """Do not accept an existing symlink or a directory owned by someone else."""
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise ValueError(f"Unsafe directory (symlink or other owner): {path}")
    path.chmod(0o700)
    return path


def private_file(path: Path, flags: int = os.O_RDWR | os.O_CREAT) -> int:
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
        os.close(fd)
        raise ValueError(f"Unsafe file (symlink or other owner): {path}")
    os.fchmod(fd, 0o600)
    return fd


def state_directory() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    base = Path(configured).expanduser() if configured and Path(configured).is_absolute() else Path.home() / ".config"
    path = base / "dusky" / "settings" / "dusky_ytdlp"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_dir(base / "dusky")
    private_dir(path.parent)
    private_dir(path)
    private_dir(path / "locks")
    private_dir(path / "yt-dlp-cache")
    return path


def is_memory_mount(root: Path) -> bool:
    if root not in {ZRAM_MOUNT, SHM_MOUNT}:
        return False
    try:
        with Path("/proc/self/mountinfo").open(encoding="utf-8") as mounts:
            for line in mounts:
                left, separator, right = line.partition(" - ")
                if not separator or left.split()[4] != str(root):
                    continue
                fields = right.split()
                if len(fields) < 2:
                    return False
                filesystem, source = fields[:2]
                if filesystem == "tmpfs":
                    return True
                if root == SHM_MOUNT or not re.fullmatch(r"/dev/zram\d+", source):
                    return False
                device = os.stat(source)
                if not stat.S_ISBLK(device.st_mode):
                    return False
                backing = Path("/sys/class/block") / Path(source).name / "backing_dev"
                return not backing.exists() or backing.read_text(encoding="utf-8").strip() == "none"
    except OSError:
        return False
    return False


def memory_target(root: Path, pool: Path, target: Path) -> Path:
    """Create only inside an owned, same-filesystem tree under the mount."""
    if not is_memory_mount(root):
        raise ValueError(f"Memory mount unavailable: {root}")
    private_dir(pool)
    if pool.stat().st_dev != root.stat().st_dev:
        raise ValueError(f"Pool is a different mounted filesystem: {pool}")
    try:
        parts = target.relative_to(pool).parts
    except ValueError as exc:
        raise ValueError(f"Path is outside the memory pool: {target}") from exc
    if any(part in {".", ".."} for part in parts):
        raise ValueError(f"Relative path components are not allowed: {target}")
    current = pool
    for part in parts:
        current = private_dir(current / part)
        if current.stat().st_dev != root.stat().st_dev:
            raise ValueError(f"Nested mount inside memory pool is not allowed: {current}")
    return current


def storage_pool(requested: Path | None = None) -> Path:
    if requested is not None:
        requested = requested.expanduser().resolve()
        choices = [requested]
    else:
        choices = list(POOLS)
    for target in choices:
        try:
            if requested is None:
                memory_target(target.parent, target, target)
            else:
                target.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".dusky-write-test-", dir=target)
            os.close(fd)
            Path(name).unlink()
            if shutil.disk_usage(target).free < 512 * 1024 * 1024:
                console.print(f"[yellow]Warning:[/] less than 512 MiB free in {escape(str(target))}.")
            return target
        except (OSError, ValueError):
            if requested is not None:
                raise
    raise ValueError("No usable zram1 filesystem or /dev/shm tmpfs mount; refusing to write to disk.")


class TargetFormat(StrEnum):
    AUDIO_BEST = "audio-best"
    AUDIO_OPUS = "audio-opus"
    AUDIO_MP3 = "audio-mp3"
    AUDIO_FLAC = "audio-flac"
    AUDIO_M4A = "audio-m4a"
    AUDIO_WAV = "audio-wav"
    VIDEO_BEST = "video-best"
    VIDEO = "video"
    VIDEO_AV1 = "video-av1"
    VIDEO_VP9 = "video-vp9"
    VIDEO_MKV = "video-mkv"

    @property
    def is_video(self) -> bool:
        return self.value.startswith("video")


FORMAT_LABELS: Final = {
    TargetFormat.AUDIO_BEST: "best native audio; no promised lossless source",
    TargetFormat.AUDIO_OPUS: "Opus audio; transcode if necessary",
    TargetFormat.AUDIO_MP3: "MP3; 320K target bitrate",
    TargetFormat.AUDIO_FLAC: "FLAC encoding; lossy input stays lossy",
    TargetFormat.AUDIO_M4A: "M4A audio",
    TargetFormat.AUDIO_WAV: "WAV PCM; lossy input stays lossy",
    TargetFormat.VIDEO_BEST: "best video; MKV when a merge is required",
    TargetFormat.VIDEO: "compatible MP4; AVC/H.264 video and AAC audio only",
    TargetFormat.VIDEO_AV1: "AV1 video only; no codec fallback",
    TargetFormat.VIDEO_VP9: "VP9 video only; no codec fallback",
    TargetFormat.VIDEO_MKV: "best video remuxed to MKV",
}
QUALITY_CAPS: Final = (2160, 1440, 1080, 720, 480, 360)
URL_SPLIT: Final = re.compile(r"(?:,\s*|\s+)(?=https?://)", re.IGNORECASE)
RANGE_DASH: Final = re.compile(r"(-?\d+)-(-?\d+)")
RANGE_COLON: Final = re.compile(r"(-?\d+)?:(-?\d+)?(?::(-?\d+))?")


@dataclass(slots=True)
class CookieChoice:
    file: Path | None = None
    browser: str | None = None


@dataclass(slots=True)
class Details:
    title: str
    heights: list[int] = field(default_factory=list)
    uploader: str | None = None
    duration: int | None = None


@dataclass(slots=True)
class Entry:
    title: str
    url: str


@dataclass(slots=True)
class ProbeResult:
    entries: list[Entry]
    collection: bool
    label: str
    details: Details | None = None


@dataclass(slots=True)
class Job:
    title: str
    url: str
    mode: TargetFormat
    height: int | None


class Status(StrEnum):
    SUCCESS = "Success"
    SKIPPED = "Skipped"
    FAILED = "Failed"
    ABORTED = "Aborted"


@dataclass(slots=True)
class Report:
    title: str
    status: Status
    path: Path | None = None
    size: int = 0
    error: str = ""


def clean_label(text: object, width: int = 300) -> str:
    normalized = " ".join(str(text).split())
    return re.sub(r"[\x00-\x1f\x7f]", "", normalized)[:width]


def http_url(text: str) -> bool:
    try:
        parts = urlsplit(text)
        return (parts.scheme.lower() in {"http", "https"} and bool(parts.netloc)
                and not any(ord(ch) < 32 or ord(ch) == 127 or ch.isspace() for ch in text))
    except ValueError:
        return False


def url_label(url: str) -> str:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    if query.get("v"):
        return "watch?v=" + clean_label(query["v"][0], 60)
    slug = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1])
    slug = re.sub(r"\.(?:html?|mp4|mkv|webm|m4a|mp3|opus)$", "", slug, flags=re.I)
    slug = re.sub(r"^[A-Za-z]?\d+[A-Za-z0-9]*-", "", slug)
    return clean_label(re.sub(r"[-_+]+", " ", slug or parts.netloc)) or parts.netloc


def split_urls(value: str) -> list[str]:
    return [piece.strip().strip("\"'") for piece in URL_SPLIT.split(value.strip()) if piece.strip()]


def batch_file(path: Path) -> list[Entry]:
    items: list[Entry] = []
    with path.open(encoding="utf-8-sig") as source:
        for line_no, line in enumerate(source, start=1):
            value = line.strip().strip("\"'")
            if not value or value.startswith(("#", ";", "]", "//")):
                continue
            if not http_url(value):
                raise ValueError(f"{path}:{line_no}: expected one HTTP(S) URL per line")
            items.append(Entry(url_label(value), value))
    if not items:
        raise ValueError(f"Batch file has no URLs: {path}")
    return items


def normalize_position(value: int, total: int) -> int:
    if value == 0:
        raise ValueError("Positions are 1-based; zero is invalid")
    return total + value + 1 if value < 0 else value


def parse_positions(spec: str, total: int) -> list[int]:
    """Inclusive -I-style ranges; all results are unique zero-based indices."""
    text = spec.strip().lower()
    if not text or text in {"none", "no", "-"}:
        return []
    if text in {"all", "*"}:
        return list(range(total))
    if total < 1:
        return []
    result: list[int] = []
    seen: set[int] = set()
    for raw in text.split(","):
        token = raw.strip()
        if not token:
            raise ValueError(f"Empty position in {spec!r}")
        dash = RANGE_DASH.fullmatch(token)
        colon = RANGE_COLON.fullmatch(token)
        if dash:
            first, last = (normalize_position(int(v), total) for v in dash.groups())
            if first > last:
                raise ValueError(f"Use START:STOP:-1 for descending range {token!r}")
            step = 1
        elif colon:
            a, b, stride = colon.groups()
            step = int(stride) if stride is not None else 1
            if step == 0:
                raise ValueError("Range step cannot be zero")
            first = normalize_position(int(a), total) if a is not None else (1 if step > 0 else total)
            last = normalize_position(int(b), total) if b is not None else (total if step > 0 else 1)
        elif re.fullmatch(r"-?\d+", token):
            pos = normalize_position(int(token), total)
            if 1 <= pos <= total and pos - 1 not in seen:
                seen.add(pos - 1)
                result.append(pos - 1)
            continue
        else:
            raise ValueError(f"Invalid position or range {token!r}")
        if step > 0 and first <= last:
            start = first + max(0, (1 - first + step - 1) // step) * step
            positions = range(start, min(last, total) + 1, step)
        elif step < 0 and first >= last:
            magnitude = -step
            start = first - max(0, (first - total + magnitude - 1) // magnitude) * magnitude
            positions = range(start, max(last, 1) - 1, step)
        else:
            positions = range(0)
        for pos in positions:
            if 1 <= pos <= total and pos - 1 not in seen:
                seen.add(pos - 1)
                result.append(pos - 1)
    return result


def selected(entries: list[Entry], spec: str) -> list[Entry]:
    indices = parse_positions(spec, len(entries))
    if not indices:
        raise ValueError(f"No items matched {spec!r} in a collection of {len(entries)}")
    return [entries[i] for i in indices]


def cookie_choice(args: argparse.Namespace, state: Path) -> CookieChoice:
    if args.cookies and args.cookies_from_browser:
        raise ValueError("Use either --cookies or --cookies-from-browser, not both.")
    if args.cookies:
        path = args.cookies.expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError(f"Not a cookie file: {path}")
        return CookieChoice(file=path)
    if args.cookies_from_browser:
        return CookieChoice(browser=args.cookies_from_browser)
    default = state / "cookies.txt"
    if default.exists():
        if not default.is_file():
            raise ValueError(f"Invalid default cookie file: {default}")
        return CookieChoice(file=default)
    return CookieChoice()


@contextmanager
def cookie_flags(choice: CookieChoice, ram_dir: Path) -> Iterator[list[str]]:
    """Keep yt-dlp's mutable cookie jar away from the original shared file."""
    if choice.browser:
        yield ["--cookies-from-browser", choice.browser]
        return
    if choice.file is None:
        yield []
        return
    fd, name = tempfile.mkstemp(prefix=".dusky-cookies-", dir=ram_dir)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as destination, choice.file.open("rb") as source:
            shutil.copyfileobj(source, destination)
        yield ["--cookies", str(temporary)]
    finally:
        temporary.unlink(missing_ok=True)


def child_environment(ram_dir: Path) -> dict[str, str]:
    return dict(os.environ, TMPDIR=str(ram_dir), TMP=str(ram_dir), TEMP=str(ram_dir))


def signal_group(pgid: int, sig: int) -> None:
    if pgid <= 1:
        return
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass


def stop_probe(proc: subprocess.Popen[bytes]) -> None:
    signal_group(proc.pid, signal.SIGTERM)
    try:
        proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        signal_group(proc.pid, signal.SIGKILL)
        try:
            proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()


def probe(url: str, state: Path, cookies: CookieChoice, ram_dir: Path, *, full: bool = False) -> ProbeResult:
    """Flat playlist / full single-video metadata through a cancellable child."""
    terminated = False
    old_term = signal.getsignal(signal.SIGTERM)

    def mark_termination(_signum: int, _frame: object) -> None:
        nonlocal terminated
        terminated = True

    signal.signal(signal.SIGTERM, mark_termination)
    try:
        with cookie_flags(cookies, ram_dir) as flags:
            command = [
                "yt-dlp", "--ignore-config", "--no-js-runtimes",
                "--js-runtimes", "node", "--color", "never", "--cache-dir", str(state / "yt-dlp-cache"),
                "--socket-timeout", "25", "--retries", "3", "--extractor-retries", "3",
                "--dump-single-json", "--no-playlist" if full else "--flat-playlist",
                *flags, "--", url,
            ]
            proc = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                process_group=0, env=child_environment(ram_dir),
            )
            deadline = time.monotonic() + 300
            try:
                while True:
                    if terminated:
                        raise SystemExit(143)
                    try:
                        output, errors = proc.communicate(timeout=0.25)
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic() >= deadline:
                            raise ValueError(f"Metadata probe timed out after 300 seconds: {url}") from None
                if terminated:
                    raise SystemExit(143)
            except BaseException:
                stop_probe(proc)
                raise
    finally:
        signal.signal(signal.SIGTERM, old_term)
    if proc.returncode:
        tail = errors.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise ValueError(" | ".join(tail)[:650] or f"yt-dlp probe exited {proc.returncode}")
    try:
        info = json.loads(output)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("yt-dlp probe returned invalid JSON") from exc
    if not isinstance(info, dict):
        raise ValueError("yt-dlp probe returned no media object")
    if info.get("_type") in {"playlist", "multi_video"} or "entries" in info:
        if full:
            raise ValueError("Expected a single video, got a nested playlist")
        raw_entries = info.get("entries")
        if not isinstance(raw_entries, list):
            raise ValueError("Collection has no usable entry list")
        items: list[Entry] = []
        for number, item in enumerate(raw_entries, 1):
            if not isinstance(item, dict):
                continue
            if item.get("_type") in {"playlist", "multi_video"}:
                raise ValueError(f"Nested collection at position {number}; supply its URL separately")
            page = item.get("webpage_url")
            direct = item.get("url")
            video_id = item.get("id")
            if isinstance(page, str) and http_url(page) and page != url:
                item_url = page
            elif isinstance(direct, str) and http_url(direct):
                item_url = direct
            elif str(item.get("ie_key") or "").lower() == "youtube" and video_id:
                item_url = f"https://www.youtube.com/watch?v={video_id}"
            else:
                raise ValueError(f"Unresolvable flat entry at position {number}; not passing a bare ID to yt-dlp")
            items.append(Entry(clean_label(item.get("title") or url_label(item_url)), item_url))
        if not items:
            raise ValueError("Collection contained no downloadable entries")
        return ProbeResult(items, True, clean_label(info.get("title") or "Collection"))
    title = clean_label(info.get("title") or url_label(url))
    page = info.get("webpage_url")
    video_url = page if isinstance(page, str) and http_url(page) else url
    heights = sorted({
        fmt["height"] for fmt in info.get("formats") or []
        if isinstance(fmt, dict) and isinstance(fmt.get("height"), int)
        and not isinstance(fmt.get("height"), bool) and fmt["height"] > 0
    }, reverse=True)
    raw_duration = info.get("duration")
    duration = int(raw_duration) if isinstance(raw_duration, (int, float)) and math.isfinite(raw_duration) and raw_duration >= 0 else None
    uploader = info.get("uploader") or info.get("channel")
    return ProbeResult(
        [Entry(title, video_url)], False, title,
        Details(title, heights, clean_label(uploader) if uploader else None, duration),
    )


def ask_text(question: str, history: FileHistory, *, default: str = "", paths: bool = False) -> str:
    console.print(f"[green]?[/] {escape(question)}")
    try:
        if sys.stdin.isatty():
            answer = toolkit_prompt(
                ">: ", history=history,
                completer=PathCompleter(expanduser=True) if paths else None,
                enable_history_search=True,
            )
        else:
            answer = input(">: ")
    except EOFError:
        if default:
            return default
        raise ValueError("Input ended before a target was provided") from None
    return answer.strip() or default


def ask_yes(question: str, history: FileHistory, *, default: bool = False) -> bool:
    while True:
        answer = ask_text(f"{question} ({'Y/n' if default else 'y/N'})", history,
                          default="y" if default else "n").lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        console.print("[red]Type y or n.[/]")


def stop_fzf(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.terminate()
    try:
        proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            raise OSError(f"fzf PID {proc.pid} did not exit after SIGKILL") from None


def fzf_lines(arguments: list[str], choices: list[str], timeout: float) -> tuple[int, list[str]]:
    """Keep fzf in the foreground TTY group; reap it on interruption."""
    terminated = False
    previous = signal.getsignal(signal.SIGTERM)

    def mark_termination(_signum: int, _frame: object) -> None:
        nonlocal terminated
        terminated = True

    signal.signal(signal.SIGTERM, mark_termination)
    try:
        proc = subprocess.Popen(
            [shutil.which("fzf") or "fzf", *arguments], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + timeout
            supplied: bytes | None = ("\n".join(choices) + "\n").encode("utf-8")
            while True:
                if terminated:
                    raise SystemExit(143)
                try:
                    output, _ = proc.communicate(input=supplied, timeout=0.25)
                    if terminated:
                        raise SystemExit(143)
                    return proc.returncode, output.decode("utf-8", "replace").splitlines()
                except subprocess.TimeoutExpired:
                    supplied = None  # communicate() accepts input only on its first call.
                    if time.monotonic() >= deadline:
                        raise ValueError("fzf timed out; try the typed picker") from None
        except BaseException:
            stop_fzf(proc)
            raise
    finally:
        signal.signal(signal.SIGTERM, previous)


def pick_one(question: str, choices: list[str], default: str, history: FileHistory) -> str:
    executable = shutil.which("fzf")
    if executable and sys.stdin.isatty() and sys.stdout.isatty():
        try:
            code, lines = fzf_lines(
                ["--height", "45%", "--reverse", "--prompt", question + "> "],
                choices, 180,
            )
        except (OSError, ValueError) as exc:
            console.print(f"[yellow]fzf unavailable: {escape(str(exc))}; using typed input.[/]")
        else:
            if code == 130:
                raise KeyboardInterrupt
            if code == 0 and lines and lines[0] in choices:
                return lines[0]
            if code == 1:
                return default
    while True:
        console.print(escape(" / ".join(choices)))
        chosen = ask_text(question + f" [{default}]", history, default=default)
        if chosen in choices:
            return chosen
        console.print("[red]Select one of the listed values.[/]")


def preview(entries: list[Entry]) -> None:
    console.print(f"[dim]{len(entries)} queued item(s); positions are 1-based.[/]")
    length = len(entries)
    numbers = list(range(length)) if length <= 35 else [*range(20), *range(length - 10, length)]
    for index in numbers:
        console.print(f"[dim]{index + 1:>4}[/] {escape(clean_label(entries[index].title, 120))}")
    if length > 35:
        console.print(f"[dim]... {length - 30} other items (fzf can search them all).[/]")


def wizard_skips(entries: list[Entry], history: FileHistory) -> list[Entry]:
    if len(entries) < 2:
        return entries
    preview(entries)
    executable = shutil.which("fzf")
    if executable and sys.stdin.isatty() and sys.stdout.isatty():
        choices = [f"{i:0{len(str(len(entries)))}d}. {clean_label(item.title, 140)} | {item.url}"
                   for i, item in enumerate(entries, 1)]
        while True:
            try:
                code, lines = fzf_lines(
                    ["--multi", "--reverse", "--height", "60%", "--prompt", "Skip> ",
                     "--header", "TAB marks items; ESC/Ctrl-C cancels; ENTER may select the highlighted item"], choices, 600,
                )
            except (OSError, ValueError) as exc:
                console.print(f"[yellow]fzf unavailable: {escape(str(exc))}; using typed ranges.[/]")
                break
            if code == 130:
                raise KeyboardInterrupt
            by_line = {line: index for index, line in enumerate(choices)}
            dropped = {by_line[line] for line in lines if line in by_line}
            if not dropped or code != 0:
                return entries
            kept = [entry for i, entry in enumerate(entries) if i not in dropped]
            if kept or ask_yes("Skip every item?", history):
                return kept
    while True:
        spec = ask_text("Skip positions (none, 1,3,5-7, -3:, or list)", history, default="none")
        if spec.lower() == "list":
            preview(entries)
            continue
        try:
            dropped = set(parse_positions(spec, len(entries)))
        except ValueError as exc:
            console.print(f"[red]{escape(str(exc))}[/]")
            continue
        if spec.lower() not in {"none", "no", "-"} and not dropped:
            console.print("[red]No positions matched; type none to keep all.[/]")
            continue
        kept = [entry for i, entry in enumerate(entries) if i not in dropped]
        if kept or ask_yes("Skip every item?", history):
            return kept


def collection_range(entries: list[Entry], history: FileHistory) -> list[Entry]:
    if len(entries) > 1 and ask_yes("Reverse collection order?", history):
        entries.reverse()
    while True:
        value = ask_text("Collection range (all, 1-3, -3:, ::-1)", history, default="all")
        try:
            return selected(entries, value)
        except ValueError as exc:
            console.print(f"[red]{escape(str(exc))}[/]")


def collect_sources(
    values: list[str], state: Path, cookies: CookieChoice, ram_dir: Path,
    playlist_spec: str | None, history: FileHistory, *, wizard: bool,
) -> tuple[list[Entry], Details | None, list[str]]:
    all_entries: list[Entry] = []
    details: Details | None = None
    errors: list[str] = []
    for value in values:
        target = Path(value).expanduser()
        if target.is_file():
            try:
                all_entries.extend(batch_file(target))
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
            continue
        if not http_url(value):
            errors.append(f"Not an HTTP(S) URL or readable batch file: {clean_label(value)}")
            continue
        try:
            with console.status(f"[cyan]Probing {escape(clean_label(value, 85))}[/]"):
                result = probe(value, state, cookies, ram_dir)
            items = result.entries
            if result.collection:
                console.print(f"[cyan]{escape(result.label)}[/] - {len(items)} item(s)")
                items = collection_range(items, history) if wizard and playlist_spec is None else selected(items, playlist_spec or "all")
            elif len(values) == 1:
                details = result.details
                if wizard and (details is None or not details.heights):
                    try:
                        with console.status("[cyan]Inspecting full video metadata[/]"):
                            inspected = probe(items[0].url, state, cookies, ram_dir, full=True)
                        details = inspected.details
                    except (OSError, ValueError) as exc:
                        console.print(f"[yellow]Full format inspection unavailable:[/] {escape(str(exc))}")
            all_entries.extend(items)
        except (OSError, ValueError) as exc:
            errors.append(f"{clean_label(value, 110)}: {exc}")
    return all_entries, details, errors


def deduplicate(entries: list[Entry]) -> list[Entry]:
    seen: set[str] = set()
    result: list[Entry] = []
    for entry in entries:
        if entry.url not in seen:
            seen.add(entry.url)
            result.append(entry)
    return result


def choose_height(details: Details | None, history: FileHistory) -> int | None:
    maximum = max(details.heights) if details and details.heights else None
    caps = [cap for cap in QUALITY_CAPS if maximum is None or cap <= maximum]
    if maximum is not None and not caps:
        caps = [maximum]
    options = ["best", *(str(value) for value in caps)]
    chosen = pick_one("Maximum video height (pixels)", options, "best", history)
    return None if chosen == "best" else int(chosen)


def worker_count(requested: int | None, count: int, history: FileHistory) -> int:
    if requested is not None:
        if requested < 1:
            raise ValueError("--concurrent must be positive")
        return min(requested, max(count, 1))
    default = min(DEFAULT_WORKERS, max(count, 1))
    if count < 2 or not sys.stdin.isatty():
        return default
    while True:
        raw = ask_text(f"Concurrent jobs (1..{min(MAX_INTERACTIVE_WORKERS, count)})", history, default=str(default))
        if raw.isdecimal() and 1 <= int(raw) <= min(MAX_INTERACTIVE_WORKERS, count):
            return int(raw)
        console.print("[red]Enter a valid worker count.[/]")


def build_jobs(
    args: argparse.Namespace, state: Path, initial_pool: Path, history: FileHistory, cookies: CookieChoice,
) -> tuple[list[Job], Path, int, bool, int]:
    wizard = not args.target
    if wizard:
        console.print("[bold cyan]Dusky Universal Media Downloader[/]")
        while True:
            raw = ask_text("Enter a URL, comma-separated URLs, playlist, or batch file", history, paths=True)
            path = Path(raw.strip("\"'")).expanduser()
            targets = [str(path)] if path.is_file() else split_urls(raw)
            entries, details, errors = collect_sources(
                targets, state, cookies, initial_pool, args.playlist_items, history, wizard=True,
            )
            for error in errors:
                console.print(f"[yellow]Probe skipped:[/] {escape(error)}")
            if entries:
                break
            console.print("[red]No targets resolved. Try another link.[/]")
        if details:
            suffix = f" | {details.uploader}" if details.uploader else ""
            if details.duration is not None:
                minutes, seconds = divmod(details.duration, 60)
                suffix += f" | {minutes}:{seconds:02d}"
            suffix += f" | up to {details.heights[0]}p" if details.heights else ""
            console.print(f"[yellow]{escape(details.title)}[/][dim]{escape(suffix)}[/]")
        if args.format is None:
            for mode, label in FORMAT_LABELS.items():
                console.print(f"[dim]{mode.value:12}[/] {escape(label)}")
        mode = TargetFormat(args.format or pick_one(
            "Delivery format", [fmt.value for fmt in TargetFormat], "audio-best", history,
        ))
        height = None if not mode.is_video else (
            choose_height(details, history) if args.quality is None else
            (None if args.quality == "best" else int(args.quality))
        )
    else:
        tokens: list[str] = []
        for raw in args.target:
            path = Path(raw.strip("\"'")).expanduser()
            tokens.extend([str(path)] if path.is_file() else split_urls(raw))
        entries, _, errors = collect_sources(tokens, state, cookies, initial_pool, args.playlist_items, history, wizard=False)
        for error in errors:
            console.print(f"[yellow]Probe skipped:[/] {escape(error)}")
        mode = TargetFormat(args.format or "audio-best")
        height = None if args.quality in {None, "best"} else int(args.quality)
    if not mode.is_video and height is not None:
        console.print("[yellow]Video quality does not apply to audio; ignoring.[/]")
        height = None
    unique = deduplicate(entries)
    had_targets = bool(unique)
    if len(unique) != len(entries):
        console.print(f"[dim]Removed {len(entries) - len(unique)} duplicate URL(s).[/]")
    if args.skip_items is not None:
        skip = set(parse_positions(args.skip_items, len(unique)))
        if not skip and args.skip_items.lower() not in {"none", "no", "-"}:
            raise ValueError(f"--skip-items {args.skip_items!r} matched no queue positions")
        unique = [entry for i, entry in enumerate(unique) if i not in skip]
    elif wizard:
        unique = wizard_skips(unique, history)
    jobs = [Job(item.title, item.url, mode, height) for item in unique]
    workers = worker_count(args.concurrent, len(jobs), history) if jobs else 1
    destination = initial_pool
    if wizard and args.output_dir is None and jobs:
        while True:
            requested = ask_text("Output directory", history, default=str(initial_pool), paths=True)
            try:
                destination = storage_pool(Path(requested))
                break
            except (OSError, ValueError) as exc:
                console.print(f"[red]{escape(str(exc))}[/]")
    return jobs, destination, workers, had_targets, len(errors)


def format_arguments(mode: TargetFormat, height: int | None) -> list[str]:
    cap = f"[height<=?{height}]" if height is not None else ""
    if not mode.is_video:
        audio = {
            TargetFormat.AUDIO_BEST: ["ba/b", "best"],
            TargetFormat.AUDIO_OPUS: ["ba[acodec^=opus]/ba/b", "opus"],
            TargetFormat.AUDIO_MP3: ["ba/b", "mp3"],
            TargetFormat.AUDIO_FLAC: ["ba/b", "flac"],
            TargetFormat.AUDIO_M4A: ["ba/b", "m4a"],
            TargetFormat.AUDIO_WAV: ["ba/b", "wav"],
        }[mode]
        result = ["-f", audio[0], "-x", "--audio-format", audio[1]]
        if mode is TargetFormat.AUDIO_MP3:
            result += ["--audio-quality", "320K"]
        elif mode is TargetFormat.AUDIO_OPUS:
            result += ["--audio-quality", "0"]
        return result
    if mode is TargetFormat.VIDEO:
        v = "bv[vcodec~='^(avc1|h264)']"
        a = "ba[acodec~='^(mp4a|aac)']"
        # Unknown direct-file metadata is accepted here and checked by FFprobe.
        combined = "b[vcodec~=?'^(avc1|h264)'][acodec~=?'^(mp4a|aac)']"
        return ["-f", f"{v}{cap}+{a}/{combined}{cap}", "-S", "res,fps,quality",
                "--merge-output-format", "mp4", "--remux-video", "mp4"]
    if mode in {TargetFormat.VIDEO_AV1, TargetFormat.VIDEO_VP9}:
        codec = "[vcodec^=av01]" if mode is TargetFormat.VIDEO_AV1 else "[vcodec~='^(vp9|vp09)']"
        unknown_codec = codec.replace("~=", "~=?").replace("^=", "^=?")
        v, combined = f"bv{codec}{cap}", f"b{unknown_codec}{cap}"
        return ["-f", f"{v}+ba/{combined}/{v}", "-S", "res,fps,quality",
                "--merge-output-format", "mkv"]
    best = f"bv{cap}+ba/b{cap}/bv{cap}"
    result = ["-f", best, "-S", "res,fps,quality", "--merge-output-format", "mkv"]
    if mode is TargetFormat.VIDEO_MKV:
        result += ["--remux-video", "mkv"]
    return result


def archive_path(state: Path, mode: TargetFormat) -> Path:
    path = state / f"archive-{mode.value}.txt"
    os.close(private_file(path))
    return path


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    descriptor = private_file(path)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def archive_snapshot(state: Path, archive: Path, work_dir: Path) -> tuple[Path, set[bytes]]:
    with exclusive_lock(state / "locks" / (archive.name + ".lock")):
        with os.fdopen(private_file(archive, os.O_RDONLY), "rb") as source:
            original = source.read()
    descriptor, name = tempfile.mkstemp(prefix=".dusky-archive-", dir=work_dir)
    with os.fdopen(descriptor, "wb") as copy:
        copy.write(original)
    return Path(name), set(original.splitlines())


def new_archive_entry(snapshot: Path, old_lines: set[bytes]) -> bytes:
    new = list(dict.fromkeys(line for line in snapshot.read_bytes().splitlines() if line and line not in old_lines))
    if len(new) != 1 or len(new[0]) > 2048:
        raise ValueError(f"Expected one yt-dlp archive record for one job; found {len(new)}")
    return new[0]


def commit_archive(state: Path, archive: Path, line: bytes) -> None:
    with exclusive_lock(state / "locks" / (archive.name + ".lock")):
        with os.fdopen(private_file(archive, os.O_RDONLY), "rb") as source:
            current = source.read()
        if line in set(current.splitlines()):
            return
        descriptor, name = tempfile.mkstemp(prefix=".dusky-archive-commit-", dir=state)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(current)
                if current and not current.endswith(b"\n"):
                    output.write(b"\n")
                output.write(line + b"\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, archive)
            directory_fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)


@dataclass(slots=True)
class MediaState:
    title: str = ""
    stage: str = "Starting"
    downloaded: int = 0
    total: int | None = None
    percent: float = 0.0
    speed: float | None = None
    eta: int | None = None
    stream_key: str = ""
    attempted: bool = False
    archive_skip: bool = False
    final_paths: list[str] = field(default_factory=list)
    errors: deque[str] = field(default_factory=lambda: deque(maxlen=8))
    tail: deque[str] = field(default_factory=lambda: deque(maxlen=8))


def finite_number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


class ProgressParser:
    """OVD-style RAW metrics, carried as yt-dlp's JSON progress object."""

    def __init__(self, initial_title: str):
        self.state = MediaState(title=initial_title)

    def line(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if text.startswith("RAW|"):
            try:
                data = json.loads(text[4:])
                if isinstance(data, dict):
                    self.download(data)
            except ValueError:
                self.state.errors.append("Invalid yt-dlp RAW progress JSON")
            return
        if text.startswith("POST|"):
            try:
                data = json.loads(text[5:])
                if isinstance(data, dict):
                    key = str(data.get("postprocessor") or "").lower()
                    self.state.stage = (
                        "Merging" if "merger" in key else
                        "Remuxing" if "remux" in key else
                        "Reencoding" if "convertor" in key else
                        "Extracting audio" if "extractaudio" in key else "Finalizing"
                    )
            except ValueError:
                self.state.errors.append("Invalid yt-dlp postprocess JSON")
            return
        for prefix, attribute in (("FILE|", "file"), ("TITLE|", "title"), ("BEGIN|", "begin")):
            if text.startswith(prefix):
                try:
                    value = json.loads(text[len(prefix):])
                except ValueError:
                    self.state.errors.append(f"Malformed {attribute} hook from yt-dlp")
                    return
                if attribute == "file" and isinstance(value, str):
                    self.state.final_paths.append(value)
                    self.state.stage = "Finalizing"
                elif attribute == "title" and isinstance(value, str):
                    self.state.title = clean_label(value)
                elif attribute == "begin":
                    self.state.attempted = True
                return
        if "has already been recorded in the archive" in text:
            self.state.archive_skip = True
        for tag, label in (("[Merger]", "Merging"), ("[VideoRemuxer]", "Remuxing"),
                           ("[VideoConvertor]", "Reencoding"), ("[ExtractAudio]", "Extracting audio")):
            if text.startswith(tag):
                self.state.stage = label
                break
        simple = clean_label(text, 320)
        self.state.tail.append(simple)
        if simple.startswith(("ERROR:", "WARNING:")) or "Error while" in simple:
            self.state.errors.append(simple)

    def download(self, data: dict[str, Any]) -> None:
        state = self.state
        key = str(data.get("filename") or "")
        if key and key != state.stream_key:
            state.stream_key = key
            state.downloaded, state.total, state.percent = 0, None, 0.0
        state.stage = "Downloading" if data.get("status") == "downloading" else "Processing"
        downloaded = finite_number(data.get("downloaded_bytes"))
        total = finite_number(data.get("total_bytes")) or finite_number(data.get("total_bytes_estimate"))
        speed = finite_number(data.get("speed"))
        eta = finite_number(data.get("eta"))
        if downloaded is not None:
            state.downloaded = int(downloaded)
        state.total = int(total) if total is not None and total >= 1 else None
        state.speed = speed
        state.eta = int(eta) if eta is not None else None
        if state.total:
            percentage = 100 * state.downloaded / state.total
        else:
            fragment = finite_number(data.get("fragment_index"))
            count = finite_number(data.get("fragment_count"))
            if fragment is not None and count:
                percentage = 100 * fragment / count
            else:
                percentage = state.percent
        state.percent = min(100.0, max(0.0, percentage))
        if data.get("status") == "finished":
            state.percent = 100.0


def translate_error(message: str, mode: TargetFormat) -> str:
    lowered = message.lower()
    hints = (
        ("requested format is not available", "Requested codec/height is absent; try video-best or a higher cap."),
        ("sign in", "This source may require valid login cookies."),
        ("not a bot", "Bot challenge: retry later or supply authorized cookies."),
        ("http error 429", "Rate limited: wait before retrying."),
        ("http error 403", "Access denied: check cookies, region, or link permissions."),
        ("no supported javascript runtime", "Node/EJS unavailable: check node --version and pacman -Q yt-dlp-ejs."),
        ("signature solving failed", "YouTube challenge solver failed: check Node and yt-dlp-ejs."),
        ("unable to obtain file audio codec", "Source has no audio track; try a video format."),
    )
    for signature, hint in hints:
        if signature in lowered:
            return f"{message} | {hint}"
    if mode is TargetFormat.VIDEO:
        return f"{message} | Compatible MP4 requires an existing AVC/AAC format; try video-best."
    return message


@dataclass(slots=True)
class Slot:
    number: int
    task_id: int
    busy: bool = False
    title: str = ""
    proc: subprocess.Popen[bytes] | None = None
    pgid: int | None = None
    stop_reason: str = ""
    stop_at: float | None = None
    escalated: bool = False


class QueueController:
    def __init__(self, slots: list[Slot]):
        self.slots = slots
        self.lock = threading.Lock()
        self.aborted = threading.Event()
        self.reason = ""

    def claim(self, title: str) -> Slot | None:
        with self.lock:
            if self.aborted.is_set():
                return None
            for slot in self.slots:
                if not slot.busy:
                    slot.busy = True
                    slot.title = title
                    slot.stop_reason = ""
                    slot.stop_at = None
                    slot.escalated = False
                    return slot
            raise RuntimeError("All slots are occupied beyond max_workers")

    def finish(self, slot: Slot, progress: Progress) -> None:
        with self.lock:
            try:
                progress.update(slot.task_id, title=f"W{slot.number} idle", description="", completed=0, metric="")
            finally:
                slot.busy = False
                slot.title = ""
                slot.proc = None
                slot.pgid = None
                slot.stop_reason = ""
                slot.stop_at = None

    def cancellation(self, slot: Slot) -> str:
        with self.lock:
            return "abort" if self.aborted.is_set() else slot.stop_reason

    def spawn(self, slot: Slot, command: list[str], work_dir: Path) -> subprocess.Popen[bytes] | None:
        with self.lock:
            if self.aborted.is_set() or slot.stop_reason:
                return None
            proc = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                process_group=0, cwd=work_dir, env=child_environment(work_dir), bufsize=0,
            )
            slot.proc = proc
            slot.pgid = proc.pid
            return proc

    def reap(self, slot: Slot, proc: subprocess.Popen[bytes]) -> None:
        """Leave no stale PGID registered between yt-dlp and ffprobe."""
        if proc.poll() is None:
            signal_group(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                signal_group(proc.pid, signal.SIGKILL)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.poll() is None:
            raise OSError(f"PID {proc.pid} did not exit after SIGKILL; inspect the host process table")
        with self.lock:
            if slot.proc is proc:
                slot.proc = None
                slot.pgid = None
                slot.stop_at = None

    def _stop(self, slot: Slot, reason: str) -> None:
        if slot.stop_reason != "abort":
            slot.stop_reason = reason
        if slot.stop_at is None:
            slot.stop_at = time.monotonic()
        if slot.pgid is not None and slot.proc is not None and slot.proc.poll() is None:
            signal_group(slot.pgid, signal.SIGTERM)

    def skip_one(self, number: int) -> str:
        with self.lock:
            if not 1 <= number <= len(self.slots):
                return f"Worker {number} does not exist."
            slot = self.slots[number - 1]
            if not slot.busy:
                return f"W{number} is idle; next job is unaffected."
            self._stop(slot, "skip")
            return f"Skipping W{number}: {slot.title}"

    def skip_all(self) -> str:
        with self.lock:
            current = [slot for slot in self.slots if slot.busy]
            for slot in current:
                self._stop(slot, "skip")
            return f"Skipping {len(current)} current job(s)." if current else "No current jobs; next jobs are unaffected."

    def abort(self, reason: str) -> None:
        with self.lock:
            if self.aborted.is_set():
                return
            self.reason = reason
            self.aborted.set()
            for slot in self.slots:
                if slot.busy:
                    self._stop(slot, "abort")

    def escalate(self, slot: Slot) -> None:
        with self.lock:
            if slot.stop_at is not None and not slot.escalated and time.monotonic() - slot.stop_at >= 3:
                slot.escalated = True
                if slot.pgid is not None:
                    signal_group(slot.pgid, signal.SIGKILL)

    def force_kill(self, slot: Slot) -> None:
        with self.lock:
            if slot.pgid is not None:
                signal_group(slot.pgid, signal.SIGKILL)


class StopRequested(Exception):
    pass


@contextmanager
def job_guard(state: Path, job: Job, controller: QueueController, slot: Slot) -> Iterator[str]:
    identity = hashlib.sha256(f"{job.mode.value}\0{job.url}".encode()).hexdigest()
    lock_path = state / "locks" / f"job-{identity}.lock"
    descriptor = private_file(lock_path)
    acquired = False
    try:
        while not acquired:
            if controller.cancellation(slot):
                raise StopRequested
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                time.sleep(0.15)
        yield identity
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def ytdlp_command(job: Job, work: Path, state: Path, archive: Path, cookie_args: list[str]) -> list[str]:
    template = str(work / "%(title).180B [%(id)s].%(ext)s")
    return [
        "yt-dlp", "--ignore-config", "--no-js-runtimes",
        "--js-runtimes", "node", "--color", "never", "--encoding", "utf-8",
        "--cache-dir", str(state / "yt-dlp-cache"), "--no-playlist", "--no-simulate",
        "--abort-on-error", "--abort-on-unavailable-fragment",
        "--newline", "--progress", "--progress-delta", "0.5",
        "--progress-template", "download:RAW|%(progress)j",
        "--progress-template", "postprocess:POST|%(progress)j",
        "--print", "before_dl:BEGIN|%(id)j",
        "--print", "before_dl:TITLE|%(title)j",
        "--print", "after_move:FILE|%(filepath)j",
        "--no-quiet",  # Archive skips must be distinguishable from a missing final file.
        "--concurrent-fragments", "4", "--retries", "15", "--fragment-retries", "15",
        "--file-access-retries", "5", "--extractor-retries", "5",
        "--retry-sleep", "fragment:exp=1:15", "--retry-sleep", "http:exp=1:15",
        "--socket-timeout", "30", "--windows-filenames", "--embed-metadata", "--embed-chapters",
        "--download-archive", str(archive), *cookie_args, *format_arguments(job.mode, job.height),
        "-o", template, "--", job.url,
    ]


def bytes_label(size: float) -> str:
    if size >= 1024 ** 2:
        return f"{size / 1024 ** 2:.1f} MiB"
    if size >= 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size:.0f} B"


def render(progress: Progress, controller: QueueController, slot: Slot, state: MediaState) -> None:
    percent = max(0.0, min(100.0, state.percent))
    detail = f"{percent:.1f}% {bytes_label(state.downloaded)}"
    if state.speed is not None:
        detail += f" {bytes_label(state.speed)}/s"
    if state.eta is not None and state.stage == "Downloading":
        detail += f" ETA {state.eta}s"
    title = clean_label(state.title, 46)
    with controller.lock:
        slot.title = state.title
    progress.update(slot.task_id, completed=percent, title=f"W{slot.number} {escape(title)}",
                    description=state.stage, metric=detail)


def pump(
    proc: subprocess.Popen[bytes], slot: Slot, controller: QueueController,
    parser: ProgressParser, progress: Progress,
) -> int | None:
    """Drain one pipe non-blockingly; a dead child cannot block a second pipe."""
    assert proc.stdout is not None
    pending = bytearray()
    parent_exited: float | None = None
    last_render = 0.0
    stale_pipe = False

    def consume(raw: bytes) -> None:
        parser.line(raw.decode("utf-8", "replace"))

    with selectors.DefaultSelector() as selector:
        try:
            os.set_blocking(proc.stdout.fileno(), False)
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                controller.escalate(slot)
                for key, _ in selector.select(0.12):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        if pending:
                            consume(bytes(pending))
                            pending.clear()
                        continue
                    pending.extend(chunk)
                    while True:
                        newline, carriage = pending.find(b"\n"), pending.find(b"\r")
                        positions = [pos for pos in (newline, carriage) if pos >= 0]
                        if not positions:
                            break
                        end = min(positions)
                        line = bytes(pending[:end])
                        del pending[:end + 1]
                        if line:
                            consume(line)
                    if len(pending) > 262144:
                        parser.state.errors.append("yt-dlp emitted an overlong output line")
                        pending.clear()
                now = time.monotonic()
                if now - last_render >= 0.16:
                    render(progress, controller, slot, parser.state)
                    last_render = now
                if proc.poll() is None:
                    continue
                if not selector.get_map():
                    break
                if parent_exited is None:
                    parent_exited = now
                if now - parent_exited > 5:
                    controller.force_kill(slot)
                if now - parent_exited > 7:
                    parser.state.errors.append("Descendant kept the output pipe open after yt-dlp exited")
                    stale_pipe = True
                    break
            try:
                code = proc.wait(timeout=2)
                return -1 if stale_pipe else code
            except subprocess.TimeoutExpired:
                controller.force_kill(slot)
                try:
                    code = proc.wait(timeout=2)
                    return -1 if stale_pipe else code
                except subprocess.TimeoutExpired:
                    parser.state.errors.append(f"Unreaped child PID {proc.pid} (uninterruptible I/O)")
                    return None
        except BaseException:
            controller.force_kill(slot)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                parser.state.errors.append(f"Unreaped child PID {proc.pid} after monitor error")
            raise
        finally:
            proc.stdout.close()


def checked_file(path_text: str, work: Path) -> Path:
    path = Path(path_text).resolve(strict=True)
    if not path.is_relative_to(work.resolve(strict=True)) or not path.is_file():
        raise ValueError(f"yt-dlp output escaped job work directory: {path}")
    if path.stat().st_size <= 0:
        raise ValueError(f"yt-dlp produced an empty file: {path}")
    return path


def verify_media(
    path: Path, mode: TargetFormat, height: int | None,
    controller: QueueController, slot: Slot, work: Path,
) -> None:
    command = ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,height",
               "-of", "json", "-i", str(path)]
    proc = controller.spawn(slot, command, work)
    if proc is None:
        raise StopRequested
    started = time.monotonic()
    try:
        while True:
            try:
                output, _ = proc.communicate(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                controller.escalate(slot)
                if time.monotonic() - started > 45:
                    raise ValueError("ffprobe exceeded 45 seconds") from None
    finally:
        controller.reap(slot, proc)
    if controller.cancellation(slot):
        raise StopRequested
    if proc.returncode:
        raise ValueError(f"ffprobe rejected {path.name}: {clean_label(output.decode('utf-8', 'replace'), 240)}")
    metadata = json.loads(output)
    streams = metadata.get("streams") if isinstance(metadata, dict) else None
    if not isinstance(streams, list) or not all(isinstance(stream, dict) for stream in streams):
        raise ValueError(f"ffprobe did not return a stream list for {path.name}")
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    video = {stream["codec_name"] for stream in video_streams
             if isinstance(stream.get("codec_name"), str) and stream["codec_name"] != "unknown"}
    audio = {stream["codec_name"] for stream in streams if stream.get("codec_type") == "audio"
             and isinstance(stream.get("codec_name"), str) and stream["codec_name"] != "unknown"}
    if mode.is_video and not video:
        raise ValueError("Downloaded file has no video track")
    if height is not None and any(
        not isinstance(stream.get("height"), int) or isinstance(stream.get("height"), bool)
        or stream["height"] <= 0 or stream["height"] > height
        for stream in video_streams
    ):
        raise ValueError(f"Downloaded video exceeds the {height}p cap or has unknown height")
    if not mode.is_video and (not audio or video):
        raise ValueError("Audio delivery is missing an audio track or still contains video")
    if mode is TargetFormat.VIDEO and (path.suffix.lower() != ".mp4" or "h264" not in video or "aac" not in audio):
        raise ValueError("Compatibility MP4 is not actually H.264/AAC")
    if mode is TargetFormat.VIDEO_AV1 and "av1" not in video:
        raise ValueError("Requested AV1 but decoded codec is not AV1")
    if mode is TargetFormat.VIDEO_VP9 and "vp9" not in video:
        raise ValueError("Requested VP9 but decoded codec is not VP9")
    if mode is TargetFormat.VIDEO_MKV and path.suffix.lower() != ".mkv":
        raise ValueError("MKV delivery was not remuxed to MKV")
    expected_audio = {
        TargetFormat.AUDIO_OPUS: ("opus", ".opus"),
        TargetFormat.AUDIO_MP3: ("mp3", ".mp3"),
        TargetFormat.AUDIO_FLAC: ("flac", ".flac"),
    }
    if mode in expected_audio:
        codec, suffix = expected_audio[mode]
        if codec not in audio or path.suffix.lower() != suffix:
            raise ValueError(f"Audio delivery is not {codec} in {suffix}")
    if mode is TargetFormat.AUDIO_WAV and (path.suffix.lower() != ".wav" or
                                            not any(codec.startswith("pcm_") for codec in audio)):
        raise ValueError("WAV delivery is not PCM in .wav")
    if mode is TargetFormat.AUDIO_M4A and (path.suffix.lower() != ".m4a" or "aac" not in audio):
        raise ValueError("M4A delivery is not AAC in .m4a")


def same_content(first: Path, second: Path) -> bool:
    if first.stat().st_size != second.stat().st_size:
        return False
    hashes = []
    for path in (first, second):
        digest = hashlib.sha256()
        with path.open("rb") as media:
            for chunk in iter(lambda: media.read(1024 * 1024), b""):
                digest.update(chunk)
        hashes.append(digest.digest())
    return hashes[0] == hashes[1]


def mark_url_downloaded(urls_path: Path, url: str) -> None:
    lock_path = urls_path.parent / f".{urls_path.name}.lock"
    with exclusive_lock(lock_path):
        try:
            content = urls_path.read_text(encoding="utf-8-sig")
            lines = content.splitlines(keepends=True)
            changed = False
            new_lines = []
            for line in lines:
                if line.strip() == url:
                    new_lines.append(f"# DOWNLOADED {url}\n")
                    changed = True
                else:
                    new_lines.append(line)
            if changed:
                temp_path = urls_path.with_name(f".tmp-{urls_path.name}-{os.getpid()}")
                temp_path.write_text("".join(new_lines), encoding="utf-8")
                temp_path.replace(urls_path)
        except OSError:
            pass


def publish(source: Path, destination: Path, move_to: Path | None = None) -> tuple[Path, bool]:
    target_dir = move_to if move_to is not None else destination
    target = target_dir / source.name
    if target.exists():
        if not target.is_file() or not same_content(source, target):
            raise ValueError(f"Output name collision; refusing to replace a different file: {target}") from None
        source.unlink()
        return target, True
    if move_to is not None:
        temp_name = f".part-{os.getpid()}-{hashlib.sha256(target.name.encode()).hexdigest()[:16]}.tmp"
        temp_target = target_dir / temp_name
        try:
            shutil.copyfile(source, temp_target)
            temp_target.replace(target)
        finally:
            temp_target.unlink(missing_ok=True)
        source.unlink()
        return target, False
    try:
        os.link(source, target)
        existed = False
    except FileExistsError:
        if not target.is_file() or not same_content(source, target):
            raise ValueError(f"Output name collision; refusing to replace a different file: {target}") from None
        existed = True
    source.unlink()
    return target, existed


def execute_job(
    job: Job, slot: Slot, controller: QueueController, progress: Progress,
    destination: Path, state: Path, cookies: CookieChoice,
    move_to: Path | None = None, urls_file: Path | None = None, min_free_gb: float = 4.0,
) -> Report:
    if move_to is not None:
        free_gb = shutil.disk_usage(move_to).free / (1024 ** 3)
        if free_gb < min_free_gb:
            controller.abort(f"disk space low ({free_gb:.2f} GiB remaining on {move_to})")
            return Report(job.title, Status.ABORTED, error=f"Disk space low ({free_gb:.1f} GiB remaining)")
    parser = ProgressParser(job.title)
    work: Path | None = None
    scratch_archive: Path | None = None
    try:
        with job_guard(state, job, controller, slot) as identity:
            if controller.cancellation(slot):
                raise StopRequested
            work_parent = private_dir(destination / ".dusky-work")
            if work_parent.stat().st_dev != destination.stat().st_dev:
                raise ValueError(f"Work directory is another filesystem: {work_parent}")
            work = private_dir(work_parent / identity)
            if work.stat().st_dev != destination.stat().st_dev:
                raise ValueError(f"Job directory is another filesystem: {work}")
            archive = archive_path(state, job.mode)
            scratch_archive, old_lines = archive_snapshot(state, archive, work)
            with cookie_flags(cookies, work) as flags:
                command = ytdlp_command(job, work, state, scratch_archive, flags)
                proc = controller.spawn(slot, command, work)
                if proc is None:
                    raise StopRequested
                try:
                    code = pump(proc, slot, controller, parser, progress)
                finally:
                    controller.reap(slot, proc)
            title = parser.state.title or job.title
            if code != 0:
                reason = controller.cancellation(slot)
                if reason == "abort":
                    return Report(title, Status.ABORTED, error="queue aborted")
                if reason == "skip":
                    return Report(title, Status.SKIPPED, error=f"skipped by user; partial data kept in {work}")
                errors = list(parser.state.errors) or list(parser.state.tail)
                message = " | ".join(errors[-3:]) if errors else f"yt-dlp exited {code}"
                return Report(title, Status.FAILED, error=translate_error(message, job.mode))
            if not parser.state.final_paths:
                if parser.state.archive_skip:
                    if urls_file is not None:
                        mark_url_downloaded(urls_file, job.url)
                    return Report(title, Status.SKIPPED, error="already in the per-format archive")
                return Report(title, Status.FAILED,
                              error="yt-dlp exited without an after_move filepath or confirmed archive skip")
            if len(parser.state.final_paths) != 1:
                return Report(title, Status.FAILED, error="single-item child emitted multiple final media paths")
            final = checked_file(parser.state.final_paths[0], work)
            verify_media(final, job.mode, job.height, controller, slot, work)
            if controller.cancellation(slot):
                raise StopRequested
            entry = new_archive_entry(scratch_archive, old_lines)
            if controller.cancellation(slot):
                raise StopRequested
            published, existed = publish(final, destination, move_to=move_to)
            try:
                commit_archive(state, archive, entry)
            except OSError as exc:
                return Report(title, Status.FAILED, path=published,
                              error=f"Media published, archive commit failed: {exc}; retry safely")
            if urls_file is not None:
                mark_url_downloaded(urls_file, job.url)
            return Report(title, Status.SKIPPED if existed else Status.SUCCESS,
                          path=published, size=published.stat().st_size,
                          error="identical output already present" if existed else "")
    except StopRequested:
        status = Status.ABORTED if controller.aborted.is_set() else Status.SKIPPED
        return Report(parser.state.title or job.title, status,
                      error="aborted" if status is Status.ABORTED else "skipped during setup/verification")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return Report(parser.state.title or job.title, Status.FAILED,
                      error=f"{clean_label(exc, 400)}; incomplete work remains in {work or destination / '.dusky-work'} if present")
    finally:
        if scratch_archive is not None:
            try:
                scratch_archive.unlink(missing_ok=True)
            except OSError:
                pass
        if work is not None:
            try:
                work.rmdir()
            except OSError:
                pass


def progress_display() -> Progress:
    return Progress(
        SpinnerColumn(), TextColumn("[yellow]{task.fields[title]}[/]"),
        BarColumn(bar_width=20), TextColumn("{task.fields[metric]}"),
        TextColumn("[cyan]{task.description}[/]"), console=console, transient=True,
    )


@dataclass(slots=True)
class SignalMailbox:
    interrupts: list[float] = field(default_factory=list)
    terminated: bool = False


@dataclass(slots=True)
class KeyReader:
    thread: threading.Thread
    fd: int
    original: list[object]

    def close(self, stop: threading.Event) -> None:
        import termios

        stop.set()
        self.thread.join(timeout=2)
        # Main restores the terminal even if the reader failed to exit.
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.original)


def start_keys(
    controller: QueueController, messages: deque[str], stop: threading.Event,
) -> KeyReader | None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None
    import select
    import termios
    import tty

    try:
        fd = sys.stdin.fileno()
        original = termios.tcgetattr(fd)
    except (OSError, ValueError):
        return None
    try:
        tty.setcbreak(fd)
    except (OSError, ValueError):
        termios.tcsetattr(fd, termios.TCSADRAIN, original)
        return None

    def read_keys() -> None:
        try:
            while not stop.is_set():
                ready, _, _ = select.select([fd], [], [], 0.12)
                if not ready or stop.is_set():
                    continue
                key = os.read(fd, 1).decode("ascii", "ignore").lower()
                if not key:
                    break
                if key in {"h", "?"}:
                    messages.append(f"Keys: 1..{min(8, len(controller.slots))} skip worker, s skip active, "
                                    "q abort; Ctrl-C twice aborts.")
                elif key == "s":
                    messages.append(controller.skip_all())
                elif key == "q":
                    controller.abort("q")
                    messages.append("Aborting the queue.")
                elif key in "12345678":
                    messages.append(controller.skip_one(int(key)))
        except (OSError, ValueError) as exc:
            messages.append(f"Keyboard controls unavailable: {exc}; use SIGINT instead.")

    thread = threading.Thread(target=read_keys, name="dusky-tty", daemon=True)
    try:
        thread.start()
    except BaseException:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)
        raise
    return KeyReader(thread, fd, original)


def run_queue(
    jobs: list[Job], destination: Path, state: Path, cookies: CookieChoice, workers: int,
    move_to: Path | None = None, urls_file: Path | None = None, min_free_gb: float = 4.0,
) -> tuple[list[Report], str]:
    """One Rich task per slot; reports remain in input order."""
    results: list[Report | None] = [None] * len(jobs)
    mailbox = SignalMailbox()
    messages: deque[str] = deque()
    stopped = threading.Event()
    last_interrupt: float | None = None
    original_int = signal.getsignal(signal.SIGINT)
    original_term = signal.getsignal(signal.SIGTERM)

    def capture(signum: int, _frame: object) -> None:
        # Python signal handlers must not take locks or call Rich.
        if signum == signal.SIGTERM:
            mailbox.terminated = True
        else:
            mailbox.interrupts.append(time.monotonic())

    with progress_display() as display:
        slots = [Slot(number=i, task_id=display.add_task("", total=100, completed=0,
                                                       title=f"W{i} idle", metric=""))
                 for i in range(1, workers + 1)]
        controller = QueueController(slots)
        signal.signal(signal.SIGINT, capture)
        signal.signal(signal.SIGTERM, capture)
        key_reader: KeyReader | None = None
        try:
            key_reader = start_keys(controller, messages, stopped)

            def one(job: Job) -> Report:
                slot = controller.claim(job.title)
                if slot is None:
                    return Report(job.title, Status.ABORTED, error="not started")
                try:
                    return execute_job(
                        job, slot, controller, display, destination, state, cookies,
                        move_to=move_to, urls_file=urls_file, min_free_gb=min_free_gb,
                    )
                finally:
                    controller.finish(slot, display)

            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dusky-dl") as executor:
                pending: dict[Future[Report], int] = {}
                next_index = 0
                completed_count = 0

                def fill_workers() -> None:
                    nonlocal next_index
                    while not controller.aborted.is_set() and len(pending) < workers and next_index < len(jobs):
                        if move_to is not None:
                            free_gb = shutil.disk_usage(move_to).free / (1024 ** 3)
                            if free_gb < min_free_gb:
                                controller.abort(f"disk space low ({free_gb:.2f} GiB remaining on {move_to})")
                                messages.append(f"Disk space headroom reached ({free_gb:.2f} GiB remaining); stopping new downloads.")
                                break
                        future = executor.submit(one, jobs[next_index])
                        pending[future] = next_index
                        next_index += 1

                fill_workers()
                try:
                    while pending:
                        if mailbox.terminated:
                            controller.abort("SIGTERM")
                        while mailbox.interrupts:
                            received = mailbox.interrupts.pop(0)
                            if last_interrupt is not None and received - last_interrupt <= 3:
                                controller.abort("double SIGINT")
                                messages.append("Second Ctrl-C: aborting queue.")
                            else:
                                messages.append(controller.skip_all() + " Press Ctrl-C again within 3s to abort.")
                            last_interrupt = received
                        while messages:
                            console.print(f"[dim]{escape(messages.popleft())}[/]")
                        if controller.aborted.is_set():
                            for future in tuple(pending):
                                future.cancel()
                        completed, _ = wait(tuple(pending), timeout=0.12, return_when=FIRST_COMPLETED)
                        for future in completed:
                            index = pending.pop(future)
                            completed_count += 1
                            try:
                                results[index] = future.result()
                            except CancelledError:
                                results[index] = Report(jobs[index].title, Status.ABORTED, error="not started")
                            except Exception as exc:
                                results[index] = Report(jobs[index].title, Status.FAILED, error=f"Worker error: {exc}")
                            report = results[index]
                            console.print(f"[dim]{index + 1}/{len(jobs)} {escape(report.title)}: {report.status.value}[/]")
                            if os.environ.get("DUSKY_GUI") == "1":
                                print("DUSKY_EVENT|" + json.dumps({"event": "result", "completed": completed_count,
                                                                  "total": len(jobs), "status": report.status.value}), flush=True)
                        fill_workers()
                    if controller.aborted.is_set():
                        for index in range(next_index, len(jobs)):
                            results[index] = Report(jobs[index].title, Status.ABORTED, error="not started")
                except BaseException:
                    controller.abort("internal interruption")
                    for future in pending:
                        future.cancel()
                    raise
        finally:
            stopped.set()
            try:
                if key_reader is not None:
                    try:
                        key_reader.close(stopped)
                    except (OSError, ValueError) as exc:
                        console.print(f"[red]Unable to restore terminal settings: {escape(str(exc))}[/]")
            finally:
                signal.signal(signal.SIGINT, original_int)
                signal.signal(signal.SIGTERM, original_term)
    reports = [report or Report(jobs[i].title, Status.ABORTED, error="not started")
               for i, report in enumerate(results)]
    return reports, controller.reason


def print_reports(reports: list[Report], destination: Path, archive: Path, move_to: Path | None = None) -> None:
    tally = {status: sum(item.status is status for item in reports) for status in Status}
    console.print(" | ".join(f"{count} {status.value.lower()}" for status, count in tally.items()))
    table = Table(title="Extraction log", box=box.SIMPLE, expand=True)
    table.add_column("Title", overflow="ellipsis", ratio=4)
    table.add_column("Status", width=9)
    table.add_column("Size", justify="right", width=12)
    table.add_column("Output / diagnosis", overflow="fold", ratio=5)
    for report in reports:
        detail = report.path.name if report.path else report.error
        if report.error and report.path:
            detail += " | " + report.error
        table.add_row(escape(report.title), report.status.value,
                      bytes_label(report.size) if report.size else "-", escape(detail))
    console.print(table)
    console.print(f"Output directory: {escape(str(destination))}\nArchive: {escape(str(archive))}")
    if move_to is not None:
        console.print(f"Final storage: {escape(str(move_to))}")


def self_test() -> None:
    assert parse_positions("1,3,5-7", 8) == [0, 2, 4, 5, 6]
    assert parse_positions("-3:,::-1", 5) == [2, 3, 4, 1, 0]
    assert parse_positions("1:10:2", 5) == [0, 2, 4]
    assert parse_positions("-5--2", 10) == [5, 6, 7, 8]
    for spec in ("1,,2", "0", "1:5:0", "bad"):
        try:
            parse_positions(spec, 5)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Bad selection accepted: {spec}")
    state = ProgressParser("initial")
    state.line("RAW|" + json.dumps({"status": "downloading", "downloaded_bytes": 50.0,
                                    "total_bytes": 100.0, "eta": float("nan")}))
    assert state.state.percent == 50 and state.state.eta is None
    state.line("RAW|" + json.dumps({"status": "downloading", "fragment_index": 3.0,
                                    "fragment_count": 4.0, "filename": "second.webm"}))
    assert state.state.percent == 75 and state.state.total is None
    state.line("POST|" + json.dumps({"postprocessor": "FFmpegMerger", "status": "started"}))
    assert state.state.stage == "Merging"
    state.line("FILE|" + json.dumps("/dev/shm/dusky_ytdlp/sample.mkv"))
    assert len(state.state.final_paths) == 1
    mp4 = format_arguments(TargetFormat.VIDEO, 720)
    assert "bv[vcodec~='^(avc1|h264)'][height<=?720]" in mp4[1]
    assert "b[vcodec~=?'^(avc1|h264)'][acodec~=?'^(mp4a|aac)'][height<=?720]" in mp4[1]
    assert format_arguments(TargetFormat.AUDIO_MP3, None)[-1] == "320K"
    for mode in TargetFormat:
        if mode.is_video:
            assert "[height<=?720]" in format_arguments(mode, 720)[1]
    state.line("POST|" + json.dumps({"postprocessor": "FFmpegVideoRemuxer", "status": "started"}))
    assert state.state.stage == "Remuxing"
    state.line("POST|" + json.dumps({"postprocessor": "FFmpegExtractAudio", "status": "started"}))
    assert state.state.stage == "Extracting audio"
    state.line("POST|" + json.dumps({"postprocessor": "FFmpegMetadata", "status": "started"}))
    assert state.state.stage == "Finalizing"
    state.line("some video has already been recorded in the archive")
    assert state.state.archive_skip

    class FakeProgress:
        def update(self, *_args: object, **_kwargs: object) -> None:
            pass

    controller = QueueController([Slot(number=1, task_id=1)])
    slot = controller.claim("one")
    assert slot is not None and "Skipping" in controller.skip_one(1)
    assert controller.cancellation(slot) == "skip"
    controller.finish(slot, FakeProgress())  # type: ignore[arg-type]
    next_slot = controller.claim("two")
    assert next_slot is not None and controller.cancellation(next_slot) == ""
    controller.abort("test")
    assert controller.claim("three") is None

    if not is_memory_mount(SHM_MOUNT):
        raise ValueError("--self-test requires /dev/shm mounted as tmpfs")
    with tempfile.TemporaryDirectory(prefix="dusky-self-test-", dir=SHM_MOUNT) as name:
        directory = Path(name)
        private_dir(directory / "locks")
        archive = archive_path(directory, TargetFormat.AUDIO_BEST)
        scratch, previous = archive_snapshot(directory, archive, directory)
        scratch.write_bytes(b"youtube sample-id\n")
        record = new_archive_entry(scratch, previous)
        commit_archive(directory, archive, record)
        commit_archive(directory, archive, record)
        assert archive.read_bytes() == b"youtube sample-id\n"
        scratch.unlink()
        source = private_dir(directory / "work") / "media.m4a"
        source.write_bytes(b"example")
        published, existed = publish(source, directory)
        assert not existed and published.read_bytes() == b"example"
        source.write_bytes(b"example")
        assert publish(source, directory)[1]
        source.write_bytes(b"different")
        try:
            publish(source, directory)
        except ValueError:
            assert published.read_bytes() == b"example"
        else:
            raise AssertionError("Name collision overwrote existing media")
        # Test move_to across directories
        ext_dir = private_dir(directory / "final_dest")
        source = private_dir(directory / "work") / "media2.m4a"
        source.write_bytes(b"moved-example")
        moved_pub, moved_ex = publish(source, directory, move_to=ext_dir)
        assert not moved_ex and moved_pub == ext_dir / "media2.m4a"
        assert moved_pub.read_bytes() == b"moved-example"
        assert not source.exists()
        # Test mark_url_downloaded
        test_urls = directory / "urls.txt"
        test_urls.write_text("https://example.com/one\nhttps://example.com/two\n", encoding="utf-8")
        mark_url_downloaded(test_urls, "https://example.com/one")
        assert test_urls.read_text(encoding="utf-8") == "# DOWNLOADED https://example.com/one\nhttps://example.com/two\n"
    print("Self-test passed: ranges, formats, progress stages, slot reset, archive, move_to and no-overwrite publication.")


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dusky Universal Media Downloader (Arch / RAM-backed output by default).")
    parser.add_argument("target", nargs="*", help="HTTP(S) links, playlists, or batch files in input order")
    parser.add_argument("-f", "--format", choices=[mode.value for mode in TargetFormat])
    parser.add_argument("-q", "--quality", choices=["best", *(str(cap) for cap in QUALITY_CAPS)])
    parser.add_argument("-o", "--output-dir", type=Path, help="output directory (default: automatic RAM-backed pool)")
    parser.add_argument("--move-to", type=Path, help="move finalized media from output directory to another directory")
    parser.add_argument("--update-urls-file", type=Path, help="mark completed URLs with # DOWNLOADED in this file")
    parser.add_argument("--min-free-gb", type=float, default=4.0, help="stop queue if free disk space in GB drops below this (default 4.0)")
    parser.add_argument("-I", "--playlist-items", help="inclusive playlist positions (all, 1-3, -3:, ::-1)")
    parser.add_argument("-N", "--concurrent", type=int, help="parallel jobs (positive; limited by queue length)")
    parser.add_argument("--skip-items", help="skip final queue positions; use --skip-items=-3: for a leading minus")
    parser.add_argument("--cookies", type=Path, help="Netscape cookie file; copied into the work directory per child")
    parser.add_argument("--cookies-from-browser", help="yt-dlp browser selector, e.g. firefox")
    parser.add_argument("--gui", action="store_true", help="launch the GTK downloader")
    parser.add_argument("--self-test", action="store_true", help="run offline parser/selector regression checks")
    return parser


def main() -> int:
    args = argument_parser().parse_args()
    if args.concurrent is not None and args.concurrent < 1:
        raise ValueError("--concurrent must be positive")
    if args.gui:
        from dusky_downloader_gui import main as gui_main
        return gui_main(args)
    if args.self_test:
        self_test()
        return 0
    check_versions()
    state = state_directory()
    initial_pool = storage_pool(args.output_dir)
    cookies = cookie_choice(args, state)
    move_to = args.move_to.expanduser().resolve() if args.move_to else None
    if move_to is not None:
        if not move_to.is_dir():
            raise ValueError(f"--move-to target is not a directory: {move_to}")
        if not os.access(move_to, os.W_OK):
            raise ValueError(f"--move-to target directory is not writable: {move_to}")
    urls_file = args.update_urls_file.expanduser().resolve() if args.update_urls_file else None
    if urls_file is not None:
        if not urls_file.is_file():
            raise ValueError(f"--update-urls-file target is not a file: {urls_file}")
    history_path = state / "input_history.txt"
    os.close(private_file(history_path))
    history = FileHistory(str(history_path))
    jobs, destination, workers, had_targets, preflight_errors = build_jobs(
        args, state, initial_pool, history, cookies)
    if not jobs:
        console.print("[yellow]Nothing queued after range and skip selection.[/]")
        return 0 if had_targets and not preflight_errors else 1
    console.print(f"[cyan]{len(jobs)}[/] item(s) | {workers} worker(s) | "
                  f"{jobs[0].mode.value} | {escape(str(destination))}")
    if os.environ.get("DUSKY_GUI") == "1":
        print("DUSKY_EVENT|" + json.dumps({"event": "queue", "total": len(jobs)}), flush=True)
    reports, abort_reason = run_queue(
        jobs, destination, state, cookies, workers,
        move_to=move_to, urls_file=urls_file, min_free_gb=args.min_free_gb,
    )
    print_reports(reports, destination, archive_path(state, jobs[0].mode), move_to=move_to)
    if preflight_errors:
        console.print(f"[yellow]{preflight_errors} input(s) could not be queued; run exits nonzero.[/]")
    if abort_reason:
        return 143 if abort_reason == "SIGTERM" else 130
    return 1 if preflight_errors or any(report.status is Status.FAILED for report in reports) else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted before the download queue started.", file=sys.stderr)
        raise SystemExit(130) from None
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Dusky: {error}", file=sys.stderr)
        raise SystemExit(2) from None
