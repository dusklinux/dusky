#!/usr/bin/env python3
# =============================================================================
# ARCH LINUX :: DUSKY THEME :: GTK3 WALLPAPER SELECTOR
#
# Target: Python 3.14+, Linux, GTK3 / PyGObject
#
# Features:
#   - Asynchronous directory scanning and virtual-scrolling GTK3 canvas grid
#   - Opens directly at the tracked current wallpaper without creating off-screen widgets
#   - Collection-wide search and favorites filtering
#   - Bounded viewport-prioritized thumbnail loading
#   - Off-screen widget and decoded-image eviction without deleting disk thumbnails
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
# The virtual grid uses Gtk.Layout: only nearby rows and the selected tile
# instantiate widgets, bounding collection-related GTK construction work.
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
import tempfile
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

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Pango


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
# Fixed geometry makes scroll positions calculable without constructing
# all preceding tiles.
GRID_TILE_SIZE = RENDER_SIZE + 16
GRID_GAP = 12
GRID_PADDING = 12
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

# Limits are PER ImageMagick process, not shared across all workers.
THUMB_TIMEOUT = 60.0
MAGICK_MEMORY_LIMIT = "128MiB"
MAGICK_MAP_LIMIT = "256MiB"
MAGICK_DISK_LIMIT = "4GiB"
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
    Run a command with a timeout and cancellation checks.

    On timeout/cancellation, terminate its process group and perform bounded
    output cleanup and child-reaping attempts.

    Descendants that create another process group/session are not guaranteed
    to be terminated. Cleanup does not wait indefinitely for such descendants
    to close inherited stdout/stderr pipes.
    """
    check_cancelled(stop_event)

    if timeout <= 0:
        raise ValueError("Command timeout must be greater than zero.")

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
    last_stdout = None
    last_stderr = None

    try:
        while True:
            check_cancelled(stop_event)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(
                    command,
                    timeout,
                    output=last_stdout,
                    stderr=last_stderr,
                )

            try:
                stdout, stderr = process.communicate(
                    timeout=min(0.2, remaining)
                )
                break
            except subprocess.TimeoutExpired as error:
                # communicate() retains accumulated output across retries.
                last_stdout = error.output
                last_stderr = error.stderr

    except BaseException as original_error:
        try:
            kill_process_group(process)
        except OSError as cleanup_error:
            log_error(
                "Could not terminate the command's process group: "
                f"{describe_error(cleanup_error)}"
            )

            # Still attempt to terminate the direct child.
            try:
                process.kill()
            except ProcessLookupError:
                pass
            except OSError as child_error:
                log_error(
                    "Could not terminate the direct child: "
                    f"{describe_error(child_error)}"
                )

        cleanup_stdout = None
        cleanup_stderr = None

        try:
            cleanup_stdout, cleanup_stderr = process.communicate(
                timeout=2.0
            )
        except subprocess.TimeoutExpired as cleanup_error:
            # A descendant outside the killed process group may still
            # own an inherited pipe writer.
            cleanup_stdout = cleanup_error.output
            cleanup_stderr = cleanup_error.stderr
        except Exception as cleanup_error:
            log_error(
                "Could not finish reading command output during cleanup: "
                f"{describe_error(cleanup_error)}"
            )
        finally:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError as cleanup_error:
                        log_error(
                            "Could not close a command output pipe: "
                            f"{describe_error(cleanup_error)}"
                        )

        if isinstance(original_error, subprocess.TimeoutExpired):
            if cleanup_stdout is not None:
                original_error.output = cleanup_stdout
            if cleanup_stderr is not None:
                original_error.stderr = cleanup_stderr

        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            log_error(
                f"Child PID {process.pid} did not exit promptly after "
                "termination; reaping will continue in the background."
            )

            # Reap only this child. A daemon waiter must not prevent the
            # selector itself from exiting if the child remains stuck.
            try:
                threading.Thread(
                    target=process.wait,
                    name=f"command-reaper-{process.pid}",
                    daemon=True,
                ).start()
            except Exception as cleanup_error:
                log_error(
                    "Could not start the child-reaping thread: "
                    f"{describe_error(cleanup_error)}"
                )
        except Exception as cleanup_error:
            log_error(
                "Could not reap the command during cleanup: "
                f"{describe_error(cleanup_error)}"
            )

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
    if isinstance(
        error,
        (subprocess.TimeoutExpired, subprocess.CalledProcessError),
    ):
        command = error.cmd
        executable = (
            command[0]
            if isinstance(command, (list, tuple)) and command
            else command
        )

        details = error.stderr or error.output or ""

        if isinstance(details, bytes):
            details = details.decode("utf-8", errors="replace")

        details = details.strip()

        if isinstance(error, subprocess.TimeoutExpired):
            message = (
                f"{executable} timed out after "
                f"{error.timeout:g} seconds."
            )
        else:
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
            if directory == WALLPAPER_DIR:
                raise OSError(
                    f"Cannot inspect wallpaper directory {directory}: {error}"
                ) from error
            log_error(f"Cannot inspect wallpaper directory {directory}: {error}")
            continue

        identity = (info.st_dev, info.st_ino)
        if identity in visited:
            continue
        visited.add(identity)

        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError as error:
            if directory == WALLPAPER_DIR:
                raise OSError(
                    f"Cannot read wallpaper directory {directory}: {error}"
                ) from error
            log_error(f"Cannot read wallpaper directory {directory}: {error}")
            continue

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
                log_error(f"Skipping inaccessible wallpaper entry {path}: {error}")
                continue

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
            info.st_size,
            info.st_mtime_ns,
        ]

    @staticmethod
    def source_matches(cached_source: object, current_stat: os.stat_result) -> bool:
        """
        Validate whether the source image matches the cached source metadata.
        Supports both current [st_size, st_mtime_ns] format and legacy
        [st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns] format.

        Ignores st_dev, st_ino, and st_ctime_ns to prevent false cache
        rebuilding across reboots, dynamic filesystem mounts (btrfs subvolumes,
        LUKS/dm-crypt, zram, removable media), and system updates where ctime
        changes upon metadata touch.
        """
        if not isinstance(cached_source, (list, tuple)):
            return False
        if len(cached_source) == 2:
            return (
                cached_source[0] == current_stat.st_size
                and cached_source[1] == current_stat.st_mtime_ns
            )
        if len(cached_source) >= 4:
            return (
                cached_source[2] == current_stat.st_size
                and cached_source[3] == current_stat.st_mtime_ns
            )
        return False

    @staticmethod
    def thumbnail_matches(cached_thumb: object, thumb_info: os.stat_result) -> bool:
        """
        Check that the thumbnail on disk is valid and non-empty.
        If recorded thumbnail size is present, verifies size matches.
        Ignores st_dev, st_ino, and st_ctime_ns of the thumbnail file.
        """
        if thumb_info.st_size == 0:
            return False
        if cached_thumb is None:
            return True
        if isinstance(cached_thumb, int):
            return thumb_info.st_size == cached_thumb
        if isinstance(cached_thumb, (list, tuple)):
            if len(cached_thumb) == 2:
                return thumb_info.st_size == cached_thumb[0]
            if len(cached_thumb) >= 3:
                return thumb_info.st_size == cached_thumb[2]
        return True

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
            source_info = os.fstat(source.fileno())
            signature = CacheManager.signature(source_info)
            metadata = CacheManager.read_metadata(metadata_path)

            if not force:
                if metadata:
                    if CacheManager.source_matches(metadata.get("source"), source_info):
                        if metadata.get("status") == "ok":
                            try:
                                thumb_info = thumb_path.stat()
                            except OSError:
                                pass
                            else:
                                if CacheManager.thumbnail_matches(
                                    metadata.get("thumbnail"),
                                    thumb_info,
                                ):
                                    if "path" not in metadata:
                                        atomic_write(
                                            metadata_path,
                                            json.dumps({
                                                "path": rel_path,
                                                "source": signature,
                                                "status": "ok",
                                                "thumbnail": thumb_info.st_size,
                                            }) + "\n",
                                        )
                                    return "cached"

                        if metadata.get("status") == "bad":
                            retry_at = metadata.get("retry_at")

                            if (
                                isinstance(retry_at, (int, float))
                                and time.time() < retry_at
                            ):
                                reason = metadata.get("error")

                                if not isinstance(reason, str) or not reason:
                                    reason = (
                                        "A previous conversion failed; "
                                        "no detailed error was recorded."
                                    )

                                log_error(
                                    f"Thumbnail temporarily unavailable for "
                                    f"{rel_path!r}; automatic retry is deferred:\n"
                                    f"{reason}"
                                )
                                return "failed"
                else:
                    # Fallback for existing valid thumbnails without JSON metadata
                    try:
                        thumb_info = thumb_path.stat()
                    except OSError:
                        pass
                    else:
                        if (
                            thumb_info.st_size > 0
                            and thumb_info.st_mtime >= source_info.st_mtime
                        ):
                            atomic_write(
                                metadata_path,
                                json.dumps({
                                    "path": rel_path,
                                    "source": signature,
                                    "status": "ok",
                                    "thumbnail": thumb_info.st_size,
                                }) + "\n",
                            )
                            return "cached"

            def source_unchanged() -> bool:
                try:
                    return (
                        CacheManager.source_matches(
                            signature,
                            os.fstat(source.fileno()),
                        )
                        and CacheManager.source_matches(
                            signature,
                            source_path.stat(),
                        )
                    )
                except OSError:
                    return False

            def record_conversion_failure(reason: str) -> None:
                if not source_unchanged():
                    return

                atomic_write(
                    metadata_path,
                    json.dumps({
                        "path": rel_path,
                        "source": signature,
                        "status": "bad",
                        "error": reason[:8000],
                        "retry_at": (
                            time.time() + BAD_THUMB_RETRY_SECONDS
                        ),
                    }) + "\n",
                )

            if source_info.st_size == 0:
                reason = "The source image is empty (0 bytes)."

                log_error(
                    f"Thumbnail failed for {rel_path!r}: {reason}"
                )
                record_conversion_failure(reason)
                return "failed"

            # Missing dependencies are not negatively cached.
            magick = require_binary(MAGICK_COMMAND)
            nice = require_binary("nice")

            try:
                # Use an explicitly located, per-conversion scratch directory.
                # It is outside THUMB_DIR, so thumbnail sweeping never touches
                # an active conversion's ImageMagick pixel-cache files.
                #
                # The context removes scratch files even when ImageMagick is
                # terminated by run_command() after a timeout/cancellation.
                with tempfile.TemporaryDirectory(
                    prefix="magick-",
                    dir=CACHE_DIR,
                ) as scratch_dir:
                    command = [
                        nice, "-n", "19",
                        magick,
                        "-limit", "thread", "1",
                        "-limit", "memory", MAGICK_MEMORY_LIMIT,
                        "-limit", "map", MAGICK_MAP_LIMIT,
                        "-limit", "disk", MAGICK_DISK_LIMIT,
                        "-limit", "time",
                        str(max(1, int(THUMB_TIMEOUT) - 2)),
                        "-define",
                        f"registry:temporary-path={scratch_dir}",
                        # An opened descriptor avoids interpretation of
                        # special characters in the original filename.
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
                    raise OSError(
                        "ImageMagick produced an empty thumbnail."
                    )

                os.replace(temporary, thumb_path)

                atomic_write(
                    metadata_path,
                    json.dumps({
                        "path": rel_path,
                        "source": signature,
                        "status": "ok",
                        "thumbnail": thumb_path.stat().st_size,
                    }) + "\n",
                )
                return "generated"

            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
            ) as error:
                reason = describe_error(error)

                log_error(
                    f"ImageMagick could not convert {rel_path!r}:\n"
                    f"{reason}"
                )

                record_conversion_failure(reason)
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
        if not wallpapers:
            return 0

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

                if temporary:
                    try:
                        os.unlink(entry.path)
                        removed += 1
                    except FileNotFoundError:
                        pass
                elif recognized and digest not in valid:
                    # Protect thumbnails of temporarily unmounted directories:
                    meta_path = THUMB_DIR / f"{digest}.json"
                    meta = CacheManager.read_metadata(meta_path)
                    cached_rel = meta.get("path")
                    if cached_rel:
                        try:
                            source_path = WALLPAPER_DIR / validate_relative_id(cached_rel)
                            if not source_path.parent.exists():
                                continue
                        except Exception:
                            pass

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
        with file_lock(CACHE_LOCK_FILE, stop_event=stop_event):
            THUMB_DIR.mkdir(parents=True, exist_ok=True)

            print(f"Scanning: {WALLPAPER_DIR}", flush=True)
            wallpapers = scan_wallpapers(stop_event)

            removed = CacheManager._sweep_locked(
                wallpapers,
                stop_event,
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
            last_reported = None
            remaining = iter(wallpapers)

            terminal_progress = (
                progress_callback is None and sys.stdout.isatty()
            )

            progress_interval = (
                0.05 if progress_callback is not None
                else 0.2 if terminal_progress
                else 1.0
            )

            def generate(path: str) -> str:
                return CacheManager.generate_thumb(
                    path,
                    force=force,
                    stop_event=stop_event,
                    cache_locked=True,
                )

            def report_progress(force_report: bool = False) -> None:
                nonlocal last_progress, last_reported

                snapshot = (
                    completed,
                    total,
                    result.generated,
                    result.failed,
                )

                # Never emit an identical progress snapshot twice.
                if snapshot == last_reported:
                    return

                now = time.monotonic()

                if (
                    not force_report
                    and now - last_progress < progress_interval
                ):
                    return

                if progress_callback is not None:
                    progress_callback(*snapshot)
                else:
                    message = (
                        f"Progress: {completed}/{total} | "
                        f"Generated: {result.generated} | "
                        f"Failed: {result.failed}"
                    )

                    if terminal_progress:
                        print(
                            "\r" + message,
                            end="",
                            flush=True,
                        )
                    else:
                        print(message, flush=True)

                last_progress = now
                last_reported = snapshot

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

            if terminal_progress:
                print(flush=True)

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

    # The file format is LF-delimited. str.splitlines() would also split
    # some otherwise valid filename characters, breaking round trips.
    for value in read_optional_text(FAVORITES_FILE).split("\n"):
        if not value:
            continue

        try:
            favorites.add(validate_relative_id(value))
        except (ValueError, UnicodeError) as error:
            log_error(
                f"Ignoring invalid favorite {value!r}: {error}"
            )

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
    theme refresh because the theme controller may read those trackers.

    Wallpaper application, tracker persistence, and theme refresh are not
    one transaction. A failure or cancellation can occur after an earlier
    stage has already completed.
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
        raise FileNotFoundError(
            f"Wallpaper not found: {full_path}"
        )

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

    print(
        f"Applying: {full_path} (full apply: {regen})",
        flush=True,
    )

    run_command(
        command,
        timeout=AWWW_APPLY_TIMEOUT,
        stop_event=stop_event,
    )

    track_file = TRACK_LIGHT if mode == "light" else TRACK_DARK

    try:
        # Once application has reported success, persist its trackers
        # without inserting another cancellation point between these writes.
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
        except OperationCancelled:
            raise
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


class VirtualWallpaperGrid(Gtk.Layout):
    """
    GTK3 virtual tile grid.

    The full collection is represented by strings. Only nearby rows and
    the selected tile have GTK widgets. Gtk.Layout supplies the full
    scrollable extent without requiring widgets for off-screen entries.

    All methods run on the GTK main thread.
    """

    __gsignals__ = {
        "selected-children-changed": (
            GObject.SignalFlags.RUN_LAST,
            None,
            (),
        ),
    }

    def __init__(self, create_tile, tiles_changed):
        super().__init__()

        self.set_name("wallpaper_grid")
        self.set_can_focus(True)
        self.set_hexpand(True)
        self.set_vexpand(True)

        self.create_tile = create_tile
        self.tiles_changed = tiles_changed

        self.paths = []
        self.positions = {}
        self.tiles = {}

        self.selected_index = None
        self.columns = 1

        self._source = 0
        self._dead = False
        self._pending_reveal = False
        self._pending_focus = False
        self._layout_width = 0

        # Gtk.ScrolledWindow may replace the Gtk.Scrollable adjustment.
        # Keep the handlers attached to the CURRENT adjustment.
        self._watched_vadjustment = None
        self._vadjustment_handlers = []

        self.connect("size-allocate", self._on_size_allocate)
        self.connect("map", self._on_grid_map)
        self.connect("destroy", self._on_destroy)
        self.connect("key-press-event", self._on_grid_key)

        self.connect(
            "notify::vadjustment",
            self._on_vadjustment_replaced,
        )

        self._bind_vadjustment()

    def _disconnect_vadjustment(self):
        adjustment = self._watched_vadjustment

        if adjustment is not None:
            for handler_id in self._vadjustment_handlers:
                adjustment.disconnect(handler_id)

        self._vadjustment_handlers.clear()
        self._watched_vadjustment = None

    def _bind_vadjustment(self):
        if self._dead:
            return

        adjustment = self.get_vadjustment()

        if adjustment is self._watched_vadjustment:
            return

        self._disconnect_vadjustment()
        self._watched_vadjustment = adjustment

        if adjustment is not None:
            self._vadjustment_handlers = [
                adjustment.connect(
                    "value-changed",
                    self._on_viewport_changed,
                ),
                adjustment.connect(
                    "changed",
                    self._on_viewport_changed,
                ),
            ]

        self._schedule()

    def _on_vadjustment_replaced(self, widget, specification):
        self._bind_vadjustment()

    def _on_viewport_changed(self, adjustment):
        self._schedule()

    def _on_grid_map(self, widget):
        # Also check when remapped after loading, filtering, or hiding.
        self._bind_vadjustment()
        self._schedule()

    @property
    def row_height(self):
        return GRID_TILE_SIZE + GRID_GAP

    def selected_path(self):
        index = self.selected_index

        if index is None or not 0 <= index < len(self.paths):
            return None

        return self.paths[index]

    def get_selected_children(self):
        path = self.selected_path()
        tile = self.tiles.get(path)
        return [tile] if tile is not None else []

    def unselect_all(self):
        previous = self.tiles.get(self.selected_path())

        if previous is not None:
            previous.unset_state_flags(Gtk.StateFlags.SELECTED)

        self.selected_index = None
        self.emit("selected-children-changed")

    def select_child(self, child):
        self.select_path(getattr(child, "rel_path", None))

    def select_path(self, path, *, reveal=False, focus=False):
        if path is None:
            return

        index = self.positions.get(path)
        if index is None:
            return

        selection_changed = index != self.selected_index

        if selection_changed:
            previous = self.tiles.get(self.selected_path())
            if previous is not None:
                previous.unset_state_flags(Gtk.StateFlags.SELECTED)

            self.selected_index = index

        current = self.tiles.get(path)
        if current is not None:
            current.set_state_flags(Gtk.StateFlags.SELECTED, False)

        if reveal:
            self._pending_reveal = True

        if focus:
            self._pending_focus = True

        if selection_changed:
            self.emit("selected-children-changed")

        self._schedule()

    def set_paths(self, paths, *, target_path=None, focus=False):
        self.unselect_all()

        for tile in list(self.tiles.values()):
            tile.destroy()
        self.tiles.clear()

        self.paths = list(paths)
        self.positions = {
            path: index for index, path in enumerate(self.paths)
        }

        if self.paths:
            self.selected_index = self.positions.get(target_path, 0)
        else:
            self.selected_index = None

        self._pending_reveal = bool(self.paths)
        self._pending_focus = bool(focus and self.paths)

        self._schedule()

    def reveal_selected(self, *, focus=False):
        if self.selected_index is None:
            return

        self._pending_reveal = True
        self._pending_focus = bool(focus)
        self._schedule()

    def _on_size_allocate(self, widget, allocation):
        self._bind_vadjustment()
        self._schedule()

    def _schedule(self):
        if self._dead or self._source:
            return

        def dispatch():
            self._source = 0

            if not self._dead:
                self._sync_tiles()

            return GLib.SOURCE_REMOVE

        # Coalesce scroll and layout signals. No inventory-wide GTK walk.
        self._source = GLib.timeout_add(16, dispatch)

    def _sync_tiles(self):
        if self._dead or not self.get_mapped():
            return

        self._bind_vadjustment()

        width = self.get_allocated_width()
        adjustment = self.get_vadjustment()
        page_size = adjustment.get_page_size()

        if width <= 1 or page_size <= 1:
            return

        old_columns = self.columns
        old_top = adjustment.get_value()
        old_top_row = max(
            0,
            int((old_top - GRID_PADDING) // self.row_height),
        )
        anchor_index = old_top_row * old_columns
        within_row = old_top - (
            GRID_PADDING + old_top_row * self.row_height
        )

        usable_width = max(
            GRID_TILE_SIZE,
            width - GRID_PADDING * 2,
        )
        columns = max(
            1,
            int((usable_width + GRID_GAP) // self.row_height),
        )
        self.columns = columns

        count = len(self.paths)
        rows = (count + columns - 1) // columns

        content_height = (
            GRID_PADDING * 2
            + rows * self.row_height
            - (GRID_GAP if rows else 0)
        )
        content_height = max(
            int(page_size),
            content_height,
            1,
        )

        # Change the virtual extent only when its dimensions change.
        # Ordinary scrolling should not request another size update.
        old_width, old_height = self.get_size()

        if old_width != width or old_height != content_height:
            self.set_size(width, content_height)

        # Ensure the current adjustment permits the startup reveal.
        # Gtk.Layout and Gtk.ScrolledWindow share this adjustment.
        if adjustment.get_upper() != float(content_height):
            adjustment.set_upper(float(content_height))

        maximum = max(
            adjustment.get_lower(),
            adjustment.get_upper() - page_size,
        )

        if self._pending_reveal and self.selected_index is not None:
            row = self.selected_index // columns
            y = GRID_PADDING + row * self.row_height

            # Center the selected row when possible.
            value = y - (page_size - GRID_TILE_SIZE) / 2
            adjustment.set_value(
                max(adjustment.get_lower(), min(value, maximum))
            )
            self._pending_reveal = False

        elif self._layout_width and columns != old_columns:
            # Preserve the approximate top item across window resizing.
            row = anchor_index // columns
            value = (
                GRID_PADDING
                + row * self.row_height
                + within_row
            )
            adjustment.set_value(
                max(adjustment.get_lower(), min(value, maximum))
            )

        self._layout_width = width

        top = adjustment.get_value()
        bottom = top + page_size

        # One additional viewport above and below the visible viewport.
        first_row = max(
            0,
            int((top - page_size - GRID_PADDING) // self.row_height),
        )
        last_row = min(
            rows,
            int(
                (bottom + page_size - GRID_PADDING)
                // self.row_height
            ) + 1,
        )

        wanted_indices = set(
            range(
                first_row * columns,
                min(count, last_row * columns),
            )
        )

        # Retain the selected tile so keyboard focus/selection does not
        # disappear merely because the user scrolls away from it.
        if self.selected_index is not None:
            wanted_indices.add(self.selected_index)

        wanted_paths = {
            self.paths[index] for index in wanted_indices
        }

        for path in list(self.tiles):
            if path not in wanted_paths:
                tile = self.tiles.pop(path)

                # Gtk.Image also releases its pixbuf during destruction.
                tile.pixbuf = None
                tile.destroy()

        slot_width = usable_width / columns
        selection_created = False

        for index in sorted(wanted_indices):
            path = self.paths[index]
            tile = self.tiles.get(path)

            x = round(
                GRID_PADDING
                + (index % columns) * slot_width
                + (slot_width - GRID_TILE_SIZE) / 2
            )
            y = GRID_PADDING + (index // columns) * self.row_height

            if tile is None:
                tile = self.create_tile(path)
                self.tiles[path] = tile
                self.put(tile, x, y)
                tile.show_all()

                if index == self.selected_index:
                    tile.set_state_flags(
                        Gtk.StateFlags.SELECTED,
                        False,
                    )
                    selection_created = True
            else:
                allocation = tile.get_allocation()

                if allocation.x != x or allocation.y != y:
                    self.move(tile, x, y)

        if selection_created:
            self.emit("selected-children-changed")

        if self._pending_focus:
            selected = self.get_selected_children()

            if selected:
                selected[0].grab_focus()
                self._pending_focus = False

        # The viewport can change without changing the resident tile set.
        # Reprioritize unfinished images for the current viewport anyway.
        #
        # WallpaperApp coalesces these notifications into one bounded
        # image-pump callback.
        self.tiles_changed()

    def _on_grid_key(self, widget, event):
        if event.state & (
            Gdk.ModifierType.CONTROL_MASK
            | Gdk.ModifierType.MOD1_MASK
            | Gdk.ModifierType.SUPER_MASK
        ):
            return False

        if not self.paths:
            return False

        index = self.selected_index
        if index is None:
            index = 0

        key = event.keyval
        page_rows = max(
            1,
            int(
                self.get_vadjustment().get_page_size()
                // self.row_height
            ),
        )

        if key == Gdk.KEY_Left:
            new_index = index - 1
        elif key == Gdk.KEY_Right:
            new_index = index + 1
        elif key == Gdk.KEY_Up:
            new_index = index - self.columns
        elif key == Gdk.KEY_Down:
            new_index = index + self.columns
        elif key == Gdk.KEY_Page_Up:
            new_index = index - page_rows * self.columns
        elif key == Gdk.KEY_Page_Down:
            new_index = index + page_rows * self.columns
        elif key == Gdk.KEY_Home:
            new_index = 0
        elif key == Gdk.KEY_End:
            new_index = len(self.paths) - 1
        else:
            return False

        new_index = max(0, min(new_index, len(self.paths) - 1))

        previous = self.tiles.get(self.selected_path())
        if previous is not None:
            previous.unset_state_flags(Gtk.StateFlags.SELECTED)

        self.selected_index = new_index

        selected = self.tiles.get(self.selected_path())
        if selected is not None:
            selected.set_state_flags(Gtk.StateFlags.SELECTED, False)

        self.emit("selected-children-changed")
        self.reveal_selected(focus=True)
        return True

    def _on_destroy(self, widget):
        self._dead = True

        if self._source:
            GLib.source_remove(self._source)
            self._source = 0

        self._disconnect_vadjustment()


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
        self.children: dict[str, Gtk.Widget] = {}
        self.current_selected_child = None
        self.applied_path: str | None = None

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
        self.image_paths: set[str] = set()
        self.image_pump_source = 0

        self.grid_building = False
        self.initial_grid = True

        # GTK can focus the search entry automatically. Only an explicit
        # user request should prevent startup from focusing the wallpaper.
        self.search_requested = False

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
        self.search_entry.connect(
            "search-changed",
            self.on_search_changed,
        )
        self.search_entry.connect(
            "button-press-event",
            self._on_search_button_press,
        )
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

        # Retain the existing attribute name to minimize changes to the
        # surrounding application. This is now a virtual Gtk.Layout,
        # not a Gtk.FlowBox.
        self.flowbox = VirtualWallpaperGrid(
            self._create_child,
            self._start_image_jobs,
        )
        self.children = self.flowbox.tiles

        self.flowbox.connect(
            "selected-children-changed",
            self.on_selection_changed,
        )

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
        #wallpaper_grid {
            padding: 0;
            background-color: transparent;
        }
        .wallpaper-tile {
            border-radius: 18px;
            padding: 6px;
            margin: 0;
            border: 2px solid transparent;
            background-color: transparent;
        }
        .wallpaper-tile:hover {
            background-color: alpha(@theme_fg_color, 0.06);
        }
        .wallpaper-tile:selected {
            border-color: @theme_selected_bg_color;
            background-color: alpha(@theme_selected_bg_color, 0.15);
        }
        .wallpaper-tile.applied-wallpaper {
            border-color: @theme_selected_bg_color;
            background-color: alpha(@theme_selected_bg_color, 0.18);
            box-shadow: 0 0 12px 3px alpha(@theme_selected_bg_color, 0.7);
        }
        .wallpaper-tile.applied-wallpaper:selected {
            border-color: shade(@theme_selected_bg_color, 1.15);
            background-color: alpha(@theme_selected_bg_color, 0.28);
            box-shadow: 0 0 16px 5px alpha(@theme_selected_bg_color, 0.85);
        }
        .applied-badge {
            background-color: @theme_selected_bg_color;
            color: @theme_selected_fg_color;
            border-radius: 9999px;
            padding: 2px 7px;
            font-size: 0.72em;
            font-weight: bold;
            box-shadow: 0 2px 5px rgba(0, 0, 0, 0.5);
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
        switch image {
            -gtk-icon-transform: scale(0);
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

        if self.image_pump_source:
            GLib.source_remove(self.image_pump_source)
            self.image_pump_source = 0

        for future in self.image_futures:
            future.cancel()

        self.image_futures.clear()
        self.image_paths.clear()
        self.grid_building = False

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

        if self.window is not None:
            self.window.set_title(
                "Wallpaper Selector — Applying…"
                if self.is_applying
                else "Wallpaper Selector"
            )

    def _matching_wallpapers(self):
        # Operate on lightweight strings, not thousands of GTK widgets.
        # The inventory is already naturally sorted.
        query = self.search_query
        favorites_only = self.show_only_favorites
        favorites = self.favorites

        return [
            path
            for path in self.wallpapers
            if (
                (not favorites_only or path in favorites)
                and (not query or query in path.casefold())
            )
        ]

    def _update_view_buttons(self):
        if self.btn_all is None:
            return

        all_context = self.btn_all.get_style_context()
        fav_context = self.btn_fav.get_style_context()

        all_context.remove_class("active-all")
        fav_context.remove_class("active-fav")

        if self.show_only_favorites:
            fav_context.add_class("active-fav")
        else:
            all_context.add_class("active-all")

    def start_refresh(self, *, rebuild=False):
        if self.closing or self.is_refreshing or self.is_applying:
            return

        self._cancel_generation()

        generation = self.generation
        stop_event = self.generation_stop
        auto_sweep = self.settings["AUTO_SWEEP_CACHE"]

        self.is_refreshing = True
        self._update_busy_controls()

        self.loading_title.set_text(
            "Rebuilding Image Cache…"
            if rebuild
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
                if auto_sweep:
                    paths = CacheManager.scan_and_sweep(stop_event)
                else:
                    paths = scan_wallpapers(stop_event)

                result = CacheBuildResult(paths)

            check_cancelled(stop_event)

            warnings = []
            favorites = None

            # Auxiliary state must not invalidate a successful inventory.
            # None tells the GTK callback to retain its previous favorites.
            try:
                favorites = load_favorites()
            except Exception as error:
                warnings.append(
                    "Could not reload favorites; keeping the last "
                    "successfully loaded favorites set:\n"
                    f"{describe_error(error)}"
                )

            current_path = None

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
                warnings.append(
                    f"Tracker warning:\n{describe_error(error)}"
                )

            check_cancelled(stop_event)

            return (
                result,
                favorites,
                current_path,
                "\n\n".join(warnings),
            )

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

    def _show_collection(self, *, target_path=None, focus=False):
        if self.closing or self.shutting_down:
            return

        if self.is_refreshing:
            return

        matches = self._matching_wallpapers()

        # Cancel old thumbnail work only when replacing the result set,
        # not when scrolling through the existing result set.
        self._cancel_generation()

        self.flowbox.set_paths(
            matches,
            target_path=target_path,
            focus=focus,
        )

        self.initial_grid = False
        self.loading_spinner.stop()

        self.empty_title.set_text("No Wallpapers Found")
        self.empty_subtitle.set_text(
            "Try another search or switch out of favorites."
        )

        self.stack.set_visible_child_name(
            "grid" if matches else "empty"
        )

        self._update_busy_controls()
        self._start_image_jobs()

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
            self.grid_building = False
            self.loading_spinner.stop()
            self._update_busy_controls()

            if self.wallpapers:
                self._show_collection(
                    target_path=self.flowbox.selected_path(),
                )
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
            log_error(f"Wallpaper loading warning:\n{warning}")

        if favorites is not None:
            self.favorites = favorites

        self.wallpapers = result.wallpapers
        if current_path is not None:
            self.applied_path = current_path

        if (
            self.initial_grid
            and current_path is not None
            and self.show_only_favorites
            and current_path not in self.favorites
            and not self.search_query
        ):
            self.show_only_favorites = False
            self._update_view_buttons()

        self.is_refreshing = False
        self.grid_building = False

        preserve_search_focus = (
            self.search_requested
            and self.search_entry.is_focus()
        )

        self._show_collection(
            target_path=current_path,
            focus=(
                not preserve_search_focus
                and self.popover is None
            ),
        )

        if result.failed:
            log_error(
                f"Cache rebuild finished with "
                f"{result.failed} unavailable images."
            )

    def _create_child(self, rel_path):
        child = Gtk.EventBox()
        child.set_visible_window(False)
        child.set_can_focus(True)
        child.set_size_request(GRID_TILE_SIZE, GRID_TILE_SIZE)
        child.get_style_context().add_class("wallpaper-tile")
        child.rel_path = rel_path
        child.pixbuf = None
        child.image_finished = False

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
        child.add(event_box)

        overlay = Gtk.Overlay()
        event_box.add(overlay)

        preview_box = Gtk.Box()
        preview_box.set_size_request(RENDER_SIZE, RENDER_SIZE)

        image = Gtk.Image()
        image.set_halign(Gtk.Align.CENTER)
        image.set_valign(Gtk.Align.CENTER)
        preview_box.pack_start(image, True, True, 0)

        child.preview_box = preview_box
        child.image_widget = image
        overlay.add(preview_box)

        heart = Gtk.Label(label="♥")
        heart.set_no_show_all(True)
        heart.get_style_context().add_class("heart-icon")
        heart.set_halign(Gtk.Align.END)
        heart.set_valign(Gtk.Align.START)
        heart.set_margin_top(6)
        heart.set_margin_end(8)

        child.heart_label = heart
        overlay.add_overlay(heart)
        overlay.set_overlay_pass_through(heart, True)

        applied_badge = Gtk.Label(label="✓ Active")
        applied_badge.set_no_show_all(True)
        applied_badge.get_style_context().add_class("applied-badge")
        applied_badge.set_halign(Gtk.Align.START)
        applied_badge.set_valign(Gtk.Align.START)
        applied_badge.set_margin_top(6)
        applied_badge.set_margin_start(8)

        child.applied_badge = applied_badge
        overlay.add_overlay(applied_badge)
        overlay.set_overlay_pass_through(applied_badge, True)

        name_label = Gtk.Label(label=os.path.basename(rel_path))
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

        child.name_label = name_label
        overlay.add_overlay(name_label)
        overlay.set_overlay_pass_through(name_label, True)

        self._render_child(child)
        return child

    # -------------------------------------------------------------------------
    # Bounded asynchronous image loading
    # -------------------------------------------------------------------------

    def _start_image_jobs(self):
        self._schedule_image_pump()

    def _schedule_image_pump(self):
        if self.closing or self.shutting_down or self.is_refreshing:
            return

        if self.image_pump_source:
            return

        def dispatch():
            self.image_pump_source = 0
            self._pump_image_jobs()
            return GLib.SOURCE_REMOVE

        self.image_pump_source = GLib.timeout_add(40, dispatch)

    def _pump_image_jobs(self):
        if (
            self.closing
            or self.shutting_down
            or self.is_refreshing
        ):
            return

        if self.stack.get_visible_child_name() != "grid":
            return

        grid = self.flowbox
        adjustment = grid.get_vadjustment()
        midpoint = (
            adjustment.get_value() + adjustment.get_page_size() / 2
        )
        selected_path = grid.selected_path()
        candidates = []

        for path, child in self.children.items():
            if child.image_finished or path in self.image_paths:
                continue

            index = grid.positions.get(path)
            if index is None:
                continue

            y = (
                GRID_PADDING
                + (index // grid.columns) * grid.row_height
                + GRID_TILE_SIZE / 2
            )
            candidates.append(
                (0 if path == selected_path else 1, abs(y - midpoint), path)
            )

        candidates.sort()
        generation = self.generation
        stop_event = self.generation_stop

        for _, _, path in candidates:
            if len(self.image_futures) >= MAX_IMAGE_JOBS:
                break

            future = self.image_executor.submit(
                self._load_pixbuf, path, stop_event
            )
            self.image_futures.add(future)
            self.image_paths.add(path)
            future.add_done_callback(
                lambda finished, rel_path=path, gen=generation: self._post_ui(
                    self._image_complete, finished, rel_path, gen
                )
            )

    @staticmethod
    def _load_pixbuf(rel_path, stop_event):
        check_cancelled(stop_event)

        # Keep a cooperating exclusive sweep/rebuild from removing a
        # thumbnail between validation/generation and its actual decoding.
        with file_lock(
            CACHE_LOCK_FILE,
            exclusive=False,
            stop_event=stop_event,
        ):
            status = CacheManager.generate_thumb(
                rel_path,
                stop_event=stop_event,
                cache_locked=True,
            )

            if status not in {"cached", "generated"}:
                return None

            thumb = CacheManager.get_thumb_path(rel_path)

            for attempt in range(2):
                check_cancelled(stop_event)

                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                        str(thumb),
                        RENDER_SIZE,
                        RENDER_SIZE,
                        True,
                    )

                    check_cancelled(stop_event)
                    return pixbuf

                except (GLib.Error, OSError) as error:
                    if attempt:
                        log_error(
                            f"Cannot decode thumbnail for "
                            f"{rel_path!r}: {error}"
                        )
                        return None

                    # A broken cached PNG is not proof that the source
                    # image is broken. Regenerate once, then retry decoding.
                    status = CacheManager.generate_thumb(
                        rel_path,
                        force=True,
                        stop_event=stop_event,
                        cache_locked=True,
                    )

                    if status != "generated":
                        return None

        return None

    def _image_complete(self, future, rel_path, generation):
        self.image_futures.discard(future)

        if generation != self.generation:
            return

        # Check the generation before clearing this marker. An old
        # completion must not remove a new generation's pending path.
        self.image_paths.discard(rel_path)

        try:
            pixbuf = future.result()
        except OperationCancelled:
            self._schedule_image_pump()
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

        self._schedule_image_pump()

    def _render_child(self, child):
        context = child.preview_box.get_style_context()

        if child.pixbuf is None:
            context.add_class("thumbnail-placeholder")
            child.image_widget.set_from_icon_name(
                "image-missing-symbolic"
                if child.image_finished
                else "image-x-generic-symbolic",
                Gtk.IconSize.DIALOG,
            )
        else:
            context.remove_class("thumbnail-placeholder")
            child.image_widget.set_from_pixbuf(child.pixbuf)

        child.heart_label.set_visible(
            child.rel_path in self.favorites
        )

        is_applied = (
            self.applied_path is not None
            and child.rel_path == self.applied_path
        )
        tile_context = child.get_style_context()
        if is_applied:
            tile_context.add_class("applied-wallpaper")
            child.applied_badge.set_visible(True)
        else:
            tile_context.remove_class("applied-wallpaper")
            child.applied_badge.set_visible(False)

        child.name_label.set_visible(
            child.rel_path == self.flowbox.selected_path()
            and self.settings["SHOW_FILENAMES"]
        )

    # -------------------------------------------------------------------------
    # Filtering, selection, and focus
    # -------------------------------------------------------------------------

    def set_view_mode(self, favorites):
        if self.closing:
            return

        changed = self.show_only_favorites != bool(favorites)
        self.show_only_favorites = bool(favorites)
        self._update_view_buttons()

        if self.flowbox is None or self.is_refreshing or not changed:
            return

        self._show_collection(target_path=self.flowbox.selected_path())

    def _on_search_button_press(self, entry, event):
        # Record explicit interaction, not GTK's automatic initial focus.
        self.search_requested = True
        return False

    def on_search_changed(self, entry):
        if self.closing:
            return

        text = entry.get_text()
        if text:
            self.search_requested = True

        query = text.casefold()
        if query == self.search_query:
            return

        self.search_query = query
        if self.flowbox is None or self.is_refreshing:
            return

        self._show_collection()

    def update_visibility_and_selection(self):
        if self.closing or self.is_refreshing or self.flowbox is None:
            return

        self.stack.set_visible_child_name(
            "grid" if self.flowbox.paths else "empty"
        )

    def on_selection_changed(self, flowbox):
        selected = flowbox.get_selected_children()
        self.current_selected_child = selected[0] if selected else None
        self.update_filename_visibility()
        self._schedule_image_pump()

    def update_filename_visibility(self):
        selected_path = self.flowbox.selected_path() if self.flowbox else None
        show_name = self.settings["SHOW_FILENAMES"]
        for path, child in self.children.items():
            child.name_label.set_visible(show_name and path == selected_path)

    def get_selected_path(self):
        if self.closing or self.is_refreshing or self.flowbox is None:
            return None

        return self.flowbox.selected_path()

    # -------------------------------------------------------------------------
    # Input handling
    # -------------------------------------------------------------------------

    def on_tile_button_press(self, event_box, event, child):
        if event.type != Gdk.EventType.BUTTON_PRESS:
            return False

        if self.is_refreshing:
            return True

        self.flowbox.select_path(child.rel_path)
        child.grab_focus()

        if event.button == 1:
            self.apply_wallpaper(child.rel_path, regen=True)
            return True
        elif event.button == 2:
            self.toggle_favorite(child.rel_path)
            return True
        elif event.button == 3:
            self.apply_wallpaper(child.rel_path, regen=False)
            return True

        return False

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
            self.search_requested = True
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
                    self.on_search_changed(self.search_entry)
                else:
                    self.flowbox.grab_focus()
                    self.flowbox.reveal_selected(focus=True)
            else:
                self.window.close()
            return True

        if editing:
            self.search_requested = True

            if (
                not alt
                and not ctrl
                and key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)
            ):
                # Apply any pending delayed search update before moving
                # focus into its result set.
                self.on_search_changed(self.search_entry)

                self.flowbox.grab_focus()
                self.flowbox.reveal_selected(focus=True)
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
                self.search_requested = True
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

        if self.show_only_favorites:
            self._show_collection(target_path=self.flowbox.selected_path())
            return

        # In All view, favorites do not change page membership.
        for path in changed:
            child = self.children.get(path)
            if child is not None:
                self._render_child(child)

        self._schedule_image_pump()

    def set_applied_path(self, rel_path):
        old_path = self.applied_path
        if old_path == rel_path:
            return

        self.applied_path = rel_path

        for path in (old_path, rel_path):
            if path:
                child = self.children.get(path)
                if child is not None:
                    self._render_child(child)

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
                    rel_path,
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
                rel_path,
            )

    def _backend_complete(self, success, message, should_close, rel_path=None):
        self.is_applying = False

        try:
            if not self.closing:
                self._update_busy_controls()

            if not success:
                self.show_error(
                    "Wallpaper Application Failed",
                    message,
                )
            else:
                if rel_path:
                    self.set_applied_path(rel_path)

                if should_close and self.window is not None:
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

        if self.image_pump_source:
            GLib.source_remove(self.image_pump_source)
            self.image_pump_source = 0

        for future in self.image_futures:
            future.cancel()

        self.image_futures.clear()
        self.image_paths.clear()
        self.grid_building = False

        if self.control_future is not None:
            self.control_future.cancel()
            self.control_future = None

        self.current_selected_child = None
        self.children.clear()
        self.window = None

    def on_shutdown(self, application):
        self.shutting_down = True
        self.closing = True
        self.generation_stop.set()
        self.backend_stop.set()

        if self.image_pump_source:
            GLib.source_remove(self.image_pump_source)
            self.image_pump_source = 0

        self.image_executor.shutdown(
            wait=False,
            cancel_futures=True,
        )
        self.control_executor.shutdown(
            wait=False,
            cancel_futures=True,
        )

        self.image_futures.clear()
        self.image_paths.clear()

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
