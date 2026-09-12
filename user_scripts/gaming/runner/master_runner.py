#!/usr/bin/env python3.14
# -*- coding: utf-8 -*-
# ==============================================================================
#  MASTER GAME RUNNER ENGINE
# ------------------------------------------------------------------------------
#  Target platform : Arch Linux (rolling, 2026 spec) / Linux >= 7.1
#  Interpreter     : CPython >= 3.14.6 (GIL or free-threaded build)
#  Session         : pure Wayland (Hyprland / wlroots / KWin) -- no X11 session
#  Dependencies    : stdlib only.  `rich` is an optional presentation upgrade.
#
#  Design rules enforced throughout this file:
#    * ZERO legacy: no Python <3.14 shims, no X11-session fallbacks, no
#      SysV/OpenRC paths, no deprecated Vulkan/Wine environment variables.
#    * ZERO subprocess in hot paths: kernel state is read from procfs/sysfs.
#      `mountpoint`, `lspci`, `vulkaninfo`, `fuser` are never required.
#    * ZERO hardcoded game identity: every game fact comes from TOML.
#    * Deterministic teardown: cgroup v2 `cgroup.kill` + pidfd supervision.
#    * Every external process is bounded by a timeout and a kill escalation.
# ==============================================================================
"""Master Game Runner Engine - declarative launcher for Arch Linux / Wayland."""

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import resource
import selectors
import shlex
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, field, replace
from enum import IntEnum, StrEnum
from functools import cache
from pathlib import Path
from typing import Any, Final, Self

# ------------------------------------------------------------------------------
# Hard interpreter gate. This engine uses PEP 649 lazy annotations, PEP 750-era
# stdlib behaviour, `Popen(process_group=)`, `pathlib` 3.14 semantics and
# `tomllib`. There is deliberately no compatibility path.
# ------------------------------------------------------------------------------
if sys.version_info < (3, 14):  # pragma: no cover - environment guard
    raise SystemExit(
        f"master_runner requires CPython >= 3.14 (running {sys.version.split()[0]}). "
        "Install `python` from [core] on Arch Linux."
    )

ENGINE_NAME: Final = "Master Game Runner Engine"
ENGINE_SLUG: Final = "master-runner"
ENGINE_VERSION: Final = "3.0.0"
ENGINE_UA: Final = f"{ENGINE_SLUG}/{ENGINE_VERSION}"

SELF_PATH: Final = Path(__file__).resolve()
SELF_DIR: Final = SELF_PATH.parent


# ==============================================================================
# SECTION 0 -- XDG base directories (canonical resolution, never guessed twice)
# ==============================================================================
def _xdg(var: str, default: Path) -> Path:
    raw = os.environ.get(var, "").strip()
    if raw:
        p = Path(raw).expanduser()
        if p.is_absolute():
            return p
    return default


HOME: Final = Path(os.environ.get("HOME") or os.path.expanduser("~")).resolve()
XDG_CONFIG_HOME: Final = _xdg("XDG_CONFIG_HOME", HOME / ".config")
XDG_DATA_HOME: Final = _xdg("XDG_DATA_HOME", HOME / ".local/share")
XDG_STATE_HOME: Final = _xdg("XDG_STATE_HOME", HOME / ".local/state")
XDG_CACHE_HOME: Final = _xdg("XDG_CACHE_HOME", HOME / ".cache")
XDG_RUNTIME_DIR: Final = Path(
    os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
)

# Configuration search order: $XDG_CONFIG_HOME/master-runner, then alongside the
# script (portable / git-checkout mode). The first hit that actually contains a
# config.toml wins; otherwise the XDG location is used and auto-created.
_CONFIG_CANDIDATES: Final = (SELF_DIR, XDG_CONFIG_HOME / ENGINE_SLUG)


def _resolve_root() -> Path:
    override = os.environ.get("MASTER_RUNNER_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    for cand in _CONFIG_CANDIDATES:
        if (cand / "config.toml").is_file() or (cand / "profiles").is_dir():
            return cand.resolve()
    return _CONFIG_CANDIDATES[0]


ROOT_DIR: Final = _resolve_root()
GLOBAL_CONFIG_PATH: Final = ROOT_DIR / "config.toml"
PRESETS_DIR: Final = ROOT_DIR / "presets"
PROFILES_DIR: Final = ROOT_DIR / "profiles"
STATE_DIR: Final = XDG_STATE_HOME / ENGINE_SLUG
CACHE_DIR: Final = XDG_CACHE_HOME / ENGINE_SLUG
RUNTIME_DIR: Final = XDG_RUNTIME_DIR / ENGINE_SLUG

SYSFS_DRM: Final = Path("/sys/class/drm")
SYSFS_CGROUP: Final = Path("/sys/fs/cgroup")
PROC: Final = Path("/proc")


# ==============================================================================
# SECTION 1 -- Console / logging
#
# `rich` is imported lazily: importing it costs ~35 ms and ~9 MB RSS which is
# pure waste for `run`, `mount` and desktop-file launches. The plain renderer is
# a first-class citizen, not a degraded fallback.
# ==============================================================================
class Verbosity(IntEnum):
    QUIET = 0
    NORMAL = 1
    VERBOSE = 2
    TRACE = 3


class Ansi(StrEnum):
    RESET = "\x1b[0m"
    DIM = "\x1b[2m"
    BOLD = "\x1b[1m"
    RED = "\x1b[31m"
    GREEN = "\x1b[32m"
    YELLOW = "\x1b[33m"
    BLUE = "\x1b[34m"
    MAGENTA = "\x1b[35m"
    CYAN = "\x1b[36m"


class Log:
    """Minimal, allocation-light logger with an optional rich backend."""

    level: Verbosity = Verbosity.NORMAL
    _rich: Any = None
    _rich_probed: bool = False
    _color: bool = sys.stderr.isatty() and os.environ.get("NO_COLOR") is None

    @classmethod
    def console(cls) -> Any:
        """Return a rich Console or None. Import is performed at most once."""
        if not cls._rich_probed:
            cls._rich_probed = True
            if os.environ.get("MASTER_RUNNER_PLAIN") == "1":
                cls._rich = None
            else:
                try:
                    from rich.console import Console

                    cls._rich = Console(highlight=False, soft_wrap=False)
                except ImportError:
                    cls._rich = None
        return cls._rich

    @classmethod
    def _emit(cls, sigil: str, colour: Ansi, msg: str) -> None:
        # Diagnostics ALWAYS go to stderr so that `--json`, `env` and any other
        # machine-readable stdout payload stays byte-clean and pipeable.
        stream = sys.stderr
        if cls._color:
            stream.write(f"{colour}{sigil}{Ansi.RESET} {msg}\n")
        else:
            stream.write(f"{sigil} {msg}\n")
        stream.flush()

    @classmethod
    def info(cls, msg: str) -> None:
        if cls.level >= Verbosity.NORMAL:
            cls._emit("::", Ansi.CYAN, msg)

    @classmethod
    def ok(cls, msg: str) -> None:
        if cls.level >= Verbosity.NORMAL:
            cls._emit(" +", Ansi.GREEN, msg)

    @classmethod
    def warn(cls, msg: str) -> None:
        if cls.level >= Verbosity.NORMAL:
            cls._emit(" !", Ansi.YELLOW, msg)

    @classmethod
    def error(cls, msg: str) -> None:
        cls._emit(" x", Ansi.RED, msg)

    @classmethod
    def debug(cls, msg: str) -> None:
        if cls.level >= Verbosity.VERBOSE:
            cls._emit(" ~", Ansi.MAGENTA, msg)

    @classmethod
    def trace(cls, msg: str) -> None:
        if cls.level >= Verbosity.TRACE:
            cls._emit(" .", Ansi.DIM, msg)


# ==============================================================================
# SECTION 2 -- Bounded process execution helpers
#
# Every helper below is non-throwing and time-bounded. A hung `hyprctl` or a
# wedged FUSE helper must never stall the launcher.
# ==============================================================================
@dataclass(frozen=True, slots=True)
class Ran:
    rc: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.rc == 0

    @property
    def message(self) -> str:
        return (self.err.strip() or self.out.strip() or f"exit {self.rc}")[:512]


def run_cmd(
    argv: Sequence[str],
    *,
    timeout: float = 15.0,
    env: Mapping[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
    stdin_data: str | None = None,
    check_binary: bool = True,
) -> Ran:
    """Run `argv`, never raise, always bounded. Returns rc=127 if not found."""
    if check_binary and not (
        os.path.isabs(argv[0]) or shutil.which(argv[0]) is not None
    ):
        return Ran(127, "", f"{argv[0]}: command not found")
    Log.trace("exec " + shlex.join(argv))
    try:
        cp = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env=dict(env) if env is not None else None,
            cwd=os.fspath(cwd) if cwd is not None else None,
            input=stdin_data,
            check=False,
        )
        return Ran(cp.returncode, cp.stdout or "", cp.stderr or "")
    except subprocess.TimeoutExpired:
        return Ran(124, "", f"timeout after {timeout}s: {argv[0]}")
    except (OSError, ValueError) as exc:
        return Ran(127, "", f"{argv[0]}: {exc}")


@cache
def have(binary: str) -> bool:
    return shutil.which(binary) is not None


def read_text(path: str | os.PathLike[str], limit: int = 1 << 20) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def read_first_line(path: str | os.PathLike[str]) -> str:
    return read_text(path, 4096).split("\n", 1)[0].strip()


def read_int(path: str | os.PathLike[str], default: int = 0) -> int:
    txt = read_first_line(path)
    try:
        return int(txt, 0) if txt else default
    except ValueError:
        return default


def notify(title: str, body: str, *, urgency: str = "normal", icon: str = "input-gaming",
           tag: str = ENGINE_SLUG) -> None:
    """Fire-and-forget desktop notification; replaces prior ones via a tag."""
    if not have("notify-send"):
        return
    with suppress(Exception):
        subprocess.Popen(
            [
                "notify-send", "--app-name", ENGINE_NAME, "--urgency", urgency,
                "--icon", icon, "--hint", f"string:x-canonical-private-synchronous:{tag}",
                title, body,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


# ==============================================================================
# SECTION 3 -- Kernel surface: procfs / sysfs / cgroup v2
# ==============================================================================
@dataclass(frozen=True, slots=True)
class MountEntry:
    mount_id: int
    parent_id: int
    dev: str
    root: str
    target: Path
    opts: str
    fstype: str
    source: str
    super_opts: str


class MountTable:
    """Snapshot of /proc/self/mountinfo.

    Replaces every `mountpoint(1)` fork. One 8-64 KiB read answers mount status
    for an arbitrary number of profiles, which is the single largest latency win
    in the interactive dashboard.
    """

    __slots__ = ("_by_target", "_stamp")

    def __init__(self) -> None:
        self._by_target: dict[str, MountEntry] = {}
        self._stamp: float = 0.0
        self.refresh()

    def refresh(self) -> Self:
        table: dict[str, MountEntry] = {}
        try:
            with open("/proc/self/mountinfo", "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    parts = line.rstrip("\n").split(" ")
                    try:
                        sep = parts.index("-")
                    except ValueError:
                        continue
                    if sep + 3 > len(parts) or sep < 6:
                        continue
                    target = _unoctal(parts[4])
                    entry = MountEntry(
                        mount_id=int(parts[0]),
                        parent_id=int(parts[1]),
                        dev=parts[2],
                        root=_unoctal(parts[3]),
                        target=Path(target),
                        opts=parts[5],
                        fstype=parts[sep + 1],
                        source=_unoctal(parts[sep + 2]),
                        super_opts=parts[sep + 3] if sep + 3 < len(parts) else "",
                    )
                    # Later entries shadow earlier ones (over-mounts).
                    table[target] = entry
        except OSError as exc:
            Log.debug(f"mountinfo unreadable: {exc}")
        self._by_target = table
        self._stamp = time.monotonic()
        return self

    def get(self, path: Path) -> MountEntry | None:
        return self._by_target.get(os.path.normpath(str(path)))

    def is_mount(self, path: Path) -> bool:
        return self.get(path) is not None

    def fstype(self, path: Path) -> str:
        e = self.get(path)
        return e.fstype if e else ""

    def children_of(self, path: Path) -> list[MountEntry]:
        prefix = os.path.normpath(str(path)) + "/"
        return [e for t, e in self._by_target.items() if t.startswith(prefix)]

    @property
    def age(self) -> float:
        return time.monotonic() - self._stamp


def _unoctal(field_: str) -> str:
    """mountinfo escapes space/tab/newline/backslash as \\OOO octal."""
    if "\\" not in field_:
        return field_
    out: list[str] = []
    i = 0
    n = len(field_)
    while i < n:
        c = field_[i]
        if c == "\\" and i + 3 < n and field_[i + 1 : i + 4].isdigit():
            out.append(chr(int(field_[i + 1 : i + 4], 8)))
            i += 4
        else:
            out.append(c)
            i += 1
    return "".join(out)


def mount_table(force: bool = False) -> MountTable:
    """Process-wide mount snapshot with a 250 ms coherence window."""
    global _MOUNT_TABLE
    if _MOUNT_TABLE is None:
        _MOUNT_TABLE = MountTable()
    elif force or _MOUNT_TABLE.age > 0.25:
        _MOUNT_TABLE.refresh()
    return _MOUNT_TABLE


_MOUNT_TABLE: MountTable | None = None


def fuse_alive(path: Path) -> bool:
    """True when a FUSE mount point answers statfs.

    A crashed dwarfs/fuse-overlayfs daemon leaves the mount in the namespace but
    every syscall returns ENOTCONN ("Transport endpoint is not connected"). That
    is the canonical stale-mount signature and it must be recovered, not ignored.
    """
    try:
        os.statvfs(path)
        return True
    except OSError as exc:
        if exc.errno in (errno.ENOTCONN, errno.ESTALE, errno.EIO, errno.EACCES):
            return False
        return True


@dataclass(frozen=True, slots=True)
class MemInfo:
    total_kib: int
    available_kib: int

    @classmethod
    def read(cls) -> Self:
        total = avail = 0
        for line in read_text("/proc/meminfo", 8192).splitlines():
            if line.startswith("MemTotal:"):
                total = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                avail = int(line.split()[1])
                break
        return cls(total or 16 << 20, avail or (total // 2 if total else 8 << 20))


def cpu_count() -> int:
    # os.process_cpu_count() honours sched_setaffinity / cpuset cgroups.
    return os.process_cpu_count() or os.cpu_count() or 4


def kernel_release() -> tuple[int, ...]:
    rel = os.uname().release.split("-", 1)[0]
    out: list[int] = []
    for chunk in rel.split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out) or (0,)


def sysctl_read(name: str) -> str:
    return read_first_line(Path("/proc/sys") / name.replace(".", "/"))


def cgroup_of(pid: int) -> Path | None:
    """Resolve the unified (v2) cgroup directory of `pid`, or None."""
    for line in read_text(f"/proc/{pid}/cgroup", 65536).splitlines():
        hier, _, rest = line.partition(":")
        if hier != "0":
            continue
        _, _, path = rest.partition(":")
        path = path.strip()
        if path.startswith("/"):
            cg = SYSFS_CGROUP / path.lstrip("/")
            return cg if cg.is_dir() else None
    return None


def cgroup_kill(cg: Path) -> bool:
    """Atomically SIGKILL an entire cgroup subtree (kernel >= 5.14: cgroup.kill).

    This is race-free: unlike killpg or /proc scraping it cannot miss a process
    that forks while we are iterating, because the kernel freezes the subtree
    for the duration of the kill.
    """
    killer = cg / "cgroup.kill"
    try:
        with open(killer, "w", encoding="ascii") as fh:
            fh.write("1")
        return True
    except OSError as exc:
        Log.debug(f"cgroup.kill({cg}) failed: {exc}")
        return False


def cgroup_pids(cg: Path) -> list[int]:
    pids: list[int] = []
    for procs in [cg / "cgroup.procs", *cg.rglob("cgroup.procs")]:
        for tok in read_text(procs, 1 << 20).split():
            with suppress(ValueError):
                pids.append(int(tok))
    return sorted(set(pids))


def raise_nofile() -> tuple[int, int]:
    """Raise RLIMIT_NOFILE to the hard cap.

    Required by WINEESYNC (one eventfd per NT sync object). Harmless and useful
    under ntsync/fsync as well because DXVK, PipeWire and gamescope all keep a
    large descriptor working set.
    """
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = hard if hard != resource.RLIM_INFINITY else 1 << 20
        if soft < target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
            return target, hard
        return soft, hard
    except (OSError, ValueError) as exc:
        Log.debug(f"RLIMIT_NOFILE unchanged: {exc}")
        return resource.getrlimit(resource.RLIMIT_NOFILE)


# ==============================================================================
# SECTION 4 -- Native D-Bus client (no python-dbus, no GLib)
#
# ROOT CAUSE FIXED HERE:
#   Calling `busctl --user call org.freedesktop.ScreenSaver ... Inhibit` and
#   keeping only the returned cookie is a NO-OP. Every screensaver service
#   (GNOME, KDE, hypridle, swayidle-dbus, xfce4-power-manager) watches
#   NameOwnerChanged for the caller's unique bus name and drops all inhibitors
#   the instant that connection closes -- which is immediately, because busctl
#   exits. The inhibitor therefore never survives one microsecond.
#
#   The only correct implementations are (a) hold the D-Bus connection open for
#   the whole session, or (b) hold the logind inhibitor file descriptor. This
#   client does both, in ~200 lines of stdlib, with zero external processes.
# ==============================================================================
_ALIGN: Final[dict[str, int]] = {
    "y": 1, "b": 4, "n": 2, "q": 2, "i": 4, "u": 4, "x": 8, "t": 8,
    "d": 8, "s": 4, "o": 4, "g": 1, "v": 1, "h": 4, "a": 4, "(": 8, "{": 8,
}


class DBusError(RuntimeError):
    pass


def _sig_end(sig: str, i: int) -> int:
    c = sig[i]
    if c == "a":
        return _sig_end(sig, i + 1)
    if c in "({":
        close = ")" if c == "(" else "}"
        depth, j = 1, i + 1
        while depth and j < len(sig):
            if sig[j] == c:
                depth += 1
            elif sig[j] == close:
                depth -= 1
            j += 1
        return j
    return i + 1


def _sig_split(sig: str) -> list[str]:
    out, i = [], 0
    while i < len(sig):
        j = _sig_end(sig, i)
        out.append(sig[i:j])
        i = j
    return out


class _Writer:
    __slots__ = ("buf",)

    def __init__(self) -> None:
        self.buf = bytearray()

    def align(self, n: int) -> None:
        pad = (-len(self.buf)) % n
        if pad:
            self.buf += b"\0" * pad

    def write(self, sig: str, val: Any) -> None:
        t = sig[0]
        match t:
            case "y":
                self.buf += struct.pack("<B", int(val) & 0xFF)
            case "b":
                self.align(4)
                self.buf += struct.pack("<I", 1 if val else 0)
            case "n":
                self.align(2)
                self.buf += struct.pack("<h", int(val))
            case "q":
                self.align(2)
                self.buf += struct.pack("<H", int(val))
            case "i":
                self.align(4)
                self.buf += struct.pack("<i", int(val))
            case "u" | "h":
                self.align(4)
                self.buf += struct.pack("<I", int(val))
            case "x":
                self.align(8)
                self.buf += struct.pack("<q", int(val))
            case "t":
                self.align(8)
                self.buf += struct.pack("<Q", int(val))
            case "d":
                self.align(8)
                self.buf += struct.pack("<d", float(val))
            case "s" | "o":
                enc = str(val).encode("utf-8")
                self.align(4)
                self.buf += struct.pack("<I", len(enc)) + enc + b"\0"
            case "g":
                enc = str(val).encode("ascii")
                self.buf += struct.pack("<B", len(enc)) + enc + b"\0"
            case "v":
                vsig, vval = val
                self.write("g", vsig)
                self.write(vsig, vval)
            case "a":
                elem = sig[1:]
                self.align(4)
                len_at = len(self.buf)
                self.buf += b"\0\0\0\0"
                self.align(_ALIGN.get(elem[0], 1))
                start = len(self.buf)
                if elem.startswith("{"):
                    for k, v in dict(val).items():
                        self.align(8)
                        inner = _sig_split(elem[1:-1])
                        self.write(inner[0], k)
                        self.write(inner[1], v)
                else:
                    for item in val:
                        self.write(elem, item)
                size = len(self.buf) - start
                self.buf[len_at : len_at + 4] = struct.pack("<I", size)
            case "(":
                self.align(8)
                for s, v in zip(_sig_split(sig[1:-1]), val, strict=True):
                    self.write(s, v)
            case _:
                raise DBusError(f"unsupported signature element {sig!r}")


class _Reader:
    __slots__ = ("data", "pos", "fds")

    def __init__(self, data: bytes, fds: Sequence[int] = ()) -> None:
        self.data = data
        self.pos = 0
        self.fds = list(fds)

    def align(self, n: int) -> None:
        self.pos += (-self.pos) % n

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise DBusError("short read")
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def read(self, sig: str) -> Any:
        t = sig[0]
        match t:
            case "y":
                return struct.unpack("<B", self.take(1))[0]
            case "b":
                self.align(4)
                return bool(struct.unpack("<I", self.take(4))[0])
            case "n":
                self.align(2)
                return struct.unpack("<h", self.take(2))[0]
            case "q":
                self.align(2)
                return struct.unpack("<H", self.take(2))[0]
            case "i":
                self.align(4)
                return struct.unpack("<i", self.take(4))[0]
            case "u":
                self.align(4)
                return struct.unpack("<I", self.take(4))[0]
            case "h":
                self.align(4)
                idx = struct.unpack("<I", self.take(4))[0]
                return self.fds[idx] if idx < len(self.fds) else -1
            case "x":
                self.align(8)
                return struct.unpack("<q", self.take(8))[0]
            case "t":
                self.align(8)
                return struct.unpack("<Q", self.take(8))[0]
            case "d":
                self.align(8)
                return struct.unpack("<d", self.take(8))[0]
            case "s" | "o":
                self.align(4)
                n = struct.unpack("<I", self.take(4))[0]
                s = self.take(n).decode("utf-8", "replace")
                self.take(1)
                return s
            case "g":
                n = struct.unpack("<B", self.take(1))[0]
                s = self.take(n).decode("ascii", "replace")
                self.take(1)
                return s
            case "v":
                vsig = self.read("g")
                return self.read(vsig) if vsig else None
            case "a":
                elem = sig[1:]
                self.align(4)
                nbytes = struct.unpack("<I", self.take(4))[0]
                self.align(_ALIGN.get(elem[0], 1))
                end = self.pos + nbytes
                items: list[Any] = []
                while self.pos < end:
                    if elem.startswith("{"):
                        self.align(8)
                        inner = _sig_split(elem[1:-1])
                        items.append((self.read(inner[0]), self.read(inner[1])))
                    else:
                        items.append(self.read(elem))
                self.pos = end
                return dict(items) if elem.startswith("{") else items
            case "(":
                self.align(8)
                return tuple(self.read(s) for s in _sig_split(sig[1:-1]))
            case _:
                raise DBusError(f"unsupported signature element {sig!r}")


class DBusConnection:
    """A long-lived, blocking D-Bus client connection.

    Holding this object open is what makes an Inhibit call actually inhibit.
    """

    HDR_PATH, HDR_IFACE, HDR_MEMBER = 1, 2, 3
    HDR_ERRNAME, HDR_REPLY, HDR_DEST = 4, 5, 6
    HDR_SENDER, HDR_SIG, HDR_FDS = 7, 8, 9

    __slots__ = ("_sock", "_serial", "unique_name")

    def __init__(self, address: str) -> None:
        self._serial = 0
        self.unique_name = ""
        self._sock = self._connect(address)
        self._auth()
        self.unique_name = self.call(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "Hello", "", (), reply_sig="s",
        )[0]

    # -- construction -----------------------------------------------------
    @staticmethod
    def _connect(address: str) -> socket.socket:
        last: Exception | None = None
        for unit in address.split(";"):
            unit = unit.strip()
            if not unit.startswith("unix:"):
                continue
            kv = dict(
                part.split("=", 1)
                for part in unit[len("unix:") :].split(",")
                if "=" in part
            )
            if "path" in kv:
                target = kv["path"]
            elif "abstract" in kv:
                target = "\0" + kv["abstract"]
            else:
                continue
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(3.0)
            try:
                s.connect(target)
                return s
            except OSError as exc:
                last = exc
                s.close()
        raise DBusError(f"cannot reach bus {address!r}: {last}")

    def _auth(self) -> None:
        s = self._sock
        s.sendall(b"\0")
        uid_hex = str(os.getuid()).encode("ascii").hex().encode("ascii")
        s.sendall(b"AUTH EXTERNAL " + uid_hex + b"\r\n")
        if not self._readline().startswith("OK"):
            raise DBusError("EXTERNAL auth rejected")
        s.sendall(b"NEGOTIATE_UNIX_FD\r\n")
        if not self._readline().startswith("AGREE_UNIX_FD"):
            Log.trace("bus refused unix fd passing")
        s.sendall(b"BEGIN\r\n")

    def _readline(self) -> str:
        buf = bytearray()
        while not buf.endswith(b"\r\n"):
            ch = self._sock.recv(1)
            if not ch:
                raise DBusError("bus closed during auth")
            buf += ch
        return buf[:-2].decode("ascii", "replace")

    # -- io ---------------------------------------------------------------
    def _recv_exact(self, n: int, want_fds: bool) -> tuple[bytes, list[int]]:
        chunks = bytearray()
        fds: list[int] = []
        while len(chunks) < n:
            if want_fds:
                data, anc, _flags, _addr = self._sock.recvmsg(
                    n - len(chunks), socket.CMSG_SPACE(16 * 4)
                )
                for level, ctype, cdata in anc:
                    if level == socket.SOL_SOCKET and ctype == socket.SCM_RIGHTS:
                        usable = len(cdata) - (len(cdata) % 4)
                        fds.extend(struct.unpack(f"<{usable // 4}i", cdata[:usable]))
            else:
                data = self._sock.recv(n - len(chunks))
            if not data:
                raise DBusError("bus connection closed")
            chunks += data
        return bytes(chunks), fds

    def call(
        self,
        dest: str,
        path: str,
        iface: str,
        member: str,
        sig: str,
        args: Sequence[Any],
        *,
        reply_sig: str = "",
        timeout: float = 4.0,
    ) -> tuple[Any, ...]:
        self._serial += 1
        serial = self._serial

        body = _Writer()
        for s, v in zip(_sig_split(sig), args, strict=True):
            body.write(s, v)

        fields: list[tuple[int, tuple[str, Any]]] = [
            (self.HDR_PATH, ("o", path)),
            (self.HDR_DEST, ("s", dest)),
            (self.HDR_IFACE, ("s", iface)),
            (self.HDR_MEMBER, ("s", member)),
        ]
        if sig:
            fields.append((self.HDR_SIG, ("g", sig)))

        hdr = _Writer()
        hdr.buf += struct.pack("<BBBB", ord("l"), 1, 0, 1)
        hdr.buf += struct.pack("<II", len(body.buf), serial)
        hdr.write("a(yv)", fields)
        hdr.align(8)

        self._sock.settimeout(timeout)
        self._sock.sendall(bytes(hdr.buf) + bytes(body.buf))

        deadline = time.monotonic() + timeout
        while True:
            if time.monotonic() > deadline:
                raise DBusError(f"timeout waiting for reply to {member}")
            head, fds = self._recv_exact(16, True)
            if head[0:1] != b"l":
                raise DBusError("big-endian bus messages are not supported")
            mtype = head[1]
            # Fixed header layout (little-endian):
            #   [0] endian [1] type [2] flags [3] protocol
            #   [4:8] body length  [8:12] serial  [12:16] header-array length
            body_len = struct.unpack("<I", head[4:8])[0]
            arr_len = struct.unpack("<I", head[12:16])[0]
            rest_len = arr_len + ((-arr_len) % 8) + body_len
            rest, more_fds = self._recv_exact(rest_len, True)
            fds.extend(more_fds)

            rdr = _Reader(head + rest, fds)
            rdr.pos = 12
            hdr_fields = rdr.read("a(yv)")
            rdr.align(8)

            reply_serial = None
            body_sig = ""
            err_name = ""
            for code, val in hdr_fields:
                if code == self.HDR_REPLY:
                    reply_serial = val
                elif code == self.HDR_SIG:
                    body_sig = val or ""
                elif code == self.HDR_ERRNAME:
                    err_name = val or ""

            if reply_serial != serial:
                for fd in fds:
                    with suppress(OSError):
                        os.close(fd)
                continue  # signal or unrelated traffic; keep draining

            if mtype == 3:  # METHOD_ERROR
                detail = ""
                with suppress(DBusError):
                    if body_sig.startswith("s"):
                        detail = rdr.read("s")
                raise DBusError(f"{err_name}: {detail}")

            want = reply_sig or body_sig
            if not want:
                return ()
            return tuple(rdr.read(s) for s in _sig_split(want))

    def close(self) -> None:
        with suppress(OSError):
            self._sock.close()


def session_bus_address() -> str:
    addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "").strip()
    return addr or f"unix:path={XDG_RUNTIME_DIR / 'bus'}"


def system_bus_address() -> str:
    addr = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS", "").strip()
    return addr or "unix:path=/run/dbus/system_bus_socket"


class IdleInhibitor:
    """Composite idle/sleep inhibitor with correct connection lifetime.

    Layer 1 -- logind (system bus): returns a UNIX fd; the lock is released by
               the kernel when the fd closes, so it is crash-proof.
    Layer 2 -- org.freedesktop.ScreenSaver (session bus): the cookie is only
               valid while the connection lives, so the connection is retained.

    Both are best-effort and independent; failure of either is non-fatal.
    """

    __slots__ = ("_stack", "_active")

    def __init__(self) -> None:
        self._stack = ExitStack()
        self._active: list[str] = []

    @property
    def active(self) -> list[str]:
        return list(self._active)

    def acquire(self, who: str, why: str, *, block_sleep: bool = True) -> None:
        self._logind(who, why, block_sleep)
        self._screensaver(who, why)
        if self._active:
            Log.debug("idle inhibition: " + ", ".join(self._active))
        else:
            Log.debug("idle inhibition unavailable (no logind / screensaver service)")

    def _logind(self, who: str, why: str, block_sleep: bool) -> None:
        what = "idle:sleep:handle-lid-switch" if block_sleep else "idle"
        try:
            conn = DBusConnection(system_bus_address())
        except (DBusError, OSError) as exc:
            Log.trace(f"logind unreachable: {exc}")
            return
        self._stack.callback(conn.close)
        try:
            (fd,) = conn.call(
                "org.freedesktop.login1", "/org/freedesktop/login1",
                "org.freedesktop.login1.Manager", "Inhibit", "ssss",
                (what, who, why, "block"), reply_sig="h",
            )
        except DBusError as exc:
            Log.trace(f"logind Inhibit refused: {exc}")
            return
        if isinstance(fd, int) and fd >= 0:
            os.set_inheritable(fd, False)
            self._stack.callback(lambda: os.close(fd))
            self._active.append(f"logind[{what}]")

    def _screensaver(self, who: str, why: str) -> None:
        try:
            conn = DBusConnection(session_bus_address())
        except (DBusError, OSError) as exc:
            Log.trace(f"session bus unreachable: {exc}")
            return
        for dest, path in (
            ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver"),
            ("org.freedesktop.ScreenSaver", "/ScreenSaver"),
        ):
            try:
                (cookie,) = conn.call(
                    dest, path, "org.freedesktop.ScreenSaver", "Inhibit",
                    "ss", (who, why), reply_sig="u",
                )
            except DBusError:
                continue
            # The connection MUST outlive the cookie -- this is the whole point.
            self._stack.callback(conn.close)
            self._stack.callback(
                lambda c=cookie, d=dest, p=path: _uninhibit(conn, d, p, c)
            )
            self._active.append(f"screensaver[cookie={cookie}]")
            return
        conn.close()

    def release(self) -> None:
        self._stack.close()
        self._active.clear()


def _uninhibit(conn: DBusConnection, dest: str, path: str, cookie: int) -> None:
    with suppress(Exception):
        conn.call(dest, path, "org.freedesktop.ScreenSaver", "UnInhibit", "u", (cookie,))


# ==============================================================================
# SECTION 5 -- GPU + Vulkan ICD topology (pure sysfs, zero subprocess)
# ==============================================================================
PCI_CLASS_DISPLAY: Final = 0x03
VENDOR_NAMES: Final[dict[int, str]] = {
    0x8086: "Intel", 0x1002: "AMD", 0x1022: "AMD", 0x10DE: "NVIDIA",
    0x1AF4: "Red Hat VirtIO", 0x1414: "Microsoft", 0x15AD: "VMware",
    0x1A03: "ASPEED", 0x102B: "Matrox", 0x1D17: "Zhaoxin",
}

# Mesa/proprietary Vulkan ICD shared-object -> canonical vendor tag. Matching on
# `library_path` (not on the manifest file name) is the only reliable method:
# distro packaging renames manifests, but the .so name is an upstream contract.
ICD_LIB_VENDOR: Final[tuple[tuple[str, str], ...]] = (
    ("libvulkan_radeon", "amd"),          # RADV
    ("libvulkan_amdgpu", "amd"),
    ("amdvlk", "amd"),
    ("libvulkan_intel_hasvk", "intel"),   # legacy gen8/9 -- still upstream
    ("libvulkan_intel", "intel"),         # ANV (i915 + xe KMD)
    ("libGLX_nvidia", "nvidia"),
    ("libnvidia-vulkan", "nvidia"),
    ("libvulkan_nouveau", "nouveau"),
    ("libvulkan_virtio", "virtio"),
    ("libvulkan_lvp", "lvp"),             # lavapipe (software)
    ("libvulkan_freedreno", "freedreno"),
    ("libvulkan_panfrost", "panfrost"),
    ("libvulkan_asahi", "asahi"),
    ("libvulkan_powervr", "powervr"),
)

KMS_DRIVER_VENDOR: Final[dict[str, str]] = {
    "amdgpu": "amd", "radeon": "amd",
    "i915": "intel", "xe": "intel",
    "nvidia": "nvidia", "nvidia-drm": "nvidia", "nouveau": "nouveau",
    "virtio_gpu": "virtio", "vmwgfx": "vmware", "simpledrm": "simple",
}


@dataclass(frozen=True, slots=True)
class VulkanICD:
    manifest: Path
    library: str
    api_version: str
    vendor: str
    is_32bit: bool

    @classmethod
    def parse(cls, manifest: Path) -> Self | None:
        try:
            doc = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        icd = doc.get("ICD") or {}
        lib = str(icd.get("library_path", ""))
        if not lib:
            return None
        base = os.path.basename(lib)
        vendor = "unknown"
        for needle, tag in ICD_LIB_VENDOR:
            if needle in base:
                vendor = tag
                break
        name = manifest.name.lower()
        is32 = ".i686." in name or "32" in name.replace("x86_64", "") or "i386" in name
        return cls(
            manifest=manifest,
            library=base,
            api_version=str(icd.get("api_version", "")),
            vendor=vendor,
            is_32bit=is32,
        )


@cache
def vulkan_icd_dirs() -> tuple[Path, ...]:
    """Canonical loader search path per the Vulkan-Loader driver spec.

    Order matters: higher-precedence directories come first, exactly matching
    `LoaderDriverInterface.md` -> "Driver Discovery on Linux".
    """
    dirs: list[Path] = []

    def add(base: str | os.PathLike[str]) -> None:
        p = Path(base) / "vulkan" / "icd.d"
        if p.is_dir() and p not in dirs:
            dirs.append(p)

    add(XDG_CONFIG_HOME)
    for d in (os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":"):
        if d:
            add(d)
    add("/etc")
    add(XDG_DATA_HOME)
    for d in (
        os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    ).split(":"):
        if d:
            add(d)
    return tuple(dirs)


@cache
def vulkan_icds() -> tuple[VulkanICD, ...]:
    found: list[VulkanICD] = []
    seen: set[str] = set()
    for d in vulkan_icd_dirs():
        with suppress(OSError):
            for manifest in sorted(d.glob("*.json")):
                real = str(manifest.resolve())
                if real in seen:
                    continue
                seen.add(real)
                icd = VulkanICD.parse(manifest)
                if icd:
                    found.append(icd)
    return tuple(found)


@dataclass(frozen=True, slots=True)
class Gpu:
    card: str                 # "card1"
    render_node: str          # "/dev/dri/renderD129" or ""
    primary_node: str         # "/dev/dri/card1"
    pci_addr: str             # "0000:01:00.0"
    vendor_id: int
    device_id: int
    subsystem: str
    driver: str               # kernel module actually bound
    boot_vga: bool
    revision: str
    model: str
    vulkan_name: str = ""
    device_uuid: str = ""

    @property
    def vendor(self) -> str:
        return KMS_DRIVER_VENDOR.get(self.driver) or {
            0x8086: "intel", 0x1002: "amd", 0x1022: "amd", 0x10DE: "nvidia",
            0x1AF4: "virtio",
        }.get(self.vendor_id, "unknown")

    @property
    def vendor_name(self) -> str:
        return VENDOR_NAMES.get(self.vendor_id, f"0x{self.vendor_id:04x}")

    @property
    def is_nvidia(self) -> bool:
        return self.vendor == "nvidia"

    @property
    def is_amd(self) -> bool:
        return self.vendor == "amd"

    @property
    def is_intel(self) -> bool:
        return self.vendor == "intel"

    @property
    def is_software(self) -> bool:
        return self.driver in ("simpledrm", "vkms", "") or self.vendor == "unknown"

    @property
    def dri_prime(self) -> str:
        """Canonical Mesa DRI_PRIME tag: `pci-0000_01_00_0`.

        Passing a raw `0000:01:00.0` (as many launchers do) silently fails: Mesa
        parses the value with `sscanf("pci-%04x_%02x_%02x_%01x")` and falls back
        to device index 0, i.e. the wrong GPU.
        """
        return "pci-" + self.pci_addr.replace(":", "_").replace(".", "_")

    @property
    def vk_device_select(self) -> str:
        """MESA_VK_DEVICE_SELECT / gamescope --prefer-vk-device form."""
        return f"{self.vendor_id:04x}:{self.device_id:04x}"

    @property
    def sysfs(self) -> Path:
        return Path("/sys/bus/pci/devices") / self.pci_addr

    def icds(self, *, include_32bit: bool = True) -> list[VulkanICD]:
        out = [i for i in vulkan_icds() if i.vendor == self.vendor]
        if not include_32bit:
            out = [i for i in out if not i.is_32bit]
        return out

    def icd_files(self, *, include_32bit: bool = True) -> str:
        return ":".join(str(i.manifest) for i in self.icds(include_32bit=include_32bit))

    def describe(self) -> str:
        role = "primary" if self.boot_vga else "offload"
        return f"{self.model} [{self.pci_addr} {self.driver} {role}]"


@cache
def _pci_ids_db() -> dict[tuple[int, int], str]:
    """Lazily parse hwdata's pci.ids for display-class devices only.

    Full parse of pci.ids is ~1.3 MB / 30k lines; we stop as soon as every
    vendor we actually care about has been resolved, so worst case is one linear
    scan and typically far less.
    """
    wanted_vendors = {g.vendor_id for g in _enumerate_gpus_raw()}
    wanted = {(g.vendor_id, g.device_id) for g in _enumerate_gpus_raw()}
    if not wanted:
        return {}
    out: dict[tuple[int, int], str] = {}
    for src in ("/usr/share/hwdata/pci.ids", "/usr/share/misc/pci.ids"):
        if not os.path.isfile(src):
            continue
        try:
            with open(src, "r", encoding="utf-8", errors="replace") as fh:
                cur = -1
                for line in fh:
                    if not line or line[0] == "#":
                        continue
                    if line[0] != "\t":
                        with suppress(ValueError):
                            cur = int(line[:4], 16)
                        if cur not in wanted_vendors:
                            cur = -1
                        continue
                    if cur < 0 or line[1] == "\t":
                        continue
                    with suppress(ValueError):
                        did = int(line[1:5], 16)
                        if (cur, did) in wanted:
                            out[(cur, did)] = line[5:].strip()
                            if len(out) == len(wanted):
                                return out
        except OSError:
            continue
    return out


def _enumerate_gpus_raw() -> tuple[Gpu, ...]:
    global _GPU_RAW
    if _GPU_RAW is not None:
        return _GPU_RAW
    gpus: list[Gpu] = []
    seen: set[str] = set()
    try:
        cards = sorted(
            (p for p in SYSFS_DRM.iterdir() if re.fullmatch(r"card\d+", p.name)),
            key=lambda p: int(p.name[4:]),
        )
    except OSError:
        cards = []
    for card in cards:
        dev = card / "device"
        try:
            pci_dir = dev.resolve(strict=True)
        except OSError:
            continue
        # Walk up until a node exposing PCI `vendor` is reached (handles the
        # extra `drm/` and USB/platform indirections on some SoCs).
        probe: Path | None = pci_dir
        for _ in range(8):
            if probe is None:
                break
            if (probe / "vendor").is_file() and (probe / "class").is_file():
                break
            probe = probe.parent if probe.parent != probe else None
        if probe is None or not (probe / "vendor").is_file():
            continue
        addr = probe.name
        if addr in seen:
            continue
        klass = read_int(probe / "class")
        if (klass >> 16) != PCI_CLASS_DISPLAY:
            continue
        seen.add(addr)
        driver = ""
        with suppress(OSError):
            driver = os.path.basename(os.readlink(probe / "driver"))
        if not driver:
            for ln in read_text(card / "device/uevent", 4096).splitlines():
                if ln.startswith("DRIVER="):
                    driver = ln.partition("=")[2].strip()
                    break
        render = ""
        with suppress(OSError):
            for entry in (card.parent).iterdir():
                if entry.name.startswith("renderD"):
                    with suppress(OSError):
                        if entry.resolve().parent.name == card.name or (
                            (entry / "device").resolve() == pci_dir
                        ):
                            render = f"/dev/dri/{entry.name}"
                            break
        vid = read_int(probe / "vendor")
        did = read_int(probe / "device")
        gpus.append(
            Gpu(
                card=card.name,
                render_node=render,
                primary_node=f"/dev/dri/{card.name}",
                pci_addr=addr,
                vendor_id=vid,
                device_id=did,
                subsystem=f"{read_int(probe / 'subsystem_vendor'):04x}:"
                          f"{read_int(probe / 'subsystem_device'):04x}",
                driver=driver,
                boot_vga=read_int(probe / "boot_vga") == 1,
                revision=read_first_line(probe / "revision"),
                model="",
            )
        )
    _GPU_RAW = tuple(gpus)
    return _GPU_RAW


_GPU_RAW: tuple[Gpu, ...] | None = None


@cache
def _vulkan_devices_info() -> dict[tuple[int, int], tuple[str, str]]:
    """Parse vulkaninfo --summary to map (vendor_id, device_id) -> (deviceName, deviceUUID)."""
    if not have("vulkaninfo"):
        return {}
    res = run_cmd(["vulkaninfo", "--summary"], timeout=5.0)
    if not res.ok:
        return {}
    out: dict[tuple[int, int], tuple[str, str]] = {}
    cur_vid: int | None = None
    cur_did: int | None = None
    cur_name: str = ""
    cur_uuid: str = ""
    for line in res.out.splitlines():
        line = line.strip()
        if line.startswith("GPU") and line.endswith(":"):
            if cur_vid is not None and cur_did is not None:
                out[(cur_vid, cur_did)] = (cur_name, cur_uuid)
            cur_vid = cur_did = None
            cur_name = cur_uuid = ""
        elif "vendorID" in line and "=" in line:
            with suppress(ValueError):
                cur_vid = int(line.split("=")[1].strip(), 16)
        elif "deviceID" in line and "=" in line:
            with suppress(ValueError):
                cur_did = int(line.split("=")[1].strip(), 16)
        elif "deviceName" in line and "=" in line:
            cur_name = line.split("=", 1)[1].strip()
        elif "deviceUUID" in line and "=" in line:
            cur_uuid = line.split("=", 1)[1].strip().replace("-", "")
    if cur_vid is not None and cur_did is not None:
        out[(cur_vid, cur_did)] = (cur_name, cur_uuid)
    return out


@cache
def gpus() -> tuple[Gpu, ...]:
    """Full GPU topology with human-readable model names and Vulkan device info."""
    db = _pci_ids_db()
    vk_info = _vulkan_devices_info()
    out: list[Gpu] = []
    for g in _enumerate_gpus_raw():
        model = db.get((g.vendor_id, g.device_id), "")
        if not model:
            model = f"{g.vendor_name} display {g.device_id:04x}"
        vk_name, vk_uuid = vk_info.get((g.vendor_id, g.device_id), ("", ""))
        out.append(replace(g, model=model, vulkan_name=vk_name, device_uuid=vk_uuid))
    # Deterministic ordering: boot VGA first, then PCI address.
    out.sort(key=lambda g: (not g.boot_vga, g.pci_addr))
    return tuple(out)


class GpuSelection(StrEnum):
    AUTO = "auto"
    DISCRETE = "discrete"
    INTEGRATED = "integrated"
    NVIDIA = "nvidia"
    AMD = "amd"
    INTEL = "intel"
    PRIMARY = "primary"


def enum_or[E: StrEnum](cls: type[E], raw: Any, fallback: E) -> E:
    """Value-lookup coercion for StrEnum.

    `raw in set(SomeStrEnum)` does NOT work: `Enum.__hash__` hashes the member
    *name*, so a lowercase value never lands in the same bucket as its member.
    Constructing by value is the only correct membership test.
    """
    try:
        return cls(str(raw))
    except ValueError:
        if str(raw):
            Log.warn(f"invalid {cls.__name__} value {raw!r}; using {fallback}")
        return fallback


def select_gpu(mode: str, *, prefer_vendor: str = "") -> Gpu | None:
    """Resolve a policy string into a concrete device.

    Supports:
      - Semantic roles: 'auto', 'discrete', 'integrated', 'primary'
      - Vendor names:   'nvidia', 'amd', 'intel'
      - Explicit PCI:   '10de:25a0', '0000:01:00.0', 'pci-0000_01_00_0'
      - Device names:   'card0', 'card1', or numeric index '0', '1'
    """
    devs = [g for g in gpus() if not g.is_software]
    if not devs:
        return None

    # Check explicit device selectors first
    m = mode.strip().lower()
    for g in devs:
        if m in (g.card.lower(), g.pci_addr.lower(), g.dri_prime.lower()):
            return g
        if m.endswith(g.pci_addr.lower()):
            return g
        if ":" in m:
            parts = m.split(":")
            if len(parts) == 2:
                with suppress(ValueError):
                    v = int(parts[0], 16)
                    d = int(parts[1], 16)
                    if v == g.vendor_id and d == g.device_id:
                        return g
    if m.isdigit():
        idx = int(m)
        if 0 <= idx < len(devs):
            return devs[idx]

    igpu = next((g for g in devs if g.boot_vga), devs[0])
    dgpus = [g for g in devs if not g.boot_vga] or [
        g for g in devs if g.is_nvidia and len(devs) > 1
    ]
    match enum_or(GpuSelection, mode, GpuSelection.AUTO):
        case GpuSelection.PRIMARY:
            return igpu
        case GpuSelection.INTEGRATED:
            return igpu
        case GpuSelection.DISCRETE:
            return dgpus[0] if dgpus else igpu
        case GpuSelection.NVIDIA:
            return next((g for g in devs if g.is_nvidia), None)
        case GpuSelection.AMD:
            return next((g for g in devs if g.is_amd), None)
        case GpuSelection.INTEL:
            return next((g for g in devs if g.is_intel), None)
        case _:
            if prefer_vendor:
                hit = next((g for g in devs if g.vendor == prefer_vendor), None)
                if hit:
                    return hit
            # AUTO: prefer the most capable renderer -- a dGPU if one exists,
            # otherwise the boot VGA device.
            return dgpus[0] if dgpus else igpu


def ntsync_available() -> bool:
    """`/dev/ntsync` exists and is usable by this user.

    ntsync landed in mainline 6.14; Wine >= 10.16 and Proton 11 use it by
    default. Presence of the node is necessary and sufficient for Wine to pick
    it up -- there is no probing ioctl worth issuing here.
    """
    try:
        st = os.stat("/dev/ntsync")
    except OSError:
        return False
    return stat.S_ISCHR(st.st_mode) and os.access("/dev/ntsync", os.R_OK | os.W_OK)


# ==============================================================================
# SECTION 6 -- Display topology (Wayland only)
# ==============================================================================
@dataclass(frozen=True, slots=True)
class Output:
    name: str
    width: int
    height: int
    refresh_hz: float
    scale: float
    focused: bool
    vrr: bool
    hdr: bool

    @property
    def logical_width(self) -> int:
        return int(self.width / self.scale) if self.scale else self.width

    @property
    def logical_height(self) -> int:
        return int(self.height / self.scale) if self.scale else self.height


def _outputs_hyprland() -> list[Output]:
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    payload = ""
    if sig:
        # Talk to the Hyprland IPC socket directly: no hyprctl fork, ~0.3 ms.
        for base in (XDG_RUNTIME_DIR / "hypr", Path("/tmp/hypr")):
            sock_path = base / sig / ".socket.sock"
            if not sock_path.exists():
                continue
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.settimeout(1.0)
                    s.connect(str(sock_path))
                    s.sendall(b"j/monitors")
                    chunks = []
                    while data := s.recv(65536):
                        chunks.append(data)
                payload = b"".join(chunks).decode("utf-8", "replace")
                break
            except OSError:
                continue
    if not payload and have("hyprctl"):
        payload = run_cmd(["hyprctl", "monitors", "-j"], timeout=1.5).out
    if not payload:
        return []
    try:
        doc = json.loads(payload)
    except ValueError:
        return []
    outs: list[Output] = []
    for m in doc:
        outs.append(
            Output(
                name=str(m.get("name", "")),
                width=int(m.get("width", 0) or 0),
                height=int(m.get("height", 0) or 0),
                refresh_hz=float(m.get("refreshRate", 0) or 0),
                scale=float(m.get("scale", 1) or 1),
                focused=bool(m.get("focused")),
                vrr=bool(m.get("vrr")),
                hdr=bool((m.get("currentFormat") or "").upper().find("2101010") >= 0),
            )
        )
    return outs


def _outputs_wlr() -> list[Output]:
    if not have("wlr-randr"):
        return []
    r = run_cmd(["wlr-randr", "--json"], timeout=1.5)
    if not r.ok:
        return []
    try:
        doc = json.loads(r.out)
    except ValueError:
        return []
    outs: list[Output] = []
    for mon in doc:
        cur = next((m for m in mon.get("modes", []) if m.get("current")), None)
        if not cur:
            continue
        outs.append(
            Output(
                name=str(mon.get("name", "")),
                width=int(cur.get("width", 0) or 0),
                height=int(cur.get("height", 0) or 0),
                refresh_hz=float(cur.get("refresh", 0) or 0),
                scale=float(mon.get("scale", 1) or 1),
                focused=bool(mon.get("focused", False)),
                vrr=bool(mon.get("adaptive_sync", False)),
                hdr=False,
            )
        )
    return outs


def _outputs_kscreen() -> list[Output]:
    if not have("kscreen-doctor"):
        return []
    r = run_cmd(["kscreen-doctor", "-j"], timeout=2.0)
    if not r.ok:
        return []
    try:
        doc = json.loads(r.out)
    except ValueError:
        return []
    outs: list[Output] = []
    for o in doc.get("outputs", []):
        if not o.get("enabled"):
            continue
        mode = next(
            (m for m in o.get("modes", []) if m.get("id") == o.get("currentModeId")),
            None,
        )
        if not mode:
            continue
        size = mode.get("size", {})
        outs.append(
            Output(
                name=str(o.get("name", "")),
                width=int(size.get("width", 0) or 0),
                height=int(size.get("height", 0) or 0),
                refresh_hz=float(mode.get("refreshRate", 0) or 0),
                scale=float(o.get("scale", 1) or 1),
                focused=bool(o.get("primary", False)),
                vrr=str(o.get("vrrPolicy", "")).lower() in ("always", "automatic"),
                hdr=bool(o.get("hdr", False)),
            )
        )
    return outs


@cache
def outputs() -> tuple[Output, ...]:
    if not os.environ.get("WAYLAND_DISPLAY"):
        Log.debug("no WAYLAND_DISPLAY: display topology unavailable")
    for probe in (_outputs_hyprland, _outputs_wlr, _outputs_kscreen):
        try:
            found = probe()
        except Exception as exc:  # defensive: never let discovery kill a launch
            Log.trace(f"output probe {probe.__name__} failed: {exc}")
            continue
        if found:
            return tuple(found)
    return ()


def active_output() -> Output:
    outs = outputs()
    if not outs:
        return Output("virtual", 1920, 1080, 60.0, 1.0, True, False, False)
    return next((o for o in outs if o.focused), outs[0])

# ==============================================================================
# SECTION 7 -- Declarative configuration hierarchy
#
#   global config.toml  ->  preset chain (`extends`, recursive)  ->  profile
#   ->  conditional [when.*] overlays  ->  CLI --set overrides
#
# Merge is structural and copy-on-write: only the branches that actually differ
# are duplicated. `copy.deepcopy` of the whole tree per profile (as in the
# previous engine) is O(profiles x tree) allocations on every dashboard repaint.
# ==============================================================================
TomlDict = dict[str, Any]


def deep_merge(base: Mapping[str, Any], over: Mapping[str, Any]) -> TomlDict:
    """Recursive dict merge. Lists REPLACE (never append) so a profile can
    always clear an inherited list; scalars replace."""
    out: TomlDict = dict(base)
    for k, v in over.items():
        cur = out.get(k)
        if isinstance(cur, Mapping) and isinstance(v, Mapping):
            out[k] = deep_merge(cur, v)
        elif isinstance(v, list):
            out[k] = list(v)
        elif isinstance(v, Mapping):
            out[k] = dict(v)
        else:
            out[k] = v
    return out


_VAR_RE: Final = re.compile(r"\$(\w+)|\$\{(\w+)\}")
_MAX_EXPANSION_PASSES: Final = 8


def expand_str(value: str, ctx: Mapping[str, str]) -> str:
    """Expand $VAR / ${VAR} from `ctx`, leaving unmatched tokens intact."""
    try:
        from string import Template
        res = Template(value).safe_substitute(ctx)
        return os.path.expanduser(res) if res.startswith("~") else res
    except Exception:
        return value


def expand_tree(node: Any, ctx: Mapping[str, str]) -> Any:
    match node:
        case str():
            return expand_str(node, ctx)
        case dict():
            return {k: expand_tree(v, ctx) for k, v in node.items()}
        case list():
            return [expand_tree(v, ctx) for v in node]
        case _:
            return node


def load_toml(path: Path) -> TomlDict:
    if not path.is_file():
        return {}
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: malformed TOML -- {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


class ConfigError(RuntimeError):
    pass


def dotted_set(tree: TomlDict, dotted: str, raw: str) -> None:
    """Apply `--set graphics.gamescope.width=1280` with TOML-ish coercion."""
    keys = dotted.split(".")
    node = tree
    for k in keys[:-1]:
        nxt = node.get(k)
        if not isinstance(nxt, dict):
            nxt = {}
            node[k] = nxt
        node = nxt
    node[keys[-1]] = coerce_scalar(raw)


def coerce_scalar(raw: str) -> Any:
    low = raw.strip().lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "none", ""):
        return ""
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        return [coerce_scalar(x) for x in shlex.split(inner.replace(",", " "))] if inner else []
    with suppress(ValueError):
        return int(raw, 0)
    with suppress(ValueError):
        return float(raw)
    return raw


DEFAULT_CONFIG_TOML: Final = '''\
# ==============================================================================
#  Master Game Runner Engine -- global defaults
#  Every key here can be overridden by a preset, a profile, or `--set k.v=x`.
# ==============================================================================
schema = 3

[runner]
auto_mount            = true      # mount DwarFS/overlay before launch
auto_unmount_on_exit  = true
mount_timeout_s       = 20.0      # readiness poll budget per FUSE layer
use_systemd_scope     = true      # launch inside a transient user scope (cgroup v2)
scope_slice           = "app-games.slice"
inhibit_idle          = true
inhibit_sleep         = true
notifications         = true
kill_grace_s          = 8.0       # SIGTERM -> SIGKILL escalation window

[storage]
dwarfs_cache_percent  = 25        # percent of MemAvailable, clamped 64 MiB..8 GiB
dwarfs_workers        = 0         # 0 = min(8, cpus/2)
dwarfs_tidy_strategy  = "time"
dwarfs_tidy_interval  = "5m"
dwarfs_tidy_max_age   = "10m"
dwarfs_seq_detector   = 4
dwarfs_readahead      = "0"
dwarfs_preload_all    = false
dwarfs_preload_category = ""      # e.g. "hotness" for mkdwarfs --categorize=hotness
dwarfs_block_allocator = "mmap"
auto_clean_workdir    = true
union_backend         = "fuse-overlayfs"

[graphics]
gpu                   = "auto"    # auto|discrete|integrated|primary|nvidia|amd|intel
prefer_xwayland       = false     # pure Wayland by default
hdr                   = false
gl_threaded           = true
vsync                 = "default" # default|on|off
shader_cache          = true
shader_cache_size_gb  = 12

[graphics.gamescope]
enabled               = false
backend               = "wayland" # wayland|sdl|drm|headless
mode                  = "borderless"  # borderless|fullscreen|windowed
width                 = 0         # 0 -> follow output
height                = 0
output_width          = 0
output_height         = 0
refresh_rate          = 0
scaler                = ""        # auto|integer|fit|fill|stretch
filter                = ""        # linear|nearest|fsr|nis|pixel
fsr_sharpness         = 5         # 0 (sharpest) .. 20 (softest)
adaptive_sync         = false
immediate_flips       = false     # DRM backend only
force_grab_cursor     = false
grab_keyboard         = false
realtime              = true      # --rt
hdr                   = false
xwayland_count        = 0
mangoapp              = true      # use --mangoapp instead of MANGOHUD=1 inside
extra_args            = []

[performance]
gamemode              = true
mangohud              = false
mangohud_preset       = ""
fps_limit             = 0
cpu_affinity          = ""        # "" | "0-7" | "pcores" | "smt-off"
scope_cpu_weight      = 0         # 0 = leave to systemd default (100)
scope_io_weight       = 0
scope_memory_high     = ""        # e.g. "24G"

[audio]
driver                = "pipewire"
quantum               = 1024      # frames; 1024/48000 = 21.3 ms -- safe for games
rate                  = 48000
openal_driver         = "pipewire"

[input]
sdl_gamecontrollerconfig = ""
raw_input             = true

[runtime]
type                  = "native"  # native|wine|proton|umu|script

[runtime.wine]
wine_binary           = "wine"
arch                  = "win64"
prefix_dir            = "prefix"
sync_mode             = "auto"    # auto|ntsync|fsync|esync|server
debug                 = "-all"
large_address_aware   = true
dxvk                  = true
vkd3d                 = true
dxvk_nvapi            = false
hide_wine             = false
disable_menubuilder   = true
dll_overrides         = {}
redistributables      = []
winetricks            = []

[sandbox]
enabled               = false
bind_gpu              = true
bind_audio            = true
bind_wayland          = true
bind_network          = false
isolate_home          = true

[hooks]
pre_mount   = []
post_mount  = []
pre_launch  = []
post_launch = []
post_unmount = []
'''

DEFAULT_PRESETS: Final[dict[str, str]] = {
    "base_native": '''\
# Native ELF game: no translation layer, Wayland-first.
[runtime]
type = "native"

[performance]
gamemode = true
''',
    "base_wine": '''\
# Wine-Staging / DXVK baseline for Win64 titles.
[runtime]
type = "wine"

[runtime.wine]
arch = "win64"
sync_mode = "auto"
dxvk = true
vkd3d = true
disable_menubuilder = true
dll_overrides = { "winemenubuilder.exe" = "", "mscoree" = "", "mshtml" = "" }

[performance]
gamemode = true
''',
    "base_proton_umu": '''\
extends = "base_wine"

# UMU (Open Wine Components) runs Proton inside the Steam Linux Runtime without
# Steam. GAMEID/STORE drive umu-database protonfixes.
[runtime]
type = "umu"

[runtime.wine]
wine_binary = "umu-run"

[runtime.umu]
proton = "GE-Proton"
store = "none"
game_id = ""
''',
    "base_unity": '''\
extends = "base_native"

[graphics]
gl_threaded = true

[input]
raw_input = true
''',
    "base_unreal5": '''\
extends = "base_wine"

# UE5 hammers vm.max_map_count and benefits from a large shader cache.
[graphics]
shader_cache_size_gb = 24

[runtime.wine]
vkd3d = true
large_address_aware = true

[performance]
gamemode = true
''',
    "handheld_720p": '''\
[graphics.gamescope]
enabled = true
width = 1280
height = 720
output_width = 1280
output_height = 720
filter = "fsr"
fsr_sharpness = 5
mode = "fullscreen"

[performance]
fps_limit = 60
mangohud = true
''',
}


@dataclass(slots=True)
class Profile:
    pid: str
    path: Path
    cfg: TomlDict

    def sect(self, *keys: str) -> TomlDict:
        node: Any = self.cfg
        for k in keys:
            node = node.get(k) if isinstance(node, Mapping) else None
            if node is None:
                return {}
        return dict(node) if isinstance(node, Mapping) else {}

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.cfg
        for k in dotted.split("."):
            if not isinstance(node, Mapping) or k not in node:
                return default
            node = node[k]
        return node

    @property
    def name(self) -> str:
        return str(self.get("meta.name") or self.pid)

    @property
    def icon(self) -> str:
        return str(self.get("meta.icon") or "input-gaming")

    @property
    def runtime(self) -> str:
        return str(self.get("runtime.type") or "native")

    @property
    def installed_hint(self) -> str:
        return str(self.get("paths.game_dir") or "")


class ProfileManager:
    """Loader + cache for the whole declarative hierarchy."""

    __slots__ = ("root", "profiles_dir", "presets_dir", "_global", "_cache", "_preset_cache")

    def __init__(self, root: Path = ROOT_DIR) -> None:
        self.root = root
        self.profiles_dir = root / "profiles"
        self.presets_dir = root / "presets"
        self._global: TomlDict | None = None
        self._cache: dict[str, Profile] = {}
        self._preset_cache: dict[str, TomlDict] = {}

    # -- discovery --------------------------------------------------------
    @property
    def global_config(self) -> TomlDict:
        if self._global is None:
            base = tomllib.loads(DEFAULT_CONFIG_TOML)
            user = load_toml(self.root / "config.toml")
            self._global = deep_merge(base, user)
        return self._global

    def discover_profiles(self) -> dict[str, Path]:
        out: dict[str, Path] = {}
        with suppress(OSError):
            for p in sorted(self.profiles_dir.glob("*.toml")):
                if not p.name.startswith("_"):
                    out[p.stem] = p
        return out

    def discover_presets(self) -> dict[str, Path]:
        out: dict[str, Path] = {}
        with suppress(OSError):
            for p in sorted(self.presets_dir.glob("*.toml")):
                out[p.stem] = p
        for name in DEFAULT_PRESETS:
            out.setdefault(name, self.presets_dir / f"{name}.toml")
        return out

    # -- preset chain -----------------------------------------------------
    def preset(self, name: str, _seen: frozenset[str] = frozenset()) -> TomlDict:
        if name in self._preset_cache:
            return self._preset_cache[name]
        if name in _seen:
            raise ConfigError(
                "circular preset inheritance: " + " -> ".join([*_seen, name])
            )
        path = self.presets_dir / f"{name}.toml"
        if path.is_file():
            data = load_toml(path)
        elif name in DEFAULT_PRESETS:
            data = tomllib.loads(DEFAULT_PRESETS[name])
        else:
            raise ConfigError(f"unknown preset {name!r} (looked in {self.presets_dir})")
        parent = data.get("extends")
        if isinstance(parent, str) and parent:
            data = deep_merge(self.preset(parent, _seen | {name}), data)
        elif isinstance(parent, list):
            merged: TomlDict = {}
            for p in parent:
                merged = deep_merge(merged, self.preset(str(p), _seen | {name}))
            data = deep_merge(merged, data)
        self._preset_cache[name] = data
        return data

    # -- profile ----------------------------------------------------------
    def load(
        self,
        ident: str,
        *,
        overrides: Mapping[str, Any] | None = None,
        use_cache: bool = True,
    ) -> Profile:
        key = ident if not overrides else f"{ident}#{id(overrides)}"
        if use_cache and not overrides and key in self._cache:
            return self._cache[key]

        cand = Path(ident).expanduser()
        if cand.is_file():
            path, pid = cand.resolve(), cand.stem
        else:
            pid = ident
            path = self.profiles_dir / f"{pid}.toml"
            if not path.is_file():
                raise ConfigError(
                    f"profile {pid!r} not found -- expected {path}"
                )
        raw = load_toml(path)

        cfg = dict(self.global_config)
        ext = raw.get("extends")
        if isinstance(ext, str) and ext:
            cfg = deep_merge(cfg, self.preset(ext))
        elif isinstance(ext, list):
            for p in ext:
                cfg = deep_merge(cfg, self.preset(str(p)))
        cfg = deep_merge(cfg, raw)

        # Conditional overlays evaluated against live hardware/session facts.
        cfg = self._apply_conditionals(cfg)

        if overrides:
            cfg = deep_merge(cfg, overrides)

        meta = dict(cfg.get("meta") or {})
        meta.setdefault("id", pid)
        meta.setdefault("name", pid.replace("_", " ").title())
        cfg["meta"] = meta

        game_dir = str((cfg.get("paths") or {}).get("game_dir") or "")
        base_ctx = {
            "ROOT": str(self.root),
            "HOME": str(HOME),
            "USER": os.environ.get("USER") or HOME.name,
            "XDG_DATA_HOME": str(XDG_DATA_HOME),
            "XDG_CONFIG_HOME": str(XDG_CONFIG_HOME),
            "XDG_CACHE_HOME": str(XDG_CACHE_HOME),
            "XDG_STATE_HOME": str(XDG_STATE_HOME),
            "XDG_RUNTIME_DIR": str(XDG_RUNTIME_DIR),
            "STATE_DIR": str(STATE_DIR),
            "CACHE_DIR": str(CACHE_DIR),
        }
        ctx = dict(base_ctx)
        ctx["PROFILE_ID"] = pid
        ctx["GAME_DIR"] = expand_str(game_dir, base_ctx)
        cfg = expand_tree(cfg, ctx)

        prof = Profile(pid=pid, path=path, cfg=cfg)
        if use_cache and not overrides:
            self._cache[key] = prof
        return prof

    def _apply_conditionals(self, cfg: TomlDict) -> TomlDict:
        """Merge `[when.<predicate>]` tables when the predicate holds.

        Supported predicates (composable, all evaluated in declaration order):
          when.nvidia / when.amd / when.intel   -- vendor present in topology
          when.hybrid / when.single_gpu
          when.ntsync                            -- /dev/ntsync usable
          when.hdr_output                        -- focused output reports HDR
          when.hyprland / when.kwin
          when.kernel_ge_7_1
        """
        when = cfg.pop("when", None)
        if not isinstance(when, Mapping):
            return cfg
        devs = gpus()
        krel = kernel_release()
        facts = {
            "nvidia": any(g.is_nvidia for g in devs),
            "amd": any(g.is_amd for g in devs),
            "intel": any(g.is_intel for g in devs),
            "hybrid": len([g for g in devs if not g.is_software]) > 1,
            "single_gpu": len([g for g in devs if not g.is_software]) <= 1,
            "ntsync": ntsync_available(),
            "hdr_output": active_output().hdr,
            "hyprland": bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")),
            "kwin": "KDE" in (os.environ.get("XDG_CURRENT_DESKTOP") or "").upper(),
            "wayland": bool(os.environ.get("WAYLAND_DISPLAY")),
            "kernel_ge_7_1": krel >= (7, 1),
            "gamescope": have("gamescope"),
        }
        for key, overlay in when.items():
            if not isinstance(overlay, Mapping):
                continue
            negate = key.startswith("not_")
            probe = key[4:] if negate else key
            value = facts.get(probe)
            if value is None:
                Log.debug(f"[when.{key}] unknown predicate -- ignored")
                continue
            if bool(value) != negate:
                cfg = deep_merge(cfg, overlay)
        return cfg


# ==============================================================================
# SECTION 8 -- Path model
# ==============================================================================
@dataclass(frozen=True, slots=True)
class GamePaths:
    game_dir: Path
    dwarfs_image: Path | None
    dwarfs_mount: Path
    overlay_dir: Path
    overlay_upper: Path
    overlay_work: Path
    prefix_dir: Path
    executable: str
    working_dir: str

    @property
    def uses_dwarfs(self) -> bool:
        return self.dwarfs_image is not None and self.dwarfs_image.is_file()

    @property
    def root(self) -> Path:
        """Directory the game actually runs out of."""
        return self.overlay_dir if self.uses_dwarfs else self.game_dir


def resolve_paths(prof: Profile) -> GamePaths:
    pcfg = prof.sect("paths")
    raw_dir = str(pcfg.get("game_dir") or "").strip()
    if not raw_dir:
        raise ConfigError(
            f"[{prof.pid}] paths.game_dir is mandatory (an empty value would "
            "silently resolve to the current working directory)"
        )
    game_dir = Path(raw_dir).expanduser()
    if not game_dir.is_absolute():
        game_dir = (prof.path.parent / game_dir).resolve()

    def under(value: str, default: str) -> Path:
        p = Path(str(value or default)).expanduser()
        return p if p.is_absolute() else (game_dir / p)

    img_raw = str(pcfg.get("dwarfs_image") or "").strip()
    image: Path | None = None
    if img_raw:
        image = under(img_raw, "")
        if not image.is_file():
            # A profile may declare a glob (release repacks rename the image).
            matches = sorted(game_dir.glob(img_raw)) if not os.path.isabs(img_raw) else []
            image = matches[0] if matches else image

    wine_cfg = prof.sect("runtime", "wine")
    return GamePaths(
        game_dir=game_dir,
        dwarfs_image=image,
        dwarfs_mount=under(pcfg.get("dwarfs_mount"), ".mnt/dwarfs"),
        overlay_dir=under(pcfg.get("overlay_dir"), ".mnt/root"),
        overlay_upper=under(pcfg.get("overlay_storage"), ".mnt/upper"),
        overlay_work=under(pcfg.get("overlay_work"), ".mnt/work"),
        prefix_dir=under(wine_cfg.get("prefix_dir"), "prefix"),
        executable=str(pcfg.get("executable") or "").strip(),
        working_dir=str(pcfg.get("working_dir") or "").strip(),
    )


def profile_installed(prof: Profile) -> bool:
    """Cheap, bounded availability probe -- never walks the whole game tree."""
    try:
        p = resolve_paths(prof)
    except ConfigError:
        return False
    try:
        if not p.game_dir.is_dir():
            return False
    except OSError:
        return False
    if p.dwarfs_image is not None:
        try:
            return p.dwarfs_image.is_file() and p.dwarfs_image.stat().st_size > 0
        except OSError:
            return False
    if p.executable:
        for cand in (p.overlay_dir / p.executable, p.game_dir / p.executable):
            with suppress(OSError):
                if cand.is_file():
                    return True
        # Bounded fallback: only the first two directory levels are scanned.
        name = os.path.basename(p.executable)
        with suppress(OSError):
            for depth1 in p.game_dir.iterdir():
                if depth1.name == name and depth1.is_file():
                    return True
                if depth1.is_dir():
                    with suppress(OSError):
                        if (depth1 / name).is_file():
                            return True
        return False
    with suppress(OSError):
        return next(p.game_dir.iterdir(), None) is not None
    return False


# ==============================================================================
# SECTION 9 -- Mount engine (DwarFS + rootless union)
# ==============================================================================
def _fuse_escape(value: str) -> str:
    """Escape a path for a FUSE `-o` option list.

    `lowerdir` is colon-separated and the whole option string is
    comma-separated, so an unescaped ':' or ',' in a game path silently mounts
    the wrong tree (or fails with a confusing EINVAL). Backslash escaping is the
    documented overlayfs/fuse-overlayfs convention.
    """
    return value.replace("\\", "\\\\").replace(":", "\\:").replace(",", "\\,")


def _dur_ok(value: str, default: str) -> str:
    return value if re.fullmatch(r"\d+(ms|s|m|h)?", str(value or "")) else default


class MountState(StrEnum):
    UNMOUNTED = "unmounted"
    PARTIAL = "partial"
    MOUNTED = "mounted"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class MountStatus:
    dwarfs: bool
    overlay: bool
    stale: tuple[Path, ...]

    @property
    def state(self) -> MountState:
        if self.stale:
            return MountState.STALE
        if self.dwarfs and self.overlay:
            return MountState.MOUNTED
        if self.dwarfs or self.overlay:
            return MountState.PARTIAL
        return MountState.UNMOUNTED


class MountEngine:
    """DwarFS (read-only, compressed) + fuse-overlayfs (rootless writable union).

    Recovery model
    --------------
    Every operation begins by *reconciling* the observed kernel state with the
    desired state instead of blindly tearing everything down. A crashed FUSE
    daemon (ENOTCONN) is detected, lazily detached and re-mounted; a half-built
    stack (dwarfs up, overlay down) is completed rather than restarted.
    """

    FSTYPE_DWARFS: Final = ("fuse.dwarfs", "fuse.dwarfs2", "fuse")
    FSTYPE_OVERLAY: Final = ("fuse.fuse-overlayfs", "fuse-overlayfs", "overlay")

    # -- inspection -------------------------------------------------------
    @staticmethod
    def status(paths: GamePaths, *, refresh: bool = False) -> MountStatus:
        if not paths.uses_dwarfs:
            return MountStatus(False, False, ())
        tbl = mount_table(force=refresh)
        stale: list[Path] = []
        results: list[bool] = []
        for mp in (paths.dwarfs_mount, paths.overlay_dir):
            if not tbl.is_mount(mp):
                results.append(False)
                continue
            if fuse_alive(mp):
                results.append(True)
            else:
                results.append(False)
                stale.append(mp)
        return MountStatus(results[0], results[1], tuple(stale))

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _wait_ready(mp: Path, timeout: float) -> bool:
        """Poll mountinfo until the FUSE daemon has published its superblock.

        `dwarfs`/`fuse-overlayfs` daemonise and return 0 *before* the mount is
        visible; launching immediately after exec is a real race that manifests
        as ENOENT on the game executable.
        """
        deadline = time.monotonic() + timeout
        delay = 0.005
        while time.monotonic() < deadline:
            if mount_table(force=True).is_mount(mp) and fuse_alive(mp):
                return True
            time.sleep(delay)
            delay = min(delay * 1.6, 0.25)
        return False

    @classmethod
    def _detach(cls, mp: Path, *, lazy: bool = True) -> bool:
        if not mount_table(force=True).is_mount(mp):
            return True
        args = ["fusermount3", "-u"]
        if lazy:
            args.append("-z")
        args.append(str(mp))
        r = run_cmd(args, timeout=10.0)
        if r.ok:
            return True
        # umount(8) works for FUSE mounts owned by the caller on modern util-linux.
        r2 = run_cmd(["umount", "-l", str(mp)], timeout=10.0)
        if r2.ok:
            return True
        Log.debug(f"detach {mp}: {r.message} / {r2.message}")
        return not mount_table(force=True).is_mount(mp)

    @staticmethod
    def _dwarfs_argv(paths: GamePaths, cfg: Mapping[str, Any]) -> list[str] | None:
        mem = MemInfo.read()
        pct = max(1, min(90, int(cfg.get("dwarfs_cache_percent", 25) or 25)))
        cache_kib = mem.available_kib * pct // 100
        cache_kib = max(64 * 1024, min(cache_kib, 8 * 1024 * 1024))
        workers = int(cfg.get("dwarfs_workers", 0) or 0) or max(2, min(8, cpu_count() // 2))

        bundled = paths.game_dir / "files" / "dwarfs-binary"
        argv: list[str]
        if bundled.is_file():
            with suppress(OSError):
                if not os.access(bundled, os.X_OK):
                    bundled.chmod(bundled.stat().st_mode | 0o111)
            argv = [str(bundled), "--tool=dwarfs"]
        elif have("dwarfs"):
            argv = ["dwarfs"]
        else:
            return None

        argv += [str(paths.dwarfs_image), str(paths.dwarfs_mount)]
        opts: list[str] = [
            f"cachesize={cache_kib}k",
            f"workers={workers}",
            f"block_allocator={cfg.get('dwarfs_block_allocator', 'mmap')}",
            f"seq_detector={int(cfg.get('dwarfs_seq_detector', 4) or 4)}",
            "cache_files",
            "clone_fd",        # per-thread /dev/fuse fd: removes the FUSE lock convoy
            "auto_unmount",    # kernel detaches if the daemon dies -> no ENOTCONN leak
            "noatime",
            "ro",
        ]
        strategy = str(cfg.get("dwarfs_tidy_strategy", "time") or "none")
        if strategy in ("time", "swap"):
            opts.append(f"tidy_strategy={strategy}")
            opts.append("tidy_interval=" + _dur_ok(cfg.get("dwarfs_tidy_interval"), "5m"))
            if strategy == "time":
                opts.append("tidy_max_age=" + _dur_ok(cfg.get("dwarfs_tidy_max_age"), "10m"))
        readahead = str(cfg.get("dwarfs_readahead", "") or "").strip()
        if readahead and readahead != "0":
            opts.append(f"readahead={readahead}")
        category = str(cfg.get("dwarfs_preload_category", "") or "").strip()
        if category:
            opts.append(f"preload_category={category}")
        elif cfg.get("dwarfs_preload_all"):
            opts.append("preload_all")
        for o in opts:
            argv += ["-o", o]
        return argv

    @staticmethod
    def _overlay_argv(paths: GamePaths) -> list[str]:
        lower = _fuse_escape(str(paths.dwarfs_mount))
        upper = _fuse_escape(str(paths.overlay_upper))
        work = _fuse_escape(str(paths.overlay_work))
        return [
            "fuse-overlayfs",
            "-o", f"lowerdir={lower}",
            "-o", f"upperdir={upper}",
            "-o", f"workdir={work}",
            "-o", "noacl",       # avoids EOPNOTSUPP storms on tmpfs/zram uppers
            "-o", "auto_unmount",
            "-o", "clone_fd",
            str(paths.overlay_dir),
        ]

    # -- operations -------------------------------------------------------
    @classmethod
    def mount(cls, prof: Profile, paths: GamePaths, *, dry_run: bool = False) -> bool:
        if not paths.uses_dwarfs:
            Log.debug(f"[{prof.pid}] no dwarfs image declared -- running from game_dir")
            return True

        storage = prof.sect("storage")
        timeout = float(prof.get("runner.mount_timeout_s", 20.0) or 20.0)
        st = cls.status(paths, refresh=True)

        if st.stale:
            Log.warn(
                "stale FUSE endpoint(s) detected: "
                + ", ".join(str(p) for p in st.stale)
                + " -- recovering"
            )
            for mp in reversed(st.stale):
                cls._detach(mp)
            st = cls.status(paths, refresh=True)

        if st.state is MountState.MOUNTED:
            Log.debug(f"[{prof.pid}] already mounted at {paths.overlay_dir}")
            return True

        if dry_run:
            Log.info(f"[dry-run] mkdir -p {paths.dwarfs_mount} {paths.overlay_upper} "
                     f"{paths.overlay_work} {paths.overlay_dir}")
            argv = cls._dwarfs_argv(paths, storage)
            Log.info("[dry-run] " + (shlex.join(argv) if argv else "dwarfs: NOT FOUND"))
            Log.info("[dry-run] " + shlex.join(cls._overlay_argv(paths)))
            return True

        for d in (paths.dwarfs_mount, paths.overlay_upper, paths.overlay_work,
                  paths.overlay_dir):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                Log.error(f"cannot create {d}: {exc}")
                return False

        # overlayfs requires upperdir and workdir on the SAME filesystem.
        try:
            if paths.overlay_upper.stat().st_dev != paths.overlay_work.stat().st_dev:
                Log.error(
                    "overlay upperdir and workdir are on different filesystems "
                    f"({paths.overlay_upper} vs {paths.overlay_work}) -- "
                    "the union mount cannot be created"
                )
                return False
        except OSError as exc:
            Log.error(f"overlay dirs unusable: {exc}")
            return False

        # A workdir left over from an unclean shutdown makes fuse-overlayfs
        # refuse to mount; it is by definition disposable state.
        if not st.dwarfs and prof.get("storage.auto_clean_workdir", True):
            cls._purge_workdir(paths.overlay_work)

        if not st.dwarfs:
            argv = cls._dwarfs_argv(paths, storage)
            if argv is None:
                Log.error(
                    "no DwarFS driver: install `dwarfs` (extra/dwarfs) or ship "
                    "files/dwarfs-binary with the game"
                )
                return False
            Log.info(
                f"dwarfs: {paths.dwarfs_image.name} -> {paths.dwarfs_mount} "
                f"({_human_kib(_cachesize_of(argv))} cache)"
            )
            r = run_cmd(argv, timeout=60.0)
            if not r.ok:
                Log.error(f"dwarfs mount failed: {r.message}")
                return False
            if not cls._wait_ready(paths.dwarfs_mount, timeout):
                Log.error(f"dwarfs mount did not become ready within {timeout}s")
                cls._detach(paths.dwarfs_mount)
                return False

        if not st.overlay:
            if not have("fuse-overlayfs"):
                Log.error("fuse-overlayfs not installed (pacman -S fuse-overlayfs)")
                cls._detach(paths.dwarfs_mount)
                return False
            argv = cls._overlay_argv(paths)
            Log.info(f"fuse-overlayfs: union -> {paths.overlay_dir}")
            r = run_cmd(argv, timeout=30.0)
            if not r.ok:
                Log.error(f"fuse-overlayfs mount failed: {r.message}")
                cls._detach(paths.dwarfs_mount)
                return False
            if not cls._wait_ready(paths.overlay_dir, timeout):
                Log.error(f"union mount did not become ready within {timeout}s")
                cls._detach(paths.overlay_dir)
                cls._detach(paths.dwarfs_mount)
                return False

        Log.ok(f"{prof.name} mounted at {paths.overlay_dir}")
        return True

    @classmethod
    def unmount(cls, prof: Profile, paths: GamePaths, *, dry_run: bool = False,
                quiet: bool = False) -> bool:
        if not paths.uses_dwarfs:
            return True
        if dry_run:
            Log.info(f"[dry-run] unmount {paths.overlay_dir} then {paths.dwarfs_mount}")
            return True
        ok = True
        # Strict order: union first (it holds the lower open), then the image.
        for mp in (paths.overlay_dir, paths.dwarfs_mount):
            if mount_table(force=True).is_mount(mp) and not cls._detach(mp):
                ok = False
                if not quiet:
                    Log.warn(f"could not detach {mp}")
        if ok and prof.get("storage.auto_clean_workdir", True):
            if not mount_table(force=True).is_mount(paths.overlay_dir):
                cls._purge_workdir(paths.overlay_work)
        if ok and not quiet:
            Log.ok(f"{prof.name} unmounted")
        return ok

    @staticmethod
    def _purge_workdir(work: Path) -> None:
        """Remove the overlay workdir contents only -- never the mount point."""
        if not work.is_dir():
            return
        if mount_table().is_mount(work):
            Log.debug(f"refusing to purge {work}: it is itself a mount point")
            return
        with suppress(OSError):
            for child in work.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)


def _cachesize_of(argv: Sequence[str]) -> int:
    for i, tok in enumerate(argv):
        if tok.startswith("cachesize="):
            with suppress(ValueError):
                return int(tok.split("=", 1)[1].rstrip("k"))
        if tok == "-o" and i + 1 < len(argv) and argv[i + 1].startswith("cachesize="):
            with suppress(ValueError):
                return int(argv[i + 1].split("=", 1)[1].rstrip("k"))
    return 0


def _human_kib(kib: int) -> str:
    if kib >= 1 << 20:
        return f"{kib / (1 << 20):.1f} GiB"
    if kib >= 1 << 10:
        return f"{kib / (1 << 10):.0f} MiB"
    return f"{kib} KiB"

# ==============================================================================
# SECTION 10 -- Wine / Proton prefix engine
# ==============================================================================
@contextmanager
def file_lock(path: Path, *, timeout: float = 120.0) -> Iterator[None]:
    """Advisory exclusive lock. Two launches sharing a prefix corrupt it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o644)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() > deadline:
                    raise TimeoutError(f"prefix lock held too long: {path}") from exc
                Log.debug(f"waiting for prefix lock {path}")
                time.sleep(0.25)
        os.truncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        yield
    finally:
        with suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class SyncMode(StrEnum):
    AUTO = "auto"
    NTSYNC = "ntsync"
    FSYNC = "fsync"
    ESYNC = "esync"
    SERVER = "server"


NVIDIA_WINE_DIRS: Final = (
    Path("/usr/lib/nvidia/wine"),
    Path("/usr/lib32/nvidia/wine"),
    Path("/usr/lib/x86_64-linux-gnu/nvidia/wine"),
    Path("/opt/nvidia/wine"),
)
# nvngx.dll + _nvngx.dll implement DLSS SR/RR; nvofapi64.dll is the Optical Flow
# API required by DLSS Frame Generation. All three ship in nvidia-utils.
NVNGX_DLLS: Final = ("nvngx.dll", "_nvngx.dll", "nvngx_dlssg.dll", "nvofapi64.dll")


class WinePrefix:
    """Prefix lifecycle: creation, repair, provisioning, NVAPI/DLSS wiring."""

    __slots__ = ("path", "wine_bin", "server_bin", "arch", "_is_proton_layout")

    def __init__(self, path: Path, wine_bin: str = "wine", arch: str = "win64") -> None:
        self.path = path
        self.wine_bin = wine_bin
        self.arch = arch
        self.server_bin = self._sibling(wine_bin, "wineserver")
        self._is_proton_layout: bool | None = None

    # -- layout -----------------------------------------------------------
    @staticmethod
    def _sibling(wine_bin: str, name: str) -> str:
        resolved = shutil.which(wine_bin) or wine_bin
        cand = Path(resolved).parent / name
        return str(cand) if cand.is_file() else name

    @property
    def pfx(self) -> Path:
        """Proton nests the real prefix under `pfx/`; Wine does not."""
        nested = self.path / "pfx"
        return nested if (nested / "system.reg").is_file() or nested.is_dir() else self.path

    @property
    def initialised(self) -> bool:
        return (self.pfx / "system.reg").is_file() and (self.pfx / "user.reg").is_file()

    @property
    def drive_c(self) -> Path:
        return self.pfx / "drive_c"

    @property
    def stamp(self) -> Path:
        return self.path / ".master-runner-provision.json"

    def base_env(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ)
        env["WINEPREFIX"] = str(self.path)
        env["WINEARCH"] = self.arch
        env["WINEDEBUG"] = "-all"
        env["DISPLAY"] = env.get("DISPLAY", "")
        env.pop("WINEDLLOVERRIDES", None)
        if extra:
            env.update(extra)
        return env

    def serverwait(self, env: Mapping[str, str], timeout: float = 180.0) -> None:
        run_cmd([self.server_bin, "-w"], env=env, timeout=timeout)

    # -- repair -----------------------------------------------------------
    def prune_broken_symlinks(self) -> int:
        """Remove dangling symlinks left behind by DLL/driver upgrades.

        Scope is deliberately wider than system32/syswow64: `dosdevices` holds
        drive links that, when dangling, make every Wine start stat() a dead
        path, and `windows/globalization` + `openxr` are populated by
        wine-mono / OpenXR loaders that get removed independently.
        """
        removed = 0
        targets = [
            self.drive_c / "windows" / "system32",
            self.drive_c / "windows" / "syswow64",
            self.drive_c / "windows" / "globalization" / "ICU",
            self.drive_c / "openxr",
            self.pfx / "dosdevices",
        ]
        for d in targets:
            if not d.is_dir():
                continue
            with suppress(OSError):
                for item in d.iterdir():
                    if not item.is_symlink():
                        continue
                    try:
                        os.stat(item)  # follows the link
                    except OSError as exc:
                        if exc.errno in (errno.ENOENT, errno.ELOOP, errno.ENOTDIR):
                            with suppress(OSError):
                                item.unlink()
                                removed += 1
        return removed

    def unify_user_dirs(self) -> None:
        """Make Wine (`$USER`) and Proton (`steamuser`) share one profile.

        Correctness detail the previous engine missed: `Path.exists()` returns
        False for a *dangling* symlink, so `if not dst.exists(): dst.symlink_to()`
        raises FileExistsError on every prefix whose `steamuser` link points at a
        renamed user. The symlink itself must be probed with `lstat`.
        """
        users = self.drive_c / "users"
        if not users.is_dir():
            return
        me = os.environ.get("USER") or HOME.name
        mine, steam = users / me, users / "steamuser"

        def dangling(p: Path) -> bool:
            return p.is_symlink() and not p.exists()

        with suppress(OSError):
            if dangling(steam):
                steam.unlink()
            if dangling(mine):
                mine.unlink()
            mine_real = mine.is_dir() and not mine.is_symlink()
            steam_real = steam.is_dir() and not steam.is_symlink()
            if mine_real and not steam.exists():
                steam.symlink_to(me, target_is_directory=True)
            elif steam_real and not mine.exists():
                mine.symlink_to("steamuser", target_is_directory=True)

    def suppress_crash_dialogs(self, env: Mapping[str, str]) -> None:
        """Disable WineDbg's crash dialog and Explorer's shell-folder nagging.

        Appending to `user.reg` while a wineserver is live is futile -- the
        server owns the in-memory registry and rewrites the file on flush,
        discarding the append. The registry must be modified through Wine.
        """
        keys = (
            (r"HKCU\Software\Wine\WineDbg", "ShowCrashDialog", "REG_DWORD", "0"),
            (r"HKCU\Software\Wine\DllOverrides", "winemenubuilder.exe", "REG_SZ", ""),
        )
        for key, name, typ, data in keys:
            argv = [self.wine_bin, "reg", "add", key, "/v", name, "/t", typ, "/f"]
            if data:
                argv += ["/d", data]
            r = run_cmd(argv, env=env, timeout=45.0)
            if not r.ok:
                Log.trace(f"reg add {key}\\{name} -> {r.message}")

    def link_translators(
        self,
        *,
        want_dxvk: bool = True,
        want_vkd3d: bool = True,
        want_nvapi: bool = False,
        want_dlss: bool = False,
    ) -> int:
        """Symlink runtime translators (DXVK, VKD3D, DXVK-NVAPI, DLSS) into prefix."""
        sys32 = self.drive_c / "windows" / "system32"
        syswow = self.drive_c / "windows" / "syswow64"
        if not sys32.is_dir():
            return 0
        linked = 0

        def _find_runtime_dir(pattern: str, sub: str) -> Path | None:
            p = Path(os.path.expanduser(pattern))
            if not p.is_dir():
                return None
            dirs = sorted([d for d in p.iterdir() if d.is_dir()], reverse=True)
            for d in dirs:
                cand = d / sub if (d / sub).is_dir() else d
                if cand.is_dir():
                    return cand
            return None

        # 1. DXVK (D3D9, D3D10, D3D11, DXGI -> Vulkan)
        if want_dxvk:
            for sub, target_dir in (("x64", sys32), ("x32", syswow if syswow.is_dir() else None)):
                if not target_dir:
                    continue
                dxvk_src = (
                    _find_runtime_dir("~/.local/share/lutris/runtime/dxvk", sub)
                    or (Path("/usr/share/dxvk") / sub if (Path("/usr/share/dxvk") / sub).is_dir() else None)
                )
                if dxvk_src:
                    for dll in ("d3d11.dll", "dxgi.dll", "d3d9.dll", "d3d10core.dll", "d3d8.dll"):
                        src = dxvk_src / dll
                        if src.is_file():
                            dst = target_dir / dll
                            with suppress(OSError):
                                if dst.is_symlink():
                                    if os.path.realpath(dst) == str(src.resolve()):
                                        continue
                                    dst.unlink()
                                elif dst.exists():
                                    dst.unlink()
                                dst.symlink_to(src)
                                linked += 1

        # 2. VKD3D (D3D12 -> Vulkan)
        if want_vkd3d:
            for sub, target_dir in (("x64", sys32), ("x32", syswow if syswow.is_dir() else None)):
                if not target_dir:
                    continue
                vkd3d_src = (
                    _find_runtime_dir("~/.local/share/lutris/runtime/vkd3d", sub)
                    or (Path("/usr/share/vkd3d") / sub if (Path("/usr/share/vkd3d") / sub).is_dir() else None)
                )
                if vkd3d_src:
                    for dll in ("d3d12.dll", "d3d12core.dll"):
                        src = vkd3d_src / dll
                        if src.is_file():
                            dst = target_dir / dll
                            with suppress(OSError):
                                if dst.is_symlink():
                                    if os.path.realpath(dst) == str(src.resolve()):
                                        continue
                                    dst.unlink()
                                elif dst.exists():
                                    dst.unlink()
                                dst.symlink_to(src)
                                linked += 1

        # 3. DXVK-NVAPI
        if want_nvapi:
            for sub, target_dir in (("x64", sys32), ("x32", syswow if syswow.is_dir() else None)):
                if not target_dir:
                    continue
                nvapi_src = _find_runtime_dir("~/.local/share/lutris/runtime/dxvk-nvapi", sub)
                if nvapi_src:
                    for dll in ("nvapi64.dll", "nvofapi64.dll", "nvapi.dll"):
                        src = nvapi_src / dll
                        if src.is_file():
                            dst = target_dir / dll
                            with suppress(OSError):
                                if dst.is_symlink():
                                    if os.path.realpath(dst) == str(src.resolve()):
                                        continue
                                    dst.unlink()
                                elif dst.exists():
                                    dst.unlink()
                                dst.symlink_to(src)
                                linked += 1

        # 4. NVIDIA DLSS (nvngx.dll / _nvngx.dll)
        if want_dlss:
            for src_dir in NVIDIA_WINE_DIRS:
                if not src_dir.is_dir():
                    continue
                dst_dir = syswow if "lib32" in str(src_dir) and syswow.is_dir() else sys32
                for dll in NVNGX_DLLS:
                    src = src_dir / dll
                    if src.is_file():
                        dst = dst_dir / dll
                        with suppress(OSError):
                            if dst.is_symlink():
                                if os.path.realpath(dst) == str(src.resolve()):
                                    continue
                                dst.unlink()
                            elif dst.exists():
                                dst.unlink()
                            dst.symlink_to(src)
                            linked += 1

        if linked:
            Log.debug(f"linked {linked} runtime translator libraries into the prefix")
        return linked

    def link_nvidia_dlss(self) -> bool:
        """Compatibility wrapper for link_translators(want_dlss=True)."""
        return self.link_translators(want_dxvk=False, want_vkd3d=False, want_dlss=True) > 0

    # -- provisioning -----------------------------------------------------
    def provision(
        self,
        *,
        root_dir: Path,
        redistributables: Sequence[str],
        winetricks: Sequence[str],
        want_dxvk: bool = True,
        want_vkd3d: bool = True,
        want_nvapi: bool = False,
        want_dlss: bool = False,
        force: bool = False,
        dry_run: bool = False,
    ) -> None:
        want_hash = hashlib.blake2b(
            json.dumps(
                {
                    "redist": sorted(redistributables),
                    "verbs": sorted(winetricks),
                    "arch": self.arch,
                    "engine": ENGINE_VERSION,
                },
                sort_keys=True,
            ).encode(),
            digest_size=16,
        ).hexdigest()

        have_hash = ""
        with suppress(OSError, ValueError):
            raw = self.stamp.read_text(encoding="utf-8")
            if raw.strip().startswith("{"):
                have_hash = json.loads(raw).get("hash", "")

        needs_boot = not self.initialised
        needs_provision = force or have_hash != want_hash

        if dry_run:
            if needs_boot:
                Log.info(f"[dry-run] wineboot -u in {self.path}")
            if needs_provision:
                Log.info(f"[dry-run] provision {len(redistributables)} redists, "
                         f"{len(winetricks)} winetricks verbs")
            return

        with file_lock(RUNTIME_DIR / f"prefix-{_slug(str(self.path))}.lock"):
            env = self.base_env()
            self.path.mkdir(parents=True, exist_ok=True)

            if needs_boot:
                Log.info(f"initialising Wine prefix at {self.path}")
                run_cmd([self.wine_bin, "wineboot", "-u"], env=env, timeout=300.0)
                self.serverwait(env)

            # Repair passes are cheap and idempotent -- always run them.
            pruned = self.prune_broken_symlinks()
            if pruned:
                Log.debug(f"pruned {pruned} dangling prefix symlinks")
            self.unify_user_dirs()

            if needs_provision:
                self._install_redists(env, root_dir, redistributables)
                self._run_winetricks(env, root_dir, winetricks)
                self.suppress_crash_dialogs(env)
                self.serverwait(env)
                with suppress(OSError):
                    self.stamp.write_text(
                        json.dumps(
                            {
                                "hash": want_hash,
                                "engine": ENGINE_VERSION,
                                "at": time.time(),
                                "wine": self.wine_bin,
                            },
                            indent=1,
                        ),
                        encoding="utf-8",
                    )
                Log.ok("prefix provisioning complete")

            self.link_translators(
                want_dxvk=want_dxvk,
                want_vkd3d=want_vkd3d,
                want_nvapi=want_nvapi,
                want_dlss=want_dlss,
            )

    def _install_redists(self, env: Mapping[str, str], root: Path,
                         declared: Sequence[str]) -> None:
        seen: list[Path] = []
        for rel in declared:
            cand = (root / rel) if not os.path.isabs(rel) else Path(rel)
            if cand.is_file():
                seen.append(cand)
            else:
                matches = sorted(root.glob(rel))
                if not matches and not os.path.isabs(rel):
                    matches = sorted(root.rglob(rel))
                seen.extend(m for m in matches if m.is_file())
        for installer in dict.fromkeys(seen):
            low = installer.name.lower()
            if "vc_redist" in low or "vcredist" in low or "windowsdesktop" in low:
                flags = ["/quiet", "/norestart"]
            elif "dxsetup" in low:
                flags = ["/silent"]
            elif "oalinst" in low or "openal" in low:
                flags = ["/silent"]
            else:
                flags = ["/S"]
            Log.info(f"provisioning runtime: {installer.name}")
            run_cmd([self.wine_bin, str(installer), *flags], env=env, timeout=900.0)
            self.serverwait(env)

    def _run_winetricks(self, env: Mapping[str, str], root: Path,
                        verbs: Sequence[str]) -> None:
        if not verbs:
            return
        bundled = root / "winetricks"
        argv0: list[str]
        if bundled.is_file():
            argv0 = ["bash", str(bundled)]
        elif have("winetricks"):
            argv0 = ["winetricks"]
        else:
            Log.warn(f"winetricks unavailable -- skipping verbs: {' '.join(verbs)}")
            return
        for verb in verbs:
            Log.info(f"winetricks: {verb}")
            r = run_cmd([*argv0, "-q", "--unattended", verb], env=env, timeout=1800.0)
            if not r.ok:
                Log.warn(f"winetricks {verb} exited {r.rc}: {r.message}")
            self.serverwait(env)

    def shutdown(self) -> None:
        run_cmd([self.server_bin, "-k"], env=self.base_env(), timeout=15.0)


def _slug(text: str) -> str:
    return hashlib.blake2b(text.encode(), digest_size=8).hexdigest()


# ==============================================================================
# SECTION 11 -- Environment matrix
# ==============================================================================
class EnvironmentBuilder:
    """Deterministic construction of the child environment.

    Ordering contract (later stages may read earlier ones):
      1. inherited environ            5. GPU / Vulkan matrix
      2. profile [env] table          6. shader caches
      3. session / Wayland            7. Wine / Proton
      4. audio / input                8. overlays + frame pacing
    """

    def __init__(self, prof: Profile, paths: GamePaths, *, dry_run: bool) -> None:
        self.p = prof
        self.paths = paths
        self.dry_run = dry_run
        self.env: dict[str, str] = dict(os.environ)
        self.gpu: Gpu | None = None
        self.prefix: WinePrefix | None = None
        self.notes: dict[str, str] = {}

    # -- helpers ----------------------------------------------------------
    def _set(self, key: str, value: Any) -> None:
        self.env[key] = str(value)

    def _drop(self, *keys: str) -> None:
        for k in keys:
            self.env.pop(k, None)

    def _mkcache(self, path: Path) -> Path:
        if not self.dry_run:
            with suppress(OSError):
                path.mkdir(parents=True, exist_ok=True)
        return path

    # -- stages -----------------------------------------------------------
    def stage_profile_env(self) -> None:
        custom = self.p.sect("env")
        for key, raw in custom.items():
            value = expand_str(str(raw), self.env)
            if key == "LD_PRELOAD":
                shim = Path(value).expanduser()
                if not shim.exists():
                    Log.warn(f"LD_PRELOAD shim missing, not injected: {shim}")
                    continue
                cur = self.env.get("LD_PRELOAD", "")
                self.env["LD_PRELOAD"] = f"{shim}:{cur}" if cur else str(shim)
                Log.debug(f"LD_PRELOAD += {shim.name}")
            elif key in ("PATH", "LD_LIBRARY_PATH") and value.startswith(":"):
                self.env[key] = self.env.get(key, "") + value
            else:
                self.env[key] = value

    def stage_session(self, *, under_gamescope: bool) -> None:
        gfx = self.p.sect("graphics")
        xwayland = bool(gfx.get("prefer_xwayland", False))

        # The environment we build is inherited by the WHOLE pipeline, gamescope
        # included. gamescope --backend wayland needs the *host* WAYLAND_DISPLAY
        # to attach to Hyprland; it then overrides WAYLAND_DISPLAY/DISPLAY for
        # its own child with its nested compositor + Xwayland. Clearing the host
        # socket here would leave gamescope with no backend at all.
        if under_gamescope:
            host_wl = os.environ.get("WAYLAND_DISPLAY")
            if host_wl:
                self._set("WAYLAND_DISPLAY", host_wl)
            elif str(self.p.get("graphics.gamescope.backend", "wayland")) == "wayland":
                Log.warn(
                    "gamescope wayland backend selected but WAYLAND_DISPLAY is "
                    "unset -- use backend='drm' or 'headless'"
                )
            self._set("SDL_VIDEODRIVER", "x11" if xwayland else "wayland")
            self._set("QT_QPA_PLATFORM", "xcb" if xwayland else "wayland")
            self._set("GDK_BACKEND", "x11" if xwayland else "wayland")
            # ENABLE_GAMESCOPE_WSI routes the client's VK_KHR_swapchain through
            # the gamescope WSI layer; it is mandatory for HDR passthrough and
            # for gamescope's own frame limiter to see real present timings.
            self._set("ENABLE_GAMESCOPE_WSI", "1")
            self._set("PROTON_ENABLE_WAYLAND", "0" if xwayland else "1")
            self.notes["session"] = "gamescope-nested"
            return

        if xwayland:
            self._set("SDL_VIDEODRIVER", "x11")
            self._set("GDK_BACKEND", "x11")
            self._set("QT_QPA_PLATFORM", "xcb")
            self._set("CLUTTER_BACKEND", "x11")
            # Always ensure DISPLAY points to a live XWayland socket (not stale :0 after Hyprland reload moved to :2)
            # Previous logic only set if not already set, which kept stale :0 from parent environ.
            socks = sorted(
                Path("/tmp/.X11-unix").glob("X[0-9]*"),
                key=lambda p: int(p.name[1:]) if p.name[1:].isdigit() else 999,
            ) if \
                Path("/tmp/.X11-unix").is_dir() else []
            # Filter to only sockets that actually exist and are live (check via displayfd or just existence)
            # Prefer the Hyprland XWayland (parent is Hyprland) — pick the one with lowest display number that exists
            # If current DISPLAY is stale (socket missing), override.
            current_disp = self.env.get("DISPLAY", "")
            # Check if current DISPLAY socket exists
            need_update = True
            if current_disp and current_disp.startswith(":"):
                disp_num = current_disp[1:].split(".")[0]
                if disp_num.isdigit():
                    sock_path = Path(f"/tmp/.X11-unix/X{disp_num}")
                    if sock_path.exists():
                        need_update = False
            if need_update:
                if socks:
                    # Prefer the one whose Xwayland parent is Hyprland (most recent)
                    # Sort by display number, pick lowest existing
                    self._set("DISPLAY", f":{socks[0].name[1:]}" if socks else ":0")
                else:
                    self._set("DISPLAY", ":0")
            self._drop("PROTON_ENABLE_WAYLAND")
            self.notes["session"] = "xwayland"
        else:
            self._set("SDL_VIDEODRIVER", "wayland,x11")
            self._set("CLUTTER_BACKEND", "wayland")
            self._set("GDK_BACKEND", "wayland,x11")
            self._set("QT_QPA_PLATFORM", "wayland;xcb")
            self._set("XDG_SESSION_TYPE", "wayland")
            self._set("PROTON_ENABLE_WAYLAND", "1")
            self._set("MOZ_ENABLE_WAYLAND", "1")
            wl = os.environ.get("WAYLAND_DISPLAY")
            if wl:
                self._set("WAYLAND_DISPLAY", wl)
            else:
                Log.warn("WAYLAND_DISPLAY is unset -- this engine targets a Wayland session")
            self.notes["session"] = "wayland"

        if bool(self.p.get("input.raw_input", True)):
            self._set("SDL_MOUSE_RELATIVE_MODE_WARP", "0")
            self._set("SDL_JOYSTICK_HIDAPI", "1")

    def stage_audio(self) -> None:
        audio = self.p.sect("audio")
        driver = str(audio.get("driver", "pipewire"))
        if driver != "pipewire":
            self.notes["audio"] = driver
            return
        quantum = int(audio.get("quantum", 1024) or 1024)
        rate = int(audio.get("rate", 48000) or 48000)
        # node.latency is a *request*; PipeWire clamps it to the graph's
        # min/max quantum. 1024/48000 = 21.3 ms is the sweet spot for games:
        # low enough to be imperceptible, high enough to avoid xruns under
        # heavy GPU load (256/48000 causes crackle on loaded systems).
        self._set("PIPEWIRE_LATENCY", f"{quantum}/{rate}")
        self._set("PIPEWIRE_RATE", f"1/{rate}")
        self._set("SDL_AUDIODRIVER", "pipewire")
        self._set("ALSOFT_DRIVERS", str(audio.get("openal_driver", "pipewire")))
        # PULSE_LATENCY_MSEC must stay coherent with the requested quantum,
        # otherwise pipewire-pulse and the native client fight over the graph.
        self._set("PULSE_LATENCY_MSEC", str(max(10, round(quantum * 1000 / rate))))
        self.notes["audio"] = f"pipewire {quantum}/{rate}"

    def stage_input(self) -> None:
        raw = str(self.p.get("input.sdl_gamecontrollerconfig", "") or "").strip()
        candidates: list[Path] = []
        if raw:
            p = Path(expand_str(raw, self.env)).expanduser()
            if p.is_file():
                candidates.append(p)
            else:
                self._set("SDL_GAMECONTROLLERCONFIG", raw)
                return
        candidates += [
            self.paths.game_dir / "gamecontrollerdb.txt",
            XDG_CONFIG_HOME / "gamecontrollerdb.txt",
            Path("/usr/share/gamecontrollerdb/gamecontrollerdb.txt"),
            Path("/usr/share/SDL2/gamecontrollerdb.txt"),
        ]
        for db in candidates:
            if db.is_file():
                # SDL3/SDL2 both accept a *file path* via SDL_GAMECONTROLLERCONFIG_FILE,
                # which avoids blowing a 250 KiB mapping database through execve's
                # ARG_MAX-constrained environment block.
                self._set("SDL_GAMECONTROLLERCONFIG_FILE", str(db))
                return

    def stage_gpu(self, *, under_gamescope: bool) -> None:
        gfx = self.p.sect("graphics")
        mode = str(gfx.get("gpu", "auto"))
        devs = [g for g in gpus() if not g.is_software]
        if not devs:
            Log.warn("no display-class PCI device found; leaving GPU env untouched")
            return

        gpu = select_gpu(mode)
        if gpu is None:
            Log.warn(f"graphics.gpu={mode!r} matched no device; falling back to auto")
            gpu = select_gpu("auto")
        if gpu is None:
            return
        self.gpu = gpu
        hybrid = len(devs) > 1
        Log.info(f"GPU: {gpu.describe()}")
        self.notes["gpu"] = gpu.describe()

        # --- Vulkan driver resolution -------------------------------------
        icds = gpu.icds()
        if icds:
            # VK_DRIVER_FILES is the current spec name; VK_ICD_FILENAMES is
            # explicitly deprecated by the loader and is NOT set here.
            self._set("VK_DRIVER_FILES", ":".join(str(i.manifest) for i in icds))
            self.notes["vulkan"] = ", ".join(sorted({i.library for i in icds}))
        else:
            Log.warn(
                f"no Vulkan ICD manifest matched vendor {gpu.vendor!r} -- "
                "leaving loader discovery untouched"
            )
        # Even with explicit driver files, the Mesa device-select layer can
        # still reorder physical devices on hybrid systems.
        if hybrid:
            self._set("MESA_VK_DEVICE_SELECT", gpu.vk_device_select)
            self._set("MESA_VK_DEVICE_SELECT_FORCE_DEFAULT_DEVICE", "1")
            if gpu.vulkan_name:
                self._set("DXVK_FILTER_DEVICE_NAME", gpu.vulkan_name)
                self._set("VKD3D_FILTER_DEVICE_NAME", gpu.vulkan_name)
            if gpu.device_uuid:
                self._set("DXVK_FILTER_DEVICE_UUID", gpu.device_uuid)

        # --- vendor specifics ---------------------------------------------
        self._drop(
            "__NV_PRIME_RENDER_OFFLOAD", "__GLX_VENDOR_LIBRARY_NAME",
            "__VK_LAYER_NV_optimus", "DRI_PRIME", "MESA_LOADER_DRIVER_OVERRIDE",
            "AMD_VULKAN_ICD",
        )
        match gpu.vendor:
            case "nvidia":
                if hybrid:
                    # PRIME render offload. DRI_PRIME is a *Mesa* knob and is
                    # deliberately NOT set: with the proprietary stack it makes
                    # Mesa pick a device for GLX/EGL that the NVIDIA GLVND
                    # vendor library then contradicts.
                    self._set("__NV_PRIME_RENDER_OFFLOAD", "1")
                    self._set("__GLX_VENDOR_LIBRARY_NAME", "nvidia")
                    self._set("__VK_LAYER_NV_optimus", "NVIDIA_only")
                    self._set("__NV_PRIME_RENDER_OFFLOAD_PROVIDER", "NVIDIA-G0")
                self._set("__GL_THREADED_OPTIMIZATIONS",
                          "1" if gfx.get("gl_threaded", True) else "0")
                self._set("__GL_MaxFramesAllowed", "1")   # lower input latency
                self._set("__GL_SHADER_DISK_CACHE", "1")
                self._set("__GL_SHADER_DISK_CACHE_SKIP_CLEANUP", "1")
                self._set("NVD_BACKEND", "direct")        # NVDEC without VDPAU
                if not str(gfx.get("vsync", "default")) == "on":
                    self._set("__GL_SYNC_TO_VBLANK", "0")
            case "amd":
                # Prefer RADV when AMDVLK is co-installed (AMDVLK regresses in
                # DXVK/VKD3D and has no ray-tracing parity on RDNA2/3).
                if any("amdvlk" in i.library for i in vulkan_icds()):
                    self._set("AMD_VULKAN_ICD", "RADV")
                self._set("MESA_LOADER_DRIVER_OVERRIDE", "radeonsi")
                if hybrid:
                    self._set("DRI_PRIME", gpu.dri_prime)
                self._set("mesa_glthread",
                          "true" if gfx.get("gl_threaded", True) else "false")
                # RADV_PERFTEST is a debug knob; only forward an explicit,
                # user-supplied value. Never invent one -- gpl/nggc/etc. are
                # already defaults on current Mesa and forcing them regresses.
                perftest = str(self.p.get("graphics.radv_perftest", "") or "")
                if perftest:
                    self._set("RADV_PERFTEST", perftest)
            case "intel":
                # There is no `xe` Gallium driver: `iris` is the GL driver for
                # BOTH the i915 and xe kernel drivers. Forcing
                # MESA_LOADER_DRIVER_OVERRIDE=xe yields a hard loader failure.
                self._set("MESA_LOADER_DRIVER_OVERRIDE", "iris")
                self._set("mesa_glthread",
                          "true" if gfx.get("gl_threaded", True) else "false")
                self._set("ANV_ENABLE_PIPELINE_CACHE", "1")
                if hybrid:
                    self._set("DRI_PRIME", gpu.dri_prime)
            case _:
                if hybrid:
                    self._set("DRI_PRIME", gpu.dri_prime)

        if bool(gfx.get("hdr", False)):
            self._set("DXVK_HDR", "1")
            self._set("ENABLE_HDR_WSI", "1")
            self._set("PROTON_ENABLE_HDR", "1")
            if not under_gamescope:
                Log.warn(
                    "graphics.hdr is on but gamescope is disabled -- HDR metadata "
                    "will only reach the display if the compositor implements "
                    "the colour-management-v1 protocol for this client"
                )

        match str(gfx.get("vsync", "default")):
            case "off":
                self._set("vblank_mode", "0")
                self._set("MESA_VK_WSI_PRESENT_MODE", "immediate")
            case "on":
                self._set("vblank_mode", "3")
                self._set("MESA_VK_WSI_PRESENT_MODE", "fifo")

    def stage_shader_cache(self) -> None:
        if not bool(self.p.get("graphics.shader_cache", True)):
            self._set("MESA_SHADER_CACHE_DISABLE", "true")
            self._set("__GL_SHADER_DISK_CACHE", "0")
            return
        size_gb = int(self.p.get("graphics.shader_cache_size_gb", 12) or 12)
        base = CACHE_DIR / "shaders" / self.p.pid
        self._set("MESA_SHADER_CACHE_DIR", str(self._mkcache(base / "mesa")))
        self._set("MESA_SHADER_CACHE_MAX_SIZE", f"{size_gb}G")
        self._set("__GL_SHADER_DISK_CACHE_PATH", str(self._mkcache(base / "nvidia")))
        self._set("__GL_SHADER_DISK_CACHE_SIZE", str(size_gb * (1 << 30)))
        self._set("DXVK_STATE_CACHE_PATH", str(self._mkcache(base / "dxvk")))
        self._set("VKD3D_SHADER_CACHE_PATH", str(self._mkcache(base / "vkd3d")))
        self._set("RADV_VIDEO_DECODE", "1")

    def stage_wine(self, *, dry_run: bool) -> None:
        rt = str(self.p.get("runtime.type", "native"))
        if rt not in ("wine", "proton", "umu"):
            return
        wcfg = self.p.sect("runtime", "wine")
        wine_bin = str(wcfg.get("wine_binary") or ("umu-run" if rt == "umu" else "wine"))
        prefix = WinePrefix(self.paths.prefix_dir, wine_bin, str(wcfg.get("arch", "win64")))
        self.prefix = prefix

        self._set("WINEPREFIX", str(prefix.path))
        self._set("WINEARCH", prefix.arch)

        debug = str(wcfg.get("debug", "-all"))
        self._set("WINEDEBUG", debug)
        if debug in ("-all", ""):
            self._set("DXVK_LOG_LEVEL", "none")
            self._set("VKD3D_DEBUG", "none")
            self._set("WINEDLLOVERRIDES_LOGLEVEL", "0")
        elif "+all" in debug or "debug" in debug:
            self._set("DXVK_LOG_LEVEL", "debug")
            self._set("VKD3D_DEBUG", "warn")
        else:
            self._set("DXVK_LOG_LEVEL", "warn")

        if bool(wcfg.get("large_address_aware", True)):
            self._set("WINE_LARGE_ADDRESS_AWARE", "1")

        # ---- synchronisation primitive selection -------------------------
        mode = enum_or(SyncMode, wcfg.get("sync_mode", "auto"), SyncMode.AUTO)
        if mode is SyncMode.AUTO:
            mode = SyncMode.NTSYNC if ntsync_available() else SyncMode.FSYNC
        self._drop("WINENTSYNC", "WINEFSYNC", "WINEESYNC",
                   "PROTON_USE_NTSYNC", "PROTON_NO_FSYNC", "PROTON_NO_ESYNC")
        match mode:
            case SyncMode.NTSYNC:
                if not ntsync_available():
                    Log.warn(
                        "sync_mode=ntsync but /dev/ntsync is absent -- load it with "
                        "`sudo modprobe ntsync` (kernel >= 6.14) or use fsync"
                    )
                # Upstream Wine >= 10.16 auto-enables ntsync; the variable is the
                # explicit opt-in for builds where it is gated, and Proton needs
                # PROTON_USE_NTSYNC for versions < 11.
                self._set("WINENTSYNC", "1")
                self._set("PROTON_USE_NTSYNC", "1")
                self._set("PROTON_NO_FSYNC", "1")
                self._set("PROTON_NO_ESYNC", "1")
            case SyncMode.FSYNC:
                self._set("WINEFSYNC", "1")
                self._set("PROTON_NO_FSYNC", "0")
                self._set("PROTON_NO_ESYNC", "1")
            case SyncMode.ESYNC:
                self._set("WINEESYNC", "1")
                self._set("PROTON_NO_ESYNC", "0")
                self._set("PROTON_NO_FSYNC", "1")
                soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
                if soft < 500_000:
                    Log.warn(f"esync with RLIMIT_NOFILE={soft} will hit `eventfd: "
                             "Too many open files` in heavy titles")
            case SyncMode.SERVER:
                self._set("PROTON_NO_FSYNC", "1")
                self._set("PROTON_NO_ESYNC", "1")
        self.notes["sync"] = str(mode)

        # ---- DLL overrides (merged, never clobbered) ---------------------
        overrides: dict[str, str] = {}
        inherited = os.environ.get("WINEDLLOVERRIDES", "")
        for chunk in inherited.split(";"):
            if "=" in chunk:
                k, _, v = chunk.partition("=")
                overrides[k.strip()] = v.strip()
        if bool(wcfg.get("disable_menubuilder", True)):
            overrides["winemenubuilder.exe"] = ""
        declared = wcfg.get("dll_overrides")
        if isinstance(declared, Mapping):
            for k, v in declared.items():
                k_str, v_str = str(k), str(v)
                if k_str in ("dxgi", "d3d9", "d3d10core", "d3d11", "d3d12", "d3d12core") and v_str == "n":
                    v_str = "n,b"
                overrides[k_str] = v_str
        if bool(wcfg.get("dxvk_nvapi", False)):
            overrides.update({"nvapi": "n", "nvapi64": "n", "nvofapi64": "n"})
        if overrides:
            self._set("WINEDLLOVERRIDES",
                      ";".join(f"{k}={v}" for k, v in sorted(overrides.items())))

        # ---- DXVK / VKD3D / NVAPI ---------------------------------------
        if wcfg.get("dxvk", True) is False:
            self._set("PROTON_USE_WINED3D", "1")
        else:
            self._drop("PROTON_USE_WINED3D")
        if bool(wcfg.get("vkd3d", True)):
            cfg_tokens = [t for t in str(wcfg.get("vkd3d_config", "")).split(",") if t]
            if bool(self.p.get("graphics.raytracing", False)):
                cfg_tokens += ["dxr", "dxr11"]
            if cfg_tokens:
                self._set("VKD3D_CONFIG", ",".join(dict.fromkeys(cfg_tokens)))
        if bool(wcfg.get("dxvk_nvapi", False)):
            nvidia_present = any(g.is_nvidia for g in gpus())
            if not nvidia_present:
                Log.warn("dxvk_nvapi enabled without an NVIDIA GPU -- ignoring")
            else:
                self._set("DXVK_ENABLE_NVAPI", "1")
                self._set("PROTON_ENABLE_NVAPI", "1")
                self._drop("DXVK_NVAPI_DRS_NGX_DLSS_SR_OVERRIDE")
                self._set("PROTON_HIDE_NVIDIA_GPU", "0")
        if bool(wcfg.get("hide_wine", False)):
            self._set("WINE_HIDE_VERSION", "1")

        # ---- UMU / Proton ------------------------------------------------
        if rt in ("proton", "umu"):
            umu = self.p.sect("runtime", "umu")
            self._set("GAMEID", str(umu.get("game_id") or f"umu-{self.p.pid}"))
            self._set("STORE", str(umu.get("store") or "none"))
            self._set("PROTON_VERB", str(umu.get("verb") or "waitforexitandrun"))
            proton = str(umu.get("proton") or "").strip()
            if proton:
                self._set("PROTONPATH", proton)
            if not bool(umu.get("protonfixes", True)):
                self._set("PROTONFIXES_DISABLE", "1")
            if Log.level >= Verbosity.VERBOSE:
                self._set("UMU_LOG", "debug")

        # ---- prefix materialisation --------------------------------------
        redists = [str(x) for x in (wcfg.get("redistributables") or [])]
        verbs = [str(x) for x in (wcfg.get("winetricks") or [])]
        prefix.provision(
            root_dir=self.paths.root,
            redistributables=redists,
            winetricks=verbs,
            want_dxvk=bool(wcfg.get("dxvk", True)),
            want_vkd3d=bool(wcfg.get("vkd3d", True)),
            want_nvapi=bool(wcfg.get("dxvk_nvapi", False)),
            want_dlss=bool(wcfg.get("dxvk_nvapi", False)) and any(g.is_nvidia for g in gpus()),
            force=bool(self.p.get("runtime.wine.reprovision", False)),
            dry_run=dry_run,
        )

    def stage_overlays(self, *, under_gamescope: bool) -> None:
        perf = self.p.sect("performance")
        fps = int(perf.get("fps_limit", 0) or 0)
        mangohud = bool(perf.get("mangohud", False))
        mangoapp = under_gamescope and bool(
            self.p.get("graphics.gamescope.mangoapp", True)
        ) and have("mangoapp")

        if mangohud and not mangoapp:
            self._set("MANGOHUD", "1")
            bits = []
            preset = str(perf.get("mangohud_preset", "") or "")
            if preset:
                self._set("MANGOHUD_PRESET", preset)
            if fps > 0:
                bits.append(f"fps_limit={fps}")
            bits.append("vsync=0")
            self._set("MANGOHUD_CONFIG", ",".join(bits))
        else:
            # Setting MANGOHUD=1 *and* passing --mangoapp renders two overlays,
            # one of which reports the compositor's frame times rather than the
            # game's. mangoapp is authoritative under gamescope.
            self._drop("MANGOHUD", "MANGOHUD_CONFIG")

        if fps > 0 and not under_gamescope:
            # DXVK/VKD3D frame limiters are frame-pacing aware; the MangoHud
            # limiter is a busy-wait and is only a fallback.
            self._set("DXVK_FRAME_RATE", str(fps))
            self._set("VKD3D_FRAME_RATE", str(fps))
        else:
            self._drop("DXVK_FRAME_RATE", "VKD3D_FRAME_RATE")

        self._set("SteamGameId", self.env.get("GAMEID", self.p.pid))
        self._set("SteamAppId", "0")
        self._set("MASTER_RUNNER_PROFILE", self.p.pid)
        self._set("MASTER_RUNNER_VERSION", ENGINE_VERSION)

    def build(self, *, under_gamescope: bool) -> dict[str, str]:
        self.stage_profile_env()
        self.stage_session(under_gamescope=under_gamescope)
        self.stage_audio()
        self.stage_input()
        self.stage_gpu(under_gamescope=under_gamescope)
        self.stage_shader_cache()
        self.stage_wine(dry_run=self.dry_run)
        self.stage_overlays(under_gamescope=under_gamescope)
        # Strip empty values: an empty DISPLAY is *not* the same as an unset one
        # for SDL and Wine.
        return {k: v for k, v in self.env.items() if v != ""}

# ==============================================================================
# SECTION 12 -- Command pipeline construction
#
# Layer order, outermost first:
#   systemd-run --scope        (cgroup v2 delegation, resource limits)
#     bwrap                    (filesystem/network isolation)
#       gamemoderun            (governor, GPU perf level, nice, ioprio)
#         gamescope            (micro-compositor; owns scaling + frame pacing)
#           mangohud           (only when gamescope is NOT used)
#             taskset          (CPU affinity)
#               wine/umu/native
# ==============================================================================
def _int0(v: Any) -> int:
    with suppress(TypeError, ValueError):
        return int(v)
    return 0


def parse_affinity(spec: str) -> list[int]:
    """Parse `0-7,16-23`, `pcores` or `smt-off` into a CPU list."""
    spec = spec.strip().lower()
    if not spec:
        return []
    online = sorted(os.sched_getaffinity(0))
    if spec == "pcores":
        # Intel hybrid: P-cores expose the highest cpuinfo_max_freq. Reading
        # cpufreq is the only vendor-neutral way (`core_type` is Intel-only).
        freqs: dict[int, int] = {}
        for cpu in online:
            f = read_int(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/cpuinfo_max_freq")
            if f:
                freqs[cpu] = f
        if not freqs:
            return online
        top = max(freqs.values())
        return [c for c in online if freqs.get(c, 0) >= top * 0.95] or online
    if spec == "smt-off":
        # Keep only the first thread of each core (thread_siblings_list[0]).
        keep: list[int] = []
        seen: set[str] = set()
        for cpu in online:
            sib = read_first_line(
                f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list"
            )
            key = sib or str(cpu)
            if key not in seen:
                seen.add(key)
                keep.append(cpu)
        return keep or online
    out: list[int] = []
    for chunk in spec.replace(" ", "").split(","):
        if "-" in chunk:
            a, _, b = chunk.partition("-")
            with suppress(ValueError):
                out.extend(range(int(a), int(b) + 1))
        else:
            with suppress(ValueError):
                out.append(int(chunk))
    return [c for c in dict.fromkeys(out) if c in set(online)]


class PipelineBuilder:
    def __init__(self, prof: Profile, paths: GamePaths, extra_args: Sequence[str]) -> None:
        self.p = prof
        self.paths = paths
        self.extra_args = list(extra_args)

    # -- gamescope --------------------------------------------------------
    @property
    def gamescope_enabled(self) -> bool:
        want = bool(self.p.get("graphics.gamescope.enabled", False))
        if want and not have("gamescope"):
            Log.warn("gamescope requested but not installed -- layer skipped")
            return False
        return want

    def gamescope_argv(self) -> list[str]:
        gs = self.p.sect("graphics", "gamescope")
        perf = self.p.sect("performance")
        out = active_output()
        backend = str(gs.get("backend", "wayland"))

        outw = _int0(gs.get("output_width")) or out.width or 1920
        outh = _int0(gs.get("output_height")) or out.height or 1080
        inw = _int0(gs.get("width")) or outw
        inh = _int0(gs.get("height")) or outh
        # Internal render size must never exceed the output; gamescope accepts
        # it but then composites a downscale nobody asked for.
        inw, inh = min(inw, outw), min(inh, outh)

        fps = _int0(perf.get("fps_limit"))
        rate = _int0(gs.get("refresh_rate")) or int(round(out.refresh_hz)) or 60
        if fps > rate:
            Log.warn(f"fps_limit={fps} exceeds output refresh {rate} Hz")

        argv: list[str] = ["gamescope", "--backend", backend]
        if backend == "wayland":
            # Without --expose-wayland a Wayland-native client (Proton's Wayland
            # driver, SDL3, Godot 4) cannot bind xdg-shell inside gamescope and
            # silently falls back to Xwayland.
            argv.append("--expose-wayland")
        argv += ["-W", str(outw), "-H", str(outh), "-w", str(inw), "-h", str(inh)]
        argv += ["-r", str(rate)]

        unfocused = _int0(gs.get("unfocused_refresh"))
        if unfocused:
            argv += ["-o", str(unfocused)]
        if fps > 0:
            argv += ["--framerate-limit", str(fps)]

        match str(gs.get("mode", "borderless")):
            case "fullscreen":
                argv.append("-f")
            case "borderless":
                argv.append("-b")
            case _:
                pass

        filt = str(gs.get("filter", "") or "")
        if filt:
            argv += ["-F", filt]
            if filt in ("fsr", "nis"):
                raw_sharp = gs.get("fsr_sharpness")
                sharp = max(0, min(20, _int0(raw_sharp))) if raw_sharp is not None else 5
                argv += ["--sharpness", str(sharp)]
        scaler = str(gs.get("scaler", "") or "")
        if scaler:
            argv += ["-S", scaler]

        if bool(gs.get("adaptive_sync", False)):
            argv.append("--adaptive-sync")
        if bool(gs.get("immediate_flips", False)):
            if backend == "drm":
                argv.append("--immediate-flips")
            else:
                Log.debug("--immediate-flips is DRM-backend only; not emitted")
        if bool(gs.get("force_grab_cursor", False)):
            argv.append("--force-grab-cursor")
        if bool(gs.get("grab_keyboard", False)):
            argv.append("-g")
        if bool(gs.get("realtime", True)):
            argv.append("--rt")
        if bool(gs.get("hdr", False)) or bool(self.p.get("graphics.hdr", False)):
            argv.append("--hdr-enabled")
            if bool(gs.get("hdr_itm", False)):
                argv.append("--hdr-itm-enable")
        xw = _int0(gs.get("xwayland_count"))
        if xw > 0:
            argv += ["--xwayland-count", str(xw)]

        # Pin gamescope's own compositing device on hybrid systems, otherwise it
        # may composite on the iGPU while the game renders on the dGPU, adding a
        # full PCIe copy per frame.
        gpu = select_gpu(str(self.p.get("graphics.gpu", "auto")))
        if gpu and len([g for g in gpus() if not g.is_software]) > 1:
            argv += ["--prefer-vk-device", gpu.vk_device_select]

        if bool(self.p.get("performance.mangohud", False)) and \
                bool(gs.get("mangoapp", True)) and have("mangoapp"):
            argv.append("--mangoapp")

        argv += [str(x) for x in (gs.get("extra_args") or [])]
        argv.append("--")
        return argv

    # -- sandbox ----------------------------------------------------------
    def bwrap_argv(self, workdir: Path) -> list[str]:
        sb = self.p.sect("sandbox")
        uid = os.getuid()
        argv: list[str] = [
            "bwrap",
            "--die-with-parent",     # no orphaned sandbox if the runner dies
            "--new-session",         # blocks TIOCSTI terminal injection
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup-try",
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/etc", "/etc",
            # Arch's /bin /lib /lib64 /sbin are symlinks into /usr; recreating
            # them as symlinks (not binds) is the only correct representation.
            "--symlink", "usr/lib", "/lib",
            "--symlink", "usr/lib", "/lib64",
            "--symlink", "usr/bin", "/bin",
            "--symlink", "usr/bin", "/sbin",
            "--bind", str(self.paths.game_dir), str(self.paths.game_dir),
        ]
        if self.paths.uses_dwarfs:
            argv += ["--bind", str(self.paths.overlay_dir), str(self.paths.overlay_dir)]
        if os.path.exists("/tmp/.X11-unix"):
            argv += ["--ro-bind-try", "/tmp/.X11-unix", "/tmp/.X11-unix"]
        if bool(sb.get("bind_gpu", True)):
            argv += ["--dev-bind-try", "/dev/dri", "/dev/dri"]
            for node in sorted(Path("/dev").glob("nvidia*")):
                argv += ["--dev-bind-try", str(node), str(node)]
            if os.path.exists("/dev/ntsync"):
                argv += ["--dev-bind-try", "/dev/ntsync", "/dev/ntsync"]
            argv += ["--ro-bind-try", "/sys/dev/char", "/sys/dev/char"]
            argv += ["--ro-bind-try", "/sys/bus/pci/devices", "/sys/bus/pci/devices"]
        if bool(sb.get("bind_audio", True)):
            for sock in (f"/run/user/{uid}/pipewire-0", f"/run/user/{uid}/pulse"):
                if os.path.exists(sock):
                    argv += ["--ro-bind-try", sock, sock]
        if bool(sb.get("bind_wayland", True)):
            wl = os.environ.get("WAYLAND_DISPLAY", "wayland-1")
            sock = wl if os.path.isabs(wl) else f"/run/user/{uid}/{wl}"
            argv += ["--ro-bind-try", sock, sock]
            argv += ["--ro-bind-try", f"/run/user/{uid}/bus", f"/run/user/{uid}/bus"]
        if not bool(sb.get("bind_network", False)):
            argv.append("--unshare-net")
        if bool(sb.get("isolate_home", True)):
            home = Path(
                str(sb.get("sandbox_home") or (XDG_DATA_HOME / "game-sandboxes" / self.p.pid))
            ).expanduser()
            home.mkdir(parents=True, exist_ok=True)
            argv += ["--bind", str(home), str(HOME)]
        argv += ["--chdir", str(workdir), "--"]
        return argv

    # -- systemd transient scope ------------------------------------------
    def scope_argv(self) -> list[str]:
        if not bool(self.p.get("runner.use_systemd_scope", True)):
            return []
        if not have("systemd-run") or not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            return []
        perf = self.p.sect("performance")
        unit = f"{ENGINE_SLUG}-{self.p.pid}-{os.getpid()}"
        argv = [
            "systemd-run", "--user", "--scope", "--quiet", "--collect",
            f"--unit={unit}",
            f"--slice={self.p.get('runner.scope_slice', 'app-games.slice')}",
            "-p", "Delegate=yes",
            # Games are the foreground workload; protect them from the
            # userspace OOM killer's default heuristics.
            "-p", "ManagedOOMPreference=avoid",
            "-p", "OOMPolicy=continue",
        ]
        if w := _int0(perf.get("scope_cpu_weight")):
            argv += ["-p", f"CPUWeight={max(1, min(10000, w))}"]
        if w := _int0(perf.get("scope_io_weight")):
            argv += ["-p", f"IOWeight={max(1, min(10000, w))}"]
        if mh := str(perf.get("scope_memory_high", "") or ""):
            argv += ["-p", f"MemoryHigh={mh}"]
        return argv

    # -- executable resolution ---------------------------------------------
    def resolve_executable(self) -> Path:
        root = self.paths.root
        rel = self.paths.executable
        if not rel:
            raise ConfigError(f"[{self.p.pid}] paths.executable is not set")
        direct = (root / rel) if not os.path.isabs(rel) else Path(rel)
        if direct.is_file():
            return direct
        # Bounded discovery: repacks nest the binary one or two levels deeper.
        name = os.path.basename(rel)
        for depth in (1, 2, 3):
            pattern = "/".join(["*"] * depth) + "/" + name
            for cand in sorted(root.glob(pattern)):
                if cand.is_file():
                    Log.warn(f"executable not at {rel}; using {cand.relative_to(root)}")
                    return cand
        raise ConfigError(
            f"[{self.p.pid}] executable {rel!r} not found under {root}"
            + (" (is the game mounted?)" if self.paths.uses_dwarfs else "")
        )

    def build(self, *, under_gamescope: bool) -> tuple[list[str], Path]:
        exe = self.resolve_executable()
        rt = str(self.p.get("runtime.type", "native"))
        args = [str(a) for a in (self.p.get("paths.arguments") or [])] + self.extra_args

        if self.paths.working_dir:
            workdir = (self.paths.root / self.paths.working_dir).resolve()
        else:
            workdir = exe.parent
        if not workdir.is_dir():
            workdir = self.paths.root

        inner: list[str]
        match rt:
            case "native":
                with suppress(OSError):
                    mode = exe.stat().st_mode
                    if not mode & 0o111:
                        exe.chmod(mode | 0o111)
                inner = [str(exe), *args]
            case "script":
                inner = ["bash", str(exe), *args]
            case "umu" | "proton":
                launcher = str(self.p.get("runtime.wine.wine_binary") or "umu-run")
                inner = [launcher, str(exe), *args]
            case "wine":
                wine = str(self.p.get("runtime.wine.wine_binary") or "wine")
                base = os.path.basename(wine).lower()
                if "proton" in base and "umu" not in base:
                    inner = [wine, "run", str(exe), *args]
                else:
                    inner = [wine, str(exe), *args]
            case other:
                raise ConfigError(f"[{self.p.pid}] unknown runtime.type {other!r}")

        pipeline = inner

        affinity = parse_affinity(str(self.p.get("performance.cpu_affinity", "") or ""))
        if affinity and have("taskset"):
            pipeline = ["taskset", "-c", _cpulist(affinity), *pipeline]

        if under_gamescope:
            pipeline = [*self.gamescope_argv(), *pipeline]
        elif bool(self.p.get("performance.mangohud", False)) and have("mangohud"):
            pipeline = ["mangohud", "--dlsym", *pipeline]

        if bool(self.p.get("performance.gamemode", True)) and have("gamemoderun"):
            pipeline = ["gamemoderun", *pipeline]

        if bool(self.p.get("sandbox.enabled", False)):
            if have("bwrap"):
                pipeline = [*self.bwrap_argv(workdir), *pipeline]
            else:
                Log.warn("sandbox.enabled but bubblewrap is not installed")

        scope = self.scope_argv()
        if scope:
            pipeline = [*scope, *pipeline]

        return pipeline, workdir


def _cpulist(cpus: Sequence[int]) -> str:
    """Compact 0,1,2,5 -> 0-2,5 (taskset accepts both; short is nicer in logs)."""
    if not cpus:
        return ""
    runs: list[str] = []
    start = prev = cpus[0]
    for c in cpus[1:]:
        if c == prev + 1:
            prev = c
            continue
        runs.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = c
    runs.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(runs)


# ==============================================================================
# SECTION 13 -- Process supervision
#
# The supervisor waits on a pidfd and a signal self-pipe through one selector.
# This is race-free: a signal arriving between `poll()` and `wait()` is captured
# by the wakeup fd instead of being lost, and child exit is level-triggered on
# the pidfd rather than polled.
#
# Teardown escalates: SIGTERM to the cgroup/process-group -> grace ->
# cgroup.kill (atomic, freezer-backed, cannot be outrun by fork bombs).
# ==============================================================================
_FATAL_SIGNALS: Final = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)


@dataclass(slots=True)
class Supervisor:
    proc: subprocess.Popen[bytes]
    grace: float = 8.0
    interrupted: int = 0
    _own_cgroup: Path | None = None
    _child_cgroup: Path | None = None
    _cgroup_probed: bool = False

    def __post_init__(self) -> None:
        self._own_cgroup = cgroup_of(os.getpid())

    @property
    def cgroup(self) -> Path | None:
        """The child's *private* cgroup, or None when it shares ours.

        SAFETY INVARIANT: if `systemd-run --scope` was unavailable the child
        lives in the launcher's own cgroup. Writing to that `cgroup.kill` would
        SIGKILL this process, the terminal's shell and every other member of the
        scope. The delegated-scope check below is therefore load-bearing, not a
        micro-optimisation.

        Resolution is lazy because `systemd-run` re-parents itself into the new
        scope a few milliseconds after exec; probing at spawn time races.
        """
        if not self._cgroup_probed:
            cg = cgroup_of(self.proc.pid)
            if cg is not None and cg != self._own_cgroup and (cg / "cgroup.kill").exists():
                self._child_cgroup = cg
                Log.debug(f"child confined to delegated cgroup {cg}")
            else:
                self._child_cgroup = None
                Log.debug("child shares the launcher cgroup; using process groups")
            if self.proc.poll() is None:
                self._cgroup_probed = True
        return self._child_cgroup

    def wait(self) -> int:
        """Block until the child exits or a fatal signal is received."""
        try:
            pidfd = os.pidfd_open(self.proc.pid, 0)
        except (OSError, AttributeError) as exc:
            Log.debug(f"pidfd_open unavailable ({exc}); falling back to waitpid")
            return self._wait_plain()

        rpipe, wpipe = os.pipe()
        os.set_blocking(rpipe, False)
        os.set_blocking(wpipe, False)
        prev_handlers: dict[int, Any] = {}
        prev_wakeup = -1
        sel = selectors.DefaultSelector()
        try:
            prev_wakeup = signal.set_wakeup_fd(wpipe, warn_on_full_buffer=False)
            for sig in _FATAL_SIGNALS:
                prev_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, _noop_handler)
            sel.register(pidfd, selectors.EVENT_READ, "child")
            sel.register(rpipe, selectors.EVENT_READ, "signal")

            while True:
                for key, _ in sel.select(timeout=None):
                    if key.data == "child":
                        return self._reap()
                    payload = os.read(rpipe, 512)
                    for raw in payload:
                        self._on_signal(raw)
                    if self.interrupted:
                        return self._teardown()
        finally:
            sel.close()
            for sig, handler in prev_handlers.items():
                with suppress(ValueError, OSError):
                    signal.signal(sig, handler)
            with suppress(ValueError, OSError):
                signal.set_wakeup_fd(prev_wakeup)
            for fd in (pidfd, rpipe, wpipe):
                with suppress(OSError):
                    os.close(fd)

    def _on_signal(self, signum: int) -> None:
        try:
            name = signal.Signals(signum).name
        except ValueError:
            name = str(signum)
        self.interrupted = signum
        Log.warn(f"caught {name} -- shutting the session down")

    def _wait_plain(self) -> int:
        try:
            return self.proc.wait()
        except KeyboardInterrupt:
            self.interrupted = int(signal.SIGINT)
            return self._teardown()

    def _reap(self) -> int:
        with suppress(Exception):
            return self.proc.wait(timeout=5)
        return self.proc.returncode if self.proc.returncode is not None else 0

    def _teardown(self) -> int:
        """SIGTERM the whole tree, then cgroup.kill anything still standing."""
        self.signal_tree(signal.SIGTERM)
        deadline = time.monotonic() + self.grace
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                return self._reap()
            time.sleep(0.05)
        Log.warn(f"grace period ({self.grace:g}s) expired -- escalating to SIGKILL")
        self._hard_kill()
        with suppress(Exception):
            self.proc.wait(timeout=5)
        return 128 + (self.interrupted or int(signal.SIGTERM))

    def _hard_kill(self) -> None:
        cg = self.cgroup
        if cg is not None and cgroup_kill(cg):
            return
        self.signal_tree(signal.SIGKILL)

    def signal_tree(self, sig: int) -> None:
        cg = self.cgroup
        if cg is not None:
            me = os.getpid()
            pids = [p for p in cgroup_pids(cg) if p != me]
            if pids:
                for pid in pids:
                    with suppress(ProcessLookupError, PermissionError, OSError):
                        os.kill(pid, sig)
                        if sig in (signal.SIGTERM, signal.SIGKILL, signal.SIGINT):
                            with suppress(ProcessLookupError, PermissionError, OSError):
                                os.kill(pid, signal.SIGCONT)
                return
        try:
            pgid = os.getpgid(self.proc.pid)
        except (ProcessLookupError, PermissionError, OSError):
            pgid = -1
        if pgid > 0 and pgid != os.getpgid(0):
            with suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(pgid, sig)
                if sig in (signal.SIGTERM, signal.SIGKILL, signal.SIGINT):
                    with suppress(ProcessLookupError, PermissionError, OSError):
                        os.killpg(pgid, signal.SIGCONT)
                return
        with suppress(ProcessLookupError, OSError):
            self.proc.send_signal(sig)
            if sig in (signal.SIGTERM, signal.SIGKILL, signal.SIGINT):
                with suppress(ProcessLookupError, OSError):
                    self.proc.send_signal(signal.SIGCONT)

    def kill_now(self) -> None:
        if self.proc.poll() is not None:
            return
        self._hard_kill()


def _noop_handler(signum: int, frame: Any) -> None:  # noqa: ARG001
    """Deliberately empty: the wakeup fd carries the information."""


# ==============================================================================
# SECTION 14 -- Session runner
#
# All teardown is expressed as an ExitStack. There is no module-level mutable
# "active context" and no atexit hook, which is what made the previous engine
# skip cleanup for every game launched after the first one in the TUI (the
# `cleaned` latch was global and never reset).
# ==============================================================================
@dataclass(slots=True)
class RunOptions:
    extra_args: list[str] = field(default_factory=list)
    dry_run: bool = False
    reprovision: bool = False
    no_mount: bool = False
    keep_mounted: bool = False
    json_out: bool = False


class GameSession:
    def __init__(self, mgr: ProfileManager, prof: Profile, opts: RunOptions) -> None:
        self.mgr = mgr
        self.p = prof
        self.opts = opts
        self.paths = resolve_paths(prof)
        self.stack = ExitStack()

    # -- hooks ------------------------------------------------------------
    def hooks(self, phase: str, env: Mapping[str, str] | None = None) -> None:
        cmds = self.p.get(f"hooks.{phase}") or []
        if not cmds:
            return
        Log.debug(f"hook[{phase}] x{len(cmds)}")
        for raw in cmds:
            cmd = str(raw)
            if self.opts.dry_run:
                Log.info(f"[dry-run] hook[{phase}]: {cmd}")
                continue
            cp = subprocess.run(
                ["bash", "-o", "pipefail", "-c", cmd],
                cwd=str(self.paths.game_dir if self.paths.game_dir.is_dir() else HOME),
                env=dict(env) if env else None,
                capture_output=Log.level < Verbosity.VERBOSE,
                text=True,
                check=False,
                timeout=600,
            )
            if cp.returncode != 0:
                Log.warn(f"hook[{phase}] `{cmd}` exited {cp.returncode}")

    # -- main -------------------------------------------------------------
    def run(self) -> int:
        prof, opts = self.p, self.opts
        Log.info(f"launching {prof.name} [{prof.pid}] runtime={prof.runtime}")

        if opts.reprovision:
            prof.cfg.setdefault("runtime", {}).setdefault("wine", {})["reprovision"] = True

        with self.stack:
            self.hooks("pre_mount")
            if not opts.no_mount and bool(prof.get("runner.auto_mount", True)):
                if not MountEngine.mount(prof, self.paths, dry_run=opts.dry_run):
                    Log.error("mount stage failed -- aborting")
                    return 74  # EX_IOERR
                if bool(prof.get("runner.auto_unmount_on_exit", True)) and \
                        not opts.keep_mounted and not opts.dry_run:
                    self.stack.callback(
                        lambda: MountEngine.unmount(prof, self.paths, quiet=True)
                    )
            self.hooks("post_mount")

            pipe = PipelineBuilder(prof, self.paths, opts.extra_args)
            under_gs = pipe.gamescope_enabled

            envb = EnvironmentBuilder(prof, self.paths, dry_run=opts.dry_run)
            env = envb.build(under_gamescope=under_gs)
            if envb.prefix is not None and not opts.dry_run:
                self.stack.callback(envb.prefix.shutdown)

            try:
                argv, workdir = pipe.build(under_gamescope=under_gs)
            except ConfigError as exc:
                Log.error(str(exc))
                return 78  # EX_CONFIG

            self._describe(argv, workdir, envb)
            if opts.dry_run:
                Log.ok("dry-run complete; nothing was executed")
                return 0

            self.hooks("pre_launch", env)

            if bool(prof.get("runner.inhibit_idle", True)):
                inhibitor = IdleInhibitor()
                inhibitor.acquire(
                    ENGINE_NAME, f"Playing {prof.name}",
                    block_sleep=bool(prof.get("runner.inhibit_sleep", True)),
                )
                self.stack.callback(inhibitor.release)

            if bool(prof.get("runner.notifications", True)):
                notify("Launching", prof.name, icon=prof.icon)

            rc, elapsed = self._spawn(argv, workdir, env)

            mins, secs = divmod(int(elapsed), 60)
            hours, mins = divmod(mins, 60)
            span = (f"{hours}h {mins}m" if hours else
                    f"{mins}m {secs}s" if mins else f"{secs}s")
            (Log.ok if rc == 0 else Log.warn)(
                f"{prof.name} exited rc={rc} after {span}"
            )
            if bool(prof.get("runner.notifications", True)):
                notify(
                    "Session ended",
                    f"{prof.name} - {span} (exit {rc})",
                    urgency="low" if rc == 0 else "normal",
                    icon=prof.icon,
                )
            self._record_session(rc, elapsed)
            self.hooks("post_launch", env)

        self.hooks("post_unmount")
        return rc

    def _spawn(self, argv: Sequence[str], workdir: Path,
               env: Mapping[str, str]) -> tuple[int, float]:
        started = time.monotonic()
        try:
            proc = subprocess.Popen(
                list(argv),
                cwd=str(workdir),
                env=dict(env),
                stdin=subprocess.DEVNULL,
                # process_group=0 keeps the child in our *session* (so the
                # terminal keeps working) but in its own process group, which
                # makes killpg() precise. start_new_session would additionally
                # detach the controlling terminal and break Ctrl-C reporting.
                process_group=0,
                close_fds=True,
            )
        except FileNotFoundError:
            Log.error(f"executable not found: {argv[0]}")
            return 127, 0.0
        except PermissionError:
            Log.error(f"permission denied: {argv[0]}")
            return 126, 0.0
        except OSError as exc:
            Log.error(f"spawn failed: {exc}")
            return 71, 0.0

        sup = Supervisor(proc, grace=float(self.p.get("runner.kill_grace_s", 8.0) or 8.0))
        self.stack.callback(sup.kill_now)
        rc = sup.wait()
        return rc, time.monotonic() - started

    def _describe(self, argv: Sequence[str], workdir: Path,
                  envb: EnvironmentBuilder) -> None:
        if self.opts.json_out:
            print(json.dumps({
                "profile": self.p.pid,
                "name": self.p.name,
                "runtime": self.p.runtime,
                "argv": list(argv),
                "cwd": str(workdir),
                "gpu": envb.gpu.describe() if envb.gpu else None,
                "notes": envb.notes,
                "env_delta": {
                    k: v for k, v in envb.env.items()
                    if os.environ.get(k) != v
                },
            }, indent=2))
            return
        rows = [
            ("profile", f"{self.p.name} ({self.p.pid})"),
            ("runtime", self.p.runtime),
            ("root", str(self.paths.root)),
            ("cwd", str(workdir)),
            *sorted(envb.notes.items()),
            ("pipeline", shlex.join(argv)),
        ]
        console = Log.console()
        if console is not None and Log.level >= Verbosity.NORMAL:
            from rich import box as _box
            from rich.table import Table as _Table

            t = _Table(title=f"launch plan :: {self.p.name}", box=_box.ROUNDED,
                       show_header=True, header_style="bold cyan")
            t.add_column("field", style="cyan", no_wrap=True)
            t.add_column("value", style="white", overflow="fold")
            for k, v in rows:
                t.add_row(k, str(v))
            console.print(t)
        else:
            for k, v in rows:
                Log.info(f"{k:>10}: {v}")

    def _record_session(self, rc: int, elapsed: float) -> None:
        with suppress(OSError):
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            log = STATE_DIR / "sessions.jsonl"
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "t": time.time(),
                    "profile": self.p.pid,
                    "rc": rc,
                    "seconds": round(elapsed, 1),
                }) + "\n")
            if log.stat().st_size > 1 << 20:
                tail = log.read_text(encoding="utf-8").splitlines()[-2000:]
                log.write_text("\n".join(tail) + "\n", encoding="utf-8")

# ==============================================================================
# SECTION 15 -- Doctor
# ==============================================================================
class Health(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class Check:
    group: str
    item: str
    state: Health
    detail: str


REQUIRED_TOOLS: Final = (
    ("fusermount3", "FUSE3 userspace unmount helper", "fuse3"),
    ("fuse-overlayfs", "rootless union filesystem", "fuse-overlayfs"),
)
OPTIONAL_TOOLS: Final = (
    ("dwarfs", "DwarFS FUSE driver", "dwarfs"),
    ("wine", "Wine-Staging runtime", "wine-staging"),
    ("wineserver", "Wine prefix coordinator", "wine-staging"),
    ("umu-run", "UMU / Proton launcher", "umu-launcher"),
    ("winetricks", "prefix provisioning verbs", "winetricks"),
    ("gamescope", "Wayland micro-compositor", "gamescope"),
    ("mangohud", "frame telemetry overlay", "mangohud"),
    ("mangoapp", "gamescope-native overlay", "mangohud"),
    ("gamemoderun", "Feral GameMode", "gamemode"),
    ("bwrap", "bubblewrap sandbox", "bubblewrap"),
    ("fzf", "fuzzy selector", "fzf"),
    ("systemd-run", "transient cgroup scope", "systemd"),
    ("notify-send", "desktop notifications", "libnotify"),
    ("taskset", "CPU affinity", "util-linux"),
)

TUNABLES: Final = (
    ("vm.max_map_count", 1048576, "DX12/UE5 map-heavy titles exhaust the default"),
    ("vm.swappiness", None, "informational"),
    ("kernel.split_lock_mitigate", 0, "0 avoids 10-100x stalls on split-lock traps"),
    ("fs.file-max", None, "informational"),
    ("kernel.sched_cfs_bandwidth_slice_us", None, "informational"),
)


def collect_checks() -> list[Check]:
    out: list[Check] = []
    add = out.append
    uname = os.uname()
    krel = kernel_release()

    add(Check("kernel", "release",
              Health.OK if krel >= (7, 1) else Health.WARN,
              f"{uname.sysname} {uname.release} "
              + ("" if krel >= (7, 1) else "(engine targets >= 7.1)")))
    gil_probe = getattr(sys, "_is_gil_enabled", None)
    build_kind = "gil" if (gil_probe is None or gil_probe()) else "free-threaded"
    add(Check("python", "interpreter", Health.OK,
              f"{sys.version.split()[0]} ({build_kind} build, "
              f"{cpu_count()} usable cpus)"))

    # -- cgroup v2 ---------------------------------------------------------
    cg_type = mount_table().fstype(SYSFS_CGROUP)
    add(Check("kernel", "cgroup v2",
              Health.OK if cg_type == "cgroup2" else Health.FAIL,
              f"{SYSFS_CGROUP} is {cg_type or 'not mounted'}"))
    my_cg = cgroup_of(os.getpid())
    add(Check("kernel", "cgroup delegation",
              Health.OK if my_cg and (my_cg / "cgroup.kill").exists() else Health.WARN,
              f"{my_cg} (cgroup.kill "
              f"{'available' if my_cg and (my_cg / 'cgroup.kill').exists() else 'missing'})"))

    # -- ntsync ------------------------------------------------------------
    if ntsync_available():
        add(Check("kernel", "ntsync", Health.OK, "/dev/ntsync usable (NT sync in-kernel)"))
    elif os.path.exists("/dev/ntsync"):
        add(Check("kernel", "ntsync", Health.WARN,
                  "/dev/ntsync exists but is not rw for this user (check udev rules)"))
    else:
        add(Check("kernel", "ntsync", Health.WARN,
                  "absent -- `sudo modprobe ntsync` and add /etc/modules-load.d/ntsync.conf"))

    # -- sysctl ------------------------------------------------------------
    for name, want, why in TUNABLES:
        raw = sysctl_read(name)
        if not raw:
            continue
        if want is None:
            add(Check("sysctl", name, Health.INFO, f"{raw} -- {why}"))
            continue
        try:
            cur = int(raw)
        except ValueError:
            add(Check("sysctl", name, Health.INFO, raw))
            continue
        good = cur >= want if name == "vm.max_map_count" else cur == want
        add(Check("sysctl", name, Health.OK if good else Health.WARN,
                  f"{cur} (recommended {want}) -- {why}"))

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    add(Check("limits", "RLIMIT_NOFILE",
              Health.OK if soft >= 524288 else Health.WARN,
              f"soft={soft} hard={hard} (>=524288 required for WINEESYNC)"))

    # -- session -----------------------------------------------------------
    wl = os.environ.get("WAYLAND_DISPLAY", "")
    add(Check("session", "wayland", Health.OK if wl else Health.FAIL,
              wl or "WAYLAND_DISPLAY unset -- this engine does not support X11 sessions"))
    add(Check("session", "compositor", Health.INFO,
              os.environ.get("XDG_CURRENT_DESKTOP")
              or ("Hyprland" if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") else "unknown")))
    for o in outputs():
        add(Check("display", o.name, Health.INFO,
                  f"{o.width}x{o.height}@{o.refresh_hz:.3g}Hz scale={o.scale:g}"
                  + (" vrr" if o.vrr else "") + (" hdr" if o.hdr else "")
                  + (" [focused]" if o.focused else "")))

    # -- gpu ---------------------------------------------------------------
    devs = gpus()
    if not devs:
        add(Check("gpu", "topology", Health.FAIL, "no display-class PCI device in sysfs"))
    for g in devs:
        icds = g.icds()
        state = Health.OK if icds else Health.WARN
        detail = g.describe()
        if icds:
            detail += " | ICD " + ", ".join(sorted({i.library for i in icds}))
        else:
            detail += " | no Vulkan ICD matched"
        add(Check("gpu", f"{g.vendor_name} {g.card}", state, detail))
    if len([g for g in devs if not g.is_software]) > 1:
        chosen = select_gpu("discrete")
        add(Check("gpu", "hybrid policy", Health.INFO,
                  f"discrete -> {chosen.describe() if chosen else 'n/a'}; "
                  f"DRI_PRIME={chosen.dri_prime if chosen else '-'}"))
    for icd in vulkan_icds():
        add(Check("vulkan", icd.manifest.name, Health.INFO,
                  f"{icd.library} api={icd.api_version} vendor={icd.vendor}"
                  + (" [32-bit]" if icd.is_32bit else "")))

    # -- nvidia specifics --------------------------------------------------
    if any(g.is_nvidia for g in devs):
        modeset_raw = read_first_line("/sys/module/nvidia_drm/parameters/modeset").upper()
        ver_raw = read_first_line("/sys/module/nvidia/version")
        ver_match = re.match(r"^(\d+)", ver_raw)
        has_card = any(g.is_nvidia and g.card.startswith("card") for g in devs)
        cmdline = read_first_line("/proc/cmdline")
        modeset_active = (
            modeset_raw in ("Y", "1")
            or (ver_match and int(ver_match.group(1)) >= 560 and has_card)
            or "nvidia-drm.modeset=1" in cmdline
            or "nvidia_drm.modeset=1" in cmdline
        )
        is_secondary = any(g.boot_vga for g in devs if not g.is_nvidia)
        if modeset_active:
            modeset_status = Health.OK
            modeset_msg = f"active (driver {ver_raw or '>=560'})"
        else:
            modeset_status = Health.WARN if is_secondary else Health.FAIL
            modeset_msg = f"modeset={modeset_raw or 'unset'} " + (
                "(recommended Y; secondary offload active)"
                if is_secondary else "(must be Y for primary Wayland)"
            )
        add(Check("nvidia", "drm modeset", modeset_status, modeset_msg))
        fbdev = read_first_line("/sys/module/nvidia_drm/parameters/fbdev")
        add(Check("nvidia", "drm fbdev", Health.INFO, f"nvidia_drm.fbdev={fbdev or 'unset'}"))
        ngx = any((d / "nvngx.dll").is_file() for d in NVIDIA_WINE_DIRS)
        add(Check("nvidia", "nvngx (DLSS)",
                  Health.OK if ngx else Health.WARN,
                  "found in /usr/lib/nvidia/wine" if ngx
                  else "missing -- install nvidia-utils >= 550 for DLSS in Wine"))

    # -- tools -------------------------------------------------------------
    for binary, desc, pkg in REQUIRED_TOOLS:
        path = shutil.which(binary)
        add(Check("tools", binary, Health.OK if path else Health.FAIL,
                  path or f"REQUIRED -- pacman -S {pkg}"))
    for binary, desc, pkg in OPTIONAL_TOOLS:
        path = shutil.which(binary)
        add(Check("tools", binary, Health.OK if path else Health.INFO,
                  path or f"optional ({desc}) -- pacman -S {pkg}"))

    # -- audio / dbus ------------------------------------------------------
    pw = XDG_RUNTIME_DIR / "pipewire-0"
    add(Check("audio", "pipewire", Health.OK if pw.exists() else Health.WARN,
              str(pw) if pw.exists() else "socket absent -- is pipewire.service running?"))
    for label, addr in (("session", session_bus_address()), ("system", system_bus_address())):
        try:
            conn = DBusConnection(addr)
            add(Check("dbus", f"{label} bus", Health.OK, f"connected as {conn.unique_name}"))
            conn.close()
        except (DBusError, OSError) as exc:
            add(Check("dbus", f"{label} bus", Health.WARN, str(exc)))

    # -- storage -----------------------------------------------------------
    mem = MemInfo.read()
    add(Check("memory", "available", Health.INFO,
              f"{_human_kib(mem.available_kib)} of {_human_kib(mem.total_kib)}"))
    add(Check("engine", "config root", Health.OK if ROOT_DIR.is_dir() else Health.WARN,
              str(ROOT_DIR)))
    return out


def doctor(*, fix: bool, as_json: bool) -> int:
    if fix:
        Log.info("applying kernel tunables (requires sudo)")
        for arg in (
            "vm.max_map_count=1048576",
            "kernel.split_lock_mitigate=0",
        ):
            r = run_cmd(["sudo", "-n", "sysctl", "-w", arg], timeout=15)
            (Log.ok if r.ok else Log.warn)(f"sysctl {arg}: {r.message if not r.ok else 'ok'}")
        if not os.path.exists("/dev/ntsync"):
            r = run_cmd(["sudo", "-n", "modprobe", "ntsync"], timeout=15)
            (Log.ok if r.ok else Log.warn)(
                "modprobe ntsync: " + ("loaded" if r.ok else r.message)
            )

    checks = collect_checks()
    if as_json:
        print(json.dumps(
            [{"group": c.group, "item": c.item, "state": str(c.state),
              "detail": c.detail} for c in checks],
            indent=2,
        ))
    else:
        _render_checks(checks)
    return 1 if any(c.state is Health.FAIL for c in checks) else 0


_HEALTH_STYLE: Final = {
    Health.OK: ("bold green", Ansi.GREEN, "OK  "),
    Health.WARN: ("bold yellow", Ansi.YELLOW, "WARN"),
    Health.FAIL: ("bold red", Ansi.RED, "FAIL"),
    Health.INFO: ("dim", Ansi.DIM, "  · "),
}


def _render_checks(checks: Sequence[Check]) -> None:
    console = Log.console()
    if console is None:
        group = ""
        for c in checks:
            if c.group != group:
                group = c.group
                print(f"\n[{group}]")
            _, ansi, label = _HEALTH_STYLE[c.state]
            print(f"  {ansi}{label}{Ansi.RESET}  {c.item:<26} {c.detail}")
        return
    from rich import box as _box
    from rich.table import Table as _Table

    t = _Table(title=f"{ENGINE_NAME} {ENGINE_VERSION} :: system diagnostics",
               box=_box.ROUNDED, header_style="bold cyan")
    t.add_column("group", style="cyan", no_wrap=True)
    t.add_column("component", style="white", no_wrap=True)
    t.add_column("state", justify="center", no_wrap=True)
    t.add_column("detail", style="dim", overflow="fold")
    last = ""
    for c in checks:
        style, _, label = _HEALTH_STYLE[c.state]
        t.add_row("" if c.group == last else c.group, c.item,
                  f"[{style}]{label.strip()}[/{style}]", c.detail)
        last = c.group
    console.print(t)


# ==============================================================================
# SECTION 16 -- Validation
# ==============================================================================
def validate(mgr: ProfileManager, targets: Sequence[str] | str | None, *, as_json: bool) -> int:
    if isinstance(targets, str):
        ids = [targets]
    elif targets:
        ids = list(targets)
    else:
        ids = list(mgr.discover_profiles())
    if not ids:
        Log.warn(f"no profiles in {mgr.profiles_dir}")
        return 0
    rows: list[dict[str, Any]] = []
    bad = 0
    for pid in ids:
        row: dict[str, Any] = {"id": pid}
        try:
            prof = mgr.load(pid)
            paths = resolve_paths(prof)
            st = MountEngine.status(paths)
            row |= {
                "name": prof.name,
                "extends": prof.cfg.get("extends", ""),
                "runtime": prof.runtime,
                "game_dir": str(paths.game_dir),
                "game_dir_exists": paths.game_dir.is_dir(),
                "dwarfs": str(paths.dwarfs_image) if paths.dwarfs_image else "",
                "dwarfs_present": paths.uses_dwarfs,
                "executable": paths.executable,
                "mount": str(st.state),
                "installed": profile_installed(prof),
                "status": "valid",
            }
            problems: list[str] = []
            if not paths.game_dir.is_dir():
                problems.append("game_dir missing")
            if paths.dwarfs_image is not None and not paths.uses_dwarfs:
                problems.append("dwarfs_image declared but absent")
            if not paths.executable and prof.runtime != "script":
                problems.append("paths.executable unset")
            ext = prof.cfg.get("extends")
            for name in ([ext] if isinstance(ext, str) else list(ext or [])):
                if name and name not in mgr.discover_presets():
                    problems.append(f"unknown preset {name}")
            if problems:
                row["status"] = "invalid"
                row["problems"] = problems
                bad += 1
        except (ConfigError, OSError) as exc:
            row |= {"status": "error", "problems": [str(exc)]}
            bad += 1
        rows.append(row)

    if as_json:
        print(json.dumps(rows, indent=2))
        return 1 if bad else 0

    console = Log.console()
    if console is None:
        for r in rows:
            mark = {"valid": "OK  ", "invalid": "WARN", "error": "FAIL"}[r["status"]]
            print(f"{mark}  {r['id']:<24} {r.get('name', ''):<28} "
                  f"{r.get('mount', '-'):<10} {'; '.join(r.get('problems', []))}")
    else:
        from rich import box as _box
        from rich.table import Table as _Table

        t = _Table(title="profile validation matrix", box=_box.ROUNDED,
                   header_style="bold cyan")
        for col in ("id", "title", "preset", "runtime", "dwarfs", "mount", "state"):
            t.add_column(col, overflow="fold")
        for r in rows:
            colour = {"valid": "green", "invalid": "yellow", "error": "red"}[r["status"]]
            t.add_row(
                r["id"], str(r.get("name", "")), str(r.get("extends", "") or "-"),
                str(r.get("runtime", "-")),
                "yes" if r.get("dwarfs_present") else ("declared" if r.get("dwarfs") else "-"),
                str(r.get("mount", "-")),
                f"[{colour}]{r['status']}[/{colour}]"
                + (("\n" + "\n".join(r.get("problems", []))) if r.get("problems") else ""),
            )
        console.print(t)
    return 1 if bad else 0


# ==============================================================================
# SECTION 17 -- Scaffolder
# ==============================================================================
_NOISE_RE: Final = re.compile(
    r"[-_. ]+(?:jc141|fitgirl|dodi|elamigos|gog|steam|repack|rip|multi\d*|"
    r"v?\d+(?:\.\d+)+[a-z0-9]*|build\d+|proper|readnfo)\b.*$",
    re.IGNORECASE,
)
ENGINE_FINGERPRINTS: Final = (
    ("unreal5", ("Engine/Binaries/Win64", "UE5", ".uproject", "Engine/Content/Slate")),
    ("unity", ("UnityPlayer.so", "UnityPlayer.dll", "globalgamemanagers", "GameAssembly")),
    ("godot", (".pck", "godot")),
    ("source", ("hl2_", "bin/engine.so")),
)


class Scaffolder:
    @staticmethod
    def analyse(target: Path) -> dict[str, Any]:
        """Fingerprint a game directory without walking it exhaustively."""
        facts: dict[str, Any] = {
            "dwarfs": "", "engine": "", "windows": False, "native": False,
            "exe": "", "vc_redist": False, "listing": "",
        }
        with suppress(OSError):
            for dw in target.glob("**/*.dwarfs"):
                facts["dwarfs"] = str(dw.relative_to(target))
                break
        # DwarFS repacks ship a manifest of the packed tree; use it instead of
        # mounting the image just to look inside.
        listing = ""
        for name in ("dwarfs-tree", "filelist.txt", "contents.txt"):
            with suppress(OSError):
                for f in target.glob(f"**/{name}"):
                    listing = read_text(f, 1 << 20)
                    break
            if listing:
                break
        facts["listing"] = listing[:4096]

        haystack = listing
        with suppress(OSError):
            for depth in (1, 2, 3):
                for p in target.glob("/".join(["*"] * depth)):
                    haystack += "\n" + p.name

        for engine, needles in ENGINE_FINGERPRINTS:
            if any(n.lower() in haystack.lower() for n in needles):
                facts["engine"] = engine
                break
        facts["windows"] = ".exe" in haystack.lower()
        facts["vc_redist"] = "vc_redist" in haystack.lower() or "vcredist" in haystack.lower()

        # Prefer an executable named after the directory, then the largest .exe.
        cands: list[tuple[bool, int, Path]] = []
        stem = target.name.lower()
        with suppress(OSError):
            for depth in (1, 2, 3):
                for p in target.glob("/".join(["*"] * depth)):
                    low = p.name.lower()
                    if p.suffix.lower() not in (".exe", ".x86_64", ".bin", ".sh"):
                        continue
                    if low.startswith(("unins", "vc_redist", "vcredist", "dxsetup",
                                       "oalinst", "crashreport", "setup")):
                        continue
                    try:
                        if not p.is_file():
                            continue
                        size = p.stat().st_size
                    except OSError:
                        continue
                    cands.append((stem not in p.stem.lower(), -size, p))
        if cands:
            cands.sort(key=lambda t: (t[0], t[1], str(t[2])))
            best = cands[0][2]
            facts["exe"] = str(best.relative_to(target))
            facts["native"] = best.suffix.lower() != ".exe"
        return facts

    @classmethod
    def scaffold(
        cls, mgr: ProfileManager, target: Path, *, pid: str | None, name: str | None,
        preset: str | None, out: Path | None, overwrite: bool, install: bool,
    ) -> Path:
        target = target.expanduser().resolve()
        if not target.is_dir():
            raise ConfigError(f"{target} is not a directory")
        folder = target.name
        pid = pid or (re.sub(r"[^a-z0-9]+", "_", _NOISE_RE.sub("", folder).lower()).strip("_")
                      or "game")
        name = name or _NOISE_RE.sub("", folder).replace(".", " ").replace("_", " ") \
            .replace("-", " ").strip().title() or pid

        f = cls.analyse(target)
        engine = f["engine"]
        windows = bool(f["windows"]) and not f["native"]
        chosen = preset or {
            "unreal5": "base_unreal5",
            "unity": "base_unity",
        }.get(engine, "base_wine" if windows else "base_native")
        runtime = "wine" if windows else "native"
        exe = f["exe"] or ("game.exe" if windows else f"{pid}.x86_64")

        lines = [
            "# " + "=" * 76,
            f"#  {name}",
            f"#  scaffolded by {ENGINE_NAME} {ENGINE_VERSION} on "
            + time.strftime("%Y-%m-%d"),
            "# " + "=" * 76,
            "",
            f'extends = "{chosen}"',
            "",
            "[meta]",
            f'id = "{pid}"',
            f'name = "{_toml_str(name)}"',
            'icon = "input-gaming"',
            f'genre = "{engine or "unknown"}"',
            f'description = "Auto-scaffolded profile for {_toml_str(name)}."',
            "",
            "[paths]",
            f'game_dir = "{_toml_str(str(target))}"',
        ]
        if f["dwarfs"]:
            lines += [
                f'dwarfs_image = "{_toml_str(f["dwarfs"])}"',
                'dwarfs_mount = ".mnt/dwarfs"',
                'overlay_dir = ".mnt/root"',
                'overlay_storage = ".mnt/upper"',
                'overlay_work = ".mnt/work"',
            ]
        lines += [
            f'executable = "{_toml_str(exe)}"',
            "arguments = []",
            "",
            "[runtime]",
            f'type = "{runtime}"',
        ]
        if runtime == "wine":
            prefix = "files/prefix" if (target / "files" / "prefix").is_dir() else "prefix"
            redist = '["**/VC_redist.x64.exe"]' if f["vc_redist"] else "[]"
            lines += [
                "",
                "[runtime.wine]",
                f'prefix_dir = "{prefix}"',
                'arch = "win64"',
                'sync_mode = "auto"        # ntsync when /dev/ntsync exists',
                "dxvk = true",
                f"vkd3d = {'true' if engine == 'unreal5' else 'false'}",
                f"redistributables = {redist}",
                "winetricks = []",
            ]
        lines += [
            "",
            "# Conditional overlays -- merged only when the predicate holds.",
            "# [when.nvidia.runtime.wine]",
            "# dxvk_nvapi = true",
            "# [when.hybrid.graphics]",
            '# gpu = "discrete"',
            "",
        ]
        out = out or (mgr.profiles_dir / f"{pid}.toml")
        if out.exists() and not overwrite:
            raise ConfigError(f"{out} exists (use --overwrite)")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines), encoding="utf-8")
        Log.ok(f"wrote {out}")
        Log.info(f"detected: engine={engine or 'n/a'} runtime={runtime} "
                 f"dwarfs={'yes' if f['dwarfs'] else 'no'} exe={exe}")
        if install:
            install_desktop(mgr, pid)
        return out


def _toml_str(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


# ==============================================================================
# SECTION 18 -- Desktop integration
# ==============================================================================
def install_desktop(mgr: ProfileManager, pid: str) -> Path:
    prof = mgr.load(pid)
    apps = XDG_DATA_HOME / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    target = apps / f"{ENGINE_SLUG}-{pid}.desktop"
    exec_line = f"{shlex.quote(sys.executable)} {shlex.quote(str(SELF_PATH))} run {shlex.quote(pid)}"
    prefers_dgpu = str(prof.get("graphics.gpu", "auto")) in ("discrete", "nvidia", "amd")
    body = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={prof.name}",
        f"Comment={prof.get('meta.description') or f'Launch {prof.name}'}",
        f"Exec={exec_line}",
        f"TryExec={sys.executable}",
        f"Icon={prof.icon}",
        "Terminal=false",
        "Categories=Game;",
        "StartupNotify=true",
        f"X-MasterRunner-Profile={pid}",
    ]
    if prefers_dgpu:
        body += ["PrefersNonDefaultGPU=true"]
    body += [
        "",
        "Actions=Mount;Unmount;",
        "",
        "[Desktop Action Mount]",
        "Name=Mount game data",
        f"Exec={shlex.quote(sys.executable)} {shlex.quote(str(SELF_PATH))} mount {shlex.quote(pid)}",
        "",
        "[Desktop Action Unmount]",
        "Name=Unmount game data",
        f"Exec={shlex.quote(sys.executable)} {shlex.quote(str(SELF_PATH))} unmount {shlex.quote(pid)}",
        "",
    ]
    target.write_text("\n".join(body), encoding="utf-8")
    target.chmod(0o755)
    if have("update-desktop-database"):
        run_cmd(["update-desktop-database", str(apps)], timeout=20)
    Log.ok(f"installed {target}")
    return target


# ==============================================================================
# SECTION 19 -- Selection helpers (targets, FZF)
# ==============================================================================
def catalogue(mgr: ProfileManager, *, show_all: bool) -> list[Profile]:
    profs: list[Profile] = []
    for pid in mgr.discover_profiles():
        try:
            profs.append(mgr.load(pid))
        except ConfigError as exc:
            Log.warn(f"skipping {pid}: {exc}")
    if not show_all:
        profs = [p for p in profs if profile_installed(p)]
    profs.sort(key=lambda p: p.name.lower())
    return profs


def resolve_targets(spec: Sequence[str], pool: Sequence[Profile]) -> list[str]:
    """Accept ids, names, 1-based indices, ranges (`2-5`) and `all`."""
    if not spec:
        return []
    joined = " ".join(spec).strip()
    if joined.lower() in ("all", "*"):
        return [p.pid for p in pool]
    by_id = {p.pid.lower(): p.pid for p in pool}
    by_name = {p.name.lower(): p.pid for p in pool}
    out: list[str] = []
    for tok in re.split(r"[\s,]+", joined):
        if not tok:
            continue
        if m := re.fullmatch(r"(\d+)\s*[-.]{1,2}\s*(\d+)", tok):
            lo, hi = sorted((int(m[1]), int(m[2])))
            for i in range(lo, hi + 1):
                if 1 <= i <= len(pool):
                    out.append(pool[i - 1].pid)
            continue
        if tok.isdigit():
            i = int(tok)
            if 1 <= i <= len(pool):
                out.append(pool[i - 1].pid)
            continue
        low = tok.lower()
        if low in by_id:
            out.append(by_id[low])
        elif low in by_name:
            out.append(by_name[low])
        else:
            partial = [p.pid for p in pool if low in p.pid.lower() or low in p.name.lower()]
            if len(partial) == 1:
                out.append(partial[0])
            elif partial:
                Log.warn(f"{tok!r} is ambiguous: {', '.join(partial)}")
            else:
                Log.warn(f"no profile matches {tok!r}")
    return list(dict.fromkeys(out))


def _fzf_theme() -> list[str]:
    theme = XDG_CONFIG_HOME / "matugen/generated/dusky_tui.json"
    if theme.is_file():
        with suppress(OSError, ValueError):
            d = json.loads(theme.read_text(encoding="utf-8"))
            spec = ",".join(
                f"{k}:{v}" for k, v in {
                    "bg": d.get("bg", "-1"), "fg": d.get("fg", "-1"),
                    "bg+": d.get("muted", "-1"), "fg+": d.get("fg", "-1"),
                    "hl": d.get("error", "-1"), "hl+": d.get("error", "-1"),
                    "header": d.get("accent", "-1"), "info": d.get("warning", "-1"),
                    "pointer": d.get("success", "-1"), "marker": d.get("success", "-1"),
                    "prompt": d.get("accent", "-1"), "border": d.get("muted", "-1"),
                    "label": d.get("accent", "-1"), "spinner": d.get("accent", "-1"),
                }.items()
            )
            return ["--color", spec]
    return ["--color", "border:dim,label:cyan,prompt:cyan,pointer:green,marker:green"]


def _fzf_rows(profs: Sequence[Profile]) -> str:
    rows: list[str] = []
    for i, p in enumerate(profs, 1):
        try:
            st = MountEngine.status(resolve_paths(p)).state
        except ConfigError:
            st = MountState.UNMOUNTED
        gpu = str(p.get("graphics.gpu", "auto"))
        gs = "gs" if p.get("graphics.gamescope.enabled") else "  "
        rows.append(
            f"{p.pid}\t{i:>3} │ {p.name[:32]:<32} │ {p.runtime:<7} │ "
            f"{gpu:<10} │ {gs} │ {str(st):<9} │ {p.get('meta.genre', '') or ''}"
        )
    return "\n".join(rows)


def fzf_pick(profs: Sequence[Profile], *, multi: bool, verb: str) -> list[str]:
    if not have("fzf"):
        Log.warn("fzf not installed")
        return []
    if not profs:
        return []
    argv = [
        "fzf", "--ansi", "--delimiter=\t", "--with-nth=2",
        "--layout=reverse", "--height=60%", "--border=rounded",
        f"--border-label= {verb.upper()} ",
        "--border-label-pos=bottom:3", "--highlight-line",
        "--pointer=▌", "--marker=┃", "--info=inline-right",
        "--prompt", f"{verb} > ",
        "--header",
        ("TAB multi-select · ENTER confirm · ESC cancel" if multi
         else "ENTER launch · ESC cancel"),
        *_fzf_theme(),
    ]
    if multi:
        argv += ["-m", "--bind=tab:toggle+down", "--bind=btab:toggle+up",
                 "--bind=ctrl-a:select-all", "--bind=ctrl-d:deselect-all"]
    r = run_cmd(argv, stdin_data=_fzf_rows(profs), timeout=3600.0)
    if not r.ok:
        return []
    return [ln.split("\t", 1)[0].strip() for ln in r.out.splitlines() if ln.strip()]


# ==============================================================================
# SECTION 20 -- Interactive dashboard
# ==============================================================================
def dashboard(mgr: ProfileManager, *, show_all: bool) -> int:
    console = Log.console()
    if console is None:
        Log.error("the interactive dashboard requires `rich` (pacman -S python-rich)")
        return 1
    from rich import box as _box
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.table import Table as _Table

    while True:
        console.clear()
        console.print(Panel.fit(
            f"[bold cyan]{ENGINE_NAME}[/bold cyan] [dim]v{ENGINE_VERSION}[/dim]\n"
            f"[dim]{os.uname().release} · "
            f"{'ntsync' if ntsync_available() else 'fsync'} · "
            f"{len([g for g in gpus() if not g.is_software])} GPU · "
            f"{active_output().width}x{active_output().height}"
            f"@{active_output().refresh_hz:.3g}Hz[/dim]",
            border_style="cyan", box=_box.ROUNDED,
        ))
        mount_table(force=True)
        installed = catalogue(mgr, show_all=False)
        every = catalogue(mgr, show_all=True)
        hidden = len(every) - len(installed)
        pool = every if show_all else installed

        if not pool:
            console.print(Panel(
                f"[yellow]No installed games detected.[/yellow]\n"
                f"[dim]{hidden} profile(s) declared but their game_dir / dwarfs_image "
                f"are not present on disk.\nPress [a] to reveal them, or run "
                f"`{SELF_PATH.name} init <dir>` to scaffold a new profile.[/dim]",
                title="catalogue", border_style="yellow", box=_box.ROUNDED))
        else:
            t = _Table(
                title=(f"{len(installed)} installed"
                       + (f" · {hidden} hidden" if hidden and not show_all else "")
                       + (" · showing all" if show_all else "")),
                box=_box.ROUNDED, header_style="bold cyan",
            )
            t.add_column("#", justify="right", style="bold cyan")
            t.add_column("title", style="white")
            t.add_column("id", style="dim")
            t.add_column("runtime", style="magenta")
            t.add_column("gpu", style="green")
            t.add_column("mount", justify="center")
            for i, p in enumerate(pool, 1):
                try:
                    st = MountEngine.status(resolve_paths(p)).state
                except ConfigError:
                    st = MountState.UNMOUNTED
                badge = {
                    MountState.MOUNTED: "[bold green]mounted[/bold green]",
                    MountState.PARTIAL: "[yellow]partial[/yellow]",
                    MountState.STALE: "[bold red]stale[/bold red]",
                    MountState.UNMOUNTED: "[dim]-[/dim]",
                }[st]
                t.add_row(str(i), p.name, p.pid, p.runtime,
                          str(p.get("graphics.gpu", "auto")), badge)
            console.print(t)

        console.print(
            "\n[bold]commands[/bold]  "
            "[cyan]<n>[/cyan] launch  "
            "[cyan]f[/cyan] fuzzy find  "
            "[yellow]m[/yellow] mount  [yellow]u[/yellow] unmount  "
            "[magenta]d[/magenta] doctor  [magenta]v[/magenta] validate  "
            "[cyan]a[/cyan] toggle hidden  [red]q[/red] quit"
        )
        try:
            choice = Prompt.ask("\n[bold green]>[/bold green]", default="q").strip()
        except (EOFError, KeyboardInterrupt):
            return 0

        low = choice.lower()
        if low in ("q", "quit", "exit"):
            return 0
        if low in ("a", "all"):
            show_all = not show_all
            continue
        if low in ("d", "doctor"):
            doctor(fix=False, as_json=False)
            Prompt.ask("\n[dim]enter to continue[/dim]", default="")
            continue
        if low in ("v", "validate"):
            validate(mgr, None, as_json=False)
            Prompt.ask("\n[dim]enter to continue[/dim]", default="")
            continue
        if low in ("f", "/", "s"):
            picks = fzf_pick(pool, multi=False, verb="launch")
            if picks:
                _launch_many(mgr, picks, RunOptions())
                Prompt.ask("\n[dim]enter to continue[/dim]", default="")
            continue
        if low == "m" or low.startswith("m "):
            rest = choice[1:].strip()
            targets = resolve_targets([rest], pool) if rest else fzf_pick(
                pool, multi=True, verb="mount")
            for pid in targets:
                p = mgr.load(pid)
                MountEngine.mount(p, resolve_paths(p))
            Prompt.ask("\n[dim]enter to continue[/dim]", default="")
            continue
        if low == "u" or low.startswith("u "):
            rest = choice[1:].strip()
            targets = resolve_targets([rest], pool) if rest else fzf_pick(
                pool, multi=True, verb="unmount")
            for pid in targets:
                p = mgr.load(pid)
                MountEngine.unmount(p, resolve_paths(p))
            Prompt.ask("\n[dim]enter to continue[/dim]", default="")
            continue
        targets = resolve_targets([choice], pool)
        if targets:
            _launch_many(mgr, targets[:1], RunOptions())
            Prompt.ask("\n[dim]enter to continue[/dim]", default="")


def _launch_many(mgr: ProfileManager, pids: Sequence[str], opts: RunOptions) -> int:
    rc = 0
    for pid in pids:
        try:
            prof = mgr.load(pid, use_cache=False)
            rc = GameSession(mgr, prof, opts).run() or rc
        except ConfigError as exc:
            Log.error(str(exc))
            rc = 78
        except Exception as exc:  # last-resort guard: never kill the dashboard
            Log.error(f"unhandled error running {pid}: {exc!r}")
            if Log.level >= Verbosity.VERBOSE:
                import traceback
                traceback.print_exc()
            rc = 70
    return rc

# ==============================================================================
# SECTION 21 -- Listing / status
# ==============================================================================
def cmd_list(mgr: ProfileManager, *, show_all: bool, as_json: bool) -> int:
    profs = catalogue(mgr, show_all=show_all)
    every = catalogue(mgr, show_all=True)
    hidden = len(every) - len([p for p in every if profile_installed(p)])
    if as_json:
        print(json.dumps([{
            "id": p.pid, "name": p.name, "runtime": p.runtime,
            "extends": p.cfg.get("extends", ""),
            "game_dir": str(p.get("paths.game_dir", "")),
            "installed": profile_installed(p),
        } for p in profs], indent=2))
        return 0
    console = Log.console()
    if console is None:
        for p in profs:
            flag = "*" if profile_installed(p) else " "
            print(f"{flag} {p.pid:<26} {p.name:<32} {p.runtime:<8} "
                  f"{p.cfg.get('extends', '') or '-'}")
        if hidden and not show_all:
            print(f"\n{hidden} not-installed profile(s) hidden; use --all")
        return 0
    from rich import box as _box
    from rich.table import Table as _Table

    t = _Table(title=f"profiles in {mgr.profiles_dir}", box=_box.ROUNDED,
               header_style="bold cyan")
    for col in ("id", "title", "preset", "runtime", "game_dir", "state"):
        t.add_column(col, overflow="fold")
    for p in profs:
        ok = profile_installed(p)
        t.add_row(p.pid, p.name, str(p.cfg.get("extends", "") or "-"), p.runtime,
                  str(p.get("paths.game_dir", "")),
                  "[green]installed[/green]" if ok else "[dim]absent[/dim]")
    console.print(t)
    presets = mgr.discover_presets()
    pt = _Table(title="preset archetypes", box=_box.ROUNDED, header_style="bold cyan")
    pt.add_column("preset", style="blue")
    pt.add_column("source", style="dim", overflow="fold")
    for name, path in sorted(presets.items()):
        pt.add_row(name, str(path) if path.is_file() else "<built-in>")
    console.print(pt)
    if hidden and not show_all:
        console.print(f"[dim]{hidden} not-installed profile(s) hidden; use --all[/dim]")
    return 0


def cmd_status(mgr: ProfileManager, *, show_all: bool, as_json: bool) -> int:
    mount_table(force=True)
    rows: list[dict[str, Any]] = []
    for p in catalogue(mgr, show_all=show_all):
        try:
            paths = resolve_paths(p)
        except ConfigError as exc:
            rows.append({"id": p.pid, "state": "error", "detail": str(exc)})
            continue
        st = MountEngine.status(paths)
        rows.append({
            "id": p.pid, "name": p.name, "state": str(st.state),
            "dwarfs": st.dwarfs, "overlay": st.overlay,
            "stale": [str(s) for s in st.stale],
            "root": str(paths.root),
        })
    if as_json:
        print(json.dumps(rows, indent=2))
        return 0
    console = Log.console()
    if console is None:
        for r in rows:
            print(f"{r.get('state', '?'):<10} {r['id']:<26} {r.get('root', '')}")
        return 0
    from rich import box as _box
    from rich.table import Table as _Table

    t = _Table(title="mount status", box=_box.ROUNDED, header_style="bold cyan")
    for col in ("id", "title", "dwarfs", "union", "state", "game root"):
        t.add_column(col, overflow="fold")
    badge = {"mounted": "[green]mounted[/green]", "partial": "[yellow]partial[/yellow]",
             "stale": "[red]stale[/red]", "unmounted": "[dim]-[/dim]",
             "error": "[red]error[/red]"}
    for r in rows:
        t.add_row(r["id"], str(r.get("name", "")),
                  "yes" if r.get("dwarfs") else "-",
                  "yes" if r.get("overlay") else "-",
                  badge.get(str(r.get("state")), str(r.get("state"))),
                  str(r.get("root", r.get("detail", ""))))
    console.print(t)
    return 0


def cmd_mount(mgr: ProfileManager, targets: Sequence[str], *, show_all: bool,
              dry_run: bool, unmount: bool) -> int:
    pool = catalogue(mgr, show_all=True) if (show_all or unmount) \
        else catalogue(mgr, show_all=False)
    pids = resolve_targets(targets, pool) if targets else fzf_pick(
        pool, multi=True, verb="unmount" if unmount else "mount")
    if not pids:
        Log.warn("nothing selected")
        return 0
    rc = 0
    for pid in pids:
        try:
            p = mgr.load(pid)
            paths = resolve_paths(p)
        except ConfigError as exc:
            Log.error(str(exc))
            rc = 78
            continue
        fn = MountEngine.unmount if unmount else MountEngine.mount
        if not fn(p, paths, dry_run=dry_run):
            rc = 74
    return rc


def cmd_unmount_all(mgr: ProfileManager, *, dry_run: bool) -> int:
    mount_table(force=True)
    n = 0
    for p in catalogue(mgr, show_all=True):
        try:
            paths = resolve_paths(p)
        except ConfigError:
            continue
        st = MountEngine.status(paths)
        if st.state is MountState.UNMOUNTED:
            continue
        Log.info(f"detaching {p.pid} ({st.state})")
        MountEngine.unmount(p, paths, dry_run=dry_run)
        n += 1
    Log.ok(f"sweep complete; {n} profile(s) touched")
    return 0


def cmd_init_config(force: bool) -> int:
    ROOT_DIR.mkdir(parents=True, exist_ok=True)
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    for d in (STATE_DIR, CACHE_DIR, RUNTIME_DIR):
        d.mkdir(parents=True, exist_ok=True)
    cfg = ROOT_DIR / "config.toml"
    if cfg.exists() and not force:
        Log.warn(f"{cfg} exists (use --force to overwrite)")
    else:
        cfg.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
        Log.ok(f"wrote {cfg}")
    for name, body in DEFAULT_PRESETS.items():
        dst = PRESETS_DIR / f"{name}.toml"
        if dst.exists() and not force:
            continue
        dst.write_text(body, encoding="utf-8")
        Log.ok(f"wrote {dst}")
    Log.info(f"profiles directory: {PROFILES_DIR}")
    return 0


# ==============================================================================
# SECTION 22 -- CLI
# ==============================================================================
KNOWN_COMMANDS: Final = frozenset({
    "run", "menu", "tui", "fzf", "select", "list", "ls", "status", "mount",
    "unmount", "umount", "unmount-all", "validate", "init", "init-config",
    "doctor", "desktop", "desktop-all", "install-desktop", "install-all-desktops",
    "env", "version", "help",
})


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog=SELF_PATH.name,
        description=f"{ENGINE_NAME} {ENGINE_VERSION} - declarative Arch/Wayland game runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  %(prog)s                       interactive dashboard\n"
            "  %(prog)s <profile>             shorthand for `run <profile>`\n"
            "  %(prog)s run witcher3 --gamescope --fps 120 --set graphics.gpu=discrete\n"
            "  %(prog)s doctor --fix\n"
            "  %(prog)s init ~/Games/SomeRepack --install-desktop\n"
        ),
    )
    ap.add_argument("--version", action="version",
                    version=f"{ENGINE_NAME} {ENGINE_VERSION}")
    ap.add_argument("-v", "--verbose", action="count", default=0,
                    help="repeat for trace output")
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument("--plain", action="store_true", help="disable rich rendering")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="machine-readable output where supported")
    ap.add_argument("--root", type=Path, default=None,
                    help=f"config root (default {ROOT_DIR})")
    sub = ap.add_subparsers(dest="command")

    # ---- run -------------------------------------------------------------
    r = sub.add_parser("run", help="launch a profile")
    r.add_argument("profile")
    r.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                   dest="overrides",
                   help="dotted override, e.g. --set graphics.gamescope.width=1280")
    r.add_argument("--gpu", metavar="GPU",
                   help="GPU selector: auto, discrete, integrated, nvidia, amd, intel, or PCI/card ID (e.g. 10de:25a0, card0)")
    r.add_argument("--gamescope", action="store_true", default=None)
    r.add_argument("--no-gamescope", action="store_false", dest="gamescope")
    r.add_argument("--res", "--gamescope-res", dest="res", metavar="WxH", help="gamescope internal render size")
    r.add_argument("--output-res", "--gamescope-out-res", dest="output_res", metavar="WxH", help="gamescope output size")
    r.add_argument("--mode", "--gamescope-mode", dest="mode", choices=["borderless", "fullscreen", "windowed", "embedded", "nested"])
    r.add_argument("--fsr", "--gamescope-fsr", dest="fsr", action="store_true", help="gamescope -F fsr")
    r.add_argument("--sharpness", "--gamescope-sharpness", dest="sharpness", type=int)
    r.add_argument("--tearing", "--gamescope-tearing", dest="tearing", action="store_true")
    r.add_argument("--vrr", action="store_true", help="gamescope --adaptive-sync")
    r.add_argument("--hdr", action="store_true")
    r.add_argument("--fps", "--fps-limit", type=int, dest="fps")
    r.add_argument("--mangohud", action="store_true", default=None)
    r.add_argument("--no-mangohud", action="store_false", dest="mangohud")
    r.add_argument("--gamemode", action="store_true", default=None)
    r.add_argument("--no-gamemode", action="store_false", dest="gamemode")
    r.add_argument("--sync", choices=[str(s) for s in SyncMode])
    r.add_argument("--wine", dest="wine_bin", metavar="BIN")
    r.add_argument("--wine-debug", metavar="CHANNELS")
    r.add_argument("--nvapi", "--dxvk-nvapi", dest="nvapi", action="store_true", help="enable DXVK-NVAPI + DLSS")
    r.add_argument("--affinity", metavar="SPEC", help="0-7 | pcores | smt-off")
    r.add_argument("--sandbox", action="store_true", default=None)
    r.add_argument("--no-sandbox", action="store_false", dest="sandbox")
    r.add_argument("--reprovision", action="store_true",
                   help="force redistributable/winetricks re-run")
    r.add_argument("--no-mount", action="store_true")
    r.add_argument("--keep-mounted", action="store_true")
    r.add_argument("-n", "--dry-run", action="store_true")
    _add_json(r)
    r.add_argument("args", nargs="*",
                   help="tokens after a bare `--` are forwarded to the game")

    for name, helptext in (("menu", "interactive dashboard"), ("tui", None)):
        m = sub.add_parser(name, help=helptext or argparse.SUPPRESS)
        m.add_argument("--all", action="store_true")

    for name in ("fzf", "select"):
        f = sub.add_parser(name, help="fuzzy pick and launch" if name == "fzf"
                           else argparse.SUPPRESS)
        f.add_argument("--all", action="store_true")

    for name in ("list", "ls"):
        lp = sub.add_parser(name, help="list profiles" if name == "list"
                            else argparse.SUPPRESS)
        lp.add_argument("--all", action="store_true")
        _add_json(lp)

    s = sub.add_parser("status", help="mount / runtime status")
    s.add_argument("--all", action="store_true")
    _add_json(s)

    for name in ("mount", "unmount", "umount"):
        mp = sub.add_parser(name, help=f"{name} game data" if name != "umount"
                            else argparse.SUPPRESS)
        mp.add_argument("profiles", nargs="*")
        mp.add_argument("--all", action="store_true")
        mp.add_argument("-n", "--dry-run", action="store_true")

    ua = sub.add_parser("unmount-all", help="detach every active profile")
    ua.add_argument("-n", "--dry-run", action="store_true")

    v = sub.add_parser("validate", help="validate profile definitions")
    v.add_argument("profile", nargs="*")
    v.add_argument("--all", action="store_true")
    _add_json(v)

    i = sub.add_parser("init", help="scaffold a profile from a game directory")
    i.add_argument("path", type=Path)
    i.add_argument("--id")
    i.add_argument("--name")
    i.add_argument("--preset")
    i.add_argument("--output", type=Path)
    i.add_argument("--overwrite", action="store_true")
    i.add_argument("--install-desktop", action="store_true")

    ic = sub.add_parser("init-config", help="write default config.toml + presets")
    ic.add_argument("--force", action="store_true")

    d = sub.add_parser("doctor", help="system diagnostics")
    d.add_argument("--fix", action="store_true", help="apply sysctl/module fixes via sudo")
    _add_json(d)

    for name in ("desktop", "install-desktop"):
        dk = sub.add_parser(name, help="install a .desktop entry" if name == "desktop" else argparse.SUPPRESS)
        dk.add_argument("profile")

    for name in ("desktop-all", "install-all-desktops"):
        dka = sub.add_parser(name, help="install entries for every profile" if name == "desktop-all" else argparse.SUPPRESS)
        dka.add_argument("--all", action="store_true")

    e = sub.add_parser("env", help="print the resolved launch environment")
    e.add_argument("profile")
    e.add_argument("--set", action="append", default=[], dest="overrides")
    _add_json(e)

    return ap


def _add_json(p: argparse.ArgumentParser) -> None:
    """Allow `--json` on either side of the subcommand.

    `default=SUPPRESS` means the sub-parser only *sets* the attribute when the
    flag is actually present, so a global `--json` placed before the subcommand
    is never silently reset to False.
    """
    p.add_argument("--json", action="store_true", dest="as_json",
                   default=argparse.SUPPRESS,
                   help="machine-readable output")


def overrides_from_args(ns: argparse.Namespace) -> TomlDict:
    tree: TomlDict = {}

    def put(dotted: str, value: Any) -> None:
        keys = dotted.split(".")
        node = tree
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = value

    for spec in getattr(ns, "overrides", []) or []:
        if "=" not in spec:
            raise ConfigError(f"--set expects KEY=VALUE, got {spec!r}")
        key, _, raw = spec.partition("=")
        dotted_set(tree, key.strip(), raw)

    if getattr(ns, "gpu", None):
        put("graphics.gpu", ns.gpu)
    if getattr(ns, "gamescope", None) is not None:
        put("graphics.gamescope.enabled", ns.gamescope)
    for attr, wkey, hkey in (("res", "graphics.gamescope.width", "graphics.gamescope.height"),
                             ("output_res", "graphics.gamescope.output_width",
                              "graphics.gamescope.output_height")):
        raw = getattr(ns, attr, None)
        if raw:
            m = re.fullmatch(r"(\d+)\s*[xX*]\s*(\d+)", str(raw).strip())
            if not m:
                raise ConfigError(f"--{attr.replace('_', '-')} expects WxH, got {raw!r}")
            put(wkey, int(m[1]))
            put(hkey, int(m[2]))
            put("graphics.gamescope.enabled", True)
    if getattr(ns, "mode", None):
        put("graphics.gamescope.mode", ns.mode)
        put("graphics.gamescope.enabled", True)
    if getattr(ns, "fsr", False):
        put("graphics.gamescope.filter", "fsr")
        put("graphics.gamescope.enabled", True)
    if getattr(ns, "sharpness", None) is not None:
        put("graphics.gamescope.fsr_sharpness", ns.sharpness)
    if getattr(ns, "vrr", False):
        put("graphics.gamescope.adaptive_sync", True)
    if getattr(ns, "hdr", False):
        put("graphics.hdr", True)
        put("graphics.gamescope.hdr", True)
    if getattr(ns, "fps", None) is not None:
        put("performance.fps_limit", ns.fps)
    if getattr(ns, "mangohud", None) is not None:
        put("performance.mangohud", ns.mangohud)
    if getattr(ns, "gamemode", None) is not None:
        put("performance.gamemode", ns.gamemode)
    if getattr(ns, "affinity", None):
        put("performance.cpu_affinity", ns.affinity)
    if getattr(ns, "sync", None):
        put("runtime.wine.sync_mode", ns.sync)
    if getattr(ns, "wine_bin", None):
        put("runtime.wine.wine_binary", ns.wine_bin)
    if getattr(ns, "wine_debug", None):
        put("runtime.wine.debug", ns.wine_debug)
    if getattr(ns, "nvapi", False):
        put("runtime.wine.dxvk_nvapi", True)
    if getattr(ns, "sandbox", None) is not None:
        put("sandbox.enabled", ns.sandbox)
    return tree


def cmd_env(mgr: ProfileManager, ns: argparse.Namespace) -> int:
    prof = mgr.load(ns.profile, overrides=overrides_from_args(ns))
    paths = resolve_paths(prof)
    pipe = PipelineBuilder(prof, paths, [])
    under_gs = pipe.gamescope_enabled
    builder = EnvironmentBuilder(prof, paths, dry_run=True)
    env = builder.build(under_gamescope=under_gs)
    delta = {k: v for k, v in env.items() if os.environ.get(k) != v}
    argv: list[str] = []
    with suppress(ConfigError):
        argv, _ = pipe.build(under_gamescope=under_gs)
    if ns.as_json:
        print(json.dumps({"env": delta, "notes": builder.notes, "argv": argv,
                          "gpu": builder.gpu.describe() if builder.gpu else None}, indent=2))
    else:
        for k in sorted(delta):
            print(f"export {k}={shlex.quote(delta[k])}")
        if argv:
            print("exec " + shlex.join(argv))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    # Bare `master_runner.py <profile>` is shorthand for `run <profile>`.
    if args and not args[0].startswith("-") and args[0] not in KNOWN_COMMANDS:
        cand = args[0]
        if (PROFILES_DIR / f"{cand}.toml").is_file() or Path(cand).is_file():
            args.insert(0, "run")

    ap = build_parser()
    ns = ap.parse_args(args)

    if ns.quiet:
        Log.level = Verbosity.QUIET
    elif ns.verbose >= 2:
        Log.level = Verbosity.TRACE
    elif ns.verbose == 1:
        Log.level = Verbosity.VERBOSE
    if ns.plain:
        os.environ["MASTER_RUNNER_PLAIN"] = "1"
        Log._rich, Log._rich_probed = None, True

    raise_nofile()
    for d in (STATE_DIR, CACHE_DIR, RUNTIME_DIR):
        with suppress(OSError):
            d.mkdir(parents=True, exist_ok=True)

    mgr = ProfileManager(ns.root.expanduser().resolve() if ns.root else ROOT_DIR)

    try:
        match ns.command:
            case None | "menu" | "tui":
                return dashboard(mgr, show_all=getattr(ns, "all", False))

            case "fzf" | "select":
                pool = catalogue(mgr, show_all=ns.all)
                if not pool:
                    Log.warn("no installed games (try --all)")
                    return 0
                return _launch_many(mgr, fzf_pick(pool, multi=False, verb="launch"),
                                    RunOptions())

            case "list" | "ls":
                return cmd_list(mgr, show_all=ns.all, as_json=ns.as_json)

            case "status":
                return cmd_status(mgr, show_all=ns.all, as_json=ns.as_json)

            case "mount":
                return cmd_mount(mgr, ns.profiles, show_all=ns.all,
                                 dry_run=ns.dry_run, unmount=False)

            case "unmount" | "umount":
                return cmd_mount(mgr, ns.profiles, show_all=ns.all,
                                 dry_run=ns.dry_run, unmount=True)

            case "unmount-all":
                return cmd_unmount_all(mgr, dry_run=ns.dry_run)

            case "validate":
                return validate(mgr, None if getattr(ns, "all", False) else ns.profile, as_json=ns.as_json)

            case "doctor":
                return doctor(fix=ns.fix, as_json=ns.as_json)

            case "init":
                Scaffolder.scaffold(
                    mgr, ns.path, pid=ns.id, name=ns.name, preset=ns.preset,
                    out=ns.output, overwrite=ns.overwrite, install=ns.install_desktop,
                )
                return 0

            case "init-config":
                return cmd_init_config(ns.force)

            case "desktop" | "install-desktop":
                install_desktop(mgr, ns.profile)
                return 0

            case "desktop-all" | "install-all-desktops":
                for p in catalogue(mgr, show_all=ns.all):
                    with suppress(ConfigError, OSError):
                        install_desktop(mgr, p.pid)
                return 0

            case "env":
                return cmd_env(mgr, ns)

            case "run":
                forwarded = [a for a in (ns.args or []) if a != "--"]
                prof = mgr.load(ns.profile, overrides=overrides_from_args(ns),
                                use_cache=False)
                opts = RunOptions(
                    extra_args=forwarded,
                    dry_run=ns.dry_run,
                    reprovision=ns.reprovision,
                    no_mount=ns.no_mount,
                    keep_mounted=ns.keep_mounted,
                    json_out=ns.as_json,
                )
                return GameSession(mgr, prof, opts).run()

            case other:
                ap.error(f"unknown command {other!r}")
                return 64
    except ConfigError as exc:
        Log.error(str(exc))
        return 78            # EX_CONFIG
    except BrokenPipeError:
        with suppress(OSError):
            sys.stdout.close()
        return 0
    except KeyboardInterrupt:
        Log.warn("interrupted")
        return 130
    except Exception as exc:
        Log.error(f"fatal: {exc!r}")
        if Log.level >= Verbosity.VERBOSE:
            import traceback
            traceback.print_exc()
        return 70            # EX_SOFTWARE


if __name__ == "__main__":
    sys.exit(main())
