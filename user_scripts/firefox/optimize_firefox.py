#!/usr/bin/env python3
"""
Conservative Firefox cache-policy manager for Linux.

Python requirement: 3.12 or newer.

Supported actions:
    --cache-mode memory
        Disable Firefox's HTTP disk cache and explicitly enable its memory
        cache. Firefox retains its existing memory-cache capacity policy.

    --cache-mode default
    --disable
        Restore the user.js content and managed prefs.js entries saved by
        this implementation. This does NOT mean "erase all user settings"
        or "reset the entire Firefox profile to factory defaults."

Safety properties:
    * No automatic dependency installation.
    * No sudo, process killing, profile creation, or service reconfiguration.
    * No forced GPU, process-count, DNS, or telemetry preferences.
    * No scheduled database maintenance.
    * Dry runs do not create locks, directories, backups, or configuration.
    * Actual changes require exclusive Linux Firefox profile locks.
    * Previous backups are retained until a new backup completes.
    * Configuration files are replaced atomically with mode 0600.
    * Rollback information is saved before applying the cache policy.
    * Legacy optimizer blocks are migrated (backup retains full original).
    * Errors produce a nonzero exit status.

Limitations:
    * This script has not verified the source or runtime behavior of a
      particular Firefox 155 build or downstream fork.
    * Linux Firefox's fcntl-based .parentlock protocol is assumed.
    * Other maintenance tools must remain stopped throughout execution.
    * Symlinked profile roots are resolved for discovery; backup destinations
      must not contain symlink components. Transient/overlay profile
      filesystems and nested profile filesystems remain unsupported.
      Internal profile symlinks are stored as links, not followed.
    * Operations across multiple files/profiles are NOT one transaction.
      If interrupted, retain backups and rollback state; rerun the requested
      operation after resolving the error.
    * Disabling the HTTP disk cache does not make a profile memory-only.
    * Cross-filesystem backups are allowed with a warning and do not
      establish fscrypt policy equivalence, encrypted-swap coverage, or
      protection against an attacker running as the same user.
"""

from __future__ import annotations

import argparse
import configparser
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterator


LOGGER = logging.getLogger("firefox_cache_policy")

USER_JS_BEGIN: Final = "// === BEGIN FIREFOX OPTIMIZATION SUITE ==="
USER_JS_END: Final = "// === END FIREFOX OPTIMIZATION SUITE ==="

STATE_FILENAME: Final = ".firefox-cache-policy-state.json"
STATE_SCHEMA: Final = 1

MAX_BACKUPS_PER_PROFILE: Final = 3
MAX_TEXT_BYTES: Final = 128 * 1024 * 1024
BACKUP_HEADROOM_BYTES: Final = 128 * 1024 * 1024

MANAGED_KEYS: Final = frozenset({
    "browser.cache.disk.enable",
    "browser.cache.memory.enable",
})

# Legacy keys from previous aggressive optimizer (usability migration).
# Removed from user.js/prefs.js when migrating old blocks to new policy.
LEGACY_KEYS: Final = frozenset({
    "browser.cache.memory.enable",
    "browser.cache.memory.capacity",
    "browser.cache.disk.smart_size.enabled",
    "browser.cache.disk_cache_ssl",
    "browser.cache.offline.enable",
    "dom.ipc.processCount",
    "dom.ipc.processCount.webIsolated",
    "dom.ipc.processCount.extension",
    "fission.autostart",
    "browser.tabs.unloadOnLowMemory",
    "gfx.webrender.all",
    "layers.acceleration.force-enabled",
    "media.ffmpeg.vaapi.enabled",
    "media.hardware-video-decoding.force-enabled",
    "widget.wayland-dmabuf-vaapi.enabled",
    "widget.wayland.opaque-region.enabled",
    "apz.gtk.kinetic_scroll.enabled",
    "toolkit.telemetry.enabled",
    "datareporting.healthreport.uploadEnabled",
    "app.normandy.enabled",
    "network.http.max-connections",
    "network.http.max-persistent-connections-per-server",
    "network.trr.mode",
    "network.trr.uri",
    "browser.cache.disk.enable",
    "browser.cache.disk.parent_directory",
})

def strip_old_optimizer_block(content: str | None) -> str | None:
    """
    Remove complete marker-delimited blocks.

    Markers must occupy their own lines. Preserve all text outside blocks
    exactly; reject unmatched or nested markers.
    """
    if content is None:
        return None

    kept: list[str] = []
    inside = False
    found = False

    for line in split_pref_lines(content):
        marker = line.strip(" \t\r\n")

        if marker == USER_JS_BEGIN:
            if inside:
                raise SafetyError("Nested legacy optimizer BEGIN marker")
            inside = True
            found = True
            continue

        if marker == USER_JS_END:
            if not inside:
                raise SafetyError("Legacy optimizer END marker without BEGIN")
            inside = False
            continue

        if not inside:
            kept.append(line)

    if inside:
        raise SafetyError("Legacy optimizer BEGIN marker without END")

    if not found:
        raise SafetyError(
            "Optimizer marker text exists, but no complete standalone "
            "optimizer block was found"
        )

    result = "".join(kept)
    return result if result.strip() else None


def strip_legacy_pref_lines(
    content: str,
    keys: frozenset[str] = LEGACY_KEYS,
) -> str:
    """Remove complete declarations by parsed key; preserve other text."""
    result = transform_pref_content(content, remove_keys=keys)
    assert result is not None
    return result

# Never open .parentlock while holding its POSIX record lock.
# A close() on another descriptor for that inode can release our locks.
EXCLUDED_ROOT_ENTRIES: Final = frozenset({
    ".parentlock",
    "lock",
})

TRANSIENT_FILESYSTEMS: Final = frozenset({
    "tmpfs",
    "ramfs",
    "overlay",
})

MAINTENANCE_UNITS: Final = (
    "psd.service",
    "psd-resync.service",
    "psd-resync.timer",
    "profile-cleaner.service",
    "profile-cleaner.timer",
)

JSON_STRING_PATTERN: Final = r'"(?:[^"\\]|\\.)*"'

PREF_START: Final = re.compile(
    rf"^\s*user_pref\s*\(\s*({JSON_STRING_PATTERN})\s*,"
)

COMPLETE_BOOLEAN_PREF: Final = re.compile(
    rf"^\s*user_pref\s*\(\s*({JSON_STRING_PATTERN})"
    r"\s*,\s*(true|false)\s*\)\s*;\s*$"
)


class SafetyError(RuntimeError):
    """An unsafe or ambiguous condition requires explicit user intervention."""


class UninitializedProfileError(SafetyError):
    """The profile directory exists but has no prefs.js."""


@dataclass(frozen=True)
class ProfilePlan:
    profile: Path
    old_user_js: str | None
    old_prefs_js: str
    old_state: str | None
    new_user_js: str | None
    new_prefs_js: str
    new_state: str | None

    @property
    def changed(self) -> bool:
        return (
            self.old_user_js != self.new_user_js
            or self.old_prefs_js != self.new_prefs_js
            or self.old_state != self.new_state
            or (
                self.new_state is not None
                and "migration_user_js" in json.loads(self.new_state)
            )
        )


@dataclass(frozen=True)
class BackupEntry:
    path: Path
    signature: tuple[int, int, int, int, int, int]


def stat_signature(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def absolute_without_symlinks(path: Path) -> Path:
    """
    Make a path absolute while rejecting symlink components before
    processing '..'. Do not silently reinterpret a symlink-containing path.
    """
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded

    # Normalize Linux's possible leading '//' spelling to '/'.
    current = Path("/")

    for part in expanded.parts[1:]:
        if part == "..":
            # Filesystem traversal through a nonexistent or non-directory
            # component is invalid, even if lexical normalization would
            # otherwise erase that component.
            if not current.is_dir():
                raise SafetyError(
                    f"Cannot traverse '..' through a missing or "
                    f"non-directory component: {current}"
                )
            current = current.parent
            continue

        current = current / part

        try:
            info = current.lstat()
        except FileNotFoundError:
            # Backup destinations may contain directories not yet created.
            continue

        if stat.S_ISLNK(info.st_mode):
            raise SafetyError(
                f"Symlinked path components are unsupported: {current}"
            )

    return current


def read_optional(path: Path) -> str | None:
    """
    Read an ordinary user-owned UTF-8 configuration file.

    Refuse final symlinks, hard-linked configuration files, group/world
    writable files, oversized input, and changes detected during the read.
    """
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK

    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None

    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())

        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or before.st_mode & 0o022
        ):
            raise SafetyError(f"Unsafe configuration file: {path}")

        if before.st_size > MAX_TEXT_BYTES:
            raise SafetyError(f"Configuration file is too large: {path}")

        data = source.read(MAX_TEXT_BYTES + 1)
        after = os.fstat(source.fileno())

        if len(data) > MAX_TEXT_BYTES:
            raise SafetyError(f"Configuration file is too large: {path}")

        if stat_signature(before) != stat_signature(after):
            raise SafetyError(f"Configuration changed while being read: {path}")

        current = path.lstat()
        if stat_signature(after) != stat_signature(current):
            raise SafetyError(f"Configuration path changed while being read: {path}")

    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SafetyError(f"Configuration is not valid UTF-8: {path}") from error


def fsync_directory(directory: Path) -> None:
    fd = os.open(
        directory,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace_text(
    path: Path,
    expected: str | None,
    replacement: str | None,
) -> None:
    """
    Replace or remove a configuration file after checking its expected content.

    Each replacement is atomic. This is not an atomic transaction across
    several files, and it does not defend against a hostile same-UID process.
    """
    if read_optional(path) != expected:
        raise SafetyError(f"Configuration changed since inspection: {path}")

    if expected == replacement:
        return

    if replacement is None:
        path.unlink()
        fsync_directory(path.parent)
        return

    data = replacement.encode("utf-8")
    if len(data) > MAX_TEXT_BYTES:
        raise SafetyError(f"Generated configuration is too large: {path}")

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)

    try:
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())

        # Recheck after preparing the replacement.
        if read_optional(path) != expected:
            raise SafetyError(f"Configuration changed before replacement: {path}")

        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def require_tools() -> None:
    for executable in ("systemctl", "findmnt"):
        if shutil.which(executable) is None:
            raise SafetyError(
                f"Required executable is missing: {executable}. "
                "Install system dependencies explicitly; this script will not."
            )


def run_inspection(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def require_inactive_maintenance_services() -> None:
    """
    Refuse active/enabled maintenance units known to this script.

    This cannot discover every possible custom maintenance job.
    """
    for unit in MAINTENANCE_UNITS:
        result = run_inspection([
            "systemctl",
            "--user",
            "show",
            unit,
            "--property=LoadState",
            "--property=ActiveState",
            "--property=UnitFileState",
        ])

        if result.returncode != 0:
            raise SafetyError(
                f"Cannot inspect {unit}: "
                f"{result.stderr.strip() or 'systemctl failed'}"
            )

        properties = dict(
            line.split("=", 1)
            for line in result.stdout.splitlines()
            if "=" in line
        )

        load_state = properties.get("LoadState")
        active_state = properties.get("ActiveState")
        unit_file_state = properties.get("UnitFileState", "")

        if load_state == "not-found":
            continue

        if load_state not in {"loaded", "masked"}:
            raise SafetyError(
                f"{unit} has an unsupported load state: {load_state!r}"
            )

        if active_state not in {"inactive", "failed"}:
            raise SafetyError(
                f"{unit} is {active_state or 'of unknown state'}. "
                "Close Firefox and stop profile-maintenance automation first. "
                "If using PSD, let it restore the persistent profile."
            )

        if unit_file_state in {
            "enabled",
            "enabled-runtime",
            "linked",
            "linked-runtime",
            "alias",
        }:
            raise SafetyError(
                f"{unit} has unit-file state {unit_file_state!r}. "
                "Disable or reconcile this maintenance automation explicitly."
            )


def filesystem_type(path: Path) -> str:
    result = run_inspection([
        "findmnt",
        "--noheadings",
        "--output",
        "FSTYPE",
        "--target",
        str(path),
    ])

    values = result.stdout.split()
    if result.returncode != 0 or len(values) != 1:
        raise SafetyError(
            f"Cannot determine the filesystem containing {path}: "
            f"{result.stderr.strip() or 'unexpected findmnt output'}"
        )

    return values[0]


def validate_profile(path: Path) -> Path:
    # Usability: resolve symlinked profile paths (e.g. ~/.mozilla -> ~/.config/mozilla)
    # instead of refusing. Backup destination handling still validates real location.
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    try:
        profile = expanded.resolve()
    except Exception:
        profile = absolute_without_symlinks(path)
    info = profile.stat()

    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
    ):
        raise SafetyError(
            f"Profile must be a user-owned directory that is not "
            f"group/world writable: {profile}"
        )

    fs_type = filesystem_type(profile)
    if fs_type in TRANSIENT_FILESYSTEMS:
        raise SafetyError(
            f"Unsupported profile filesystem {fs_type!r}: {profile}. "
            "Use the persistent, non-overlay profile."
        )

    if read_optional(profile / "prefs.js") is None:
        raise UninitializedProfileError(
            f"No prefs.js in {profile}; skipping uninitialized profile."
        )

    return profile


def find_firefox_profiles(explicit: list[Path]) -> list[Path]:
    """
    Use explicit paths when supplied; otherwise inspect registered profiles.

    No directory-name guessing and no automatic profile creation.
    """
    candidates: list[Path] = []

    if explicit:
        candidates.extend(explicit)
    else:
        xdg_value = os.environ.get("XDG_CONFIG_HOME")
        xdg_config = (
            Path(xdg_value)
            if xdg_value
            else Path.home() / ".config"
        )

        if not xdg_config.is_absolute():
            raise SafetyError("XDG_CONFIG_HOME must be absolute when set")

        roots = (
            xdg_config / "mozilla" / "firefox",
            Path.home() / ".mozilla" / "firefox",
        )

        for root in dict.fromkeys(roots):
            try:
                root = absolute_without_symlinks(root)
            except SafetyError:
                # Usability: symlinked roots (e.g. ~/.mozilla -> ~/.config/mozilla)
                # are standard; resolve and dedup instead of aborting.
                try:
                    resolved = root.expanduser()
                    if not resolved.is_absolute():
                        resolved = Path.cwd() / resolved
                    resolved = resolved.resolve()
                    LOGGER.warning(
                        "Symlinked profile root %s resolved to %s",
                        root,
                        resolved,
                    )
                    root = resolved
                except Exception:
                    continue
            ini_path = root / "profiles.ini"
            ini_content = read_optional(ini_path)

            if ini_content is None:
                continue

            config = configparser.ConfigParser(interpolation=None)
            config.read_string(ini_content, source=str(ini_path))

            for section in config.sections():
                if not re.fullmatch(r"Profile\d+", section):
                    continue

                raw_path = config.get(section, "Path")
                relative = config.getint(section, "IsRelative")

                if not raw_path:
                    raise SafetyError(f"Empty profile path in {ini_path}: {section}")

                registered = Path(raw_path)

                if relative == 1:
                    if registered.is_absolute():
                        raise SafetyError(
                            f"Absolute path marked relative in {ini_path}: {section}"
                        )
                    registered = root / registered
                elif relative == 0:
                    if not registered.is_absolute():
                        raise SafetyError(
                            f"Relative path marked absolute in {ini_path}: {section}"
                        )
                else:
                    raise SafetyError(
                        f"Invalid IsRelative in {ini_path}: {section}"
                    )

                candidates.append(registered)

    profiles_by_inode: dict[tuple[int, int], Path] = {}

    for candidate in candidates:
        try:
            profile = validate_profile(candidate)
        except UninitializedProfileError as error:
            LOGGER.warning("Skipping profile %s: %s", candidate, error)
            continue
        except FileNotFoundError:
            if explicit:
                raise
            LOGGER.warning(
                "Skipping missing registered profile: %s",
                candidate,
            )
            continue

        info = profile.stat()
        profiles_by_inode.setdefault((info.st_dev, info.st_ino), profile)

    profiles = sorted(profiles_by_inode.values())

    if not profiles:
        raise SafetyError(
            "No initialized registered profiles found. "
            "Run Firefox normally first, or use --profile PATH."
        )

    # A nested profile would make backup and ownership boundaries ambiguous.
    for index, profile in enumerate(profiles):
        for other in profiles[index + 1:]:
            if profile.is_relative_to(other) or other.is_relative_to(profile):
                raise SafetyError(
                    f"Nested selected profiles are unsupported: {profile}, {other}"
                )

    return profiles


@contextmanager
def locked_profile(profile: Path) -> Iterator[None]:
    """
    Hold Firefox's Linux fcntl profile lock throughout the operation.

    Do not unlink .parentlock and do not open/read it elsewhere while held.
    """
    original_profile = profile.stat()

    fd = os.open(
        profile / ".parentlock",
        os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
    )

    try:
        info = os.fstat(fd)

        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o022
        ):
            raise SafetyError(f"Unsafe profile lock: {profile / '.parentlock'}")

        try:
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SafetyError(
                f"Firefox profile is locked: {profile}. "
                "Close Firefox normally and rerun."
            ) from error

        current_lock = (profile / ".parentlock").lstat()
        if (
            not stat.S_ISREG(current_lock.st_mode)
            or (info.st_dev, info.st_ino)
            != (current_lock.st_dev, current_lock.st_ino)
        ):
            raise SafetyError(f"Profile lock changed during acquisition: {profile}")

        current_profile = profile.stat()
        if (
            original_profile.st_dev,
            original_profile.st_ino,
        ) != (
            current_profile.st_dev,
            current_profile.st_ino,
        ):
            raise SafetyError(f"Profile directory changed: {profile}")

        yield
    finally:
        # Closing this descriptor releases our record lock.
        os.close(fd)


def split_pref_lines(content: str) -> list[str]:
    """
    Split on CRLF, LF, or CR only, preserving every character.

    Other Unicode separator characters may occur inside preference strings
    and must not be treated as preference-statement boundaries.
    """
    return re.findall(
        r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$",
        content,
    )


def managed_pref_key(line: str) -> str | None:
    """
    Identify a managed entry in Firefox-generated prefs.js.

    Managed entries must be complete boolean user_pref statements, one per
    line. Ambiguous syntax is refused rather than edited by substring.
    """
    match = PREF_START.match(line)

    if match is None:
        stripped = line.lstrip()
        if stripped.startswith(("user_pref", "pref", "sticky_pref")):
            if any(key in line for key in MANAGED_KEYS):
                raise SafetyError(
                    "Unsupported syntax for a managed preference in prefs.js"
                )
        return None

    key = json.loads(match.group(1))
    if key not in MANAGED_KEYS:
        return None

    complete = COMPLETE_BOOLEAN_PREF.fullmatch(line)
    if complete is None:
        raise SafetyError(
            f"Managed preference is not a complete boolean statement: {key}"
        )

    if json.loads(complete.group(1)) != key:
        raise SafetyError("Inconsistent managed preference parsing")

    return key


def transform_pref_content(
    content: str | None,
    remove_keys: frozenset[str] = frozenset(),
) -> str | None:
    """
    Validate the supported user.js subset before appending managed settings.

    Accepted:
        user_pref("name", "string");
        user_pref("name", true);
        user_pref("name", false);
        user_pref("name", signed_32_bit_integer);

    Whitespace and // or /* */ comments may separate tokens.

    Reject unsupported syntax, incomplete comments, and existing managed
    declarations, including escaped key names. This is deliberately not
    a general JavaScript parser.
    """
    if content is None:
        return None

    if USER_JS_BEGIN in content or USER_JS_END in content:
        raise SafetyError(
            "An existing optimizer block has no accepted rollback state. "
            "Restore or reconcile it manually from a trustworthy backup."
        )

    decoder = json.JSONDecoder()
    position = 0
    length = len(content)
    removals: list[tuple[int, int]] = []

    def fail(message: str) -> None:
        raise SafetyError(
            f"Unsupported or ambiguous user.js near character "
            f"{position}: {message}"
        )

    def skip_trivia() -> None:
        nonlocal position

        while position < length:
            if content[position] in " \t\r\n":
                position += 1
                continue

            if content.startswith("//", position):
                position += 2
                while (
                    position < length
                    and content[position] not in "\r\n"
                ):
                    position += 1
                continue

            if content.startswith("/*", position):
                end = content.find("*/", position + 2)
                if end == -1:
                    fail("unterminated block comment")
                position = end + 2
                continue

            break

    def consume(token: str) -> None:
        nonlocal position

        skip_trivia()
        if not content.startswith(token, position):
            fail(f"expected {token!r}")
        position += len(token)

    def decode_value() -> object:
        nonlocal position

        skip_trivia()
        try:
            value, end = decoder.raw_decode(content, position)
        except json.JSONDecodeError as error:
            fail(f"expected a supported literal ({error.msg})")

        position = end
        return value

    while True:
        skip_trivia()
        if position == length:
            result = content
            for start, end in reversed(removals):
                result = result[:start] + result[end:]
            return result

        statement_start = position
        consume("user_pref")
        consume("(")

        key = decode_value()
        if not isinstance(key, str):
            fail("preference names must be double-quoted strings")

        if key in MANAGED_KEYS and key not in remove_keys:
            raise SafetyError(
                f"user.js already defines managed preference {key!r}. "
                "Resolve that policy explicitly before applying this tool."
            )

        consume(",")
        value = decode_value()

        if type(value) is int:
            if not -(2**31) <= value < 2**31:
                fail("integer preference is outside the signed 32-bit range")
        elif type(value) not in (str, bool):
            fail("only string, boolean, and integer values are supported")

        consume(")")
        consume(";")
        if key in remove_keys:
            removals.append((statement_start, position))


def validate_original_user_js(content: str | None) -> None:
    transform_pref_content(content)


def generated_user_js(original: str | None) -> str:
    lines = (
        USER_JS_BEGIN,
        "// Managed HTTP cache policy; no memory-capacity override.",
        'user_pref("browser.cache.disk.enable", false);',
        'user_pref("browser.cache.memory.enable", true);',
        USER_JS_END,
        "",
    )
    block = "\n".join(lines)
    prefix = original or ""

    separator = "" if not prefix or prefix.endswith(("\n", "\r")) else "\n"
    return prefix + separator + block


def encode_state(
    original_user_js: str | None,
    baseline_prefs: list[str],
    *,
    migration_user_js: str | None = None,
) -> str:
    state = {
        "schema": STATE_SCHEMA,
        "original_user_js": original_user_js,
        "baseline_prefs": baseline_prefs,
    }

    if migration_user_js is not None:
        state["migration_user_js"] = migration_user_js

    encoded = json.dumps(state, ensure_ascii=True, indent=2) + "\n"

    if len(encoded.encode("utf-8")) > MAX_TEXT_BYTES:
        raise SafetyError("Rollback state would exceed the supported size limit")

    return encoded


def reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise SafetyError(f"Duplicate key in rollback state: {key}")
        result[key] = value
    return result


def decode_state(content: str) -> tuple[str | None, list[str]]:
    state = json.loads(
        content,
        object_pairs_hook=reject_duplicate_json_keys,
    )

    required = {"schema", "original_user_js", "baseline_prefs"}
    allowed = required | {"migration_user_js"}

    if (
        not isinstance(state, dict)
        or not required.issubset(state)
        or not set(state).issubset(allowed)
    ):
        raise SafetyError("Invalid rollback-state structure")

    if "migration_user_js" in state:
        source = state["migration_user_js"]
        if (
            not isinstance(source, str)
            or USER_JS_BEGIN not in source
            or USER_JS_END not in source
        ):
            raise SafetyError("Invalid pending migration source")

    if type(state["schema"]) is not int or state["schema"] != STATE_SCHEMA:
        raise SafetyError("Unsupported rollback-state schema; no migration is attempted")

    original = state["original_user_js"]
    baseline = state["baseline_prefs"]

    if original is not None and not isinstance(original, str):
        raise SafetyError("Invalid original user.js in rollback state")

    if not isinstance(baseline, list):
        raise SafetyError("Invalid saved preference list in rollback state")

    validate_original_user_js(original)

    validated: list[str] = []
    for line in baseline:
        if not isinstance(line, str) or len(split_pref_lines(line)) != 1:
            raise SafetyError("Invalid saved preference line")

        if managed_pref_key(line) not in MANAGED_KEYS:
            raise SafetyError("Unexpected preference in rollback state")

        validated.append(line)

    return original, validated


def restore_managed_prefs(content: str, baseline: list[str]) -> str:
    remaining = "".join(
        line
        for line in split_pref_lines(content)
        if managed_pref_key(line) not in MANAGED_KEYS
    )

    if not baseline:
        return remaining

    if remaining and not remaining.endswith(("\n", "\r")):
        remaining += "\n"

    # Retain statement text, ensuring separate saved entries stay separate.
    saved = "".join(
        line if line.endswith(("\n", "\r")) else line + "\n"
        for line in baseline
    )

    return remaining + saved


def prepare_profile(profile: Path, memory_mode: bool) -> ProfilePlan:
    user_js = read_optional(profile / "user.js")
    prefs_js = read_optional(profile / "prefs.js")
    state_text = read_optional(profile / STATE_FILENAME)

    if prefs_js is None:
        raise SafetyError(f"Missing prefs.js: {profile}")

    # Validate all managed prefs.js statements before any planned write.
    baseline_now = [
        line
        for line in split_pref_lines(prefs_js)
        if managed_pref_key(line) in MANAGED_KEYS
    ]

    if state_text is None:
        if user_js and (
            USER_JS_BEGIN in user_js or USER_JS_END in user_js
        ):
            # Usability migration: replace legacy aggressive block instead of refusing.
            # Backup (created before any write) retains full original.
            LOGGER.warning(
                "%s: migrating legacy optimizer block to new cache policy",
                profile,
            )
            stripped = strip_old_optimizer_block(user_js)

            if stripped is not None:
                stripped = strip_legacy_pref_lines(stripped)
                if not stripped.strip():
                    stripped = None

            # Validate for BOTH memory mode and --disable.
            validate_original_user_js(stripped)
            cleaned_prefs = strip_legacy_pref_lines(prefs_js)
            if not memory_mode:
                # --disable on legacy: remove aggressive tuning, revert to defaults.
                return ProfilePlan(
                    profile,
                    user_js,
                    prefs_js,
                    None,
                    stripped,
                    cleaned_prefs,
                    None,
                )
            # memory_mode: validate stripped benign base, then apply new block.
            validate_original_user_js(stripped)
            new_user_js = generated_user_js(stripped)
            if len(new_user_js.encode("utf-8")) > MAX_TEXT_BYTES:
                raise SafetyError(f"Generated user.js is too large: {profile}")
            return ProfilePlan(
                profile=profile,
                old_user_js=user_js,
                old_prefs_js=prefs_js,
                old_state=None,
                new_user_js=new_user_js,
                new_prefs_js=cleaned_prefs,
                new_state=encode_state(
                    stripped,
                    [],
                    migration_user_js=user_js,
                ),
            )

        if not memory_mode:
            return ProfilePlan(
                profile,
                user_js,
                prefs_js,
                None,
                user_js,
                prefs_js,
                None,
            )

        validate_original_user_js(user_js)
        new_user_js = generated_user_js(user_js)

        if len(new_user_js.encode("utf-8")) > MAX_TEXT_BYTES:
            raise SafetyError(f"Generated user.js is too large: {profile}")

        return ProfilePlan(
            profile=profile,
            old_user_js=user_js,
            old_prefs_js=prefs_js,
            old_state=None,
            new_user_js=new_user_js,
            # Firefox will apply user.js at startup; no prefs.js rewrite
            # is necessary when enabling this policy.
            new_prefs_js=prefs_js,
            new_state=encode_state(user_js, baseline_now),
        )

    original, baseline = decode_state(state_text)
    state_data = json.loads(state_text)
    pending_source = state_data.get("migration_user_js")
    expected_applied = generated_user_js(original)

    acceptable = [original, expected_applied]

    if pending_source is not None:
        recovered_base = strip_old_optimizer_block(pending_source)

        if recovered_base is not None:
            recovered_base = strip_legacy_pref_lines(recovered_base)
            if not recovered_base.strip():
                recovered_base = None

        validate_original_user_js(recovered_base)

        if recovered_base != original or baseline:
            raise SafetyError(
                f"{profile}: pending migration does not match its saved base"
            )

        acceptable.append(pending_source)

    if user_js not in acceptable:
        raise SafetyError(
            f"{profile}: user.js changed outside this tool. "
            "Refusing to overwrite it. Reconcile your edits and saved state."
        )

    working_prefs = (
        strip_legacy_pref_lines(prefs_js)
        if pending_source is not None
        else prefs_js
    )

    if memory_mode:
        return ProfilePlan(
            profile,
            user_js,
            prefs_js,
            state_text,
            expected_applied,
            working_prefs,
            # Keep pending recovery information until writes complete.
            state_text,
        )

    return ProfilePlan(
        profile,
        user_js,
        prefs_js,
        state_text,
        original,
        restore_managed_prefs(working_prefs, baseline),
        None,
    )


def verify_plan_unchanged(plan: ProfilePlan) -> None:
    expected = (
        ("user.js", plan.old_user_js),
        ("prefs.js", plan.old_prefs_js),
        (STATE_FILENAME, plan.old_state),
    )

    for filename, content in expected:
        if read_optional(plan.profile / filename) != content:
            raise SafetyError(
                f"Configuration changed after planning: {plan.profile / filename}"
            )


def create_backup_directories(destination: Path) -> None:
    """
    Create missing directories with private permissions and persist each
    new directory entry before proceeding.

    An unexpected concurrent creation is treated as an error rather than
    silently accepting a directory we did not inspect.
    """
    missing: list[Path] = []
    ancestor = destination

    while not ancestor.exists():
        missing.append(ancestor)
        ancestor = ancestor.parent

    if not ancestor.is_dir():
        raise SafetyError(
            f"Backup ancestor is not a directory: {ancestor}"
        )

    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        fsync_directory(directory)
        fsync_directory(directory.parent)

    # Also sync the immediate parent when the destination already existed.
    fsync_directory(destination.parent)


def backup_destination(
    profile: Path,
    override: Path | None,
    all_profiles: list[Path],
    *,
    create: bool,
) -> Path:
    destination = absolute_without_symlinks(
        override if override is not None
        else profile.parent / ".firefox-cache-policy-backups"
    )

    for selected in all_profiles:
        if destination == selected or destination.is_relative_to(selected):
            raise SafetyError(
                f"Backup directory must be outside every selected profile: "
                f"{destination}"
            )

    ancestor = destination
    while not ancestor.exists():
        ancestor = ancestor.parent

    if not ancestor.is_dir():
        raise SafetyError(f"Backup ancestor is not a directory: {ancestor}")

    profile_device = profile.stat().st_dev

    if ancestor.stat().st_dev != profile_device:
        # Usability: allow cross-filesystem backups with explicit warning
        # (e.g. tiny LUKS profile -> roomy /home). Passwords may be exposed
        # when LUKS is locked; user explicitly prioritizes usability.
        LOGGER.warning(
            "Backup destination %s is on a different filesystem than profile %s. "
            "Encrypted-profile data may remain accessible when LUKS is locked.",
            destination,
            profile,
        )

    if create:
        create_backup_directories(destination)

    if destination.exists():
        info = destination.lstat()

        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
        ):
            raise SafetyError(
                f"Backup directory must be user-owned directory: {destination}"
            )

        # Usability: tighten loose perms instead of refusing (real runs only;
        # dry runs must not modify).
        if info.st_mode & 0o077:
            if not create:
                LOGGER.warning(
                    "Backup directory %s has permissive permissions %o (would tighten to 0700 on apply)",
                    destination,
                    info.st_mode & 0o777,
                )
            else:
                try:
                    os.chmod(destination, 0o700)
                    LOGGER.warning(
                        "Tightened backup directory permissions to 0700: %s",
                        destination,
                    )
                    info = destination.lstat()
                except OSError as error:
                    raise SafetyError(
                        f"Cannot secure backup directory permissions: {destination}"
                    ) from error

        if info.st_mode & 0o077 and create:
            raise SafetyError(
                f"Backup directory must be private (0700): {destination}"
            )

        if info.st_dev != profile_device:
            LOGGER.warning(
                "Backup directory %s is on a different filesystem than profile. "
                "See earlier cross-filesystem warning.",
                destination,
            )

    return destination


def reject_nested_profile_mounts(profile: Path) -> None:
    """
    Reject mount points strictly inside the selected profile.

    st_dev alone misses bind mounts of directories on the same filesystem.
    Linux mountinfo escapes whitespace and backslashes using octal sequences.
    """
    try:
        mountinfo = Path("/proc/self/mountinfo").read_bytes()
    except OSError as error:
        raise SafetyError(
            "Cannot inspect mount topology before backing up the profile"
        ) from error

    def unescape_mount_path(value: bytes) -> bytes:
        return re.sub(
            rb"\\([0-7]{3})",
            lambda match: bytes([int(match.group(1), 8)]),
            value,
        )

    for line in mountinfo.splitlines():
        fields = line.split()

        if len(fields) < 10 or b"-" not in fields[6:]:
            raise SafetyError("Unexpected /proc/self/mountinfo format")

        mount_path = Path(
            os.fsdecode(unescape_mount_path(fields[4]))
        )

        if not mount_path.is_absolute():
            raise SafetyError("Non-absolute mount point in mountinfo")

        if mount_path != profile and mount_path.is_relative_to(profile):
            raise SafetyError(
                f"Nested mount point inside the profile is unsupported: "
                f"{mount_path}. Unmount or reconcile it explicitly before "
                "backing up this profile."
            )


def inspect_backup_tree(profile: Path) -> tuple[list[BackupEntry], int]:
    """
    Build a deterministic inventory without following symlinks.

    Reject nested mounts and repeated directory identities. Root Firefox
    lock entries are omitted before they can be opened.

    Other maintenance tools must remain stopped throughout the operation.
    """
    reject_nested_profile_mounts(profile)

    profile_device = profile.stat().st_dev
    entries: list[BackupEntry] = []
    visited_directories: set[tuple[int, int]] = set()
    total_file_bytes = 0
    pending = [profile]

    while pending:
        path = pending.pop()
        info = path.lstat()

        # Usability: allow internal symlinks (e.g. theme_matugen colors.css).
        # Symlink perms (0777) are ignored; target is stored as link, not followed.
        is_symlink = stat.S_ISLNK(info.st_mode)
        if not is_symlink and (
            info.st_dev != profile_device
            or info.st_uid != os.getuid()
        ):
            raise SafetyError(
                f"Unsupported ownership or nested filesystem "
                f"in backup source: {path}"
            )

        # Usability: Firefox creates world-writable files (e.g. 0666 telemetry).
        # Allow with warning instead of refusing entire profile.
        if not is_symlink and (info.st_mode & 0o022):
            LOGGER.warning(
                "Permissive permissions %o on %s; including in backup",
                info.st_mode & 0o777,
                path,
            )

        if is_symlink:
            # Owner check only; do not follow, recurse, or count target bytes.
            if info.st_uid != os.getuid():
                raise SafetyError(
                    f"Unsupported symlink ownership in backup source: {path}"
                )
            entries.append(BackupEntry(path, stat_signature(info)))
            continue

        is_directory = stat.S_ISDIR(info.st_mode)
        is_regular_file = stat.S_ISREG(info.st_mode)

        if not (is_directory or is_regular_file):
            raise SafetyError(
                f"Special file in profile backup source: {path}"
            )

        if is_directory:
            identity = (info.st_dev, info.st_ino)
            if identity in visited_directories:
                raise SafetyError(
                    f"Repeated directory identity in profile backup source: "
                    f"{path}. A directory alias or mount cycle may be present."
                )
            visited_directories.add(identity)

        entries.append(BackupEntry(path, stat_signature(info)))

        if is_regular_file:
            total_file_bytes += info.st_size
            continue

        with os.scandir(path) as directory:
            children = sorted(
                (
                    Path(entry.path)
                    for entry in directory
                    if not (
                        path == profile
                        and entry.name in EXCLUDED_ROOT_ENTRIES
                    )
                ),
                key=os.fspath,
                reverse=True,
            )

        pending.extend(children)

    reject_nested_profile_mounts(profile)
    return entries, total_file_bytes


def backup_profile(
    profile: Path,
    destination: Path,
) -> Path:
    entries, total_file_bytes = inspect_backup_tree(profile)

    # Conservative preflight, not a guarantee. PAX metadata and concurrent
    # disk use can still cause failure; incomplete archives are removed.
    estimated_bytes = (
        total_file_bytes
        + total_file_bytes // 10
        + len(entries) * 8192
        + BACKUP_HEADROOM_BYTES
    )

    available = shutil.disk_usage(destination).free
    if available < estimated_bytes:
        raise SafetyError(
            f"Insufficient conservative backup headroom at {destination}: "
            f"{available / 1024**2:.0f} MiB available, "
            f"{estimated_bytes / 1024**2:.0f} MiB estimated. "
            "Move or prune old backups explicitly; this script will not "
            "delete them before creating a replacement."
        )

    identity = hashlib.sha256(os.fsencode(profile)).hexdigest()[:24]
    prefix = f"firefox-backup-{identity}-"
    final_path = destination / (
        f"{prefix}{time.time_ns()}-{uuid.uuid4().hex}.tar.gz"
    )

    fd, temporary_name = tempfile.mkstemp(
        prefix=".incomplete-firefox-backup-",
        dir=destination,
    )
    temporary = Path(temporary_name)

    try:
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), 0o600)

            with tarfile.open(
                fileobj=output,
                mode="w:gz",
                format=tarfile.PAX_FORMAT,
                dereference=False,
            ) as archive:
                for entry in entries:
                    before = entry.path.lstat()
                    if stat_signature(before) != entry.signature:
                        raise SafetyError(
                            f"Profile changed before backup: {entry.path}"
                        )

                    relative = entry.path.relative_to(profile)
                    archive_name = Path(profile.name) / relative

                    # Do not recurse here: use only the inspected inventory.
                    archive.add(
                        entry.path,
                        arcname=archive_name.as_posix(),
                        recursive=False,
                    )

                    if stat_signature(entry.path.lstat()) != entry.signature:
                        raise SafetyError(
                            f"Profile changed during backup: {entry.path}"
                        )

            output.flush()
            os.fsync(output.fileno())

        # Recheck topology and source metadata before publishing the backup.
        # This detects ordinary concurrent changes, but is not protection
        # against a hostile process with this user's privileges.
        reject_nested_profile_mounts(profile)

        for entry in entries:
            if stat_signature(entry.path.lstat()) != entry.signature:
                raise SafetyError(
                    f"Profile changed during backup: {entry.path}"
                )

        os.replace(temporary, final_path)
        fsync_directory(destination)
    finally:
        temporary.unlink(missing_ok=True)

    LOGGER.info("Backup created: %s", final_path)

    filename_pattern = re.compile(
        re.escape(prefix) + r"\d+-[0-9a-f]{32}\.tar\.gz"
    )
    previous: list[Path] = []

    for candidate in destination.iterdir():
        if candidate == final_path:
            continue

        if filename_pattern.fullmatch(candidate.name) is None:
            continue

        info = candidate.lstat()
        if (
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.getuid()
            and info.st_nlink == 1
            and not info.st_mode & 0o077
        ):
            previous.append(candidate)

    previous.sort(
        key=lambda candidate: candidate.stat().st_mtime_ns,
        reverse=True,
    )

    for obsolete in previous[MAX_BACKUPS_PER_PROFILE - 1:]:
        try:
            obsolete.unlink()
            LOGGER.info("Rotated old backup: %s", obsolete.name)
        except OSError as error:
            LOGGER.warning("Could not rotate %s: %s", obsolete, error)

    fsync_directory(destination)
    return final_path


def apply_profile(plan: ProfilePlan) -> None:
    verify_plan_unchanged(plan)

    state_path = plan.profile / STATE_FILENAME
    user_path = plan.profile / "user.js"
    prefs_path = plan.profile / "prefs.js"

    if plan.new_state is not None:
        # Persist rollback information before applying any policy.
        replace_text(state_path, plan.old_state, plan.new_state)
        replace_text(prefs_path, plan.old_prefs_js, plan.new_prefs_js)
        replace_text(user_path, plan.old_user_js, plan.new_user_js)

        state_data = json.loads(plan.new_state)

        if "migration_user_js" in state_data:
            final_state = encode_state(
                state_data["original_user_js"],
                state_data["baseline_prefs"],
            )
            replace_text(state_path, plan.new_state, final_state)
    else:
        # Restore preference data before discarding rollback information.
        replace_text(prefs_path, plan.old_prefs_js, plan.new_prefs_js)
        replace_text(user_path, plan.old_user_js, plan.new_user_js)
        replace_text(state_path, plan.old_state, None)

    LOGGER.info("Updated profile: %s", plan.profile)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Conservative Firefox HTTP cache-policy management with profile "
            "locking, private backups, and saved preference rollback."
        ),
        epilog=(
            "No package installation, process killing, PSD configuration, "
            "database vacuuming, or forced acceleration is performed."
        ),
    )

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--cache-mode",
        choices=("memory", "default"),
        help=(
            "'memory': disable the HTTP disk cache and enable the memory "
            "cache without setting its capacity. "
            "'default': restore the baseline saved by this implementation."
        ),
    )
    action.add_argument(
        "--disable",
        action="store_true",
        help="Restore the baseline saved by this implementation.",
    )

    parser.add_argument(
        "--profile",
        type=Path,
        action="append",
        default=[],
        help=(
            "Initialized profile directory. May be repeated. Explicit paths "
            "replace automatic profiles.ini discovery."
        ),
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        help=(
            "Backup directory outside all selected profiles. "
            "Cross-filesystem destinations are allowed with a warning. "
            "Use a roomy destination when the profile volume lacks space."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Inspect and report only. Do not acquire/create profile locks, "
            "create directories, back up, or write configuration."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Include debug diagnostics on failure.",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if sys.version_info < (3, 12):
        parser.error("Python 3.12 or newer is required.")

    if not sys.platform.startswith("linux"):
        parser.error("This implementation supports Linux only.")

    if os.getuid() == 0 or os.geteuid() == 0:
        parser.error("Run as the Firefox profile owner, not root or through sudo.")

    if os.getuid() != os.geteuid() or os.getgid() != os.getegid():
        parser.error("Elevated or mismatched real/effective identities are unsupported.")

    memory_mode = not args.disable and args.cache_mode == "memory"

    LOGGER.info("Firefox conservative HTTP cache-policy manager")
    LOGGER.info(
        "Requested action: %s",
        "memory cache policy" if memory_mode else "restore saved baseline",
    )

    try:
        require_tools()
        require_inactive_maintenance_services()
        profiles = find_firefox_profiles(args.profile)

        LOGGER.info("Selected %d profile(s).", len(profiles))
        for profile in profiles:
            LOGGER.info("  %s", profile)

        if args.dry_run:
            LOGGER.warning(
                "Dry run does not acquire profile locks. Close Firefox for "
                "a more reliable inspection; live reads are not a snapshot."
            )

            for profile in profiles:
                plan = prepare_profile(profile, memory_mode)

                if not plan.changed:
                    LOGGER.info("[Dry run] No change: %s", profile)
                    continue

                destination = backup_destination(
                    profile,
                    args.backup_dir,
                    profiles,
                    create=False,
                )

                # Read-only inventory validates the intended backup source.
                entries, size = inspect_backup_tree(profile)

                LOGGER.info(
                    "[Dry run] Would back up %d entries, approximately "
                    "%.1f MiB of file data, to %s",
                    len(entries),
                    size / 1024**2,
                    destination,
                )
                LOGGER.info("[Dry run] Would update: %s", profile)

            LOGGER.info(
                "Dry run completed. Disk-space sufficiency and exclusive "
                "access have not been established."
            )
            return 0

        with ExitStack() as stack:
            # Stable ordering and inode deduplication avoid acquiring the
            # same POSIX lock twice under different registered paths.
            for profile in profiles:
                stack.enter_context(locked_profile(profile))

            require_inactive_maintenance_services()

            plans = [
                prepare_profile(profile, memory_mode)
                for profile in profiles
            ]
            changes = [plan for plan in plans if plan.changed]

            if not changes:
                LOGGER.info(
                    "No changes needed. With no saved state, 'default' and "
                    "'--disable' do not reset unrelated Firefox settings."
                )
                return 0

            # Validate all destinations before creating any backup.
            destinations = {
                plan.profile: backup_destination(
                    plan.profile,
                    args.backup_dir,
                    profiles,
                    create=False,
                )
                for plan in changes
            }

            # Back up every affected profile before the first configuration
            # change. Failure can leave completed backups, but no preference
            # changes have occurred yet.
            for plan in changes:
                destination = backup_destination(
                    plan.profile,
                    destinations[plan.profile],
                    profiles,
                    create=True,
                )
                backup_profile(plan.profile, destination)

            # Detect noncooperating maintenance edits during backup.
            require_inactive_maintenance_services()
            for plan in changes:
                verify_plan_unchanged(plan)

            for plan in changes:
                apply_profile(plan)

        LOGGER.info("Requested cache-policy changes completed.")
        LOGGER.info("Start Firefox normally to load the resulting preferences.")

        if memory_mode:
            LOGGER.info(
                "The memory-cache policy is configured. Any legacy optimizer "
                "migration also removed the listed legacy preference overrides. "
                "History, cookies, site storage, downloads, and other profile "
                "data can still be written to disk."
            )

        return 0

    except KeyboardInterrupt:
        LOGGER.error(
            "Interrupted. Some writes may have completed. Retain backups "
            "and rollback state; resolve the interruption and rerun."
        )
        return 130

    except Exception as error:
        LOGGER.error("%s", error)
        LOGGER.debug("Failure details", exc_info=True)
        LOGGER.error(
            "Operation did not complete successfully. If configuration "
            "writes had started, some changes may already be applied. "
            "Retain backups and rollback-state files. Do not interpret "
            "this failure as a completed rollback."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
