#!/usr/bin/env python3
# =============================================================================
# ARCH LINUX :: DUSKY THEME :: GTK3 WALLPAPER SELECTOR
#
# Target: Python 3.14+, Linux, GTK3 / PyGObject
#
# Features:
#   - Asynchronous directory scanning and bounded thumbnail loading
#   - Atomic thumbnail replacement and source-fingerprint caching
#   - Coordinated GUI/CLI cache operations
#   - Serialized wallpaper/theme application
#   - Favorites, search, keyboard navigation, and cache rebuild progress
#
# External dependencies:
#   - PyGObject with Gtk 3.0, GdkPixbuf 2.0, and Pango introspection data
#   - ImageMagick: magick
#   - awww and awww-daemon
#   - theme_ctl.sh for full application / favorite cycling
#   - notify-send is optional
#
# TRACKER FORMAT:
#   "basename" preserves the original external tracker-file contract.
#   Ambiguous duplicate basenames are rejected before application.
#
#   Change to "relative" only when ALL external tracker readers support IDs
#   such as "landscapes/example.jpg".
#
# This is asynchronous loading, not a virtualized/viewport-only image grid.
# =============================================================================

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

from concurrent.futures import (
    FIRST_COMPLETED,
    Future,
    ThreadPoolExecutor,
    wait,
)
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango


# =============================================================================
# CONFIGURATION
# =============================================================================

HOME = Path.home()

WALLPAPER_DIR = HOME / "Pictures/wallpapers"
SETTINGS_DIR = HOME / ".config/dusky/settings"
THEME_DIR = SETTINGS_DIR / "dusky_theme"

FAVORITES_FILE = THEME_DIR / "wal_fav_list"
STATE_FILE = THEME_DIR / "state.conf"
FAV_STATE_FILE = THEME_DIR / "current_fav"
TRACK_LIGHT = THEME_DIR / "light_wal"
TRACK_DARK = THEME_DIR / "dark_wal"

APP_SETTINGS_FILE = THEME_DIR / "gtk_wall_settings"
THEME_CTL = HOME / "user_scripts/theme_matugen/theme_ctl.sh"

CACHE_DIR = HOME / ".cache/dusky_images/wallpaper_selector"
THUMB_DIR = CACHE_DIR / "thumbs"

# Lock files must remain outside the directory being swept.
CACHE_LOCK_FILE = CACHE_DIR / "cache.lock"
APPLY_LOCK_FILE = CACHE_DIR / "apply.lock"
FAVORITES_LOCK_FILE = CACHE_DIR / "favorites.lock"

TRACKER_ID_FORMAT = "basename"  # "basename" or "relative"

AWWW_COMMAND = "awww"
AWWW_DAEMON_COMMAND = "awww-daemon"
MAGICK_COMMAND = "magick"

THUMB_SIZE = 240
RENDER_SIZE = 145
THUMB_RECIPE = "dusky-gtk-thumb-r26"

IMAGE_EXTENSIONS = frozenset({
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
})

# Conservative concurrency: ImageMagick limits apply PER process.
WORKER_COUNT = min(os.process_cpu_count() or 2, 4)
MAX_IMAGE_JOBS = WORKER_COUNT * 2

THUMB_TIMEOUT = 25.0
BAD_THUMB_RETRY_SECONDS = 300.0
AWWW_QUERY_TIMEOUT = 1.5
AWWW_START_TIMEOUT = 7.0
AWWW_APPLY_TIMEOUT = 30.0
THEME_TIMEOUT = 180.0

DEFAULT_SETTINGS = {
    "AUTO_CLOSE": False,
    "FAST_APPLY_AUTO_CLOSE": False,
    "SHOW_FILENAMES": True,
    "START_IN_FAVORITES": False,
    "AUTO_SWEEP_CACHE": False,
}

TRANSITION_OPTIONS = (
    ("AWWW_TRANS_TYPE", "--transition-type"),
    ("AWWW_TRANS_DURATION", "--transition-duration"),
    ("AWWW_TRANS_FPS", "--transition-fps"),
    ("AWWW_TRANS_BEZIER", "--transition-bezier"),
    ("AWWW_TRANS_ANGLE", "--transition-angle"),
    ("AWWW_TRANS_POS", "--transition-pos"),
)

RELEVANT_STATE_KEYS = {
    "THEME_MODE",
    *(key for key, _ in TRANSITION_OPTIONS),
}

_NATURAL_PARTS = re.compile(r"([0-9]+)")
_CACHE_FILENAME = re.compile(r"([0-9a-f]{64})\.(.+)")


# =============================================================================
# GENERAL HELPERS
# =============================================================================

class OperationCancelled(Exception):
    """An operation was intentionally cancelled."""


class BusyError(RuntimeError):
    """A cooperating process already owns an operation lock."""


def log_error(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def check_cancelled(stop_event: threading.Event | None) -> None:
    if stop_event is not None and stop_event.is_set():
        raise OperationCancelled()


def natural_key(text: str) -> tuple:
    """
    Deterministic, case-insensitive natural sorting by ASCII digit runs.

    Uses digit-string lengths rather than int(), so extremely long numeric
    filenames do not encounter Python's integer-string conversion limit.

    This is not an implementation of GNU sort -V.
    """
    parts = []

    for part in _NATURAL_PARTS.split(text):
        if part and part[0].isascii() and part[0].isdigit():
            normalized = part.lstrip("0") or "0"
            parts.append((1, len(normalized), normalized))
        else:
            parts.append((0, part.casefold()))

    return tuple(parts), text


def read_optional_text(path: Path) -> str:
    """Missing files are optional; other failures must remain visible."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def atomic_write(path: Path, content: str) -> None:
    """
    Atomically replace a UTF-8 text file.

    Existing symlinks are followed. Failure is reported to the caller.
    Individual file replacement is atomic; multiple files are not a transaction.
    """
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    temporary = target.with_name(
        f"{target.name}.tmp.{uuid.uuid4().hex}"
    )

    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temporary, target)

        directory_fd = os.open(
            target.parent,
            os.O_RDONLY | os.O_DIRECTORY,
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


@contextmanager
def file_lock(
    path: Path,
    *,
    exclusive: bool = True,
    timeout: float | None = None,
    stop_event: threading.Event | None = None,
):
    """
    Cancellable advisory flock.

    All operations that need coordination must use the same lock file.
    Lock files must not be deleted while operations may be using them.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    deadline = (
        None if timeout is None
        else time.monotonic() + timeout
    )

    with path.open("a+b") as handle:
        while True:
            check_cancelled(stop_event)

            try:
                fcntl.flock(
                    handle.fileno(),
                    operation | fcntl.LOCK_NB,
                )
                break
            except BlockingIOError:
                if (
                    deadline is not None
                    and time.monotonic() >= deadline
                ):
                    raise BusyError(
                        f"Another operation is already using {path.name}."
                    ) from None

                if stop_event is None:
                    time.sleep(0.05)
                else:
                    stop_event.wait(0.05)

        try:
            check_cancelled(stop_event)
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def require_binary(command: str) -> str:
    resolved = shutil.which(command)
    if resolved is None:
        raise FileNotFoundError(
            f"Required executable was not found: {command}"
        )
    return resolved


def kill_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_command(
    command: list[str],
    *,
    timeout: float,
    stop_event: threading.Event | None = None,
    pass_fds: tuple[int, ...] = (),
    check: bool = True,
) -> subprocess.CompletedProcess:
    """
    Run a command with bounded duration and cancellation.

    On timeout/cancellation, terminate its process group and reap the specific
    child. This covers ordinary descendants that remain in that group.
    """
    check_cancelled(stop_event)

    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
        pass_fds=pass_fds,
    )

    deadline = time.monotonic() + timeout

    try:
        while True:
            check_cancelled(stop_event)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)

            try:
                stdout, stderr = process.communicate(
                    timeout=min(0.2, remaining)
                )
                break
            except subprocess.TimeoutExpired:
                continue

    except BaseException:
        kill_process_group(process)
        process.communicate()
        raise

    result = subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout,
        stderr,
    )

    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            command,
            output=stdout,
            stderr=stderr,
        )

    return result


def describe_error(error: BaseException) -> str:
    if isinstance(error, subprocess.TimeoutExpired):
        command = error.cmd
        executable = command[0] if isinstance(command, list) else command
        return f"{executable} timed out after {error.timeout:g} seconds."

    if isinstance(error, subprocess.CalledProcessError):
        command = error.cmd
        executable = command[0] if isinstance(command, list) else command
        details = (error.stderr or error.output or "").strip()

        if isinstance(details, bytes):
            details = details.decode("utf-8", errors="replace")

        message = (
            f"{executable} exited with status {error.returncode}."
        )
        return message + (f"\n{details}" if details else "")

    if isinstance(error, OperationCancelled):
        return "Operation cancelled."

    return str(error) or error.__class__.__name__


def validate_relative_id(value: str) -> str:
    """
    Validate an ID representable by the existing UTF-8 line-based files.

    Symlinks inside WALLPAPER_DIR are supported; IDs remain lexical paths
    relative to WALLPAPER_DIR rather than resolved target paths.
    """
    if not value:
        raise ValueError("An empty wallpaper ID is not valid.")

    value.encode("utf-8")

    if "\n" in value or "\r" in value or "\0" in value:
        raise ValueError("Wallpaper IDs must not contain line breaks or NUL.")

    path = Path(value)

    if path.is_absolute() or ".." in path.parts:
        raise ValueError(
            f"Wallpaper ID must be relative to {WALLPAPER_DIR}: {value!r}"
        )

    if str(path) in {"", "."}:
        raise ValueError("Invalid wallpaper ID.")

    return str(path)


# =============================================================================
# DIRECTORY SCANNING
# =============================================================================

def scan_wallpapers(
    stop_event: threading.Event | None = None,
) -> list[str]:
    """
    Iterative traversal with directory-inode cycle detection.

    Directory-read failures propagate. An incomplete scan must not be used
    as an authoritative inventory for cache sweeping.
    """
    check_cancelled(stop_event)

    if not WALLPAPER_DIR.is_dir():
        raise NotADirectoryError(
            f"Wallpaper directory does not exist or is not a directory:\n"
            f"{WALLPAPER_DIR}"
        )

    pending = [WALLPAPER_DIR]
    visited = set()
    wallpapers = []

    while pending:
        check_cancelled(stop_event)
        directory = pending.pop()

        try:
            info = directory.stat()
        except OSError as error:
            raise OSError(
                f"Cannot inspect wallpaper directory {directory}: {error}"
            ) from error

        identity = (info.st_dev, info.st_ino)
        if identity in visited:
            continue
        visited.add(identity)

        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError as error:
            raise OSError(
                f"Cannot read wallpaper directory {directory}: {error}"
            ) from error

        subdirectories = []

        for entry in entries:
            check_cancelled(stop_event)
            path = directory / entry.name

            try:
                if entry.is_dir(follow_symlinks=True):
                    subdirectories.append(path)
                elif (
                    entry.is_file(follow_symlinks=True)
                    and path.suffix.lower() in IMAGE_EXTENSIONS
                ):
                    relative = str(path.relative_to(WALLPAPER_DIR))

                    try:
                        relative = validate_relative_id(relative)
                    except (ValueError, UnicodeError) as error:
                        log_error(
                            f"Skipping unsupported filename "
                            f"{relative!r}: {error}"
                        )
                        continue

                    wallpapers.append(relative)

            except OSError as error:
                raise OSError(
                    f"Cannot inspect wallpaper entry {path}: {error}"
                ) from error

        # Deterministic depth-first traversal, with scandir already closed.
        pending.extend(reversed(subdirectories))

    wallpapers.sort(key=natural_key)
    return wallpapers


# =============================================================================
# CACHE MANAGEMENT
# =============================================================================

@dataclass
class CacheBuildResult:
    wallpapers: list[str]
    generated: int = 0
    failed: int = 0


class CacheManager:
    @staticmethod
    def get_digest(rel_path: str) -> str:
        data = (
            os.fsencode(str(WALLPAPER_DIR))
            + b"\0"
            + os.fsencode(rel_path)
            + b"\0"
            + THUMB_RECIPE.encode("ascii")
        )
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def get_thumb_path(rel_path: str) -> Path:
        return THUMB_DIR / f"{CacheManager.get_digest(rel_path)}.png"

    @staticmethod
    def signature(info: os.stat_result) -> list[int]:
        return [
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        ]

    @staticmethod
    def read_metadata(path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, UnicodeError):
            return {}

    @staticmethod
    def generate_thumb(
        rel_path: str,
        *,
        force: bool = False,
        stop_event: threading.Event | None = None,
        cache_locked: bool = False,
    ) -> str:
        """
        Return 'generated', 'cached', or 'failed'.

        Cancellation propagates. A 'failed' result must not be interpreted as
        permission to display an old thumbnail that happens to remain on disk.
        """
        check_cancelled(stop_event)

        def generate_with_image_lock():
            THUMB_DIR.mkdir(parents=True, exist_ok=True)
            thumb = CacheManager.get_thumb_path(rel_path)

            with file_lock(
                thumb.with_suffix(".lock"),
                stop_event=stop_event,
            ):
                return CacheManager._generate_locked(
                    rel_path,
                    force=force,
                    stop_event=stop_event,
                )

        try:
            if cache_locked:
                return generate_with_image_lock()

            with file_lock(
                CACHE_LOCK_FILE,
                exclusive=False,
                stop_event=stop_event,
            ):
                return generate_with_image_lock()

        except OperationCancelled:
            raise
        except Exception as error:
            log_error(
                f"Thumbnail failed for {rel_path!r}: "
                f"{describe_error(error)}"
            )
            return "failed"

    @staticmethod
    def _generate_locked(
        rel_path: str,
        *,
        force: bool,
        stop_event: threading.Event | None,
    ) -> str:
        check_cancelled(stop_event)

        source_path = WALLPAPER_DIR / validate_relative_id(rel_path)
        thumb_path = CacheManager.get_thumb_path(rel_path)
        metadata_path = thumb_path.with_suffix(".json")
        temporary = thumb_path.with_name(
            f"{thumb_path.stem}.{uuid.uuid4().hex}.tmp.png"
        )

        with source_path.open("rb") as source:
            signature = CacheManager.signature(os.fstat(source.fileno()))
            metadata = CacheManager.read_metadata(metadata_path)

            if not force and metadata.get("source") == signature:
                if metadata.get("status") == "ok":
                    try:
                        thumb_info = thumb_path.stat()
                    except OSError:
                        pass
                    else:
                        if (
                            metadata.get("thumbnail")
                            == CacheManager.signature(thumb_info)
                        ):
                            return "cached"

                if metadata.get("status") == "bad":
                    retry_at = metadata.get("retry_at")
                    if (
                        isinstance(retry_at, (int, float))
                        and time.time() < retry_at
                    ):
                        return "failed"

            def source_unchanged() -> bool:
                try:
                    return (
                        CacheManager.signature(os.fstat(source.fileno()))
                        == signature
                        and CacheManager.signature(source_path.stat())
                        == signature
                    )
                except OSError:
                    return False

            def record_conversion_failure() -> None:
                if not source_unchanged():
                    return

                atomic_write(
                    metadata_path,
                    json.dumps({
                        "source": signature,
                        "status": "bad",
                        "retry_at": (
                            time.time() + BAD_THUMB_RETRY_SECONDS
                        ),
                    }) + "\n",
                )

            if signature[2] == 0:
                record_conversion_failure()
                return "failed"

            # Missing dependencies are not negatively cached.
            magick = require_binary(MAGICK_COMMAND)
            nice = require_binary("nice")

            # Passing an opened file descriptor avoids ImageMagick interpreting
            # brackets, glob characters, or backslashes in the actual filename.
            command = [
                nice, "-n", "19",
                magick,
                "-limit", "thread", "1",
                "-limit", "memory", "128MiB",
                "-limit", "map", "256MiB",
                "-limit", "disk", "1GiB",
                "-limit", "time", str(max(1, int(THUMB_TIMEOUT) - 2)),
                f"/proc/self/fd/{source.fileno()}[0]",
                "-auto-orient",
                "-strip",
                "-thumbnail", f"{THUMB_SIZE}x{THUMB_SIZE}^",
                "-gravity", "center",
                "-extent", f"{THUMB_SIZE}x{THUMB_SIZE}",
                "(",
                "-size", f"{THUMB_SIZE}x{THUMB_SIZE}",
                "xc:none",
                "-fill", "white",
                "-draw",
                (
                    f"roundrectangle 0,0,"
                    f"{THUMB_SIZE - 1},{THUMB_SIZE - 1},24,24"
                ),
                ")",
                "-alpha", "set",
                "-compose", "DstIn",
                "-composite",
                str(temporary),
            ]

            try:
                run_command(
                    command,
                    timeout=THUMB_TIMEOUT,
                    stop_event=stop_event,
                    pass_fds=(source.fileno(),),
                )

                check_cancelled(stop_event)

                if not source_unchanged():
                    log_error(
                        f"Source changed during conversion: {rel_path!r}"
                    )
                    return "failed"

                if temporary.stat().st_size == 0:
                    raise OSError("ImageMagick produced an empty thumbnail.")

                os.replace(temporary, thumb_path)

                atomic_write(
                    metadata_path,
                    json.dumps({
                        "source": signature,
                        "status": "ok",
                        "thumbnail": CacheManager.signature(
                            thumb_path.stat()
                        ),
                    }) + "\n",
                )
                return "generated"

            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
            ) as error:
                log_error(
                    f"ImageMagick could not convert {rel_path!r}:\n"
                    f"{describe_error(error)}"
                )
                record_conversion_failure()
                return "failed"

            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    @staticmethod
    def _sweep_locked(
        wallpapers: list[str],
        stop_event: threading.Event | None = None,
    ) -> int:
        """
        Caller must hold CACHE_LOCK_FILE exclusively.

        No cooperating thumbnail writer can be active, so temporary files
        may be removed without an arbitrary age threshold.
        """
        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        valid = {
            CacheManager.get_digest(path)
            for path in wallpapers
        }
        removed = 0

        with os.scandir(THUMB_DIR) as entries:
            for entry in entries:
                check_cancelled(stop_event)

                if not entry.is_file(follow_symlinks=False):
                    continue

                match = _CACHE_FILENAME.fullmatch(entry.name)
                if match is None:
                    continue

                digest, suffix = match.groups()
                recognized = suffix in {"png", "json", "bad", "lock"}
                temporary = ".tmp." in entry.name

                if temporary or (recognized and digest not in valid):
                    try:
                        os.unlink(entry.path)
                        removed += 1
                    except FileNotFoundError:
                        pass

        return removed

    @staticmethod
    def scan_and_sweep(
        stop_event: threading.Event | None = None,
    ) -> list[str]:
        with file_lock(CACHE_LOCK_FILE, stop_event=stop_event):
            wallpapers = scan_wallpapers(stop_event)
            removed = CacheManager._sweep_locked(
                wallpapers, stop_event
            )
            print(f"Cache files removed: {removed}", flush=True)
            return wallpapers

    @staticmethod
    def build_cache(
        *,
        force: bool = False,
        progress_callback: Callable[[int, int, int, int], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> CacheBuildResult:
        # Check once, rather than emitting a missing-binary error per image.
        require_binary(MAGICK_COMMAND)
        require_binary("nice")

        with file_lock(CACHE_LOCK_FILE, stop_event=stop_event):
            THUMB_DIR.mkdir(parents=True, exist_ok=True)

            print(f"Scanning: {WALLPAPER_DIR}", flush=True)
            wallpapers = scan_wallpapers(stop_event)

            removed = CacheManager._sweep_locked(
                wallpapers, stop_event
            )
            print(
                f"Found {len(wallpapers)} images; "
                f"removed {removed} stale cache files.",
                flush=True,
            )

            result = CacheBuildResult(wallpapers)
            total = len(wallpapers)
            completed = 0
            last_progress = 0.0
            remaining = iter(wallpapers)

            def generate(path: str) -> str:
                return CacheManager.generate_thumb(
                    path,
                    force=force,
                    stop_event=stop_event,
                    cache_locked=True,
                )

            def report_progress(force_report: bool = False) -> None:
                nonlocal last_progress
                now = time.monotonic()

                if not force_report and now - last_progress < 0.05:
                    return

                if progress_callback is not None:
                    progress_callback(
                        completed,
                        total,
                        result.generated,
                        result.failed,
                    )
                else:
                    print(
                        f"\rProgress: {completed}/{total} | "
                        f"Generated: {result.generated} | "
                        f"Failed: {result.failed}",
                        end="",
                        flush=True,
                    )

                last_progress = now

            with ThreadPoolExecutor(
                max_workers=WORKER_COUNT,
                thread_name_prefix="cache-build",
            ) as executor:
                pending: dict[Future, str] = {}

                def fill_queue() -> None:
                    while len(pending) < MAX_IMAGE_JOBS:
                        check_cancelled(stop_event)
                        try:
                            path = next(remaining)
                        except StopIteration:
                            break
                        pending[executor.submit(generate, path)] = path

                try:
                    fill_queue()
                    report_progress(force_report=True)

                    while pending:
                        check_cancelled(stop_event)

                        finished, _ = wait(
                            pending,
                            timeout=0.2,
                            return_when=FIRST_COMPLETED,
                        )

                        for future in finished:
                            path = pending.pop(future)

                            try:
                                status = future.result()
                            except OperationCancelled:
                                raise
                            except Exception as error:
                                log_error(
                                    f"Cache worker failed for {path!r}: "
                                    f"{describe_error(error)}"
                                )
                                status = "failed"

                            completed += 1
                            result.generated += status == "generated"
                            result.failed += status == "failed"

                        fill_queue()
                        report_progress()

                    report_progress(force_report=True)

                finally:
                    for future in pending:
                        future.cancel()

            if progress_callback is None:
                print()

            print(
                f"Cache complete: {result.generated} generated, "
                f"{result.failed} unavailable.",
                flush=True,
            )
            return result


# =============================================================================
# STATE, FAVORITES, AND BACKEND
# =============================================================================

def read_state_conf() -> dict[str, str]:
    """
    Read relevant literal KEY=value settings.

    Supports quoted literals and trailing comments, but deliberately does not
    execute shell code or perform variable/command expansion.
    """
    state = {}

    for line_number, raw_line in enumerate(
        read_optional_text(STATE_FILE).splitlines(), 1
    ):
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[7:].lstrip()

        if "=" not in line:
            continue

        key, raw_value = line.split("=", 1)
        key = key.strip()

        if key not in RELEVANT_STATE_KEYS:
            continue

        try:
            values = shlex.split(
                raw_value,
                comments=True,
                posix=True,
            )
        except ValueError as error:
            raise ValueError(
                f"{STATE_FILE}:{line_number}: {error}"
            ) from error

        if len(values) > 1:
            raise ValueError(
                f"{STATE_FILE}:{line_number}: "
                f"{key} must contain one literal value."
            )

        value = values[0] if values else ""

        if "$" in value or "`" in value:
            raise ValueError(
                f"{STATE_FILE}:{line_number}: "
                "Shell expansion is not supported in selector settings."
            )

        state[key] = value

    mode = state.get("THEME_MODE", "dark")
    if mode not in {"light", "dark"}:
        raise ValueError(f"Invalid THEME_MODE: {mode!r}")

    return state


def read_tracker(path: Path) -> str:
    return read_optional_text(path).rstrip("\r\n")


def match_wallpaper_id(
    wallpapers: list[str],
    tracker: str,
) -> str | None:
    if not tracker:
        return None

    if TRACKER_ID_FORMAT == "relative":
        if tracker in wallpapers:
            return tracker

        # Allow unambiguous old basename trackers during migration.
        if "/" in tracker:
            return None

    matches = [
        path
        for path in wallpapers
        if os.path.basename(path) == tracker
    ]

    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous wallpaper tracker: {tracker!r}.\n"
            "Multiple wallpapers have that basename."
        )

    return matches[0] if matches else None


def tracker_id_for(
    rel_path: str,
    wallpapers: list[str],
) -> str:
    if TRACKER_ID_FORMAT == "relative":
        return rel_path

    if TRACKER_ID_FORMAT != "basename":
        raise ValueError(
            "TRACKER_ID_FORMAT must be 'basename' or 'relative'."
        )

    basename = os.path.basename(rel_path)
    matches = [
        path
        for path in wallpapers
        if os.path.basename(path) == basename
    ]

    if len(matches) != 1:
        examples = "\n".join(matches[:8])
        raise ValueError(
            f"Cannot safely apply {rel_path!r} with basename trackers.\n\n"
            f"Conflicting paths:\n{examples}\n\n"
            "Rename the duplicate files, or update all external tracker "
            "readers to support relative paths and set "
            "TRACKER_ID_FORMAT = 'relative'."
        )

    return basename


def load_favorites() -> set[str]:
    favorites = set()

    for value in read_optional_text(FAVORITES_FILE).splitlines():
        if not value:
            continue

        try:
            favorites.add(validate_relative_id(value))
        except (ValueError, UnicodeError) as error:
            log_error(f"Ignoring invalid favorite {value!r}: {error}")

    return favorites


def save_favorites(favorites: set[str]) -> None:
    content = "\n".join(sorted(favorites, key=natural_key))
    atomic_write(
        FAVORITES_FILE,
        content + ("\n" if content else ""),
    )


def toggle_saved_favorite(rel_path: str) -> set[str]:
    rel_path = validate_relative_id(rel_path)

    # GUI actions should fail clearly rather than block GTK indefinitely.
    with file_lock(FAVORITES_LOCK_FILE, timeout=0):
        favorites = load_favorites()

        if rel_path in favorites:
            favorites.remove(rel_path)
        else:
            favorites.add(rel_path)

        save_favorites(favorites)
        return favorites


def ensure_awww_daemon(
    stop_event: threading.Event | None = None,
) -> str:
    client = require_binary(AWWW_COMMAND)

    def query(timeout: float) -> subprocess.CompletedProcess:
        return run_command(
            [client, "query"],
            timeout=timeout,
            stop_event=stop_event,
            check=False,
        )

    try:
        result = query(AWWW_QUERY_TIMEOUT)
        if result.returncode == 0:
            return client
        last_error = (result.stderr or result.stdout or "").strip()
    except subprocess.TimeoutExpired as error:
        last_error = describe_error(error)

    daemon_binary = require_binary(AWWW_DAEMON_COMMAND)
    check_cancelled(stop_event)

    print("Starting awww-daemon...", flush=True)

    daemon = subprocess.Popen(
        [daemon_binary, "--format", "xrgb"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    # Reap only this particular child. Never use waitpid(-1).
    threading.Thread(
        target=daemon.wait,
        name="awww-daemon-waiter",
        daemon=True,
    ).start()

    deadline = time.monotonic() + AWWW_START_TIMEOUT

    while True:
        check_cancelled(stop_event)
        remaining = deadline - time.monotonic()

        if remaining <= 0:
            break

        try:
            result = query(min(AWWW_QUERY_TIMEOUT, remaining))
            if result.returncode == 0:
                return client
            last_error = (result.stderr or result.stdout or "").strip()
        except subprocess.TimeoutExpired as error:
            last_error = describe_error(error)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        if stop_event is None:
            time.sleep(min(0.15, remaining))
        else:
            stop_event.wait(min(0.15, remaining))

    daemon_status = daemon.poll()
    message = "awww-daemon did not become responsive."

    if daemon_status is not None:
        message += f"\nThe launched daemon exited with status {daemon_status}."

    if last_error:
        message += f"\n{last_error}"

    message += "\nCheck: awww query; awww-daemon --help"
    raise RuntimeError(message)


def _apply_wallpaper_locked(
    rel_path: str,
    *,
    regen: bool,
    wallpapers: list[str] | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    """
    Caller must hold APPLY_LOCK_FILE.

    Trackers are committed after successful wallpaper application and before
    theme refresh, because the theme controller may read those trackers.
    """
    rel_path = validate_relative_id(rel_path)
    check_cancelled(stop_event)

    if wallpapers is None:
        wallpapers = scan_wallpapers(stop_event)

    if rel_path not in wallpapers:
        raise FileNotFoundError(
            f"Wallpaper is no longer in the collection: {rel_path}"
        )

    full_path = WALLPAPER_DIR / rel_path
    if not full_path.is_file():
        raise FileNotFoundError(f"Wallpaper not found: {full_path}")

    tracker_id = tracker_id_for(rel_path, wallpapers)
    state = read_state_conf()
    mode = state.get("THEME_MODE", "dark")

    if regen and (
        not THEME_CTL.is_file()
        or not os.access(THEME_CTL, os.X_OK)
    ):
        raise RuntimeError(
            "Theme controller is missing or not executable:\n"
            f"{THEME_CTL}"
        )

    client = ensure_awww_daemon(stop_event)
    command = [client, "img"]

    for key, flag in TRANSITION_OPTIONS:
        value = state.get(key, "disable")
        if value and value != "disable":
            command.extend([flag, value])

    command.append(str(full_path))

    print(f"Applying: {full_path} (full apply: {regen})", flush=True)

    run_command(
        command,
        timeout=AWWW_APPLY_TIMEOUT,
        stop_event=stop_event,
    )

    track_file = TRACK_LIGHT if mode == "light" else TRACK_DARK

    try:
        # Do not insert a cancellation point between successful application
        # and these short persistence operations.
        atomic_write(track_file, tracker_id + "\n")
        atomic_write(FAV_STATE_FILE, tracker_id + "\n")
    except Exception as error:
        raise RuntimeError(
            "The wallpaper command succeeded, but tracker persistence "
            "failed. Some tracker files may already have been updated.\n\n"
            f"{describe_error(error)}"
        ) from error

    if regen:
        try:
            run_command(
                [str(THEME_CTL), "refresh"],
                timeout=THEME_TIMEOUT,
                stop_event=stop_event,
            )
        except Exception as error:
            raise RuntimeError(
                "The wallpaper was applied and its trackers were updated, "
                "but theme regeneration did not complete successfully.\n\n"
                f"{describe_error(error)}"
            ) from error


def perform_wallpaper_apply(
    rel_path: str,
    *,
    regen: bool,
    stop_event: threading.Event | None = None,
) -> None:
    with file_lock(
        APPLY_LOCK_FILE,
        timeout=0,
        stop_event=stop_event,
    ):
        _apply_wallpaper_locked(
            rel_path,
            regen=regen,
            stop_event=stop_event,
        )


def notify_best_effort(
    title: str,
    message: str,
    urgency: str = "low",
) -> None:
    binary = shutil.which("notify-send")
    if binary is None:
        return

    try:
        run_command(
            [
                binary,
                "-a", "dusky-fav-wal",
                "-h", "string:x-canonical-private-synchronous:fav-wal",
                "-i", "emblem-favorite-symbolic",
                "-u", urgency,
                "-t", "2000",
                "--",
                title,
                message,
            ],
            timeout=3,
            check=False,
        )
    except Exception:
        # Notifications must never determine whether application succeeded.
        pass


def cycle_favorites(
    direction: str,
    stop_event: threading.Event | None = None,
) -> int:
    if direction not in {"next", "prev"}:
        raise ValueError(f"Invalid cycling direction: {direction!r}")

    try:
        with file_lock(
            APPLY_LOCK_FILE,
            timeout=0,
            stop_event=stop_event,
        ):
            wallpapers = scan_wallpapers(stop_event)
            available = set(wallpapers)

            favorites = sorted(
                load_favorites() & available,
                key=natural_key,
            )

            if not favorites:
                notify_best_effort(
                    "No Favorites",
                    "No existing favorite wallpapers were found.",
                    "normal",
                )
                return 0

            # Resolve against the whole collection, not just favorites.
            # Otherwise a basename could appear unique only because its
            # conflicting counterpart is not a favorite.
            current = match_wallpaper_id(
                wallpapers,
                read_tracker(FAV_STATE_FILE),
            )

            if current not in favorites:
                selected = (
                    favorites[0]
                    if direction == "next"
                    else favorites[-1]
                )
            else:
                step = 1 if direction == "next" else -1
                index = (favorites.index(current) + step) % len(favorites)
                selected = favorites[index]

            _apply_wallpaper_locked(
                selected,
                regen=True,
                wallpapers=wallpapers,
                stop_event=stop_event,
            )

        notify_best_effort("Favorite", os.path.basename(selected))
        return 0

    except OperationCancelled:
        raise
    except Exception as error:
        message = describe_error(error)
        log_error(f"Favorite application failed:\n{message}")
        notify_best_effort("Wallpaper Error", message, "critical")
        return 1


# =============================================================================
# ERROR DIALOG
# =============================================================================

class ThemedErrorDialog(Gtk.Dialog):
    def __init__(
        self,
        parent: Gtk.Window,
        title: str,
        message: str,
    ):
        super().__init__(
            title=title,
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.set_default_size(560, 300)
        self.get_style_context().add_class("themed-error-dialog")

        content = self.get_content_area()
        content.set_spacing(14)
        content.set_border_width(20)

        header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
        )

        icon = Gtk.Image.new_from_icon_name(
            "dialog-error-symbolic",
            Gtk.IconSize.DIALOG,
        )
        header.pack_start(icon, False, False, 0)

        label = Gtk.Label(label=title)
        label.set_line_wrap(True)
        label.set_xalign(0)
        label.get_style_context().add_class("dialog-title")
        header.pack_start(label, True, True, 0)
        content.pack_start(header, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(
            Gtk.PolicyType.AUTOMATIC,
            Gtk.PolicyType.AUTOMATIC,
        )
        scrolled.set_min_content_height(140)
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)

        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_cursor_visible(False)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        text_view.set_monospace(True)
        text_view.set_left_margin(10)
        text_view.set_right_margin(10)
        text_view.set_top_margin(10)
        text_view.set_bottom_margin(10)

        # Keep unexpectedly enormous command output from overwhelming GTK.
        if len(message) > 100_000:
            message = message[:100_000] + "\n\n[Further output omitted]"

        text_view.get_buffer().set_text(message)
        scrolled.add(text_view)
        content.pack_start(scrolled, True, True, 0)

        self.add_button("OK", Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)
        self.connect("response", lambda dialog, response: dialog.destroy())
        self.connect("key-press-event", self._on_key_press)

        self.show_all()

    def _on_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.response(Gtk.ResponseType.CLOSE)
            return True
        return False


# =============================================================================
# GTK APPLICATION
# =============================================================================

class WallpaperApp:
    def __init__(self):
        self.app = Gtk.Application(
            application_id="com.dusky.wallpaperselector",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.app.connect("activate", self.do_activate)
        self.app.connect("shutdown", self.on_shutdown)

        self.window = None
        self.flowbox = None
        self.scrolled = None
        self.stack = None
        self.search_entry = None

        self.btn_all = None
        self.btn_fav = None
        self.btn_refresh = None
        self.btn_settings = None
        self.btn_help = None

        self.loading_spinner = None
        self.loading_title = None
        self.loading_progress = None
        self.loading_status = None

        self.empty_title = None
        self.empty_subtitle = None
        self.popover = None

        self.wallpapers: list[str] = []
        self.favorites: set[str] = set()
        self.children: dict[str, Gtk.FlowBoxChild] = {}
        self.current_selected_child = None

        self.search_query = ""
        self.settings = DEFAULT_SETTINGS.copy()
        self.show_only_favorites = False

        self.closing = False
        self.shutting_down = False
        self.is_refreshing = False
        self.is_applying = False

        self.generation = 0
        self.generation_stop = threading.Event()
        self.backend_stop = threading.Event()

        self.image_futures: set[Future] = set()
        self.image_iterator = iter(())
        self.control_future = None

        self.image_executor = ThreadPoolExecutor(
            max_workers=WORKER_COUNT,
            thread_name_prefix="thumbnail",
        )
        self.control_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="selector-control",
        )

        self.signal_sources = []
        self.startup_messages = []

        self._load_settings()

        try:
            self.favorites = load_favorites()
        except Exception as error:
            self.startup_messages.append(
                f"Could not read favorites:\n{describe_error(error)}"
            )

    # -------------------------------------------------------------------------
    # Settings and main-thread dispatch
    # -------------------------------------------------------------------------

    def _load_settings(self):
        try:
            content = read_optional_text(APP_SETTINGS_FILE)
        except Exception as error:
            self.startup_messages.append(
                f"Could not read settings:\n{describe_error(error)}"
            )
            content = ""

        boolean_values = {
            "true": True,
            "1": True,
            "yes": True,
            "false": False,
            "0": False,
            "no": False,
        }

        for line in content.splitlines():
            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().lower()

            if key not in self.settings:
                log_error(f"Ignoring unknown selector setting: {key}")
            elif value not in boolean_values:
                log_error(
                    f"Ignoring invalid Boolean setting: {key}={value}"
                )
            else:
                self.settings[key] = boolean_values[value]

        self.show_only_favorites = self.settings["START_IN_FAVORITES"]

    def _save_settings(self):
        lines = ["# Dusky GTK Wallpaper Selector Configuration"]

        for key, value in sorted(self.settings.items()):
            lines.append(f"{key}={'true' if value else 'false'}")

        atomic_write(APP_SETTINGS_FILE, "\n".join(lines) + "\n")

    def _post_ui(self, callback, *args):
        if self.closing or self.shutting_down:
            return
        GLib.idle_add(self._dispatch_ui, callback, args)

    def _dispatch_ui(self, callback, args):
        if not self.closing and not self.shutting_down:
            callback(*args)
        return GLib.SOURCE_REMOVE

    def show_error(self, title: str, message: str):
        log_error(f"{title}:\n{message}")

        if self.closing or self.window is None:
            return

        self.window.present()
        ThemedErrorDialog(self.window, title, message)

    # -------------------------------------------------------------------------
    # Window construction
    # -------------------------------------------------------------------------

    def do_activate(self, application):
        if self.closing:
            return

        if self.window is not None:
            self.window.present()
            return

        gtk_settings = Gtk.Settings.get_default()
        if gtk_settings is not None:
            gtk_settings.set_property(
                "gtk-application-prefer-dark-theme", True
            )

        self.window = Gtk.ApplicationWindow(application=application)
        self.window.set_title("Wallpaper Selector")
        self.window.set_default_size(800, 600)
        self.window.connect("destroy", self.on_window_destroy)
        self.window.connect("key-press-event", self.on_key_press)

        self.setup_css()

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
        )
        self.window.add(root)

        header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
        )
        header.set_name("header_bar")

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search…  /")
        self.search_entry.set_tooltip_text("Search filenames: Ctrl+F or /")
        self.search_entry.set_width_chars(22)
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("changed", self.on_search_changed)
        header.pack_start(self.search_entry, True, True, 0)

        tabs = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=4,
        )
        tabs.get_style_context().add_class("tab-container")

        self.btn_all = Gtk.Button(label="All")
        self.btn_all.get_style_context().add_class("tab-btn")
        self.btn_all.connect(
            "clicked", lambda button: self.set_view_mode(False)
        )

        self.btn_fav = Gtk.Button(label="♥")
        self.btn_fav.get_style_context().add_class("tab-btn")
        self.btn_fav.set_tooltip_text("Favorites view: Alt+P")
        self.btn_fav.connect(
            "clicked", lambda button: self.set_view_mode(True)
        )

        tabs.pack_start(self.btn_all, False, False, 0)
        tabs.pack_start(self.btn_fav, False, False, 0)
        header.pack_start(tabs, False, False, 0)

        self.btn_refresh = self._icon_button(
            "view-refresh-symbolic",
            "Rebuild cache: Alt+R",
            lambda button: self.start_refresh(rebuild=True),
        )
        self.btn_settings = self._icon_button(
            "preferences-system-symbolic",
            "Preferences: Alt+O",
            self.show_settings_popover,
        )
        self.btn_help = self._icon_button(
            "help-about-symbolic",
            "Keyboard shortcuts: F1",
            self.show_shortcuts_popover,
        )

        for button in (
            self.btn_refresh,
            self.btn_settings,
            self.btn_help,
        ):
            header.pack_start(button, False, False, 0)

        root.pack_start(header, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(120)
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(
            Gtk.PolicyType.NEVER,
            Gtk.PolicyType.AUTOMATIC,
        )

        self.flowbox = Gtk.FlowBox()
        self.flowbox.set_valign(Gtk.Align.START)
        self.flowbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.flowbox.set_min_children_per_line(1)
        self.flowbox.set_max_children_per_line(30)
        self.flowbox.set_homogeneous(True)
        self.flowbox.set_activate_on_single_click(True)
        self.flowbox.set_filter_func(self.filter_child)
        self.flowbox.connect("child-activated", self.on_child_activated)
        self.flowbox.connect(
            "selected-children-changed",
            self.on_selection_changed,
        )

        # Inventory is already sorted; no repeated FlowBox sort callback.
        self.scrolled.add(self.flowbox)
        self.stack.add_named(self.scrolled, "grid")
        self.stack.add_named(self._make_empty_view(), "empty")
        self.stack.add_named(self._make_loading_view(), "loading")

        root.pack_start(self.stack, True, True, 0)

        self.window.show_all()
        self.set_view_mode(self.show_only_favorites)
        self.start_refresh()

        for signum in (signal.SIGINT, signal.SIGTERM):
            source = GLib.unix_signal_add(
                GLib.PRIORITY_DEFAULT,
                signum,
                self._on_unix_signal,
            )
            self.signal_sources.append(source)

        self.window.present()

        if self.startup_messages:
            message = "\n\n".join(self.startup_messages)
            self.startup_messages.clear()
            self._post_ui(
                self.show_error,
                "Configuration Read Error",
                message,
            )

    def _icon_button(self, icon_name, tooltip, callback):
        button = Gtk.Button()
        button.set_image(
            Gtk.Image.new_from_icon_name(
                icon_name,
                Gtk.IconSize.BUTTON,
            )
        )
        button.set_tooltip_text(tooltip)
        button.get_style_context().add_class("action-btn")
        button.connect("clicked", callback)
        return button

    def _make_empty_view(self):
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
        )
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.set_border_width(24)

        icon = Gtk.Image.new_from_icon_name(
            "edit-find-symbolic",
            Gtk.IconSize.DIALOG,
        )
        icon.set_pixel_size(64)

        self.empty_title = Gtk.Label(label="No Wallpapers Found")
        self.empty_title.get_style_context().add_class("placeholder-title")

        self.empty_subtitle = Gtk.Label(
            label="Try another search or switch out of favorites."
        )
        self.empty_subtitle.set_line_wrap(True)
        self.empty_subtitle.set_justify(Gtk.Justification.CENTER)

        box.pack_start(icon, False, False, 0)
        box.pack_start(self.empty_title, False, False, 0)
        box.pack_start(self.empty_subtitle, False, False, 0)
        return box

    def _make_loading_view(self):
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
        )
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.set_border_width(24)

        self.loading_spinner = Gtk.Spinner()
        self.loading_spinner.set_size_request(56, 56)

        self.loading_title = Gtk.Label(label="Loading Wallpapers…")
        self.loading_title.get_style_context().add_class(
            "placeholder-title"
        )

        self.loading_progress = Gtk.ProgressBar()
        self.loading_progress.set_size_request(360, -1)
        self.loading_progress.set_show_text(True)

        self.loading_status = Gtk.Label(label="")
        self.loading_status.set_line_wrap(True)
        self.loading_status.set_justify(Gtk.Justification.CENTER)

        for widget in (
            self.loading_spinner,
            self.loading_title,
            self.loading_progress,
            self.loading_status,
        ):
            box.pack_start(widget, False, False, 0)

        return box

    def setup_css(self):
        css = """
        window {
            background-color: @theme_bg_color;
        }
        #header_bar {
            padding: 10px 14px;
            background-color: shade(@theme_bg_color, 0.97);
            border-bottom: 1px solid alpha(@theme_fg_color, 0.12);
        }
        entry {
            border-radius: 8px;
        }
        .action-btn {
            border-radius: 8px;
            padding: 6px 8px;
        }
        .tab-container {
            border-radius: 9px;
            padding: 3px;
            background-color: alpha(@theme_fg_color, 0.05);
        }
        .tab-btn {
            background-image: none;
            background-color: transparent;
            border: 1px solid transparent;
            border-radius: 6px;
            padding: 6px 15px;
            font-weight: bold;
        }
        .tab-btn.active-all {
            background-color: alpha(@theme_fg_color, 0.13);
            border-color: alpha(@theme_fg_color, 0.15);
        }
        .tab-btn.active-fav {
            color: #f38ba8;
            background-color: alpha(#f38ba8, 0.15);
            border-color: alpha(#f38ba8, 0.35);
        }
        stack, scrolledwindow, viewport {
            background-color: @theme_base_color;
        }
        scrolledwindow overshoot.top {
            background-image: linear-gradient(
                to bottom,
                alpha(@theme_selected_bg_color, 0.2),
                transparent
            );
        }
        scrolledwindow overshoot.bottom {
            background-image: linear-gradient(
                to top,
                alpha(@theme_selected_bg_color, 0.2),
                transparent
            );
        }
        scrolledwindow undershoot.top,
        scrolledwindow undershoot.bottom {
            background-image: none;
            background-color: transparent;
        }
        flowbox {
            padding: 12px;
            background-color: transparent;
        }
        flowboxchild {
            border-radius: 18px;
            padding: 6px;
            margin: 4px;
            border: 2px solid transparent;
            background-color: transparent;
        }
        flowboxchild:hover {
            background-color: alpha(@theme_fg_color, 0.06);
        }
        flowboxchild:selected {
            border-color: @theme_selected_bg_color;
            background-color: alpha(@theme_selected_bg_color, 0.15);
        }
        .thumbnail-placeholder {
            border-radius: 14px;
            background-color: alpha(@theme_fg_color, 0.06);
            color: alpha(@theme_fg_color, 0.45);
        }
        .wallpaper-name-overlay {
            border-radius: 6px;
            padding: 4px 6px;
            color: @theme_fg_color;
            background-color: alpha(@theme_bg_color, 0.88);
            font-size: 0.8em;
            font-weight: bold;
        }
        .heart-icon {
            color: #f38ba8;
            font-size: 1.5em;
            text-shadow: 0 1px 3px rgba(0, 0, 0, 0.7);
        }
        .placeholder-title {
            font-size: 1.35em;
            font-weight: bold;
        }
        .popover-title {
            font-size: 1.1em;
            font-weight: bold;
            color: @theme_selected_bg_color;
        }
        .dialog-title {
            font-size: 1.15em;
            font-weight: bold;
            color: #f38ba8;
        }
        .themed-error-dialog textview text {
            background-color: @theme_base_color;
            color: @theme_text_color;
        }
        """

        provider = Gtk.CssProvider()

        try:
            provider.load_from_data(css.encode("utf-8"))
            screen = Gdk.Screen.get_default()
            if screen is not None:
                Gtk.StyleContext.add_provider_for_screen(
                    screen,
                    provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )
        except GLib.Error as error:
            log_error(f"Could not load application CSS: {error}")

    # -------------------------------------------------------------------------
    # Popovers
    # -------------------------------------------------------------------------

    def _new_popover(self, relative_to, title):
        if self.popover is not None:
            self.popover.destroy()

        popover = Gtk.Popover.new(relative_to)
        popover.set_position(Gtk.PositionType.BOTTOM)
        self.popover = popover

        def destroyed(widget):
            if self.popover is widget:
                self.popover = None

        popover.connect("closed", lambda widget: widget.destroy())
        popover.connect("destroy", destroyed)

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=14,
        )
        box.set_border_width(18)

        label = Gtk.Label(label=title)
        label.set_xalign(0)
        label.get_style_context().add_class("popover-title")
        box.pack_start(label, False, False, 0)

        popover.add(box)
        return popover, box

    def show_settings_popover(self, widget):
        if self.closing:
            return

        popover, box = self._new_popover(widget, "Preferences")

        grid = Gtk.Grid()
        grid.set_column_spacing(22)
        grid.set_row_spacing(12)

        rows = (
            ("Auto-close after Full Apply", "AUTO_CLOSE"),
            ("Auto-close after Fast Apply", "FAST_APPLY_AUTO_CLOSE"),
            ("Show Wallpaper Filenames", "SHOW_FILENAMES"),
            ("Default to Favorites View", "START_IN_FAVORITES"),
            ("Auto-Sweep Cache on Startup", "AUTO_SWEEP_CACHE"),
        )

        for row, (description, key) in enumerate(rows):
            label = Gtk.Label(label=description)
            label.set_xalign(0)

            switch = Gtk.Switch()
            switch.set_active(self.settings[key])
            switch.set_halign(Gtk.Align.END)
            switch.set_valign(Gtk.Align.CENTER)

            def toggled(control, specification, setting=key):
                old_value = self.settings[setting]
                new_value = control.get_active()

                if old_value == new_value:
                    return

                self.settings[setting] = new_value

                try:
                    self._save_settings()
                except Exception as error:
                    self.settings[setting] = old_value
                    control.set_active(old_value)
                    self.show_error(
                        "Settings Save Failed",
                        describe_error(error),
                    )
                    return

                if setting == "SHOW_FILENAMES":
                    self.update_filename_visibility()

            switch.connect("notify::active", toggled)
            grid.attach(label, 0, row, 1, 1)
            grid.attach(switch, 1, row, 1, 1)

        box.pack_start(grid, False, False, 0)
        popover.show_all()
        popover.popup()

    def show_shortcuts_popover(self, widget):
        if self.closing:
            return

        popover, box = self._new_popover(widget, "Keyboard Shortcuts")

        grid = Gtk.Grid()
        grid.set_column_spacing(24)
        grid.set_row_spacing(10)

        shortcuts = (
            ("Apply and regenerate theme", "Enter / Left-click"),
            ("Fast apply", "Alt+S / Right-click"),
            ("Toggle favorite", "Alt+A / Middle-click"),
            ("Toggle favorites view", "Alt+P"),
            ("Rebuild cache", "Alt+R"),
            ("Preferences", "Alt+O"),
            ("Keyboard shortcuts", "F1"),
            ("Focus search", "Ctrl+F / /"),
            ("Quit outside search", "Esc / Q / Ctrl+C"),
        )

        for row, (description, keys) in enumerate(shortcuts):
            label = Gtk.Label(label=description)
            label.set_xalign(0)

            key_label = Gtk.Label(label=keys)
            key_label.set_xalign(1)

            attributes = Pango.AttrList()
            attributes.insert(Pango.attr_family_new("monospace"))
            key_label.set_attributes(attributes)

            grid.attach(label, 0, row, 1, 1)
            grid.attach(key_label, 1, row, 1, 1)

        box.pack_start(grid, False, False, 0)
        popover.show_all()
        popover.popup()

    # -------------------------------------------------------------------------
    # Scanning, rebuilds, and batched widget creation
    # -------------------------------------------------------------------------

    def _cancel_generation(self):
        self.generation_stop.set()

        for future in self.image_futures:
            future.cancel()

        self.image_futures.clear()
        self.image_iterator = iter(())

        if self.control_future is not None:
            self.control_future.cancel()
            self.control_future = None

        self.generation += 1
        self.generation_stop = threading.Event()

    def _update_busy_controls(self):
        if self.btn_refresh is not None:
            self.btn_refresh.set_sensitive(
                not self.is_refreshing and not self.is_applying
            )

    def start_refresh(self, *, rebuild=False):
        if self.closing or self.is_refreshing or self.is_applying:
            return

        self._cancel_generation()
        generation = self.generation
        stop_event = self.generation_stop

        self.is_refreshing = True
        self._update_busy_controls()

        self.loading_title.set_text(
            "Rebuilding Image Cache…" if rebuild
            else "Loading Wallpapers…"
        )
        self.loading_progress.set_fraction(0)
        self.loading_progress.set_text("Preparing…")
        self.loading_status.set_text("")
        self.loading_spinner.start()
        self.stack.set_visible_child_name("loading")

        def progress(current, total, generated, failed):
            self._post_ui(
                self._update_cache_progress,
                generation,
                current,
                total,
                generated,
                failed,
            )

        def work():
            check_cancelled(stop_event)

            if rebuild:
                result = CacheManager.build_cache(
                    force=True,
                    progress_callback=progress,
                    stop_event=stop_event,
                )
            else:
                if self.settings["AUTO_SWEEP_CACHE"]:
                    paths = CacheManager.scan_and_sweep(stop_event)
                else:
                    paths = scan_wallpapers(stop_event)
                result = CacheBuildResult(paths)

            favorites = load_favorites()

            # Tracker errors should not prevent browsing the collection.
            current_path = None
            tracker_warning = ""

            try:
                state = read_state_conf()
                track = (
                    TRACK_LIGHT
                    if state.get("THEME_MODE", "dark") == "light"
                    else TRACK_DARK
                )
                current_path = match_wallpaper_id(
                    result.wallpapers,
                    read_tracker(track),
                )
            except Exception as error:
                tracker_warning = describe_error(error)

            return result, favorites, current_path, tracker_warning

        self.control_future = self.control_executor.submit(work)
        self.control_future.add_done_callback(
            lambda future: self._post_ui(
                self._scan_complete,
                future,
                generation,
            )
        )

    def _update_cache_progress(
        self,
        generation,
        current,
        total,
        generated,
        failed,
    ):
        if generation != self.generation or not self.is_refreshing:
            return

        self.loading_progress.set_fraction(
            current / total if total else 1.0
        )
        self.loading_progress.set_text(f"{current} / {total}")
        self.loading_status.set_text(
            f"{generated} regenerated · {failed} unavailable"
        )

    def _scan_complete(self, future, generation):
        if generation != self.generation:
            return

        self.control_future = None

        try:
            result, favorites, current_path, warning = future.result()
        except OperationCancelled:
            return
        except Exception as error:
            self.is_refreshing = False
            self.loading_spinner.stop()
            self._update_busy_controls()

            if self.children:
                self.update_visibility_and_selection()
                self._start_image_jobs()
            else:
                self.empty_title.set_text("Could Not Load Wallpapers")
                self.empty_subtitle.set_text(
                    "Check the wallpaper directory and try rebuilding."
                )
                self.stack.set_visible_child_name("empty")

            self.show_error(
                "Wallpaper Loading Failed",
                describe_error(error),
            )
            return

        if warning:
            log_error(f"Tracker warning: {warning}")

        self.favorites = favorites
        self.wallpapers = result.wallpapers

        self.current_selected_child = None

        for child in self.flowbox.get_children():
            child.destroy()

        self.children.clear()

        self.loading_progress.set_fraction(0)
        self.loading_progress.set_text("Building image grid…")

        iterator = iter(self.wallpapers)
        created = 0
        total = len(self.wallpapers)

        def create_batch():
            nonlocal created

            if self.closing or generation != self.generation:
                return GLib.SOURCE_REMOVE

            try:
                for _ in range(80):
                    rel_path = next(iterator)
                    child = self._create_child(rel_path)
                    self.children[rel_path] = child
                    self.flowbox.add(child)
                    child.show_all()
                    created += 1
            except StopIteration:
                self._finish_grid(generation, current_path, result.failed)
                return GLib.SOURCE_REMOVE
            except Exception as error:
                self.is_refreshing = False
                self.loading_spinner.stop()
                self._update_busy_controls()
                self.update_visibility_and_selection()
                self.show_error(
                    "Grid Creation Failed",
                    describe_error(error),
                )
                return GLib.SOURCE_REMOVE

            self.loading_progress.set_fraction(
                created / total if total else 1.0
            )
            return GLib.SOURCE_CONTINUE

        GLib.idle_add(create_batch)

    def _create_child(self, rel_path):
        child = Gtk.FlowBoxChild()
        child.rel_path = rel_path
        child.name_label = None
        child.pixbuf = None
        child.image_finished = False

        # A persistent EventBox provides reliable per-tile middle/right clicks
        # without guessing FlowBox event coordinate origins.
        event_box = Gtk.EventBox()
        event_box.set_visible_window(False)
        event_box.set_size_request(RENDER_SIZE, RENDER_SIZE)
        event_box.set_tooltip_text(rel_path)
        event_box.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        event_box.connect(
            "button-press-event",
            self.on_tile_button_press,
            child,
        )

        child.event_box = event_box
        event_box.add(self._thumbnail_placeholder(failed=False))
        child.add(event_box)
        return child

    def _finish_grid(self, generation, current_path, failed):
        if generation != self.generation:
            return

        self.is_refreshing = False
        self.loading_spinner.stop()
        self._update_busy_controls()

        self.empty_title.set_text("No Wallpapers Found")
        self.empty_subtitle.set_text(
            "Try another search or switch out of favorites."
        )

        self.flowbox.invalidate_filter()

        target = self.children.get(current_path)
        if target is not None and self.filter_child(target):
            self.flowbox.select_child(target)

        self.update_visibility_and_selection()
        self._start_image_jobs()
        self._focus_selected_later(generation)

        if failed:
            log_error(
                f"Cache rebuild finished with {failed} unavailable images."
            )

    # -------------------------------------------------------------------------
    # Bounded asynchronous image loading
    # -------------------------------------------------------------------------

    def _start_image_jobs(self):
        if self.closing:
            return

        self.image_iterator = iter(self.wallpapers)
        self._pump_image_jobs()

    def _pump_image_jobs(self):
        if self.closing or self.is_refreshing:
            return

        generation = self.generation
        stop_event = self.generation_stop

        while len(self.image_futures) < MAX_IMAGE_JOBS:
            try:
                rel_path = next(self.image_iterator)
            except StopIteration:
                break

            future = self.image_executor.submit(
                self._load_pixbuf,
                rel_path,
                stop_event,
            )
            self.image_futures.add(future)

            future.add_done_callback(
                lambda finished, path=rel_path, gen=generation:
                    self._post_ui(
                        self._image_complete,
                        finished,
                        path,
                        gen,
                    )
            )

    @staticmethod
    def _load_pixbuf(rel_path, stop_event):
        check_cancelled(stop_event)

        status = CacheManager.generate_thumb(
            rel_path,
            stop_event=stop_event,
        )

        if status not in {"cached", "generated"}:
            return None

        thumb = CacheManager.get_thumb_path(rel_path)

        for attempt in range(2):
            check_cancelled(stop_event)

            try:
                return GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    str(thumb),
                    RENDER_SIZE,
                    RENDER_SIZE,
                    True,
                )
            except (GLib.Error, OSError) as error:
                if attempt:
                    log_error(
                        f"Cannot decode thumbnail for {rel_path!r}: {error}"
                    )
                    return None

                # A broken cached PNG is not proof of a broken source image.
                status = CacheManager.generate_thumb(
                    rel_path,
                    force=True,
                    stop_event=stop_event,
                )
                if status != "generated":
                    return None

        return None

    def _image_complete(self, future, rel_path, generation):
        self.image_futures.discard(future)

        if generation != self.generation:
            return

        try:
            pixbuf = future.result()
        except OperationCancelled:
            return
        except Exception as error:
            log_error(
                f"Image worker failed for {rel_path!r}: "
                f"{describe_error(error)}"
            )
            pixbuf = None

        child = self.children.get(rel_path)
        if child is not None:
            child.pixbuf = pixbuf
            child.image_finished = True
            self._render_child(child)

        self._pump_image_jobs()

    def _thumbnail_placeholder(self, *, failed):
        box = Gtk.Box()
        box.set_size_request(RENDER_SIZE, RENDER_SIZE)
        box.get_style_context().add_class("thumbnail-placeholder")

        icon = Gtk.Image.new_from_icon_name(
            "image-missing-symbolic" if failed
            else "image-x-generic-symbolic",
            Gtk.IconSize.DIALOG,
        )
        icon.set_halign(Gtk.Align.CENTER)
        icon.set_valign(Gtk.Align.CENTER)
        box.pack_start(icon, True, True, 0)
        return box

    def _render_child(self, child):
        child.name_label = None

        old_content = child.event_box.get_child()
        if old_content is not None:
            old_content.destroy()

        if child.pixbuf is None:
            placeholder = self._thumbnail_placeholder(
                failed=child.image_finished
            )
            child.event_box.add(placeholder)
            placeholder.show_all()
            return

        overlay = Gtk.Overlay()
        image = Gtk.Image.new_from_pixbuf(child.pixbuf)
        overlay.add(image)

        if child.rel_path in self.favorites:
            heart = Gtk.Label(label="♥")
            heart.get_style_context().add_class("heart-icon")
            heart.set_halign(Gtk.Align.END)
            heart.set_valign(Gtk.Align.START)
            heart.set_margin_top(6)
            heart.set_margin_end(8)
            overlay.add_overlay(heart)
            overlay.set_overlay_pass_through(heart, True)

        name_label = Gtk.Label(
            label=os.path.basename(child.rel_path)
        )
        name_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        name_label.set_max_width_chars(17)
        name_label.set_halign(Gtk.Align.END)
        name_label.set_valign(Gtk.Align.END)
        name_label.set_margin_bottom(8)
        name_label.set_margin_end(8)
        name_label.set_no_show_all(True)
        name_label.get_style_context().add_class(
            "wallpaper-name-overlay"
        )

        overlay.add_overlay(name_label)
        overlay.set_overlay_pass_through(name_label, True)

        child.name_label = name_label
        child.event_box.add(overlay)
        overlay.show_all()

        name_label.set_visible(
            child is self.current_selected_child
            and self.settings["SHOW_FILENAMES"]
        )

    # -------------------------------------------------------------------------
    # Filtering, selection, and focus
    # -------------------------------------------------------------------------

    def filter_child(self, child):
        rel_path = getattr(child, "rel_path", "")

        if self.show_only_favorites and rel_path not in self.favorites:
            return False

        return not self.search_query or (
            self.search_query in rel_path.casefold()
        )

    def set_view_mode(self, favorites):
        if self.closing:
            return

        self.show_only_favorites = bool(favorites)

        if self.btn_all is not None:
            all_context = self.btn_all.get_style_context()
            fav_context = self.btn_fav.get_style_context()

            all_context.remove_class("active-all")
            fav_context.remove_class("active-fav")

            if self.show_only_favorites:
                fav_context.add_class("active-fav")
            else:
                all_context.add_class("active-all")

        if self.flowbox is not None:
            self.flowbox.invalidate_filter()
            self.update_visibility_and_selection()

    def on_search_changed(self, entry):
        if self.closing:
            return

        self.search_query = entry.get_text().casefold()
        self.flowbox.invalidate_filter()
        self.update_visibility_and_selection()

    def update_visibility_and_selection(self):
        if self.closing or self.is_refreshing or self.flowbox is None:
            return

        selected = self.flowbox.get_selected_children()

        if selected and self.filter_child(selected[0]):
            self.stack.set_visible_child_name("grid")
            return

        first = next(
            (
                child
                for child in self.flowbox.get_children()
                if self.filter_child(child)
            ),
            None,
        )

        if first is None:
            self.flowbox.unselect_all()
            self.stack.set_visible_child_name("empty")
        else:
            self.stack.set_visible_child_name("grid")
            self.flowbox.select_child(first)

    def on_selection_changed(self, flowbox):
        previous_label = getattr(
            self.current_selected_child,
            "name_label",
            None,
        )
        if previous_label is not None:
            previous_label.hide()

        selected = flowbox.get_selected_children()
        self.current_selected_child = selected[0] if selected else None
        self.update_filename_visibility()

    def update_filename_visibility(self):
        label = getattr(
            self.current_selected_child,
            "name_label",
            None,
        )
        if label is not None:
            label.set_visible(self.settings["SHOW_FILENAMES"])

    def get_selected_path(self):
        if self.closing or self.is_refreshing:
            return None

        selected = self.flowbox.get_selected_children()

        if not selected or not self.filter_child(selected[0]):
            return None

        return selected[0].rel_path

    def _focus_selected_later(self, generation):
        attempts = 0

        def focus():
            nonlocal attempts

            if self.closing or generation != self.generation:
                return GLib.SOURCE_REMOVE

            selected = self.flowbox.get_selected_children()
            if not selected:
                return GLib.SOURCE_REMOVE

            child = selected[0]
            allocation = child.get_allocation()

            if allocation.height <= 1 and attempts < 20:
                attempts += 1
                return GLib.SOURCE_CONTINUE

            # Do not steal focus if the user has already begun searching.
            if self.search_entry.is_focus():
                return GLib.SOURCE_REMOVE

            child.grab_focus()

            translated = child.translate_coordinates(
                self.flowbox, 0, 0
            )
            if translated is not None:
                _, y = translated
                adjustment = self.scrolled.get_vadjustment()
                lower = adjustment.get_lower()
                upper = max(
                    lower,
                    adjustment.get_upper() - adjustment.get_page_size(),
                )
                adjustment.set_value(
                    max(lower, min(y - 20, upper))
                )

            return GLib.SOURCE_REMOVE

        GLib.timeout_add(16, focus)

    # -------------------------------------------------------------------------
    # Input handling
    # -------------------------------------------------------------------------

    def on_child_activated(self, flowbox, child):
        if not self.is_refreshing:
            self.apply_wallpaper(child.rel_path, regen=True)

    def on_tile_button_press(self, event_box, event, child):
        if (
            event.type != Gdk.EventType.BUTTON_PRESS
            or event.button not in (2, 3)
        ):
            return False

        if self.is_refreshing:
            return True

        self.flowbox.select_child(child)
        child.grab_focus()

        if event.button == 2:
            self.toggle_favorite(child.rel_path)
        else:
            self.apply_wallpaper(child.rel_path, regen=False)

        return True

    def _focus_is_in_grid(self, focus):
        widget = focus

        while widget is not None:
            if widget is self.flowbox:
                return True
            widget = widget.get_parent()

        return False

    def on_key_press(self, window, event):
        if self.closing:
            return False

        # Let the active popover handle its own Escape, switches, and focus.
        if self.popover is not None:
            return False

        key = event.keyval
        state = event.state
        alt = bool(state & Gdk.ModifierType.MOD1_MASK)
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        super_key = bool(state & Gdk.ModifierType.SUPER_MASK)
        focus = window.get_focus()
        editing = isinstance(focus, Gtk.Entry)

        if super_key:
            return False

        if key == Gdk.KEY_F1:
            self.show_shortcuts_popover(self.btn_help)
            return True

        if ctrl and not alt and key in (Gdk.KEY_f, Gdk.KEY_F):
            self.search_entry.grab_focus()
            self.search_entry.select_region(0, -1)
            return True

        if alt and not ctrl:
            if key in (Gdk.KEY_o, Gdk.KEY_O):
                self.show_settings_popover(self.btn_settings)
                return True

            if key in (Gdk.KEY_p, Gdk.KEY_P):
                self.set_view_mode(not self.show_only_favorites)
                return True

            if key in (Gdk.KEY_r, Gdk.KEY_R):
                self.start_refresh(rebuild=True)
                return True

            if key in (Gdk.KEY_s, Gdk.KEY_S):
                rel_path = self.get_selected_path()
                if rel_path:
                    self.apply_wallpaper(rel_path, regen=False)
                return True

            if key in (Gdk.KEY_a, Gdk.KEY_A):
                rel_path = self.get_selected_path()
                if rel_path:
                    self.toggle_favorite(rel_path)
                return True

        if key == Gdk.KEY_Escape:
            if editing:
                if self.search_entry.get_text():
                    self.search_entry.set_text("")
                else:
                    selected = self.flowbox.get_selected_children()
                    if selected:
                        selected[0].grab_focus()
                    else:
                        self.flowbox.grab_focus()
            else:
                self.window.close()
            return True

        if editing:
            if (
                not alt
                and not ctrl
                and key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)
            ):
                selected = self.flowbox.get_selected_children()
                if selected:
                    selected[0].grab_focus()
                return True

            # Preserve normal editing, including Ctrl+C.
            return False

        if ctrl and not alt and key in (Gdk.KEY_c, Gdk.KEY_C):
            self.window.close()
            return True

        if not ctrl and not alt:
            if key in (Gdk.KEY_q, Gdk.KEY_Q):
                self.window.close()
                return True

            if key == Gdk.KEY_slash:
                self.search_entry.grab_focus()
                return True

            if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
                # Toolbar buttons retain their normal Enter behavior.
                if self._focus_is_in_grid(focus):
                    rel_path = self.get_selected_path()
                    if rel_path:
                        self.apply_wallpaper(rel_path, regen=True)
                    return True

        return False

    # -------------------------------------------------------------------------
    # Favorites and backend application
    # -------------------------------------------------------------------------

    def toggle_favorite(self, rel_path):
        if self.closing or self.is_refreshing:
            return

        try:
            favorites = toggle_saved_favorite(rel_path)
        except Exception as error:
            self.show_error(
                "Favorites Save Failed",
                describe_error(error),
            )
            return

        changed = self.favorites ^ favorites
        self.favorites = favorites

        for path in changed:
            child = self.children.get(path)
            if child is not None and child.image_finished:
                self._render_child(child)

        self.flowbox.invalidate_filter()
        self.update_visibility_and_selection()

    def apply_wallpaper(self, rel_path, *, regen):
        if (
            not rel_path
            or self.closing
            or self.is_refreshing
            or self.is_applying
        ):
            return

        self.is_applying = True
        self._update_busy_controls()

        close_setting = (
            "AUTO_CLOSE" if regen
            else "FAST_APPLY_AUTO_CLOSE"
        )
        should_close = self.settings[close_setting]

        if should_close:
            self.window.hide()

        self.app.hold()

        def work():
            success = False
            message = ""

            try:
                perform_wallpaper_apply(
                    rel_path,
                    regen=regen,
                    stop_event=self.backend_stop,
                )
                success = True
            except Exception as error:
                message = describe_error(error)
            finally:
                # Must run even after the window closes: it releases app.hold().
                GLib.idle_add(
                    self._backend_complete,
                    success,
                    message,
                    should_close,
                )

        try:
            threading.Thread(
                target=work,
                name="wallpaper-apply",
                daemon=True,
            ).start()
        except Exception as error:
            self._backend_complete(
                False,
                describe_error(error),
                should_close,
            )

    def _backend_complete(self, success, message, should_close):
        self.is_applying = False

        try:
            if not self.closing:
                self._update_busy_controls()

            if not success:
                self.show_error(
                    "Wallpaper Application Failed",
                    message,
                )
            elif should_close and self.window is not None:
                self.window.close()

        finally:
            self.app.release()

        return GLib.SOURCE_REMOVE

    # -------------------------------------------------------------------------
    # Shutdown
    # -------------------------------------------------------------------------

    def _on_unix_signal(self):
        # Signals request cancellation; normal window-close lets an already
        # running wallpaper/theme application finish.
        self.backend_stop.set()

        if self.window is not None:
            self.window.close()

        return GLib.SOURCE_CONTINUE

    def on_window_destroy(self, window):
        self.closing = True
        self.generation_stop.set()

        for future in self.image_futures:
            future.cancel()
        self.image_futures.clear()

        if self.control_future is not None:
            self.control_future.cancel()

        self.image_iterator = iter(())
        self.current_selected_child = None
        self.children.clear()
        self.window = None

    def on_shutdown(self, application):
        self.shutting_down = True
        self.closing = True
        self.generation_stop.set()
        self.backend_stop.set()

        self.image_executor.shutdown(
            wait=False,
            cancel_futures=True,
        )
        self.control_executor.shutdown(
            wait=False,
            cancel_futures=True,
        )

        for source in self.signal_sources:
            GLib.source_remove(source)
        self.signal_sources.clear()

    def run(self):
        try:
            return self.app.run([sys.argv[0]])
        finally:
            # Also covers failure before Gtk.Application reaches shutdown.
            self.generation_stop.set()
            self.backend_stop.set()

            self.image_executor.shutdown(
                wait=False,
                cancel_futures=True,
            )
            self.control_executor.shutdown(
                wait=False,
                cancel_futures=True,
            )


# =============================================================================
# ENTRY POINT
# =============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dusky Theme GTK3 Wallpaper Selector",
    )

    group = parser.add_mutually_exclusive_group()

    group.add_argument(
        "--build-cache",
        action="store_true",
        help=(
            "Generate missing/outdated thumbnails and sweep orphaned cache "
            "files, then exit."
        ),
    )
    group.add_argument(
        "--rebuild-cache",
        action="store_true",
        help=(
            "Force-regenerate thumbnails atomically and sweep orphaned "
            "cache files, then exit."
        ),
    )
    group.add_argument(
        "--next-fav",
        action="store_true",
        help="Apply the next existing favorite and regenerate its theme.",
    )
    group.add_argument(
        "--prev-fav",
        action="store_true",
        help="Apply the previous existing favorite and regenerate its theme.",
    )
    group.add_argument(
        "--precache",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    args = parser.parse_args()

    if TRACKER_ID_FORMAT not in {"basename", "relative"}:
        parser.error(
            "TRACKER_ID_FORMAT must be 'basename' or 'relative'."
        )

    headless = any((
        args.build_cache,
        args.rebuild_cache,
        args.precache,
        args.next_fav,
        args.prev_fav,
    ))

    if not headless:
        return WallpaperApp().run()

    stop_event = threading.Event()
    received_signal = [None]

    def request_stop(signum, frame):
        received_signal[0] = signum
        stop_event.set()

    old_handlers = {
        signum: signal.signal(signum, request_stop)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }

    try:
        if args.build_cache or args.rebuild_cache or args.precache:
            result = CacheManager.build_cache(
                force=args.rebuild_cache,
                stop_event=stop_event,
            )
            check_cancelled(stop_event)
            return 1 if result.failed else 0

        direction = "next" if args.next_fav else "prev"
        status = cycle_favorites(direction, stop_event)
        check_cancelled(stop_event)
        return status

    except OperationCancelled:
        signum = received_signal[0]
        return 128 + signum if signum is not None else 130

    finally:
        stop_event.set()
        for signum, old_handler in old_handlers.items():
            signal.signal(signum, old_handler)


if __name__ == "__main__":
    try:
        exit_status = main()
    except KeyboardInterrupt:
        exit_status = 130
    except Exception as error:
        log_error(f"Error:\n{describe_error(error)}")
        exit_status = 1

    sys.exit(exit_status)
