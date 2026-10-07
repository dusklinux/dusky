"""Shared probing, process lifecycle, input discovery and atomic output."""

from contextlib import contextmanager
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile

lazy from .filesystem import rename_no_replace

from .ui import UI

MEDIA_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".mov", ".webm", ".avi", ".ts", ".m2ts", ".mts",
    ".m4v", ".mpg", ".mpeg", ".ogv", ".gif", ".mp3", ".flac", ".wav",
    ".m4a", ".aac", ".ogg", ".opus", ".aiff", ".aif",
})


class ConversionError(Exception):
    """An actionable input/tool/conversion failure."""


def require_tool(name: str) -> str:
    if tool := shutil.which(name):
        return tool
    package = "ffmpeg" if name in {"ffmpeg", "ffprobe"} else name
    raise ConversionError(f"Missing {name}. Install the ISO package: {package}.")


def stop_process(process: subprocess.Popen):
    """Abort the whole temporary job, including TERM-ignoring helper processes."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def capture(command: list[str], *, live_errors: bool = False, live_output: bool = False) -> str:
    with subprocess.Popen(command, stdin=subprocess.DEVNULL,
                          stdout=sys.stderr if live_output else subprocess.PIPE,
                          stderr=None if live_errors else subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace",
                          start_new_session=True) as process:
        try:
            output, errors = process.communicate()
        except BaseException:
            stop_process(process)
            raise
        if process.returncode:
            raise ConversionError((errors or f"{Path(command[0]).name} failed ({process.returncode}).").strip())
        return output or ""


@dataclass(slots=True)
class Media:
    path: Path
    streams: tuple[dict, ...]
    duration: float | None
    format_name: str

    @property
    def video(self) -> dict:
        for stream in self.streams:
            if stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic"):
                return stream
        raise ConversionError(f"No video stream: {self.path.name}")

    @property
    def audio(self) -> tuple[dict, ...]:
        return tuple(s for s in self.streams if s.get("codec_type") == "audio")

    @property
    def fps(self) -> Fraction | None:
        stream = self.video
        for key in ("avg_frame_rate", "r_frame_rate"):
            try:
                value = Fraction(stream.get(key, "0/0"))
                if value > 0:
                    return value
            except (ValueError, ZeroDivisionError):
                pass
        return None


def probe(path: Path) -> Media:
    raw = capture([require_tool("ffprobe"), "-v", "error", "-show_streams",
                   "-show_format", "-of", "json", str(path)])
    try:
        data = json.loads(raw)
        streams = tuple(data["streams"])
        duration = float(data.get("format", {}).get("duration", "nan"))
        duration = duration if math.isfinite(duration) and duration > 0 else None
    except (KeyError, TypeError, ValueError) as error:
        raise ConversionError(f"Invalid ffprobe result for {path.name}: {error}") from error
    if not streams:
        raise ConversionError(f"No media streams: {path.name}")
    return Media(path, streams, duration, data.get("format", {}).get("format_name", ""))


def discover(inputs: list[str]) -> list[Path]:
    files = []
    for value in inputs:
        path = Path(value).expanduser().resolve(strict=True)
        if path.is_dir():
            files.extend(sorted(p for p in path.iterdir()
                                if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS))
        elif path.is_file():
            files.append(path)
        else:
            raise ConversionError(f"Not a file or directory: {value}")
    files = list(dict.fromkeys(p.resolve(strict=True) for p in files))
    if not files:
        raise ConversionError("No media files found (directory scanning is nonrecursive).")
    return files


def download(url: str, directory: Path, ui: UI) -> Path:
    ui.message("↓ Downloading source with yt-dlp…")
    manifest = directory / "download.json"
    capture([
        require_tool("yt-dlp"), "--ignore-config", "--no-playlist", "--no-simulate",
        "--quiet", "--progress", "--newline", "-f", "bv*+ba/b",
        "-o", str(directory / "source.%(ext)s"),
        "--print-to-file", "after_move:%(filepath)j", str(manifest), "--", url,
    ], live_errors=True, live_output=True)
    try:
        path = Path(json.loads(manifest.read_text(encoding="utf-8"))).resolve(strict=True)
    except (ValueError, TypeError, OSError) as error:
        raise ConversionError(f"Cannot locate downloaded media: {error}") from error
    if not path.is_file():
        raise ConversionError("Downloader did not produce a media file.")
    return path


def decode_seek(media: Media, args) -> bool:
    # These formats lack an index for reliably finding the preceding keyframe.
    return bool(args.start and media.format_name in {"mpegts", "mpeg", "h264", "hevc"})


def input_args(media: Media, args) -> list[str]:
    result = []
    slow_seek = decode_seek(media, args)
    if args.start and not slow_seek:
        result += ["-ss", str(args.start)]
    if args.duration is not None:
        result += ["-t", str(args.duration + args.start if slow_seek else args.duration)]
    return result + ["-i", str(media.path)]


def output_trim_args(media: Media, args, *, seek_in_filter: bool = False) -> list[str]:
    result = ["-ss", str(args.start)] if decode_seek(media, args) and not seek_in_filter else []
    # Input -t limits each stream's read duration; output -t enforces the clip timeline,
    # including files whose audio/video streams begin at different times.
    if args.duration is not None:
        result += ["-t", str(args.duration)]
    return result


def clip_duration(media: Media, args) -> float | None:
    if media.duration is None:
        return args.duration
    remaining = media.duration - args.start
    if remaining <= 0:
        raise ConversionError("Start time is past the end of the source.")
    return min(remaining, args.duration) if args.duration is not None else remaining


def ffmpeg(arguments: list[str], ui: UI, label: str, duration: float | None):
    command = [require_tool("ffmpeg"), "-hide_banner", "-nostdin", "-y",
               "-xerror", "-abort_on", "empty_output_stream", "-loglevel", "warning",
               "-nostats", "-progress", "pipe:1", *arguments]
    ui.message(f"→ {label}")
    # Disk-backed stderr cannot fill a pipe or grow application memory indefinitely.
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=errors, text=True, encoding="utf-8", errors="replace",
                              start_new_session=True) as process:
            try:
                progress = {}
                for line in process.stdout:
                    key, _, value = line.rstrip().partition("=")
                    progress[key] = value
                    if key == "progress":
                        try:
                            elapsed = int(progress.get("out_time_us", "0")) / 1_000_000
                        except ValueError:
                            elapsed = 0.0
                        ui.progress(label, elapsed, duration, progress.get("speed", ""))
                        progress.clear()
                status = process.wait()
            except BaseException:
                stop_process(process)
                raise
            finally:
                ui.clear()
        errors.seek(0, os.SEEK_END)
        size = errors.tell()
        errors.seek(max(0, size - 8192))
        detail = errors.read().decode("utf-8", errors="replace").strip()
        if status:
            raise ConversionError(detail or f"FFmpeg failed ({status}).")
        if detail:
            ui.message(detail, "33")


@contextmanager
def atomic_output(target: Path, source: Path, overwrite: bool):
    if target.resolve() == source.resolve() or (target.exists() and target.samefile(source)):
        raise ConversionError("Output must differ from the source file.")
    if target.exists() and not overwrite:
        raise ConversionError(f"Output exists: {target}. Use --overwrite to replace it.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".dusky-", dir=target.parent) as directory:
        temporary = Path(directory) / target.name
        yield temporary
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise ConversionError("FFmpeg produced no output.")
        if overwrite:
            os.replace(temporary, target)
        else:
            # Atomic no-clobber rename also works on filesystems without hard links.
            rename_no_replace(temporary, target)
