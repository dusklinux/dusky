#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
import fcntl
import stat
import json
import os
import random
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rich import box
from rich.console import Console
from rich.markup import escape as escape_markup
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.prompt import Confirm
from rich.table import Table

DEFAULT_DIR = "/mnt/zram1/dusky_ytdlp/"
DEFAULT_ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".mp4", ".m4a", ".mp3", ".wav", ".webm",
        ".mkv", ".opus", ".mka", ".flac", ".aac", ".m4v", ".mov",
    }
)

EP_RE: re.Pattern[str] = re.compile(
    r"(?:"
    r"\b(?:america\s+(?:first|frist)(?:\s+with\s+nicholas\s+j\.?\s+fuentes)?|af)\s+(?:ep\.?|episode)\s*(\d{1,6})(?!\w)"
    r"|\b(?:ep\.?|episode)\s*(\d{1,6})(?!\w)"
    r"|[\|｜]\s*(\d{3,4})(?!\w)"
    r")",
    re.IGNORECASE,
)
NUMERIC_STEM_RE: re.Pattern[str] = re.compile(r"^(\d{1,6}(?:_\d+)?)$")

EXIT_OK = 0
EXIT_RUNTIME_FAILURE = 1
EXIT_USAGE = 2
EXIT_INTERRUPT = 130

console = Console()


def escape(text: str) -> str:
    """Display filename bytes that aren't UTF-8 without emitting invalid text."""
    return escape_markup(text.encode("utf-8", "backslashreplace").decode("utf-8"))


@dataclass(frozen=True, slots=True)
class PlanEntry:
    source: Path
    episode_id: int
    target_name: str
    identity: tuple[int, int] | None = None

    @property
    def target(self) -> Path:
        return self.source.parent / self.target_name


@dataclass
class ScanResult:
    plans: list[PlanEntry] = field(default_factory=list)
    media_identities: set[tuple[int, int]] = field(default_factory=set)
    already: list[str] = field(default_factory=list)
    skipped_no_pattern: list[tuple[str, str]] = field(default_factory=list)
    skipped_bad_ext: list[tuple[str, str]] = field(default_factory=list)
    skipped_exists: list[tuple[str, str]] = field(default_factory=list)
    skipped_duplicate: list[tuple[str, str]] = field(default_factory=list)
    skipped_other: list[tuple[str, str]] = field(default_factory=list)

    @property
    def skipped_total(self) -> int:
        return (
            len(self.skipped_no_pattern)
            + len(self.skipped_bad_ext)
            + len(self.skipped_exists)
            + len(self.skipped_duplicate)
            + len(self.skipped_other)
        )


def parse_allowed_extensions(raw: str | None) -> frozenset[str]:
    if not raw:
        return DEFAULT_ALLOWED_EXTENSIONS
    cleaned: set[str] = set()
    for token in re.split(r"[,\s]+", raw.strip()):
        if not token:
            continue
        token = token.lower()
        if not token.startswith("."):
            token = f".{token}"
        cleaned.add(token)
    return frozenset(cleaned) or DEFAULT_ALLOWED_EXTENSIONS


def normalize_extension(filename: str, allowed: frozenset[str]) -> str | None:
    suffix = Path(filename).suffix
    if not suffix:
        return None
    lowered = suffix.lower()
    return lowered if lowered in allowed else None


def extract_episode_id(filename: str) -> int | None:
    try:
        match = EP_RE.search(filename)
        if not match:
            return None
        for g in match.groups():
            if g is not None:
                episode_id = int(g)
                return episode_id if episode_id > 0 else None
        return None
    except (ValueError, OverflowError):
        return None


def canonical_name_for_numeric(filename: str, allowed: frozenset[str]) -> str | None:
    suffix = Path(filename).suffix
    if not suffix:
        return None
    lowered = suffix.lower()
    if lowered not in allowed:
        return None
    stem = filename[: -len(suffix)] if suffix else filename
    match = NUMERIC_STEM_RE.fullmatch(stem)
    if not match:
        return None
    parts = stem.split("_")
    try:
        base_id = int(parts[0])
        if base_id <= 0:
            return None
        if len(parts) == 1:
            canonical = f"{base_id}{lowered}"
        else:
            sub_id = int(parts[1])
            if sub_id <= 0:
                return None
            canonical = f"{base_id}_{sub_id}{lowered}"
    except (ValueError, OverflowError):
        return None
    return None if canonical == filename else canonical


def is_canonical(filename: str, allowed: frozenset[str]) -> bool:
    suffix = Path(filename).suffix
    if not suffix or suffix != suffix.lower() or suffix.lower() not in allowed:
        return False
    stem = filename[: -len(suffix)]
    match = NUMERIC_STEM_RE.fullmatch(stem)
    if not match:
        return False
    parts = stem.split("_")
    try:
        base_id = int(parts[0])
        if base_id <= 0:
            return False
        if len(parts) == 1:
            return stem == str(base_id) and filename == f"{base_id}{suffix.lower()}"
        sub_id = int(parts[1])
        if sub_id <= 0:
            return False
        return stem == f"{base_id}_{sub_id}" and filename == f"{stem}{suffix.lower()}"
    except (ValueError, OverflowError):
        return False


def iter_candidate_files(directory: Path, recursive: bool) -> list[Path]:
    try:
        if recursive:
            def raise_walk_error(exc: OSError) -> None:
                raise exc

            entries = [root / name for root, _, names in directory.walk(on_error=raise_walk_error)
                       for name in names]
        else:
            entries = list(directory.iterdir())
    except OSError as exc:
        raise OSError(f"cannot list directory '{directory}': {exc.strerror or exc}") from exc
    def file_sort_key(p: Path) -> tuple[int, str, str]:
        try:
            sz = -p.stat().st_size
        except OSError:
            sz = 0
        return (sz, p.name.casefold(), str(p))

    entries.sort(key=file_sort_key)
    return entries


def plan_renames(
    directory: Path, allowed: frozenset[str], recursive: bool = False
) -> ScanResult:
    result = ScanResult()
    seen_targets: dict[Path, str] = {}

    for item in iter_candidate_files(directory, recursive):
        name = item.name

        try:
            info = item.lstat()
        except FileNotFoundError:
            result.skipped_other.append((name, "vanished during scan"))
            continue
        identity = (info.st_dev, info.st_ino)
        if stat.S_ISLNK(info.st_mode):
            result.skipped_other.append((name, "symlink; skipped for safety"))
            continue
        if not stat.S_ISREG(info.st_mode):
            continue

        if is_canonical(name, allowed):
            result.media_identities.add(identity)
            result.already.append(name)
            continue

        fixup = canonical_name_for_numeric(name, allowed)
        if fixup is not None:
            result.media_identities.add(identity)
            target_path = item.parent / fixup
            if target_path in seen_targets:
                result.skipped_duplicate.append(
                    (name, f"target '{fixup}' already queued from '{seen_targets[target_path]}'")
                )
                continue
            if target_path.exists() or target_path.is_symlink():
                result.skipped_exists.append(
                    (name, f"target '{fixup}' already exists; refusing to overwrite")
                )
                continue
            try:
                episode_id = int(Path(fixup).stem)
            except ValueError:
                result.skipped_other.append((name, "unparseable numeric name"))
                continue
            seen_targets[target_path] = name
            result.plans.append(PlanEntry(source=item, episode_id=episode_id, target_name=fixup, identity=identity))
            continue

        episode_id = extract_episode_id(name)
        if episode_id is None:
            result.skipped_no_pattern.append(
                (name, "no recognised America First episode number")
            )
            continue

        ext = normalize_extension(name, allowed)
        if ext is None:
            result.skipped_bad_ext.append(
                (name, f"extension '{Path(name).suffix or '(none)'}' not in allowed set")
            )
            continue

        result.media_identities.add(identity)
        target_name = f"{episode_id}{ext}"
        if target_name == name:
            result.already.append(name)
            continue
        target_path = item.parent / target_name
        if target_path in seen_targets:
            result.skipped_duplicate.append(
                (name, f"target '{target_name}' already queued from '{seen_targets[target_path]}'")
            )
            continue
        if target_path.exists() or target_path.is_symlink():
            result.skipped_exists.append(
                (name, f"target '{target_name}' already exists; refusing to overwrite")
            )
            continue

        seen_targets[target_path] = name
        result.plans.append(PlanEntry(source=item, episode_id=episode_id, target_name=target_name, identity=identity))

    return result


# Linux/glibc interface: renameat2(..., RENAME_NOREPLACE) is one atomic
# directory-entry change, including on filesystems without hard links.
_libc = ctypes.CDLL(None, use_errno=True)
_renameat2 = _libc.renameat2
_renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
_renameat2.restype = ctypes.c_int


def file_identity(path: Path) -> tuple[int, int]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise OSError(f"source '{path.name}' is not a regular file")
    return info.st_dev, info.st_ino


def safe_rename(src: Path, dst: Path, identity: tuple[int, int] | None = None) -> None:
    actual = file_identity(src)
    if identity is not None and actual != identity:
        raise OSError(f"source '{src.name}' changed since it was scanned")
    if _renameat2(-100, os.fsencode(src), -100, os.fsencode(dst), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(src), None, str(dst))


def execute_plans(
    plans: list[PlanEntry],
    show_progress: bool = True,
    before_rename: Callable[[PlanEntry], None] | None = None,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    succeeded: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []

    if not plans:
        return succeeded, failed

    def _run_one(plan: PlanEntry) -> None:
        if before_rename is not None:
            before_rename(plan)
        try:
            safe_rename(plan.source, plan.target, plan.identity)
        except FileExistsError as exc:
            failed.append((plan.source.name, f"target exists at execution time: {exc}"))
        except FileNotFoundError as exc:
            failed.append((plan.source.name, f"source vanished: {exc}"))
        except PermissionError as exc:
            failed.append((plan.source.name, f"permission denied: {exc}"))
        except OSError as exc:
            failed.append((plan.source.name, f"{type(exc).__name__}: {exc}"))
        else:
            succeeded.append((plan.source.name, plan.target_name))

    if not show_progress:
        for plan in plans:
            _run_one(plan)
        return succeeded, failed

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    with progress:
        task = progress.add_task("Renaming…", total=len(plans))
        for plan in plans:
            progress.update(task, description=f"Renaming {escape(plan.source.name[:50])}")
            _run_one(plan)
            progress.advance(task)

    return succeeded, failed


def undo_from_log(log_path: Path, *, dry_run: bool = False) -> tuple[int, int, list[str]]:
    ok, failed = 0, 0
    errors: list[str] = []
    try:
        with open(log_path, "r", encoding="utf-8", errors="surrogateescape",
                  opener=lambda path, flags: os.open(path, flags | os.O_NONBLOCK)) as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                raise OSError("undo log must be a regular file")
            fcntl.flock(fh, fcntl.LOCK_SH if dry_run else fcntl.LOCK_EX)
            lines = fh.read().splitlines()
            # Simulate filename changes for dry-runs, so chained records have
            # the same outcome as a real undo without touching the filesystem.
            virtual: dict[Path, tuple[int, int] | None] = {}

            def identity_at(path: Path) -> tuple[int, int] | None:
                if path in virtual:
                    return virtual[path]
                try:
                    return file_identity(path)
                except FileNotFoundError:
                    return None

            for index in range(len(lines) - 1, -1, -1):
                line = lines[index]
                if not line.strip():
                    continue
                try:
                    line.encode("utf-8")  # Reject invalid raw bytes, preserving other records.
                    record = json.loads(line)
                    if not isinstance(record, dict) or not all(
                        isinstance(record.get(key), str) and record[key]
                        for key in ("dir", "src", "dst")
                    ):
                        raise ValueError("expected nonempty dir/src/dst strings")
                    directory = Path(record["dir"])
                    for key in ("src", "dst"):
                        name = record[key]
                        if Path(name).name != name or name in {".", ".."} or "\x00" in name:
                            raise ValueError("src/dst must be filenames")
                    if "\x00" in record["dir"]:
                        raise ValueError("directory contains NUL")
                    current = directory / record["dst"]
                    original = directory / record["src"]
                    if current == original:
                        raise ValueError("source and destination are identical")
                    identity = record.get("identity")
                    if identity is not None and (
                        not isinstance(identity, list) or len(identity) != 2
                        or any(type(value) is not int or value < 0 for value in identity)
                    ):
                        raise ValueError("invalid file identity")
                    expected = tuple(identity) if identity is not None else None
                except (ValueError, KeyError, TypeError, RecursionError) as exc:
                    failed += 1
                    errors.append(f"line {index + 1}: corrupt entry ({exc})")
                    continue
                try:
                    # A record is also written for an attempted rename that
                    # never happened. Already-restored records are no-ops.
                    restored = identity_at(original)
                    if expected is not None and restored == expected:
                        continue
                    actual = identity_at(current)
                    if expected is None and actual is None and restored is not None:
                        continue  # Logs written before identity tracking.
                    if actual is None:
                        raise FileNotFoundError(current)
                    if expected is not None and actual != expected:
                        raise OSError(f"'{current.name}' is a different file; leaving it untouched")
                    if restored is not None:
                        raise FileExistsError(original)
                    if dry_run:
                        virtual[current], virtual[original] = None, actual
                    else:
                        safe_rename(current, original, expected)
                except (OSError, ValueError) as exc:
                    failed += 1
                    errors.append(f"line {index + 1}: {record['dst']} -> {record['src']}: {exc}")
                else:
                    ok += 1
    except (OSError, UnicodeError) as exc:
        return ok, failed + 1, [*errors, f"cannot read log '{log_path}': {exc}"]
    return ok, failed, errors


def resolve_directory(explicit: str | None) -> tuple[Path, str]:
    if explicit:
        return Path(explicit).expanduser(), "command line"
    for var in ("DUSKY_YTDLP_DIR", "DSUSKY_YTDLP_DIR"):
        value = os.environ.get(var)
        if value:
            legacy = " (legacy typo, still honoured)" if var == "DSUSKY_YTDLP_DIR" else ""
            return Path(value).expanduser(), f"${var}{legacy}"
    return Path(DEFAULT_DIR).expanduser(), "built-in default"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rename 'America First Ep. <N>' media files to canonical '<N>.<ext>'.",
        epilog="Idempotent: already-canonical files are left untouched. Never overwrites.",
    )
    parser.add_argument(
        "dir",
        nargs="?",
        default=None,
        help="Target directory (overrides $DUSKY_YTDLP_DIR and the default).",
    )
    parser.add_argument("--dir", dest="dir_opt", default=None, help="Same as DIR.")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation prompt.")
    parser.add_argument(
        "-n", "--dry-run", action="store_true", help="Show the plan without renaming anything."
    )
    parser.add_argument(
        "-r", "--recursive", action="store_true", help="Process subdirectories too."
    )
    parser.add_argument(
        "--extensions",
        default=None,
        help="Comma-separated extension allow-list, e.g. '--extensions mp4,m4a,mp3'.",
    )
    parser.add_argument(
        "--log",
        default=None,
        help="Undo-log path (default: <target-dir>/.rename_undo.jsonl).",
    )
    parser.add_argument("--undo", default=None, help="Restore names from LOG and exit.")
    parser.add_argument("-q", "--quiet", action="store_true", help="Minimal output.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show skipped-file details.")
    parser.add_argument("--no-color", action="store_true", help="Disable Rich colour output.")
    parser.add_argument("--self-test", action="store_true", help="Run the built-in regression suite and exit.")
    return parser


def run_self_test() -> int:
    failures: list[str] = []
    passes = 0

    def check(cond: bool, label: str) -> None:
        nonlocal passes
        if cond:
            passes += 1
        else:
            failures.append(label)
            console.print(f"[red]FAIL {label}[/]")

    def run(*args: str, env_extra: dict[str, str] | None = None):
        env = dict(os.environ)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), *args],
            capture_output=True,
            text=True,
            env=env,
        )

    check(".m4a" in parse_allowed_extensions(None), "units default ext")
    check(parse_allowed_extensions("mp4,m4a") == frozenset({".mp4", ".m4a"}), "units custom ext")
    check(parse_allowed_extensions("") == DEFAULT_ALLOWED_EXTENSIONS, "units empty ext")
    check(parse_allowed_extensions(",,,   ") == DEFAULT_ALLOWED_EXTENSIONS, "units junk ext")

    for name, want in [
        ("T America First Ep. 1687 [a].m4a", 1687),
        ("america first ep. 42.mp4", 42),
        ("AMERICA FIRST EP.1687.m4a", 1687),
        ("America First Ep.  1687.m4a", 1687),
        ("America   First   Ep.  7.mp3", 7),
        ("America First Ep. 0.m4a", None),
        ("America First Ep..m4a", None),
        ("IRAN WAR BEGINS [x].m4a", None),
        ("1687.m4a", None),
        ("random file.txt", None),
        ("America First Ep. 9999999.m4a", None),
        ("America First Ep. 0005.m4a", 5),
        ("America First Ep. 1.m4a America First Ep. 2.m4a", 1),
        ("", None),
        ("America First Ep. 12a34.m4a", None),
        ("America First with Nicholas J. Fuentes Ep. 836 [x].m4a", 836),
        ("America First Ep 871 [x].m4a", 871),
        ("America Frist Ep. 911 [x].m4a", 911),
    ]:
        check(extract_episode_id(name) == want, "units extract " + repr(name))

    for name, want in [
        ("a.m4a", ".m4a"),
        ("a.MP4", ".mp4"),
        ("a.txt", None),
        ("a.m4a.part", None),
        ("noext", None),
        ("a.MKV", ".mkv"),
        (".m4a", None),
        ("a.m4a ", None),
    ]:
        check(normalize_extension(name, DEFAULT_ALLOWED_EXTENSIONS) == want, "units ext " + repr(name))

    check(is_canonical("1687.m4a", DEFAULT_ALLOWED_EXTENSIONS), "units canonical yes")
    check(not is_canonical("1687.MP4", DEFAULT_ALLOWED_EXTENSIONS), "units canonical case")
    check(not is_canonical("001687.m4a", DEFAULT_ALLOWED_EXTENSIONS), "units canonical zeros")
    check(not is_canonical("0.m4a", DEFAULT_ALLOWED_EXTENSIONS), "units canonical zero")
    check(not is_canonical("abc.m4a", DEFAULT_ALLOWED_EXTENSIONS), "units canonical alpha")
    check(canonical_name_for_numeric("001687.m4a", DEFAULT_ALLOWED_EXTENSIONS) == "1687.m4a", "units fixup zeros")
    check(canonical_name_for_numeric("1687.MP4", DEFAULT_ALLOWED_EXTENSIONS) == "1687.mp4", "units fixup case")
    check(canonical_name_for_numeric("1687.m4a", DEFAULT_ALLOWED_EXTENSIONS) is None, "units fixup none")

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        fixture = {
            "SHOW America First Ep. 1687 [v1].m4a": "EP1687-A",
            "SHOW America First Ep. 1686 [v2].m4a": "EP1686",
            "lowercase america first ep. 1600 [x].mp3": "EP1600",
            "NOSPACE America First Ep.1685 [x].m4a": "EP1685",
            "DUBSPACE America First Ep.  1684 [x].wav": "EP1684",
            "UPPER America First Ep. 1683 [x].MP4": "EP1683",
            "ZERO America First Ep. 0007 [x].m4a": "EP7",
            "1680.m4a": "ALREADY1680",
            "001679.m4a": "ZEROPAD1679",
            "1678.MP4": "UPPER1678",
            "IRAN WAR BEGINS [v9].m4a": "NOPATTERN",
            "random notes.txt": "TEXT",
            "America First Ep. 1670 [x].txt": "BADEXT",
            "America First Ep. 1669 [x].m4a.part": "PARTFILE",
            "noextensionfile": "NOEXT",
            "America First Ep. 0 [x].m4a": "ZEROEP",
            "DUP-A America First Ep. 1650 [aaa].m4a": "DUP-A",
            "DUP-B America First Ep. 1650 [bbb].m4a": "DUP-B",
            "1649.m4a": "PRE1649",
            "COLLIDE America First Ep. 1649 [zzz].m4a": "COLLIDE",
            "FULLWIDTH America First Ep. 1648 [x].m4a": "FULLWIDTH",
        }
        for name, content in fixture.items():
            (d / name).write_bytes(content.encode())
        (d / "sub").mkdir()
        (d / "sub" / "SUB America First Ep. 1646 [x].m4a").write_text("SUBFILE")
        try:
            os.symlink(d / "1680.m4a", d / "link.m4a")
            os.symlink(d / "missing", d / "broken.m4a")
        except OSError:
            pass
        before = sorted(p.name for p in d.iterdir())
        r = run(str(d), "--dry-run", "--no-color", "-q")
        check(r.returncode == 0, "fixture dry-run exit 0")
        check(sorted(p.name for p in d.iterdir()) == before, "fixture dry-run no mutation")
        r = run(str(d), "--yes", "--no-color", "-q")
        check(r.returncode == 0, "fixture run exit 0")

        def content_of(name: str) -> str:
            return (d / name).read_bytes().decode()

        check((d / "1687.m4a").exists() and content_of("1687.m4a") == "EP1687-A", "fixture 1687 bytes")
        check((d / "1683.mp4").exists() and content_of("1683.mp4") == "EP1683", "fixture ext lower")
        check((d / "7.m4a").exists(), "fixture zero ep")
        check(content_of("1680.m4a") == "ALREADY1680", "fixture already untouched")
        check((d / "1679.m4a").exists() and content_of("1679.m4a") == "ZEROPAD1679", "fixture zeropad")
        check((d / "1678.mp4").exists() and content_of("1678.mp4") == "UPPER1678", "fixture upper")
        check((d / "IRAN WAR BEGINS [v9].m4a").exists(), "fixture nopattern kept")
        check((d / "America First Ep. 1670 [x].txt").exists(), "fixture badext kept")
        check(content_of("1649.m4a") == "PRE1649", "fixture no overwrite")
        check((d / "COLLIDE America First Ep. 1649 [zzz].m4a").exists(), "fixture collide kept")
        check((d / "1650.m4a").exists(), "fixture dup once")
        check(content_of("1650.m4a") in ("DUP-A", "DUP-B"), "fixture dup winner valid")
        check(len([p for p in d.iterdir() if p.name.startswith("DUP-")]) == 1, "fixture dup loser kept")
        check((d / "sub" / "SUB America First Ep. 1646 [x].m4a").exists(), "fixture sub ignored")
        check((d / "link.m4a").is_symlink(), "fixture symlink kept")
        logf = d / ".rename_undo.jsonl"
        check(logf.exists(), "fixture undo log exists")
        rec = json.loads(logf.read_text().splitlines()[0])
        check({"ts", "dir", "src", "dst"} <= set(rec), "fixture undo schema")
        snap = sorted(p.name for p in d.iterdir())
        r2 = run(str(d), "--yes", "--no-color", "-q")
        check(r2.returncode == 0, "fixture rerun exit 0")
        check(sorted(p.name for p in d.iterdir()) == snap, "fixture idempotent")
        r3 = run(str(d), "--yes", "--no-color", "--recursive", "--dry-run", "-q")
        check(r3.returncode == 0, "fixture recursive ok")

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        for n in [
            "  spaces  America First Ep. 1901 [x].m4a",
            "newline America First Ep. 1902 [x]\n.m4a",
            "dots... America First Ep. 1903 [x].m4a",
            "UPPER.WAV America First Ep. 1904 [x].WAV",
            "tab\tAmerica First Ep. 1905 [x].m4a",
            "uni-é-ß-日 America First Ep. 1906 [x].opus",
            "America First Ep. 1907 [x].m4a.backup",
            ".hidden America First Ep. 1908 [x].m4a",
        ]:
            (d / n).write_bytes(b"x")
        (d / ("a" * 200)).write_bytes(b"long")
        r = run(str(d), "--yes", "--no-color", "-q")
        check(r.returncode == 0, "weird exit 0")
        check((d / "1901.m4a").exists(), "weird spaces")
        check((d / "1904.wav").exists(), "weird upper wav")
        check((d / "1906.opus").exists(), "weird unicode opus")
        check((d / "1908.m4a").exists(), "weird hidden")
        check((d / "America First Ep. 1907 [x].m4a.backup").exists(), "weird double ext kept")
        snap = sorted(p.name for p in d.iterdir())
        r2 = run(str(d), "--yes", "--no-color", "-q")
        check(sorted(p.name for p in d.iterdir()) == snap and r2.returncode == 0, "weird idempotent")

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "RACE America First Ep. 1910 [x].m4a").write_bytes(b"RACE")
        planned = plan_renames(d, DEFAULT_ALLOWED_EXTENSIONS)
        check(len(planned.plans) == 1, "race plan one")
        (d / "1910.m4a").write_bytes(b"SQUATTER")
        try:
            safe_rename(planned.plans[0].source, planned.plans[0].target)
            check(False, "race must refuse")
        except FileExistsError:
            check(True, "race refuses overwrite")
        check((d / "1910.m4a").read_bytes() == b"SQUATTER", "race squatter intact")
        check((d / "RACE America First Ep. 1910 [x].m4a").read_bytes() == b"RACE", "race source intact")

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        rng = random.Random(7)
        total_bytes = 0
        for i in range(60):
            ep = 3000 + (i % 20)
            tag = "".join(rng.choice("abXYZ  .-é") for _ in range(rng.randint(1, 12)))
            name = "F" + tag + " America First Ep. " + str(ep) + " [v" + str(i) + "].m4a"
            data = ("payload-" + str(i)).encode()
            total_bytes += len(data)
            (d / name).write_bytes(data)
        n_before = len(list(d.iterdir()))
        r = run(str(d), "--yes", "--no-color", "-q")
        check(r.returncode == 0, "fuzz exit 0")
        files = list(d.iterdir())
        byte_sum = sum(p.stat().st_size for p in files if p.is_file() and p.name != ".rename_undo.jsonl")
        check(byte_sum == total_bytes, "fuzz bytes conserved")
        check(len(files) == n_before + 1, "fuzz count conserved plus log")
        targets = [p.name for p in files if p.suffix == ".m4a" and p.stem.isdigit()]
        check(len(targets) == len(set(targets)), "fuzz no dup targets")
        snap = sorted(p.name for p in d.iterdir())
        r2 = run(str(d), "--yes", "--no-color", "-q")
        check(r2.returncode == 0 and sorted(p.name for p in d.iterdir()) == snap, "fuzz idempotent")

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        original_name = "America First with Nicholas J. Fuentes Ep 123 [x].m4a"
        for folder in ("a", "b"):
            (d / folder).mkdir()
            (d / folder / original_name).write_text(folder, encoding="utf-8")
        check(len(plan_renames(d, DEFAULT_ALLOWED_EXTENSIONS, True).plans) == 2,
              "recursive independent targets")
        check(run(str(d), "--recursive", "--yes", "-q").returncode == 0,
              "recursive execution")
        logf = d / ".rename_undo.jsonl"
        records = [json.loads(line) for line in logf.read_text(encoding="utf-8").splitlines()]
        check({record["dir"] for record in records} == {str(d / "a"), str(d / "b")},
              "recursive undo folders")
        check(run("--undo", str(logf), "--dry-run", "-q").returncode == 0
              and all((d / folder / "123.m4a").exists() for folder in ("a", "b")),
              "undo dry-run no mutation")
        check(run("--undo", str(logf), "-q").returncode == 0
              and all((d / folder / original_name).read_text(encoding="utf-8") == folder
                      for folder in ("a", "b")), "recursive undo restores contents")
        logf.write_text('null\n[]\n{"dir": 1, "src": "a", "dst": "b"}\n', encoding="utf-8")
        check(undo_from_log(logf)[1] == 3, "malformed undo records reported")

    r = run("/nonexistent-dir-xyz-123", "--no-color")
    check(r.returncode == 2, "cli missing dir exit 2")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "A America First Ep. 1950 [x].m4a").write_bytes(b"A")
        (d / "B America First Ep. 1951 [x].mkv").write_bytes(b"B")
        r = run(str(d), "--dry-run", "--no-color", "-q", "--extensions", "m4a")
        check(r.returncode == 0 and (d / "A America First Ep. 1950 [x].m4a").exists(), "cli dry-run kept")
        check(not (d / "1950.m4a").exists(), "cli dry-run no file")
        r = run(str(d), "--yes", "--no-color", "-q", "--extensions", "m4a")
        check(r.returncode == 0 and (d / "1950.m4a").exists(), "cli custom ext renames m4a")
        check((d / "B America First Ep. 1951 [x].mkv").exists(), "cli custom ext skips mkv")
    with tempfile.TemporaryDirectory() as tmp:
        r = run(tmp, "--no-color", "-q")
        check(r.returncode == 0, "cli empty dir ok")
        f = Path(tmp) / "notadir"
        f.write_text("x")
        r = run(str(f), "--no-color")
        check(r.returncode == 2, "cli file-as-dir exit 2")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "U America First Ep. 2001 [x].m4a").write_text("U2001")
        check(run(str(d), "--yes", "--no-color", "-q").returncode == 0, "cli undo setup")
        logf = d / ".rename_undo.jsonl"
        logf.write_text(logf.read_text() + "CORRUPT-LINE\n" + json.dumps({"bad": 1}) + "\n")
        r = run("--undo", str(logf), "--no-color", "-q")
        check(r.returncode == 1, "cli corrupt undo exit 1")
        check((d / "U America First Ep. 2001 [x].m4a").exists(), "cli undo restores despite corrupt lines")
    with tempfile.TemporaryDirectory() as tmp:
        r = run("--dry-run", "--no-color", "-q", env_extra={"DUSKY_YTDLP_DIR": tmp})
        check(r.returncode == 0, "env correct var")
        r = run("--dry-run", "--no-color", "-q", env_extra={"DSUSKY_YTDLP_DIR": tmp})
        check(r.returncode == 0, "env legacy var")

    console.print(f"[dim]PASSES {passes} FAILURES {len(failures)}[/]")
    if failures:
        return EXIT_RUNTIME_FAILURE
    console.print("[green]ALL CHECKS PASSED[/]")
    return EXIT_OK


def render_plan(result: ScanResult, allowed: frozenset[str], verbose: bool, quiet: bool) -> None:
    if quiet:
        return
    if result.plans:
        table = Table(
            title=f"[bold green]{len(result.plans)} file(s) queued for renaming[/]",
            box=box.ROUNDED,
            show_lines=False,
        )
        table.add_column("#", justify="right", style="dim", width=4)
        table.add_column("Episode", justify="right", style="cyan", width=8)
        table.add_column("Source", style="white", overflow="fold")
        table.add_column("Target", style="bold green")
        for idx, plan in enumerate(result.plans, 1):
            src = plan.source.name
            src_short = src if len(src) <= 70 else src[:67] + "…"
            table.add_row(str(idx), str(plan.episode_id), escape(src_short), escape(plan.target_name))
        console.print(table)
    else:
        console.print("[dim]No files queued for renaming.[/]")

    if result.already and (verbose or not quiet):
        console.print(f"[green]✓ {len(result.already)} already organised (idempotent no-op).[/]")
        if verbose:
            for name in sorted(result.already)[:20]:
                console.print(f"  [dim]• {escape(name)}[/]")
            if len(result.already) > 20:
                console.print(f"  [dim]… and {len(result.already) - 20} more[/]")

    skip_groups = [
        ("No episode pattern", result.skipped_no_pattern, "yellow"),
        ("Unsupported extension", result.skipped_bad_ext, "magenta"),
        ("Target already exists", result.skipped_exists, "red"),
        ("Duplicate target in batch", result.skipped_duplicate, "red"),
        ("Other (symlink, etc.)", result.skipped_other, "dim"),
    ]
    for label, entries, style in skip_groups:
        if not entries:
            continue
        if not verbose and label in ("No episode pattern", "Other (symlink, etc.)"):
            console.print(f"[{style}]⊘ {len(entries)} skipped — {label.lower()} (use -v for names).[/]")
            continue
        table = Table(title=f"[bold {style}]{len(entries)} skipped — {label}[/]", box=box.ROUNDED)
        table.add_column("File", overflow="fold")
        table.add_column("Reason", overflow="fold")
        for name, reason in sorted(entries)[:50]:
            table.add_row(escape(name if len(name) <= 70 else name[:67] + "…"), escape(reason))
        if len(entries) > 50:
            table.add_row(f"… +{len(entries) - 50} more", "")
        console.print(table)

    ext_list = ", ".join(sorted(allowed))
    console.print(f"[dim]Allowed extensions: {escape(ext_list)}[/]")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    global console
    if args.no_color:
        console = Console(no_color=True)

    if args.self_test:
        return run_self_test()

    if args.undo:
        ok, failed, errors = undo_from_log(Path(args.undo).expanduser(), dry_run=args.dry_run)
        if not args.quiet:
            console.print(
                Panel(
                    f"[green]{'Would restore' if args.dry_run else 'Restored'}: {ok}[/]   [red]Failed: {failed}[/]",
                    title="Undo preview" if args.dry_run else "Undo complete",
                    box=box.ROUNDED,
                )
            )
        for err in errors[:20]:
            console.print(f"[red]• {escape(err)}[/]")
        return EXIT_OK if failed == 0 else EXIT_RUNTIME_FAILURE

    allowed = parse_allowed_extensions(args.extensions)
    explicit = args.dir_opt or args.dir
    raw_dir, origin = resolve_directory(explicit)
    try:
        directory = raw_dir.resolve()
    except OSError as exc:
        console.print(f"[red]Cannot resolve directory: {escape(str(exc))}[/]")
        return EXIT_USAGE

    if not args.quiet:
        console.print(
            Panel.fit(
                "[bold cyan]Smart File Renamer[/]\n"
                f"[dim]Target:[/] {escape(str(directory))}\n"
                f"[dim]Source:[/] {escape(origin)}"
                + ("   [bold yellow]DRY RUN[/]" if args.dry_run else "")
                + ("   [dim](recursive)[/]" if args.recursive else ""),
                title="🎬 ytdlp organiser",
                box=box.ROUNDED,
            )
        )

    if not directory.is_dir():
        console.print(f"[bold red]Error:[/] not a directory: {escape(str(directory))}")
        return EXIT_USAGE
    if not os.access(directory, os.R_OK | os.X_OK):
        console.print(f"[bold red]Error:[/] directory not readable: {escape(str(directory))}")
        return EXIT_USAGE
    if not args.dry_run and not os.access(directory, os.W_OK | os.X_OK):
        console.print(f"[bold red]Error:[/] directory not writable (use --dry-run): {escape(str(directory))}")
        return EXIT_USAGE

    try:
        with console.status("[cyan]Scanning directory…[/]", spinner="dots"):
            result = plan_renames(directory, allowed, recursive=args.recursive)
        scanned = (
            len(result.plans)
            + len(result.already)
            + result.skipped_total
        )
        if not args.quiet:
            console.print(f"[dim]Scanned {scanned} file(s).[/]")
        render_plan(result, allowed, verbose=args.verbose, quiet=args.quiet)
    except OSError as exc:
        console.print(f"[bold red]Scan failed:[/] {escape(str(exc))}")
        return EXIT_RUNTIME_FAILURE
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted during scan. Nothing was changed.[/]")
        return EXIT_INTERRUPT

    if not result.plans:
        if not args.quiet:
            console.print(
                Panel(
                    f"Nothing to do. {len(result.already)} already organised, "
                    f"{result.skipped_total} skipped.",
                    title="✅ All tidy",
                    box=box.ROUNDED,
                )
            )
        return EXIT_OK

    if args.dry_run:
        if not args.quiet:
            console.print("[bold yellow]Dry run — no files were changed.[/]")
        return EXIT_OK

    if not args.yes:
        try:
            proceed = Confirm.ask(
                f"Proceed with renaming {len(result.plans)} file(s)?",
                default=False,
            )
        except (EOFError, KeyboardInterrupt):
            console.print("\n[yellow]Aborted. Nothing was changed.[/]")
            return EXIT_INTERRUPT
        if not proceed:
            console.print("[yellow]Cancelled. Nothing was changed.[/]")
            return EXIT_OK

    log_path = Path(args.log).expanduser() if args.log else directory / ".rename_undo.jsonl"
    try:
        log_path = log_path.resolve()
        if normalize_extension(log_path.name, allowed) is not None and (
            is_canonical(log_path.name, allowed)
            or canonical_name_for_numeric(log_path.name, allowed) is not None
            or extract_episode_id(log_path.name) is not None
        ):
            raise OSError("undo log cannot use a media episode filename")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a+", encoding="utf-8") as undo_log:
            fcntl.flock(undo_log, fcntl.LOCK_EX)
            log_info = os.fstat(undo_log.fileno())
            if not stat.S_ISREG(log_info.st_mode):
                raise OSError("undo log must be a regular file")
            log_identity = (log_info.st_dev, log_info.st_ino)
            if log_identity in result.media_identities:
                raise OSError("undo log overlaps a planned media source or target")
            # Keep a previous partial record separate from this run's records.
            if log_info.st_size and os.pread(undo_log.fileno(), 1, log_info.st_size - 1) != b"\n":
                undo_log.write("\n")
                undo_log.flush()

            def record_attempt(plan: PlanEntry) -> None:
                identity = file_identity(plan.source)
                if plan.identity is not None and identity != plan.identity:
                    raise OSError(f"source '{plan.source.name}' changed since it was scanned")
                undo_log.write(json.dumps({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "dir": str(plan.source.parent.absolute()),
                    "src": plan.source.name, "dst": plan.target_name,
                    "identity": identity,
                }, ensure_ascii=True) + "\n")
                # Write-ahead: failures and process interruption happen before
                # the name change, or leave a record identifying the moved file.
                undo_log.flush()

            succeeded, failed = execute_plans(
                result.plans, show_progress=not args.quiet, before_rename=record_attempt,
            )
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted mid-run. Undo records identify attempted renames.[/]")
        return EXIT_INTERRUPT
    except OSError as exc:
        console.print(f"[red]Rename/log failure: {escape(str(exc))}. Check {escape(str(log_path))} for undo records.[/]")
        return EXIT_RUNTIME_FAILURE

    if not args.quiet:
        status = "green" if not failed else "red"
        console.print(
            Panel(
                f"[green]Renamed: {len(succeeded)}[/]   "
                f"[red]Failed: {len(failed)}[/]   "
                f"[dim]Skipped: {result.skipped_total}   Already: {len(result.already)}[/]"
                + (f"\n[dim]Undo log: {escape(str(log_path))}[/]" if succeeded else ""),
                title=f"[{status}]Processing complete[/]",
                box=box.ROUNDED,
            )
        )
        for name, err in failed[:20]:
            console.print(f"[red]✗ {escape(name)}: {escape(err)}[/]")

    return EXIT_OK if not failed else EXIT_RUNTIME_FAILURE


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        console.print("\n\n[yellow]Interrupted by user (Ctrl+C). Exiting safely.[/]")
        raise SystemExit(EXIT_INTERRUPT)
