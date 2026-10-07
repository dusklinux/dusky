"""Interactive launcher and scriptable subcommands."""

import argparse
import json
import math
import os
from pathlib import Path
import signal
import sys
import tempfile
import time

lazy from . import audio, gif, premiere, video
lazy import readline

from .core import (ConversionError, atomic_output, discover, download, probe,
                   require_tool)
from .ui import UI


def number(value: str) -> float:
    try:
        result = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use a finite number of seconds / frames per second.") from error
    if not math.isfinite(result) or result < 0:
        raise argparse.ArgumentTypeError("Use a finite, nonnegative number.")
    return result


def positive(value: str) -> float:
    result = number(value)
    if result == 0:
        raise argparse.ArgumentTypeError("Value must be greater than zero.")
    return result


def width(value: str) -> int:
    result = int(value)
    if result < 2:
        raise argparse.ArgumentTypeError("Width must be at least 2 pixels.")
    return result


def crf(value: str) -> int:
    result = int(value)
    if not 0 <= result <= 51:
        raise argparse.ArgumentTypeError("CRF must be between 0 and 51.")
    return result


def track(value: str) -> int:
    result = int(value)
    if result < 0:
        raise argparse.ArgumentTypeError("Track index must be nonnegative.")
    return result


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Dusky Converter · quality presets, batch conversion and live progress",
        allow_abbrev=False,
        epilog="Run without arguments for the interactive menu. Python 3.15+ and FFmpeg required.",
    )
    commands = root.add_subparsers(dest="command", required=True)
    descriptions = frozendict(
        gif="Palette-based animated GIF", premiere="MOV remux / ProRes editing master",
        video="Compress to H.264/AAC MP4", audio="Extract an audio track",
        info="Inspect codecs, tracks, size and duration",
    )
    for name, description in descriptions.items():
        sub = commands.add_parser(name, help=description, description=description,
                                  allow_abbrev=False,
                                  formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        sub.add_argument("inputs", nargs="*", help="Files, directories, or one HTTP(S) URL; use -- before dash-prefixed paths")
        if name == "info":
            sub.add_argument("--json", action="store_true", help="Write structured results to stdout")
            continue
        destination = sub.add_mutually_exclusive_group()
        destination.add_argument("-o", "--output", type=Path, help="Explicit output file (one input only)")
        destination.add_argument("--output-dir", type=Path, help="Directory for all outputs")
        sub.add_argument("--overwrite", action="store_true", help="Replace existing outputs after successful conversion")
        sub.add_argument("--start", type=number, default=0, metavar="SECONDS", help="Start at this time")
        sub.add_argument("--duration", type=positive, metavar="SECONDS", help="Convert only this duration")
        if name in {"gif", "video", "premiere"}:
            sub.add_argument("--fps", type=positive, help="Target frame rate (GIF cap unless interpolating; ProRes / MP4 CFR)")
        if name in {"gif", "video"}:
            choices = ("web", "balanced", "quality") if name == "gif" else ("fast", "balanced", "quality")
            sub.add_argument("--preset", choices=choices, default="balanced", help="Quality / speed preset")
            sub.add_argument("--width", type=width, help="Maximum output width; never upscale")
        if name == "gif":
            sub.add_argument("--interpolate", action="store_true", help="Generate intermediate frames (CPU intensive)")
        elif name == "video":
            sub.add_argument("--crf", type=crf, help="Override quality; lower is better / larger")
        elif name == "premiere":
            sub.add_argument("--mode", choices=("auto", "remux", "prores"), default="auto",
                             help="Auto copies supported video; otherwise encodes ProRes 422")
        elif name == "audio":
            sub.add_argument("--format", choices=("flac", "wav", "mp3", "m4a"), default="flac", help="Audio format")
            sub.add_argument("--track", type=track, default=0, help="Zero-based audio track index")
    return root


def prompt(text: str) -> str:
    # readline's filename completion handles spaces without shell evaluation.
    readline.parse_and_bind("tab: complete")
    print(text, end="", file=sys.stderr, flush=True)
    return input().strip()


def interactive(ui: UI) -> list[str]:
    ui.banner()
    ui.message("  1  GIF                 2  Premiere / ProRes")
    ui.message("  3  Compress video      4  Extract audio")
    ui.message("  5  Inspect media       q  Quit")
    choices = frozendict({"1": "gif", "2": "premiere", "3": "video", "4": "audio", "5": "info"})
    while True:
        choice = prompt("Choose [1–5/q]: ")
        if choice.lower() == "q":
            return []
        if choice in choices:
            return [choices[choice]]
        ui.message("Choose a number from 1 to 5, or q.", "33")


def destination(source: Path, args, *, remote: bool = False, directory_batch: bool = False) -> Path:
    extension = frozendict(gif=".gif", premiere=".mov", video=".mp4", audio=f".{getattr(args, 'format', 'flac')}")[args.command]
    if args.output:
        target = args.output.expanduser().absolute()
        if target.suffix.lower() != extension:
            raise ConversionError(f"Output for {args.command} must end in {extension}.")
        return target
    stem = f"download-{time.time_ns()}" if remote else source.stem
    folder = source.parent
    suffix = ""
    if remote:
        folder = Path.cwd()
    if args.command == "premiere":
        folder /= "premiere_ready"
        if args.mode == "prores":
            suffix = "_ProRes"
    elif args.command == "video":
        suffix = "_compressed"
    elif args.command == "audio":
        suffix = f"_audio{args.track + 1}"
    elif source.suffix.lower() == ".gif":
        suffix = "_converted"
    if directory_batch and args.command != "premiere":
        folder /= frozendict(gif="gifs", video="converted_video", audio="extracted_audio")[args.command]
    if args.output_dir:
        folder = args.output_dir.expanduser().absolute()
    return folder / f"{stem}{suffix}{extension}"


def inspect(media) -> dict:
    return {"path": str(media.path), "size_bytes": media.path.stat().st_size,
            "duration_seconds": media.duration, "streams": media.streams}


def convert(media, output: Path, args, ui: UI):
    # Only load the chosen feature; --help and inspection avoid encoder modules.
    match args.command:
        case "gif":
            gif.convert(media, output, args, ui)
        case "premiere":
            premiere.convert(media, output, args, ui)
        case "video":
            video.convert(media, output, args, ui)
        case "audio":
            audio.convert(media, output, args, ui)


def process(inputs: list[Path], args, ui: UI, *, remote: bool = False, directory_batch: bool = False) -> int:
    targets = []
    if args.command != "info":
        if args.output and len(inputs) != 1:
            raise ConversionError("--output requires exactly one input; use --output-dir for batches.")
        targets = [destination(path, args, remote=remote, directory_batch=directory_batch) for path in inputs]
        resolved = [target.resolve() for target in targets]
        if len(set(resolved)) != len(resolved):
            raise ConversionError("Inputs produce duplicate output names. Convert them separately with --output.")
        for target in targets:
            if target.resolve() in inputs or (target.exists() and any(target.samefile(p) for p in inputs)):
                raise ConversionError(f"Output would replace an input: {target}")
    failures = 0
    reports = []
    for index, path in enumerate(inputs):
        ui.message(f"\n[{index + 1}/{len(inputs)}] {path.name}", "1;36")
        try:
            media = probe(path)
            if args.command == "info":
                report = inspect(media)
                reports.append(report)
                if not args.json:
                    duration = f"{media.duration:.2f}s" if media.duration else "unknown duration"
                    print(f"{path}\n  {duration} · {report['size_bytes'] / 1048576:.2f} MiB")
                    for stream in media.streams:
                        details = ""
                        if stream.get("codec_type") == "video":
                            details = f" · {stream.get('width')}×{stream.get('height')} · {stream.get('avg_frame_rate', '?')} fps"
                        elif stream.get("codec_type") == "audio":
                            details = f" · {stream.get('sample_rate', '?')} Hz · {stream.get('channels', '?')} channels"
                        print(f"  #{stream['index']} {stream.get('codec_type')} · {stream.get('codec_name')}{details}")
                continue
            if args.command in {"gif", "video"} and media.video.get("color_transfer") in {"smpte2084", "arib-std-b67"}:
                ui.message("HDR source: these SDR presets do not perform tone mapping; colors may change.", "33")
            target = targets[index]
            with atomic_output(target, path, args.overwrite) as temporary:
                convert(media, temporary, args, ui)
            ui.message(f"✓ {target} · {target.stat().st_size / 1048576:.2f} MiB", "32")
        except BrokenPipeError:
            raise
        except (ConversionError, OSError) as error:
            failures += 1
            ui.message(f"✗ {path.name}: {error}", "31")
    if args.command == "info" and args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    if args.command == "info":
        # Small reports can otherwise fail only during interpreter shutdown.
        sys.stdout.flush()
    ui.message(f"\n{len(inputs) - failures} succeeded · {failures} failed", "31" if failures else "32")
    return int(bool(failures))


def main(argv: list[str] | None = None) -> int:
    ui = UI()
    interrupted = signal.SIGINT

    def cancel(signum, _frame):
        nonlocal interrupted
        interrupted = signum
        raise KeyboardInterrupt

    previous = {sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        arguments = list(sys.argv[1:] if argv is None else argv)
        menu = not arguments
        if not arguments:
            if not sys.stdin.isatty():
                raise ConversionError("Specify a subcommand and input; see --help.")
            arguments = interactive(ui)
            if not arguments:
                return 0
        root = parser()
        try:
            args = root.parse_args(arguments)
        except SystemExit:
            sys.stdout.flush()
            raise
        if not menu and args.command != "info":
            ui.banner()
        if not args.inputs:
            if not sys.stdin.isatty():
                raise ConversionError("Specify an input file, directory, or URL.")
            source = prompt("Source file / directory / URL (Tab completes paths): ")
            if not source:
                raise ConversionError("No input provided.")
            args.inputs = [source]
        require_tool("ffprobe")
        if args.command != "info":
            require_tool("ffmpeg")
        urls = [item for item in args.inputs if item.startswith(("https://", "http://"))]
        if urls:
            if len(args.inputs) != 1:
                raise ConversionError("Use a single URL per invocation.")
            with tempfile.TemporaryDirectory(prefix="dusky-download-") as directory:
                source = download(urls[0], Path(directory), ui)
                return process([source], args, ui, remote=True)
        inputs = discover(args.inputs)
        directory_batch = any(Path(value).expanduser().is_dir() for value in args.inputs)
        return process(inputs, args, ui, directory_batch=directory_batch)
    except KeyboardInterrupt:
        ui.message("\nCancelled; unfinished output removed.", "33")
        return 128 + interrupted
    except EOFError:
        ui.message("No input received.", "33")
        return 1
    except BrokenPipeError:
        # A consumer such as head exited; discard buffered output before shutdown.
        with open(os.devnull, "w", encoding="utf-8") as sink:
            for stream in (sys.stdout, sys.stderr):
                os.dup2(sink.fileno(), stream.fileno())
        return 128 + signal.SIGPIPE
    except (ConversionError, OSError) as error:
        ui.message(f"✗ {error}", "31")
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
