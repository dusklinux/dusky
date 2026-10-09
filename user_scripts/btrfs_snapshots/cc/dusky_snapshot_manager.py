#!/usr/bin/env python3
"""
Dusky Btrfs and Snapper controller.

Target: Arch Linux, kernel >= 7.3, Python >= 3.15, btrfs-progs >= 7.1,
snapper >= 0.13.2, util-linux >= 2.42.4, systemd >= 262 and fzf >= 0.74.4.
Subvolume commands use the installed btrfs text interface; these commands do
not support --format=json. Snapper and findmnt use their JSON interfaces.

Restore exchanges are atomic per subvolume, not across multiple subvolumes.
Durable journals support explicit --recover / --recover --abort after an
interruption. No automatic boot recovery is installed. Reboot is required
for root restores; the currently mounted root continues serving the old state.
"""

from __future__ import annotations

lazy import argparse
lazy import base64
lazy import binascii
lazy import ctypes
import errno
import fcntl
lazy import hashlib
lazy import json
import logging
import logging.handlers
import os
import pwd
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
lazy import unicodedata
lazy import uuid as uuidlib
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any, Final, NoReturn

type JSONDict = dict[str, Any]

DUSKY_VERSION: Final = "3.3.4"
JOURNAL_FORMAT: Final = 2

SCRIPT_PATH: Final = Path(__file__).resolve()

RUN_DIR: Final = Path("/run/dusky")
MNT_ROOT: Final = RUN_DIR / "mnt"
LOCK_PATH: Final = RUN_DIR / "dusky.lock"
LOG_PATH: Final = Path("/var/log/dusky.log")
UNIT_DIR_REL: Final = "etc/systemd/system"

# Journal lives in the FS_TREE, i.e. outside every subvolume Dusky can swap.
JOURNAL_DIR_NAME: Final = ".dusky"
# States in which a transaction is still open and MUST be recovered before any
# other destructive operation is allowed to start.
OPEN_TXN_STATES: Final = frozenset({"prepared", "activating", "activated", "failed", "unwinding"})

TAG_RETIRED: Final = "_to_delete_"
TAG_STAGED: Final = "_dusky_new_"
TAG_SEND: Final = ".tmp_send_"
TAG_RECV: Final = ".btrfs_recv_"
TAG_PROBE: Final = ".dusky_probe_"

# Anchored grammar for Dusky-owned transient names.  Only names that match
# exactly are eligible for automatic reclamation.  A user subvolume that merely
# *contains* the substring "_to_delete_" is NOT Dusky's to delete.
STAMP_RE: Final = r"\d{8}T\d{6}Z"
TRANSIENT_NAME_RE: Final = re.compile(
    r"\A(?:"
    rf"(?P<retired>.+{re.escape(TAG_RETIRED)}{STAMP_RE}(?:_[0-9a-f]{{8}})?)"
    rf"|(?P<staged>.+{re.escape(TAG_STAGED)}\d+_{STAMP_RE}(?:_[0-9a-f]{{8}})?)"
    rf"|(?P<send>{re.escape(TAG_SEND)}.+_{STAMP_RE})"
    rf"|(?P<probe>{re.escape(TAG_PROBE)}\d+_\d+_[ab])"
    r")\Z"
)
RECV_STAGING_RE: Final = re.compile(rf"\A{re.escape(TAG_RECV)}[A-Za-z0-9_]+\Z")

SAFE_NAME_RE: Final = re.compile(r"\A[A-Za-z0-9@._+:=-]{1,200}\Z")
MOUNT_DIR_RE: Final = re.compile(r"\Atop_(?P<pid>\d+)_(?P<tag>[A-Za-z0-9_]+)\Z")
UUID_RE: Final = re.compile(r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")

PAIR_STRICT_SECONDS: Final = 120
BTRFS_FS_TREE_OBJECTID: Final = 5

# Strict environment allowlist.  v3.0.0 inherited the caller's entire
# environment, so LD_PRELOAD / LD_LIBRARY_PATH / PYTHONPATH / BTRFS_* /
# SNAPPER_* all reached root-privileged children.  sudo's env_reset is a
# configuration, not a guarantee, and Dusky may also be started by systemd.
_ENV_PASSTHROUGH: Final = ("TERM", "TERMINFO", "COLORTERM", "TZ", "SYSTEMD_COLORS")
SUBPROCESS_ENV: Final = frozendict({
    **{k: os.environ[k] for k in _ENV_PASSTHROUGH if k in os.environ},
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin",
    "LC_ALL": "C.UTF-8",
    "LANG": "C.UTF-8",
    "HOME": pwd.getpwuid(0).pw_dir,
    "SHELL": "/usr/bin/bash",
})
# Snapper/findmnt use JSON; Btrfs uses its explicit C-locale subvolume fields.

C_RESET = "\x1b[0m"
C_ERR = "\x1b[1;38;5;196m"
C_WARN = "\x1b[1;38;5;220m"
C_OK = "\x1b[1;38;5;114m"
C_INFO = "\x1b[1;38;5;81m"
C_ACCENT = "\x1b[1;38;5;213m"
C_DIM = "\x1b[38;5;246m"
C_RULE = "\x1b[38;5;238m"
C_TEXT = "\x1b[38;5;253m"


# =============================================================================
# ERRORS / LOGGING / TERMINAL
# =============================================================================
class DuskyError(RuntimeError):
    """Fatal but recoverable at top level.  Never raised mid-unwind."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class DuskyAbort(DuskyError):
    """User aborted, or an interactive answer was required and unavailable."""


class DuskyBug(DuskyError):
    """An invariant Dusky itself guarantees was violated.  Always a bug."""


def die(message: str, exit_code: int = 1) -> NoReturn:
    raise DuskyError(message, exit_code)


def _build_logger() -> logging.Logger:
    # Logging transport failures must not print internal tracebacks in the UI.
    logging.raiseExceptions = bool(os.environ.get("DUSKY_DEBUG"))
    log = logging.getLogger("dusky")
    log.setLevel(logging.DEBUG if os.environ.get("DUSKY_DEBUG") else logging.INFO)
    log.propagate = False

    with suppress(Exception):
        journal = logging.handlers.SysLogHandler(address="/dev/log")
        journal.setFormatter(logging.Formatter("dusky[%(process)d]: %(levelname)s %(message)s"))
        log.addHandler(journal)

    # Create the file with 0600 *at open time*.  v3.0.0 used FileHandler and
    # chmod'ed afterwards, leaving a world-readable window on a log that
    # records full filesystem layout.
    with suppress(OSError):
        fd = os.open(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_CLOEXEC, 0o600)
        stream = os.fdopen(fd, "a", encoding="utf-8")
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(handler)

    if not log.handlers:
        log.addHandler(logging.NullHandler())
    return log


LOG: Final = _build_logger()

_TTY_IN: Any = None
_TTY_OUT: Any = None


def _tty() -> tuple[Any, Any] | None:
    """
    Open the controlling terminal directly, with O_NOCTTY.

    fzf owns /dev/tty for its own UI but releases it on exit; this process's
    stdin/stdout may be pipes (preview children, --json consumers, systemd).
    O_NOCTTY matters: without it a session leader that has no controlling
    terminal would *acquire* one by opening /dev/tty.  We also require
    os.isatty(), because /dev/tty can be opened successfully in contexts where
    it is not a usable interactive device.
    """
    global _TTY_IN, _TTY_OUT
    if _TTY_IN is not None and not _TTY_IN.closed:
        return _TTY_IN, _TTY_OUT
    try:
        rfd = os.open("/dev/tty", os.O_RDONLY | os.O_NOCTTY | os.O_CLOEXEC)
    except OSError:
        return None
    if not os.isatty(rfd):
        os.close(rfd)
        return None
    try:
        wfd = os.open("/dev/tty", os.O_WRONLY | os.O_NOCTTY | os.O_CLOEXEC)
    except OSError:
        os.close(rfd)
        return None
    _TTY_IN = os.fdopen(rfd, "r", encoding="utf-8", errors="replace")
    _TTY_OUT = os.fdopen(wfd, "w", encoding="utf-8", errors="replace")
    return _TTY_IN, _TTY_OUT


def interactive() -> bool:
    return _tty() is not None


def ask(prompt: str) -> str:
    pair = _tty()
    if pair is None:
        raise DuskyAbort("[!] Interactive input required but no controlling terminal is available.")
    tin, tout = pair
    tout.write(prompt)
    tout.flush()
    line = tin.readline()
    if line == "":
        raise DuskyAbort("[!] Aborted (EOF on terminal).")
    return line.strip()


def confirm(prompt: str, *, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    while True:
        try:
            answer = ask(f"\n{C_WARN}{prompt} [y/N]: {C_RESET}").lower()
        except (DuskyAbort, KeyboardInterrupt):
            return False
        if answer in ("y", "yes"):
            return True
        if answer in ("", "n", "no"):
            return False
        say(f"{C_DIM}Please answer y or n.{C_RESET}")


def confirm_phrase(prompt: str, phrase: str, *, assume_yes: bool = False) -> bool:
    """Typed confirmation for irreversible, whole-system operations."""
    if assume_yes:
        return True
    try:
        answer = ask(f"\n{C_ERR}{prompt}{C_RESET}\n{C_WARN}Type {phrase!r} to proceed: {C_RESET}")
    except (DuskyAbort, KeyboardInterrupt):
        return False
    return answer == phrase


def pause(message: str = "Press Enter to return...") -> None:
    with suppress(DuskyAbort, KeyboardInterrupt):
        ask(f"\n{C_OK}{message}{C_RESET}")


def say(text: str = "") -> None:
    print(text, flush=True)


def warn(text: str) -> None:
    with suppress(BrokenPipeError):
        print(f"{C_WARN}{text}{C_RESET}", file=sys.stderr, flush=True)
    LOG.warning(strip_ansi(text))


def note(text: str) -> None:
    say(f"{C_INFO}{text}{C_RESET}")
    LOG.info(strip_ansi(text))


def good(text: str) -> None:
    say(f"{C_OK}{text}{C_RESET}")
    LOG.info(strip_ansi(text))


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def display_width(text: str) -> int:
    width = 0
    for ch in strip_ansi(text):
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


# =============================================================================
# SUBPROCESS
# =============================================================================
@dataclass(frozen=True, slots=True)
class Proc:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def text(self) -> str:
        return self.stdout.strip()

    @property
    def message(self) -> str:
        return self.stderr.strip() or self.stdout.strip() or "<no output>"


# Operations that must never be killed by a watchdog: a half-committed
# 'subvolume delete --commit-after' or 'filesystem sync' on a busy filesystem
# can legitimately run for many minutes.
NO_TIMEOUT: Final = None
DEFAULT_TIMEOUT: Final = 300.0


def run(
    *argv: str,
    check: bool = False,
    timeout: float | None = DEFAULT_TIMEOUT,
    stdin_text: str | None = None,
) -> Proc:
    cmd = [str(a) for a in argv]
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=SUBPROCESS_ENV,
            timeout=timeout,
            input=stdin_text,
            check=False,
            start_new_session=False,
        )
    except FileNotFoundError as exc:
        die(f"[!] Missing executable: {cmd[0]} ({exc})")
    except subprocess.TimeoutExpired:
        die(f"[!] Timed out after {timeout}s: {shlex.join(cmd)}")
    except OSError as exc:
        die(f"[!] Failed to execute: {shlex.join(cmd)}\n    {exc}")

    proc = Proc(cmd, completed.returncode, completed.stdout or "", completed.stderr or "")
    if not proc.ok:
        LOG.debug("cmd rc=%s %s :: %s", proc.returncode, shlex.join(cmd), proc.message)
    if check and not proc.ok:
        code = proc.returncode if proc.returncode > 0 else 128 - proc.returncode
        die(f"[!] Command failed ({proc.returncode}): {shlex.join(cmd)}\n    {proc.message}", code)
    return proc


def run_tty(*argv: str) -> int:
    cmd = [str(a) for a in argv]
    try:
        return subprocess.run(cmd, env=SUBPROCESS_ENV, check=False).returncode
    except OSError as exc:
        die(f"[!] Failed to execute: {shlex.join(cmd)}\n    {exc}")


def reap_process(proc: subprocess.Popen) -> None:
    """Stop an interrupted child and collect it before releasing its resources."""
    if proc.poll() is None:
        with suppress(ProcessLookupError):
            proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                proc.kill()
            proc.wait()


def require_tools(*tools: str) -> None:
    missing = [t for t in tools if shutil.which(t, path=SUBPROCESS_ENV["PATH"]) is None]
    if missing:
        die(f"[!] Missing required tool(s): {', '.join(missing)}")


def ensure_root() -> None:
    if os.geteuid() == 0:
        return
    if shutil.which("sudo") is None:
        die("[!] Root privileges are required and sudo is not installed.")
    warn("[*] Elevating via sudo...")
    sys.stdout.flush()
    sys.stderr.flush()
    argv = ["sudo", "--", sys.executable, str(SCRIPT_PATH), *sys.argv[1:]]
    try:
        os.execvp("sudo", argv)
    except OSError as exc:
        die(f"[!] Failed to elevate privileges: {exc}")


# =============================================================================
# LOCK / SIGNALS
# =============================================================================
_LOCK_FD: int | None = None
_LOCK_DEPTH = 0


@contextmanager
def dusky_lock(*, wait: bool = True) -> Iterator[None]:
    """Process-wide, reentrant, host-wide exclusive advisory lock."""
    global _LOCK_FD, _LOCK_DEPTH
    if _LOCK_DEPTH > 0:
        _LOCK_DEPTH += 1
        try:
            yield
        finally:
            _LOCK_DEPTH -= 1
        return

    try:
        RUN_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(RUN_DIR, 0o700)
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    except OSError as exc:
        die(f"[!] Cannot create lock {LOCK_PATH}: {exc}")

    acquired = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            if not wait:
                die("[!] Another Dusky operation holds the lock. Refusing to queue.")
            holder = ""
            with suppress(OSError):
                holder = os.pread(fd, 64, 0).decode(errors="replace").strip()
            warn(f"[*] Waiting for the Dusky lock (held by pid {holder or '?'})...")
            fcntl.flock(fd, fcntl.LOCK_EX)
        acquired = True
        with suppress(OSError):
            os.ftruncate(fd, 0)
            os.pwrite(fd, f"{os.getpid()}\n".encode(), 0)
        _LOCK_FD, _LOCK_DEPTH = fd, 1
        yield
    finally:
        if acquired:
            _LOCK_DEPTH = max(0, _LOCK_DEPTH - 1)
        if _LOCK_DEPTH == 0:
            _LOCK_FD = None
            with suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
        with suppress(OSError):
            if _LOCK_FD is None:
                os.close(fd)


@contextmanager
def critical_section() -> Iterator[None]:
    """
    Block (never discard) terminating signals across the activation window.

    pthread_sigmask beats signal.SIG_IGN: a Ctrl-C pressed inside the window is
    *queued* by the kernel and delivered the instant the window closes, so the
    user's intent is honoured instead of silently swallowed.  SIGKILL and power
    loss are handled by the on-disk journal, not by this.
    """
    blocked = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT, signal.SIGPIPE}
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, blocked)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


# =============================================================================
# renameat2(2) -- directory-descriptor relative
# =============================================================================
AT_FDCWD: Final = -100
RENAME_NOREPLACE: Final = 1 << 0
RENAME_EXCHANGE: Final = 1 << 1


@cache
def _load_libc() -> ctypes.CDLL:
    # Arch uses glibc's stable soname; no linker-tool subprocess is needed.
    lib = ctypes.CDLL("libc.so.6", use_errno=True)
    if not hasattr(lib, "renameat2"):
        die("[!] glibc does not export renameat2(); atomic activation is unavailable.")
    lib.renameat2.restype = ctypes.c_int
    lib.renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    return lib


def _renameat2(olddirfd: int, oldname: str, newdirfd: int, newname: str, flags: int) -> None:
    rc = _load_libc().renameat2(
        olddirfd, os.fsencode(oldname), newdirfd, os.fsencode(newname), ctypes.c_uint(flags)
    )
    if rc != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), oldname, None, newname)


def rename_exchange_at(dfd: int, left: str, right: str) -> None:
    """Atomically swap two directory entries inside one directory."""
    _renameat2(dfd, left, dfd, right, RENAME_EXCHANGE)


def rename_noreplace_at(dfd: int, src: str, dst: str) -> None:
    _renameat2(dfd, src, dfd, dst, RENAME_NOREPLACE)


def rename_exchange(left: Path, right: Path) -> None:
    _renameat2(AT_FDCWD, str(left), AT_FDCWD, str(right), RENAME_EXCHANGE)


def rename_noreplace(src: Path, dst: Path) -> None:
    _renameat2(AT_FDCWD, str(src), AT_FDCWD, str(dst), RENAME_NOREPLACE)


@contextmanager
def open_dir(path: Path) -> Iterator[int]:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


def fsync_path(path: Path, *, is_dir: bool = False) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC | (os.O_DIRECTORY if is_dir else 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def file_in_root(root: Path, path: Path) -> bool:
    """Check an absolute path with symlinks resolved inside an offline root."""
    if not path.is_absolute():
        raise ValueError("interpreter path must be absolute")
    pending = deque(str(path).split("/")[1:])
    resolved = root
    links = 0
    while pending:
        part = pending.popleft()
        if part in ("", "."):
            continue
        if part == "..":
            if resolved != root:
                resolved = resolved.parent
            continue
        candidate = resolved / part
        if candidate.is_symlink():
            links += 1
            if links > 40:
                raise OSError(errno.ELOOP, "too many interpreter symlinks", str(path))
            target = os.readlink(candidate)
            if target.startswith("/"):
                resolved = root
            pending.extendleft(reversed(target.split("/")))
        else:
            resolved = candidate
            if pending and not resolved.is_dir():
                return False
    return resolved.is_file()


def write_file_durable(path: Path, content: str, mode: int = 0o644) -> None:
    """write -> fsync(file) -> rename -> fsync(dir).  Nothing less is durable."""
    tmp = path.with_name(f".{path.name}.dusky-tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, mode)
    try:
        data = memoryview(content.encode("utf-8"))
        while data:
            written = os.write(fd, data)
            if written <= 0:
                raise OSError(errno.EIO, "short write to journal")
            data = data[written:]
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    fsync_path(path.parent, is_dir=True)


# =============================================================================
# btrfs subvolume text interface (C locale; explicit UUID columns)
# =============================================================================
def _norm_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")


def btrfs_records(*argv: str, check: bool = True) -> JSONDict | None:
    """Parse the C-locale subvolume interface of btrfs-progs 7.1."""
    proc_plain = run("btrfs", *argv)
    if not proc_plain.ok:
        if check:
            die(f"[!] btrfs {' '.join(argv)} failed:\n    {proc_plain.message}")
        return None

    stdout = proc_plain.stdout or ""

    if argv[:2] == ("subvolume", "list"):
        rows: list[JSONDict] = []
        for line in stdout.splitlines():
            if not line:
                continue
            # Paths are arbitrary names, not metadata. A name containing
            # 'parent_uuid ...' must never supply a missing UUID column.
            metadata, separator, path = line.partition(" path ")
            m_id = re.search(r"\bID\s+(\d+)\b", metadata)
            m_gen = re.search(r"\bgen\s+(\d+)\b", metadata)
            m_top = re.search(r"\btop level\s+(\d+)\b", metadata)
            m_uuid = re.search(r"\buuid\s+([0-9a-fA-F-]{36})\b", metadata)
            m_puuid = re.search(r"\bparent_uuid\s+([0-9a-fA-F-]{36})\b", metadata)
            m_ruuid = re.search(r"\breceived_uuid\s+([0-9a-fA-F-]{36})\b", metadata)
            if not all((m_id, m_gen, m_top, separator, path)) or ("-u" in argv and not m_uuid):
                die(f"[!] Unrecognised btrfs subvolume list record: {line!r}")
            if m_id:
                row = {
                    "subvolume_id": int(m_id.group(1)),
                    "generation": int(m_gen.group(1)) if m_gen else 0,
                    "parent_id": int(m_top.group(1)) if m_top else 0,
                    "uuid": m_uuid.group(1) if m_uuid else "",
                    "parent_uuid": m_puuid.group(1) if m_puuid else "",
                    "received_uuid": m_ruuid.group(1) if m_ruuid else "",
                    "path": path,
                }
                rows.append(row)
        return {"subvolumes": rows}

    if argv[:2] == ("subvolume", "show"):
        kv: dict[str, str] = {}
        for line in stdout.splitlines():
            # The first line and deeper-indented snapshot paths can contain
            # colons and field names too. Only parse metadata at one tab.
            if line.startswith("\tSnapshot(s):"):
                break
            if line.startswith("\t") and not line.startswith("\t\t") and ":" in line:
                k, v = line.split(":", 1)
                k_norm = _norm_key(k.strip())
                v_val = v.strip()
                if v_val == "-":
                    v_val = ""
                kv[k_norm] = v_val
        subvol_id = _as_int(kv.get("subvolume_id")) or _as_int(kv.get("subvolid"))
        if subvol_id is not None:
            return {
                "subvolume_id": subvol_id,
                "uuid": kv.get("uuid", ""),
                "parent_uuid": kv.get("parent_uuid", ""),
                "received_uuid": kv.get("received_uuid", ""),
                "generation": _as_int(kv.get("generation")) or 0,
                "flags": kv.get("flags", ""),
                "path": stdout.splitlines()[0] if stdout.splitlines() else "",
            }
        return None

    if argv[:2] == ("subvolume", "get-default"):
        m_id = re.search(r"\bID\s+(\d+)\b", stdout)
        if m_id:
            return {"subvolume_id": int(m_id.group(1))}
        return None

    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdecimal():
        return int(value.strip())
    return None


def _pick(mapping: JSONDict, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return default


@dataclass(frozen=True, slots=True)
class SubvolInfo:
    subvolid: int
    path: str
    uuid: str
    parent_uuid: str
    received_uuid: str
    generation: int
    readonly: bool

    @property
    def is_fs_tree(self) -> bool:
        return self.subvolid == BTRFS_FS_TREE_OBJECTID


def subvol_show(path: str | Path) -> SubvolInfo | None:
    record = btrfs_records("subvolume", "show", "--", str(path), check=False)
    if record is None:
        return None
    subvolid = record["subvolume_id"]
    rel = record["path"].lstrip("/")
    if subvolid == BTRFS_FS_TREE_OBJECTID:
        rel = ""
    return SubvolInfo(
        subvolid=subvolid,
        path=rel,
        uuid=record["uuid"],
        parent_uuid=record["parent_uuid"],
        received_uuid=record["received_uuid"],
        generation=record["generation"],
        readonly="readonly" in record["flags"],
    )


def subvol_list(mount_target: str | Path) -> list[SubvolInfo]:
    """
    Every subvolume of the filesystem, with paths relative to the FS_TREE.

    -u/-q/-R are mandatory: btrfs-subvolume(8) only emits the uuid, parent uuid
    and received uuid columns when they are requested.  v3.0.0 omitted them and
    therefore reported uuid="" for every subvolume, which silently defeated
    every UUID comparison built on top of it.
    """
    records = btrfs_records("subvolume", "list", "-a", "-u", "-q", "-R", "--", str(mount_target))
    readonly = btrfs_records("subvolume", "list", "-a", "-r", "--", str(mount_target))
    if records is None or readonly is None:
        die(f"[!] Could not parse subvolume lists for {mount_target}.")
    ro_ids = {record["subvolume_id"] for record in readonly["subvolumes"]}

    out: list[SubvolInfo] = []
    for record in records["subvolumes"]:
        sid = record["subvolume_id"]
        rel = record["path"]
        if rel.startswith("<FS_TREE>/"):
            rel = rel[len("<FS_TREE>/") :]
        rel = rel.strip("/")
        out.append(
            SubvolInfo(
                subvolid=sid,
                path=rel,
                uuid=record["uuid"],
                parent_uuid=record["parent_uuid"],
                received_uuid=record["received_uuid"],
                generation=record["generation"],
                readonly=sid in ro_ids,
            )
        )
    return out


def get_default_subvolid(mount_target: str | Path) -> int:
    payload = btrfs_records("subvolume", "get-default", str(mount_target), check=False)
    if payload is not None:
        return payload["subvolume_id"]
    die(f"[!] Could not determine the default subvolume of {mount_target}.")


def subvol_path_by_id(mount_target: str | Path, subvolid: int) -> str | None:
    for info in subvol_list(mount_target):
        if info.subvolid == subvolid:
            return info.path
    return None


def set_default_subvolid(subvolid: int, mount_target: str | Path) -> None:
    run("btrfs", "subvolume", "set-default", str(subvolid), str(mount_target), check=True)


def btrfs_sync(path: str | Path) -> None:
    run("btrfs", "filesystem", "sync", str(path), check=True, timeout=NO_TIMEOUT)


def subvol_set_ro(path: Path, value: bool) -> None:
    run("btrfs", "property", "set", "-ts", str(path), "ro", "true" if value else "false", check=True)


def subvol_is_ro(path: Path) -> bool | None:
    proc = run("btrfs", "property", "get", "-ts", str(path), "ro")
    if not proc.ok:
        return None
    return "ro=true" in proc.text.replace(" ", "")


# =============================================================================
# findmnt / filesystem resolution
# =============================================================================
@dataclass(frozen=True, slots=True)
class Filesystem:
    uuid: str
    source: str

    @property
    def mount_source(self) -> str:
        """
        Always mount by UUID.

        /dev/sdX enumeration is not stable between the findmnt call and the
        mount call, and a multi-device btrfs is only coherently addressable by
        fsid: mounting one member device of a RAID profile by path can silently
        assemble a degraded view.
        """
        return f"UUID={self.uuid}"


def findmnt_entries(target: str | None = None) -> list[JSONDict]:
    argv = ["findmnt", "--json", "-o", "SOURCE,TARGET,FSTYPE,OPTIONS,UUID,PROPAGATION,FSROOT"]
    if target is not None:
        argv += ["--mountpoint", target]
    proc = run(*argv, timeout=60.0)
    if not proc.ok or not proc.text:
        if target is not None and proc.returncode == 1 and not proc.stderr.strip():
            return []
        die(f"[!] Cannot read mount table: {proc.message}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        die(f"[!] Invalid findmnt output: {exc}")
    entries: list[JSONDict] = []

    def _walk(items: Sequence[Any]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            entries.append(item)
            children = item.get("children")
            if isinstance(children, list):
                _walk(children)

    _walk(payload.get("filesystems", []))
    return entries


def effective_mount(target: str) -> JSONDict | None:
    """
    Return the *topmost* mount at a mountpoint.

    findmnt lists stacked mounts in mount order; the last one is what the
    kernel resolves.  Taking the first entry (v2 behaviour) resolves an
    over-mounted path to the wrong subvolume - and that subvolume would then be
    the one renamed away by a restore.
    """
    entries = findmnt_entries(target)
    exact = [e for e in entries if str(e.get("target", "")) == target] or entries
    return exact[-1] if exact else None


def subvol_from_options(options: str) -> str | None:
    match = re.search(r"(?:\A|[,\s=])subvol=([^,\s]+)(?=[,\s]|\Z)", options.strip())
    if not match:
        return None
    return match.group(1).strip().strip('"').lstrip("/") or None


def subvolid_from_options(options: str) -> int | None:
    match = re.search(r"(?:\A|[,\s=])subvolid=(\d+)(?=[,\s]|\Z)", options.strip())
    return int(match.group(1)) if match else None


def strip_source_subvol(source: str) -> str:
    return re.sub(r"\[.*?\]\Z", "", source).strip()


def filesystem_of(mountpoint: str) -> Filesystem:
    entry = effective_mount(mountpoint)
    if entry is None:
        die(f"[!] {mountpoint} is not a mount point.")
    if str(entry.get("fstype", "")) != "btrfs":
        die(f"[!] {mountpoint} is not btrfs (fstype={entry.get('fstype')!r}).")

    fs_uuid = str(entry.get("uuid") or "").strip()
    source = strip_source_subvol(str(entry.get("source") or ""))
    if not fs_uuid and source.startswith("/dev/"):
        blk = run("blkid", "-s", "UUID", "-o", "value", os.path.realpath(source), timeout=30.0)
        fs_uuid = blk.text.splitlines()[0].strip() if blk.ok and blk.text else ""
    if not fs_uuid and source.startswith("UUID="):
        fs_uuid = source.split("=", 1)[1].strip()
    if not UUID_RE.fullmatch(fs_uuid):
        die(f"[!] Could not resolve a valid btrfs fsid for {mountpoint} (source={source or '<none>'}).")
    return Filesystem(uuid=fs_uuid, source=source)


def is_mountpoint(path: str | Path) -> bool:
    return run("mountpoint", "-q", "--", str(path), timeout=30.0).ok


def active_subvol(mountpoint: str, *, required: bool = True) -> tuple[str, int] | None:
    """
    Resolve (relative subvolume path, subvolume id) for a live mount point.

    Kernel truth (BTRFS_IOC_GET_SUBVOL_INFO behind 'btrfs subvolume show') is
    the primary source; the mount option string is only a cross-check.  A
    disagreement means something is over-mounted, and we refuse to proceed.
    """
    info = subvol_show(mountpoint)
    entry = effective_mount(mountpoint)
    options = str((entry or {}).get("options") or "")
    opt_subvol = subvol_from_options(options)
    opt_subvolid = subvolid_from_options(options)

    if info is not None:
        rel = info.path.strip("/")
        if not rel and info.subvolid != BTRFS_FS_TREE_OBJECTID:
            # 'subvolume show' reports a path relative to the FS root; if that
            # field is unusable for any reason, resolve the id authoritatively
            # through the subvolume table rather than assuming FS_TREE.
            rel = (subvol_path_by_id(mountpoint, info.subvolid) or "").strip("/")
            if not rel:
                die(
                    f"[!] {mountpoint} is subvolume id {info.subvolid} but no path for it exists in the "
                    "subvolume table. Refusing to operate on an unidentifiable subvolume."
                )
        if opt_subvol and rel and opt_subvol.strip("/") != rel:
            die(
                f"[!] Inconsistent view of {mountpoint}: mount says subvol={opt_subvol!r} but the "
                f"kernel reports {rel!r}. Refusing to operate on an ambiguous (over-mounted) path."
            )
        if opt_subvolid is not None and opt_subvolid != info.subvolid:
            die(
                f"[!] Inconsistent view of {mountpoint}: mount says subvolid={opt_subvolid} but the "
                f"kernel reports {info.subvolid}."
            )
        if rel:
            return rel, info.subvolid
        if required:
            die(
                f"[!] {mountpoint} is the top-level tree (subvolid=5). Dusky refuses to restore over "
                "an FS_TREE mount; use a subvol=@ style layout."
            )
        return None

    if opt_subvol:
        die(f"[!] btrfs could not describe {mountpoint} even though it is mounted with subvol={opt_subvol}.")
    if required:
        die(f"[!] Could not determine the active btrfs subvolume for {mountpoint}.")
    return None


# =============================================================================
# PRIVATE subvolid=5 MOUNTS
# =============================================================================
_ACTIVE_MOUNTS: set[Path] = set()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _ensure_private_mnt_root() -> None:
    MNT_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(MNT_ROOT, 0o700)
    if not is_mountpoint(MNT_ROOT):
        run("mount", "--bind", str(MNT_ROOT), str(MNT_ROOT), check=True)
    # rprivate, not private: /run is MS_SHARED on systemd, and a plain
    # --make-private on the root of the subtree still lets *new* child mounts
    # created later propagate if any inner mount is shared.
    run("mount", "--make-rprivate", str(MNT_ROOT), check=True)


def sweep_stale_mounts() -> int:
    """
    Reap /run/dusky/mnt leftovers from a killed run.

    CRITICAL: it must never touch a mount that another live Dusky (or this
    process, in a nested top_level() context) is using.  Directory names carry
    the owning PID, and mounts registered in this process are skipped
    unconditionally.
    """
    if not MNT_ROOT.is_dir():
        return 0
    reaped = 0
    for child in sorted(MNT_ROOT.iterdir()):
        match = MOUNT_DIR_RE.fullmatch(child.name)
        if not child.is_dir() or match is None:
            continue
        if child in _ACTIVE_MOUNTS:
            continue
        if _pid_alive(int(match.group("pid"))):
            continue
        if is_mountpoint(child):
            if not run("umount", "--", str(child)).ok:
                run("umount", "--lazy", "--", str(child))
        with suppress(OSError):
            child.rmdir()
            reaped += 1
    if reaped:
        LOG.info("Reaped %d stale private mount(s)", reaped)
    return reaped


def multidevice_ready(fs: Filesystem) -> tuple[bool, str]:
    """
    A multi-device btrfs that is not fully scanned mounts degraded or not at
    all.  btrfs(5) requires every member device to have been seen by
    'btrfs device scan' (udev does this) before the fsid can be assembled.
    """
    proc = run("btrfs", "filesystem", "show", fs.uuid, timeout=60.0)
    if not proc.ok:
        return False, proc.message
    total = re.search(r"Total devices\s+(\d+)", proc.stdout)
    seen = len(re.findall(r"^\s*devid\s+", proc.stdout, re.MULTILINE))
    if total is None:
        # Unparseable output degrades to permissive, never to a false "ready".
        LOG.warning("Could not parse 'btrfs filesystem show %s' device count", fs.uuid)
        return False, "device count unknown"
    if int(total.group(1)) != seen:
        return False, f"{seen}/{total.group(1)} member devices visible (run: btrfs device scan)"
    return True, f"{seen} device(s)"


@contextmanager
def top_level(fs: Filesystem, *, writable: bool = True, quiet: bool = False,
              allow_empty: bool = False) -> Iterator[Path]:
    """
    Mount subvolid=5 privately and prove we mounted what we intended.

    Never silently changes the requested rw/ro mode.  v3.0.0 retried a failed
    read-only mount as READ-WRITE, which turned '--sweep' (an explicit dry run)
    into a writable handle on the live root filesystem.
    """
    if not UUID_RE.fullmatch(fs.uuid):
        die(f"[!] Refusing to mount a filesystem by malformed fsid {fs.uuid!r}.")
    ready, detail = multidevice_ready(fs)
    if not ready:
        die(f"[!] Filesystem UUID={fs.uuid} is not fully assembled: {detail}. Refusing to mount.")

    _ensure_private_mnt_root()
    sweep_stale_mounts()

    mnt = Path(tempfile.mkdtemp(prefix=f"top_{os.getpid()}_", dir=str(MNT_ROOT)))
    if MOUNT_DIR_RE.fullmatch(mnt.name) is None:  # pragma: no cover - defensive
        with suppress(OSError):
            mnt.rmdir()
        raise DuskyBug(f"[!] Generated an unparseable private mount name: {mnt.name}")

    opts = "subvolid=5,nodev,nosuid,noexec,noatime" + ("" if writable else ",ro")
    if not quiet:
        note(f"[*] Mounting top-level tree (subvolid=5) of UUID={fs.uuid} {'rw' if writable else 'ro'}...")

    mounted = run("mount", "-t", "btrfs", "-o", opts, fs.mount_source, str(mnt), timeout=120.0)
    if not mounted.ok:
        with suppress(OSError):
            mnt.rmdir()
        die(f"[!] Failed to mount subvolid=5 for UUID={fs.uuid} ({'rw' if writable else 'ro'}):\n    {mounted.message}")

    _ACTIVE_MOUNTS.add(mnt)
    try:
        seen = effective_mount(str(mnt)) or {}
        seen_uuid = str(seen.get("uuid") or "").strip()
        if seen_uuid != fs.uuid:
            die(f"[!] REFUSING TO CONTINUE: mounted UUID={seen_uuid} but expected UUID={fs.uuid}.")
        info = subvol_show(str(mnt))
        if info is None or info.subvolid != BTRFS_FS_TREE_OBJECTID:
            got = "unreadable" if info is None else str(info.subvolid)
            die(f"[!] {mnt} reports subvolume id {got}, not 5. That is not the top-level tree of UUID={fs.uuid}.")
        if not writable and "ro" not in str(seen.get("options") or "").split(","):
            die(f"[!] Requested a read-only view of UUID={fs.uuid} but the kernel reports it writable.")
        if not allow_empty and not any(mnt.iterdir()):
            die(
                f"[!] The top-level tree of UUID={fs.uuid} is empty. That is the signature of a "
                "wrong/zeroed device or a failed multi-device assembly. Aborting."
            )
        yield mnt
    finally:
        _ACTIVE_MOUNTS.discard(mnt)
        if not quiet:
            note("[*] Unmounting top-level tree...")
        detached = False
        last = ""
        for attempt in range(5):
            result = run("umount", "--", str(mnt), timeout=120.0)
            if result.ok:
                detached = True
                break
            last = result.message
            time.sleep(0.3 * (attempt + 1))
        if not detached:
            run("umount", "--lazy", "--", str(mnt))
            LOG.warning("Lazy-unmounted %s after failures: %s", mnt, last)
        with suppress(OSError):
            mnt.rmdir()


def probe_exchange(top: Path) -> bool:
    """
    Empirically prove RENAME_EXCHANGE works on subvolume links of *this*
    filesystem before betting the root filesystem on it.  Probe subvolumes use
    Dusky's transient grammar so a SIGKILL leaves nothing the sweeper cannot
    reclaim.
    """
    tag = f"{TAG_PROBE}{os.getpid()}_{int(time.time())}"
    a, b = top / f"{tag}_a", top / f"{tag}_b"
    try:
        for path in (a, b):
            if not run("btrfs", "subvolume", "create", "--", str(path)).ok:
                return False
        try:
            with open_dir(top) as dfd:
                rename_exchange_at(dfd, a.name, b.name)
        except OSError as exc:
            LOG.error("RENAME_EXCHANGE probe failed: %s", exc)
            return False
        return True
    finally:
        for path in (a, b):
            if os.path.lexists(path):
                run("btrfs", "subvolume", "delete", "--", str(path), timeout=NO_TIMEOUT)


# =============================================================================
# SUBVOLUME MODEL / CLASSIFICATION
# =============================================================================
@dataclass(slots=True)
class Subvolume:
    subvolid: int
    path: str
    uuid: str
    received_uuid: str
    fs_uuid: str
    readonly: bool
    mount_target: str
    mounted_at: str = ""
    kind: str = "normal"

    def as_meta(self) -> JSONDict:
        return {
            "id": str(self.subvolid),
            "path": self.path,
            "uuid": self.uuid,
            "received_uuid": self.received_uuid,
            "fs_uuid": self.fs_uuid,
            "is_ro": self.readonly,
            "mount_target": self.mount_target,
            "mounted_at": self.mounted_at,
            "kind": self.kind,
        }


_CACHE: dict[str, Any] = {}


def cached(key: str, producer: Callable[[], Any]) -> Any:
    """
    Request-scoped memoisation.

    A single TUI redraw used to fork ~40 processes because every helper
    re-derived the mount table.  Nothing here mutates state, and the cache is
    dropped explicitly around every write.
    """
    if key not in _CACHE:
        _CACHE[key] = producer()
    return _CACHE[key]


def invalidate_cache() -> None:
    _CACHE.clear()


def btrfs_filesystems() -> dict[str, tuple[Filesystem, str]]:
    """fs_uuid -> (Filesystem, shortest mount point on it)."""

    def build() -> dict[str, tuple[Filesystem, str]]:
        result: dict[str, tuple[Filesystem, str]] = {}
        for entry in cached("mount_entries", findmnt_entries):
            if str(entry.get("fstype")) != "btrfs":
                continue
            target = str(entry.get("target") or "")
            fs_uuid = str(entry.get("uuid") or "").strip()
            if not fs_uuid or not target or not UUID_RE.fullmatch(fs_uuid):
                continue
            if fs_uuid in result:
                if len(target) < len(result[fs_uuid][1]):
                    result[fs_uuid] = (result[fs_uuid][0], target)
                continue
            source = strip_source_subvol(str(entry.get("source") or ""))
            result[fs_uuid] = (Filesystem(fs_uuid, source), target)
        return result

    return cached("filesystems", build)


def mounted_subvol_paths() -> dict[tuple[str, str], str]:
    """(filesystem UUID, relative subvolume path) -> live mount point."""

    def build() -> dict[tuple[str, str], str]:
        mapping: dict[tuple[str, str], str] = {}
        for entry in cached("mount_entries", findmnt_entries):
            if str(entry.get("fstype")) != "btrfs":
                continue
            fs_uuid = str(entry.get("uuid") or "")
            target = str(entry.get("target") or "")
            sv = subvol_from_options(str(entry.get("options") or ""))
            if sv:
                mapping.setdefault((fs_uuid, sv.strip("/")), target)
            elif target:
                info = subvol_show(target)
                if info and info.path:
                    mapping.setdefault((fs_uuid, info.path.strip("/")), target)
        return mapping

    return cached("mounted_subvols", build)


def snapshot_roots() -> set[str]:
    """
    Relative paths of subvolumes that hold snapper snapshot stores.

    Purely informational, so it must never abort: active_subvol() can die() on
    an ambiguous mount, which in v3.0.0 made an unrelated over-mount fatal to
    '--list-subvols'.
    """

    def build() -> set[str]:
        roots: set[str] = set()
        for cfg in snapper_configs():
            snaps = snapshots_mountpoint(cfg["subvolume"])
            with suppress(DuskyError):
                resolved = active_subvol(snaps, required=False)
                if resolved:
                    roots.add(resolved[0].strip("/"))
        for _fs_uuid, path in mounted_subvol_paths():
            if path.endswith("_snapshots") or path == ".snapshots" or path.endswith("/.snapshots"):
                roots.add(path)
        return {r for r in roots if r}

    return cached("snapshot_roots", build)


def transient_kind(rel_path: str) -> str | None:
    """
    Classify a Dusky transient artefact by its BASENAME using an anchored
    grammar.  v3.0.0 used 'token in path', so /srv/archive_to_delete_2024 (a
    perfectly legitimate user subvolume) was eligible for --sweep-apply.
    """
    name = rel_path.strip("/").rsplit("/", 1)[-1]
    match = TRANSIENT_NAME_RE.fullmatch(name)
    if match is None:
        return None
    for key in ("retired", "staged", "send", "probe"):
        if match.group(key):
            return {"retired": "retired", "staged": "staged", "send": "ephemeral", "probe": "probe"}[key]
    return None


def classify_subvol(path: str, roots: set[str]) -> str:
    """'transient' | 'snapshot' | 'normal'"""
    p = path.strip("/")
    if transient_kind(p):
        return "transient"
    if p == ".snapshots" or p.startswith(".snapshots/") or "/.snapshots/" in f"/{p}":
        return "snapshot"
    for root in roots:
        if p == root or p.startswith(root + "/"):
            return "snapshot"
    if re.fullmatch(r"(?:.*/)?\.snapshots/\d+/snapshot", p):
        return "snapshot"
    if re.fullmatch(r"(?:.*/)?[^/]+_snapshots/\d+/snapshot", p):
        return "snapshot"
    return "normal"


def enumerate_subvolumes(*, include_snapshots: bool = False, include_transient: bool = False) -> list[Subvolume]:
    roots = snapshot_roots()
    live = mounted_subvol_paths()
    found: list[Subvolume] = []
    for fs_uuid, (_fs, mount_target) in btrfs_filesystems().items():
        for info in subvol_list(mount_target):
            kind = classify_subvol(info.path, roots)
            if kind == "snapshot" and not include_snapshots:
                continue
            if kind == "transient" and not include_transient:
                continue
            found.append(
                Subvolume(
                    subvolid=info.subvolid,
                    path=info.path,
                    uuid=info.uuid,
                    received_uuid=info.received_uuid,
                    fs_uuid=fs_uuid,
                    readonly=info.readonly,
                    mount_target=mount_target,
                    mounted_at=live.get((fs_uuid, info.path), ""),
                    kind=kind,
                )
            )
    return found


def _fstab_like_files() -> list[Path]:
    files = [Path("/etc/fstab")]
    unit_dirs = (
        Path("/etc/systemd/system"),
        Path("/run/systemd/generator"),
        Path("/usr/lib/systemd/system"),
    )
    for directory in unit_dirs:
        if directory.is_dir():
            files.extend(sorted(directory.glob("*.mount")))
    return files


def protected_subvolumes() -> set[str]:
    """
    Subvolumes that must never be deleted through Dusky.

    Union of every live mount's subvolume, every snapper snapshot store, every
    subvolume referenced by fstab OR by a systemd .mount unit (including
    generator output), and the filesystem default subvolume - which, if
    deleted, bricks the boot even though nothing has it mounted
    (btrfs-subvolume(8), 'set-default').
    """
    protected: set[str] = {path for _fs_uuid, path in mounted_subvol_paths()}
    protected |= snapshot_roots()
    protected_ids: dict[str, set[int]] = {}

    for path in _fstab_like_files():
        with suppress(OSError):
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                blob = stripped
                sv = subvol_from_options(blob)
                if sv:
                    protected.add(sv.strip("/"))
                svid = subvolid_from_options(blob)
                if svid is not None:
                    protected_ids.setdefault("*", set()).add(svid)

    for fs_uuid, (_fs, mount_target) in btrfs_filesystems().items():
        default_id = get_default_subvolid(mount_target)
        wanted = set(protected_ids.get("*", set()))
        if default_id is not None and default_id != BTRFS_FS_TREE_OBJECTID:
            wanted.add(default_id)
        if not wanted:
            continue
        for info in subvol_list(mount_target):
            if info.subvolid in wanted:
                protected.add(info.path)
        del fs_uuid
    return {p for p in protected if p}


# =============================================================================
# SNAPPER
# =============================================================================
def snapper_configs() -> list[dict[str, str]]:
    def build() -> list[dict[str, str]]:
        proc = run("snapper", "--jsonout", "list-configs", "--columns", "config,subvolume", check=True)
        try:
            rows = json.loads(proc.stdout)["configs"]
            if not isinstance(rows, list):
                raise ValueError("configs is not a list")
            configs = []
            for row in rows:
                name, subvolume = row["config"], row["subvolume"]
                if not isinstance(name, str) or not isinstance(subvolume, str) or not subvolume.startswith("/"):
                    raise ValueError("invalid config or subvolume")
                configs.append({"config": name, "subvolume": os.path.normpath(subvolume)})
            return configs
        except (ValueError, TypeError, KeyError) as exc:
            die(f"[!] Cannot parse Snapper configurations: {exc}")

    return cached("snapper_configs", build)


def snapper_config_subvolume(config: str) -> str:
    for cfg in snapper_configs():
        if cfg["config"] == config:
            return cfg["subvolume"]
    die(f"[!] Unknown snapper config {config!r}. Known: {', '.join(c['config'] for c in snapper_configs()) or 'none'}")


def snapshots_mountpoint(target_mnt: str) -> str:
    return "/.snapshots" if target_mnt == "/" else f"{target_mnt.rstrip('/')}/.snapshots"


def validate_snap_id(raw: object) -> str:
    value = str(raw).strip()
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        die(f"[!] Invalid snapshot id: {raw!r}")
    return str(int(value))


def parse_dt(raw: object) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = float(raw)
        if value > 1_000_000_000_000:
            value /= 1000.0
        with suppress(OverflowError, OSError, ValueError):
            return datetime.fromtimestamp(value, tz=UTC)
        return None
    text = str(raw).strip()
    if not text:
        return None
    for candidate in (text, text.replace(" ", "T", 1)):
        with suppress(ValueError):
            parsed = datetime.fromisoformat(candidate)
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    return None


def human_date(raw: object) -> str:
    parsed = parse_dt(raw)
    return parsed.astimezone().strftime("%m/%d/%y %H:%M") if parsed else (str(raw).strip() if raw else "")


def time_ago(moment: datetime | None) -> str:
    if moment is None:
        return "unknown"
    seconds = int((datetime.now(UTC) - moment.astimezone(UTC)).total_seconds())
    if seconds < 0:
        return "future"
    for limit, div, suffix in ((60, 1, "s"), (3600, 60, "m"), (86400, 3600, "h"), (2592000, 86400, "d")):
        if seconds < limit:
            return f"{seconds // div}{suffix} ago"
    return f"{seconds // 2592000}mo ago"


def parse_userdata(raw: object) -> dict[str, str]:
    data: dict[str, str] = {}
    if raw is None:
        return data
    if isinstance(raw, dict):
        return {str(k).strip(): str(v).strip() for k, v in raw.items() if k is not None}
    for part in re.split(r"[;,]", str(raw)):
        key, sep, value = part.strip().partition("=")
        if sep and key.strip():
            data[key.strip()] = value.strip()
    return data


def snapshot_rows(config: str) -> list[dict[str, Any]]:
    """
    Snapshot rows for one snapper config.

    Query with --iso and --utc: Snapper's JSON dates otherwise use the
    human-readable local format. UTC avoids ambiguous local times during a
    daylight-saving transition when matching snapshots across configs.
    """

    def build() -> list[dict[str, Any]]:
        proc = run("snapper", "--jsonout", "--utc", "--iso", "-c", config, "list", "--disable-used-space",
                   "--columns", "number,type,pre-number,date,user,cleanup,description,userdata")
        if not proc.ok:
            LOG.error("snapper list failed for %s: %s", config, proc.message)
            die(f"[!] snapper could not list config {config!r}:\n    {proc.message}")
        try:
            payload = json.loads(proc.stdout)
            records = payload[config]
            if not isinstance(records, list) or any(
                not isinstance(row, dict) or _as_int(row.get("number")) is None
                for row in records
            ):
                raise ValueError("expected a snapshot array with numeric snapshot numbers")
        except (ValueError, TypeError, KeyError) as exc:
            die(f"[!] snapper --jsonout returned invalid snapshot data for {config!r}: {exc}")
        target = snapper_config_subvolume(config)
        snaps_mnt = snapshots_mountpoint(target)
        snaps_live = Path(snaps_mnt).is_mount()

        rows: list[dict[str, Any]] = []
        for record in records:
            raw_id = _pick(record, "number", "id", "num")
            snap_id = re.sub(r"[*+-]+\Z", "", str(raw_id).strip()) if raw_id is not None else ""
            if not snap_id.isdigit() or snap_id == "0":
                continue
            raw_date = _pick(record, "date", "timestamp", "time")
            moment = parse_dt(raw_date)
            userdata_dict = parse_userdata(_pick(record, "userdata", "user_data"))
            pre_number = str(_pick(record, "pre_number", "pre-number", "pre_num", default="") or "").strip()
            location = f"{snaps_mnt}/{snap_id}/snapshot"
            dead = bool(snaps_live and not Path(location).exists())
            description = str(_pick(record, "description", "desc", default="") or "")
            if dead and not description.startswith("[DEAD]"):
                description = f"[DEAD] {description}"
            rows.append(
                {
                    "config": config,
                    "id": snap_id,
                    "type": str(_pick(record, "type", "snapshot_type", default="") or ""),
                    "date": human_date(raw_date),
                    "raw_date": "" if raw_date is None else str(raw_date),
                    "epoch": moment.timestamp() if moment else None,
                    "description": description,
                    "cleanup": str(_pick(record, "cleanup", "cleanup_algorithm", default="") or ""),
                    "userdata": ",".join(f"{k}={v}" for k, v in userdata_dict.items()),
                    "userdata_dict": userdata_dict,
                    "user": str(_pick(record, "user", "creator", default="root") or "root"),
                    "pre_number": "" if pre_number in ("0", "-", "") else pre_number,
                    "age": time_ago(moment),
                    "location": location,
                    "dead": dead,
                }
            )
        return rows

    return cached(f"snapshots:{config}", build)


def all_snapshot_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cfg in snapper_configs():
        with suppress(DuskyError):
            rows.extend(snapshot_rows(cfg["config"]))
    return rows


# =============================================================================
# PAIR MATCHING
# =============================================================================
@dataclass(frozen=True, slots=True)
class PairMatch:
    left_id: str
    right_id: str
    method: str
    delta: float

    @property
    def exact(self) -> bool:
        return self.method == "dusky_pair"


def find_pair(
    left_cfg: str,
    right_cfg: str,
    *,
    target_date: str | None = None,
    target_desc: str | None = None,
    target_userdata: dict[str, str] | None = None,
    left_id_hint: str | None = None,
    threshold: int = PAIR_STRICT_SECONDS,
) -> PairMatch:
    """
    Resolve a synchronized snapshot pair.  Ranked strategies, all of which
    refuse ambiguity rather than guessing:

      1. dusky_pair userdata (uuid4, unique by construction; dusky_role checked)
      2. identical UTC timestamp
      3. identical description + identical snapshot type, nearest timestamp
         within 'threshold' seconds (default 120, not 900)

    v2 defaulted to a 900 second window over *all* right-hand snapshots with no
    type check, so an unrelated hourly timeline snapshot 14 minutes later - or
    the 'post' half of a pacman pre/post pair - could be silently accepted and
    restored alongside a 'pre' root snapshot.
    """
    left_rows = snapshot_rows(left_cfg)
    right_rows = snapshot_rows(right_cfg)
    if not left_rows:
        die(f"[!] No snapshots found for config {left_cfg!r}.")
    if not right_rows:
        die(f"[!] No snapshots found for config {right_cfg!r}.")

    left: dict[str, Any] | None = None
    if left_id_hint:
        matches = [r for r in left_rows if r["id"] == str(left_id_hint)]
        if len(matches) == 1:
            left = matches[0]
        elif not matches:
            die(f"[!] {left_cfg} has no snapshot with id {left_id_hint}.")
    if left is None:
        if target_date is None:
            die("[!] Neither a snapshot id nor a target date was supplied for pair matching.")
        wanted = parse_dt(target_date)
        matches = [r for r in left_rows if r["raw_date"] == target_date]
        if not matches and wanted is not None:
            matches = [r for r in left_rows if r["epoch"] is not None and abs(r["epoch"] - wanted.timestamp()) < 1.0]
        # An explicit description is a FILTER, not decoration.  v3.0.0 accepted
        # --sync-restore DATE DESC and then ignored DESC entirely.
        if target_desc:
            narrowed = [r for r in matches if r["description"] == target_desc]
            if narrowed:
                matches = narrowed
            else:
                die(f"[!] No {left_cfg} snapshot at {target_date!r} has description {target_desc!r}.")
        if len(matches) > 1:
            ids = ", ".join(r["id"] for r in matches)
            die(f"[!] Ambiguous: {len(matches)} {left_cfg} snapshots match {target_date!r} (ids: {ids}).")
        if not matches:
            die(f"[!] No {left_cfg} snapshot matches {target_date!r}.")
        left = matches[0]

    if target_desc is not None and left["description"] != target_desc:
        die(f"[!] Snapshot {left['id']} does not have description {target_desc!r}.")
    if left["dead"]:
        die(f"[!] {left_cfg} snapshot {left['id']} is dead (its subvolume is missing).")

    userdata = dict(left.get("userdata_dict") or {})
    if target_userdata is not None and target_userdata != userdata:
        die("[!] Snapshot pairing metadata changed since selection; refresh and select again.")
    if userdata.get("dusky_role", left_cfg) != left_cfg:
        die(f"[!] Snapshot {left['id']} has the wrong dusky_role for {left_cfg!r}.")
    pair_id = userdata.get("dusky_pair")
    if pair_id:
        tagged = [
            r
            for r in right_rows
            if r.get("userdata_dict", {}).get("dusky_pair") == pair_id
            and r.get("userdata_dict", {}).get("dusky_role", right_cfg) == right_cfg
        ]
        if len(tagged) > 1:
            die(f"[!] Corrupt pairing: {len(tagged)} {right_cfg} snapshots share dusky_pair={pair_id}.")
        if len(tagged) == 1:
            if tagged[0]["dead"]:
                die(f"[!] Paired {right_cfg} snapshot {tagged[0]['id']} is dead.")
            return PairMatch(left["id"], tagged[0]["id"], "dusky_pair", 0.0)
        die(f"[!] {left_cfg} snapshot {left['id']} carries dusky_pair={pair_id} but no {right_cfg} half exists.")

    same_time = [r for r in right_rows if left["epoch"] is not None and r["epoch"] == left["epoch"]
                 and r["type"] == left["type"] and r["description"] == left["description"]
                 and not r["dead"] and not r.get("userdata_dict", {}).get("dusky_pair")]
    if len(same_time) == 1:
        return PairMatch(left["id"], same_time[0]["id"], "identical timestamp", 0.0)
    if len(same_time) > 1 and left["description"]:
        narrowed = [r for r in same_time if r["description"] == left["description"]]
        if len(narrowed) == 1:
            return PairMatch(left["id"], narrowed[0]["id"], "identical timestamp", 0.0)

    if left["epoch"] is None:
        die("[!] The source snapshot has an unparseable timestamp; heuristic matching is unsafe.")

    candidates = [
        r
        for r in right_rows
        if not r["dead"]
        and r["epoch"] is not None
        and r["type"] == left["type"]
        and r["description"] == left["description"]
        and not r.get("userdata_dict", {}).get("dusky_pair")
    ]
    if not candidates:
        die(
            f"[!] No {right_cfg} snapshot shares the description {left['description']!r} "
            f"and type {left['type']!r}. Refusing to guess."
        )
    scored = sorted(candidates, key=lambda r: abs(r["epoch"] - left["epoch"]))
    best = scored[0]
    delta = abs(best["epoch"] - left["epoch"])
    if delta > threshold:
        die(
            f"[!] Closest {right_cfg} candidate (id {best['id']}) is {delta:.0f}s away, beyond the "
            f"{threshold}s safety threshold. Create pairs with --create-pair so they carry dusky_pair userdata."
        )
    if len(scored) > 1 and abs(abs(scored[1]["epoch"] - left["epoch"]) - delta) < 1.0:
        die(f"[!] Ambiguous: {right_cfg} snapshots {best['id']} and {scored[1]['id']} are equidistant.")
    return PairMatch(left["id"], best["id"], "nearest timestamp", delta)


# =============================================================================
# SYSTEMD UNIT GENERATION
# =============================================================================
def systemd_quote(value: str) -> str:
    """
    Quote a literal for a systemd Exec= line.

    systemd.service(5) 'Command lines': Exec values are split with the rules of
    systemd.syntax(7), which performs C-style unescaping (\\xNN, \\NNN, \\t,
    \\n, ...), '%' specifier expansion, and dollar-sign variable substitution
    (both the bare and the brace-delimited forms).  A
    literal '%' must be '%%' and a literal '$' must be '$$'.  This is also
    exactly why Dusky does NOT round-trip names through systemd-escape: the
    escape form of '-' is '\\x2d', and pasting '\\x2d' into an Exec line is
    unescaped straight back to '-' by systemd's own C-escape pass.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("%", "%%").replace("$", "$$")
    return f'"{escaped}"'


CLEANUP_UNIT_TEMPLATE: Final = """[Unit]
Description=Dusky deferred Btrfs cleanup of {display}
After=local-fs.target
Requires=local-fs.target
X-Dusky-Version={version}
X-Dusky-FsUuid={fs_uuid_plain}
X-Dusky-Subvolid={subvolid}

[Service]
Type=oneshot
Nice=15
IOSchedulingClass=idle
TimeoutStartSec=infinity
ExecStart={python} {helper} --cleanup-subvol {fs_uuid} {relative} --expected-id {subvolid}

[Install]
WantedBy=multi-user.target
"""


def cleanup_unit_name(fs_uuid: str, subvol_rel: str) -> str:
    digest = hashlib.blake2s(f"{fs_uuid}:{subvol_rel}".encode(), digest_size=8).hexdigest()
    return f"dusky-cleanup-{digest}.service"


def schedule_boot_cleanup(
    *, fs_uuid: str, subvol_rel: str, subvolid: int, offline_root: Path | None
) -> str:
    """Install cleanup using the running interpreter and a durable helper copy."""
    rel = subvol_rel.strip("/")
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        raise DuskyBug(f"[!] Refusing to schedule cleanup for the unsafe relative path {subvol_rel!r}.")
    if any(ch in rel for ch in ("\n", "\r", "\x00")):
        raise DuskyBug("[!] Refusing to schedule cleanup for a path containing control characters.")

    unit_name = cleanup_unit_name(fs_uuid, rel)
    root = offline_root if offline_root is not None else Path("/")
    python = Path(sys.executable)
    if not file_in_root(root, python):
        die(f"[!] Cleanup interpreter {python} is missing from the restored root.")
    helper_rel = f"usr/local/lib/dusky/cleanup-{DUSKY_VERSION}.py"
    helper = root / helper_rel
    helper.parent.mkdir(parents=True, exist_ok=True)
    write_file_durable(helper, Path(__file__).read_text(encoding="utf-8"), 0o644)
    content = CLEANUP_UNIT_TEMPLATE.format(
        display=rel.replace("%", "%%"), version=DUSKY_VERSION,
        fs_uuid_plain=fs_uuid, subvolid=subvolid,
        python=systemd_quote(str(python)),
        helper=systemd_quote("/" + helper_rel),
        fs_uuid=systemd_quote(fs_uuid), relative=systemd_quote(rel),
    )
    base = root / UNIT_DIR_REL
    base.mkdir(parents=True, exist_ok=True)
    write_file_durable(base / unit_name, content, 0o644)

    # Write the enablement symlink by hand.  This is exactly what
    # 'systemctl --root=... enable' does, and it is the only correct method for
    # an offline root that is not mounted at / yet.
    wants = base / "multi-user.target.wants"
    wants.mkdir(parents=True, exist_ok=True)
    link = wants / unit_name
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(f"/{UNIT_DIR_REL}/{unit_name}")
    fsync_path(wants, is_dir=True)

    if offline_root is None:
        run("systemctl", "daemon-reload", check=True)
    LOG.info("Scheduled %s to delete %s (id %d) on UUID=%s", unit_name, rel, subvolid, fs_uuid)
    return unit_name


def cancel_boot_cleanup(unit_name: str, *, offline_root: Path | None = None) -> bool:
    # Hard guard: an empty or foreign name would resolve to a DIRECTORY under
    # /etc/systemd/system and hand unlink() the unit directory itself.
    if not unit_name.startswith("dusky-cleanup-") or not unit_name.endswith(".service"):
        return False
    base = (offline_root / UNIT_DIR_REL) if offline_root else Path("/") / UNIT_DIR_REL
    removed = False
    for candidate in (base / unit_name, base / "multi-user.target.wants" / unit_name):
        with suppress(OSError):
            if candidate.is_symlink() or candidate.exists():
                candidate.unlink()
                removed = True
    if removed:
        fsync_path(base, is_dir=True)
        wants = base / "multi-user.target.wants"
        if wants.is_dir():
            fsync_path(wants, is_dir=True)
        if offline_root is None:
            run("systemctl", "daemon-reload")
        LOG.info("Cancelled %s", unit_name)
    return removed


def list_pending_cleanups() -> list[str]:
    base = Path("/") / UNIT_DIR_REL
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.glob("dusky-cleanup-*.service"))


# =============================================================================
# TRANSACTION JOURNAL  (lives in the FS_TREE, outside every swappable subvol)
# =============================================================================
@dataclass(slots=True)
class JournalEntry:
    config: str
    mountpoint: str
    fs_uuid: str
    parent_rel: str
    live_name: str
    staged_name: str
    retired_name: str
    source_rel: str
    old_subvolid: int
    new_subvolid: int = 0
    default_before: int = 0
    exchanged: bool = False
    retired_named: bool = False

    def to_json(self) -> JSONDict:
        return {
            "config": self.config,
            "mountpoint": self.mountpoint,
            "fs_uuid": self.fs_uuid,
            "parent_rel": self.parent_rel,
            "live_name": self.live_name,
            "staged_name": self.staged_name,
            "retired_name": self.retired_name,
            "source_rel": self.source_rel,
            "old_subvolid": self.old_subvolid,
            "new_subvolid": self.new_subvolid,
            "default_before": self.default_before,
            "exchanged": self.exchanged,
            "retired_named": self.retired_named,
        }

    @staticmethod
    def from_json(raw: JSONDict) -> JournalEntry:
        return JournalEntry(
            config=str(raw.get("config", "")),
            mountpoint=str(raw.get("mountpoint", "")),
            fs_uuid=str(raw.get("fs_uuid", "")),
            parent_rel=str(raw.get("parent_rel", "")),
            live_name=str(raw.get("live_name", "")),
            staged_name=str(raw.get("staged_name", "")),
            retired_name=str(raw.get("retired_name", "")),
            source_rel=str(raw.get("source_rel", "")),
            old_subvolid=int(raw.get("old_subvolid", 0) or 0),
            new_subvolid=int(raw.get("new_subvolid", 0) or 0),
            default_before=int(raw.get("default_before", 0) or 0),
            exchanged=bool(raw.get("exchanged")),
            retired_named=bool(raw.get("retired_named")),
        )

    def rel_of(self, name: str) -> str:
        return f"{self.parent_rel}/{name}" if self.parent_rel else name


@dataclass(slots=True)
class Journal:
    txn: str
    fs_uuid: str
    state: str
    started: str
    entries: list[JournalEntry]
    path: Path | None = None
    tops: dict[str, Path] = field(default_factory=dict, repr=False)
    operation: str = "restore"

    def to_json(self) -> JSONDict:
        return {
            "format": JOURNAL_FORMAT,
            "dusky": DUSKY_VERSION,
            "txn": self.txn,
            "fs_uuid": self.fs_uuid,
            "state": self.state,
            "operation": self.operation,
            "started": self.started,
            "entries": [e.to_json() for e in self.entries],
        }

    def commit(self, top: Path, state: str | None = None) -> None:
        """
        Persist the journal and force it to disk with a btrfs transaction
        commit.  Without the commit, the journal update and the subsequent
        rename can land in different transactions and a power cut between them
        loses exactly the record we need.
        """
        if state is not None:
            self.state = state
        # Commit every participant before publishing the coordinator's state.
        # The coordinator is authoritative; replicas block independent cleanup.
        locations = self.tops or {self.fs_uuid: top}
        for location in locations.values():
            btrfs_sync(location)
        ordered = [p for k, p in locations.items() if k != self.fs_uuid] + [top]
        for location in ordered:
            directory = location / JOURNAL_DIR_NAME
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            target = directory / f"txn-{self.txn}.json"
            write_file_durable(target, json.dumps(self.to_json(), indent=1, sort_keys=True), 0o600)
            btrfs_sync(location)
        self.path = top / JOURNAL_DIR_NAME / f"txn-{self.txn}.json"

    def discard(self, top: Path) -> None:
        locations = self.tops or {self.fs_uuid: top}
        ordered = [p for k, p in locations.items() if k != self.fs_uuid] + [top]
        for location in ordered:
            directory = location / JOURNAL_DIR_NAME
            target = directory / f"txn-{self.txn}.json"
            target.unlink(missing_ok=True)
            if directory.is_dir():
                fsync_path(directory, is_dir=True)
                btrfs_sync(location)



def load_journals(top: Path) -> list[Journal]:
    directory = top / JOURNAL_DIR_NAME
    if not directory.is_dir():
        return []
    out: list[Journal] = []
    for path in sorted(directory.glob("txn-*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("journal is not an object")
            txn = raw["txn"]
            if not isinstance(txn, str) or not re.fullmatch(r"[0-9a-f]{32}", txn) or path.name != f"txn-{txn}.json":
                raise ValueError("transaction identity does not match its filename")
            if raw["format"] != JOURNAL_FORMAT:
                raise ValueError(f"unsupported format {raw['format']!r}")
            if raw["state"] not in OPEN_TXN_STATES | {"finalised"}:
                raise ValueError("unknown transaction state")
            if raw.get("operation", "restore") not in {"restore", "undo"}:
                raise ValueError("unknown operation")
            entries = [JournalEntry.from_json(e) for e in raw["entries"]]
            if not entries or not UUID_RE.fullmatch(raw["fs_uuid"]):
                raise ValueError("missing transaction identities")
            for e in entries:
                if not UUID_RE.fullmatch(e.fs_uuid) or e.old_subvolid <= 5 or e.new_subvolid < 0:
                    raise ValueError("invalid filesystem/subvolume identity")
                if e.new_subvolid and (e.new_subvolid <= 5 or e.new_subvolid == e.old_subvolid):
                    raise ValueError("invalid clone identity")
                for name in (e.live_name, e.staged_name, e.retired_name):
                    if not name or name in (".", "..") or "/" in name or "\x00" in name:
                        raise ValueError("invalid transaction name")
                for rel in (e.parent_rel, e.source_rel):
                    if Path(rel).is_absolute() or ".." in Path(rel).parts or "\x00" in rel:
                        raise ValueError("invalid transaction path")
                if e.live_name in (e.staged_name, e.retired_name):
                    raise ValueError("live and transient names overlap")
                if raw.get("operation", "restore") == "restore" and e.staged_name == e.retired_name:
                    raise ValueError("staged and retired names overlap")
            out.append(Journal(txn=str(raw["txn"]), fs_uuid=raw["fs_uuid"],
                               state=raw["state"], started=str(raw.get("started", "")),
                               entries=entries, path=path, operation=raw.get("operation", "restore")))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            die(f"[!] Cannot read transaction {path}: {exc}. Resolve it before modifying this filesystem.")
    return out


# =============================================================================
# RESTORE ENGINE
# =============================================================================
@dataclass(slots=True)
class RestoreTarget:
    config: str
    snap_id: str
    mountpoint: str
    fs: Filesystem
    active_path: str
    active_id: int
    snapshots_path: str


@dataclass(slots=True)
class RestorePlan:
    target: RestoreTarget
    top: Path
    parent_rel: str
    live_name: str
    staged_name: str
    retired_name: str
    source_rel: str
    dirfd: int = -1
    exchanged: bool = False
    retired_named: bool = False
    new_subvol_id: int = 0
    default_before: int = 0
    scheduled_unit: str = ""

    @property
    def parent(self) -> Path:
        return self.top / self.parent_rel if self.parent_rel else self.top

    @property
    def live(self) -> Path:
        return self.parent / self.live_name

    @property
    def staged(self) -> Path:
        return self.parent / self.staged_name

    @property
    def retired(self) -> Path:
        return self.parent / self.retired_name

    @property
    def source(self) -> Path:
        return self.top / self.source_rel

    @property
    def retired_rel(self) -> str:
        return f"{self.parent_rel}/{self.retired_name}" if self.parent_rel else self.retired_name

    def journal_entry(self) -> JournalEntry:
        return JournalEntry(
            config=self.target.config,
            mountpoint=self.target.mountpoint,
            fs_uuid=self.target.fs.uuid,
            parent_rel=self.parent_rel,
            live_name=self.live_name,
            staged_name=self.staged_name,
            retired_name=self.retired_name,
            source_rel=self.source_rel,
            old_subvolid=self.target.active_id,
            new_subvolid=self.new_subvol_id,
            default_before=self.default_before,
            exchanged=self.exchanged,
            retired_named=self.retired_named,
        )


def resolve_target(config: str, snap_id: str) -> RestoreTarget:
    snap_id = validate_snap_id(snap_id)
    mountpoint = snapper_config_subvolume(config)
    if not is_mountpoint(mountpoint):
        die(f"[!] The subvolume for config {config!r} ({mountpoint}) is not mounted; cannot resolve it safely.")

    fs = filesystem_of(mountpoint)
    resolved = active_subvol(mountpoint)
    if resolved is None:
        die(f"[!] Could not resolve the active subvolume for {mountpoint}.")
    active_path, active_id = resolved
    if any(transient_kind(part) for part in Path(active_path).parts):
        die(f"[!] {mountpoint} still serves retired/staged state {active_path!r}. "
            "Reboot or activate the committed restore before restoring again.")

    snaps_mnt = snapshots_mountpoint(mountpoint)
    snaps = active_subvol(snaps_mnt, required=False)
    if snaps is None:
        die(
            f"[!] {snaps_mnt} is not a mounted btrfs subvolume. Snapper's snapshot store must be a real "
            "subvolume (for example @snapshots mounted at /.snapshots) for atomic rollback."
        )
    snaps_fs = filesystem_of(snaps_mnt)
    if snaps_fs.uuid != fs.uuid:
        die(f"[!] {snaps_mnt} lives on UUID={snaps_fs.uuid} but {mountpoint} is on UUID={fs.uuid}.")

    return RestoreTarget(
        config=config,
        snap_id=snap_id,
        mountpoint=mountpoint,
        fs=fs,
        active_path=active_path,
        active_id=active_id,
        snapshots_path=snaps[0],
    )


def build_plan(target: RestoreTarget, top: Path, stamp: str, salt: str) -> RestorePlan:
    rel = target.active_path.strip("/")
    parent_rel, _, live_name = rel.rpartition("/")
    return RestorePlan(
        target=target,
        top=top,
        parent_rel=parent_rel,
        live_name=live_name,
        staged_name=f"{live_name}{TAG_STAGED}{target.snap_id}_{stamp}_{salt}",
        retired_name=f"{live_name}{TAG_RETIRED}{stamp}_{salt}",
        source_rel=f"{target.snapshots_path.strip('/')}/{target.snap_id}/snapshot",
    )


def assert_flat_topology(plan: RestorePlan) -> None:
    """
    Refuse to restore a subvolume that physically contains nested subvolumes.

    btrfs snapshots are not recursive (btrfs-subvolume(8)): nested subvolumes
    appear as empty directories in the clone, and after activation the
    originals are trapped inside the retired subvolume, which then cannot even
    be deleted - BTRFS_IOC_SNAP_DESTROY returns ENOTEMPTY.  The classic failure
    is a snapper 'root' config whose /.snapshots is nested inside @ instead of
    being a separate top-level @snapshots subvolume; restoring it would orphan
    every snapshot you own.
    """
    prefix = plan.target.active_path.strip("/") + "/"
    nested = [i.path for i in subvol_list(plan.top) if i.path.startswith(prefix)]
    if nested:
        listed = "\n".join(f"      - {n}" for n in nested)
        die(
            f"\n[!] CRITICAL HALT: {plan.target.active_path!r} physically contains nested subvolumes:\n"
            f"{listed}\n"
            "[!] An atomic rollback would strand them inside the retired subvolume and make it\n"
            "    undeletable (ENOTEMPTY). Move them to the top level (for example @snapshots,\n"
            "    @var_log, @var_cache) and mount them via fstab before restoring."
        )


def preflight(plans: Sequence[RestorePlan], stack: ExitStack, *, allow_default_fixup: bool) -> None:
    seen: set[str] = set()
    for plan in plans:
        if any(transient_kind(part) for part in Path(plan.target.active_path).parts):
            die("[!] Reboot or remount the committed restore before restoring again.")
        key = f"{plan.target.fs.uuid}:{plan.target.active_path}"
        if key in seen:
            die(f"[!] Two restore targets resolve to the same subvolume: {key}")
        seen.add(key)

        if not plan.source.is_dir():
            die(f"[!] Snapshot {plan.target.snap_id} of {plan.target.config!r} is missing at {plan.source}")
        info = subvol_show(plan.source)
        if info is None:
            die(f"[!] {plan.source} is not a btrfs subvolume.")
        if not info.readonly:
            warn(
                f"[!] Snapshot {plan.target.snap_id} of {plan.target.config!r} is WRITABLE; its content "
                "may have drifted since it was taken."
            )
        if not plan.live.is_dir():
            die(f"[!] Active subvolume missing at {plan.live}")
        live_info = subvol_show(plan.live)
        if live_info is None or live_info.subvolid != plan.target.active_id:
            die(
                f"[!] {plan.live} does not match the live mount of {plan.target.mountpoint} "
                f"(expected subvolume id {plan.target.active_id}, got {live_info.subvolid if live_info else 'none'})."
            )
        # lexists, not exists: a DANGLING symlink is invisible to exists() and
        # would then make 'btrfs subvolume snapshot' fail with EEXIST later.
        if os.path.lexists(plan.staged) or os.path.lexists(plan.retired):
            die(f"[!] Transient path collision for {plan.target.config!r}; retry in a second.")
        if not SAFE_NAME_RE.fullmatch(plan.retired_name) or not SAFE_NAME_RE.fullmatch(plan.staged_name):
            die(f"[!] Refusing to generate transient names outside the safe charset for {plan.live_name!r}.")

        assert_flat_topology(plan)
        fstab = Path("/etc/fstab")
        if fstab.is_file():
            for line in fstab.read_text(encoding="utf-8", errors="replace").splitlines():
                fields = line.split()
                if fields and not fields[0].startswith("#") and len(fields) >= 4:
                    if fields[1] == plan.target.mountpoint and subvolid_from_options(fields[3]) is not None:
                        die(f"[!] fstab pins {fields[1]} to subvolid=. Replace it with subvol= before restoring.")
        for unit_file in _fstab_like_files()[1:]:
            text = unit_file.read_text(encoding="utf-8", errors="replace")
            where = re.search(r"^Where=(.*)$", text, re.MULTILINE)
            options = re.search(r"^Options=(.*)$", text, re.MULTILINE)
            if where and options and where.group(1).strip() == plan.target.mountpoint:
                if subvolid_from_options(options.group(1)) is not None:
                    die(f"[!] {unit_file} pins {plan.target.mountpoint} to subvolid=. Use subvol= before restoring.")
        if plan.target.mountpoint == "/":
            python = Path(sys.executable)
            if not file_in_root(plan.source, python):
                die(f"[!] Snapshot lacks the cleanup interpreter {python}; install Python 3.15 "
                    "in the snapshot before restoring.")
            fstab = plan.source / "etc/fstab"
            if fstab.is_file():
                for line in fstab.read_text(encoding="utf-8", errors="replace").splitlines():
                    fields = line.split()
                    if not fields or fields[0].startswith("#") or len(fields) < 4:
                        continue
                    if fields[1] in {p.target.mountpoint for p in plans} and subvolid_from_options(fields[3]) is not None:
                        die(f"[!] Snapshot fstab pins {fields[1]} to subvolid=. Use subvol= paths before restoring.")

        plan.default_before = get_default_subvolid(plan.top)
        if plan.default_before == plan.target.active_id and not allow_default_fixup:
            die(
                f"[!] The filesystem default subvolume is id {plan.target.active_id}, exactly the subvolume "
                "being replaced. After the swap the bootloader would still select the OLD subvolume, which "
                "is scheduled for deletion - i.e. an unbootable system. Re-run with --fix-default so Dusky "
                "repoints the default inside the same signal-blocked critical section."
            )

        # Capture the parent directory descriptor NOW and do every rename
        # relative to it, so nothing between validation and commit can
        # substitute a different directory (renameat2(2), path resolution).
        plan.dirfd = stack.enter_context(open_dir(plan.parent))
        parent_info = subvol_show(plan.parent)
        if parent_info is None or parent_info.subvolid != BTRFS_FS_TREE_OBJECTID:
            if plan.parent_rel == "":
                die(f"[!] {plan.parent} is not the FS_TREE of UUID={plan.target.fs.uuid}.")


def activate(plans: Sequence[RestorePlan], journal: Journal, top: Path, *, fix_default: bool) -> None:
    """Exchange each verified clone; retain the journal on any partial failure."""
    try:
        with critical_section():
            journal.commit(top, "activating")
            for plan in plans:
                current, staged = subvol_show(plan.live), subvol_show(plan.staged)
                if current is None or current.subvolid != plan.target.active_id:
                    die("[!] Live subvolume changed after preflight.")
                if staged is None or staged.subvolid != plan.new_subvol_id or staged.readonly:
                    die("[!] Staged clone changed after preflight.")
                note(f"[*] Activating {plan.target.config!r}...")
                rename_exchange_at(plan.dirfd, plan.live_name, plan.staged_name)
                plan.exchanged = True
                journal.entries = [p.journal_entry() for p in plans]
                journal.commit(top, "activated")
            for plan in plans:
                rename_noreplace_at(plan.dirfd, plan.staged_name, plan.retired_name)
                plan.retired_named = True
                if fix_default and plan.default_before == plan.target.active_id:
                    set_default_subvolid(plan.new_subvol_id, plan.top)
            journal.entries = [p.journal_entry() for p in plans]
            journal.commit(top, "activated")
    except (OSError, DuskyError) as exc:
        die(f"[!] Restore interrupted: {exc}\n"
            f"    Transaction {journal.txn} remains journalled. Run --recover to complete "
            "or --recover --abort to unwind before rebooting.", 75)


def audit_boot_consistency(root_dir: Path) -> list[str]:
    """Check external kernel images against images shipped with restored modules."""
    problems: list[str] = []
    modules_dir = root_dir / "usr/lib/modules"
    if not modules_dir.is_dir():
        return ["restored root has no /usr/lib/modules directory"]
    available = [p for p in modules_dir.iterdir()
                 if p.is_dir() and re.prefixmatch(r"\d+\.\d+", p.name)]
    if not available:
        return ["restored root has no installed kernel modules"]
    for tree in available:
        if not (tree / "modules.dep").is_file():
            problems.append(f"modules tree {tree.name} has no modules.dep")

    def digest(path: Path) -> bytes:
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").digest()

    kernels = {digest(p / "vmlinuz") for p in available if (p / "vmlinuz").is_file()}
    esps = [p for p in (Path("/efi"), Path("/boot"), Path("/boot/efi"))
            if p.is_dir() and is_mountpoint(p)]
    for esp in esps:
        images = set(esp.glob("vmlinuz-*"))
        for entry in sorted((esp / "loader/entries").glob("*.conf")):
            text = entry.read_text(encoding="utf-8", errors="replace")
            for match in re.finditer(r"^\s*(linux|initrd)\s+(\S+)", text, re.MULTILINE):
                image = esp / match.group(2).lstrip("/")
                if not image.is_file():
                    problems.append(f"boot entry {entry.name} references missing {image}")
                elif match.group(1) == "linux":
                    images.add(image)
            for match in re.finditer(r"^\s*options\s+(.*)$", text, re.MULTILINE):
                if subvolid_from_options(match.group(1)) is not None:
                    problems.append(f"boot entry {entry.name} pins subvolid=; rollback changes that ID")
        for image in images:
            if not kernels:
                problems.append(f"cannot verify {image}: restored modules have no vmlinuz images")
            elif digest(image) not in kernels:
                problems.append(f"{image} does not match any kernel in the restored modules tree")
        if any((esp / "EFI/Linux").glob("*.efi")):
            problems.append(f"UKIs on {esp} require a separate kernel/modules consistency check")
    return sorted(set(problems))


def perform_restore(
    targets: Sequence[RestoreTarget], *, fix_default: bool, assume_yes: bool
) -> list[RestorePlan]:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    salt = uuidlib.uuid4().hex[:8]
    by_uuid: dict[str, Filesystem] = {t.fs.uuid: t.fs for t in targets}
    if len(by_uuid) > 1:
        warn(
            "[!] This transaction spans multiple filesystems. Only per-filesystem atomicity is possible; "
            "Dusky journals the intent so --recover can complete or unwind it."
        )

    with dusky_lock(), ExitStack() as stack:
        invalidate_cache()
        tops = {fs_uuid: stack.enter_context(top_level(fs)) for fs_uuid, fs in by_uuid.items()}
        for fs_uuid, top in tops.items():
            assert_no_open_transactions(top)
            if not probe_exchange(top):
                die(
                    f"[!] RENAME_EXCHANGE is not usable on UUID={fs_uuid}. Dusky will not perform a "
                    "non-atomic rollback. Check kernel and btrfs-progs versions."
                )

        plans = [build_plan(t, tops[t.fs.uuid], stamp, salt) for t in targets]
        preflight(plans, stack, allow_default_fixup=fix_default)

        say()
        for plan in plans:
            say(
                f"  {C_ACCENT}{plan.target.config:<8}{C_RESET} {C_DIM}{plan.target.mountpoint}{C_RESET}  "
                f"{plan.target.active_path} (id {plan.target.active_id}) <- snapshot "
                f"{C_INFO}{plan.target.snap_id}{C_RESET}"
            )
        if not confirm("Commit this atomic rollback?", assume_yes=assume_yes):
            raise DuskyAbort("[*] Aborted; nothing was modified.")

        primary_uuid = plans[0].target.fs.uuid
        journal = Journal(
            txn=uuidlib.uuid4().hex,
            fs_uuid=primary_uuid,
            state="prepared",
            started=datetime.now(UTC).isoformat(),
            entries=[p.journal_entry() for p in plans],
            tops=tops,
        )
        journal.commit(tops[primary_uuid], "prepared")

        try:
            for plan in plans:
                note(f"[*] Staging writable clone for {plan.target.config!r}: {plan.staged_name}")
                created = run(
                    "btrfs", "subvolume", "snapshot", str(plan.source), str(plan.staged), timeout=NO_TIMEOUT
                )
                if not created.ok:
                    die(f"[!] Failed to stage clone for {plan.target.config!r}:\n    {created.message}")
                staged_info = subvol_show(plan.staged)
                if staged_info is None:
                    die("[!] Cannot identify the staged snapshot.")
                plan.new_subvol_id = staged_info.subvolid
                if subvol_is_ro(plan.staged):
                    subvol_set_ro(plan.staged, False)
            journal.entries = [p.journal_entry() for p in plans]
            journal.commit(tops[primary_uuid], "prepared")
        except BaseException:
            # Retain the prepared journal. Recovery can identify and remove
            # an interrupted snapshot even if its ID was not yet recorded.
            warn("[!] Staging interrupted. Run --recover --abort before retrying.")
            raise

        activate(plans, journal, tops[primary_uuid], fix_default=fix_default)
        finalise(plans, journal, tops)

        root_plan = next((p for p in plans if p.target.mountpoint == "/"), None)
        if root_plan is not None:
            issues = audit_boot_consistency(root_plan.live)
            if issues:
                warn("[!] BOOT CONSISTENCY WARNINGS for the restored root:")
                for issue in issues:
                    warn(f"      - {issue}")
                warn("    Regenerate the initramfs / reinstall the matching kernel BEFORE rebooting, e.g.")
                warn("      Mount the restored root and its boot partition, then run kernel/initramfs repair inside arch-chroot.")
        return plans


def finalise(plans: Sequence[RestorePlan], journal: Journal, tops: dict[str, Path]) -> None:
    root_plan = next((p for p in plans if p.target.mountpoint == "/"), None)
    offline_root = root_plan.live if root_plan is not None else None

    for top in tops.values():
        btrfs_sync(top)

    cleanup_errors: list[str] = []
    for plan in plans:
        top = plan.top
        rel = plan.retired_rel
        old_id = plan.target.active_id
        if not os.path.lexists(plan.retired):
            continue
        info = subvol_show(plan.retired)
        if info is None or info.subvolid != old_id:
            die(f"[!] Retired subvolume identity changed at {rel}.")
        default_now = get_default_subvolid(top)
        if default_now == old_id:
            die(
                f"[!] REFUSING to schedule deletion of {rel}: it is still the filesystem default "
                f"subvolume (id {old_id}). Re-run with --fix-default or set it manually.",
                71,
            )

        # The retired subvolume is still serving a live mount whenever the
        # mountpoint is up: for '/' always, and for '/home' until we remount.
        # Compare SUBVOLUME IDS, never mountpoint-ness: after the exchange the
        # retired subvolume is not a mountpoint at its new top-level path, so a
        # naive mountpoint test happily authorises deleting the running system.
        busy_at = live_mount_of_subvolid(plan.target.fs.uuid, old_id)
        if busy_at is None:
            note(f"[*] Deleting the previous state of {plan.target.config!r} ({rel})...")
            deleted = run("btrfs", "subvolume", "delete", "--commit-after", "--", str(plan.retired),
                          timeout=NO_TIMEOUT)
            if deleted.ok:
                btrfs_sync(top)
                continue
            warn(f"[!] Immediate deletion failed ({deleted.message}); deferring to boot.")
        else:
            note(f"[*] {busy_at} still serves subvolume id {old_id}; deferring deletion of {rel} to next boot.")

        try:
            unit = schedule_boot_cleanup(
                fs_uuid=plan.target.fs.uuid,
                subvol_rel=rel,
                subvolid=old_id,
                offline_root=offline_root,
            )
            plan.scheduled_unit = unit
            good(f"[+] Scheduled {unit} to reclaim {rel}.")
        except (OSError, DuskyError) as exc:
            cleanup_errors.append(rel)
            warn(
                f"[!] Could not schedule boot cleanup for {rel}: {exc}. "
                "Correct the cause, then run --recover to retry."
            )

    if cleanup_errors:
        die(f"[!] Restore activated, but cleanup could not be scheduled for {', '.join(cleanup_errors)}. "
            "The journal was retained; run --recover to retry finalisation.", 75)

    journal.entries = [p.journal_entry() for p in plans]
    journal.commit(tops[journal.fs_uuid], "finalised")
    journal.discard(tops[journal.fs_uuid])


def live_mount_of_subvolid(fs_uuid: str, subvolid: int) -> str | None:
    """Return a mountpoint currently serving this subvolume id, or None."""
    for entry in findmnt_entries():
        if str(entry.get("fstype")) != "btrfs":
            continue
        if str(entry.get("uuid") or "").strip() != fs_uuid:
            continue
        target = str(entry.get("target") or "")
        if not target:
            continue
        mounted_id = subvolid_from_options(str(entry.get("options") or ""))
        if mounted_id is None:
            die(f"[!] Cannot identify the mounted subvolume at {target}.")
        if mounted_id == subvolid:
            return target
    return None


def systemd_mount_unit(mountpoint: str) -> str | None:
    proc = run("systemd-escape", "--path", "--suffix=mount", mountpoint)
    if not proc.ok or not proc.text:
        return None
    unit = proc.text
    state = run("systemctl", "show", "-p", "LoadState", "--value", unit)
    return unit if state.ok and state.text == "loaded" else None


def reactivate_mount(mountpoint: str, expected_id: int) -> bool:
    """
    Swap a non-root mount onto the restored subvolume without rebooting.

    Goes through systemd when the mountpoint is unit-managed.  A bare
    'umount && mount' pair (v3.0.0) desynchronises systemd's mount unit state
    machine: systemd observes the unmount, considers the unit inactive, and may
    stop everything that has RequiresMountsFor= on it - or re-mount the OLD
    subvolume from its cached parameters.

    Never uses 'umount -l': a lazy unmount leaves processes writing into the
    retired subvolume, and those writes vanish at the next boot.
    """
    invalidate_cache()
    if not is_mountpoint(mountpoint):
        note(f"[*] {mountpoint} is not mounted; the restored subvolume applies at the next mount.")
        return True

    children = [
        e for e in findmnt_entries()
        if str(e.get("target", "")).startswith(mountpoint.rstrip("/") + "/")
    ]
    if children:
        warn(
            f"[!] {mountpoint} has submounts ({', '.join(str(c.get('target')) for c in children)}); "
            "skipping live remount."
        )
        return False

    unit = systemd_mount_unit(mountpoint)
    if not unit:
        warn(f"[!] No systemd mount unit for {mountpoint}; reboot to activate the restore.")
        return False
    note(f"[*] Remounting {mountpoint} via {unit}...")
    result = run("systemctl", "restart", unit, timeout=120.0)
    ok, message = result.ok, result.message
    current = subvol_show(mountpoint) if is_mountpoint(mountpoint) else None
    if ok and (current is None or current.subvolid != expected_id):
        ok, message = False, "mount did not select the restored subvolume; check its subvol/subvolid options"
    if not ok:
        warn(
            f"[!] Could not activate {mountpoint}: {message}.\n"
            "[!] The restore is committed on disk. Check the mount before further writes; reboot to activate it."
        )
        return False

    invalidate_cache()
    good(f"[+] {mountpoint} is now serving the restored snapshot.")
    return True


def reclaim_after_remount(fs_uuid: str, retired_rel: str, old_subvolid: int, unit_name: str) -> None:
    """
    After a successful live remount the retired subvolume is idle, so reclaim
    it now and cancel the boot unit.  v3.0.0 always deferred (its 'busy' test
    was mountpoint-based and therefore always true) and never cancelled the
    unit, so space stayed pinned until the next reboot.
    """
    if live_mount_of_subvolid(fs_uuid, old_subvolid) is not None:
        return
    fs = Filesystem(uuid=fs_uuid, source="")
    with dusky_lock(), top_level(fs, quiet=True) as top:
        assert_no_open_transactions(top)
        victim = top / retired_rel
        if not os.path.lexists(victim):
            cancel_boot_cleanup(unit_name)
            return
        info = subvol_show(victim)
        if info is None or info.subvolid != old_subvolid:
            warn(f"[!] {retired_rel} no longer has subvolume id {old_subvolid}; leaving the boot unit in place.")
            return
        deleted = run("btrfs", "subvolume", "delete", "--commit-after", "--", str(victim), timeout=NO_TIMEOUT)
        if deleted.ok:
            btrfs_sync(top)
            if unit_name:
                cancel_boot_cleanup(unit_name)
            good(f"[+] Reclaimed {retired_rel} immediately; boot cleanup cancelled.")
        else:
            warn(f"[!] Immediate reclaim failed ({deleted.message}); the boot unit will retry.")


# =============================================================================
# RECOVERY / UNDO
# =============================================================================
@dataclass(slots=True)
class EntryState:
    """Ground truth for one journal entry, derived from the filesystem."""

    entry: JournalEntry
    top: Path
    parent: Path
    exchanged: bool           # live_name currently holds a subvolume != old_subvolid
    old_at: str               # basename currently holding old_subvolid, or ""
    new_at: str               # basename currently holding the staged clone, or ""


def _inspect_entry(entry: JournalEntry, top: Path) -> EntryState:
    """
    Determine what actually happened, by SUBVOLUME ID.

    Trusting the journal's boolean flags is unsound: a crash can land between
    the rename and the journal commit that records it, and acting on a stale
    'exchanged=False' would delete the operator's pre-restore root under the
    belief that it is a staged clone.  Identity is the only safe discriminator.
    """
    parent = top / entry.parent_rel if entry.parent_rel else top
    def id_of(name: str) -> int | None:
        path = parent / name
        if not os.path.lexists(path):
            return None
        info = subvol_show(path)
        if info is None:
            die(f"[!] Recovery found a non-subvolume object at {path}; leaving it untouched.")
        return info.subvolid

    identities = {name: id_of(name) for name in
                  (entry.live_name, entry.staged_name, entry.retired_name)}
    if not entry.new_subvolid:
        # A crash during staging may precede recording the clone ID.
        candidate = subvol_show(parent / entry.staged_name)
        source = subvol_show(top / entry.source_rel)
        if candidate and source and candidate.parent_uuid == source.uuid:
            entry.new_subvolid = candidate.subvolid
    allowed = {entry.old_subvolid, entry.new_subvolid}
    if identities[entry.live_name] is None:
        die(f"[!] Recovery cannot find live subvolume {entry.live_name}.")
    for name, sid in identities.items():
        if sid is not None and sid not in allowed:
            die(f"[!] Recovery found unexpected subvolume id {sid} at {name}; leaving it untouched.")
    old_at = next((n for n, sid in identities.items() if sid == entry.old_subvolid), "")
    new_at = next((n for n, sid in identities.items() if sid == entry.new_subvolid), "")
    exchanged = identities[entry.live_name] == entry.new_subvolid
    entry.exchanged = exchanged
    entry.retired_named = bool(old_at == entry.retired_name and exchanged)
    return EntryState(entry=entry, top=top, parent=parent, exchanged=exchanged,
                      old_at=old_at, new_at=new_at)


def _recover_journal(
    primary_top: Path, primary_fs: Filesystem, journal: Journal, *, abort: bool, assume_yes: bool
) -> None:
    if primary_fs.uuid != journal.fs_uuid:
        coordinator = Filesystem(journal.fs_uuid, "")
        with top_level(coordinator, quiet=True) as coordinator_top:
            records = [j for j in load_journals(coordinator_top) if j.txn == journal.txn]
            if not records and journal.state == "prepared":
                # Initial replica writes precede the coordinator and all staging.
                # Inspect identities and unwind this unstarted transaction.
                _recover_journal(coordinator_top, coordinator, journal, abort=True, assume_yes=assume_yes)
                return
            if len(records) != 1:
                die(f"[!] Missing coordinator journal for {journal.txn}; manual inspection required.")
            _recover_journal(coordinator_top, coordinator, records[0], abort=abort, assume_yes=assume_yes)
        return
    say(f"{C_WARN}[*] Transaction {journal.txn} on UUID={journal.fs_uuid} state={journal.state}{C_RESET}")

    with ExitStack() as stack:
        tops: dict[str, Path] = {primary_fs.uuid: primary_top}
        for entry in journal.entries:
            uuid = entry.fs_uuid or primary_fs.uuid
            if uuid not in tops:
                tops[uuid] = stack.enter_context(top_level(Filesystem(uuid, ""), quiet=True))

        journal.tops = tops
        if journal.state == "finalised":
            journal.discard(primary_top)
            good(f"[+] Removed completed transaction {journal.txn}.")
            return

        states = [_inspect_entry(e, tops[e.fs_uuid or primary_fs.uuid]) for e in journal.entries]
        for st in states:
            say(
                f"    {st.entry.config:<8} {st.entry.mountpoint:<10} live={st.entry.live_name} "
                f"exchanged={st.exchanged} old_id={st.entry.old_subvolid} "
                f"old_at={st.old_at or '-'} clone_at={st.new_at or '-'}"
            )

        roll_forward = journal.state in ("activating", "activated") and not abort

        if roll_forward:
            pending = [s for s in states if not s.exchanged]
            if pending and not confirm(
                f"Complete this transaction by exchanging {len(pending)} remaining subvolume(s)?",
                assume_yes=assume_yes,
            ):
                raise DuskyAbort("[*] Left untouched. Re-run with --recover --abort to unwind instead.")
            with critical_section():
                for st in states:
                    entry = st.entry
                    if not st.exchanged:
                        clone = subvol_show(st.parent / entry.staged_name)
                        if st.new_at != entry.staged_name or clone is None or clone.subvolid != entry.new_subvolid:
                            die(
                                f"[!] Cannot roll {entry.config!r} forward: the staged clone "
                                f"{entry.staged_name} is gone. Re-run with --recover --abort.",
                                75,
                            )
                        if journal.operation == "restore" and clone.readonly:
                            die(f"[!] Staged clone {entry.staged_name} is read-only; cannot activate it.", 75)
                        with open_dir(st.parent) as dfd:
                            rename_exchange_at(dfd, entry.live_name, entry.staged_name)
                        st.exchanged = True
                        entry.exchanged = True
                        journal.commit(primary_top, "activated")
                    # Give the displaced old subvolume its retired name.
                    if entry.staged_name != entry.retired_name and os.path.lexists(st.parent / entry.staged_name):
                        info = subvol_show(st.parent / entry.staged_name)
                        if info is not None and info.subvolid == entry.old_subvolid:
                            with open_dir(st.parent) as dfd:
                                rename_noreplace_at(dfd, entry.staged_name, entry.retired_name)
                                entry.retired_named = True
                    if entry.default_before and entry.default_before == entry.old_subvolid:
                        live = subvol_show(st.parent / entry.live_name)
                        if live is not None:
                            set_default_subvolid(live.subvolid, st.top)
            plans = []
            for st in states:
                entry = st.entry
                current = _inspect_entry(entry, st.top)
                target = RestoreTarget(entry.config, "", entry.mountpoint,
                                       Filesystem(entry.fs_uuid, ""),
                                       entry.rel_of(entry.live_name), entry.old_subvolid, "")
                plans.append(RestorePlan(
                    target=target, top=st.top, parent_rel=entry.parent_rel,
                    live_name=entry.live_name, staged_name=entry.staged_name,
                    retired_name=current.old_at or entry.retired_name,
                    source_rel=entry.source_rel, new_subvol_id=entry.new_subvolid,
                    default_before=entry.default_before, exchanged=True, retired_named=True))
            if journal.operation == "undo":
                for st in states:
                    unit = cleanup_unit_name(st.entry.fs_uuid, st.entry.rel_of(st.entry.retired_name))
                    cancel_boot_cleanup(unit)
                    if st.entry.mountpoint == "/":
                        cancel_boot_cleanup(unit, offline_root=st.parent / st.entry.live_name)
                journal.commit(primary_top, "finalised")
                journal.discard(primary_top)
            else:
                finalise(plans, journal, tops)
            good("[+] Transaction completed. Reboot to run on the restored subvolume(s).")
            return

        # ---- unwind -------------------------------------------------------
        if not confirm("Unwind this transaction to the pre-restore state?", assume_yes=assume_yes):
            raise DuskyAbort("[*] Left untouched.")
        if any(st.exchanged and not st.old_at for st in states):
            die("[!] Cannot unwind: a pre-restore subvolume has already been reclaimed.")
        with critical_section():
            journal.commit(primary_top, "unwinding")
            for st in reversed(states):
                entry = st.entry
                if st.exchanged:
                    if not st.old_at or st.old_at == entry.live_name:
                        die(
                            f"[!] Cannot unwind {entry.config!r}: the pre-restore subvolume "
                            f"(id {entry.old_subvolid}) is not where the journal says it is. "
                            "Inspect manually before doing anything else.",
                            70,
                        )
                    with open_dir(st.parent) as dfd:
                        rename_exchange_at(dfd, entry.live_name, st.old_at)
                    st.exchanged = False
                    entry.exchanged = False
                if entry.default_before:
                    current = get_default_subvolid(st.top)
                    if current is not None and current != entry.default_before:
                        set_default_subvolid(entry.default_before, st.top)
        # Only now drop the clones: after the unwind, anything still carrying a
        # Dusky transient name whose id is NOT old_subvolid is a staged clone.
        for st in states:
            entry = st.entry
            for name in (() if journal.operation == "undo" else (entry.staged_name, entry.retired_name)):
                path = st.parent / name
                if not os.path.lexists(path) or transient_kind(name) is None:
                    continue
                info = subvol_show(path)
                if info is None or info.subvolid != entry.new_subvolid:
                    continue
                if live_mount_of_subvolid(entry.fs_uuid, info.subvolid) is not None:
                    warn(f"[!] {name} is serving a live mount; leaving it in place.")
                    continue
                deleted = run("btrfs", "subvolume", "delete", "--", str(path), timeout=NO_TIMEOUT)
                if not deleted.ok:
                    die(f"[!] Paths unwound, but clone cleanup failed at {path}: {deleted.message}. "
                        "Run --recover --abort to retry.", 75)
            btrfs_sync(st.top)
        journal.discard(primary_top)
        good("[+] Pre-restore paths restored. Existing mounts are unchanged; reboot to use them.")


def cmd_recover(*, abort: bool, assume_yes: bool) -> None:
    found = 0
    with dusky_lock():
        for fs_uuid, (fs, _mount) in btrfs_filesystems().items():
            with top_level(fs, quiet=True) as top:
                for journal in load_journals(top):
                    found += 1
                    _recover_journal(top, fs, journal, abort=abort, assume_yes=assume_yes)
            del fs_uuid
    if not found:
        good("[+] No unfinished Dusky transactions.")


def cmd_undo(*, assume_yes: bool) -> None:
    """
    Undo the most recent committed restore, while the retired subvolume is
    still on disk.  Undo of a committed exchange is another RENAME_EXCHANGE, so
    it is exactly as atomic as the restore itself.
    """
    with dusky_lock():
        candidates: list[tuple[Filesystem, Path, str, Subvolume]] = []
        for _fs_uuid, (fs, _mount) in btrfs_filesystems().items():
            with top_level(fs, quiet=True) as top:
                for info in subvol_list(top):
                    if transient_kind(info.path) == "retired":
                        candidates.append((fs, top, info.path, Subvolume(
                            subvolid=info.subvolid, path=info.path, uuid=info.uuid,
                            received_uuid=info.received_uuid, fs_uuid=fs.uuid,
                            readonly=info.readonly, mount_target=str(top))))
        if not candidates:
            die("[!] Nothing to undo: no retired subvolume from a previous restore is on disk.")
    # Retired names embed an ISO-8601 UTC stamp, so lexical order is chronological.
    candidates.sort(key=lambda c: c[2].rsplit(TAG_RETIRED, 1)[-1], reverse=True)

    say(f"{C_WARN}[*] Retired subvolumes available for undo (most recent first):{C_RESET}")
    for index, (fs, _top, rel, sub) in enumerate(candidates, 1):
        say(f"  {index:>2}. UUID={fs.uuid} id={sub.subvolid} {rel}")
    choice = "1" if assume_yes else ask(f"{C_WARN}[*] Undo which one? [1]: {C_RESET}") or "1"
    if not choice.isdigit() or not 1 <= int(choice) <= len(candidates):
        die("[!] Invalid selection.")
    fs, _top, rel, sub = candidates[int(choice) - 1]

    live_name = rel.rsplit("/", 1)[-1].rsplit(TAG_RETIRED, 1)[0]
    parent_rel = rel.rpartition("/")[0]
    if not confirm_phrase(
        f"This will swap {live_name} back to the pre-restore subvolume id {sub.subvolid} "
        f"and DISCARD everything written since the restore.",
        "UNDO",
        assume_yes=assume_yes,
    ):
        raise DuskyAbort("[*] Aborted.")

    with dusky_lock(), top_level(fs) as top:
        assert_no_open_transactions(top)
        parent = top / parent_rel if parent_rel else top
        retired_name = rel.rsplit("/", 1)[-1]
        if not os.path.lexists(parent / live_name) or not os.path.lexists(parent / retired_name):
            die("[!] The expected pair of subvolume links no longer exists; refusing to guess.")
        # Re-prove identity: the listing was taken under a previous mount and a
        # concurrent sweep or boot unit may have changed the world since.
        retired_info = subvol_show(parent / retired_name)
        if retired_info is None or retired_info.subvolid != sub.subvolid:
            die(
                f"[!] {retired_name} no longer has subvolume id {sub.subvolid} "
                f"(now {retired_info.subvolid if retired_info else 'unreadable'}). Refusing to swap."
            )
        live_info = subvol_show(parent / live_name)
        if live_info is None or live_info.subvolid == sub.subvolid:
            die(f"[!] {live_name} does not look like the post-restore subvolume. Refusing to swap.")
        default_before = get_default_subvolid(top)
        entry = JournalEntry("undo", live_mount_of_subvolid(fs.uuid, sub.subvolid) or
                             live_mount_of_subvolid(fs.uuid, live_info.subvolid) or "", fs.uuid, parent_rel, live_name,
                             retired_name, retired_name, rel, live_info.subvolid,
                             sub.subvolid, default_before)
        journal = Journal(uuidlib.uuid4().hex, fs.uuid, "prepared",
                          datetime.now(UTC).isoformat(), [entry], operation="undo")
        with critical_section(), open_dir(parent) as dfd:
            journal.commit(top, "activating")
            rename_exchange_at(dfd, live_name, retired_name)
            entry.exchanged = True
            entry.retired_named = True
            if default_before == live_info.subvolid:
                set_default_subvolid(sub.subvolid, top)
            journal.commit(top, "activated")
            # The restored root may also carry the old cleanup unit.
            unit = cleanup_unit_name(fs.uuid, rel)
            cancel_boot_cleanup(unit)
            if (parent / live_name / UNIT_DIR_REL).is_dir():
                cancel_boot_cleanup(unit, offline_root=parent / live_name)
            journal.commit(top, "finalised")
            journal.discard(top)
        btrfs_sync(top)
    good(f"[+] Undo committed: {live_name} is the pre-restore subvolume again. Reboot to run on it.")


# =============================================================================
# BACKUP (btrfs send | btrfs receive)
# =============================================================================
def _purge_staging(staging: Path, *, fs_uuid: str | None = None) -> bool:
    """Remove idle received subvolumes; never traverse or erase ordinary data."""
    if not staging.exists():
        return True
    if staging.is_symlink() or not staging.is_dir():
        return False
    items = list(staging.iterdir())
    if items:
        if fs_uuid is None:
            fs_uuid = run("findmnt", "--noheadings", "--output", "UUID", "--target",
                          str(staging), check=True).text
        default_id = get_default_subvolid(staging)
        for item in items:
            info = None if item.is_symlink() else subvol_show(item)
            if info is None:
                warn(f"[!] Unrecognised receive staging object {item}; leaving it intact.")
                continue
            if info.subvolid == default_id or live_mount_of_subvolid(fs_uuid, info.subvolid) is not None:
                warn(f"[!] Receive staging subvolume {item} is mounted or default; leaving it intact.")
                continue
            result = run("btrfs", "subvolume", "delete", "--commit-after", "--", str(item),
                         timeout=NO_TIMEOUT)
            if not result.ok:
                warn(f"[!] Could not remove {item}: {result.message}")
    with suppress(OSError):
        staging.rmdir()
    return not staging.exists()


def backup_subvolume(fs: Filesystem, src_rel: str, destination: str, *, parent_rel: str | None = None) -> Path:
    dest = Path(destination).resolve()
    if not dest.is_dir():
        die(f"[!] Destination is not a directory: {dest}")
    fstype = run("stat", "-f", "-c", "%T", str(dest))
    if not fstype.ok or "btrfs" not in fstype.text.lower():
        die(f"[!] Destination {dest} is not btrfs; 'btrfs receive' requires a btrfs target.")
    mount_data = run("findmnt", "--json", "--target", str(dest),
                     "--output", "UUID,TARGET,FSROOT", check=True)
    try:
        dest_mount = json.loads(mount_data.stdout)["filesystems"][0]
        dest_uuid = dest_mount["uuid"]
        dest_relative = Path(dest_mount["fsroot"].lstrip("/")) / dest.relative_to(dest_mount["target"])
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        die(f"[!] Cannot resolve the backup destination mount: {exc}")
    if dest_uuid == fs.uuid:
        warn("[!] Destination is the SAME btrfs filesystem as the source. This is a reflink copy, not a backup.")

    # Keep temporary send/receive objects protected from every Dusky sweeper.
    with dusky_lock(), top_level(fs) as top, ExitStack() as stack:
        invalidate_cache()

        source = top / src_rel.strip("/")
        if not source.exists():
            die(f"[!] Source subvolume not found at the physical layer: {src_rel}")
        info = subvol_show(source)
        if info is None:
            die(f"[!] {src_rel} is not a btrfs subvolume.")

        send_source = source
        if not info.readonly:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            ephemeral = top / f"{TAG_SEND}{(source.name or 'root')}_{stamp}"
            note("[*] Source is writable; taking an ephemeral read-only snapshot for a consistent stream...")
            run("btrfs", "subvolume", "snapshot", "-r", str(source), str(ephemeral), check=True, timeout=NO_TIMEOUT)
            btrfs_sync(top)
            stack.callback(lambda: run("btrfs", "subvolume", "delete", "--", str(ephemeral), timeout=NO_TIMEOUT))
            send_source = ephemeral
        send_info = subvol_show(send_source)
        if send_info is None:
            die("[!] Could not describe the send source.")

        # Both ends use the installed btrfs-progs; the supported baseline can
        # transfer compressed extents directly with send protocol 2.
        argv = ["btrfs", "send", "--proto", "2", "--compressed-data"]
        if parent_rel:
            parent_abs = top / parent_rel.strip("/")
            if not parent_abs.exists():
                die(f"[!] Parent subvolume for the incremental stream not found: {parent_rel}")
            parent_info = subvol_show(parent_abs)
            if parent_info is None or not parent_info.readonly:
                die(f"[!] Incremental parent {parent_rel} must be a READ-ONLY subvolume (btrfs-send(8)).")
            # btrfs-receive(8): the parent must already exist at the
            # destination and be identifiable by received_uuid, otherwise
            # receive aborts with 'cannot find parent subvolume'.
            parent_stream_uuid = parent_info.received_uuid or parent_info.uuid
            if not _destination_has_parent(dest, parent_stream_uuid):
                die(
                    f"[!] The destination has no subvolume matching stream UUID {parent_stream_uuid} "
                    f"(the uuid of {parent_rel}). An incremental send would be unreceivable."
                )
            argv += ["-p", str(parent_abs)]
        argv.append(str(send_source))

        receive_top = stack.enter_context(top_level(Filesystem(dest_uuid, ""), quiet=True, allow_empty=True))
        staging = Path(tempfile.mkdtemp(dir=str(dest), prefix=TAG_RECV))
        stack.callback(lambda: _purge_staging(staging, fs_uuid=dest_uuid))
        receive_staging = receive_top / dest_relative / staging.name

        receive_argv = ["btrfs", "receive", "-m", str(receive_top), str(receive_staging)]
        note(f"[*] {shlex.join(argv)} | {shlex.join(receive_argv)}")
        send_err = stack.enter_context(tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace"))

        # send's stderr goes to a FILE, never a pipe: a pipe that nobody reads
        # while receive is still consuming stdout deadlocks at 64 KiB.
        send_proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=send_err, env=SUBPROCESS_ENV)
        stack.callback(reap_process, send_proc)
        assert send_proc.stdout is not None
        stack.callback(send_proc.stdout.close)
        recv_proc = subprocess.Popen(
            receive_argv,
            stdin=send_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=SUBPROCESS_ENV,
        )
        stack.callback(reap_process, recv_proc)
        send_proc.stdout.close()
        try:
            _, recv_err = recv_proc.communicate()
            send_rc = send_proc.wait()
        except KeyboardInterrupt:
            for proc in (recv_proc, send_proc):
                with suppress(OSError):
                    proc.terminate()
            raise
        send_err.seek(0)
        send_stderr = send_err.read().strip()

        if recv_proc.returncode != 0:
            die(f"[!] btrfs receive failed (rc={recv_proc.returncode}):\n{recv_err.strip()}\n{send_stderr}")
        if send_rc != 0:
            # -13 is SIGPIPE.  Treating it as success (v2) masks a truncated
            # stream whenever receive dies mid-transfer.
            reason = "SIGPIPE (receive exited early)" if send_rc == -signal.SIGPIPE else f"rc={send_rc}"
            die(f"[!] btrfs send failed: {reason}\n{send_stderr}")

        received = list(staging.iterdir())
        if len(received) != 1:
            die(f"[!] Expected exactly one received subvolume in staging, found {len(received)}.")
        item = received[0]

        got = subvol_show(item)
        if got is None:
            die("[!] The received object is not a btrfs subvolume.")
        if not got.received_uuid:
            die("[!] The received subvolume has no received_uuid: the stream did not complete cleanly.")
        stream_uuid = send_info.received_uuid or send_info.uuid
        if got.received_uuid != stream_uuid:
            die(f"[!] Integrity check failed: received_uuid {got.received_uuid} != stream UUID {stream_uuid}.")
        if not got.readonly:
            die("[!] The received subvolume is not read-only; 'btrfs receive' did not finalise it.")

        label = Path(src_rel).name or "root"
        if label == "snapshot":
            label = f"{Path(src_rel).parent.parent.name}_{Path(src_rel).parent.name}"
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        final = dest / f"dusky_backup_{label}_{stamp}"
        if os.path.lexists(final):
            die(f"[!] Backup target already exists: {final}")
        # Give received temporary send snapshots a permanent, non-transient name.
        if item.name != "snapshot":
            rename_noreplace(item, staging / "snapshot")
            item = staging / "snapshot"
        # A read-only subvolume cannot move between parent directories.
        # Publish its containing directory instead, preserving received_uuid.
        rename_noreplace(staging, final)
        final = final / item.name
        fsync_path(dest, is_dir=True)
        btrfs_sync(dest)
        good(f"[+] Backup complete: {final}")
        if info.readonly:
            say(f"{C_DIM}    received_uuid {got.received_uuid}; keep {src_rel} for --parent{C_RESET}")
        else:
            say(f"{C_DIM}    received_uuid {got.received_uuid}; temporary source removed (full backup){C_RESET}")
        return final


def _destination_has_parent(dest: Path, parent_uuid: str) -> bool:
    for info in subvol_list(dest):
        if not info.readonly:
            continue
        if info.received_uuid and info.received_uuid == parent_uuid:
            return True
        if info.uuid == parent_uuid:
            return True
    return False


# =============================================================================
# SWEEP
# =============================================================================
def sweep_orphans(*, apply: bool) -> int:
    """
    Reclaim Dusky's own transient artefacts.

    Holds the global lock for the whole sweep: v3.0.0 did not, so a concurrent
    '--sweep-apply' could delete the staged clone of an in-flight restore.
    Names are matched against the anchored transient grammar, never by
    substring, and retired subvolumes that are still serving a live mount are
    skipped.
    """
    count = 0
    seen_staging: set[tuple[int, int]] = set()
    with dusky_lock():
        invalidate_cache()
        for fs_uuid, (fs, _mount) in btrfs_filesystems().items():
            with top_level(fs, writable=apply, quiet=True) as top:
                assert_no_open_transactions(top)
                default_id: int | None = None
                for info in sorted(subvol_list(top), key=lambda i: i.path):
                    kind = transient_kind(info.path)
                    if kind is None:
                        continue
                    if default_id is None:
                        default_id = get_default_subvolid(top)
                    if info.subvolid == default_id:
                        say(f"  default   UUID={fs_uuid} {info.path}; skipped")
                        continue
                    if live_mount_of_subvolid(fs_uuid, info.subvolid) is not None:
                        say(f"  {C_ERR}in use{C_RESET} UUID={fs_uuid} {info.path} (serving a live mount)")
                        continue
                    count += 1
                    say(f"  {C_WARN}{kind:<9}{C_RESET} UUID={fs_uuid} id={info.subvolid} {info.path}")
                    if apply:
                        run("btrfs", "subvolume", "delete", "--", str(top / info.path),
                            check=True, timeout=NO_TIMEOUT)
                        cancel_boot_cleanup(cleanup_unit_name(fs_uuid, info.path))
                if apply:
                    btrfs_sync(top)

            for entry in findmnt_entries():
                if str(entry.get("fstype")) != "btrfs" or entry.get("uuid") != fs_uuid:
                    continue
                target = Path(str(entry.get("target") or ""))
                if not target.is_dir():
                    continue
                for staging in sorted(target.iterdir()):
                    if staging.is_symlink() or not staging.is_dir() or not RECV_STAGING_RE.fullmatch(staging.name):
                        continue
                    stat = staging.stat()
                    identity = (stat.st_dev, stat.st_ino)
                    if identity in seen_staging:
                        continue
                    seen_staging.add(identity)
                    count += 1
                    say(f"  {C_WARN}recv-stg {C_RESET} {staging}")
                    if apply and not _purge_staging(staging, fs_uuid=fs_uuid):
                        die(f"[!] Staging cleanup incomplete at {staging}; remaining objects were preserved.")
    return count


# =============================================================================
# COMMAND HANDLERS
# =============================================================================
def cmd_list(config: str, as_json: bool) -> int:
    if as_json:
        say(json.dumps(snapshot_rows(config), ensure_ascii=False))
        return 0
    return run_tty("snapper", "-c", config, "list")


def cmd_create(config: str, description: str) -> None:
    with dusky_lock():
        run("snapper", "-c", config, "create", "-d", description, check=True, timeout=NO_TIMEOUT)
        invalidate_cache()
    good(f"[+] Snapshot created for {config!r}.")


def cmd_create_pair(left: str, right: str, description: str) -> None:
    if left == right:
        die("[!] A coordinated pair requires two distinct configs.")
    pair_id = uuidlib.uuid4().hex
    stamp = datetime.now(UTC).isoformat()
    with dusky_lock():
        invalidate_cache()
        common = f"dusky_pair={pair_id},dusky_ts={stamp}"
        try:
            run("snapper", "-c", left, "create", "-d", description,
                "--userdata", f"{common},dusky_role={left}", check=True, timeout=NO_TIMEOUT)
            run("snapper", "-c", right, "create", "-d", description,
                "--userdata", f"{common},dusky_role={right}", check=True, timeout=NO_TIMEOUT)
        except BaseException:
            # A command may create its snapshot before losing its reply. Find
            # both halves by the operation's tag, never by a guessed number.
            warn(f"[!] Pair creation interrupted (dusky_pair={pair_id}); removing any created halves.")
            with critical_section():
                invalidate_cache()
                for config in (left, right):
                    try:
                        for row in snapshot_rows(config):
                            if row.get("userdata_dict", {}).get("dusky_pair") == pair_id:
                                run("snapper", "-c", config, "delete", row["id"],
                                    check=True, timeout=NO_TIMEOUT)
                    except (DuskyError, OSError) as exc:
                        warn(f"[!] Could not verify/remove {config!r} half of {pair_id}: {exc}")
            raise
        finally:
            invalidate_cache()
    good(f"[+] Coordinated snapshots created (dusky_pair={pair_id}).")


def cmd_modify_snapshots(
    targets: Sequence[tuple[str, str]], *, description: str | None = None, keep: bool = False,
) -> None:
    """Update selected metadata without replacing Snapper's pairing userdata."""
    targets = list(dict.fromkeys((config, validate_snap_id(number)) for config, number in targets))
    if not targets or (description is None and not keep):
        return
    options: list[str] = []
    if description is not None:
        options.extend(("--description", description))
    if keep:
        options.extend(("--cleanup-algorithm", ""))
    configs = dict.fromkeys(config for config, _ in targets)
    with dusky_lock():
        invalidate_cache()
        before = {(config, row["id"]): row for config in configs for row in snapshot_rows(config)}
        for config, number in targets:
            row = before.get((config, number))
            if row is None:
                die(f"[!] {config!r} has no snapshot {number}; no metadata was changed.")
            # Empty-pre-post cleanup ignores the cleanup algorithm, even when
            # it is unset. Only single snapshots can honestly be marked KEEP.
            if keep and row["type"] != "single":
                die("[!] Snapper can delete empty pre/post pairs even with cleanup disabled. "
                    "Create a single snapshot to keep instead; no metadata was changed.")
        completed: list[str] = []
        try:
            for config, number in targets:
                run("snapper", "-c", config, "modify", *options, number, check=True, timeout=NO_TIMEOUT)
                completed.append(f"{config}:{number}")
            invalidate_cache()
            after = {(config, row["id"]): row for config in configs for row in snapshot_rows(config)}
            for config, number in targets:
                row = after.get((config, number))
                expected = description
                if row and row["dead"] and expected is not None and not expected.startswith("[DEAD]"):
                    expected = f"[DEAD] {expected}"
                if row is None or (keep and row["cleanup"]) or (
                    expected is not None and row["description"] != expected
                ):
                    die(f"[!] Could not verify metadata for {config}:{number} after modification.")
        except BaseException:
            if completed:
                warn(f"[!] Metadata updates are not atomic across snapshots; "
                     f"completed commands: {', '.join(completed)}. Recheck the selected snapshots.")
            raise
        finally:
            invalidate_cache()
    good(f"[+] Updated {len(targets)} snapshot(s)" + ("; automatic cleanup disabled." if keep else "."))


_SNAPPER_META_ALLOWED = re.compile(r"\A(?:info\.xml|filelist-\d+\.txt(?:\.(?:gz|zst))?)\Z")


def cmd_delete(config: str, snap_id: str) -> None:
    snap_id = validate_snap_id(snap_id)
    with dusky_lock():
        invalidate_cache()
        result = run("snapper", "-c", config, "delete", snap_id, timeout=NO_TIMEOUT)
        invalidate_cache()
        if result.ok:
            # Snapper filters active/default snapshots out of the delete set
            # and can still exit zero. Verify the requested postcondition.
            if any(row["id"] == snap_id for row in snapshot_rows(config)):
                die(f"[!] Snapper left snapshot {snap_id} of {config!r} intact: {result.message}")
            good(f"[+] Deleted snapshot {snap_id} of {config!r}.")
            return

        target = snapper_config_subvolume(config)
        snaps_mnt = snapshots_mountpoint(target)
        if not Path(snaps_mnt).is_mount():
            die(f"[!] Failed to delete {snap_id}: {result.message}")
        meta_dir = Path(snaps_mnt) / snap_id
        subvol = meta_dir / "snapshot"
        if meta_dir.is_dir() and not os.path.lexists(subvol):
            # Snapper comparison caches may be plain, gzip or zstd files.
            metadata = list(meta_dir.iterdir())
            leftovers = {p.name for p in metadata}
            unexpected = {n for n in leftovers if not _SNAPPER_META_ALLOWED.fullmatch(n)}
            if unexpected or any(not p.is_file() for p in metadata):
                die(f"[!] Refusing to purge {meta_dir}: unexpected content {sorted(unexpected)}")
            for path in metadata:
                path.unlink()
            # Keep the number reserved, as Snapper does for UNIQUE_NUMBERS.
            # Removing the highest numbered directory permits ID reuse.
            fsync_path(meta_dir, is_dir=True)
            good(f"[+] Purged dead snapshot metadata {snap_id} of {config!r} (number reserved).")
            return
        die(f"[!] Failed to delete snapshot {snap_id} of {config!r}:\n    {result.message}")


def cmd_delete_pair(left: str, left_id: str, right: str, right_id: str) -> None:
    if left == right:
        die("[!] A coordinated delete requires two distinct configs.")
    left_id, right_id = validate_snap_id(left_id), validate_snap_id(right_id)
    with dusky_lock():
        invalidate_cache()
        for config, number in ((left, left_id), (right, right_id)):
            if not any(row["id"] == number for row in snapshot_rows(config)):
                die(f"[!] {config!r} has no snapshot {number}; neither half was deleted.")
        cmd_delete(left, left_id)
        try:
            cmd_delete(right, right_id)
        except (DuskyError, OSError):
            warn(f"[!] Partial deletion: {left!r} snapshot {left_id} was deleted; "
                 f"{right!r} snapshot {right_id} could not be deleted.")
            raise
    good("[+] Coordinated deletion complete.")


def _post_restore(plans: Sequence[RestorePlan], *, remount: bool) -> None:
    for plan in plans:
        if plan.target.mountpoint == "/":
            continue
        if not remount:
            warn(f"[!] {plan.target.mountpoint} still serves the retired subvolume until you remount or reboot.")
            continue
        if reactivate_mount(plan.target.mountpoint, plan.new_subvol_id):
            reclaim_after_remount(
                plan.target.fs.uuid, plan.retired_rel, plan.target.active_id, plan.scheduled_unit
            )


def cmd_restore(config: str, snap_id: str, *, remount: bool, fix_default: bool, assume_yes: bool) -> None:
    target = resolve_target(config, snap_id)
    plans = perform_restore([target], fix_default=fix_default, assume_yes=assume_yes)
    good(f"\n[+] Restore of {config!r} committed.")
    if target.mountpoint == "/":
        say(f"{C_ERR}[!] ROOT RESTORED. Reboot now; the running system is the retired subvolume.{C_RESET}")
        return
    _post_restore(plans, remount=remount)


def cmd_restore_pair(
    left: str, left_id: str, right: str, right_id: str,
    *, remount: bool, fix_default: bool, assume_yes: bool,
) -> None:
    if left == right:
        die("[!] A coordinated restore requires two distinct configs.")
    targets = [resolve_target(left, left_id), resolve_target(right, right_id)]
    if targets[0].active_path == targets[1].active_path and targets[0].fs.uuid == targets[1].fs.uuid:
        die("[!] Both configs resolve to the same subvolume.")
    plans = perform_restore(targets, fix_default=fix_default, assume_yes=assume_yes)
    good("\n[+] Coordinated restore committed.")
    if any(t.mountpoint == "/" for t in targets):
        say(f"{C_ERR}[!] ROOT RESTORED. Reboot now.{C_RESET}")
    _post_restore(plans, remount=remount)


def assert_no_open_transactions(top: Path) -> None:
    # A finalised replica may precede the coordinator's final commit. Only
    # recovery can reconcile and remove it; it must still block new changes.
    journals = load_journals(top)
    if journals:
        die(f"[!] Transaction {journals[0].txn} is unfinished or awaiting reconciliation; "
            "run --recover before modifying this filesystem.", 75)


def cmd_cleanup_subvol(fs_uuid: str, subvol_rel: str, *, expected_id: int | None = None) -> None:
    """
    Manual counterpart of the generated boot unit.

    Guardrails: only names matching Dusky's transient grammar are eligible, the
    victim must not be the filesystem default subvolume, and its subvolume id
    must not be serving any live mount.  A retired subvolume is normally still
    the running / until reboot, and it is NOT a mountpoint at its top-level
    path, so a naive mountpoint check would authorise deleting the filesystem
    out from under the running system.
    """
    rel = subvol_rel.strip("/")
    if ".." in Path(rel).parts or transient_kind(rel) is None:
        die(f"[!] Refusing to delete {rel!r}: it does not match Dusky's transient-artefact grammar.")
    fs_uuid = fs_uuid.strip()
    if not UUID_RE.fullmatch(fs_uuid):
        die(f"[!] {fs_uuid!r} is not a filesystem UUID.")

    fs = Filesystem(uuid=fs_uuid, source="")
    with dusky_lock(), top_level(fs, quiet=True) as top:
        assert_no_open_transactions(top)
        victim = top / rel
        if not os.path.lexists(victim):
            LOG.info("Cleanup target already gone: %s", rel)
            good(f"[+] {rel} is already gone.")
            cancel_boot_cleanup(cleanup_unit_name(fs_uuid, rel))
            return
        info = subvol_show(victim)
        if info is None:
            die(f"[!] {rel} is not a subvolume.")
        if expected_id is not None and info.subvolid != expected_id:
            die(f"[!] {rel} has id {info.subvolid}, expected {expected_id}; cleanup cancelled.")
        default_id = get_default_subvolid(top)
        if default_id == info.subvolid:
            die(f"[!] Refusing to delete {rel}: it is the filesystem default subvolume (id {info.subvolid}).")
        busy_at = live_mount_of_subvolid(fs_uuid, info.subvolid)
        if busy_at is not None:
            die(
                f"[!] Refusing to delete {rel}: subvolume id {info.subvolid} is currently serving {busy_at}. "
                "Reboot first; the boot-time unit will reclaim it."
            )
        run("btrfs", "subvolume", "delete", "--commit-after", "--", str(victim), check=True, timeout=NO_TIMEOUT)
        btrfs_sync(top)
        LOG.info("Deleted %s (id %d) on UUID=%s", rel, info.subvolid, fs_uuid)
    cancel_boot_cleanup(cleanup_unit_name(fs_uuid, rel))
    good(f"[+] Reclaimed {rel}.")


# =============================================================================
# TOP-LEVEL SUBVOLUME CREATION
# =============================================================================
def validate_subvol_name(name: str) -> str:
    candidate = name.strip().lstrip("/")
    if not candidate or candidate in (".", ".."):
        die("[!] Invalid subvolume name.")
    if "/" in candidate:
        die("[!] Top-level mode takes a single name such as @data, not a path.")
    if candidate.startswith("."):
        die("[!] Names starting with '.' are reserved.")
    if not SAFE_NAME_RE.fullmatch(candidate):
        die("[!] Use only [A-Za-z0-9@._+:=-]; other characters break fstab, systemd and the bootloader.")
    if transient_kind(candidate) is not None:
        die("[!] That name collides with Dusky's transient-artefact grammar.")
    return candidate


def create_top_level_subvolume(fs: Filesystem, name: str, nocow: bool) -> None:
    name = validate_subvol_name(name)
    with dusky_lock(), top_level(fs, allow_empty=True) as top:
        target = top / name
        if os.path.lexists(target):
            die(f"[!] {name} already exists at the top level.")
        run("btrfs", "subvolume", "create", "--", str(target), check=True)
        if nocow:
            run("chattr", "+C", str(target), check=True)
            attrs = run("lsattr", "-d", str(target))
            flags = attrs.text.split()[0] if attrs.ok and attrs.text else ""
            if "C" not in flags:
                warn("[!] NOCOW could not be verified; check with lsattr -d.")
            else:
                good("[+] Created with copy-on-write disabled (NOCOW).")
        else:
            good("[+] Created.")
        btrfs_sync(top)
    say(f"{C_DIM}    mount -o subvol=/{name},noatime UUID={fs.uuid} /your/mountpoint{C_RESET}")


# =============================================================================
# DOCTOR
# =============================================================================
def _kernel_tuple() -> tuple[int, ...]:
    release = run("uname", "-r").text
    parts = re.findall(r"\d+", release.split("-")[0])[:3]
    return tuple(int(p) for p in parts)


def cmd_doctor() -> int:
    problems = 0
    say(f"{C_ACCENT}Dusky doctor v{DUSKY_VERSION}{C_RESET}")
    say(f"{C_RULE}{'-' * 70}{C_RESET}")

    for tool in ("btrfs", "snapper", "findmnt", "mount", "umount", "mountpoint",
                 "systemctl", "systemd-escape", "test", "rm", "mkdir", "fzf"):
        path = shutil.which(tool, path=SUBPROCESS_ENV["PATH"])
        if path:
            say(f"  tool {tool:<16} {C_OK}ok{C_RESET} {C_DIM}{path}{C_RESET}")
        else:
            problems += 1
            say(f"  tool {tool:<16} {C_ERR}MISSING{C_RESET}")

    kernel = run("uname", "-r").text
    ktuple = _kernel_tuple()
    say(f"  kernel           {C_DIM}{kernel}{C_RESET}" + ("" if ktuple >= (7, 3) else f"  {C_WARN}(< 7.3){C_RESET}"))
    say(f"  btrfs-progs      {C_DIM}{run('btrfs', '--version').text}{C_RESET}")
    snap_version = run("snapper", "--version")
    say(f"  snapper          {C_DIM}{snap_version.text.splitlines()[0] if snap_version.ok and snap_version.text else 'unknown'}{C_RESET}")
    say(f"  python           {C_DIM}{sys.version.split()[0]}{C_RESET}")
    _load_libc()
    say(f"  renameat2 (libc) {C_OK}exported{C_RESET}")

    metadata_ok = btrfs_records("subvolume", "get-default", "/", check=False) is not None
    say(f"  btrfs metadata   {(C_OK + 'readable') if metadata_ok else (C_ERR + 'unreadable')}{C_RESET}")
    problems += 0 if metadata_ok else 1
    problems += int(ktuple < (7, 3))

    say()
    for fs_uuid, (fs, mount_target) in btrfs_filesystems().items():
        say(f"{C_INFO}filesystem UUID={fs_uuid}{C_RESET} {C_DIM}({fs.source} at {mount_target}){C_RESET}")
        ready, detail = multidevice_ready(fs)
        say(f"  devices           : {(C_OK if ready else C_ERR)}{detail}{C_RESET}")
        problems += 0 if ready else 1
        default_id = get_default_subvolid(mount_target)
        say(f"  default subvolume : {default_id if default_id is not None else 'unknown'}")
        try:
            with top_level(fs, quiet=True) as top:
                supported = probe_exchange(top)
                say(f"  RENAME_EXCHANGE   : {(C_OK + 'supported') if supported else (C_ERR + 'NOT SUPPORTED')}{C_RESET}")
                problems += 0 if supported else 1
                journals = load_journals(top)
                if journals:
                    problems += len(journals)
                    for journal in journals:
                        say(f"  {C_ERR}UNFINISHED TXN    : {journal.txn} state={journal.state} "
                            f"-> run 'dusky --recover'{C_RESET}")
                transients = [i.path for i in subvol_list(top) if transient_kind(i.path)]
                if transients:
                    say(f"  {C_WARN}transient debris  : {', '.join(transients)}{C_RESET}")

        except (DuskyError, OSError) as exc:
            problems += 1
            warn(f"[!] Filesystem audit failed: {exc}")

    say()
    for cfg in snapper_configs():
        mountpoint = cfg["subvolume"]
        snaps_mnt = snapshots_mountpoint(mountpoint)
        live = is_mountpoint(snaps_mnt)
        resolved = None
        with suppress(DuskyError):
            resolved = active_subvol(mountpoint, required=False)
        say(f"{C_INFO}snapper config {cfg['config']!r}{C_RESET} -> {mountpoint}")
        say(f"  active subvolume  : {resolved[0] if resolved else C_ERR + 'unresolved' + C_RESET}")
        say(f"  snapshot store    : {snaps_mnt} {'(mounted subvolume)' if live else C_WARN + '(NOT a mounted subvolume)' + C_RESET}")
        if not live or resolved is None:
            problems += 1
        if resolved:
            prefix = resolved[0].strip("/") + "/"
            nested = [i.path for i in subvol_list(mountpoint) if i.path.startswith(prefix)]
            if nested:
                problems += 1
                say(f"  {C_ERR}nested subvolumes : {', '.join(nested)}{C_RESET}")
                say(f"  {C_ERR}                    rollback of this config is BLOCKED{C_RESET}")
            else:
                say(f"  nested subvolumes : {C_OK}none (flat, rollback-safe){C_RESET}")

    say()
    with suppress(OSError):
        for line in Path("/etc/fstab").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("#") or "btrfs" not in line:
                continue
            if subvolid_from_options(line) is not None:
                problems += 1
                say(f"{C_ERR}fstab pins subvolid= : {line.strip()}{C_RESET}")
                say(f"{C_DIM}    A numeric subvolid is invalidated by every rollback. Use subvol=@name.{C_RESET}")

    pending = list_pending_cleanups()
    say(f"pending boot cleanups : {', '.join(pending) if pending else 'none'}")
    say(f"stale private mounts  : {sweep_stale_mounts()} reaped")

    if any(p.is_dir() and is_mountpoint(p) for p in (Path("/efi"), Path("/boot"))):
        issues = audit_boot_consistency(Path("/"))
        if issues:
            problems += len(issues)
            say(f"kernel/module match   : {C_ERR}{'; '.join(issues)}{C_RESET}")
        else:
            say(f"kernel/module match   : {C_OK}consistent{C_RESET}")

    say()
    if problems:
        say(f"{C_ERR}[!] {problems} issue(s) require attention.{C_RESET}")
        return 2
    good("[+] System is rollback-ready.")
    return 0


# =============================================================================
# fzf TUI
# =============================================================================
US = "\x1f"
ROW_TRANSLATION: Final = str.maketrans({"\n": " ", "\r": " ", US: " "})
VIEWS: Final = ("home", "root", "coordinated", "global", "subvolumes", "maintenance")

TAB_DEFS: Final = (
    ("home", "HOME", "114"),
    ("root", "ROOT", "39"),
    ("coordinated", "ROOT+HOME", "213"),
    ("global", "GLOBAL", "81"),
    ("subvolumes", "SUBVOLUMES", "203"),
    ("maintenance", "MAINTENANCE", "215"),
)


def encode_meta(meta: JSONDict) -> str:
    """
    Base64 the row payload.

    fzf substitutes {2} into a shell command line; passing raw JSON through
    argparse.REMAINDER and re-joining with ' ' (v3.0.0) is lossy for any value
    containing runs of whitespace, and every quoting layer in between is a
    place for an embedded quote to escape.  Base64 has none of those problems.
    """
    return base64.b64encode(json.dumps(meta, ensure_ascii=True).encode()).decode()


def decode_meta(blob: str) -> JSONDict:
    with suppress(ValueError, binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        data = json.loads(base64.b64decode(blob.encode(), validate=True).decode())
        if isinstance(data, dict):
            return data
    return {}


def _fit_panel_text(value: str, limit: int) -> str:
    """Clip coloured preview text by terminal cells without splitting an SGR code."""
    if display_width(value) <= limit:
        return value
    if limit <= 0:
        return ""
    used = 0
    parts: list[str] = []
    for token in re.findall(r"\x1b\[[0-9;]*[A-Za-z]|[^\x1b]", value):
        if token.startswith("\x1b["):
            parts.append(token)
            continue
        cells = display_width(token)
        if used + cells > limit - 1:
            break
        parts.append(token)
        used += cells
    return "".join(parts) + C_RESET + "…"


def panel(title: str, rows: Sequence[str]) -> None:
    width = max(6, display_width(title) + 5,
                *(display_width(row) + 4 for row in rows))
    preview_columns = os.environ.get("FZF_PREVIEW_COLUMNS", "")
    if preview_columns.isdecimal() and int(preview_columns) >= 6:
        width = min(width, int(preview_columns))
    title = _fit_panel_text(title, width - 5)
    dash = "\u2500"
    say(f"{C_WARN}\u256d{dash} {title} {C_WARN}{dash * max(0, width - display_width(title) - 5)}\u256e{C_RESET}")
    for row in rows:
        row = _fit_panel_text(row, width - 4)
        pad = max(0, width - display_width(row) - 4)
        say(f"{C_WARN}\u2502{C_RESET} {row}{' ' * pad} {C_WARN}\u2502{C_RESET}")
    say(f"{C_WARN}\u2570{dash * (width - 2)}\u256f{C_RESET}\n")


def tui_preview(view: str, blob: str, *, show_diff: bool) -> None:
    meta = decode_meta(blob)

    if view == "subvolumes":
        panel(f"{C_ACCENT}SUBVOLUME ACTIONS{C_RESET}", [
            f"{C_OK}[CTRL-N]{C_RESET}  create top-level subvolume",
            f"{C_INFO}[CTRL-S]{C_RESET}  native btrfs snapshot",
            f"{C_ACCENT}[CTRL-G]{C_RESET}  init snapper config",
            f"{C_WARN}[CTRL-B]{C_RESET}  send/receive backup",
            f"{C_ERR}[DEL]{C_RESET}     delete subvolume",
            f"{C_DIM}[TAB]{C_RESET}     next view",
        ])
    elif view == "maintenance":
        panel(f"{C_WARN}MAINTENANCE{C_RESET}", [
            f"{C_ERR}[DEL]{C_RESET}     reclaim selected orphan",
            f"{C_OK}[CTRL-R]{C_RESET}  recover unfinished transaction",
            f"{C_DIM}[TAB]{C_RESET}     next view",
            f"{C_DIM}Transient artefacts from interrupted{C_RESET}",
            f"{C_DIM}restores and backups live here.{C_RESET}",
        ])
    else:
        panel(f"{C_WARN}SHORTCUTS{C_RESET}", [
            f"{C_OK}[ENTER]{C_RESET}   atomic restore",
            f"{C_ERR}[DEL]{C_RESET}     delete snapshot(s)",
            f"{C_INFO}[CTRL-S]{C_RESET}  create snapshot",
            f"{C_INFO}[CTRL-E]{C_RESET}  edit description",
            f"{C_OK}[CTRL-K]{C_RESET}  keep single snapshot(s)",
            f"{C_WARN}[CTRL-B]{C_RESET}  backup to external btrfs",
            f"{C_ACCENT}[TAB]{C_RESET}     next view",
            f"{C_DIM}[CTRL-A/X]{C_RESET} select / deselect all",
            f"{C_DIM}[CTRL-V/P]{C_RESET} diff mode on / off",
            f"{C_DIM}[ALT-P]{C_RESET}    toggle this pane",
        ])

    if not meta or meta.get("empty"):
        say(f"{C_DIM}[i] Nothing selected.{C_RESET}")
        return

    def field_row(label: str, value: object, colour: str = C_TEXT) -> None:
        say(f" {C_DIM}{label:<9}{C_RESET}\u2502 {colour}{value}{C_RESET}")

    if view == "subvolumes":
        say(f"{C_ACCENT}SUBVOLUME{C_RESET}")
        say(f"{C_RULE}{'-' * 52}{C_RESET}")
        field_row("id", meta.get("id", "?"))
        field_row("path", meta.get("path", "?"))
        field_row("flags", "read-only" if meta.get("is_ro") else "read-write")
        field_row("uuid", meta.get("uuid") or "-", C_DIM)
        if meta.get("received_uuid"):
            field_row("recv uuid", meta["received_uuid"], C_DIM)
        field_row("fs uuid", meta.get("fs_uuid", "?"), C_DIM)
        field_row("mounted", meta.get("mounted_at") or "not mounted",
                  C_OK if meta.get("mounted_at") else C_DIM)
        return

    if view == "maintenance":
        say(f"{C_WARN}ORPHANED ARTEFACT{C_RESET}")
        say(f"{C_RULE}{'-' * 52}{C_RESET}")
        field_row("path", meta.get("path", "?"))
        field_row("id", meta.get("id", "?"))
        field_row("fs uuid", meta.get("fs_uuid", "?"), C_DIM)
        field_row("kind", meta.get("kind", "?"), C_WARN)
        return

    config = str(meta.get("config") or ("root" if view in ("root", "coordinated") else "home"))
    say(f"{C_INFO}SNAPSHOT {meta.get('id')}{C_RESET}")
    say(f"{C_RULE}{'-' * 52}{C_RESET}")
    field_row("config", config.upper())
    field_row("type", meta.get("type") or "-", C_ACCENT)
    if meta.get("pre_number"):
        field_row("pre", meta["pre_number"])
    field_row("date", meta.get("date") or "-", C_WARN)
    field_row("age", meta.get("age") or "-", C_OK)
    field_row("user", meta.get("user") or "root")
    cleanup = meta.get("cleanup") or (
        "KEEP / automatic cleanup disabled" if meta.get("type") == "single"
        else "none / empty pre-post pairs may still be cleaned"
    )
    field_row("cleanup", cleanup)
    if meta.get("location"):
        field_row("path", meta["location"], C_DIM)
    if meta.get("userdata"):
        field_row("userdata", meta["userdata"], C_DIM)
    if meta.get("dead"):
        field_row("state", "DEAD / SUBVOLUME MISSING", C_ERR)
    field_row("desc", meta.get("description") or "-")
    say()

    if view == "coordinated":
        try:
            match = find_pair(
                "root", "home",
                target_date=str(meta.get("raw_date") or ""),
                target_userdata=dict(meta.get("userdata_dict") or {}),
                left_id_hint=str(meta.get("id")),
            )
            colour = C_OK if match.exact else C_WARN
            say(f"{colour}PAIR  root={match.left_id}  home={match.right_id}{C_RESET}")
            say(f"{C_DIM}    matched by {match.method} (delta {match.delta:.0f}s){C_RESET}\n")
        except DuskyError as exc:
            say(f"{C_ERR}PAIR UNRESOLVED{C_RESET}\n{C_DIM}{exc}{C_RESET}\n")

    if not show_diff:
        say(f"{C_DIM}[i] File changes hidden for scroll performance.{C_RESET}")
        say(f"{C_DIM}    CTRL-V compute diff  |  CTRL-P hide again{C_RESET}")
        return

    def render_diff(cfg: str, snap: str) -> None:
        say(f"{C_ACCENT}\u25b6 {cfg}: files that would change{C_RESET}")
        proc = run("snapper", "-c", cfg, "status", f"{snap}..0", timeout=120.0)
        if not proc.ok:
            say(f"  {C_ERR}{proc.message}{C_RESET}")
            return
        lines = [l for l in proc.stdout.splitlines() if l.strip()]
        if not lines:
            say(f"  {C_DIM}no differences{C_RESET}")
            return
        for entry in lines[:150]:
            status, _, rest = entry.partition(" ")
            path = rest.strip()
            if status.startswith("+"):
                say(f"  {C_ERR}[-]{C_RESET} {C_DIM}{path}{C_RESET}")
            elif status.startswith("-"):
                say(f"  {C_OK}[+]{C_RESET} {path}")
            else:
                say(f"  {C_WARN}[~]{C_RESET} {path}")
        if len(lines) > 150:
            say(f"  {C_DIM}... {len(lines) - 150} more{C_RESET}")

    if view in ("home", "root", "global"):
        render_diff(config, str(meta.get("id")))
    elif view == "coordinated":
        render_diff("root", str(meta.get("id")))
        with suppress(DuskyError):
            match = find_pair(
                "root", "home",
                target_date=str(meta.get("raw_date") or ""),
                target_userdata=dict(meta.get("userdata_dict") or {}),
                left_id_hint=str(meta.get("id")),
            )
            say()
            render_diff("home", match.right_id)


def _tab_bar(current: str) -> str:
    cells = []
    for view_id, label, colour in TAB_DEFS:
        if view_id == current:
            cells.append(f"\x1b[1;38;5;232;48;5;{colour}m {label} {C_RESET}")
        else:
            cells.append(f"{C_DIM} {label} {C_RESET}")
    return "  " + "  ".join(cells)


def _storage_header() -> str:
    gib = 1024 ** 3
    total, used, free = shutil.disk_usage("/")
    return (
        f" {C_INFO}BTRFS{C_RESET} {C_TEXT}{total / gib:.1f}G total{C_RESET} {C_RULE}|{C_RESET} "
        f"\x1b[38;5;203m{used / gib:.1f}G used{C_RESET} {C_RULE}|{C_RESET} "
        f"{C_OK}{free / gib:.1f}G free{C_RESET}  {C_RULE}|{C_RESET} {C_DIM}dusky {DUSKY_VERSION}{C_RESET} "
    )


def _rows_for_view(view: str) -> list[str]:
    invalidate_cache()
    sep = f"{C_RULE}\u2502{C_RESET}"
    rule = f"{C_RULE}{'\u2500' * 400}{C_RESET}"
    out = [_tab_bar(view), rule]
    empty = encode_meta({"empty": True})

    if view == "subvolumes":
        out.append(f"{C_DIM}{'ID':>7}{C_RESET} {sep} {C_DIM}{'MOUNTED AT':<16}{C_RESET} {sep} "
                   f"{C_DIM}{'RO':<3}{C_RESET} {sep} {C_DIM}PATH{C_RESET}")
        items = sorted(enumerate_subvolumes(), key=lambda s: s.path)
        for item in items:
            visible = (
                f"\x1b[1;38;5;39m{item.subvolid:>7}{C_RESET} {sep} "
                f"{C_WARN}{(item.mounted_at or '-'):<16}{C_RESET} {sep} "
                f"{(C_ERR + 'ro ' + C_RESET) if item.readonly else (C_OK + 'rw ' + C_RESET)} {sep} "
                f"{C_TEXT}{item.path}{C_RESET}"
            )
            out.append(f"{visible.translate(ROW_TRANSLATION)}{US}{encode_meta(item.as_meta())}")
        if not items:
            out.append(f"{C_DIM}{'-':>7}{C_RESET} {sep} no subvolumes{US}{empty}")
        return out

    if view == "maintenance":
        out.append(f"{C_DIM}{'KIND':<10}{C_RESET} {sep} {C_DIM}{'ID':>7}{C_RESET} {sep} "
                   f"{C_DIM}{'FS UUID':<36}{C_RESET} {sep} {C_DIM}PATH{C_RESET}")
        found = 0
        for item in enumerate_subvolumes(include_transient=True, include_snapshots=False):
            kind = transient_kind(item.path)
            if kind is None:
                continue
            found += 1
            meta = item.as_meta() | {"kind": kind}
            visible = (
                f"{C_WARN}{kind:<10}{C_RESET} {sep} \x1b[1;38;5;39m{item.subvolid:>7}{C_RESET} {sep} "
                f"{C_DIM}{item.fs_uuid:<36}{C_RESET} {sep} {C_TEXT}{item.path}{C_RESET}"
            )
            out.append(f"{visible.translate(ROW_TRANSLATION)}{US}{encode_meta(meta)}")
        if not found:
            out.append(f"{C_OK}{'clean':<10}{C_RESET} {sep} no orphaned artefacts{US}{empty}")
        return out

    if view == "global":
        out.append(
            f"{C_DIM}{'CFG':<9}{C_RESET} {sep} {C_DIM}{'ID':>5}{C_RESET} {sep} {C_DIM}{'AGE':<9}{C_RESET} "
            f"{sep} {C_DIM}{'DATE':<15}{C_RESET} {sep} {C_DIM}DESCRIPTION{C_RESET}"
        )
        rows = sorted(all_snapshot_rows(), key=lambda r: (r["config"], -int(r["id"])))
    else:
        out.append(
            f"{C_DIM}{'ID':>5}{C_RESET} {sep} {C_DIM}{'TYPE':<7}{C_RESET} {sep} {C_DIM}{'AGE':<9}{C_RESET} "
            f"{sep} {C_DIM}{'DATE':<15}{C_RESET} {sep} {C_DIM}DESCRIPTION{C_RESET}"
        )
        config = "root" if view in ("root", "coordinated") else "home"
        rows = []
        with suppress(DuskyError):
            rows = sorted(snapshot_rows(config), key=lambda r: -int(r["id"]))

    if not rows:
        out.append(f"{C_ERR} no snapshots{C_RESET}{US}{empty}")
        return out

    for row in rows:
        colour = C_ERR if row["dead"] else C_TEXT
        if view == "global":
            visible = (
                f"{C_ACCENT}{row['config']:<9}{C_RESET} {sep} \x1b[1;38;5;39m{row['id']:>5}{C_RESET} {sep} "
                f"{C_OK}{row['age']:<9}{C_RESET} {sep} {C_WARN}{row['date']:<15}{C_RESET} {sep} "
                f"{colour}{row['description']}{C_RESET}"
            )
        else:
            visible = (
                f"\x1b[1;38;5;39m{row['id']:>5}{C_RESET} {sep} {C_ACCENT}{row['type']:<7}{C_RESET} {sep} "
                f"{C_OK}{row['age']:<9}{C_RESET} {sep} {C_WARN}{row['date']:<15}{C_RESET} {sep} "
                f"{colour}{row['description']}{C_RESET}"
            )
        out.append(f"{visible.translate(ROW_TRANSLATION)}{US}{encode_meta(row)}")
    return out


def launch_tui() -> None:
    require_tools("fzf", "btrfs", "snapper", "findmnt")
    if not interactive():
        die("[!] The TUI requires a controlling terminal.")

    colors = (
        "bg+:#1e1e2e,bg:-1,spinner:#f5e0dc,fg:#cdd6f4,fg+:#cdd6f4,header:#89b4fa,"
        "info:#cba6f7,pointer:#f5e0dc,marker:#a6e3a1,prompt:#cba6f7,hl:#f38ba8,"
        "hl+:#f38ba8,border:#585b70,label:#a6e3a1"
    )
    self_cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(SCRIPT_PATH))}"
    view_index = 0
    empty_cycles = 0

    while True:
        view = VIEWS[view_index]
        lines = _rows_for_view(view)
        preview = f"{self_cmd} --tui-preview {view} {{2}}"
        preview_diff = f"{self_cmd} --tui-preview {view} {{2}} --show-diff"

        argv = [
            "fzf",
            "--multi",
            "--ansi",
            "--reverse",
            "--delimiter=\\x1f",
            "--with-nth=1",
            "--nth=1",
            "--header", _storage_header(),
            "--header-first",
            "--header-lines=3",
            "--border=rounded",
            "--border-label", f" Dusky {DUSKY_VERSION} ",
            "--prompt", " :: action > ",
            f"--color={colors}",
            "--pointer=\u258c",
            "--marker=\u25b6",
            "--no-hscroll",
            "--ellipsis=",
            "--highlight-line",
            "--scrollbar=\u2503",
            "--info=inline-right",
            "--expect=enter,ctrl-d,delete,tab,btab,ctrl-s,ctrl-n,ctrl-g,ctrl-b,ctrl-r,ctrl-e,ctrl-k",
            "--bind=ctrl-a:select-all,ctrl-x:deselect-all,ctrl-space:toggle,"
            "shift-down:toggle+down,shift-up:toggle+up,"
            f"ctrl-p:change-preview({preview})+change-prompt( :: action > ),"
            f"ctrl-v:change-preview({preview_diff})+change-prompt( :: diff > ),"
            "alt-p:toggle-preview",
            "--preview", preview,
            "--preview-window", "right,46%,border-left,wrap",
        ]

        try:
            process = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                text=True, encoding="utf-8", env=SUBPROCESS_ENV,
            )
            try:
                stdout, _ = process.communicate(input="\n".join(lines))
            finally:
                reap_process(process)
        except OSError as exc:
            die(f"[!] Failed to launch fzf: {exc}")

        if process.returncode == 2:
            die("[!] fzf failed; see its diagnostic above.")
        if process.returncode == 130:
            say(f"\n{C_DIM}[*] Bye.{C_RESET}")
            return
        if not stdout.strip():
            empty_cycles += 1
            if empty_cycles > 3:
                die("[!] fzf produced no output three times in a row; aborting to avoid a spin loop.")
            continue
        empty_cycles = 0

        output = stdout.rstrip("\n").split("\n")
        key = output[0]
        if key in ("tab", "btab"):
            view_index = (view_index + (1 if key == "tab" else -1)) % len(VIEWS)
            continue

        selected: list[JSONDict] = []
        for line in output[1:]:
            chunks = line.split(US)
            if len(chunks) < 2:
                continue
            meta = decode_meta(chunks[1])
            if meta and not meta.get("empty"):
                selected.append(meta)

        try:
            if _dispatch(view, key, selected):
                return
        except DuskyAbort as exc:
            say(f"{C_DIM}{exc}{C_RESET}")
            pause()
        except DuskyError as exc:
            say(f"{C_ERR}{exc}{C_RESET}")
            pause()
        finally:
            invalidate_cache()


def _dispatch(view: str, key: str, selected: list[JSONDict]) -> bool:
    """Returns True when the TUI should exit."""
    if key == "ctrl-r":
        if view == "maintenance":
            cmd_recover(abort=False, assume_yes=False)
            pause()
        return False

    if key == "ctrl-s" and view in ("home", "root", "coordinated", "global"):
        config = "root" if view == "coordinated" else view
        if view == "global":
            config = ask(f"{C_WARN}[*] target config: {C_RESET}")
        if not config:
            return False
        description = ask(f"{C_WARN}[*] description: {C_RESET}")
        if not description:
            return False
        if view == "coordinated":
            cmd_create_pair("root", "home", description)
        else:
            cmd_create(config, description)
        pause()
        return False

    if not selected:
        return False
    head = selected[0]

    if key in ("ctrl-e", "ctrl-k") and view in ("home", "root", "global", "coordinated"):
        if key == "ctrl-e" and len(selected) != 1:
            die("[!] Select exactly one snapshot or coordinated pair to edit.")
        targets: list[tuple[str, str]] = []
        for meta in selected:
            targets.append((str(meta["config"]), str(meta["id"])))
            if view == "coordinated":
                match = find_pair(
                    "root", "home", target_date=str(meta.get("raw_date") or ""),
                    target_userdata=dict(meta.get("userdata_dict") or {}), left_id_hint=str(meta["id"]),
                )
                if not match.exact:
                    die("[!] Metadata changes in the coordinated view require an exact pair. "
                        "Use the root/home views to edit these snapshots individually.")
                targets.append(("home", match.right_id))
        if key == "ctrl-e":
            description = ask(f"{C_WARN}[*] new description (empty clears it): {C_RESET}")
            cmd_modify_snapshots(targets, description=description)
        else:
            cmd_modify_snapshots(targets, keep=True)
        pause()
        return False

    if view == "coordinated":
        pairs: list[PairMatch] = []
        for meta in selected:
            match = find_pair(
                "root", "home",
                target_date=str(meta.get("raw_date") or ""),
                target_userdata=dict(meta.get("userdata_dict") or {}),
                left_id_hint=str(meta.get("id")),
            )
            pairs.append(match)
            say(f"{C_DIM}[*] pair root={match.left_id} home={match.right_id} via {match.method}{C_RESET}")

        if key == "enter":
            if len(pairs) != 1:
                die("[!] Select exactly one pair to restore.")
            match = pairs[0]
            if not match.exact and not confirm(
                f"This pair was matched heuristically ({match.method}, {match.delta:.0f}s apart). Continue?"
            ):
                return False
            cmd_restore_pair("root", match.left_id, "home", match.right_id,
                             remount=True, fix_default=True, assume_yes=False)
            pause("Press Enter to exit...")
            return True
        if key in ("ctrl-d", "delete"):
            if confirm(f"Permanently delete {len(pairs)} snapshot pair(s)?"):
                for match in pairs:
                    cmd_delete_pair("root", match.left_id, "home", match.right_id)
            pause()
        return False

    if view in ("home", "root", "global"):
        if key == "enter":
            if len(selected) != 1:
                die("[!] Select exactly one snapshot to restore.")
            if head.get("dead"):
                die("[!] That snapshot is dead: its subvolume is missing.")
            cmd_restore(str(head["config"]), str(head["id"]), remount=True, fix_default=True, assume_yes=False)
            pause("Press Enter to exit...")
            return True
        if key in ("ctrl-d", "delete"):
            if confirm(f"Permanently delete {len(selected)} snapshot(s)?"):
                for meta in selected:
                    cmd_delete(str(meta["config"]), str(meta["id"]))
            pause()
            return False
        if key == "ctrl-b":
            if len(selected) != 1 or head.get("dead"):
                die("[!] Select exactly one live snapshot to back up.")
            config = str(head["config"])
            mountpoint = snapper_config_subvolume(config)
            fs = filesystem_of(mountpoint)
            snaps = active_subvol(snapshots_mountpoint(mountpoint))
            if snaps is None:
                die("[!] Snapshot store is not a subvolume.")
            destination = ask(f"{C_WARN}[*] destination (btrfs mount, e.g. /mnt/backup): {C_RESET}")
            if destination:
                backup_subvolume(fs, f"{snaps[0]}/{head['id']}/snapshot", destination)
            pause()
        return False

    if view == "maintenance":
        if key in ("ctrl-d", "delete"):
            if confirm(f"Reclaim {len(selected)} orphaned artefact(s)?"):
                for meta in selected:
                    cmd_cleanup_subvol(str(meta["fs_uuid"]), str(meta["path"]))
            pause()
        return False

    # subvolumes view
    if key in ("ctrl-n", "ctrl-s", "ctrl-g", "ctrl-b") and len(selected) > 1:
        die("[!] Select exactly one subvolume for that action.")

    fs = Filesystem(uuid=str(head.get("fs_uuid", "")), source="")
    path = str(head.get("path", ""))

    match key:
        case "ctrl-n":
            name = ask(f"{C_WARN}[*] new top-level subvolume name (e.g. @data): {C_RESET}")
            if name:
                create_top_level_subvolume(fs, name, confirm("Disable copy-on-write (chattr +C)?"))
            pause()
        case "ctrl-s":
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            default = f"@snapshots/{path.strip('@/').replace('/', '_')}_{stamp}"
            dest = ask(f"{C_WARN}[*] destination relative to fs root [{default}]: {C_RESET}") or default
            readonly = confirm("Read-only snapshot?")
            with dusky_lock(), top_level(fs) as top:
                src = top / path
                target = top / dest.lstrip("/")
                if ".." in Path(dest).parts:
                    die("[!] Destination must stay inside the filesystem root.")
                if not src.exists():
                    die(f"[!] Source subvolume vanished: {path}")
                if os.path.lexists(target):
                    die(f"[!] Destination already exists: {dest}")
                target.parent.mkdir(parents=True, exist_ok=True)
                argv = ["btrfs", "subvolume", "snapshot"] + (["-r"] if readonly else [])
                run(*argv, str(src), str(target), check=True, timeout=NO_TIMEOUT)
                btrfs_sync(top)
            good("[+] Snapshot created.")
            pause()
        case "ctrl-g":
            say(f"{C_DIM}[i] The subvolume must already be mounted for snapper create-config.{C_RESET}")
            mountpoint = ask(f"{C_WARN}[*] live mount point: {C_RESET}")
            name = ask(f"{C_WARN}[*] snapper config name: {C_RESET}")
            if mountpoint and name:
                with dusky_lock():
                    run("snapper", "-c", name, "create-config", mountpoint, check=True)
                    invalidate_cache()
                good(f"[+] Config {name!r} created.")
            pause()
        case "ctrl-b":
            destination = ask(f"{C_WARN}[*] destination (btrfs mount): {C_RESET}")
            if destination:
                backup_subvolume(fs, path, destination)
            pause()
        case "delete" | "ctrl-d":
            protected = protected_subvolumes()
            victims: list[JSONDict] = []
            for meta in selected:
                candidate = str(meta["path"]).strip("/")
                if candidate in protected:
                    say(f"{C_ERR}[!] GUARDRAIL: {candidate} is mounted, in fstab or a .mount unit, a "
                        f"snapshot root, or the filesystem default subvolume. Refusing.{C_RESET}")
                else:
                    say(f"  target {candidate} (id {meta.get('id')})")
                    victims.append(meta)
            if victims and confirm_phrase(
                f"Permanently delete {len(victims)} subvolume(s) and everything inside them.", "DELETE"
            ):
                grouped: dict[str, list[JSONDict]] = {}
                for meta in victims:
                    grouped.setdefault(str(meta["fs_uuid"]), []).append(meta)
                with dusky_lock():
                    for fs_uuid, metas in grouped.items():
                        with top_level(Filesystem(fs_uuid, "")) as top:
                            assert_no_open_transactions(top)
                            invalidate_cache()
                            protected_now = protected_subvolumes()
                            for meta in metas:
                                rel = str(meta["path"])
                                victim = top / rel
                                if not os.path.lexists(victim):
                                    say(f"{C_WARN}[-] already gone: {rel}{C_RESET}")
                                    continue
                                info = subvol_show(victim)
                                if info is None or str(info.subvolid) != str(meta.get("id")):
                                    say(f"{C_ERR}[!] {rel} changed identity since the listing; skipping.{C_RESET}")
                                    continue
                                if rel in protected_now or live_mount_of_subvolid(fs_uuid, info.subvolid) is not None:
                                    die(f"[!] {rel} is now protected or mounted; refusing deletion.")
                                run("btrfs", "subvolume", "delete", "--", str(victim),
                                    check=True, timeout=NO_TIMEOUT)
                            btrfs_sync(top)
                good("[+] Deleted.")
            pause()
    return False


# =============================================================================
# CLI
# =============================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dusky",
        description=f"Dusky Btrfs + Snapper master controller {DUSKY_VERSION}",
        color=True,
        suggest_on_error=True,
    )
    parser.add_argument("--version", action="version", version=f"dusky {DUSKY_VERSION}")
    parser.add_argument("-c", "--config", help="snapper configuration name")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--yes", action="store_true", help="assume yes for confirmations")
    parser.add_argument("--no-remount", action="store_true", help="never live-remount after a non-root restore")
    parser.add_argument("--remount", action="store_true", help="force the live remount attempt")
    parser.add_argument("--fix-default", action="store_true",
                        help="repoint the filesystem default subvolume inside the activation window")
    parser.add_argument("--pair-threshold", type=int, default=PAIR_STRICT_SECONDS,
                        help=f"seconds allowed between paired snapshots (default {PAIR_STRICT_SECONDS})")
    parser.add_argument("--parent", help="parent subvolume (relative to fs root) for an incremental send")
    parser.add_argument("--abort", action="store_true", help="with --recover: unwind instead of rolling forward")
    parser.add_argument("--expected-id", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--cleanup-subvol", nargs=2, metavar=("UUID", "SUBVOL"), help=argparse.SUPPRESS)
    parser.add_argument("--tui-preview", nargs=2, metavar=("VIEW", "B64"), help=argparse.SUPPRESS)
    parser.add_argument("--show-diff", action="store_true", help=argparse.SUPPRESS)

    group = parser.add_mutually_exclusive_group()
    group.add_argument("-l", "--list", action="store_true", help="list snapshots")
    group.add_argument("-C", "--create", metavar="DESC", help="create a snapshot")
    group.add_argument("-R", "--restore", metavar="ID", help="atomically restore a snapshot")
    group.add_argument("-D", "--delete", metavar="ID", help="delete a snapshot")
    group.add_argument("--create-pair", nargs=3, metavar=("CFG1", "CFG2", "DESC"), help="coordinated create")
    group.add_argument("--restore-pair", nargs=4, metavar=("CFG1", "ID1", "CFG2", "ID2"), help="coordinated restore")
    group.add_argument("--delete-pair", nargs=4, metavar=("CFG1", "ID1", "CFG2", "ID2"), help="coordinated delete")
    group.add_argument("--sync-restore", nargs="+", metavar="ARG", help="pair-match then restore (DATE|ID [DESC])")
    group.add_argument("--sync-delete", nargs="+", metavar="ARG", help="pair-match then delete (DATE|ID [DESC])")
    group.add_argument("--backup", nargs=2, metavar=("ID", "DEST"), help="send a snapshot of --config to DEST")
    group.add_argument("--list-subvols", action="store_true", help="enumerate subvolumes")
    group.add_argument("--sweep", action="store_true", help="report orphaned transient artefacts")
    group.add_argument("--sweep-apply", action="store_true", help="reclaim orphaned transient artefacts")
    group.add_argument("--recover", action="store_true", help="complete or unwind an interrupted transaction")
    group.add_argument("--undo", action="store_true", help="swap back to the pre-restore subvolume")
    group.add_argument("--doctor", action="store_true", help="audit the system for rollback readiness")
    return parser


def _sync_args(values: Sequence[str]) -> tuple[str | None, str | None, str | None]:
    if not 1 <= len(values) <= 2:
        die("[!] --sync-* takes DATE_OR_ID [DESCRIPTION].")
    first = values[0]
    description = values[1] if len(values) == 2 else None
    if first.isdigit():
        return None, description, first
    return first, description, None


def main(argv: Sequence[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.tui_preview is not None:
        view, blob = args.tui_preview
        if view not in VIEWS:
            return 0
        tui_preview(view, blob, show_diff=args.show_diff)
        return 0

    if args.remount and args.no_remount:
        parser.error("--remount and --no-remount cannot be combined")
    if args.abort and not args.recover:
        parser.error("--abort requires --recover")
    if args.pair_threshold < 0:
        parser.error("--pair-threshold must be nonnegative")
    if args.expected_id is not None and not args.cleanup_subvol:
        parser.error("--expected-id requires --cleanup-subvol")
    if args.backup:
        args.backup[1] = str(Path(args.backup[1]).resolve())
    ensure_root()
    with suppress(OSError):
        os.chdir("/")

    remount = args.remount or not args.no_remount

    if args.cleanup_subvol:
        cmd_cleanup_subvol(args.cleanup_subvol[0], args.cleanup_subvol[1], expected_id=args.expected_id)
        return 0
    if args.doctor:
        with dusky_lock():
            return cmd_doctor()
    if args.recover:
        cmd_recover(abort=args.abort, assume_yes=args.yes)
        return 0
    if args.undo:
        cmd_undo(assume_yes=args.yes)
        return 0
    if args.sweep or args.sweep_apply:
        found = sweep_orphans(apply=args.sweep_apply)
        good(f"[+] {found} orphaned artefact(s) {'reclaimed' if args.sweep_apply else 'found'}.")
        return 0
    if args.list_subvols:
        subvols = enumerate_subvolumes(include_snapshots=args.json, include_transient=args.json)
        if args.json:
            say(json.dumps([s.as_meta() for s in subvols], ensure_ascii=False))
        else:
            for item in sorted(subvols, key=lambda s: s.path):
                say(f"{item.subvolid:>8}  {'ro' if item.readonly else 'rw'}  "
                    f"{item.mounted_at or '-':<16}  {item.path}")
        return 0

    needs_config = any(
        v is not None and v is not False
        for v in (args.list or None, args.create, args.restore, args.delete, args.backup)
    )
    if needs_config and not args.config:
        parser.error("-c/--config is required for --list/--create/--restore/--delete/--backup")

    if args.list:
        return cmd_list(args.config, args.json)
    if args.create is not None:
        cmd_create(args.config, args.create)
    elif args.delete is not None:
        cmd_delete(args.config, args.delete)
    elif args.restore is not None:
        cmd_restore(args.config, args.restore, remount=remount,
                    fix_default=args.fix_default, assume_yes=args.yes)
    elif args.create_pair:
        cmd_create_pair(*args.create_pair)
    elif args.delete_pair:
        cmd_delete_pair(*args.delete_pair)
    elif args.restore_pair:
        cmd_restore_pair(*args.restore_pair, remount=remount,
                         fix_default=args.fix_default, assume_yes=args.yes)
    elif args.backup:
        snap_id, destination = args.backup
        mountpoint = snapper_config_subvolume(args.config)
        fs = filesystem_of(mountpoint)
        snaps = active_subvol(snapshots_mountpoint(mountpoint))
        if snaps is None:
            die("[!] The snapshot store is not a mounted subvolume.")
        backup_subvolume(fs, f"{snaps[0]}/{validate_snap_id(snap_id)}/snapshot",
                         destination, parent_rel=args.parent)
    elif args.sync_restore or args.sync_delete:
        values = args.sync_restore or args.sync_delete
        date, description, hint = _sync_args(values)
        match = find_pair(
            "root", "home",
            target_date=date, target_desc=description,
            left_id_hint=hint, threshold=args.pair_threshold,
        )
        say(f"[*] pair: root={match.left_id} home={match.right_id} ({match.method}, {match.delta:.0f}s)")
        if args.sync_restore:
            cmd_restore_pair("root", match.left_id, "home", match.right_id,
                             remount=remount, fix_default=args.fix_default, assume_yes=args.yes)
        else:
            cmd_delete_pair("root", match.left_id, "home", match.right_id)
    else:
        parser.error("no action requested (try --doctor, --list, or run with no arguments for the TUI)")
    return 0


def entrypoint() -> int:
    # Restore the default SIGPIPE disposition for children only; inside this
    # process a closed stdout must raise BrokenPipeError so that '| head' does
    # not silently truncate a --json export.
    def interrupted(signum: int, _frame: Any) -> None:
        raise DuskyAbort(f"[!] Interrupted by {signal.Signals(signum).name}; use --recover after a restore interruption.")

    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    try:
        if len(sys.argv) == 1:
            ensure_root()
            with suppress(OSError):
                os.chdir("/")
            launch_tui()
            return 0
        return main(sys.argv[1:])
    except DuskyAbort as exc:
        with suppress(BrokenPipeError):
            print(f"{C_DIM}{exc}{C_RESET}", file=sys.stderr)
        return 130
    except DuskyBug as exc:
        with suppress(BrokenPipeError):
            print(f"{C_ERR}[BUG] {exc}{C_RESET}", file=sys.stderr)
        LOG.critical("BUG: %s", strip_ansi(str(exc)))
        return 99
    except DuskyError as exc:
        with suppress(BrokenPipeError):
            print(f"{C_ERR}{exc}{C_RESET}", file=sys.stderr)
        LOG.critical(strip_ansi(str(exc)))
        return exc.exit_code
    except KeyboardInterrupt:
        with suppress(BrokenPipeError):
            print(f"\n{C_ERR}[!] Interrupted.{C_RESET}", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # POSIX: exit as if killed by SIGPIPE so the pipeline sees the truth.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)
        return 128 + int(signal.SIGPIPE)
    except OSError as exc:
        print(f"[!] Operating system error: {exc}", file=sys.stderr)
        return 1
    finally:
        with suppress(Exception):
            sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(entrypoint())
