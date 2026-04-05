#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, NoReturn


# ==============================================================================
# WAYCLICK AUDIO SPRITE SLICER
#
# Robust design goals:
# - Auto-detects input manifest in current directory:
#     config.json / sprite.json / any valid *.json manifest
# - Auto-detects source audio:
#     prefers manifest-referenced file; otherwise requires exactly one .ogg in cwd
# - Uses Decimal for timing when parsing JSON (avoids float drift)
# - Converts timing to exact sample indices via ffprobe sample rate
# - Uses ffmpeg atrim=start_sample/end_sample for sample-accurate cuts
# - Uses batched single-process ffmpeg runs (more robust than process swarms)
# - Writes WayClick config.json into output directory only
#
# Supported manifest styles:
#   1) {"defines": {"30": [0, 37], "31": [37, 42]}}          # ms
#   2) {"spritemap": {"30": {"start": 0.000, "end": 0.037}}} # seconds
#   3) {"30": [0, 37], "31": [37, 42]}                       # bare defines-style object
#
# Notes:
# - "defines" is treated as milliseconds.
# - "spritemap" is treated as seconds unless *_ms keys are used.
# - This script intentionally fails on ambiguity instead of guessing.
#
# Requires:
#   - ffmpeg
#   - ffprobe
# ==============================================================================


_THOUSAND = Decimal("1000")
_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class SliceDef:
    key: str
    start_s: Decimal
    duration_s: Decimal

    @property
    def end_s(self) -> Decimal:
        return self.start_s + self.duration_s


@dataclass(slots=True)
class ManifestCandidate:
    path: Path
    data: dict[str, Any] | None
    slices: list[SliceDef] | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class AudioInfo:
    sample_rate: int
    total_samples: int | None


def die(message: str) -> NoReturn:
    raise SystemExit(message)


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        die(f"Error: required tool '{name}' was not found in PATH.")


def qint(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def to_decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric, not boolean")

    if isinstance(value, Decimal):
        out = value
    elif isinstance(value, int):
        out = Decimal(value)
    elif isinstance(value, float):
        out = Decimal(str(value))
    elif isinstance(value, str):
        try:
            out = Decimal(value.strip())
        except InvalidOperation:
            raise ValueError(f"{label} must be numeric, got {value!r}") from None
    else:
        raise ValueError(f"{label} must be numeric, got {value!r}")

    if not out.is_finite():
        raise ValueError(f"{label} must be finite, got {value!r}")

    return out


def to_nonnegative_seconds(value: Any, label: str) -> Decimal:
    out = to_decimal(value, label)
    if out < _ZERO:
        raise ValueError(f"{label} must be >= 0, got {value!r}")
    return out


def to_positive_seconds(value: Any, label: str) -> Decimal:
    out = to_decimal(value, label)
    if out <= _ZERO:
        raise ValueError(f"{label} must be > 0, got {value!r}")
    return out


def to_nonnegative_ms_as_seconds(value: Any, label: str) -> Decimal:
    return to_nonnegative_seconds(value, label) / _THOUSAND


def to_positive_ms_as_seconds(value: Any, label: str) -> Decimal:
    return to_positive_seconds(value, label) / _THOUSAND


def get_first_present(mapping: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    raise KeyError(names[0] if names else "value")


def normalize_slices(items: list[SliceDef]) -> list[SliceDef]:
    if not items:
        raise ValueError("manifest contains no slices")

    seen: set[str] = set()
    out: list[SliceDef] = []

    for item in items:
        if item.key in seen:
            raise ValueError(f"duplicate slice key {item.key!r}")
        seen.add(item.key)

        if item.start_s < _ZERO:
            raise ValueError(f"slice {item.key!r} has negative start")
        if item.duration_s <= _ZERO:
            raise ValueError(f"slice {item.key!r} has non-positive duration")

        out.append(item)

    out.sort(key=lambda s: (s.start_s, s.key))
    return out


def parse_defines_object(defines: Any) -> list[SliceDef]:
    if not isinstance(defines, dict) or not defines:
        raise ValueError("'defines' must be a non-empty JSON object")

    slices: list[SliceDef] = []

    for raw_key, raw_value in defines.items():
        key = str(raw_key)

        if isinstance(raw_value, list):
            if len(raw_value) != 2:
                raise ValueError(
                    f"defines[{key!r}] must contain exactly [start_ms, duration_ms]"
                )
            start_s = to_nonnegative_ms_as_seconds(raw_value[0], f"defines[{key!r}][0]")
            duration_s = to_positive_ms_as_seconds(raw_value[1], f"defines[{key!r}][1]")

        elif isinstance(raw_value, dict):
            start_s = to_nonnegative_ms_as_seconds(
                get_first_present(raw_value, "start", "start_ms"),
                f"defines[{key!r}].start",
            )

            if "end" in raw_value or "end_ms" in raw_value:
                if "end" in raw_value:
                    end_s = to_nonnegative_ms_as_seconds(
                        raw_value["end"], f"defines[{key!r}].end"
                    )
                else:
                    end_s = to_nonnegative_ms_as_seconds(
                        raw_value["end_ms"], f"defines[{key!r}].end_ms"
                    )

                duration_s = end_s - start_s
                if duration_s <= _ZERO:
                    raise ValueError(f"defines[{key!r}] has end <= start")

            else:
                if "duration_ms" in raw_value and "duration" not in raw_value:
                    duration_s = to_positive_ms_as_seconds(
                        raw_value["duration_ms"],
                        f"defines[{key!r}].duration_ms",
                    )
                else:
                    duration_s = to_positive_ms_as_seconds(
                        get_first_present(raw_value, "duration", "length"),
                        f"defines[{key!r}].duration",
                    )
        else:
            raise ValueError(
                f"defines[{key!r}] must be [start_ms, duration_ms] or an object"
            )

        slices.append(SliceDef(key=key, start_s=start_s, duration_s=duration_s))

    return normalize_slices(slices)


def parse_spritemap_object(spritemap: Any) -> list[SliceDef]:
    if not isinstance(spritemap, dict) or not spritemap:
        raise ValueError("'spritemap' must be a non-empty JSON object")

    slices: list[SliceDef] = []

    for raw_key, raw_value in spritemap.items():
        key = str(raw_key)

        if not isinstance(raw_value, dict):
            raise ValueError(f"spritemap[{key!r}] must be an object")

        if "start" in raw_value:
            start_s = to_nonnegative_seconds(
                raw_value["start"], f"spritemap[{key!r}].start"
            )
        elif "start_ms" in raw_value:
            start_s = to_nonnegative_ms_as_seconds(
                raw_value["start_ms"], f"spritemap[{key!r}].start_ms"
            )
        else:
            raise ValueError(f"spritemap[{key!r}] is missing start/start_ms")

        if "end" in raw_value or "end_ms" in raw_value:
            if "end" in raw_value:
                end_s = to_nonnegative_seconds(
                    raw_value["end"], f"spritemap[{key!r}].end"
                )
            else:
                end_s = to_nonnegative_ms_as_seconds(
                    raw_value["end_ms"], f"spritemap[{key!r}].end_ms"
                )

            duration_s = end_s - start_s
            if duration_s <= _ZERO:
                raise ValueError(f"spritemap[{key!r}] has end <= start")

        else:
            if "duration" in raw_value:
                duration_s = to_positive_seconds(
                    raw_value["duration"], f"spritemap[{key!r}].duration"
                )
            elif "duration_ms" in raw_value:
                duration_s = to_positive_ms_as_seconds(
                    raw_value["duration_ms"], f"spritemap[{key!r}].duration_ms"
                )
            elif "length" in raw_value:
                duration_s = to_positive_seconds(
                    raw_value["length"], f"spritemap[{key!r}].length"
                )
            elif "length_ms" in raw_value:
                duration_s = to_positive_ms_as_seconds(
                    raw_value["length_ms"], f"spritemap[{key!r}].length_ms"
                )
            else:
                raise ValueError(
                    f"spritemap[{key!r}] is missing end/end_ms or duration/duration_ms"
                )

        slices.append(SliceDef(key=key, start_s=start_s, duration_s=duration_s))

    return normalize_slices(slices)


def looks_like_bare_defines_object(data: dict[str, Any]) -> bool:
    if not data:
        return False
    if "defaults" in data and "mappings" in data:
        # Likely already a WayClick output config, not an input sprite manifest.
        return False

    try:
        parse_defines_object(data)
        return True
    except ValueError:
        return False


def inspect_manifest_candidate(path: Path) -> ManifestCandidate:
    try:
        data = json.loads(
            path.read_text(encoding="utf-8-sig"),
            parse_float=Decimal,
        )
    except Exception as exc:
        return ManifestCandidate(path=path, data=None, slices=None, reason=f"invalid JSON: {exc}")

    if not isinstance(data, dict):
        return ManifestCandidate(
            path=path,
            data=None,
            slices=None,
            reason="top-level JSON must be an object",
        )

    try:
        if "defines" in data:
            slices = parse_defines_object(data["defines"])
        elif "spritemap" in data:
            slices = parse_spritemap_object(data["spritemap"])
        elif looks_like_bare_defines_object(data):
            slices = parse_defines_object(data)
        else:
            return ManifestCandidate(
                path=path,
                data=data,
                slices=None,
                reason=(
                    "not a supported sprite manifest "
                    "(expected 'defines', 'spritemap', or a bare key->slice mapping)"
                ),
            )
    except ValueError as exc:
        return ManifestCandidate(path=path, data=data, slices=None, reason=str(exc))

    return ManifestCandidate(path=path, data=data, slices=slices, reason=None)


def find_manifest(cwd: Path, explicit: Path | None) -> ManifestCandidate:
    if explicit is not None:
        path = explicit if explicit.is_absolute() else (cwd / explicit)
        path = path.resolve(strict=False)

        if not path.is_file():
            die(f"Error: manifest file not found: {path}")

        candidate = inspect_manifest_candidate(path)
        if candidate.slices is None:
            die(f"Error: {path.name} is not a valid sprite manifest:\n  {candidate.reason}")
        return candidate

    candidates: list[Path] = []
    seen: set[Path] = set()

    for preferred in ("config.json", "sprite.json"):
        p = cwd / preferred
        if p.is_file():
            rp = p.resolve(strict=False)
            if rp not in seen:
                seen.add(rp)
                candidates.append(rp)

    for p in sorted(cwd.glob("*.json"), key=lambda x: x.name.casefold()):
        rp = p.resolve(strict=False)
        if rp not in seen:
            seen.add(rp)
            candidates.append(rp)

    inspected = [inspect_manifest_candidate(p) for p in candidates]
    valid = [c for c in inspected if c.slices is not None]
    preferred_valid = [c for c in valid if c.path.name in {"config.json", "sprite.json"}]

    if len(preferred_valid) == 1:
        return preferred_valid[0]

    if len(preferred_valid) > 1:
        names = ", ".join(c.path.name for c in preferred_valid)
        die(
            "Error: multiple valid preferred manifest files found in current directory:\n"
            f"  {names}\n"
            "Use --json to choose one explicitly."
        )

    if len(valid) == 1:
        return valid[0]

    if len(valid) > 1:
        names = ", ".join(c.path.name for c in valid)
        die(
            "Error: multiple valid manifest JSON files found in current directory:\n"
            f"  {names}\n"
            "Use --json to choose one explicitly."
        )

    lines = [
        "Error: no valid sprite manifest was found in the current directory.",
        "Looked for config.json, sprite.json, and other *.json files with a supported schema.",
    ]

    if inspected:
        lines.append("")
        lines.append("Checked files:")
        for c in inspected:
            lines.append(f"  - {c.path.name}: {c.reason}")

    die("\n".join(lines))


def iter_manifest_audio_refs(
    manifest_data: dict[str, Any],
    manifest_path: Path,
    cwd: Path,
):
    keys = ("resources", "resource", "audio", "src", "source", "file", "files")
    raw_values: list[str] = []

    for key in keys:
        if key not in manifest_data:
            continue

        value = manifest_data[key]
        if isinstance(value, str):
            raw_values.append(value)
        elif isinstance(value, list):
            raw_values.extend(v for v in value if isinstance(v, str))

    seen: set[Path] = set()

    for raw in raw_values:
        if "://" in raw:
            continue

        p = Path(raw)
        candidates: list[Path] = []

        if p.is_absolute():
            candidates.append(p)
        else:
            candidates.append((manifest_path.parent / p).resolve(strict=False))
            candidates.append((cwd / p.name).resolve(strict=False))

        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate.is_file():
                yield candidate


def find_audio(
    cwd: Path,
    manifest_path: Path,
    manifest_data: dict[str, Any],
    explicit: Path | None,
) -> Path:
    if explicit is not None:
        path = explicit if explicit.is_absolute() else (cwd / explicit)
        path = path.resolve(strict=False)
        if not path.is_file():
            die(f"Error: audio file not found: {path}")
        return path

    referenced = list(iter_manifest_audio_refs(manifest_data, manifest_path, cwd))
    referenced_ogg = [p for p in referenced if p.suffix.lower() == ".ogg"]

    if len(referenced_ogg) == 1:
        return referenced_ogg[0]

    if len(referenced_ogg) > 1:
        names = ", ".join(p.name for p in referenced_ogg)
        die(
            "Error: manifest references multiple .ogg files:\n"
            f"  {names}\n"
            "Use --audio to choose one explicitly."
        )

    if len(referenced) == 1:
        return referenced[0]

    if len(referenced) > 1:
        names = ", ".join(p.name for p in referenced)
        die(
            "Error: manifest references multiple audio files:\n"
            f"  {names}\n"
            "Use --audio to choose one explicitly."
        )

    oggs = sorted(
        [p.resolve(strict=False) for p in cwd.iterdir() if p.is_file() and p.suffix.lower() == ".ogg"],
        key=lambda p: p.name.casefold(),
    )

    if len(oggs) == 1:
        return oggs[0]

    if not oggs:
        die(
            "Error: no source audio file was found.\n"
            "Expected either a manifest-referenced audio file or exactly one .ogg in the current directory."
        )

    names = ", ".join(p.name for p in oggs)
    die(
        "Error: multiple .ogg files were found in the current directory:\n"
        f"  {names}\n"
        "Use --audio to choose one explicitly."
    )


def parse_ratio(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, str) or "/" not in value:
        return None
    left, right = value.split("/", 1)
    try:
        num = int(left)
        den = int(right)
    except ValueError:
        return None
    if den == 0:
        return None
    return num, den


def probe_audio_info(audio_path: Path) -> AudioInfo:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,duration_ts,time_base",
        "-of", "json",
        str(audio_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)

    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "unknown ffprobe error"
        die(f"Error: ffprobe failed for {audio_path.name}:\n{detail}")

    try:
        data = json.loads(proc.stdout)
        streams = data.get("streams", [])
        stream = streams[0]
        sample_rate = int(stream["sample_rate"])
    except Exception as exc:
        die(f"Error: could not read audio stream info from {audio_path.name}: {exc}")

    if sample_rate <= 0:
        die(f"Error: invalid sample rate reported for {audio_path.name}: {sample_rate}")

    total_samples: int | None = None
    ratio = parse_ratio(stream.get("time_base"))
    duration_ts = stream.get("duration_ts")

    if ratio is not None and duration_ts is not None:
        try:
            num, den = ratio
            ticks = to_decimal(duration_ts, "stream.duration_ts")
            total = (ticks * Decimal(num) * Decimal(sample_rate)) / Decimal(den)
            exactish = qint(total)
            if exactish > 0:
                total_samples = exactish
        except ValueError:
            total_samples = None

    return AudioInfo(sample_rate=sample_rate, total_samples=total_samples)


def seconds_to_samples(seconds: Decimal, sample_rate: int) -> int:
    return qint(seconds * Decimal(sample_rate))


def slice_sample_bounds(s: SliceDef, sample_rate: int) -> tuple[int, int]:
    start_sample = seconds_to_samples(s.start_s, sample_rate)
    duration_samples = seconds_to_samples(s.duration_s, sample_rate)

    if duration_samples <= 0:
        duration_samples = 1

    end_sample = start_sample + duration_samples
    return start_sample, end_sample


def validate_slices_against_audio_length(
    slices: list[SliceDef],
    sample_rate: int,
    total_samples: int | None,
) -> None:
    if total_samples is None:
        return

    for s in slices:
        _, end_sample = slice_sample_bounds(s, sample_rate)
        if end_sample > total_samples:
            die(
                f"Error: slice {s.key!r} extends beyond the source audio.\n"
                f"  slice end sample:  {end_sample}\n"
                f"  source last sample: {total_samples}\n"
                "Check the manifest timing or choose the correct source audio."
            )


def sanitize_key_for_filename(key: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", key).strip("._-")
    return cleaned or "unnamed"


def make_unique_output_filenames(slices: list[SliceDef]) -> dict[str, str]:
    used: set[str] = set()
    out: dict[str, str] = {}

    for s in slices:
        base = f"key_{sanitize_key_for_filename(s.key)}"
        filename = f"{base}.ogg"
        n = 2

        while filename.casefold() in used:
            filename = f"{base}_{n}.ogg"
            n += 1

        used.add(filename.casefold())
        out[s.key] = filename

    return out


def choose_default_filename(
    slices: list[SliceDef],
    filename_map: dict[str, str],
    explicit_default_key: str | None,
) -> str:
    if explicit_default_key is not None:
        if explicit_default_key not in filename_map:
            die(f"Error: requested default key {explicit_default_key!r} does not exist.")
        return filename_map[explicit_default_key]

    if "30" in filename_map:
        return filename_map["30"]

    return filename_map[slices[0].key]


def choose_output_dir(cwd: Path, audio_path: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        return (explicit if explicit.is_absolute() else (cwd / explicit)).resolve(strict=False)

    base = (cwd.name or audio_path.stem or "wayclick_pack").strip() or "wayclick_pack"
    name = base if base.endswith("_wayclick") else f"{base}_wayclick"
    return (cwd / name).resolve(strict=False)


def batched(items: list[SliceDef], batch_size: int):
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]


def run_ffmpeg_batch(
    audio_path: Path,
    batch: list[SliceDef],
    output_dir: Path,
    filename_map: dict[str, str],
    sample_rate: int,
    quality: int,
) -> None:
    outputs: list[tuple[int, Path, int, int]] = []

    for index, s in enumerate(batch):
        start_sample, end_sample = slice_sample_bounds(s, sample_rate)
        if end_sample <= start_sample:
            die(
                f"Error: slice {s.key!r} rounds to zero or negative length "
                f"at {sample_rate} Hz."
            )

        out_path = output_dir / filename_map[s.key]
        outputs.append((index, out_path, start_sample, end_sample))

    filter_lines: list[str] = []

    if len(outputs) == 1:
        _, _, start_sample, end_sample = outputs[0]
        filter_lines.append(
            f"[0:a]atrim=start_sample={start_sample}:end_sample={end_sample},"
            "asetpts=PTS-STARTPTS[out0]"
        )
    else:
        split_labels = "".join(f"[a{i}]" for i in range(len(outputs)))
        filter_lines.append(f"[0:a]asplit={len(outputs)}{split_labels}")
        for i, _, start_sample, end_sample in outputs:
            filter_lines.append(
                f"[a{i}]atrim=start_sample={start_sample}:end_sample={end_sample},"
                f"asetpts=PTS-STARTPTS[out{i}]"
            )

    filter_script = ";\n".join(filter_lines) + "\n"

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".ffscript",
        delete=False,
    ) as tmp:
        tmp.write(filter_script)
        filter_script_path = Path(tmp.name)

    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-nostdin",
            "-i", str(audio_path),
            "-filter_complex_script", str(filter_script_path),
        ]

        for i, out_path, _, _ in outputs:
            cmd.extend([
                "-map", f"[out{i}]",
                "-map_metadata", "-1",
                "-map_chapters", "-1",
                "-c:a", "libvorbis",
                "-q:a", str(quality),
                str(out_path),
            ])

        proc = subprocess.run(cmd, capture_output=True, text=True)

        if proc.returncode != 0:
            detail = proc.stderr.strip() or proc.stdout.strip() or "unknown ffmpeg error"
            die(f"Error: ffmpeg failed:\n{detail}")

    finally:
        filter_script_path.unlink(missing_ok=True)


def slice_with_ffmpeg(
    audio_path: Path,
    slices: list[SliceDef],
    output_dir: Path,
    filename_map: dict[str, str],
    sample_rate: int,
    quality: int,
    batch_size: int,
) -> None:
    if not (0 <= quality <= 10):
        die("Error: --quality must be between 0 and 10 for libvorbis.")
    if batch_size < 1:
        die("Error: --batch-size must be >= 1.")

    output_dir.mkdir(parents=True, exist_ok=True)

    total_batches = (len(slices) + batch_size - 1) // batch_size
    for batch_index, batch in enumerate(batched(slices, batch_size), start=1):
        print(f"[*] ffmpeg batch {batch_index}/{total_batches} ({len(batch)} slices)")
        run_ffmpeg_batch(
            audio_path=audio_path,
            batch=batch,
            output_dir=output_dir,
            filename_map=filename_map,
            sample_rate=sample_rate,
            quality=quality,
        )


def write_wayclick_config(
    output_dir: Path,
    slices: list[SliceDef],
    filename_map: dict[str, str],
    default_filename: str,
) -> Path:
    mappings = {s.key: filename_map[s.key] for s in slices}
    config = {
        "defaults": [default_filename],
        "mappings": mappings,
    }

    config_path = output_dir / "config.json"
    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return config_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Slice an audio sprite into individual .ogg files and generate a "
            "WayClick pack config.json."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "-j", "--json", "--manifest",
        dest="json_path",
        type=Path,
        help=(
            "Input sprite manifest JSON. If omitted, auto-detects "
            "config.json / sprite.json / a valid *.json in the current directory."
        ),
    )
    parser.add_argument(
        "-a", "--audio",
        dest="audio_path",
        type=Path,
        help=(
            "Source audio file. If omitted, auto-detects from manifest references "
            "or requires exactly one .ogg in the current directory."
        ),
    )
    parser.add_argument(
        "-o", "--output-dir",
        dest="output_dir",
        type=Path,
        help="Output pack directory.",
    )
    parser.add_argument(
        "--default-key",
        help="Key to use for WayClick defaults[0]. If omitted, prefers key '30', else the earliest slice.",
    )
    parser.add_argument(
        "-q", "--quality",
        type=int,
        default=5,
        help="libvorbis quality for output .ogg files (0..10).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=256,
        help="Maximum number of slices per ffmpeg batch.",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    require_tool("ffmpeg")
    require_tool("ffprobe")

    cwd = Path.cwd().resolve(strict=False)

    manifest = find_manifest(cwd, args.json_path)
    assert manifest.data is not None
    assert manifest.slices is not None

    audio_path = find_audio(cwd, manifest.path, manifest.data, args.audio_path)
    audio_info = probe_audio_info(audio_path)

    validate_slices_against_audio_length(
        manifest.slices,
        audio_info.sample_rate,
        audio_info.total_samples,
    )

    output_dir = choose_output_dir(cwd, audio_path, args.output_dir)

    if output_dir.exists() and not output_dir.is_dir():
        die(f"Error: output path exists and is not a directory: {output_dir}")

    output_config_path = (output_dir / "config.json").resolve(strict=False)
    if output_config_path == manifest.path.resolve(strict=False):
        die(
            "Error: the output config.json would overwrite the input manifest.\n"
            "Choose a different --output-dir."
        )

    filename_map = make_unique_output_filenames(manifest.slices)
    default_filename = choose_default_filename(
        manifest.slices,
        filename_map,
        args.default_key,
    )

    print(f"[+] Manifest:    {manifest.path.name}")
    print(f"[+] Audio:       {audio_path.name}")
    print(f"[+] Sample rate: {audio_info.sample_rate} Hz")
    print(f"[+] Slices:      {len(manifest.slices)}")
    print(f"[+] Output dir:  {output_dir}")

    if audio_info.total_samples is None:
        print("[i] ffprobe did not expose an exact stream sample count; preflight bounds check was skipped.")
    else:
        print(f"[+] Source len:  {audio_info.total_samples} samples")

    if output_dir.exists():
        try:
            if any(output_dir.iterdir()):
                print("[i] Output directory already exists; expected files will be overwritten, unrelated files will be left untouched.")
        except OSError:
            pass

    slice_with_ffmpeg(
        audio_path=audio_path,
        slices=manifest.slices,
        output_dir=output_dir,
        filename_map=filename_map,
        sample_rate=audio_info.sample_rate,
        quality=args.quality,
        batch_size=args.batch_size,
    )

    config_path = write_wayclick_config(
        output_dir=output_dir,
        slices=manifest.slices,
        filename_map=filename_map,
        default_filename=default_filename,
    )

    print("[+] Done.")
    print(f"[+] Wrote pack to: {output_dir}")
    print(f"[+] Wrote config:  {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
