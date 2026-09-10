#!/usr/bin/env -S python3 -I
"""Dusky SSHFS manager for Arch Linux.

Design:
- Run as a normal user.
- Honor OpenSSH configuration for unspecified users and ports.
- Discover mounts through /proc/self/mountinfo without probing remote data.
- Never automatically kill SSHFS processes or replace existing mounts.
- Require explicit lazy unmount.
- Coordinate modifying operations between instances of this program.
- Keep history failures independent of mount success.

Mount locations must be empty local directories without symlink components.
A simple local name is placed below ~/Documents/sshfs.
Use ./name for a path relative to the current working directory.

Status includes SSHFS mounts owned by your UID in the current mount namespace,
including mounts created by other programs.
"""

import os
import sys

if os.geteuid() == 0:
    print(
        "Run this program as your normal user, not as root.\n"
        "Install dependencies separately when necessary.",
        file=sys.stderr,
    )
    sys.exit(1)

import argparse
import contextlib
import fcntl
import hashlib
import ipaddress
import json
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import unquote

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt
    from rich.table import Table
    from rich.text import Text
except ImportError as exc:
    print(
        f"Cannot import Rich: {exc}\n\n"
        "Install the required Arch packages:\n"
        "  sudo pacman -Syu --needed python python-rich sshfs fuse3 openssh",
        file=sys.stderr,
    )
    sys.exit(1)


console = Console()
error_console = Console(stderr=True)

try:
    HOME: Final[Path] = Path.home()
except (RuntimeError, KeyError) as exc:
    print(f"Cannot determine the home directory: {exc}", file=sys.stderr)
    sys.exit(1)

if not HOME.is_absolute():
    print("The home directory must be an absolute path.", file=sys.stderr)
    sys.exit(1)


STATE_FILE: Final[Path] = (
    HOME / ".config/dusky/settings/sshfiles/sshfs"
)
BASE_MOUNT_DIR: Final[Path] = HOME / "Documents/sshfs"

# Independent of the history directory, so unmounting does not require
# writable history/configuration storage.
LOCK_FILE: Final[Path] = Path(
    f"/tmp/dusky-sshfs-{os.getuid()}.lock"
)

MAX_HISTORY: Final[int] = 10
MAX_STATE_BYTES: Final[int] = 1024 * 1024
MAX_DIAGNOSTIC_BYTES: Final[int] = 128 * 1024

INSTALL_COMMAND: Final[str] = (
    "sudo pacman -Syu --needed python python-rich sshfs fuse3 openssh"
)

SSHFS_OPTIONS: Final[tuple[str, ...]] = (
    "reconnect",
    "ServerAliveInterval=15",
    "ServerAliveCountMax=3",
    "ConnectTimeout=10",
    "ConnectionAttempts=1",
    "StrictHostKeyChecking=accept-new",
)

_gui_processes: list[subprocess.Popen[bytes]] = []


class AppError(Exception):
    """An operational error that should be displayed without a traceback."""


def say(message: str, style: str = "") -> None:
    console.print(message, style=style, markup=False)


def warn(message: str) -> None:
    error_console.print(message, style="yellow", markup=False)


def report_error(message: str) -> None:
    error_console.print(message, style="bold red", markup=False)


@contextlib.contextmanager
def operation_lock() -> Iterator[None]:
    """Serialize modifications made by instances of this program.

    This does not coordinate with unrelated mount/unmount programs.
    The lock file must not be deleted after releasing the lock.
    """
    with LOCK_FILE.open("a", encoding="utf-8") as handle:
        try:
            fcntl.flock(
                handle.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as exc:
            raise AppError(
                "Another instance is performing a mount, unmount, "
                "or history update. Wait for it to finish."
            ) from exc

        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def find_executable(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return os.path.abspath(found)

    candidate = Path("/usr/bin") / name
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)

    return None


def require_executable(name: str) -> str:
    found = find_executable(name)
    if found is None:
        raise AppError(
            f"Required executable not found: {name}\n\n"
            f"Install the required packages:\n  {INSTALL_COMMAND}"
        )
    return found


@dataclass(frozen=True)
class ParsedTarget:
    user: str | None
    host: str
    port: int | None
    remote_path: str

    @property
    def authority(self) -> str:
        prefix = f"{self.user}@" if self.user else ""
        return f"{prefix}{self.host}"

    @property
    def sshfs_target_spec(self) -> str:
        return f"{self.authority}:{self.remote_path}"

    @property
    def canonical_string(self) -> str:
        # Explicit port 22 must remain explicit: ~/.ssh/config may
        # otherwise select a different port.
        if self.port is not None:
            return (
                f"{self.authority}:{self.port}:{self.remote_path}"
            )

        path = self.remote_path

        # Numeric relative paths must not be reparsed as port syntax.
        if re.match(r"^[0-9]+(?:$|[:/])", path):
            path = "./" + path

        return f"{self.authority}:{path}"


def parse_port(value: str) -> int:
    # Bound the length before converting to int.
    if not re.fullmatch(r"[0-9]{1,5}", value):
        raise ValueError("Invalid port")

    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError("Port must be in the range 1..65535")

    return port


def decode_uri_component(value: str) -> str:
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise ValueError("Malformed URI percent escape")

    return unquote(value, encoding="utf-8", errors="strict")


def validate_user(user: str | None) -> bool:
    if user is None:
        return True

    return (
        bool(user)
        and user.isprintable()
        and not user.startswith("-")
        and not any(
            character.isspace() or character in "@:/\\[]"
            for character in user
        )
    )


def validate_host(host: str) -> bool:
    if not host or not host.isprintable() or host.startswith("-"):
        return False

    if host.startswith("["):
        if not host.endswith("]"):
            return False

        try:
            ipaddress.IPv6Address(host[1:-1])
        except ValueError:
            return False

        return True

    return not any(
        character.isspace() or character in "@:/\\[]"
        for character in host
    )


def split_authority(value: str) -> tuple[str | None, str, str]:
    """Parse user/host first, leaving the remote-path suffix untouched."""
    match = re.fullmatch(
        r"(?:(?P<user>[^@:/\s]+)@)?"
        r"(?P<host>\[[^\]]+\]|[^:/@\s]+)"
        r"(?P<tail>.*)",
        value,
    )

    if match is None:
        raise ValueError("Invalid SSH authority")

    return match["user"], match["host"], match["tail"]


def is_unbracketed_ipv6(value: str) -> bool:
    """Reject a bare IPv6 literal rather than interpreting it as host:path."""
    candidate = value

    if "@" in candidate:
        candidate = candidate.split("@", 1)[1]

    if candidate.startswith("["):
        return False

    try:
        ipaddress.IPv6Address(candidate)
    except ValueError:
        return False

    return True


def parse_target(raw: str) -> ParsedTarget | None:
    """Parse supported SSHFS target syntax.

    Supported:
      host
      user@host
      host:/absolute/path
      host:relative/path
      host:2222
      host:2222:/absolute/path
      host:2222:relative/path
      host:2222/absolute/path
      ssh -p 2222 user@host:/path
      ssh -p2222 user@host:/path
      ssh://user@host:2222/path

    Rules:
    - IPv6 literals must be bracketed.
    - Omitted users and ports are left to OpenSSH configuration.
    - Omitted remote paths mean "/".
    - Numeric relative paths require "./", e.g. host:./123.
    - URI usernames and paths support UTF-8 percent escapes.
    - URI '?' and '#' characters must be percent-encoded.
    - Unsupported SSH command flags are rejected, not silently discarded.
    - This is not a general shell-command parser. Embedded shell quoting
      is not interpreted.
    """
    target = raw.strip()

    if not target or not target.isprintable():
        return None

    if target.startswith("ssh "):
        target = target[4:].lstrip()

    if not target:
        return None

    flag_port: int | None = None

    try:
        if target.startswith("-"):
            match = re.fullmatch(
                r"-p(?: +([0-9]+)|([0-9]+)) +(.+)",
                target,
            )
            if match is None:
                return None

            flag_port = parse_port(match[1] or match[2])
            target = match[3]

        inline_port: int | None = None

        if target.lower().startswith("ssh://"):
            rest = target[6:]

            if "?" in rest or "#" in rest:
                return None

            authority, separator, path_part = rest.partition("/")

            if is_unbracketed_ipv6(authority):
                return None

            user, host, tail = split_authority(authority)

            if tail:
                if not tail.startswith(":"):
                    return None
                inline_port = parse_port(tail[1:])

            if user is not None:
                user = decode_uri_component(user)

            host = decode_uri_component(host)
            remote_path = (
                decode_uri_component("/" + path_part)
                if separator
                else "/"
            )

        else:
            if is_unbracketed_ipv6(target):
                return None

            user, host, tail = split_authority(target)

            if tail and not tail.startswith(":"):
                return None

            suffix = tail[1:] if tail else ""
            remote_path = "/"

            if suffix:
                if re.fullmatch(r"[0-9]+", suffix):
                    inline_port = parse_port(suffix)
                else:
                    port_path = re.fullmatch(
                        r"([0-9]+)([:/])(.*)",
                        suffix,
                    )

                    if port_path is None:
                        remote_path = suffix
                    else:
                        inline_port = parse_port(port_path[1])

                        if port_path[2] == "/":
                            remote_path = "/" + port_path[3]
                        else:
                            remote_path = port_path[3] or "/"

        if (
            flag_port is not None
            and inline_port is not None
            and flag_port != inline_port
        ):
            return None

        port = (
            flag_port
            if flag_port is not None
            else inline_port
        )

        if not validate_user(user) or not validate_host(host):
            return None

        if not remote_path or not remote_path.isprintable():
            return None

        return ParsedTarget(
            user=user,
            host=host,
            port=port,
            remote_path=remote_path,
        )

    except (ValueError, UnicodeError):
        return None


def require_target(raw: str) -> ParsedTarget:
    target = parse_target(raw)

    if target is None:
        raise AppError(
            "Invalid SSH target.\n\n"
            "Examples:\n"
            "  user@host:/data\n"
            "  host:2222:/data\n"
            "  ssh -p 2222 host:/data\n"
            "  ssh://host:2222/data\n"
            "  user@[2001:db8::1]:/data\n\n"
            "Only the SSH -p convenience option is supported here. "
            "Use ~/.ssh/config for other SSH options.\n"
            "Use host:./123 for a numeric relative directory."
        )

    return target


def local_path(raw: str | Path) -> Path:
    """Return an absolute, lexically normalized local path.

    No symlink resolution is performed: unmounting must not traverse a
    potentially disconnected filesystem.

    Simple names select a folder below BASE_MOUNT_DIR.
    """
    value = os.fspath(raw).strip()

    if not value:
        raise AppError("The local mount path cannot be empty.")

    if "\x00" in value:
        raise AppError("The local mount path contains a NUL character.")

    try:
        expanded = Path(value).expanduser()
    except RuntimeError as exc:
        raise AppError(f"Cannot expand local path: {exc}") from exc

    if (
        not expanded.is_absolute()
        and "/" not in value
        and value not in (".", "..")
    ):
        expanded = BASE_MOUNT_DIR / expanded

    return Path(os.path.abspath(expanded))


def derive_mount_point(
    target: ParsedTarget,
    custom_path: str | None = None,
) -> Path:
    if custom_path is not None and custom_path.strip():
        return local_path(custom_path)

    label = re.sub(
        r"[^A-Za-z0-9_.-]+",
        "_",
        target.host,
    ).strip("._-")
    label = (label or "host")[:40]

    digest = hashlib.sha256(
        target.canonical_string.encode("utf-8")
    ).hexdigest()[:16]

    return local_path(
        BASE_MOUNT_DIR / f"{label}-{digest}"
    )


@dataclass(frozen=True)
class MountRecord:
    mount_id: int
    source: str
    mount_point: Path
    fstype: str
    owner_uid: int | None


def unescape_mount_field(value: bytes) -> str:
    def replace(match: re.Match[bytes]) -> bytes:
        return bytes([int(match[1], 8)])

    decoded = re.sub(
        rb"\\([0-3][0-7]{2})",
        replace,
        value,
    )
    return os.fsdecode(decoded)


def mount_records() -> list[MountRecord]:
    """Read kernel metadata without accessing mounted filesystem contents."""
    records: list[MountRecord] = []

    try:
        with open("/proc/self/mountinfo", "rb") as handle:
            for line in handle:
                before, separator, after = (
                    line.rstrip(b"\n").partition(b" - ")
                )

                fields = before.split()
                filesystem = after.split()

                if (
                    not separator
                    or len(fields) < 6
                    or len(filesystem) < 3
                ):
                    raise AppError(
                        "Malformed /proc/self/mountinfo entry."
                    )

                options = (
                    fields[5].split(b",")
                    + filesystem[2].split(b",")
                )

                owner_uid = None
                for option in options:
                    if option.startswith(b"user_id="):
                        owner_uid = int(
                            option.split(b"=", 1)[1]
                        )
                        break

                records.append(
                    MountRecord(
                        mount_id=int(fields[0]),
                        source=unescape_mount_field(filesystem[1]),
                        mount_point=Path(
                            unescape_mount_field(fields[4])
                        ),
                        fstype=unescape_mount_field(filesystem[0]),
                        owner_uid=owner_uid,
                    )
                )

    except (OSError, ValueError) as exc:
        raise AppError(
            f"Cannot read mount metadata: {exc}"
        ) from exc

    return records


def mounts_at(
    path: Path,
    records: list[MountRecord] | None = None,
) -> list[MountRecord]:
    if records is None:
        records = mount_records()

    return [
        record
        for record in records
        if record.mount_point == path
    ]


def descendant_mounts(
    path: Path,
    records: list[MountRecord],
) -> list[MountRecord]:
    return [
        record
        for record in records
        if (
            record.mount_point != path
            and record.mount_point.is_relative_to(path)
        )
    ]


def active_mounts() -> list[MountRecord]:
    return sorted(
        (
            record
            for record in mount_records()
            if (
                record.fstype == "fuse.sshfs"
                and record.owner_uid == os.getuid()
            )
        ),
        key=lambda record: (
            str(record.mount_point),
            record.mount_id,
        ),
    )


def render_banner() -> None:
    console.print(
        Panel.fit(
            Text(
                "Dusky SSH File System Mounter\n"
                "Your SSHFS mounts in the current mount namespace",
                style="bold cyan",
                justify="center",
            ),
            border_style="cyan",
        )
    )


def render_mounts(records: list[MountRecord]) -> None:
    if not records:
        say(
            "No SSHFS mounts owned by your UID are registered.",
            "dim",
        )
        return

    table = Table(
        title="Registered SSHFS mounts",
        expand=False,
    )
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Remote source")
    table.add_column("Local mount point", style="yellow")
    table.add_column("State", style="green")

    for index, record in enumerate(records, 1):
        table.add_row(
            str(index),
            Text(record.source),
            Text(str(record.mount_point)),
            "Registered",
        )

    console.print(table)
    say(
        "Registered means present in the kernel mount table; "
        "remote responsiveness is not probed.",
        "dim",
    )


def render_history(history: list[str]) -> None:
    if not history:
        return

    table = Table(title="Recent targets", expand=False)
    table.add_column("#", justify="right", style="cyan")
    table.add_column("Target")

    for index, target in enumerate(history, 1):
        table.add_row(str(index), Text(target))

    console.print(table)


def clean_history(items: list[object]) -> list[str]:
    result: list[str] = []

    for item in items:
        if not isinstance(item, str):
            continue

        target = parse_target(item)
        if target is None:
            continue

        canonical = target.canonical_string

        if canonical not in result:
            result.append(canonical)

        if len(result) >= MAX_HISTORY:
            break

    return result


def read_history() -> list[str]:
    """Read history strictly, so write callers do not erase unreadable data."""
    try:
        metadata = STATE_FILE.stat()
    except FileNotFoundError:
        return []

    if not stat.S_ISREG(metadata.st_mode):
        raise AppError(
            f"History is not a regular file: {STATE_FILE}"
        )

    with STATE_FILE.open("rb") as handle:
        raw = handle.read(MAX_STATE_BYTES + 1)

    if len(raw) > MAX_STATE_BYTES:
        raise AppError(
            f"History exceeds {MAX_STATE_BYTES} bytes: {STATE_FILE}"
        )

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise AppError(
            f"History is corrupt: {STATE_FILE}: {exc}"
        ) from exc

    if isinstance(data, dict):
        if "history" not in data:
            raise AppError(
                f"History object has no 'history' field: {STATE_FILE}"
            )
        items = data["history"]
    elif isinstance(data, list):
        items = data
    else:
        raise AppError(
            f"Invalid history structure: {STATE_FILE}"
        )

    if not isinstance(items, list):
        raise AppError(
            f"The history field must be a list: {STATE_FILE}"
        )

    return clean_history(items)


def load_history() -> list[str]:
    try:
        return read_history()
    except (AppError, OSError) as exc:
        warn(f"Cannot load history; continuing without it:\n{exc}")
        return []


def write_history(history: list[str]) -> None:
    """Atomically replace the resolved history destination."""
    try:
        destination = STATE_FILE.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise AppError(
            f"Cannot resolve history destination: {exc}"
        ) from exc

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
        mode=0o700,
    )

    try:
        metadata = destination.stat()
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(metadata.st_mode):
            raise AppError(
                f"History destination is not a regular file: "
                f"{destination}"
            )

    payload = json.dumps(
        {"history": history},
        indent=4,
        ensure_ascii=True,
    ) + "\n"

    fd, temporary = tempfile.mkstemp(
        prefix=destination.name + ".",
        suffix=".tmp",
        dir=destination.parent,
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temporary, destination)

        # Directory fsync is best-effort because not every filesystem
        # supports it. The file contents were synced before replacement.
        with contextlib.suppress(OSError):
            directory_fd = os.open(
                destination.parent,
                os.O_DIRECTORY | os.O_CLOEXEC,
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def remember_target_locked(target: ParsedTarget) -> None:
    """Update history while the caller holds operation_lock.

    Failure does not change the result of a successful mount.
    Unreadable/corrupt existing history is left untouched.
    """
    try:
        history = clean_history(
            [target.canonical_string, *read_history()]
        )
        write_history(history)

    except (AppError, OSError) as exc:
        warn(
            "The mount succeeded, but history could not be saved.\n"
            f"{exc}\n"
            "An unreadable or corrupt existing history file was not "
            "intentionally overwritten."
        )


def prepare_mount_directory(path: Path) -> None:
    """Prepare an empty directory without traversing registered FUSE mounts."""
    records = mount_records()

    if path == Path(path.anchor):
        raise AppError(
            "The filesystem root cannot be used as a mount location."
        )

    if mounts_at(path, records):
        raise AppError(
            f"{path} is already a mount point.\n"
            "Unmount it explicitly or choose another directory."
        )

    children = descendant_mounts(path, records)
    if children:
        raise AppError(
            f"Other mounts exist below {path}.\n"
            "Choose another directory or unmount those children first."
        )

    registered: dict[Path, list[MountRecord]] = {}
    for record in records:
        registered.setdefault(record.mount_point, []).append(record)

    current = Path(path.anchor)

    for component in path.parts[1:]:
        current /= component

        for existing in registered.get(current, []):
            if (
                existing.fstype == "fuse"
                or existing.fstype.startswith("fuse.")
            ):
                raise AppError(
                    "Mount locations inside an existing FUSE mount "
                    f"are not supported:\n{current}"
                )

        try:
            metadata = current.lstat()
        except FileNotFoundError:
            # exist_ok handles another local process creating the
            # directory between lstat and mkdir. Revalidate afterward.
            current.mkdir(mode=0o700, exist_ok=True)
            metadata = current.lstat()

        if stat.S_ISLNK(metadata.st_mode):
            raise AppError(
                "Symlink components are not supported in mount "
                f"locations:\n{current}\n"
                "Use the actual local directory path."
            )

        if not stat.S_ISDIR(metadata.st_mode):
            raise AppError(f"Not a directory: {current}")

    if not os.access(path, os.W_OK | os.X_OK):
        raise AppError(
            f"Mount directory is not writable/searchable: {path}"
        )

    with os.scandir(path) as entries:
        if next(entries, None) is not None:
            raise AppError(
                f"Mount directory is not empty: {path}\n"
                "Choose an empty directory."
            )

    # Recheck kernel metadata immediately before mounting.
    # The advisory lock cannot prevent unrelated programs from racing us.
    latest = mount_records()

    if mounts_at(path, latest) or descendant_mounts(path, latest):
        raise AppError(
            f"A mount appeared at or below {path} during preparation. "
            "Refusing to mount over it."
        )

    for record in latest:
        if (
            path.is_relative_to(record.mount_point)
            and (
                record.fstype == "fuse"
                or record.fstype.startswith("fuse.")
            )
        ):
            raise AppError(
                "A FUSE mount appeared in the local path during "
                "preparation. Recheck status before retrying."
            )


def check_fuse_device() -> None:
    """Check actual device access, including restrictions os.access misses."""
    try:
        fd = os.open(
            "/dev/fuse",
            os.O_RDWR | os.O_CLOEXEC,
        )
    except FileNotFoundError as exc:
        raise AppError(
            "/dev/fuse is missing. Check FUSE kernel support and "
            "module loading."
        ) from exc
    except OSError as exc:
        raise AppError(
            f"Cannot open /dev/fuse: {exc}"
        ) from exc

    try:
        if not stat.S_ISCHR(os.fstat(fd).st_mode):
            raise AppError(
                "/dev/fuse is not a character device."
            )
    finally:
        os.close(fd)


def read_diagnostics(handle) -> str:
    """Read a bounded tail of startup output from a regular temporary file."""
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    start = max(0, size - MAX_DIAGNOSTIC_BYTES)
    handle.seek(start)

    output = handle.read(MAX_DIAGNOSTIC_BYTES).decode(
        "utf-8",
        errors="replace",
    )

    if start:
        output = "[Earlier diagnostic output omitted]\n" + output

    return output


def show_mount_failure(
    target: ParsedTarget,
    path: Path,
    returncode: int,
    output: str,
) -> None:
    message = Text()
    message.append(
        f"Target: {target.canonical_string}\n",
        style="cyan",
    )
    message.append(
        f"Local path: {path}\n",
        style="yellow",
    )
    message.append(
        f"Exit code: {returncode}\n\n",
        style="bold red",
    )
    message.append(
        output.strip() or "No diagnostic output was returned."
    )
    message.append(
        "\n\nCheck SSH configuration, authentication, SFTP access, "
        "and local/remote directory permissions.\n"
        "A connection reset alone does not establish a rate-limit "
        "penalty. A retry delay does not prove a penalty has expired.",
        style="dim",
    )

    console.print(
        Panel(
            message,
            title="SSHFS mount failed",
            border_style="red",
            expand=False,
        )
    )


def build_mount_command(
    target: ParsedTarget,
    path: Path,
) -> list[str]:
    sshfs = require_executable("sshfs")
    ssh = require_executable("ssh")
    require_executable("fusermount3")

    ssh_command = [ssh]

    if "SSHPASS" in os.environ:
        sshpass = find_executable("sshpass")

        if sshpass is None:
            raise AppError(
                "SSHPASS is set, but sshpass is not installed.\n"
                "Install sshpass or unset SSHPASS."
            )

        ssh_command = [sshpass, "-e", ssh]

    # SSHFS/FUSE uses commas as -o separators. Avoid silently breaking
    # an unusual executable path containing a comma.
    if any("," in argument for argument in ssh_command):
        raise AppError(
            "SSH/sshpass executable paths containing commas are not "
            "supported by this command builder. Use the Arch binaries "
            "under /usr/bin."
        )

    options = [
        *SSHFS_OPTIONS,
        f"uid={os.getuid()}",
        f"gid={os.getgid()}",
        f"ssh_command={shlex.join(ssh_command)}",
    ]

    command = [sshfs]

    for option in options:
        command.extend(("-o", option))

    if target.port is not None:
        command.extend(("-p", str(target.port)))

    command.extend(
        (target.sshfs_target_spec, str(path))
    )

    return command


def mount_target(
    target: ParsedTarget,
    custom_path: str | None = None,
) -> bool:
    path = derive_mount_point(target, custom_path)
    command = build_mount_command(target, path)

    with operation_lock():
        check_fuse_device()
        prepare_mount_directory(path)

        say(
            f"Connecting to {target.canonical_string} ...",
            "bold cyan",
        )

        # Do not impose a short overall timeout on interactive
        # authentication. OpenSSH's normal terminal password/passphrase
        # prompts use /dev/tty.
        #
        # A regular temporary file avoids waiting for pipe EOF if a
        # background process inherits stderr.
        with tempfile.TemporaryFile(mode="w+b") as diagnostics:
            process = subprocess.run(
                command,
                stderr=diagnostics,
                check=False,
            )
            output = read_diagnostics(diagnostics)

        if process.returncode != 0:
            show_mount_failure(
                target,
                path,
                process.returncode,
                output,
            )

            if mounts_at(path):
                warn(
                    "A mount is nevertheless registered at this path. "
                    "Inspect it and unmount explicitly before retrying."
                )

            return False

        deadline = time.monotonic() + 5.0

        while True:
            found = mounts_at(path)

            if (
                len(found) == 1
                and found[0].fstype == "fuse.sshfs"
                and found[0].owner_uid == os.getuid()
            ):
                if output.strip():
                    warn(output.strip())

                # Keep mount + read/merge/write history inside the same
                # advisory lock. History errors do not undo the mount.
                remember_target_locked(target)

                console.print(
                    Panel.fit(
                        Text(
                            "Filesystem mounted\n"
                            f"Remote: {target.canonical_string}\n"
                            f"Local:  {path}",
                            style="green",
                        ),
                        border_style="green",
                    )
                )
                return True

            if found or time.monotonic() >= deadline:
                break

            time.sleep(0.1)

        show_mount_failure(
            target,
            path,
            1,
            output
            + "\nsshfs returned success, but a single SSHFS mount "
            "owned by your UID was not found at the expected path.",
        )
        return False


def unmount_one(path: Path, *, lazy: bool) -> bool:
    """Unmount one exact mount-table path. Caller holds operation_lock."""
    records = mount_records()
    matches = mounts_at(path, records)

    if not matches:
        say(
            f"No mount is registered at this exact path: {path}",
            "yellow",
        )
        return True

    if len(matches) != 1:
        raise AppError(
            f"Multiple mounts are stacked at {path}.\n"
            "Resolve the stacked mounts explicitly before using "
            "this program."
        )

    record = matches[0]

    if record.fstype != "fuse.sshfs":
        raise AppError(
            f"{path} is mounted as {record.fstype}, not fuse.sshfs."
        )

    if record.owner_uid != os.getuid():
        raise AppError(
            f"This SSHFS mount is not owned by your UID: {path}"
        )

    if descendant_mounts(path, records):
        raise AppError(
            f"Other mounts remain below {path}.\n"
            "Unmount those children first. This program will not "
            "detach them implicitly with their parent."
        )

    fusermount = require_executable("fusermount3")
    command = [fusermount, "-u"]

    if lazy:
        command.append("-z")

    command.extend(("--", str(path)))

    say(
        f"{'Lazily detaching' if lazy else 'Unmounting'} {path} ...",
        "cyan",
    )

    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AppError(
            f"fusermount3 timed out for {path}.\n"
            "The result is uncertain; recheck status before retrying."
        ) from exc

    deadline = time.monotonic() + 2.0

    while mounts_at(path):
        if time.monotonic() >= deadline:
            details = (
                process.stderr or process.stdout
            ).strip()

            report_error(
                details
                or f"The mount remains registered at {path}."
            )

            if not lazy:
                warn(
                    "No lazy fallback was performed. Close users of "
                    "the mount or explicitly request lazy unmount."
                )

            return False

        time.sleep(0.1)

    if process.returncode != 0:
        warn(
            "fusermount3 reported an error, but no mount remains "
            "registered at this path. Another process may have "
            "unmounted it."
        )

        details = (
            process.stderr or process.stdout
        ).strip()

        if details:
            warn(details)

    if lazy:
        say(
            f"Detached from the mount namespace: {path}",
            "green",
        )
        warn(
            "Lazy detach does not guarantee completed pending I/O or "
            "immediate process cleanup. Existing references may remain."
        )
    else:
        say(f"Unmounted: {path}", "green")

    return True


def unmount_targets(
    path: str | Path | None = None,
    *,
    all_mounts: bool = False,
    lazy: bool = False,
) -> bool:
    with operation_lock():
        if all_mounts:
            records = active_mounts()

            # Children first; one attempt per distinct mount point.
            paths = sorted(
                {record.mount_point for record in records},
                key=lambda item: (len(item.parts), str(item)),
                reverse=True,
            )

        elif path is not None:
            paths = [local_path(path)]

        else:
            records = active_mounts()
            paths = sorted(
                {record.mount_point for record in records},
                key=str,
            )

            if len(paths) > 1:
                raise AppError(
                    "Several SSHFS mounts are registered.\n"
                    "Specify a path or use --unmount all."
                )

        if not paths:
            say(
                "No matching SSHFS mounts are registered.",
                "yellow",
            )
            return True

        success = True

        for target_path in paths:
            try:
                if not unmount_one(target_path, lazy=lazy):
                    success = False

            except (AppError, OSError) as exc:
                report_error(str(exc))
                success = False

        return success


def reap_gui_processes() -> None:
    """Reap completed file-manager launcher processes."""
    _gui_processes[:] = [
        process
        for process in _gui_processes
        if process.poll() is None
    ]


def open_mounts(paths: list[Path]) -> None:
    opener = require_executable("xdg-open")
    reap_gui_processes()

    for path in paths:
        process = subprocess.Popen(
            [opener, str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _gui_processes.append(process)

    say("File-manager launch requested.", "green")


def pause() -> None:
    Prompt.ask("Press Enter to continue", default="")


def ask_mount_path(
    target: ParsedTarget,
    current: str | None = None,
) -> str:
    default = (
        str(local_path(current))
        if current is not None
        else str(derive_mount_point(target))
    )

    value = Prompt.ask(
        "Local mount path",
        default=default,
    ).strip()

    # Validate and normalize immediately, while still in the edit flow.
    return str(local_path(value or default))


def interactive_mount(
    target: ParsedTarget,
    history: list[str],
) -> list[str]:
    custom_path = ask_mount_path(target)

    while True:
        try:
            success = mount_target(target, custom_path)
        except (AppError, OSError) as exc:
            report_error(str(exc))
            success = False

        if success:
            # Preserve a successful target in this session even if
            # persistent history storage was unavailable.
            history = clean_history(
                [target.canonical_string, *history]
            )

            if find_executable("xdg-open") and Confirm.ask(
                "Open the mounted folder in a file manager?",
                default=False,
            ):
                try:
                    open_mounts(
                        [derive_mount_point(target, custom_path)]
                    )
                except (AppError, OSError) as exc:
                    warn(
                        f"Could not launch the file manager: {exc}"
                    )

            pause()
            return history

        # Stay in this inner loop until the user explicitly requests a
        # retry or successfully changes the target/path. Invalid edits
        # must not accidentally retry the previous target.
        while True:
            say(
                "\nr  Retry\n"
                "w  Wait 15 seconds, then retry\n"
                "t  Change target\n"
                "p  Change local mount path\n"
                "0  Return to menu",
                "cyan",
            )

            choice = Prompt.ask(
                "Choose",
                choices=["r", "w", "t", "p", "0"],
                default="0",
            )

            if choice == "0":
                return history

            if choice == "r":
                break

            if choice == "w":
                say(
                    "Waiting 15 seconds before retrying ...",
                    "cyan",
                )
                time.sleep(15)
                break

            if choice == "t":
                raw = Prompt.ask(
                    "SSH target",
                    default=target.canonical_string,
                )

                replacement = parse_target(raw)
                if replacement is None:
                    report_error(
                        "Invalid target. The previous target was not retried."
                    )
                    continue

                try:
                    replacement_path = ask_mount_path(replacement)
                except (AppError, OSError) as exc:
                    report_error(str(exc))
                    continue

                target = replacement
                custom_path = replacement_path
                break

            if choice == "p":
                try:
                    replacement_path = ask_mount_path(
                        target,
                        custom_path,
                    )
                except (AppError, OSError) as exc:
                    report_error(str(exc))
                    continue

                custom_path = replacement_path
                break


def interactive_unmount() -> None:
    records = active_mounts()
    render_mounts(records)

    if not records:
        return

    choice = Prompt.ask(
        f"Mount number (1-{len(records)}), 'all', or '0' to cancel",
        default="0",
    ).strip().lower()

    if choice == "0":
        return

    if choice == "all":
        if not Confirm.ask(
            "Unmount all SSHFS mounts owned by your UID in this "
            "namespace, including mounts created by other programs?",
            default=False,
        ):
            return

        path = None
        all_mounts = True

    else:
        try:
            index = int(choice)
        except ValueError as exc:
            raise AppError("Invalid mount selection.") from exc

        if not 1 <= index <= len(records):
            raise AppError("Invalid mount selection.")

        path = records[index - 1].mount_point
        all_mounts = False

    lazy = Confirm.ask(
        "Use lazy detach? Choose no for an ordinary unmount",
        default=False,
    )

    unmount_targets(
        path,
        all_mounts=all_mounts,
        lazy=lazy,
    )


def interactive_main() -> int:
    history = load_history()

    while True:
        reap_gui_processes()
        console.clear()
        render_banner()

        records = active_mounts()
        render_mounts(records)
        render_history(history)

        say(
            "\n1  Mount a target\n"
            "2  Mount a recent target\n"
            "3  Unmount\n"
            "4  Refresh\n"
            "o  Open registered mounts in a file manager\n"
            "0  Exit",
            "bold cyan",
        )

        choice = Prompt.ask(
            "Choose",
            default="1",
        ).strip().lower()

        if choice in ("0", "q", "quit", "exit"):
            return 0

        try:
            if choice == "1":
                raw = Prompt.ask(
                    "SSH target (omit user/port to use SSH configuration)",
                    default=history[0] if history else "",
                )

                target = require_target(raw)
                history = interactive_mount(target, history)

            elif choice == "2":
                if not history:
                    say("No recent targets.", "yellow")
                    pause()
                    continue

                raw_index = Prompt.ask(
                    f"Recent target number (1-{len(history)})",
                    default="1",
                )

                try:
                    index = int(raw_index)
                except ValueError as exc:
                    raise AppError(
                        "Invalid history selection."
                    ) from exc

                if not 1 <= index <= len(history):
                    raise AppError(
                        "Invalid history selection."
                    )

                target = require_target(history[index - 1])
                history = interactive_mount(target, history)

            elif choice == "3":
                interactive_unmount()
                pause()

            elif choice in ("4", "r", "refresh"):
                # Import targets saved by other instances while keeping
                # successful in-memory entries if persistence failed.
                history = clean_history(
                    [*load_history(), *history]
                )

            elif choice == "o":
                if records:
                    open_mounts(
                        sorted(
                            {
                                record.mount_point
                                for record in records
                            },
                            key=str,
                        )
                    )
                else:
                    say(
                        "No registered SSHFS mounts to open.",
                        "yellow",
                    )

                pause()

            else:
                report_error("Invalid menu choice.")
                pause()

        except (AppError, OSError) as exc:
            report_error(str(exc))
            pause()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Manage SSHFS mounts owned by your UID in the current "
            "mount namespace. Status does not probe remote "
            "filesystem responsiveness."
        ),
        epilog=(
            "Examples:\n"
            "  %(prog)s user@host:/data\n"
            "  %(prog)s host:2222:/data server\n"
            "  %(prog)s 'ssh -p 2222 host:/data' ./server\n"
            "  %(prog)s 'ssh://user@[2001:db8::1]:2222/data'\n"
            "  %(prog)s --status\n"
            "  %(prog)s --unmount server\n"
            "  %(prog)s --unmount all --lazy\n\n"
            "A simple local name is below ~/Documents/sshfs.\n"
            "./name is relative to the current working directory.\n"
            "Mount paths must not contain symlink components; use the "
            "actual local directory path.\n"
            "Use ~/.ssh/config for SSH options other than the supported "
            "target-string -p convenience syntax.\n"
            "Omitted remote paths mean '/'; use host:. for the remote "
            "login directory.\n"
            "'all' includes your SSHFS mounts created outside this program."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    mode = parser.add_mutually_exclusive_group()

    mode.add_argument(
        "-s",
        "--status",
        action="store_true",
        help="list your registered SSHFS mounts",
    )

    mode.add_argument(
        "-u",
        "--unmount",
        nargs="?",
        const="",
        metavar="PATH",
        help="unmount PATH, the sole mount, or 'all'",
    )

    parser.add_argument(
        "--lazy",
        action="store_true",
        help="explicitly request lazy detach with --unmount",
    )

    parser.add_argument(
        "target",
        nargs="?",
        help="SSH target",
    )

    parser.add_argument(
        "mount_path",
        nargs="?",
        help="local mount directory",
    )

    return parser


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()

    if args.lazy and args.unmount is None:
        parser.error("--lazy requires --unmount")

    if (
        args.status or args.unmount is not None
    ) and args.target is not None:
        parser.error(
            "target arguments cannot be combined with status/unmount"
        )

    if args.status:
        render_banner()
        render_mounts(active_mounts())
        return 0

    if args.unmount is not None:
        if args.unmount.lower() == "all":
            success = unmount_targets(
                all_mounts=True,
                lazy=args.lazy,
            )
        else:
            success = unmount_targets(
                args.unmount or None,
                lazy=args.lazy,
            )

        return 0 if success else 1

    if args.target is not None:
        target = require_target(args.target)

        return (
            0
            if mount_target(target, args.mount_path)
            else 1
        )

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.error(
            "interactive mode requires a terminal; provide a command"
        )

    return interactive_main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())

    except KeyboardInterrupt:
        report_error(
            "\nInterrupted. A mount/unmount operation may already "
            "have completed. Use --status to inspect registered mounts."
        )
        raise SystemExit(130)

    except EOFError:
        raise SystemExit(0)

    except BrokenPipeError:
        with contextlib.suppress(OSError):
            sys.stdout.close()
        raise SystemExit(0)

    except (AppError, OSError) as exc:
        report_error(str(exc))
        raise SystemExit(1)
