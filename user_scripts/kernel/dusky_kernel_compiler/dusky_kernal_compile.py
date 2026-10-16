#!/usr/bin/env python3
"""Build hardware-tailored stock Linux 7.3+ kernels for x86-64 Arch Linux.

Workflow: collect modules -> select profile/release -> configure and verify ->
make pacman-pkg -> install locally or transfer packages to the target.
RAM builds prefer /mnt/zram1, checkpointing objects to persistent storage.
No kernel feature patches; narrowly validated NVIDIA DKMS compatibility overlay.

Use --help for CLI options or run without arguments for the guided menu.
"""

import sys

if sys.version_info < (3, 15):
    sys.stderr.write(f"Dusky Kernel Compiler requires Python >= 3.15 (running {sys.version.split()[0]}).\n")
    raise SystemExit(70)

import argparse
import kernel_storage
lazy import kernel_nvidia
import collections
import functools
import fcntl
from contextlib import ExitStack, contextmanager
import gzip
import hashlib
import json
import lzma
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import tarfile
import tempfile
import textwrap
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal, Self

type Sections = dict[str, dict[str, Any]]
type Json = dict[str, Any]

# ---------------------------------------------------------------------------------------------------
# Constants & filesystem layout (XDG aware, env-overridable)
# ---------------------------------------------------------------------------------------------------
APP_NAME: Final = "Dusky Kernel Compiler"
APP_VERSION: Final = "7.0.0"
APP_TAGLINE: Final = "Tailored Arch Linux kernels (stock Linux 7.3+)"
MIN_KERNEL: Final = (7, 3)
SCRIPT_DIR: Final = Path(__file__).resolve().parent
XDG_CONFIG: Final = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
XDG_CACHE: Final = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
XDG_STATE: Final = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
XDG_DATA: Final = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
PROFILES_DIR: Final = Path(os.environ.get("DUSKY_PROFILES_DIR") or SCRIPT_DIR / "kernel_profiles")
USER_PROFILES_DIR: Final = XDG_CONFIG / "dusky-kernel" / "kernel_profiles"
CONFIG_SNAPSHOT_DIR: Final = XDG_CONFIG / "dusky-kernel" / "configs"
STATE_DIR: Final = XDG_STATE / "dusky-kernel"


def _detect_default_build_dir() -> Path:
    env = os.environ.get("DUSKY_BUILD_DIR")
    if env:
        return Path(env).expanduser()
    return XDG_CACHE / "dusky-kernel"


BUILD_DIR: Path = _detect_default_build_dir()
SRC_DIR: Path = BUILD_DIR / "src"
TARBALL_DIR: Path = Path(os.environ.get("DUSKY_TARBALL_DIR") or XDG_CACHE / "dusky-kernel" / "tarballs")
THINLTO_CACHE_DIR: Path = Path(os.environ.get("DUSKY_THINLTO_CACHE") or XDG_CACHE / "dusky-kernel" / "thinlto-cache")
PKGDEST_DIR: Path = Path(os.environ.get("DUSKY_PKGDEST") or BUILD_DIR / "packages")
IMPORT_DIR: Final = STATE_DIR / "imports"
STORAGE: dict = {}
CCACHE_DIR: Path = XDG_CACHE / "dusky-kernel" / "ccache"
RAM_RESERVE_GIB: int = 0


def set_build_dir(new_path: Path | str) -> None:
    global BUILD_DIR, SRC_DIR, TARBALL_DIR, THINLTO_CACHE_DIR, PKGDEST_DIR
    BUILD_DIR = Path(new_path).expanduser().resolve()
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    SRC_DIR = BUILD_DIR / "src"
    TARBALL_DIR = Path(os.environ.get("DUSKY_TARBALL_DIR") or STORAGE.get("persistent_dir", XDG_CACHE / "dusky-kernel") / "tarballs")
    THINLTO_CACHE_DIR = STORAGE.get("thinlto_dir", Path(os.environ.get("DUSKY_THINLTO_CACHE") or XDG_CACHE / "dusky-kernel" / "thinlto-cache"))
    PKGDEST_DIR = STORAGE.get("packages_dir", Path(os.environ.get("DUSKY_PKGDEST") or XDG_CACHE / "dusky-kernel" / "packages"))
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    for d in (SRC_DIR, TARBALL_DIR, THINLTO_CACHE_DIR, PKGDEST_DIR, BUILD_DIR / "seeds"):
        d.mkdir(parents=True, exist_ok=True)
LOG_DIR: Final = STATE_DIR / "logs"
HISTORY_FILE: Final = STATE_DIR / "history.json"
MODPROBED_DB_PATH: Final = XDG_CONFIG / "modprobed.db"
# Canonical upstream location for modprobed-db v2.50+ (XDG_DATA_HOME aware).
# Older guides / this script's legacy default used ~/.config/modprobed.db.
MODPROBED_DB_CANONICAL: Final = XDG_DATA / "modprobed-db" / "modprobed.db"


def _modprobed_db_from_conf() -> Path | None:
    """Read configured DBPATH from modprobed-db.conf if present."""
    for conf in (XDG_CONFIG / "modprobed-db" / "modprobed-db.conf", XDG_CONFIG / "modprobed-db.conf"):
        try:
            if conf.is_file():
                for raw_line in conf.read_text(encoding="utf-8", errors="replace").splitlines():
                    clean = raw_line.split("#", 1)[0].strip()
                    m = re.match(r'^DBPATH=["\']?([^"\']+)["\']?', clean)
                    if m:
                        val = m.group(1).strip()
                        if val:
                            return Path(val).expanduser() / "modprobed.db"
        except OSError:
            pass
    return None


def modprobed_db_candidates() -> tuple[Path, ...]:
    """Ordered search list for the modprobed.db database (env override first).

    Order: $DUSKY_MODPROBED_DB > modprobed-db.conf DBPATH > canonical XDG_DATA
    location > legacy ~/.config location. Callers should use resolve_modprobed_db()
    instead of hard-coding MODPROBED_DB_PATH.
    """
    cands: list[Path] = []
    env = os.environ.get("DUSKY_MODPROBED_DB")
    if env:
        cands.append(Path(env).expanduser())
    conf_db = _modprobed_db_from_conf()
    if conf_db and conf_db not in cands:
        cands.append(conf_db)
    if MODPROBED_DB_CANONICAL not in cands:
        cands.append(MODPROBED_DB_CANONICAL)
    if MODPROBED_DB_PATH not in cands:
        cands.append(MODPROBED_DB_PATH)
    return tuple(cands)


def _is_usable_db(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _extract_module_name(line: str) -> str | None:
    """Extract a kernel module name from one db/lsmod line, or None if not a module.

    Accepts both formats: single-token modprobed.db lines ("nvidia") and
    lsmod rows ("nvidia 12345 1 ..."). Rejects the lsmod header wherever it
    appears, comments/blank lines, and garbage multi-token lines whose second
    field is not numeric (so "bad name ..." never becomes a bogus "bad").
    Dashes are normalized to underscores (kernel canonical form).
    """
    s = line.split("#", 1)[0].strip()
    if not s:
        return None
    if s.startswith("Module") and "Size" in s:
        return None  # lsmod header, wherever it appears
    parts = s.split()
    first = parts[0].replace("-", "_")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", first):
        return None
    if len(parts) > 1:
        # lsmod-style row: second field must be the numeric size.
        if not parts[1].isdigit():
            return None
    return first


def count_db_modules(path: Path) -> int:
    """Count usable module entries, tolerating both db and lsmod formats."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    seen: set[str] = set()
    for line in text.splitlines():
        mod = _extract_module_name(line)
        if mod is not None:
            seen.add(mod)
    return len(seen)


def resolve_modprobed_db(custom: str | None = None) -> Path | None:
    """Resolve the modprobed.db to use.

    Precedence: explicit profile path > $DUSKY_MODPROBED_DB > auto-discovery.
    Explicit paths are authoritative (returned as-is when they contain at
    least one valid module, else None so callers fall back instead of
    pruning against an empty set). Auto-discovery scans candidate locations
    and prefers the one with the most entries (newest mtime breaks ties)
    so a stale legacy copy never shadows a fresh canonical DB. Files with
    zero valid modules are ignored everywhere.
    """
    def _usable_with_modules(p: Path) -> Path | None:
        try:
            r = p.resolve() if p.exists() else p
        except (OSError, RuntimeError):
            r = p
        for cand in (r, p):
            try:
                if cand.is_file() and cand.stat().st_size > 0 and count_db_modules(cand) > 0:
                    return r
            except (OSError, RuntimeError):
                continue
        return None
    if custom:
        p = Path(custom).expanduser()
        return _usable_with_modules(p)
    env = os.environ.get("DUSKY_MODPROBED_DB")
    if env:
        r = _usable_with_modules(Path(env).expanduser())
        if r is not None:
            return r
        debug(f"$DUSKY_MODPROBED_DB={env} is not usable; falling back to auto-discovery")
    best: Path | None = None
    best_key: tuple[int, float] = (-1, -1.0)
    for cand in modprobed_db_candidates():
        try:
            resolved = cand.resolve() if cand.is_symlink() else cand
        except (OSError, RuntimeError):
            resolved = cand
        if not _is_usable_db(resolved):
            continue
        try:
            count = count_db_modules(resolved)
        except (OSError, RuntimeError):
            continue
        if count <= 0:
            continue
        try:
            key = (count, resolved.stat().st_mtime)
        except (OSError, RuntimeError):
            key = (count, 0.0)
        if key > best_key:
            best_key = key
            best = resolved
    return best


def normalize_lsmod_file_to_db(src: Path, dest: Path) -> int:
    """Convert an lsmod-format listing to modprobed.db format (one name/line).

    Returns the number of modules written. Used when importing bundles that
    only contain lsmod.txt (copying it verbatim would leave the 'Module ...'
    header in LSMOD, which streamline_config would treat as a bogus module).
    """
    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    names: set[str] = set()
    for line in text.splitlines():
        mod = _extract_module_name(line)
        if mod is not None:
            names.add(mod)
    if not names:
        return 0
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n".join(sorted(names)) + "\n", encoding="utf-8")
    except OSError:
        return 0
    return len(names)
KERNEL_ORG_RELEASES: Final = "https://www.kernel.org/releases.json"
ARCH_UPSTREAM_CONFIG_URL: Final = "https://gitlab.archlinux.org/archlinux/packaging/packages/linux/-/raw/main/config"
KERNEL_SIGNING_FPRS: Final = frozenset({
    "ABAF11C65A2970B130ABE3C479BE3E4300411886",  # Linus Torvalds
    "647F28654894E3BD457199BE38DBBDC86092693E",  # Greg Kroah-Hartman
    "E27E5D8A3403A2EF66873BBCDEA66FF797772CDC",  # Sasha Levin
})
USER_AGENT: Final = f"DuskyKernelCompiler/{APP_VERSION} (+Arch Linux)"

# ---------------------------------------------------------------------------------------------------
# Terminal UI primitives
# ---------------------------------------------------------------------------------------------------
ESC: Final = "\x1b"


class C:
    RESET = ESC + "[0m"
    BOLD = ESC + "[1m"
    DIM = ESC + "[2m"
    RED = ESC + "[31m"
    GREEN = ESC + "[32m"
    YELLOW = ESC + "[33m"
    BLUE = ESC + "[34m"
    MAGENTA = ESC + "[35m"
    CYAN = ESC + "[36m"
    ACCENT = ESC + "[38;5;141m"
    CLEAR_EOL = ESC + "[K"
    HIDE = ESC + "[?25l"
    SHOW = ESC + "[?25h"

    @classmethod
    def disable(cls) -> None:
        for name in ("RESET", "BOLD", "DIM", "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN", "ACCENT", "CLEAR_EOL", "HIDE", "SHOW"):
            setattr(cls, name, "")


_ANSI_RE: Final = re.compile(ESC + r"\[[0-9;?]*[A-Za-z]")


def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def visible_len(s: str) -> int:
    return len(strip_ansi(s))


def pad(s: str, width: int) -> str:
    return s + " " * max(0, width - visible_len(s))


def truncate_ansi(s: str, max_len: int) -> str:
    if max_len <= 0:
        return ""
    if visible_len(s) <= max_len:
        return s
    res: list[str] = []
    vis = 0
    i = 0
    n = len(s)
    while i < n:
        if s[i] == ESC:
            m = _ANSI_RE.match(s[i:])
            if m:
                res.append(m.group(0))
                i += len(m.group(0))
                continue
        if vis < max_len:
            res.append(s[i])
            vis += 1
            i += 1
        else:
            break
    res.append(C.RESET)
    return "".join(res)


def term_width() -> int:
    try:
        return max(20, min(240, os.get_terminal_size().columns))
    except OSError:
        return 100


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


class Journal:
    """Plain-text build journal under ~/.local/state/dusky-kernel/logs."""

    def __init__(self) -> None:
        self.fh = None
        self.path: Path | None = None

    def open(self, name: str) -> None:
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            self.path = LOG_DIR / f"build-{name}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.log"
            self.fh = open(self.path, "w", encoding="utf-8", buffering=1)
        except OSError:
            self.fh = None

    def write(self, s: str) -> None:
        if self.fh is not None:
            try:
                self.fh.write(strip_ansi(s) + "\n")
            except OSError:
                pass

    def close(self) -> None:
        if self.fh is not None:
            try:
                self.fh.close()
            finally:
                self.fh = None


JOURNAL: Final = Journal()
_VERBOSE: bool = False
ASSUME_YES: bool = False
_LIVE: "Live | None" = None
_OUT_LOCK: Final = threading.RLock()


def say(s: str = "") -> None:
    with _OUT_LOCK:
        if _LIVE is not None:
            _LIVE.emit(s)
        else:
            sys.stdout.write(s + "\n")
            sys.stdout.flush()
        JOURNAL.write(s)


def info(s: str) -> None:
    say(f"  {C.BLUE}::{C.RESET} {s}")


def ok(s: str) -> None:
    say(f"  {C.GREEN}✓{C.RESET} {s}")


def warn(s: str) -> None:
    say(f"  {C.YELLOW}▲{C.RESET} {s}")


def err(s: str) -> None:
    say(f"  {C.RED}✗{C.RESET} {s}")


def note(s: str) -> None:
    say(f"  {C.DIM}{s}{C.RESET}")


def debug(s: str) -> None:
    if _VERBOSE:
        say(f"  {C.DIM}debug: {s}{C.RESET}")


def rule(title: str = "") -> None:
    w = term_width()
    if not title:
        say(C.DIM + "─" * w + C.RESET)
        return
    t = f" {title.strip()} "
    fill = max(0, w - visible_len(t) - 4)
    say(f"{C.DIM}──{C.RESET}{C.BOLD}{t}{C.RESET}{C.DIM}{'─' * fill}{C.RESET}")


def banner() -> None:
    say(f"{C.CYAN}{C.BOLD}{APP_NAME} v{APP_VERSION}{C.RESET} {C.DIM}-- {APP_TAGLINE}{C.RESET}")


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    if not rows:
        return
    cells = [[str(v) for v in r] for r in rows]
    widths = [visible_len(h) for h in headers]
    for r in cells:
        for i, val in enumerate(r):
            widths[i] = max(widths[i], visible_len(val))
    say("  " + "  ".join(pad(C.BOLD + h + C.RESET, widths[i]) for i, h in enumerate(headers)))
    say("  " + "  ".join("─" * w for w in widths))
    for r in cells:
        say("  " + "  ".join(pad(val, widths[i]) for i, val in enumerate(r)))


def send_notification(title: str, msg: str, urgency: str = "normal", icon: str = "dialog-information") -> None:
    if not shutil.which("notify-send"):
        return
    try:
        subprocess.run(["notify-send", "-a", "Dusky Kernel", "-u", urgency, "-i", icon, title, msg], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def fmt_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TiB"


# ---------------------------------------------------------------------------------------------------
# Prompts (KeyboardInterrupt intentionally propagates: Ctrl-C always aborts)
# ---------------------------------------------------------------------------------------------------
def ask(prompt: str, default: str = "") -> str:
    suffix = f" {C.DIM}[{default}]{C.RESET}" if default else ""
    try:
        return input(f"  {C.ACCENT}›{C.RESET} {prompt}{suffix}: ").strip() or default
    except EOFError:
        say("")
        return default


def ask_yes(prompt: str, default: bool = True) -> bool:
    if ASSUME_YES or not interactive():
        return default
    res = ask(f"{prompt} [{'Y/n' if default else 'y/N'}]").lower()
    if not res:
        return default
    return res in ("y", "yes", "true", "1", "on")


def ask_index(prompt: str, max_idx: int, default: int = 1) -> int:
    while True:
        val = ask(f"{prompt} (1-{max_idx})", str(default))
        if val.isdigit() and 1 <= int(val) <= max_idx:
            return int(val)
        warn(f"Enter an integer between 1 and {max_idx}")


def ask_choice(prompt: str, choices: Sequence[str], default: str) -> str:
    say(f"  {prompt} ({', '.join(choices)})")
    while True:
        val = ask("Choice", default)
        if val in choices:
            return val
        if val.isdigit() and 1 <= int(val) <= len(choices):
            return choices[int(val) - 1]
        warn(f"Invalid choice '{val}'. Pick from: {', '.join(choices)}")


def pause() -> None:
    if interactive() and not ASSUME_YES:
        ask("Press Enter to return", "")


# ---------------------------------------------------------------------------------------------------
# Error model (exit codes are part of the CLI contract)
# ---------------------------------------------------------------------------------------------------
class DuskyError(Exception):
    exit_code = 1


class ProfileError(DuskyError):
    exit_code = 2


class NetworkError(DuskyError):
    exit_code = 3


class VerifyError(DuskyError):
    exit_code = 4


class BuildError(DuskyError):
    exit_code = 5


class DependencyError(DuskyError):
    exit_code = 6


class AbortError(DuskyError):
    exit_code = 130


# ---------------------------------------------------------------------------------------------------
# Choice vocabularies (single source of truth for validation, CLI, wizard and Kconfig mapping)
# ---------------------------------------------------------------------------------------------------
from kernel_profiles.schema import (
    HZ_CHOICES,
    SCHED_CHOICES,
    CHANNEL_CHOICES,
    LTO_CHOICES,
    FOOTPRINT_CHOICES,
    TOOLCHAIN_CHOICES,
    HEADERS_CHOICES,
    MODULES_MODE_CHOICES,
    CHOICE_HELP,
    FieldSpec,
    PROFILE_SPEC,
    WIZARD_STEPS,
    SECURITY_BUNDLES,
    FOOTPRINT_RANK,
 )


@dataclass(slots=True)
class KernelProfile:
    path: Path
    sections: Sections
    explicit: set[tuple[str, str]] = field(default_factory=set)
    notices: list[str] = field(default_factory=list)

    def g(self, section: str, key: str, default: Any = None) -> Any:
        return self.sections.get(section, {}).get(key, default)

    def set(self, section: str, key: str, value: Any, *, explicit: bool = True) -> None:
        self.sections.setdefault(section, {})[key] = value
        if explicit:
            self.explicit.add((section, key))

    @property
    def name(self) -> str:
        return str(self.g("meta", "name") or self.path.stem)

    @property
    def description(self) -> str:
        return str(self.g("meta", "description", ""))

    @property
    def priority(self) -> int:
        return int(self.g("meta", "priority", 50))

    @property
    def suffix(self) -> str:
        return str(self.g("meta", "suffix") or self.name).strip().strip("-")

    def localversion(self) -> str:
        return "-" + self.suffix

    @property
    def pkgbase(self) -> str:
        return "linux-" + self.suffix

    @property
    def footprint_rank(self) -> int:
        return FOOTPRINT_RANK[self.g("memory", "footprint")]

    def lean(self, tier: str) -> bool:
        return self.footprint_rank >= FOOTPRINT_RANK[tier]

    def clone(self) -> Self:
        return type(self)(self.path, json.loads(json.dumps(self.sections)), set(self.explicit), list(self.notices))

    def summarize(self) -> list[tuple[str, str]]:
        s = self.sections
        sched = s["scheduler"]["type"] + (f" + {s['scheduler']['scx']}" if s["scheduler"]["scx"] != "none" else "")
        return [
            ("profile", f"{self.name}  ({self.pkgbase})"), ("channel", s["release"]["channel"] + (f" pin={s['release']['pin']}" if s["release"]["pin"] else "")),
            ("cpu", f"{s['cpu']['arch']} | gov={s['cpu']['governor']} | amd_pstate={s['cpu']['amd_pstate']} | mitigations={s['cpu']['mitigations']}"),
            ("scheduler", f"{sched} | CAS={'on' if s['cache']['sched_cache'] else 'off'}"),
            ("timing", f"{s['timing']['hz']}Hz | {s['timing']['tickless']} | preempt={s['timing']['preempt']}{' (dynamic)' if s['timing']['preempt_dynamic'] else ''}"),
            ("memory", f"footprint={s['memory']['footprint']} | THP={s['memory']['thp']} | swap={s['memory']['swap_backend']} | MGLRU={'on' if s['memory']['mglru'] else 'off'}"),
            ("toolchain", f"{s['compiler']['toolchain']} | {s['compiler']['optimize']} | lto={s['compiler']['lto']} | kcfi={'on' if s['compiler']['kcfi'] else 'off'} | rust={'on' if s['compiler']['rust'] else 'off'}"),
            ("security", f"{s['security']['profile']}"),
            ("modules", f"{s['modules']['mode']} | headers={s['compiler']['headers']}"),
            ("network", f"{s['network']['congestion']} / {s['network']['qdisc']}"),
        ]


def _coerce_value(spec: FieldSpec, val: Any) -> Any:
    expected = {"bool": bool, "int": int, "str": str, "list": list, "table": dict}[spec.kind]
    if type(val) is not expected:
        raise ProfileError(f"{spec.key}: expected {spec.kind}, got {type(val).__name__}")
    if spec.kind == "list" and not all(isinstance(x, str) for x in val):
        raise ProfileError(f"{spec.key}: list entries must be strings")
    return val.copy() if isinstance(val, (list, dict)) else val


def coerce(data: Mapping[str, Any], path: Path) -> tuple[Sections, set[tuple[str, str]]]:
    res: Sections = {}
    explicit: set[tuple[str, str]] = set()
    for sec in data:
        if sec not in PROFILE_SPEC:
            raise ProfileError(f"{path.name}: unknown section [{sec}]")
    for sec, fields in PROFILE_SPEC.items():
        in_sec = data.get(sec, {})
        if not isinstance(in_sec, dict):
            raise ProfileError(f"{path.name}: [{sec}] must be a table")
        known = {f.key for f in fields}
        for key in in_sec:
            if key not in known:
                raise ProfileError(f"{path.name}: unknown key {sec}.{key}")
        out: dict[str, Any] = {}
        for f in fields:
            if f.key in in_sec:
                out[f.key] = _coerce_value(f, in_sec[f.key])
                explicit.add((sec, f.key))
            else:
                out[f.key] = json.loads(json.dumps(f.default))
        res[sec] = out
    return res, explicit


def apply_security_bundle(p: KernelProfile) -> None:
    """Security knobs not explicitly set in the TOML follow the selected hardening bundle."""
    bundle = SECURITY_BUNDLES.get(p.g("security", "profile"), {})
    for key, val in bundle.items():
        if ("security", key) not in p.explicit:
            p.sections["security"][key] = val


def validate_profile(p: KernelProfile) -> None:
    for sec, fields in PROFILE_SPEC.items():
        for f in fields:
            val = p.sections[sec].get(f.key)
            if f.required and not val:
                raise ProfileError(f"{p.path.name}: missing required field {sec}.{f.key}")
            if f.choices and val not in f.choices:
                raise ProfileError(f"{p.path.name}: invalid value '{val}' for {sec}.{f.key}; allowed: {', '.join(map(str, f.choices))}")
            if f.kind == "int":
                if f.minimum is not None and val < f.minimum:
                    raise ProfileError(f"{p.path.name}: {sec}.{f.key}={val} below minimum {f.minimum}")
                if f.maximum is not None and val > f.maximum:
                    raise ProfileError(f"{p.path.name}: {sec}.{f.key}={val} above maximum {f.maximum}")
    if not re.fullmatch(r"none|scx_[A-Za-z0-9_]+", p.g("scheduler", "scx")):
        raise ProfileError("scheduler.scx must be none or a scx_ executable name")
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+", p.g("cpu", "arch")):
        raise ProfileError("cpu.arch must be a compiler CPU name")
    try:
        flags = shlex.split(p.g("cpu", "march"))
        shlex.split(p.g("boot", "cmdline_extra"))
        shlex.split(p.g("scheduler", "scx_flags"))
    except ValueError as e:
        raise ProfileError(f"Invalid quoting in profile: {e}") from e
    if any(not re.fullmatch(r"-m(?:arch|tune)=[A-Za-z0-9_.+-]+", flag) for flag in flags):
        raise ProfileError("cpu.march accepts only -march=CPU and -mtune=CPU flags")
    for key, value in p.g("dusky", "extra_config").items():
        if not re.fullmatch(r"(?:CONFIG_)?[A-Za-z0-9_]+", key) or type(value) not in (bool, int, str):
            raise ProfileError(f"Invalid Kconfig override: {key}")
        if isinstance(value, str) and ('\n' in value or '\r' in value):
            raise ProfileError(f"Kconfig string override {key} must fit on one line")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", p.suffix):
        raise ProfileError(f"{p.path.name}: meta.suffix '{p.suffix}' must match [a-z0-9][a-z0-9-]*")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", p.name):
        raise ProfileError(f"{p.path.name}: meta.name '{p.name}' contains unsupported characters")


def normalize_profile(p: KernelProfile) -> list[str]:
    """Resolve dependent knobs deterministically; returns human-readable notices."""
    s = p.sections
    notes: list[str] = []

    def force(sec: str, key: str, val: Any, why: str) -> None:
        if s[sec][key] != val:
            notes.append(f"{sec}.{key}: {s[sec][key]} -> {val} ({why})")
            s[sec][key] = val

    if s["compiler"]["toolchain"] == "gcc":
        force("compiler", "lto", "none", "LTO requires LLVM")
        force("compiler", "kcfi", False, "kCFI requires clang")
        force("compiler", "fdo", "none", "AutoFDO/Propeller require clang")
    if s["compiler"]["lto"] != "thin":
        force("compiler", "thinlto_cache", False, "ThinLTO cache only applies to lto=thin")
    if s["compiler"]["fdo"] != "none":
        force("compiler", "debug_info", "full", "FDO profile collection needs debug line information")
    if s["timing"]["preempt"] == "rt":
        force("timing", "preempt_dynamic", False, "PREEMPT_RT excludes PREEMPT_DYNAMIC")
    if s["scheduler"]["scx"] != "none":
        force("memory", "tracing", "full", "sched_ext daemon probes need function tracing")
        force("scheduler", "scx_enable_class", True, "a BPF scheduler daemon needs SCHED_CLASS_EXT")
    if s["memory"]["swap_backend"] != "zram":
        force("memory", "zram_multi_comp", False, "no zram device configured")
    if s["memory"]["thp"] == "never":
        force("memory", "thp_defrag", "never", "THP disabled")
    if not s["memory"]["numa"]:
        force("memory", "numa_balancing", False, "NUMA disabled")
    if not s["memory"]["ksm"]:
        force("memory", "ksm_run", False, "KSM not compiled")
    if s["memory"]["slub_tiny"]:
        force("memory", "slab_buckets", False, "SLAB_BUCKETS depends on !SLUB_TINY")
    if s["compiler"]["headers"] != "never" and s["memory"]["trim_unused_ksyms"]:
        force("memory", "trim_unused_ksyms", False, "TRIM_UNUSED_KSYMS breaks DKMS/out-of-tree modules; requires headers=never")
    if s["memory"]["footprint"] == "embedded":
        force("cpu", "compat32", False, "embedded footprint drops IA32 emulation")
        force("power", "hibernation", False, "embedded footprint drops hibernation")
    if s["cpu"]["mitigations"] == "off" and s["security"]["profile"] == "hardened":
        force("cpu", "mitigations", "on", "hardened profile keeps mitigations")
    if s["storage"]["io_scheduler"] == "bfq" and s["storage"]["iocost"]:
        notes.append("storage: bfq + iocost both active; iocost applies to devices without bfq")
    return notes


def cross_validate(p: KernelProfile, facts: "HostFacts | None" = None, *, force: bool = False) -> None:
    s = p.sections
    remote = bool(s["meta"]["portable_package"] or s["meta"]["manifest_path"])
    if remote and s["cpu"]["arch"] == "native":
        raise ProfileError("Remote/portable builds require an explicit target CPU, never native")
    if remote:
        extra = {str(key).removeprefix("CONFIG_"): value for key, value in s["dusky"]["extra_config"].items()}
        if extra.get("X86_NATIVE_CPU") in (True, "y", "m"):
            raise ProfileError("Remote/portable builds cannot enable CONFIG_X86_NATIVE_CPU")
    if s["modules"]["mode"] == "strict" and not s["modules"]["modprobed_db"] and not s["modules"]["allow_lsmod_fallback"]:
        raise ProfileError("modules.mode=strict needs modprobed_db=true (or allow_lsmod_fallback=true)")
    if s["security"]["profile"] == "extreme" or s["cpu"]["mitigations"] == "off":
        if not s["security"]["acknowledge_risk"]:
            raise ProfileError("security.profile=extreme / cpu.mitigations=off require security.acknowledge_risk=true")
    if s["timing"]["hz"] not in HZ_CHOICES:
        raise ProfileError(f"timing.hz={s['timing']['hz']} unsupported")
    floor = KVer.parse(s["release"]["min_version"])
    if floor is None:
        raise ProfileError("release.min_version must be a kernel version")
    if s["release"]["pin"]:
        pinned = KVer.parse(s["release"]["pin"])
        if pinned is None:
            raise ProfileError(f"release.pin '{s['release']['pin']}' is not a kernel version")
        if pinned.key() < max(floor.key(), KVer(*MIN_KERNEL, rc=0).key()):
            raise ProfileError(f"release.pin {s['release']['pin']} is below the {MIN_KERNEL[0]}.{MIN_KERNEL[1]} floor")
        if pinned.rc is not None and not s["release"]["allow_rc"]:
            raise ProfileError("release.pin selects an RC but release.allow_rc=false")
    if remote and "native" in s["cpu"]["march"]:
        raise ProfileError("remote/portable builds cannot use native CPU flags")
    if s["cpu"]["nr_cpus"] == 1:
        raise ProfileError("This SMP kernel requires nr_cpus >= 2, or 0 for automatic sizing")
    if facts is not None:
        if s["memory"]["numa"] and s["memory"]["nodes_shift"] and 2 ** s["memory"]["nodes_shift"] < facts.numa_nodes:
            raise ProfileError("memory.nodes_shift is too small for the target; use 0 for automatic sizing")
        if s["meta"]["bare_metal_only"] and not force:
            _det, _reason = _virt_guest(facts)
            if _det:
                raise ProfileError(f"profile is bare_metal_only but this host looks virtualized ({_reason}) (use --force to override)")
        if s["cpu"]["nr_cpus"] and s["cpu"]["nr_cpus"] < facts.threads:
            warn(f"cpu.nr_cpus={s['cpu']['nr_cpus']} is below the host thread count ({facts.threads}); extra CPUs stay offline")
        if s["compiler"]["lto"] == "full" and facts.mem_gib + facts.swap_gib < 16:
            warn(f"Full LTO link needs ~16-24 GiB; host has {facts.mem_gib:.1f} GiB RAM + {facts.swap_gib:.1f} GiB swap. Consider lto=thin.")
        if s["memory"]["swap_backend"] == "zswap" and not facts.disk_swap:
            warn("zswap selected but no disk swap device is active; zswap needs a backing swap (or choose zram)")
        if s["compiler"]["headers"] == "never" and facts.dkms_modules:
            warn(f"headers=never but DKMS modules are installed: {', '.join(facts.dkms_modules)}")
        if s["timing"]["tickless"] == "full" and "nohz_full=" not in facts.cmdline:
            warn("tickless=full without nohz_full= on the command line adds overhead and no benefit")


# ---------------------------------------------------------------------------------------------------
# TOML emitter (stdlib only reads TOML; profiles are written with this self-documenting renderer)
# ---------------------------------------------------------------------------------------------------
def toml_scalar(v: Any) -> str:
    match v:
        case bool():
            return "true" if v else "false"
        case int():
            return str(v)
        case str():
            return json.dumps(v, ensure_ascii=False)
        case list() | tuple():
            return "[" + ", ".join(toml_scalar(x) for x in v) + "]"
        case _:
            return json.dumps(str(v))


def render_profile_toml(sections: Sections, *, header: str = "") -> str:
    lines: list[str] = [f"# generated by {APP_NAME} {APP_VERSION} -- {datetime.now(UTC).strftime('%Y-%m-%d')}"]
    if header:
        lines.append(f"# {header}")
    lines.append("")
    for sec, fields in PROFILE_SPEC.items():
        lines.append(f"[{sec}]")
        tables: list[tuple[str, dict[str, Any]]] = []
        for f in fields:
            val = sections.get(sec, {}).get(f.key, f.default)
            if f.kind == "table":
                tables.append((f.key, dict(val or {})))
                continue
            ctx = f" | choices: {' | '.join(map(str, f.choices))}" if f.choices else ""
            lines.append(f"# {f.help}{ctx}")
            lines.append(f"{f.key} = {toml_scalar(val)}")
        lines.append("")
        for key, tbl in tables:
            lines.append(f"[{sec}.{key}]")
            lines.append("# SYMBOL = true | false | \"m\" | 123 | \"string\"   (CONFIG_ prefix optional)")
            for k, v in tbl.items():
                lines.append(f"{k} = {toml_scalar(v)}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def profile_from_tweaks(name: str, description: str, suffix: str, tweaks: Mapping[str, Mapping[str, Any]], priority: int = 50) -> KernelProfile:
    data: dict[str, dict[str, Any]] = {sec: dict(vals) for sec, vals in tweaks.items()}
    data.setdefault("meta", {}).update({"name": name, "description": description, "suffix": suffix, "priority": priority})
    sections, explicit = coerce(data, Path(f"{name}.toml"))
    p = KernelProfile(Path(f"{name}.toml"), sections, explicit)
    apply_security_bundle(p)
    return p


def load_profile(path: Path) -> KernelProfile:
    try:
        with open(path, "rb") as fp:
            raw = tomllib.load(fp)
    except tomllib.TOMLDecodeError as e:
        raise ProfileError(f"{path.name}: TOML parse error: {e}") from e
    except OSError as e:
        raise ProfileError(f"{path.name}: {e}") from e
    sections, explicit = coerce(raw, path)
    p = KernelProfile(path, sections, explicit)
    apply_security_bundle(p)
    validate_profile(p)
    return p


def profile_dirs() -> list[Path]:
    dirs = [PROFILES_DIR]
    if USER_PROFILES_DIR != PROFILES_DIR:
        dirs.append(USER_PROFILES_DIR)
    return dirs


def discover_profiles() -> list[KernelProfile]:
    profiles: dict[str, KernelProfile] = {}
    for d in profile_dirs():
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.toml")):
            try:
                p = load_profile(f)
            except DuskyError as e:
                warn(str(e))
                continue
            profiles[p.name] = p
    return sorted(profiles.values(), key=lambda x: (x.priority, x.name))


def ensure_profiles_exist() -> list[KernelProfile]:
    profiles = discover_profiles()
    if profiles:
        return profiles
    warn(f"No profiles found in {', '.join(str(d) for d in profile_dirs())}")
    if interactive() and ask_yes("Write the profile template now?", True):
        do_write_defaults(argparse.Namespace())
        return discover_profiles()
    raise ProfileError("No profiles available; run --write-default-profiles")


def print_profile_table(profiles: Sequence[KernelProfile], facts: "HostFacts | None" = None) -> None:
    rec = recommend_profile(profiles, facts) if facts else None
    rows = []
    for i, p in enumerate(profiles, 1):
        s = p.sections
        mark = f" {C.GREEN}★{C.RESET}" if rec is p else ""
        rows.append([str(i), p.name + mark, s["release"]["channel"], s["cpu"]["arch"], s["scheduler"]["type"] + ("+" + s["scheduler"]["scx"][4:] if s["scheduler"]["scx"] != "none" else ""),
                     str(s["timing"]["hz"]), s["timing"]["preempt"], s["memory"]["footprint"], s["compiler"]["lto"], s["modules"]["mode"][:3]])
    table(["#", "name", "channel", "arch", "sched", "hz", "preempt", "footprint", "lto", "mods"], rows)
    if rec is not None:
        note(f"★ recommended for this host: {rec.name}")


def recommend_profile(profiles: Sequence[KernelProfile], facts: "HostFacts") -> KernelProfile | None:
    # Priority is owned by TOML, never by a hardcoded profile name.
    return min(profiles, key=lambda p: (p.priority, p.name)) if profiles else None


def select_profile(profiles: Sequence[KernelProfile], wanted: str | None, facts: "HostFacts | None" = None) -> KernelProfile:
    if wanted:
        for p in profiles:
            if p.name.lower() == wanted.lower():
                return p
        close = [p.name for p in profiles if wanted.lower() in p.name.lower()]
        hint = f" (did you mean: {', '.join(close)}?)" if close else ""
        raise ProfileError(f"No profile named '{wanted}'{hint}")
    if not interactive():
        raise ProfileError("Non-interactive session requires --profile NAME")
    rule("Select build profile")
    print_profile_table(profiles, facts)
    default = 1
    if facts is not None:
        rec = recommend_profile(profiles, facts)
        if rec is not None:
            default = list(profiles).index(rec) + 1
    return profiles[ask_index("Profile", len(profiles), default) - 1]


# ---------------------------------------------------------------------------------------------------
# CLI overrides + granular interactive wizard
# ---------------------------------------------------------------------------------------------------
@dataclass(slots=True)
class Overrides:
    cpu_arch: str | None = None
    modules_mode: str | None = None
    toolchain: str | None = None
    lto: str | None = None
    jobs: int | None = None
    pin: str | None = None
    channel: str | None = None
    scheduler: str | None = None
    scx: str | None = None
    headers: str | None = None
    footprint: str | None = None
    no_rust: bool = False
    allow_rc: bool = False

    @classmethod
    def from_env_and_args(cls, args: argparse.Namespace) -> Self:
        def env(name: str) -> str | None:
            v = os.environ.get(name, "").strip()
            return v or None

        jobs_env = env("DUSKY_JOBS")
        return cls(
            cpu_arch=getattr(args, "cpu_arch", None) or env("DUSKY_CPU_ARCH"),
            modules_mode=getattr(args, "modules_mode", None) or env("DUSKY_MODULES_MODE"),
            toolchain=getattr(args, "toolchain", None) or env("DUSKY_TOOLCHAIN"),
            lto=getattr(args, "lto", None) or env("DUSKY_LTO"),
            jobs=getattr(args, "jobs", None) if getattr(args, "jobs", None) is not None else (int(jobs_env) if jobs_env and re.fullmatch(r"-?\d+", jobs_env) else None),
            pin=getattr(args, "pin", None) or env("DUSKY_PIN"),
            channel=getattr(args, "channel", None) or env("DUSKY_CHANNEL"),
            scheduler=getattr(args, "scheduler", None) or env("DUSKY_SCHEDULER"),
            scx=getattr(args, "scx", None) or env("DUSKY_SCX"),
            headers=getattr(args, "headers", None) or env("DUSKY_HEADERS"),
            footprint=getattr(args, "footprint", None) or env("DUSKY_FOOTPRINT"),
            no_rust=bool(getattr(args, "no_rust", False)),
            allow_rc=bool(getattr(args, "allow_rc", False)),
        )


def apply_overrides(p: KernelProfile, o: Overrides) -> list[str]:
    diff: list[str] = []

    def put(sec: str, key: str, val: Any) -> None:
        old = p.g(sec, key)
        if old != val:
            p.set(sec, key, val)
            diff.append(f"{sec}.{key}: {old} -> {val}")

    if o.cpu_arch:
        put("cpu", "arch", o.cpu_arch)
    if o.modules_mode:
        put("modules", "mode", o.modules_mode)
    if o.toolchain:
        put("compiler", "toolchain", o.toolchain)
    if o.lto:
        put("compiler", "lto", o.lto)
    if o.jobs is not None:
        put("compiler", "jobs", o.jobs)
    if o.pin:
        put("release", "pin", o.pin)
    if o.channel:
        put("release", "channel", o.channel)
    if o.scheduler:
        put("scheduler", "type", o.scheduler)
    if o.scx:
        put("scheduler", "scx", o.scx)
    if o.headers:
        put("compiler", "headers", o.headers)
    if o.footprint:
        put("memory", "footprint", o.footprint)
    if o.no_rust:
        put("compiler", "rust", False)
    if o.allow_rc:
        put("release", "allow_rc", True)
    return diff


def field_relevant(s: Sections, sec: str, key: str) -> bool:
    """Skip questions whose answer cannot matter given earlier answers."""
    match (sec, key):
        case ("release", "allow_rc"):
            return s["release"]["channel"] == "mainline" or bool(s["release"]["pin"])
        case ("scheduler", "scx_flags"):
            return s["scheduler"]["scx"] != "none"
        case ("cache", "llc_aggr_tolerance") | ("cache", "llc_overaggr_pct"):
            return bool(s["cache"]["sched_cache"])
        case ("rseq", "slice_ext_nsec"):
            return bool(s["rseq"]["slice_extension"])
        case ("timing", "preempt_dynamic"):
            return s["timing"]["preempt"] != "rt"
        case ("memory", "thp_defrag") | ("memory", "thp_shmem"):
            return s["memory"]["thp"] != "never"
        case ("memory", "mglru_mask") | ("memory", "mglru_min_ttl_ms"):
            return bool(s["memory"]["mglru"])
        case ("memory", "zram_algo") | ("memory", "zram_size_pct") | ("memory", "zram_multi_comp"):
            return s["memory"]["swap_backend"] == "zram"
        case ("memory", "zram_recomp_algo"):
            return s["memory"]["swap_backend"] == "zram" and bool(s["memory"]["zram_multi_comp"])
        case ("memory", "zswap_compressor") | ("memory", "zswap_max_pool_pct"):
            return s["memory"]["swap_backend"] == "zswap"
        case ("memory", "numa_balancing") | ("memory", "nodes_shift"):
            return bool(s["memory"]["numa"])
        case ("memory", "ksm_run"):
            return bool(s["memory"]["ksm"])
        case ("memory", "slab_buckets"):
            return not s["memory"]["slub_tiny"]
        case ("memory", "trim_unused_ksyms"):
            return s["compiler"]["headers"] == "never"
        case ("compiler", "lto") | ("compiler", "kcfi") | ("compiler", "fdo"):
            return s["compiler"]["toolchain"] == "llvm"
        case ("compiler", "thinlto_cache") | ("compiler", "thinlto_cache_size_gb"):
            return s["compiler"]["toolchain"] == "llvm" and s["compiler"]["lto"] == "thin"
        case ("compiler", "fdo_profile_dir"):
            return s["compiler"]["fdo"] != "none"
        case ("security", "acknowledge_risk"):
            return s["security"]["profile"] == "extreme" or s["cpu"]["mitigations"] == "off"
        case ("modules", "modprobed_db_path") | ("modules", "allow_lsmod_fallback"):
            return bool(s["modules"]["modprobed_db"]) or s["modules"]["mode"] == "strict"
        case ("modules", "lmc_keep_extra"):
            return s["modules"]["mode"] == "expanded"
        case ("boot", "cmdline_extra") | ("boot", "write_entries"):
            return True
        case _:
            return True


def _fmt_value(spec: FieldSpec, val: Any) -> str:
    match spec.kind:
        case "bool":
            return "yes" if val else "no"
        case "list":
            return "[" + ", ".join(map(str, val)) + "]" if val else "[]"
        case "table":
            return ", ".join(f"{k}={v}" for k, v in val.items()) if val else "(none)"
        case _:
            return str(val) if str(val) != "" else "(empty)"


def _parse_answer(spec: FieldSpec, raw: str, current: Any) -> Any:
    match spec.kind:
        case "bool":
            low = raw.lower()
            if low in ("y", "yes", "true", "1", "on"):
                return True
            if low in ("n", "no", "false", "0", "off"):
                return False
            raise ValueError("answer y or n")
        case "int":
            if not re.fullmatch(r"-?\d+", raw):
                raise ValueError("enter an integer")
            val = int(raw)
            if spec.choices and val not in spec.choices:
                if 1 <= val <= len(spec.choices):
                    return spec.choices[val - 1]
                raise ValueError(f"allowed: {', '.join(map(str, spec.choices))}")
            if spec.minimum is not None and val < spec.minimum:
                raise ValueError(f"minimum is {spec.minimum}")
            if spec.maximum is not None and val > spec.maximum:
                raise ValueError(f"maximum is {spec.maximum}")
            return val
        case "list":
            if raw in ("-", "[]", "none"):
                return []
            return [x.strip() for x in raw.split(",") if x.strip()]
        case "table":
            out = dict(current)
            for item in (x.strip() for x in raw.split(",") if x.strip()):
                if item.startswith("-"):
                    out.pop(item[1:].removeprefix("CONFIG_"), None)
                    continue
                if "=" not in item:
                    raise ValueError("use SYMBOL=y|n|m|<int>|\"str\" or -SYMBOL to remove")
                sym, _, v = item.partition("=")
                sym = sym.strip().removeprefix("CONFIG_")
                v = v.strip()
                if v in ("y", "true"):
                    out[sym] = True
                elif v in ("n", "false"):
                    out[sym] = False
                elif re.fullmatch(r"-?\d+", v):
                    out[sym] = int(v)
                else:
                    out[sym] = v.strip('"')
            return out
        case _:
            if spec.choices:
                if raw in spec.choices:
                    return raw
                if raw.isdigit() and 1 <= int(raw) <= len(spec.choices):
                    return spec.choices[int(raw) - 1]
                raise ValueError(f"allowed: {', '.join(map(str, spec.choices))}")
            return raw


class WizardSignal(StrEnum):
    SKIP_SECTION = "s"
    ACCEPT_REST = "!"
    BACK = "b"
    MENU = "m"


def prompt_field(p: KernelProfile, sec: str, spec: FieldSpec, facts: "HostFacts | None") -> str | WizardSignal | None:
    """Ask one question. Returns a diff line, a WizardSignal, or None (kept default)."""
    current = p.g(sec, spec.key)
    say("")
    say(f"  {C.BOLD}{spec.help}{C.RESET}  {C.DIM}[{sec}.{spec.key}]{C.RESET}")
    say(f"    current: {C.CYAN}{_fmt_value(spec, current)}{C.RESET}")
    help_map = CHOICE_HELP.get((sec, spec.key), {})
    if spec.choices:
        for i, ch in enumerate(spec.choices, 1):
            ctx = help_map.get(str(ch), "")
            marker = "●" if ch == current else "○"
            say(f"    {marker} {i:>2}) {C.BOLD}{ch}{C.RESET}  {C.DIM}{ctx}{C.RESET}")
        if (sec, spec.key) == ("cpu", "arch") and facts is not None:
            note(f"    host: {facts.model} -> detected uarch '{facts.uarch or 'unknown'}', psABI v{facts.psabi_level}, {facts.threads} threads")
    elif spec.kind == "bool":
        say(f"    {C.DIM}y/n{C.RESET}")
    elif spec.kind == "int":
        rng = f"{spec.minimum if spec.minimum is not None else '-inf'}..{spec.maximum if spec.maximum is not None else 'inf'}"
        say(f"    {C.DIM}integer in {rng}{C.RESET}")
    elif spec.kind == "list":
        say(f"    {C.DIM}comma-separated values, '-' clears{C.RESET}")
    elif spec.kind == "table":
        say(f"    {C.DIM}SYMBOL=y|n|m|<int>|\"str\" comma-separated, -SYMBOL removes{C.RESET}")
    if (sec, spec.key) == ("memory", "footprint") and facts is not None:
        note(f"    host RAM: {facts.mem_gib:.1f} GiB -> suggested tier: {suggest_footprint(facts.mem_gib)}")
    while True:
        raw = ask("Enter keeps current | value | 'b' back | 's' skip section | '!' accept rest | 'm' jump to section", "")
        if raw == "":
            return None
        if raw in ("b", "back"):
            return WizardSignal.BACK
        if raw in ("s", "skip"):
            return WizardSignal.SKIP_SECTION
        if raw in ("!", "accept"):
            return WizardSignal.ACCEPT_REST
        if raw in ("m", "menu", "jump"):
            return WizardSignal.MENU
        try:
            val = _parse_answer(spec, raw, current)
        except ValueError as e:
            warn(f"Invalid: {e}")
            continue
        if val == current:
            note("    unchanged")
            return None
        p.set(sec, spec.key, val)
        line = f"{sec}.{spec.key}: {_fmt_value(spec, current)} -> {_fmt_value(spec, val)}"
        say(f"    {C.YELLOW}↳ {line}{C.RESET}")
        return line


def suggest_footprint(mem_gib: float) -> str:
    if mem_gib <= 3.5:
        return "embedded"
    if mem_gib <= 6:
        return "minimal"
    if mem_gib <= 8.5:
        return "lean"
    return "standard"


def run_wizard(p: KernelProfile, facts: "HostFacts | None", steps: Sequence[int] | None = None) -> list[str]:
    """Granular questionnaire over tunable knobs with per-question backward navigation ('b'), skip ('s'), jump ('m'), and accept ('!')."""
    order = list(steps) if steps else list(range(len(WIZARD_STEPS)))
    say("")
    info("Wizard controls: Enter = keep current, value = new value, 'b' = previous question, 's' = skip section, '!' = accept rest, 'm' = jump to section")

    active_questions: list[tuple[int, str, str, FieldSpec]] = []
    for step_idx in order:
        step = WIZARD_STEPS[step_idx]
        for sec, keys in step.groups:
            specs = {f.key: f for f in PROFILE_SPEC[sec]}
            for key in keys:
                spec = specs[key]
                if spec.wizard:
                    active_questions.append((step_idx, step.title, sec, spec))

    pos = 0
    history: list[tuple[int, str, str, Any, str | None]] = []
    diff_map: dict[str, str] = {}
    current_step_idx = -1

    while pos < len(active_questions):
        step_idx, step_title, sec, spec = active_questions[pos]
        if not field_relevant(p.sections, sec, spec.key):
            pos += 1
            continue

        if step_idx != current_step_idx:
            current_step_idx = step_idx
            rule(f"Wizard {step_idx + 1}/{len(WIZARD_STEPS)} · {step_title}")

        old_val = p.g(sec, spec.key)
        res = prompt_field(p, sec, spec, facts)

        if isinstance(res, WizardSignal):
            if res is WizardSignal.BACK:
                if history:
                    prev_pos, prev_sec, prev_key, prev_old_val, _ = history.pop()
                    p.set(prev_sec, prev_key, prev_old_val)
                    diff_map.pop(f"{prev_sec}.{prev_key}", None)
                    prev_spec = active_questions[prev_pos][3]
                    warn(f"Reverted {prev_sec}.{prev_key} to {_fmt_value(prev_spec, prev_old_val)} and stepped back.")
                    pos = prev_pos
                    current_step_idx = -1
                    continue
                else:
                    warn("Already at the very first question in the wizard.")
                    continue
            elif res is WizardSignal.SKIP_SECTION:
                cur_s = step_idx
                while pos < len(active_questions) and active_questions[pos][0] == cur_s:
                    pos += 1
                continue
            elif res is WizardSignal.ACCEPT_REST:
                info("Accepting profile defaults for all remaining sections")
                break
            elif res is WizardSignal.MENU:
                say("")
                say(f"{C.ACCENT}  Wizard sections:{C.RESET}")
                for i, st in enumerate(WIZARD_STEPS, 1):
                    marker = "▶" if (i - 1) == step_idx else " "
                    say(f"  {marker} {i:>2}) {st.title}")
                target_step = ask_index("Jump to section", len(WIZARD_STEPS), step_idx + 1) - 1
                found = False
                for idx, (s_idx, _, _, _) in enumerate(active_questions):
                    if s_idx == target_step:
                        pos = idx
                        current_step_idx = -1
                        found = True
                        break
                if found:
                    continue
                else:
                    warn(f"Section '{WIZARD_STEPS[target_step].title}' has no active questions in this pass.")
                    continue

        diff_line = res if isinstance(res, str) else None
        if diff_line:
            diff_map[f"{sec}.{spec.key}"] = diff_line
        history.append((pos, sec, spec.key, old_val, diff_line))
        pos += 1

    return list(diff_map.values())


def wizard_review_loop(p: KernelProfile, facts: "HostFacts | None", diff: list[str], *, force: bool) -> list[str]:
    """Validate after the wizard; let the user revisit an offending step instead of aborting."""
    while True:
        notes = normalize_profile(p)
        for n in notes:
            note(f"auto-adjusted {n}")
        try:
            validate_profile(p)
            cross_validate(p, facts, force=force)
            return diff + notes
        except ProfileError as e:
            err(str(e))
            if not interactive():
                raise
            raw = ask(f"Revisit wizard step number (1-{len(WIZARD_STEPS)}) or 'q' to abort", "q")
            if raw.lower() == "q":
                raise AbortError("Aborted in wizard") from e
            if raw.isdigit() and 1 <= int(raw) <= len(WIZARD_STEPS):
                diff.extend(run_wizard(p, facts, [int(raw) - 1]))


def offer_save_profile(p: KernelProfile) -> None:
    if not interactive() or ASSUME_YES:
        return
    if not ask_yes("Save these overrides as a new profile TOML?", False):
        return
    name = ask("New profile name", f"{p.name}_custom")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        warn("Invalid profile name; not saved")
        return
    suffix = ask("LOCALVERSION suffix (pkgbase becomes linux-<suffix>)", f"dusky-{name.replace('_', '-')}"[:40])
    sections = json.loads(json.dumps(p.sections))
    sections["meta"]["name"] = name
    sections["meta"]["suffix"] = suffix.strip("-")
    sections["meta"]["description"] = f"Derived from {p.name} via wizard"
    dest_dir = USER_PROFILES_DIR if not os.access(PROFILES_DIR, os.W_OK) else PROFILES_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{name}.toml"
    dest.write_text(render_profile_toml(sections, header=f"derived from {p.name}"), encoding="utf-8")
    ok(f"Saved profile: {dest}")


def configure_profile_interactively(p: KernelProfile, facts: "HostFacts | None", args: argparse.Namespace) -> list[str]:
    """Apply defaults and overrides; the granular wizard is explicitly requested."""
    cli_diff = apply_overrides(p, Overrides.from_env_and_args(args))
    diff = list(cli_diff)
    if getattr(args, "wizard", False):
        diff.extend(run_wizard(p, facts))
        diff = wizard_review_loop(p, facts, diff, force=bool(getattr(args, "force", False)))
        if diff:
            offer_save_profile(p)
        return diff
    notes = normalize_profile(p)
    validate_profile(p)
    cross_validate(p, facts, force=bool(getattr(args, "force", False)))
    return diff + notes

# ---------------------------------------------------------------------------------------------------
# Process execution: every child runs in its own process group so aborts never leak make/clang trees
# ---------------------------------------------------------------------------------------------------
_CHILD_LOCK: Final = threading.Lock()
_CHILD_PGIDS: Final[set[int]] = set()
_ABORT: Final = threading.Event()


def terminate_process_group(pgid: int, grace: float = 2.0) -> None:
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _reap_all() -> None:
    with _CHILD_LOCK:
        pgids = list(_CHILD_PGIDS)
        _CHILD_PGIDS.clear()
    for pgid in pgids:
        terminate_process_group(pgid)


def _register(proc: subprocess.Popen[Any], own_group: bool) -> int | None:
    if not own_group:
        return None
    with _CHILD_LOCK:
        _CHILD_PGIDS.add(proc.pid)
    return proc.pid


def _unregister(pgid: int | None) -> None:
    if pgid is not None:
        with _CHILD_LOCK:
            _CHILD_PGIDS.discard(pgid)


def _on_signal(signum: int, _frame: Any) -> None:
    _ABORT.set()
    _reap_all()
    sys.stdout.write(C.SHOW)
    sys.stdout.flush()
    if signum == signal.SIGINT:
        raise KeyboardInterrupt
    raise SystemExit(128 + signum)


def install_signal_handlers() -> None:
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass


def check_abort() -> None:
    if _ABORT.is_set():
        raise AbortError("Aborted")


def run(cmd: Sequence[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None, check: bool = True,
        timeout: float | None = None, capture: bool = True, own_group: bool = True, stdin_null: bool = True) -> subprocess.CompletedProcess[str]:
    check_abort()
    debug("run: " + shlex.join(cmd))
    JOURNAL.write("$ " + shlex.join(cmd))
    pgid: int | None = None
    try:
        proc = subprocess.Popen(list(cmd), cwd=cwd, env=dict(env) if env is not None else None, text=True, encoding="utf-8", errors="replace",
                                stdin=subprocess.DEVNULL if stdin_null else None,
                                stdout=subprocess.PIPE if capture else None, stderr=subprocess.STDOUT if capture else None,
                                start_new_session=own_group)
    except FileNotFoundError as e:
        raise DependencyError(f"Executable not found: {cmd[0]}") from e
    pgid = _register(proc, own_group)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        if pgid is not None:
            terminate_process_group(pgid)
        else:
            proc.kill()
        proc.wait()
        raise BuildError(f"Command timed out after {timeout}s: {shlex.join(cmd)}") from e
    finally:
        if proc.poll() is None:
            if pgid is not None:
                terminate_process_group(pgid)
            else:
                proc.kill()
            proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()
        _unregister(pgid)
    out = out or ""
    if out and capture:
        JOURNAL.write(out.rstrip())
    if check and proc.returncode != 0:
        tail = "\n".join(out.strip().splitlines()[-25:])
        raise BuildError(f"Command failed (exit {proc.returncode}): {shlex.join(cmd)}\n{tail}")
    return subprocess.CompletedProcess(list(cmd), proc.returncode, out, "")


def run_stream(cmd: Sequence[str], *, cwd: Path | None = None, env: Mapping[str, str] | None = None,
               on_line: Callable[[str], None] | None = None) -> int:
    check_abort()
    JOURNAL.write("$ " + shlex.join(cmd))
    proc = subprocess.Popen(list(cmd), cwd=cwd, env=dict(env) if env is not None else None, text=True, encoding="utf-8", errors="replace",
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True, bufsize=1)
    pgid = _register(proc, True)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            text = line.rstrip("\r\n")
            JOURNAL.write(text)
            if on_line is not None:
                on_line(text)
            if _ABORT.is_set():
                terminate_process_group(proc.pid)
                break
        proc.stdout.close()
        return proc.wait()
    finally:
        if proc.poll() is None:
            terminate_process_group(proc.pid)
            proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()
        _unregister(pgid)


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def tool_version(cmd: Sequence[str]) -> str:
    try:
        cp = run(list(cmd), check=False, timeout=20)
    except DuskyError:
        return ""
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", cp.stdout or "")
    return m.group(1) if m else ""


def version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


class Privilege:
    """sudo/doas/run0 front-end with a credential keep-alive during long phases."""

    def __init__(self) -> None:
        self.tool: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def detect(self) -> str:
        if self.tool:
            return self.tool
        if os.geteuid() == 0:
            self.tool = "root"
        elif have("sudo"):
            self.tool = "sudo"
        elif have("doas"):
            self.tool = "doas"
        elif have("run0"):
            self.tool = "run0"
        else:
            raise DependencyError("No privilege escalation tool found (sudo, doas or run0)")
        return self.tool

    def ensure(self) -> None:
        tool = self.detect()
        if tool != "sudo" or (self._thread is not None and self._thread.is_alive()):
            return
        info("Privileged steps ahead (sudo)")
        try:
            subprocess.run(["sudo", "-v"], check=True)
        except (subprocess.CalledProcessError, OSError) as e:
            raise DependencyError("sudo credentials required") from e
        self._stop.clear()
        self._thread = threading.Thread(target=self._keepalive, name="sudo-keepalive", daemon=True)
        self._thread.start()

    def _keepalive(self) -> None:
        while not self._stop.wait(50):
            subprocess.run(["sudo", "-nv"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def argv(self, cmd: Sequence[str]) -> list[str]:
        match self.detect():
            case "root":
                return list(cmd)
            case "sudo":
                return ["sudo", *cmd]
            case "doas":
                return ["doas", *cmd]
            case _:
                return ["run0", *cmd]

    def run(self, cmd: Sequence[str], *, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
        self.ensure()
        return run(self.argv(cmd), check=check, capture=capture, own_group=False, stdin_null=False)

    def write_files(self, files: Mapping[Path, tuple[str, str]]) -> None:
        """Install {dest: (content, mode)} with one privileged shell invocation."""
        if not files:
            return
        self.ensure()
        with tempfile.TemporaryDirectory(prefix="dusky-stage-") as tmp:
            staged: list[tuple[Path, Path, str]] = []
            for i, (dest, (content, mode)) in enumerate(files.items()):
                src = Path(tmp) / f"{i:03d}-{dest.name}"
                src.write_text(content, encoding="utf-8")
                staged.append((src, dest, mode))
            script = "set -e\n" + "\n".join(f"install -Dm{mode} {shlex.quote(str(src))} {shlex.quote(str(dest))}" for src, dest, mode in staged) + "\n"
            (Path(tmp) / "install.sh").write_text(script, encoding="utf-8")
            os.chmod(Path(tmp), 0o755)
            for src, _, _ in staged:
                os.chmod(src, 0o644)
            self.run(["sh", str(Path(tmp) / "install.sh")])

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            if not self._thread.is_alive():
                self._thread = None



PRIV: Final = Privilege()


# ---------------------------------------------------------------------------------------------------
# Live build monitor
# ---------------------------------------------------------------------------------------------------
_KBUILD_STEP_RE: Final = re.compile(r"^\s{2}([A-Z][A-Z0-9_]+)(?:\s\[[MA]\])?\s+(\S.*)$")


class Live:
    SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label: str, expected_steps: int | None, expected_seconds: float | None, lto: str = "thin") -> None:
        self.label = label
        self.expected_steps = expected_steps or 8380
        self.expected_seconds = expected_seconds
        self.lto = lto
        self.steps = 0
        self.phase = "configure"
        self.last = ""
        self.errors: list[str] = []
        self.tail: collections.deque[str] = collections.deque(maxlen=40)
        self.start = time.monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick = 0
        self.tty = sys.stdout.isatty()

        # Adaptive throughput tracking and smoothed ETA
        self._samples: collections.deque[tuple[float, int]] = collections.deque(maxlen=60)
        self._samples.append((self.start, 0))
        self._smoothed_eta: float | None = None
        self._link_phase_start: float | None = None
        self._orig_winch: Any = None

    def feed(self, line: str) -> None:
        self.tail.append(line)
        m = _KBUILD_STEP_RE.match(line)
        if m:
            self.steps += 1
            tag, target = m.group(1), m.group(2)
            self.last = f"{tag} {target}"[-70:]
            now = time.monotonic()
            if not self._samples or (now - self._samples[-1][0]) >= 0.25:
                self._samples.append((now, self.steps))

            if tag in ("CC", "RUSTC", "AS"):
                self.phase = "compile"
                # Dynamic step count expansion: if compile steps exceed expected_steps,
                # extend expected_steps so progress never freezes or causes retrograde ETA
                if self.steps >= self.expected_steps - 10:
                    self.expected_steps = max(self.expected_steps + 150, int(self.steps * 1.05))
            elif tag in ("LTO", "LD") and ("vmlinux" in target):
                self.phase = "link vmlinux" + (" (LTO)" if tag == "LTO" or "vmlinux.o" in target else "")
                if self._link_phase_start is None:
                    self._link_phase_start = now
            elif tag == "BTF":
                self.phase = "BTF generation" if ("vmlinux" in target or not target.endswith(".ko")) else "module BTF"
            elif tag == "MODPOST":
                self.phase = "modpost"
            elif tag in ("INSTALL", "STRIP", "SIGN", "ZSTD", "XZ", "GZIP") and ("modules" in target or tag == "DEPMOD"):
                self.phase = "modules_install"
        elif line.startswith("==>"):
            low_line = line.lower()
            if "package" in low_line or "fakeroot" in low_line or "compress" in low_line:
                self.phase = "packaging: " + line[4:40].strip()
        low = line.lower()
        if ("error:" in low or " error " in low or low.startswith("make: ***") or "undefined reference" in low or "Error " in line) and len(self.errors) < 40:
            self.errors.append(line.strip()[:200])

    def _calc_eta(self, elapsed: float) -> float | None:
        now = time.monotonic()
        if self.phase.startswith("link vmlinux"):
            link_started = self._link_phase_start or now
            link_spent = now - link_started
            link_total = 150.0 if self.lto == "full" else (75.0 if self.lto == "thin" else 25.0)
            remaining_link = max(8.0, link_total - link_spent)
            target_eta = remaining_link + 40.0
        elif self.phase == "BTF generation":
            target_eta = 35.0
        elif self.phase in ("modpost", "modules_install", "module BTF"):
            target_eta = 25.0
        elif self.phase.startswith("packaging"):
            target_eta = 15.0
        elif self.steps < 30:
            if self.expected_seconds and self.expected_seconds > elapsed:
                target_eta = self.expected_seconds - elapsed
            else:
                return None
        else:
            # Active compilation phase: calculate measured step throughput
            recent_rate = 0.0
            if len(self._samples) >= 3:
                t0, s0 = self._samples[0]
                t1, s1 = self._samples[-1]
                dt = t1 - t0
                ds = s1 - s0
                if dt >= 5.0 and ds > 0:
                    recent_rate = ds / dt

            overall_rate = self.steps / max(1.0, elapsed)
            effective_rate = (0.70 * recent_rate + 0.30 * overall_rate) if recent_rate > 0 else overall_rate

            remaining_compile_steps = max(0, self.expected_steps - self.steps)
            remaining_compile_time = remaining_compile_steps / max(0.2, effective_rate)

            post_overhead = 180.0 if self.lto == "full" else (90.0 if self.lto == "thin" else 45.0)
            raw_eta = remaining_compile_time + post_overhead

            # Smoothly blend with historical/heuristic estimate during initial warmup (first 90s)
            if elapsed < 90.0 and self.expected_seconds and self.expected_seconds > elapsed:
                alpha = elapsed / 90.0
                baseline_eta = max(15.0, self.expected_seconds - elapsed)
                target_eta = alpha * raw_eta + (1.0 - alpha) * baseline_eta
            else:
                target_eta = raw_eta

        # Exponential moving average to eliminate jitter while keeping responsiveness
        if self._smoothed_eta is None:
            self._smoothed_eta = target_eta
        else:
            self._smoothed_eta = 0.15 * target_eta + 0.85 * self._smoothed_eta

        return max(1.0, self._smoothed_eta)

    def _status(self) -> str:
        elapsed = time.monotonic() - self.start
        spin = f"{C.ACCENT}{self.SPIN[self._tick % len(self.SPIN)]}{C.RESET}"
        eta = self._calc_eta(elapsed)
        eta_part = f"ETA ~{fmt_duration(eta)}" if eta is not None and eta > 0 else ""

        w = term_width()
        prefix = f"{spin} {self.label} │ {fmt_duration(elapsed)} │ {self.steps:,} steps"
        avail = w - 1 - visible_len(prefix)

        extra_parts: list[str] = []
        if eta_part and avail > (visible_len(eta_part) + 4):
            extra_parts.append(eta_part)
            avail -= (visible_len(eta_part) + 3)

        phase_str = self.phase
        if avail > 10:
            if visible_len(phase_str) > avail - 3:
                phase_str = phase_str[:max(4, avail - 5)] + ".."
            extra_parts.append(phase_str)
            avail -= (visible_len(phase_str) + 3)

        last_str = self.last
        if last_str and avail >= 12:
            display_last = last_str[-avail:] if len(last_str) > avail else last_str
            extra_parts.append(C.DIM + display_last + C.RESET)

        parts = [prefix] + extra_parts
        s = " │ ".join(parts)

        # Safety bound: strictly ensure visible length never exceeds w - 1 to prevent line-wrapping duplicate lines
        while len(parts) > 1 and visible_len(s) > w - 1:
            parts.pop()
            s = " │ ".join(parts)

        if visible_len(s) > w - 1:
            s = truncate_ansi(s, w - 1)

        return s

    def _loop(self) -> None:
        while not self._stop.wait(0.5):
            self._tick += 1
            with _OUT_LOCK:
                if self.tty:
                    sys.stdout.write("\r" + C.CLEAR_EOL + self._status())
                    sys.stdout.flush()

    def emit(self, text: str) -> None:
        if self.tty:
            sys.stdout.write("\r" + C.CLEAR_EOL + text + "\n" + self._status())
        else:
            sys.stdout.write(text + "\n")
        sys.stdout.flush()

    def __enter__(self) -> Self:
        global _LIVE
        _LIVE = self
        if self.tty:
            sys.stdout.write(C.HIDE)
            try:
                if threading.current_thread() is threading.main_thread() and hasattr(signal, "SIGWINCH"):
                    def _sigwinch_handler(sig: int, frame: Any) -> None:
                        sys.stdout.write("\r" + C.CLEAR_EOL)
                        sys.stdout.flush()
                    self._orig_winch = signal.signal(signal.SIGWINCH, _sigwinch_handler)
            except (ValueError, OSError):
                pass
        self._thread = threading.Thread(target=self._loop, name="live-status", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        global _LIVE
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        _LIVE = None
        if self.tty:
            if self._orig_winch is not None:
                try:
                    signal.signal(signal.SIGWINCH, self._orig_winch)
                except (ValueError, OSError):
                    pass
            sys.stdout.write("\r" + C.CLEAR_EOL + C.SHOW)
            sys.stdout.flush()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.start


def load_history() -> list[Json]:
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def record_history(entry: Json) -> None:
    hist = load_history()
    hist.append(entry)
    hist = hist[-200:]
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(hist, indent=1), encoding="utf-8")
    except OSError:
        pass


def history_estimate(profile: str, lto: str, current_jobs: int = 0, is_clean: bool = True) -> tuple[int, float]:
    """Estimate expected steps and build duration (in seconds) based on history and host hardware.

    Scales historical durations to current job/core count via Amdahl's law (parallel compile vs serial link/BTF),
    differentiates clean full builds from incremental rebuilds, and provides an accurate hardware-derived
    fallback for fresh installations with no previous history.
    """
    hist = [e for e in reversed(load_history()) if e.get("success")]
    jobs = max(1, current_jobs or os.cpu_count() or 4)

    # Segregate history by build type (full >= 5000 steps vs incremental < 5000 steps)
    target_hist = [e for e in hist if (int(e.get("steps") or 0) >= 5000 if is_clean else int(e.get("steps") or 0) < 5000)]
    if not target_hist:
        target_hist = hist

    matched_entry: Json | None = None
    for e in target_hist:
        if e.get("profile") == profile and e.get("lto") == lto:
            matched_entry = e
            break
    if not matched_entry:
        for e in target_hist:
            if e.get("lto") == lto:
                matched_entry = e
                break
    if not matched_entry and target_hist:
        matched_entry = target_hist[0]

    if matched_entry:
        h_steps = int(matched_entry.get("steps") or 0)
        h_dur = float(matched_entry.get("duration") or 0.0)
        h_jobs = int(matched_entry.get("jobs") or jobs)

        # Scale duration to current job count using Amdahl's Law:
        # ~80% of kernel build is parallel compilations, ~20% is serialized linking/BTF/packaging
        if h_jobs > 0 and jobs > 0 and h_dur > 0:
            parallel = h_dur * 0.80 * (h_jobs / jobs)
            serial = h_dur * 0.20
            scaled_dur = max(20.0, parallel + serial)
        else:
            scaled_dur = h_dur

        steps = h_steps if h_steps > 100 else (8380 if is_clean else 2000)
        return steps, round(scaled_dur, 1)

    # Fallback for fresh machine / no history: hardware-calibrated heuristic
    # Full build: ~8,380 steps. Incremental: ~2,000 steps.
    base_steps = 8380 if is_clean else 2000
    parallel_sec = (base_steps * 0.95) / (jobs * 0.65)
    link_sec = 160.0 if lto == "full" else (85.0 if lto == "thin" else 40.0)
    fallback_dur = max(30.0, parallel_sec + link_sec)
    return base_steps, round(fallback_dur, 1)



# ---------------------------------------------------------------------------------------------------
# Host telemetry
# ---------------------------------------------------------------------------------------------------
def _read(path: str | Path, default: str = "") -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return default


def _cpuinfo_flags() -> frozenset[str]:
    flags = [set(line.split(":", 1)[1].split()) for line in _read("/proc/cpuinfo").splitlines() if line.startswith("flags")]
    return frozenset(set.intersection(*flags)) if flags else frozenset()


def psabi_level(flags: frozenset[str]) -> int:
    v2 = {"cx16", "lahf_lm", "popcnt", "sse4_1", "sse4_2", "ssse3"}
    v3 = {"avx", "avx2", "bmi1", "bmi2", "f16c", "fma", "abm", "movbe", "xsave"}
    v4 = {"avx512f", "avx512bw", "avx512cd", "avx512dq", "avx512vl"}
    if v4 <= flags and v3 <= flags and v2 <= flags:
        return 4
    if v3 <= flags and v2 <= flags:
        return 3
    if v2 <= flags:
        return 2
    return 1


def detect_native_uarch() -> str:
    """Ask the compiler what -march=native resolves to; map onto our vocabulary."""
    candidates: list[str] = []
    if have("clang"):
        cp = run(["clang", "-march=native", "-E", "-", "-###"], check=False, timeout=30)
        m = re.search(r'"-target-cpu"\s+"([^"]+)"', cp.stdout or "")
        if m:
            candidates.append(m.group(1).lower())
    if have("gcc"):
        cp = run(["gcc", "-march=native", "-Q", "--help=target"], check=False, timeout=30)
        m = re.search(r"-march=\s+(\S+)", cp.stdout or "")
        if m:
            candidates.append(m.group(1).lower())
    aliases = {"x86-64": "generic", "x86-64-v2": "generic_v2", "x86-64-v3": "generic_v3", "x86-64-v4": "generic_v4"}
    return aliases.get(candidates[0], candidates[0]) if candidates else ""


def _sys_llc() -> tuple[int, int]:
    domains: set[str] = set()
    size_kib = 0
    base = Path("/sys/devices/system/cpu")
    for cpu in base.glob("cpu[0-9]*"):
        for idx in (cpu / "cache").glob("index*"):
            if _read(idx / "level").strip() == "3":
                domains.add(_read(idx / "shared_cpu_list").strip())
                m = re.match(r"(\d+)K", _read(idx / "size").strip())
                if m:
                    size_kib = max(size_kib, int(m.group(1)))
    return len(domains), size_kib


def _gpu_vendors() -> tuple[str, ...]:
    vendors: list[str] = []
    for dev in Path("/sys/bus/pci/devices").glob("*"):
        cls = _read(dev / "class").strip()
        if not cls.startswith("0x03"):
            continue
        vid = _read(dev / "vendor").strip().lower()
        name = {"0x1002": "amd", "0x10de": "nvidia", "0x8086": "intel", "0x1af4": "virtio", "0x15ad": "vmware", "0x1b36": "qxl", "0x1234": "bochs"}.get(vid, vid)
        if name not in vendors:
            vendors.append(name)
    return tuple(vendors)


def _mounted_filesystems() -> tuple[tuple[str, ...], str]:
    fstypes: list[str] = []
    root_fs = ""
    for line in _read("/proc/mounts").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mnt, fstype = parts[1], parts[2]
        if fstype in ("ext4", "btrfs", "xfs", "f2fs", "vfat", "exfat", "ntfs", "ntfs3", "virtiofs", "fuse", "fuseblk", "overlay", "nfs", "nfs4", "cifs", "smb3", "erofs", "zfs", "bcachefs"):
            if fstype not in fstypes:
                fstypes.append(fstype)
            if mnt == "/":
                root_fs = fstype
    for line in _read("/etc/fstab").splitlines():
        parts = line.split()
        if len(parts) >= 3 and not line.lstrip().startswith("#") and parts[2] not in fstypes and parts[2] in ("ext4", "btrfs", "xfs", "f2fs", "vfat", "exfat", "ntfs", "ntfs3", "virtiofs", "nfs", "nfs4", "cifs"):
            fstypes.append(parts[2])
    return tuple(fstypes), root_fs


def _dkms_modules() -> tuple[str, ...]:
    mods: list[str] = []
    dkms_dir = Path("/var/lib/dkms")
    if dkms_dir.is_dir():
        mods.extend(sorted(p.name for p in dkms_dir.iterdir() if p.is_dir()))
    if have("pacman"):
        cp = run(["pacman", "-Qq"], check=False, timeout=30)
        for line in (cp.stdout or "").splitlines():
            if line.endswith("-dkms") and line not in mods:
                mods.append(line)
    return tuple(mods)


def _bootloaders() -> tuple[tuple[str, ...], str, str]:
    found: list[str] = []
    esp = xbootldr = ""
    if have("bootctl"):
        esp_out = (run(["bootctl", "-p"], check=False, timeout=20).stdout or "").strip()
        xbootldr_out = (run(["bootctl", "-x"], check=False, timeout=20).stdout or "").strip()
        if esp_out.startswith("/"):
            esp = esp_out
        if xbootldr_out.startswith("/"):
            xbootldr = xbootldr_out
        cp = run(["bootctl", "is-installed"], check=False, timeout=20)
        has_sdboot_efivars = False
        try:
            efivars = Path("/sys/firmware/efi/efivars")
            if efivars.is_dir():
                has_sdboot_efivars = any(efivars.glob("LoaderInfo-*")) or any(efivars.glob("LoaderEntry*"))
        except Exception:
            pass
        if cp.returncode == 0 or has_sdboot_efivars:
            found.append("systemd-boot")
    if "systemd-boot" not in found:
        for cand in ("/boot/loader/entries", "/efi/loader/entries", "/boot/efi/loader/entries"):
            if Path(cand).is_dir():
                found.append("systemd-boot")
                esp = esp or str(Path(cand).parent.parent)
                break
    if Path("/boot/grub/grub.cfg").is_file() or (have("grub-mkconfig") and Path("/boot/grub").is_dir()):
        found.append("grub")
    for cand in ("/boot/EFI/refind/refind.conf", "/efi/EFI/refind/refind.conf", "/boot/efi/EFI/refind/refind.conf"):
        if Path(cand).is_file():
            found.append("refind")
            break
    for cand in ("/boot/limine.conf", "/boot/EFI/limine/limine.conf", "/efi/limine.conf"):
        if Path(cand).is_file():
            found.append("limine")
            break
    return tuple(found), esp, xbootldr


def _tool_versions() -> dict[str, str]:
    probes = {"clang": ["clang", "--version"], "ld.lld": ["ld.lld", "--version"], "llvm-ar": ["llvm-ar", "--version"], "gcc": ["gcc", "--version"],
              "rustc": ["rustc", "--version"], "bindgen": ["bindgen", "--version"], "pahole": ["pahole", "--version"], "make": ["make", "--version"],
              "makepkg": ["makepkg", "--version"], "mkinitcpio": ["mkinitcpio", "--version"], "perf": ["perf", "--version"],
              "create_llvm_prof": ["create_llvm_prof", "--version"], "ccache": ["ccache", "--version"], "gpg": ["gpg", "--version"], "curl": ["curl", "--version"], "aria2c": ["aria2c", "--version"]}
    out: dict[str, str] = {}
    for name, cmd in probes.items():
        out[name] = tool_version(cmd) or ("present" if have(name) else "") if have(name) else ""
    for name in ("modprobed-db", "scx_loader", "scx_lavd", "scx_bpfland", "zram-generator", "dkms", "kernel-install", "grub-mkconfig", "bootctl", "limine-update", "mkrlconf"):
        out[name] = "present" if (have(name) or Path(f"/usr/lib/systemd/system-generators/{name}").exists()) else ""
    return out


@dataclass(slots=True, kw_only=True)
class HostFacts:
    vendor: str
    model: str
    flags: frozenset[str]
    threads: int
    cores: int
    llc_domains: int
    llc_kib: int
    mem_gib: float
    swap_gib: float
    disk_swap: bool
    numa_nodes: int
    virt: str
    gpus: tuple[str, ...]
    psabi_level: int
    uarch: str
    kernel: str
    cmdline: str
    filesystems: tuple[str, ...]
    root_fs: str
    root_luks: bool
    has_nvme: bool
    rotational: bool
    battery: bool
    dkms_modules: tuple[str, ...]
    bootloaders: tuple[str, ...]
    esp: str
    xbootldr: str
    tools: dict[str, str]
    sched_ext_live: bool
    initrd_compression: str
    microcode_hook: bool

    def as_json(self) -> Json:
        d = {k: getattr(self, k) for k in self.__slots__}
        d["flags"] = sorted(self.flags)
        return d


def possible_cpu_count() -> int:
    ranges = _read("/sys/devices/system/cpu/possible").strip().split(",")
    slots = 0
    for item in ranges:
        if not item:
            continue
        lo, _, hi = item.partition("-")
        slots = max(slots, int(hi or lo) + 1)
    return slots or os.cpu_count() or 1


@functools.cache
def host_facts() -> HostFacts:
    cpuinfo = _read("/proc/cpuinfo")
    vendor_id = re.search(r"^vendor_id\s*:\s*(\S+)", cpuinfo, re.M)
    vendor = {"AuthenticAMD": "amd", "GenuineIntel": "intel"}.get(vendor_id.group(1) if vendor_id else "", "other")
    model_m = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.M)
    cores = len({(m.group(1), m.group(2)) for m in re.finditer(r"physical id\s*:\s*(\d+)\n(?:.*\n)*?core id\s*:\s*(\d+)", cpuinfo)}) or (os.cpu_count() or 1)
    meminfo = _read("/proc/meminfo")

    def mem_kib(key: str) -> int:
        m = re.search(rf"^{key}:\s+(\d+)", meminfo, re.M)
        return int(m.group(1)) if m else 0

    swaps = [line.split()[0] for line in _read("/proc/swaps").splitlines()[1:] if line.strip()]
    virt = "none"
    if have("systemd-detect-virt"):
        cp = run(["systemd-detect-virt", "--vm"], check=False, timeout=10)
        virt = (cp.stdout or "none").strip() if cp.returncode == 0 else "none"
    llc_domains, llc_kib = _sys_llc()
    fstypes, root_fs = _mounted_filesystems()
    rotational = any(_read(p).strip() == "1" for p in Path("/sys/block").glob("sd*/queue/rotational"))
    flags = _cpuinfo_flags()
    mkconf = _read("/etc/mkinitcpio.conf")
    comp_m = re.search(r'^COMPRESSION="?(\w+)"?', mkconf, re.M)
    hooks_m = re.search(r"^HOOKS=\((.*)\)", mkconf, re.M)
    hooks = hooks_m.group(1).split() if hooks_m else []
    bootloaders, esp, xbootldr = _bootloaders()
    root_luks = any(line.split()[0].startswith("/dev/mapper/") for line in _read("/proc/mounts").splitlines() if len(line.split()) > 1 and line.split()[1] == "/")
    return HostFacts(
        vendor=vendor, model=(model_m.group(1).strip() if model_m else platform.processor() or "unknown"), flags=flags,
        threads=possible_cpu_count(), cores=cores, llc_domains=llc_domains, llc_kib=llc_kib,
        mem_gib=mem_kib("MemTotal") / 1048576.0, swap_gib=mem_kib("SwapTotal") / 1048576.0,
        disk_swap=any(not s.startswith("/dev/zram") for s in swaps),
        numa_nodes=len(list(Path("/sys/devices/system/node").glob("node[0-9]*"))) or 1, virt=virt, gpus=_gpu_vendors(),
        psabi_level=psabi_level(flags), uarch=detect_native_uarch(), kernel=os.uname().release, cmdline=_read("/proc/cmdline").strip(),
        filesystems=fstypes, root_fs=root_fs, root_luks=root_luks, has_nvme=any(Path("/sys/block").glob("nvme*")), rotational=rotational,
        battery=any(Path("/sys/class/power_supply").glob("BAT*")), dkms_modules=_dkms_modules(), bootloaders=bootloaders, esp=esp, xbootldr=xbootldr,
        tools=_tool_versions(), sched_ext_live=Path("/sys/kernel/sched_ext").is_dir(),
        initrd_compression=(comp_m.group(1) if comp_m else "zstd"), microcode_hook="microcode" in hooks,
    )


_VIRT_GPUS: Final = frozenset({"virtio", "qxl", "bochs", "vmware"})


def _virt_guest(facts: HostFacts) -> tuple[bool, str]:
    """Detect a VM guest via detect-virt OR hypervisor CPU flag OR virt GPU.

    Returns (detected, reason); reason is audit-friendly, e.g. "kvm" or
    "none+hypervisor-flag+virtio-gpu". "hypervisor" alone is ~never set on
    true bare metal; GPU IDs are PCI display-class virt vendors only.
    """
    if facts.virt != "none":
        return True, facts.virt
    sigs: list[str] = []
    if "hypervisor" in facts.flags:
        sigs.append("hypervisor-flag")
    vgpus = sorted(set(facts.gpus) & _VIRT_GPUS)
    if vgpus:
        sigs.append("+".join(vgpus) + "-gpu")
    if sigs:
        return True, "none+" + "+".join(sigs)
    return False, "none"


def _is_virt_target(p: KernelProfile, f: HostFacts) -> tuple[bool, str]:
    if "vm" in p.sections.get("meta", {}).get("tags", []):
        return True, "vm_guest"
    return _virt_guest(f)


def resolve_profile_manifest(p: KernelProfile) -> Path | None:
    value = p.g("meta", "manifest_path")
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = p.path.parent / path
    if not path.is_file():
        raise ProfileError(f"Target manifest missing: {path}; re-import the hardware bundle")
    return path


def target_facts_for_profile(p: KernelProfile, host: HostFacts) -> HostFacts:
    path = resolve_profile_manifest(p)
    if path is None:
        return host
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format") != "dusky_bundle_v3":
            raise ValueError("re-export the target using this script (bundle v3 required)")
        values = {key: data[key] for key in HostFacts.__slots__}
        for key in ("threads", "cores", "llc_domains", "llc_kib", "numa_nodes", "psabi_level"):
            if type(values[key]) is not int or values[key] < (0 if key in ("llc_kib", "llc_domains") else 1):
                raise ValueError(f"invalid target {key}")
        if values["psabi_level"] not in (1, 2, 3, 4):
            raise ValueError("invalid target ISA level")
        for key in ("mem_gib", "swap_gib"):
            if type(values[key]) not in (int, float) or not 0 <= values[key] < 1_000_000:
                raise ValueError(f"invalid target {key}")
        for key in ("flags", "gpus", "filesystems", "dkms_modules", "bootloaders"):
            if not isinstance(values[key], list) or any(not isinstance(x, str) for x in values[key]):
                raise ValueError(f"invalid target {key}")
        for key in ("disk_swap", "root_luks", "has_nvme", "rotational", "battery", "sched_ext_live", "microcode_hook"):
            if type(values[key]) is not bool:
                raise ValueError(f"invalid target {key}")
        if not isinstance(values["tools"], dict) or any(not isinstance(x, str) for pair in values["tools"].items() for x in pair):
            raise ValueError("invalid target tool versions")
        for key in ("vendor", "model", "virt", "uarch", "kernel", "cmdline", "root_fs", "esp", "xbootldr", "initrd_compression"):
            if not isinstance(values[key], str):
                raise ValueError(f"invalid target {key}")
        values["flags"] = frozenset(values["flags"])
        for key in ("gpus", "filesystems", "dkms_modules", "bootloaders"):
            values[key] = tuple(values[key])
        return HostFacts(**values)
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ProfileError(f"Invalid target manifest {path}: {e}") from e


def broad_hardware(p: KernelProfile) -> bool:
    return p.g("meta", "portable_package") and not p.g("meta", "manifest_path")


def auto_jobs(facts: HostFacts, lto: str) -> int:
    per_job_gib = 1.5 if lto == "full" else 1.0
    available = re.search(r"^MemAvailable:\s+(\d+)", _read("/proc/meminfo"), re.M)
    memory = min(facts.mem_gib, int(available[1]) / 1048576) if available else facts.mem_gib
    return max(1, min(os.process_cpu_count() or facts.threads, facts.threads,
                      int(max(1, memory - 2 - RAM_RESERVE_GIB) // per_job_gib)))


# ---------------------------------------------------------------------------------------------------
# Kernel versions & releases (kernel.org releases.json)
# ---------------------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True, order=False)
class KVer:
    major: int
    minor: int
    patch: int = 0
    rc: int | None = None

    @classmethod
    def parse(cls, text: str) -> "KVer | None":
        m = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?(?:-rc(\d+))?", text.strip())
        if not m:
            return None
        return cls(int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), int(m.group(4)) if m.group(4) else None)

    def key(self) -> tuple[int, int, int, int, int]:
        return (self.major, self.minor, self.patch, 0 if self.rc is not None else 1, self.rc or 0)

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}" + (f".{self.patch}" if self.patch else "")
        return base + (f"-rc{self.rc}" if self.rc is not None else "")


@dataclass(frozen=True, slots=True)
class Release:
    version: str
    moniker: str
    released: str
    source_url: str
    pgp_url: str | None

    @property
    def is_rc(self) -> bool:
        return "-rc" in self.version

    @property
    def kver(self) -> KVer:
        return KVer.parse(self.version) or KVer(0, 0)

    @property
    def archive_name(self) -> str:
        return Path(urllib.parse.urlparse(self.source_url).path).name


def http_get(url: str, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise NetworkError(f"GET {url} failed: {e}") from e


def cdn_url(version: str) -> str:
    major = version.split(".")[0]
    return f"https://cdn.kernel.org/pub/linux/kernel/v{major}.x/linux-{version}.tar.xz"


def fetch_releases() -> list[Release]:
    try:
        data = json.loads(http_get(KERNEL_ORG_RELEASES).decode("utf-8"))
    except (NetworkError, ValueError) as e:
        warn(f"kernel.org releases.json unavailable ({e}); only pinned versions can be resolved")
        return []
    res: list[Release] = []
    for r in data.get("releases", []):
        ver = str(r.get("version", ""))
        if not KVer.parse(ver):
            continue
        released = r.get("released", {}) or {}
        res.append(Release(version=ver, moniker=str(r.get("moniker", "stable")), released=str(released.get("isodate", "")),
                           source_url=str(r.get("source") or cdn_url(ver)), pgp_url=r.get("pgp") or None))
    return res


def candidates_for(releases: Sequence[Release], channel: str, allow_rc: bool, min_ver: str) -> list[Release]:
    floor = KVer.parse(min_ver) or KVer(*MIN_KERNEL)
    floor = max(floor, KVer(*MIN_KERNEL, rc=0), key=lambda k: k.key())
    effective_allow_rc = allow_rc
    cands: list[Release] = []
    for r in releases:
        if r.is_rc and not effective_allow_rc:
            continue
        if r.kver.key() < floor.key():
            continue
        match channel:
            case "mainline":
                if r.moniker == "mainline":
                    cands.append(r)
            case "stable":
                if r.moniker == "stable" or (r.moniker == "mainline" and not r.is_rc):
                    cands.append(r)
            case "longterm":
                if r.moniker == "longterm":
                    cands.append(r)
    cands.sort(key=lambda r: r.kver.key(), reverse=True)
    return cands


def pinned_release(pin: str, releases: Sequence[Release]) -> Release:
    for r in releases:
        if r.version == pin:
            return r
    kv = KVer.parse(pin)
    if kv is None:
        raise ProfileError(f"release.pin '{pin}' is not a kernel version")
    if kv.rc is not None:
        return Release(pin, "mainline", "", f"https://git.kernel.org/torvalds/t/linux-{pin}.tar.gz", None)
    return Release(pin, "pinned", "", cdn_url(pin), cdn_url(pin).replace(".tar.xz", ".tar.sign"))


def choose_release(p: KernelProfile, releases: Sequence[Release], exact_pin: bool = False) -> Release:
    pin = p.g("release", "pin")
    channel = p.g("release", "channel")
    preferred = pinned_release(pin, releases) if pin else None
    cands = candidates_for(releases, channel, p.g("release", "allow_rc"), p.g("release", "min_version"))
    if exact_pin or not interactive() or ASSUME_YES:
        if preferred:
            ok(f"Pinned release {preferred.version}")
            return preferred
        if not cands:
            raise NetworkError(f"No kernel >= {MIN_KERNEL[0]}.{MIN_KERNEL[1]} found in channel '{channel}' (allow_rc={p.g('release', 'allow_rc')})")
        return cands[0]
    # The interactive choice explicitly authorizes an RC. allow_rc controls
    # unattended selection, not which channel is available in this picker.
    cands = candidates_for(releases, channel, True, p.g("release", "min_version"))
    floor = max(KVer.parse(p.g("release", "min_version")) or KVer(*MIN_KERNEL), KVer(*MIN_KERNEL, rc=0), key=lambda k: k.key())
    listed = {r.version: r for r in releases if r.moniker in CHANNEL_CHOICES and r.kver.key() >= floor.key()}
    if preferred:
        listed.setdefault(preferred.version, preferred)
    selectable = sorted(listed.values(), key=lambda r: r.kver.key(), reverse=True)
    if not selectable:
        raise NetworkError(f"No kernel >= {floor} available in kernel.org releases.json; use --pin for an exact version")
    fallback = next((r for r in selectable if p.g("release", "allow_rc") or not r.is_rc), selectable[0])
    default_version = preferred.version if preferred else (cands[0].version if cands else fallback.version)
    default_index = next(i for i, r in enumerate(selectable, 1) if r.version == default_version)
    rule("Select kernel release (profile preference marked ★)")
    default_label = "★ profile default" if preferred or cands else "★ fallback (preferred channel unavailable)"
    if selectable[default_index - 1].is_rc:
        default_label += " (RC: explicit selection)"
    rows = [[str(i), r.version, r.moniker, r.released, default_label if i == default_index else ""]
            for i, r in enumerate(selectable, 1)]
    unavailable = sorted((r for r in releases if r.moniker in CHANNEL_CHOICES and r.kver.key() < floor.key()),
                         key=lambda r: r.kver.key(), reverse=True)
    rows.extend(["–", r.version, r.moniker, r.released, f"below {floor} minimum"] for r in unavailable)
    table(["#", "version", "channel", "released", "status"], rows)
    if not (preferred or cands):
        warn(f"Profile channel '{channel}' has no release at or above {floor} in this feed; choose another channel's supported release")
    if not p.g("release", "allow_rc") and any(r.is_rc for r in selectable):
        note("RCs may be selected explicitly here; unattended RC selection requires --allow-rc")
    return selectable[ask_index("Release", len(selectable), default_index) - 1]


# ---------------------------------------------------------------------------------------------------
# Tarballs: download (resumable), SHA256 (sha256sums.asc) and PGP (xz -cd | gpg --verify)
# ---------------------------------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(4 << 20):
            h.update(chunk)
    return h.hexdigest()


def expected_sha256(archive_name: str, version: str) -> str | None:
    major = version.split(".")[0]
    try:
        raw = http_get(f"https://cdn.kernel.org/pub/linux/kernel/v{major}.x/sha256sums.asc").decode("utf-8", "replace")
    except NetworkError:
        return None
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].lstrip("*") == archive_name:
            return parts[0]
    return None


def archive_valid(path: Path, extension: str) -> bool:
    """Check the tar header and read the entire compressed stream, including its checksum."""
    try:
        if not tarfile.is_tarfile(path):
            return False
        opener = gzip.open if extension == ".gz" else lzma.open if extension == ".xz" else open
        with opener(path, "rb") as stream:
            while stream.read(4 << 20):
                pass
        return True
    except (OSError, EOFError, lzma.LZMAError, tarfile.TarError):
        return False


def download(url: str, dest: Path, fallback_urls: Sequence[str] = ()) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not (have("aria2c") or have("curl")):
        raise DependencyError("Neither aria2c nor curl is installed (pacman -S curl)")
    sources = (url, *fallback_urls)
    failures = []
    index = 0
    auto_attempts = 0
    while True:
        source = sources[index]
        # Different hosts can serve different gzip streams for the same source tree.
        # Never resume a partial download from one host against another.
        tmp = dest.with_name(dest.name + (".part" if index == 0 else f".fallback{index}.part"))
        codeload = urllib.parse.urlparse(source).hostname == "codeload.github.com"
        if codeload and tmp.exists():
            note("GitHub archive host does not support byte-range resume; restarting its partial download")
            tmp.unlink()
            tmp.with_name(tmp.name + ".aria2").unlink(missing_ok=True)
        info(f"Downloading {source}")
        if have("aria2c") and (index == 0 or not have("curl")):
            cmd = ["aria2c", "--console-log-level=warn", "--summary-interval=0", "-x1" if codeload else "-x8",
                   "-s1" if codeload else "-s8", "-k1M", *([] if codeload else ["-c"]),
                   "--connect-timeout=20", "--timeout=45", "--max-tries=3", "--retry-wait=2", "--auto-file-renaming=false",
                   "-d", str(dest.parent), "-o", tmp.name, source]
        else:
            cmd = ["curl", "-fL", "--connect-timeout", "20", "--retry", "3", "--retry-all-errors",
                   *([] if codeload else ["-C", "-"]),
                   "--progress-bar", "-A", USER_AGENT, "-o", str(tmp), source]
        cp = run(cmd, check=False, capture=False)
        check_abort()
        if cp.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0 and archive_valid(tmp, dest.suffix):
            tmp.replace(dest)
            return
        if cp.returncode == 0:  # A successful HTTP response can still be an error page.
            tmp.unlink(missing_ok=True)
            tmp.with_name(tmp.name + ".aria2").unlink(missing_ok=True)
        reason = f"exit {cp.returncode}" if cp.returncode else "missing or invalid archive"
        failures.append(f"{source} ({reason})")
        if auto_attempts < 2:
            auto_attempts += 1
            warn(f"Download attempt {auto_attempts}/3 failed ({reason}); retrying automatically...")
            time.sleep(2)
            continue
        if interactive() and not ASSUME_YES:
            choices = "[r]etry / [a]lternate / [c]ancel" if len(sources) > 1 else "[r]etry / [c]ancel"
            default = "a" if len(sources) > 1 else "c"
            while True:
                action = ask(f"Download failed after 3 attempts ({reason}). {choices}", default).lower()
                if action in ("r", "retry"):
                    auto_attempts = 0
                    break
                if action in ("a", "alternate") and len(sources) > 1:
                    index = (index + 1) % len(sources)
                    auto_attempts = 0
                    break
                if action in ("c", "cancel"):
                    raise AbortError("Download cancelled; partial archives remain available for resume")
                warn("Choose retry, alternate or cancel")
            continue
        if index + 1 >= len(sources):
            raise NetworkError(f"Download failed: {'; '.join(failures)}")
        warn("Source unavailable; trying alternate archive host")
        index += 1
        auto_attempts = 0


def ensure_kernel_keys() -> bool:
    if not have("gpg"):
        return False
    cp = run(["gpg", "--batch", "--list-keys", "--with-colons", *sorted(KERNEL_SIGNING_FPRS)], check=False, timeout=30)
    if cp.returncode == 0:
        return True
    info("Fetching kernel.org signing keys via WKD (torvalds@kernel.org, gregkh@kernel.org, sashal@kernel.org)")
    cp = run(["gpg", "--batch", "--locate-keys", "torvalds@kernel.org", "gregkh@kernel.org", "sashal@kernel.org"], check=False, timeout=120)
    return cp.returncode == 0


def verify_pgp(tarball: Path, pgp_url: str) -> bool | None:
    """True = valid signature by a kernel.org key, False = invalid, None = not verifiable (no gpg / no keys)."""
    if not ensure_kernel_keys():
        return None
    sig = tarball.with_name(Path(urllib.parse.urlparse(pgp_url).path).name)
    if not sig.is_file():
        try:
            sig.write_bytes(http_get(pgp_url))
        except NetworkError as e:
            warn(f"Signature download failed: {e}")
            return None
    decomp = ["xz", "-cd", str(tarball)] if tarball.suffix == ".xz" else ["gzip", "-cd", str(tarball)]
    decompressor = subprocess.Popen(decomp, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    pg1 = _register(decompressor, True)
    verifier = None
    pg2 = None
    try:
        verifier = subprocess.Popen(["gpg", "--batch", "--status-fd", "1", "--verify", str(sig), "-"],
                                    stdin=decompressor.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, start_new_session=True)
        pg2 = _register(verifier, True)
        assert decompressor.stdout is not None
        decompressor.stdout.close()
        out, _ = verifier.communicate()
        decompressor.wait()
    finally:
        for proc, pgid in ((verifier, pg2), (decompressor, pg1)):
            if proc is not None:
                if proc.poll() is None:
                    terminate_process_group(proc.pid)
                    proc.wait()
                if proc.stdout is not None:
                    proc.stdout.close()
            _unregister(pgid)
    if decompressor.returncode or verifier.returncode:
        return False
    # GnuPG's optional final VALIDSIG field identifies a signing subkey's
    # primary key. Kernel release signatures can legitimately use subkeys.
    for line in out.splitlines():
        fields = line.split()
        if fields[:2] == ["[GNUPG:]", "VALIDSIG"] and len(fields) >= 11:
            primary = fields[11] if len(fields) >= 12 else fields[2]
            if primary in KERNEL_SIGNING_FPRS:
                return True
    return False


def obtain_tarball(rel: Release, require_signature: bool) -> Path:
    TARBALL_DIR.mkdir(parents=True, exist_ok=True)
    dest = TARBALL_DIR / rel.archive_name
    if dest.is_file() and dest.stat().st_size > 0:
        ok(f"Using cached archive {dest.name} ({fmt_bytes(dest.stat().st_size)})")
    else:
        fallback = (f"https://codeload.github.com/torvalds/linux/tar.gz/refs/tags/v{rel.version}",) if rel.is_rc and rel.source_url.startswith("https://git.kernel.org/torvalds/") else ()
        download(rel.source_url, dest, fallback)
    verified = False
    if rel.pgp_url:
        match verify_pgp(dest, rel.pgp_url):
            case True:
                ok("PGP signature valid (kernel.org release key)")
                verified = True
            case False:
                dest.unlink(missing_ok=True)
                raise VerifyError(f"PGP verification FAILED for {dest.name}; archive removed")
            case None:
                note("PGP verification unavailable (gpg or keys missing)")
    if not verified:
        exp = expected_sha256(rel.archive_name, rel.version)
        if exp:
            actual = sha256_file(dest)
            if actual != exp:
                dest.unlink(missing_ok=True)
                raise VerifyError(f"SHA256 mismatch for {dest.name}: expected {exp[:16]}..., got {actual[:16]}...; archive removed")
            ok("SHA256 matches kernel.org sha256sums.asc")
            verified = True
    if not verified:
        if rel.is_rc:
            warn("-rc source snapshots carry no archive signature or checksum; continuing because allow_rc/pin opted in")
        elif require_signature:
            raise VerifyError(f"Could not verify {dest.name} (no PGP, no SHA256). Set release.require_signature=false to override.")
        else:
            warn(f"{dest.name} is unverified (require_signature=false)")
    return dest


def is_valid_kernel_tree(p: Path) -> bool:
    return (p / "Makefile").is_file() and (p / "Kconfig").is_file() and (p / "kernel" / "Kconfig.hz").is_file()


def tree_version(tree: Path) -> str:
    mf = _read(tree / "Makefile")
    v = re.search(r"^[ \t]*VERSION[ \t]*=[ \t]*(\d+)", mf, re.M)
    pl = re.search(r"^[ \t]*PATCHLEVEL[ \t]*=[ \t]*(\d+)", mf, re.M)
    sl = re.search(r"^[ \t]*SUBLEVEL[ \t]*=[ \t]*(\d+)", mf, re.M)
    extra = re.search(r"^[ \t]*EXTRAVERSION[ \t]*=[ \t]*(\S*)", mf, re.M)
    if not (v and pl):
        return "unknown"
    res = f"{v.group(1)}.{pl.group(1)}"
    if sl and sl.group(1) != "0":
        res += f".{sl.group(1)}"
    if extra and extra.group(1):
        res += extra.group(1)
    return res


def tree_dir_for(rel: Release, identity: str) -> Path:
    return SRC_DIR / (f"linux-{rel.version}" + (f"+{identity}" if identity else ""))


def unpack(tarball: Path, rel: Release, identity: str, fresh: bool) -> Path:
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    dest = tree_dir_for(rel, identity)
    if fresh and dest.exists():
        info(f"--fresh: removing {dest}")
        shutil.rmtree(dest)
    if is_valid_kernel_tree(dest):
        if KVer.parse(tree_version(dest)) != rel.kver:
            raise BuildError(f"Cached source version differs from {rel.version}; use --fresh")
        ok(f"Reusing source tree {dest.name} (incremental build)")
        return dest
    rule(f"Extracting {tarball.name}")
    with tempfile.TemporaryDirectory(dir=SRC_DIR, prefix=".extract-") as tmp:
        run(["tar", "-xf", str(tarball), "-C", tmp])
        inner = [d for d in Path(tmp).iterdir() if d.is_dir()]
        if len(inner) != 1 or not is_valid_kernel_tree(inner[0]):
            raise BuildError(f"Unexpected archive layout in {tarball.name}")
        if dest.exists():
            shutil.rmtree(dest)
        inner[0].rename(dest)
        (dest / ".dusky").mkdir(exist_ok=True)
        (dest / ".dusky" / "source-epoch").write_text(str(int((dest / "Makefile").stat().st_mtime)))
    kv = KVer.parse(tree_version(dest).split("-dusky")[0])
    if kv != rel.kver:
        raise VerifyError(f"Archive reports Linux {tree_version(dest)}, expected {rel.version}")
    if kv is None or kv.key() < KVer(*MIN_KERNEL, rc=0).key():
        raise ProfileError(f"Extracted tree reports {tree_version(dest)}, below the {MIN_KERNEL[0]}.{MIN_KERNEL[1]} floor")
    ok(f"Extracted Linux {tree_version(dest)} -> {dest}")
    return dest

# ---------------------------------------------------------------------------------------------------
# .config seeding, modprobed-db and localmodconfig pruning
# ---------------------------------------------------------------------------------------------------
def is_plausible_kernel_config(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 20000:
        return False
    head = _read(path)
    return "CONFIG_X86_64=y" in head and "CONFIG_MODULES=y" in head


def snapshot_path(p: KernelProfile) -> Path:
    return CONFIG_SNAPSHOT_DIR / f"{p.name}.config"


def arch_upstream_config() -> Path | None:
    dest = BUILD_DIR / "seeds" / "arch-linux.config"
    if dest.is_file() and time.time() - dest.stat().st_mtime < 7 * 86400 and is_plausible_kernel_config(dest):
        return dest
    try:
        data = http_get(ARCH_UPSTREAM_CONFIG_URL, timeout=60)
    except NetworkError as e:
        debug(f"arch config fetch failed: {e}")
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    if is_plausible_kernel_config(dest):
        return dest
    # A previously pruned running kernel cannot seed drivers for newly recorded
    # peripherals. Prefer the installed distribution kernel's complete config.
    configs = [module / "build/.config" for module in Path("/usr/lib/modules").iterdir()
               if _read(module / "pkgbase").strip() == "linux"
               and is_plausible_kernel_config(module / "build/.config")]
    return max(configs, key=lambda path: path.stat().st_mtime) if configs else None


def seed_config(tree: Path, p: KernelProfile, env: Mapping[str, str], override: Path | None) -> str:
    rule("Seed .config")
    dest = tree / ".config"
    if override is not None:
        if not is_plausible_kernel_config(override):
            raise ProfileError(f"--seed-config {override} is not a plausible x86-64 kernel config")
        override.copy(dest)
        ok(f"Seeded from {override}")
        return str(override)
    order = {"auto": ("arch", "running", "headers", "snapshot", "defconfig")}.get(p.g("dusky", "seed"), (p.g("dusky", "seed"),))
    for src in order:
        if p.g("meta", "manifest_path") and src in ("running", "headers"):
            continue
        match src:
            case "snapshot":
                snap = snapshot_path(p)
                if is_plausible_kernel_config(snap):
                    snap.copy(dest)
                    ok(f"Seeded from snapshot {snap}")
                    return "snapshot"
            case "arch":
                cfg = arch_upstream_config()
                if cfg is not None:
                    cfg.copy(dest)
                    ok(f"Seeded from Arch Linux config: {cfg}")
                    return "arch"
            case "running":
                gz = Path("/proc/config.gz")
                if gz.is_file():
                    import gzip
                    with gzip.open(gz, "rb") as fh:
                        dest.write_bytes(fh.read())
                    if is_plausible_kernel_config(dest):
                        ok(f"Seeded from /proc/config.gz ({os.uname().release})")
                        return "running"
            case "headers":
                hdr = Path(f"/usr/lib/modules/{os.uname().release}/build/.config")
                if is_plausible_kernel_config(hdr):
                    hdr.copy(dest)
                    ok(f"Seeded from {hdr}")
                    return "headers"
            case "defconfig":
                run(["make", "defconfig"], cwd=tree, env=env)
                warn("Seeded from 'make defconfig' -- not desktop-complete; review the verification report carefully")
                return "defconfig"
    raise BuildError(f"No usable seed found for dusky.seed={p.g('dusky', 'seed')}")


def ensure_modprobed_db_service(*, prompt: bool = True) -> bool:
    """Enable --now the modprobed-db user service (DB writer for localmodconfig).

    Empirically: package ships ONLY user units
    (/usr/lib/systemd/user/modprobed-db.service + .timer, no system unit,
    timer is static). Enabling the service pulls in the timer (Wants=).
    Must run WITHOUT sudo: `sudo systemctl --user` fails (no user bus for root).
    Idempotent; safe to call on every fresh install / build.
    """
    if not have("modprobed-db"):
        return False
    if not have("systemctl"):
        warn("systemctl not found; cannot enable modprobed-db.service")
        return False
    if prompt and not ask_yes("Enable modprobed-db user service (auto-store loaded modules every 6h + at boot for localmodconfig)?", True):
        note("Skipping modprobed-db.service enable (one-shot store only)")
        run(["modprobed-db", "store"], check=False, timeout=60)
        return False
    run(["modprobed-db", "store"], check=False, timeout=60)
    enabled = (run(["systemctl", "--user", "is-enabled", "modprobed-db.service"], check=False, timeout=10).stdout or "").strip()
    timer = (run(["systemctl", "--user", "is-active", "modprobed-db.timer"], check=False, timeout=10).stdout or "").strip()
    if enabled == "enabled" and timer == "active":
        ok("modprobed-db.service already enabled (timer active)")
        return True
    cp = run(["systemctl", "--user", "enable", "--now", "modprobed-db.service"], check=False, timeout=30)
    if cp.returncode == 0:
        ok("modprobed-db.service enabled --now (timer stores every 6h + at boot)")
        return True
    warn("could not enable modprobed-db.service; run manually: systemctl --user enable --now modprobed-db.service")
    return False


def ensure_modprobed_db(p: KernelProfile) -> Path | None:
    if not p.g("modules", "modprobed_db"):
        return None
    custom = p.g("modules", "modprobed_db_path")
    if custom and not Path(custom).expanduser().is_absolute():
        custom = str((p.path.parent / custom).resolve())
    if not custom and not p.g("meta", "manifest_path") and have("modprobed-db"):
        ensure_modprobed_db_service(prompt=False)

    if p.g("meta", "manifest_path") and not custom:
        raise ProfileError("Remote target requires modules.modprobed_db_path")
    db = resolve_modprobed_db(custom if custom else None)
    if db is not None:
        count = count_db_modules(db)
        if count > 0:
            ok(f"modprobed.db: {db} ({count} modules)")
            if count < 40 and not custom:
                warn("modprobed.db is small; use the system for a few days (USB devices, VPN, printers...) before trusting strict mode")
            return db
    if not custom:
        searched = ", ".join(str(c) for c in modprobed_db_candidates())
        warn(f"modprobed.db missing (searched: {searched}; install from AUR: paru -S modprobed-db; modprobed-db store)")
    else:
        warn(f"modprobed.db not found at {Path(custom).expanduser()} (searched custom path only)")
    return None


LMC_KEEP_BASE: Final = ("drivers/usb", "drivers/gpu", "drivers/net", "drivers/hid", "drivers/input", "drivers/nvme", "drivers/bluetooth",
                        "drivers/thunderbolt", "drivers/platform/x86", "drivers/media/usb", "sound", "fs", "net/wireless", "crypto")


def _generate_modprobed_db_from_lsmod() -> Path | None:
    """Best-effort LSMOD snapshot from the live lsmod set (strict fallback only).

    Writes a sorted, unique, validated snapshot to BUILD_DIR (never touches the
    canonical DB owned by `modprobed-db store`, see modprobed_db_candidates()).
    No comment lines: streamline_config parses the first token of every line,
    so a `#` line would become a bogus module. Point-in-time only: unloaded HW
    (USB/VPN/printer) is missing by definition.
    """
    if not have("lsmod"):
        return None
    try:
        cp = run(["lsmod"], check=False, timeout=30)
    except DuskyError:
        return None
    if cp.returncode != 0 or not cp.stdout:
        return None
    names: set[str] = set()
    for line in cp.stdout.splitlines():
        mod = _extract_module_name(line)
        if mod is not None:
            names.add(mod)
    if not names:
        return None
    dest = BUILD_DIR / "lsmod-fallback.db"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n".join(sorted(names)) + "\n", encoding="utf-8")
    except OSError:
        return None
    return dest if dest.is_file() and dest.stat().st_size > 0 else None


def localmodconfig(tree: Path, p: KernelProfile, db: Path | None, env: Mapping[str, str]) -> set[str]:
    mode = p.g("modules", "mode")
    if p.g("meta", "manifest_path") and db is None:
        raise ProfileError("Remote pruning requires a usable module census from the target")
    rule(f"Module pruning ({mode})")
    lm_env = dict(env)
    if db is not None:
        lm_env["LSMOD"] = str(db)
    elif mode == "strict" and p.g("modules", "allow_lsmod_fallback"):
        generated = _generate_modprobed_db_from_lsmod()
        if generated is not None:
            lm_env["LSMOD"] = str(generated)
            warn(f"modprobed.db missing; using point-in-time lsmod snapshot ({generated}) -- unloaded HW may be pruned; prefer expanded mode or a full modprobed.db")
        else:
            searched = ", ".join(str(c) for c in modprobed_db_candidates())
            raise ProfileError(f"strict pruning needs modprobed.db (searched: {searched}; could not generate from lsmod either)")
    elif mode == "strict":
        searched = ", ".join(str(c) for c in modprobed_db_candidates())
        raise ProfileError(f"strict pruning needs modprobed.db (searched: {searched}; none found; set modules.allow_lsmod_fallback=true for a point-in-time lsmod snapshot or use mode=expanded)")
    else:
        warn("Pruning against the live lsmod set only (modules not currently loaded will be dropped)")
    if mode == "expanded":
        lm_env["LMC_KEEP"] = ":".join((*LMC_KEEP_BASE, *p.g("modules", "lmc_keep_extra")))
    target = "localyesconfig" if p.g("modules", "localyesconfig") else "localmodconfig"
    before = sum(1 for line in _read(tree / ".config").splitlines() if line.endswith("=m"))
    result = run(["make", target], cwd=tree, env=lm_env)
    after = sum(1 for line in _read(tree / ".config").splitlines() if line.endswith("=m"))
    ok(f"{target}: modules {before} -> {after}")
    missing = set(re.findall(r"(?m)^(\w+) config not found!", result.stdout))
    # Upstream streamline_config only recognizes obj-$(CONFIG_*). Intel's
    # generated intel-target-* modules need their source-declared mapping.
    restored: set[str] = set()
    makefile = _read(tree / "drivers/platform/x86/intel/Makefile")
    for symbol, objects in re.findall(r"(?m)^intel-target-\$\(CONFIG_(\w+)\)\s*\+=\s*(.+)$", makefile):
        if any("intel_" + name.removesuffix(".o").replace("-", "_") in missing
               for name in objects.split() if name.endswith(".o")):
            restored.add(symbol)
    if restored:
        note("Restoring target census drivers missed by upstream localmodconfig: " + ", ".join(sorted(restored)))
    unresolved = missing - {"intel_" + name.removesuffix(".o").replace("-", "_")
                            for symbol, objects in re.findall(r"(?m)^intel-target-\$\(CONFIG_(\w+)\)\s*\+=\s*(.+)$", makefile)
                            if symbol in restored for name in objects.split()}
    if unresolved:
        note("Census entries not mapped by localmodconfig (may be built-in or external): " + ", ".join(sorted(unresolved)))
    return restored


# ---------------------------------------------------------------------------------------------------
# Kconfig symbol index: know exactly which symbols this tree offers (drives skip/soft/hard verification)
# ---------------------------------------------------------------------------------------------------
_KCONFIG_SYM_RE: Final = re.compile(r"^\s*(?:menu)?config\s+([A-Za-z0-9_]+)\s*$", re.M)


@dataclass(slots=True)
class KconfigIndex:
    symbols: frozenset[str]
    x86_64_version_max: int
    types: dict[str, str] = field(default_factory=dict)

    @classmethod
    def scan(cls, tree: Path) -> Self:
        syms: set[str] = set()
        types: dict[str, str] = {}
        skip_dirs = {".git", ".dusky", ".thinlto-cache", "Documentation", "tools", "samples", "LICENSES", "pacman"}
        for root, dirs, files in tree.walk():
            if root == tree / "arch":
                dirs[:] = [d for d in dirs if d == "x86"]
            else:
                dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fn in files:
                if fn == "Kconfig" or fn.startswith("Kconfig."):
                    text = _read(root / fn)
                    syms.update(_KCONFIG_SYM_RE.findall(text))
                    for block in re.split(r"(?m)^[ \t]*(?:menu)?config[ \t]+", text)[1:]:
                        name = block.split()[0]
                        kind = re.search(r"(?m)^[ \t]+(bool|tristate|int|hex|string)\b", block)
                        if kind:
                            types[name] = kind.group(1)
        vmax = 3
        cpu_kconfig = _read(tree / "arch" / "x86" / "Kconfig.cpu")
        m = re.search(r"config X86_64_VERSION\n(?:.*\n)*?\s*range\s+(\d+)\s+(\d+)", cpu_kconfig)
        if m:
            vmax = int(m.group(2))
        return cls(frozenset(syms), vmax, types)

    def has(self, sym: str) -> bool:
        """An empty index (no tree scanned yet) is permissive so dry-runs can print the full matrix."""
        return not self.symbols or sym in self.symbols


type OpAction = Literal["y", "n", "m", "val", "str"]


@dataclass(frozen=True, slots=True)
class Op:
    action: OpAction
    symbol: str
    value: int | str | None = None
    optional: bool = False
    why: str = ""

    def render(self) -> str:
        match self.action:
            case "y":
                return f"CONFIG_{self.symbol}=y"
            case "n":
                return f"# CONFIG_{self.symbol} is not set"
            case "m":
                return f"CONFIG_{self.symbol}=m"
            case "val":
                return f"CONFIG_{self.symbol}={self.value}"
            case _:
                value = str(self.value).replace('\\', '\\\\').replace('"', '\\"')
                return f'CONFIG_{self.symbol}="{value}"'


class Matrix:
    """Ordered, de-duplicated Kconfig operations; symbols absent from the tree are recorded, not applied."""

    def __init__(self, idx: KconfigIndex) -> None:
        self.idx = idx
        self._ops: dict[str, Op] = {}
        self.skipped: list[Op] = []

    def add(self, op: Op) -> None:
        if not self.idx.has(op.symbol):
            self.skipped.append(op)
            return
        self._ops.pop(op.symbol, None)
        self._ops[op.symbol] = op

    def y(self, sym: str, *, optional: bool = False, why: str = "") -> None:
        self.add(Op("y", sym, optional=optional, why=why))

    def n(self, sym: str, *, optional: bool = False, why: str = "") -> None:
        self.add(Op("n", sym, optional=optional, why=why))

    def m(self, sym: str, *, optional: bool = False, why: str = "") -> None:
        self.add(Op("m", sym, optional=optional, why=why))

    def val(self, sym: str, value: int, *, optional: bool = False, why: str = "") -> None:
        self.add(Op("val", sym, value, optional=optional, why=why))

    def s(self, sym: str, value: str, *, optional: bool = False, why: str = "") -> None:
        self.add(Op("str", sym, value, optional=optional, why=why))

    def flag(self, sym: str, on: bool, *, optional: bool = False, why: str = "") -> None:
        (self.y if on else self.n)(sym, optional=optional, why=why)

    def choice(self, members: Iterable[str], selected: str, *, optional: bool = False, why: str = "") -> None:
        for mbr in members:
            if mbr != selected:
                self.n(mbr, optional=True)
        self.y(selected, optional=optional, why=why)

    @property
    def ops(self) -> list[Op]:
        return list(self._ops.values())

    def __len__(self) -> int:
        return len(self._ops)

# ---------------------------------------------------------------------------------------------------
# Derived build state (resolved once, shared by matrix, environment, cmdline and verification)
# ---------------------------------------------------------------------------------------------------
@dataclass(slots=True)
class Derived:
    facts: HostFacts
    idx: KconfigIndex
    tree: Path
    version: str
    sched: str
    toolchain: str
    lto: str
    btf: bool
    tracing: str
    rust: bool
    rust_reason: str
    fdo: str
    fdo_reason: str
    march: str = ""
    mtune: str = ""
    kcflags: list[str] = field(default_factory=list)
    krustflags: list[str] = field(default_factory=list)
    kernelrelease: str = ""
    seed_source: str = ""
    compile_duration: float = 0.0
    compile_steps: int = 0
    census_symbols: set[str] = field(default_factory=set)

    @property
    def scx_class(self) -> bool:
        return self.sched == "eevdf"


def derive(p: KernelProfile, facts: HostFacts, idx: KconfigIndex, tree: Path, sched: str, rust_available: bool, rust_output: str) -> Derived:
    s = p.sections
    toolchain = s["compiler"]["toolchain"]
    lto = s["compiler"]["lto"] if toolchain == "llvm" else "none"
    scx_class = bool(s["scheduler"]["scx_enable_class"])
    tracing = s["memory"]["tracing"]
    if tracing == "auto":
        tracing = "full" if (s["scheduler"]["scx"] != "none" or not p.lean("lean")) else "minimal"
    btf = scx_class or tracing == "full"
    rust, reason = bool(s["compiler"]["rust"]), ""
    if rust and toolchain == "llvm" and not rust_available:
        rust, reason = False, "make LLVM=1 rustavailable failed: " + (rust_output.strip().splitlines() or ["no output"])[-1][:160]
    elif rust and toolchain == "gcc" and not rust_available:
        rust, reason = False, "make rustavailable failed with the GCC toolchain"
    elif rust and btf and lto != "none":
        rust, reason = False, "Kconfig: RUST depends on !DEBUG_INFO_BTF || (PAHOLE_HAS_LANG_EXCLUDE && !LTO); BTF (sched_ext/BPF) and LTO are both selected"
    fdo, fdo_reason = s["compiler"]["fdo"], ""
    if fdo != "none":
        pdir = Path(s["compiler"]["fdo_profile_dir"]).expanduser() if s["compiler"]["fdo_profile_dir"] else STATE_DIR / "fdo" / p.name
        if toolchain != "llvm":
            fdo, fdo_reason = "none", "AutoFDO requires clang"
        elif not (pdir / "kernel.afdo").is_file():
            fdo, fdo_reason = "autofdo", "Profile collection build without feedback; boot this kernel and use --fdo-record"
        elif fdo == "autofdo_propeller" and not ((pdir / "propeller_cc_profile.txt").is_file() and (pdir / "propeller_ld_profile.txt").is_file()):
            fdo, fdo_reason = "autofdo", f"Propeller profiles missing in {pdir}; using AutoFDO only"
    if s["timing"]["preempt"] == "lazy" and not idx.has("PREEMPT_LAZY"):
        raise ProfileError("This tree has no PREEMPT_LAZY -- it is not a Linux >= 7.3 x86-64 tree")
    if s["cache"]["sched_cache"] and not idx.has("SCHED_CACHE"):
        warn("CONFIG_SCHED_CACHE (cache-aware scheduling) is not present in this tree; CAS knobs become no-ops")
    if s["gaming"]["ntsync"] and not idx.has("NTSYNC"):
        warn("CONFIG_NTSYNC is not present in this tree")

    arch = s["cpu"]["arch"]
    if arch == "native":
        march = "native"
        mtune = "native"
    elif arch == "generic":
        march = "x86-64"
        mtune = "generic"
    elif arch.startswith("generic_v"):
        v = arch.split("_v")[1]
        march = f"x86-64-v{v}"
        mtune = "generic"
    elif "x86-64" in arch:
        march = arch.replace("_", "-")
        mtune = "generic"
    else:
        march = arch
        mtune = arch

    overrides = dict(flag.split("=", 1) for flag in shlex.split(s["cpu"]["march"]))
    if "-march" in overrides:
        march = overrides["-march"]
        mtune = "generic" if march.startswith("x86-64") else march
    mtune = overrides.get("-mtune", mtune)

    return Derived(facts=facts, idx=idx, tree=tree, version=tree_version(tree), sched=sched, toolchain=toolchain, lto=lto, btf=btf,
                   tracing=tracing, rust=rust, rust_reason=reason, fdo=fdo, fdo_reason=fdo_reason, march=march, mtune=mtune)


MANAGED_CMDLINE_KEYS: Final = frozenset({"mitigations", "nosmt", "amd_pstate", "amd_prefcore", "preempt", "cpuidle.governor", "nvme.poll_queues", "zswap.enabled",
                                         "zswap.compressor", "zswap.zpool", "zswap.max_pool_percent", "zswap.shrinker_enabled", "split_lock_detect", "nowatchdog",
                                         "nmi_watchdog", "pcie_aspm", "pcie_aspm.policy", "transparent_hugepage", "rcu_nocbs", "rcutree.enable_rcu_lazy", "pcie_acs_override"})


def flavor_cmdline(p: KernelProfile, facts: HostFacts) -> list[str]:
    """Flavor-specific kernel parameters (baked into CONFIG_CMDLINE and/or the boot entry)."""
    s = p.sections
    out: list[str] = []
    match s["cpu"]["mitigations"]:
        case "off":
            out.append("mitigations=off")
        case "nosmt":
            out.append("mitigations=auto,nosmt")
    if not s["cpu"]["smt"]:
        out.append("nosmt")
    if facts.vendor == "amd" or broad_hardware(p):
        mode = s["cpu"]["amd_pstate"]
        if mode != "undefined":
            out.append(f"amd_pstate={mode}")
        if not s["cpu"]["prefcore"]:
            out.append("amd_prefcore=disable")
    if s["timing"]["preempt_dynamic"] and s["timing"]["preempt"] != "rt":
        out.append(f"preempt={s['timing']['preempt']}")
    out.append(f"cpuidle.governor={s['power']['cpu_idle_governor']}")
    if s["storage"]["nvme_poll_queues"]:
        out.append(f"nvme.poll_queues={s['storage']['nvme_poll_queues']}")
    match s["memory"]["swap_backend"]:
        case "zswap":
            out += ["zswap.enabled=1", f"zswap.compressor={s['memory']['zswap_compressor']}", "zswap.zpool=zsmalloc",
                    f"zswap.max_pool_percent={s['memory']['zswap_max_pool_pct']}", "zswap.shrinker_enabled=1"]
        case _:
            out.append("zswap.enabled=0")
    if not s["gaming"]["split_lock_mitigate"]:
        out.append("split_lock_detect=off")
    if s["boot"]["nowatchdog"]:
        out += ["nowatchdog", "nmi_watchdog=0"]
    if s["power"]["pcie_aspm"] != "default":
        out.append(f"pcie_aspm.policy={s['power']['pcie_aspm']}")
    out.append(f"transparent_hugepage={s['memory']['thp']}")
    if s["power"]["rcu_lazy"]:
        out.append("rcutree.enable_rcu_lazy=1")
    out += shlex.split(s["boot"]["cmdline_extra"])
    return out


# ---------------------------------------------------------------------------------------------------
# Kconfig matrix
# ---------------------------------------------------------------------------------------------------
def _ops_uarch(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    arch = p.g("cpu", "arch")
    native = arch == "native" and not p.g("cpu", "march")
    mx.flag("X86_NATIVE_CPU", native, why="native CPU code generation")
    if not native:
        mx.y("GENERIC_CPU", optional=True)
        level = {"generic": 1, "generic_v2": 2, "generic_v3": 3, "generic_v4": 4}.get(arch, 1)
        mx.val("X86_64_VERSION", min(level, d.idx.x86_64_version_max), optional=True)
        d.kcflags = [f"-march={d.march}", f"-mtune={d.mtune}"]
        if d.rust:
            d.krustflags = [f"-Ctarget-cpu={d.march}", f"-Ztune-cpu={d.mtune}"]


def _ops_core(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    s, f = p.sections, d.facts
    lean = p.lean("lean")
    mx.y("SMP")
    mx.y("X86_64")
    mx.y("DEBUG_FS")
    for sym in ("EXPERT", "MULTIUSER", "POSIX_MQUEUE", "SYSVIPC", "NAMESPACES", "USER_NS", "PID_NS", "NET_NS", "UTS_NS", "IPC_NS", "TIME_NS",
                "CGROUPS", "CGROUP_BPF", "CGROUP_SCHED", "FAIR_GROUP_SCHED", "CFS_BANDWIDTH", "CGROUP_FREEZER", "CGROUP_PIDS", "CGROUP_DEVICE",
                "CGROUP_CPUACCT", "CGROUP_PERF", "CGROUP_MISC", "CPUSETS", "SECCOMP", "SECCOMP_FILTER", "SECURITY", "SECURITYFS", "SECURITY_LANDLOCK",
                "SECURITY_YAMA", "SECURITY_LOCKDOWN_LSM", "LOCK_DOWN_KERNEL_FORCE_NONE", "EPOLL", "SIGNALFD", "TIMERFD", "EVENTFD", "FHANDLE",
                "INOTIFY_USER", "FANOTIFY", "FANOTIFY_ACCESS_PERMISSIONS", "IO_URING", "ADVISE_SYSCALLS", "MEMBARRIER", "RSEQ", "KCMP", "FUTEX", "FUTEX_PI",
                "DEVTMPFS", "DEVTMPFS_MOUNT", "TMPFS", "TMPFS_POSIX_ACL", "TMPFS_XATTR", "TMPFS_INODE64", "PROC_FS", "PROC_PAGE_MONITOR",
                "SYSFS", "CONFIGFS_FS", "EFIVAR_FS", "EFI", "EFI_STUB", "EFI_HANDOVER_PROTOCOL", "BLK_DEV_INITRD", "RD_ZSTD", "RD_GZIP", "MODULES",
                "MODULE_UNLOAD", "KERNEL_ZSTD", "RELOCATABLE", "RANDOMIZE_BASE", "RANDOMIZE_MEMORY", "MICROCODE", "DMI", "DMIID", "ACPI",
                "PCI", "PCI_MSI", "PCIEPORTBUS", "PCIEAER", "PCIEASPM", "HOTPLUG_PCI", "HOTPLUG_PCI_PCIE", "VT", "VT_CONSOLE", "UNIX98_PTYS", "FW_LOADER",
                "FW_LOADER_COMPRESS", "FW_LOADER_COMPRESS_ZSTD", "FW_LOADER_COMPRESS_XZ", "SYSFB_SIMPLEFB", "DRM_FBDEV_EMULATION",
                "FRAMEBUFFER_CONSOLE", "FRAMEBUFFER_CONSOLE_DETECT_PRIMARY", "KALLSYMS", "BINFMT_ELF", "BINFMT_SCRIPT", "COREDUMP", "PERF_EVENTS", "HWMON",
                "THERMAL", "NLS", "NLS_UTF8", "UNICODE", "AUTOFS_FS", "KEYS", "BLK_DEV_DM", "EFI_PARTITION", "MSDOS_PARTITION", "SWAP", "SHMEM", "AIO",
                "UNIX", "INET", "IPV6", "NETFILTER", "PACKET", "CRYPTO_USER_API_HASH", "CRYPTO_USER_API_SKCIPHER", "CRYPTO_USER_API_RNG", "CRYPTO_USER_API_AEAD",
                "INTEGRITY"):
        mx.y(sym)
    for sym in ("BINFMT_MISC", "BLK_DEV_LOOP", "FUSE_FS", "OVERLAY_FS", "DM_CRYPT", "DM_INTEGRITY", "X86_MSR", "X86_CPUID"):
        mx.m(sym)
    comp = {"zstd": "RD_ZSTD", "xz": "RD_XZ", "lz4": "RD_LZ4", "gzip": "RD_GZIP", "lzma": "RD_LZMA", "bzip2": "RD_BZIP2", "lzo": "RD_LZO"}
    mx.y(comp.get(f.initrd_compression, "RD_ZSTD"), why=f"mkinitcpio COMPRESSION={f.initrd_compression}")
    mx.y("X86_X2APIC", optional=True, why="needs IRQ_REMAP or HYPERVISOR_GUEST")
    mx.n("MICROCODE_LATE_LOADING")
    mx.n("X86_EXTENDED_PLATFORM")
    mx.flag("EFI_MIXED", not lean)
    mx.flag("IKHEADERS", False)
    mx.flag("IMA", False)
    mx.flag("EVM", False)
    mx.n("WERROR")
    for werr in ("DRM_WERROR", "DRM_AMDGPU_WERROR", "DRM_XE_WERROR", "DRM_I915_WERROR", "KVM_WERROR"):
        mx.n(werr, optional=True)
    mx.s("SYSTEM_TRUSTED_KEYS", "")
    mx.s("SYSTEM_REVOCATION_KEYS", "")
    mx.flag("FRAMEBUFFER_CONSOLE_DEFERRED_TAKEOVER", not s["dusky"]["enhanced"])
    mx.flag("DYNAMIC_DEBUG", not lean)
    mx.flag("PROFILING", not lean)
    mx.flag("BSD_PROCESS_ACCT", not lean)
    mx.flag("SYSFS_SYSCALL", False)
    mx.flag("PCSPKR_PLATFORM", not lean)
    mx.flag("X86_16BIT", not lean)


def _ops_sched(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    s = p.sections
    mx.flag("SCHED_AUTOGROUP", s["scheduler"]["autogroup"])
    mx.flag("RT_GROUP_SCHED", s["scheduler"]["rt_group"])
    mx.flag("SCHED_CORE", s["scheduler"]["sched_core"])
    mx.y("SCHED_MC")
    mx.flag("SCHED_MC_PRIO", s["cpu"]["prefcore"])
    mx.flag("SCHED_SMT", s["cpu"]["smt"])
    mx.y("SCHED_CLUSTER")
    mx.flag("SCHED_CACHE", s["cache"]["sched_cache"], why="Cache-aware scheduling")
    mx.flag("UCLAMP_TASK", s["gaming"]["uclamp"])
    mx.flag("UCLAMP_TASK_GROUP", s["gaming"]["uclamp"])
    mx.flag("RSEQ_SLICE_EXTENSION", s["rseq"]["slice_extension"], optional=True)
    mx.y("PSI")
    mx.n("PSI_DEFAULT_DISABLED")
    mx.flag("SCHEDSTATS", d.tracing == "full" and not p.lean("lean"))
    mx.y("CPU_FREQ_GOV_SCHEDUTIL")
    if d.scx_class and s["scheduler"]["scx_enable_class"]:
        for sym in ("BPF", "BPF_SYSCALL", "BPF_JIT", "BPF_JIT_ALWAYS_ON", "BPF_JIT_DEFAULT_ON", "SCHED_CLASS_EXT", "DEBUG_INFO_BTF", "DEBUG_INFO_BTF_MODULES",
                    "BPF_UNPRIV_DEFAULT_OFF", "CGROUP_BPF"):
            mx.y(sym, why="sched_ext")
        mx.n("MODULE_ALLOW_BTF_MISMATCH")
    else:
        mx.n("SCHED_CLASS_EXT")
    if d.tracing == "full":
        for sym in ("FTRACE", "TRACEPOINTS", "STACKTRACE", "FUNCTION_TRACER", "DYNAMIC_FTRACE", "FUNCTION_GRAPH_TRACER", "FPROBE", "KPROBES", "KPROBE_EVENTS",
                    "UPROBES", "UPROBE_EVENTS", "BPF_EVENTS", "BPF_SYSCALL", "BPF_JIT", "PERF_EVENTS", "BPF_LSM"):
            mx.y(sym, why="tracing=full")
        for sym in ("FTRACE_SYSCALLS", "STACK_TRACER", "BPF_KPROBE_OVERRIDE", "MMIOTRACE", "FUNCTION_PROFILER", "HWLAT_TRACER", "OSNOISE_TRACER", "TIMERLAT_TRACER"):
            mx.n(sym)
    else:
        for sym in ("FUNCTION_TRACER", "DYNAMIC_FTRACE", "FUNCTION_GRAPH_TRACER", "FPROBE", "KPROBES", "KPROBE_EVENTS", "UPROBES", "UPROBE_EVENTS", "BPF_EVENTS",
                    "STACK_TRACER", "BLK_DEV_IO_TRACE", "FTRACE_SYSCALLS", "SCHED_TRACER", "IRQSOFF_TRACER", "PREEMPT_TRACER", "HWLAT_TRACER", "OSNOISE_TRACER",
                    "TIMERLAT_TRACER", "MMIOTRACE", "SYNTH_EVENTS", "HIST_TRIGGERS", "BOOTTIME_TRACING", "FUNCTION_PROFILER", "KPROBE_EVENTS_ON_NOTRACE", "FTRACE"):
            mx.n(sym, why="tracing=minimal")
        mx.n("BPF_LSM", optional=True, why="needs BPF_EVENTS")
        mx.y("BPF_SYSCALL")
        mx.y("BPF_JIT")


def _ops_cpu(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    s, f = p.sections, d.facts
    c = s["cpu"]
    lean = p.lean("lean")
    _ops_uarch(mx, p, d)
    if c["nr_cpus"]:
        nr = c["nr_cpus"]
    elif broad_hardware(p):
        nr = 512
    else:
        # Exported possible-CPU slots include offline CPUs and sparse logical IDs.
        nr = max(2, f.threads)
    portable = broad_hardware(p)
    mx.val("NR_CPUS", nr)
    mx.n("MAXSMP")
    mx.flag("CPUMASK_OFFSTACK", nr > 512)
    mx.flag("X86_MCE", c["mce"])
    mx.flag("X86_MCE_AMD", c["mce"] and (f.vendor != "intel" or portable))
    mx.flag("X86_MCE_INTEL", c["mce"] and (f.vendor != "amd" or portable))
    mx.n("X86_MCELOG_LEGACY")
    mx.flag("CPU_MITIGATIONS", c["mitigations"] != "off", why=f"cpu.mitigations={c['mitigations']}")
    mx.flag("IA32_EMULATION", c["compat32"])
    mx.n("X86_X32_ABI")
    mx.y("CPU_FREQ")
    mx.y("CPU_FREQ_STAT")
    govs = ("PERFORMANCE", "POWERSAVE", "USERSPACE", "ONDEMAND", "CONSERVATIVE", "SCHEDUTIL")
    for g in govs:
        keep = not lean or g in ("PERFORMANCE", "POWERSAVE", "SCHEDUTIL") or g == c["governor"].upper()
        mx.flag("CPU_FREQ_GOV_" + g, keep)
    mx.choice([f"CPU_FREQ_DEFAULT_GOV_{g}" for g in govs], f"CPU_FREQ_DEFAULT_GOV_{c['governor'].upper()}")
    mx.y("X86_INTEL_PSTATE")
    mx.y("X86_AMD_PSTATE")
    mx.n("X86_AMD_PSTATE_UT")
    mx.m("X86_ACPI_CPUFREQ")
    mx.y("X86_ACPI_CPUFREQ_CPB")
    if c["amd_pstate"] != "undefined":
        mx.val("X86_AMD_PSTATE_DEFAULT_MODE", {"disable": 1, "passive": 2, "active": 3, "guided": 4}[c["amd_pstate"]], why=f"amd_pstate={c['amd_pstate']}")
    gov = s["power"]["cpu_idle_governor"]
    mx.y("CPU_IDLE")
    mx.flag("CPU_IDLE_GOV_TEO", gov == "teo")
    mx.flag("CPU_IDLE_GOV_MENU", gov == "menu", optional=True)
    mx.n("CPU_IDLE_GOV_LADDER")
    _hv, _ = _is_virt_target(p, f)
    mx.flag("CPU_IDLE_GOV_HALTPOLL", gov == "haltpoll" or _hv, optional=True, why="KVM guests only")
    mx.flag("HALTPOLL_CPUIDLE", _hv, optional=True)
    mx.flag("INTEL_IDLE", f.vendor != "amd" or portable)
    if f.vendor == "intel" or portable:
        mx.y("INTEL_HFI_THERMAL", optional=True, why="Intel Thread Director feedback")
        mx.y("INTEL_TURBO_MAX_3", optional=True, why="Intel ITMT preferred-core boost")
        mx.m("INTEL_TCC_COOLING", optional=True)
        mx.m("INTEL_RAPL", optional=True)
        mx.y("X86_INTEL_LPSS")
        mx.m("INTEL_PMC_CORE", optional=True)
        mx.m("INTEL_UNCORE_FREQ_CONTROL", optional=True)
    if f.vendor == "amd" or portable:
        mx.m("AMD_PMC", optional=True)
        mx.m("AMD_PMF", optional=True)
        mx.y("X86_AMD_PLATFORM_DEVICE")
        mx.m("SENSORS_K10TEMP")
        mx.m("AMD_3D_VCACHE", optional=True)
        mx.n("AMD_HSMP")
        mx.flag("AMD_NUMA", s["memory"]["numa"])
    mx.flag("X86_CPU_RESCTRL", not lean, optional=True)
    mx.y("LEGACY_VSYSCALL_NONE", why="Modern VDSO only, eliminate legacy vsyscall page")
    mx.n("LEGACY_VSYSCALL_XONLY", optional=True)
    mx.n("LEGACY_VSYSCALL_EMULATE", optional=True)


def _ops_timing(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    s = p.sections
    t = s["timing"]
    hz = t["hz"]
    mx.choice([f"HZ_{h}" for h in HZ_CHOICES], f"HZ_{hz}", why=f"{hz} Hz")
    mx.val("HZ", hz)
    match t["tickless"]:
        case "periodic":
            mx.y("HZ_PERIODIC")
            for sym in ("NO_HZ_IDLE", "NO_HZ_FULL", "NO_HZ"):
                mx.n(sym)
        case "idle":
            mx.n("HZ_PERIODIC")
            mx.y("NO_HZ_IDLE")
            mx.n("NO_HZ_FULL")
            mx.y("NO_HZ")
            mx.y("NO_HZ_COMMON")
        case "full":
            mx.n("HZ_PERIODIC")
            mx.n("NO_HZ_IDLE")
            mx.y("NO_HZ_FULL")
            mx.y("NO_HZ")
            mx.y("NO_HZ_COMMON")
            mx.y("CONTEXT_TRACKING_USER")
            mx.n("CONTEXT_TRACKING_USER_FORCE")
            mx.y("VIRT_CPU_ACCOUNTING_GEN")
            mx.y("CPU_ISOLATION")
    sel = {"lazy": "PREEMPT_LAZY", "full": "PREEMPT", "rt": "PREEMPT_RT"}[t["preempt"]]
    mx.choice(("PREEMPT_NONE", "PREEMPT_VOLUNTARY", "PREEMPT", "PREEMPT_LAZY", "PREEMPT_RT"), sel, why=f"preempt={t['preempt']}")
    mx.flag("PREEMPT_DYNAMIC", t["preempt_dynamic"] and t["preempt"] != "rt")
    mx.y("HIGH_RES_TIMERS")
    mx.y("POSIX_TIMERS")
    mx.y("IRQ_TIME_ACCOUNTING")
    if s["power"]["rcu_lazy"]:
        mx.y("RCU_EXPERT")
        mx.y("RCU_NOCB_CPU")
        mx.y("RCU_NOCB_CPU_DEFAULT_ALL")
        mx.y("RCU_LAZY", why="battery: lazy RCU callbacks")
        mx.n("RCU_LAZY_DEFAULT_OFF")
    else:
        mx.n("RCU_LAZY", optional=True)


def _ops_memory(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    s, f = p.sections, d.facts
    m, sec = s["memory"], s["security"]
    lean, minimal, embedded = p.lean("lean"), p.lean("minimal"), p.lean("embedded")
    hardened, extreme = sec["profile"] == "hardened", sec["profile"] == "extreme"
    vm = _virt_guest(f)[0]
    thp = m["thp"]
    # In-tree GPU SVM helpers use huge-page definitions even with runtime THP
    # disabled. Keep support available; NEVER disables automatic THP allocation.
    mx.y("TRANSPARENT_HUGEPAGE")
    mx.choice(("TRANSPARENT_HUGEPAGE_ALWAYS", "TRANSPARENT_HUGEPAGE_MADVISE", "TRANSPARENT_HUGEPAGE_NEVER"), f"TRANSPARENT_HUGEPAGE_{thp.upper()}", why=f"thp={thp}")
    mx.y("THP_SWAP", optional=True, why="def_bool y in Kconfig when THP and SWAP are enabled")
    mx.flag("READ_ONLY_THP_FOR_FS", thp != "never" and not lean)
    mx.flag("HUGETLBFS", m["hugetlbfs"])
    mx.flag("HUGETLB_PAGE", m["hugetlbfs"])
    mx.flag("LRU_GEN", m["mglru"])
    mx.flag("LRU_GEN_ENABLED", m["mglru"])
    mx.n("LRU_GEN_STATS")
    mx.y("SWAP")
    mx.y("ZSMALLOC")
    mx.n("ZSMALLOC_STAT")
    zdef = {"zstd": "ZRAM_DEF_COMP_ZSTD", "lz4": "ZRAM_DEF_COMP_LZ4", "lz4hc": "ZRAM_DEF_COMP_LZ4HC", "lzo-rle": "ZRAM_DEF_COMP_LZORLE"}
    match m["swap_backend"]:
        case "zram":
            algos = {m["zram_algo"], m["zram_recomp_algo"] if m["zram_multi_comp"] else m["zram_algo"]}
            mx.y("ZRAM", why="swap_backend=zram")
            mx.flag("ZRAM_BACKEND_ZSTD", "zstd" in algos)
            mx.flag("ZRAM_BACKEND_LZ4", "lz4" in algos)
            mx.flag("ZRAM_BACKEND_LZ4HC", "lz4hc" in algos)
            mx.flag("ZRAM_BACKEND_LZO", "lzo-rle" in algos)
            mx.n("ZRAM_BACKEND_DEFLATE")
            mx.n("ZRAM_BACKEND_842")
            mx.choice(zdef.values(), zdef[m["zram_algo"]])
            mx.flag("ZRAM_MULTI_COMP", m["zram_multi_comp"], why="zram recompression")
            mx.flag("ZRAM_TRACK_ENTRY_ACTIME", m["zram_multi_comp"])
            mx.y("ZRAM_WRITEBACK")
            mx.n("ZRAM_MEMORY_TRACKING")
            mx.flag("ZSWAP", not lean)
            mx.n("ZSWAP_DEFAULT_ON", optional=True)
        case "zswap":
            comp = m["zswap_compressor"]
            mx.y("ZSWAP", why="swap_backend=zswap")
            mx.y("ZSWAP_DEFAULT_ON")
            mx.y("ZSWAP_SHRINKER_DEFAULT_ON")
            zc = {"zstd": "ZSWAP_COMPRESSOR_DEFAULT_ZSTD", "lz4": "ZSWAP_COMPRESSOR_DEFAULT_LZ4", "lz4hc": "ZSWAP_COMPRESSOR_DEFAULT_LZ4HC", "lzo": "ZSWAP_COMPRESSOR_DEFAULT_LZO"}
            mx.choice(zc.values(), zc[comp])
            mx.y(f"CRYPTO_{comp.upper()}")
            mx.y("ZSWAP_ZPOOL_DEFAULT_ZSMALLOC", optional=True)
            mx.m("ZRAM")
        case _:
            mx.n("ZSWAP_DEFAULT_ON", optional=True)
            mx.flag("ZSWAP", not lean)
            if lean:
                mx.n("ZRAM")
            else:
                mx.m("ZRAM")
    tiny = m["slub_tiny"]
    mx.y("SLUB")
    mx.flag("SLUB_TINY", tiny, why="minimal allocator footprint")
    mx.flag("SLUB_CPU_PARTIAL", not tiny and not lean and s["timing"]["preempt"] != "rt", optional=tiny)
    mx.flag("SLUB_DEBUG", not lean and not tiny)
    mx.n("SLUB_DEBUG_ON")
    mx.n("SLUB_STATS")
    mx.flag("SLAB_MERGE_DEFAULT", not hardened)
    mx.flag("SLAB_FREELIST_HARDENED", sec["slab_freelist_hardened"] and not tiny, why="depends on !SLUB_TINY")
    mx.flag("SLAB_FREELIST_RANDOM", sec["slab_freelist_random"] and not tiny, why="depends on !SLUB_TINY")
    mx.flag("SLAB_BUCKETS", m["slab_buckets"] and not tiny)
    mx.flag("RANDOM_KMALLOC_CACHES", hardened and not lean and not tiny)
    mx.flag("SHUFFLE_PAGE_ALLOCATOR", not extreme and not lean)
    mx.flag("PER_VMA_LOCK", m["per_vma_lock"], optional=True)
    mx.y("COMPACTION")
    mx.y("MIGRATION")
    mx.flag("KSM", m["ksm"])
    for sym in ("DAMON", "DAMON_VADDR", "DAMON_PADDR", "DAMON_SYSFS", "DAMON_RECLAIM", "DAMON_LRU_SORT"):
        mx.flag(sym, m["damon"])
    mx.flag("PAGE_REPORTING", m["page_reporting"], optional=True, why="also selected by retained balloon drivers")
    if m["numa"] and (f.numa_nodes > 1 or broad_hardware(p)):
        mx.y("NUMA")
        mx.y("X86_64_ACPI_NUMA")
        mx.val("NODES_SHIFT", m["nodes_shift"] or max(1, (f.numa_nodes - 1).bit_length()))
        mx.flag("NUMA_BALANCING", m["numa_balancing"])
        mx.flag("NUMA_BALANCING_DEFAULT_ENABLED", m["numa_balancing"])
        mx.n("NUMA_EMU")
    else:
        mx.n("NUMA", why="single-node host")
    mx.flag("MEMCG", m["memcg"])
    mx.flag("MEMCG_V1", m["memcg"] and not lean)
    mx.flag("CPUSETS_V1", not lean)
    retained = parse_dotconfig(_read(d.tree / ".config"))
    recorded_virtio_mem = retained.get("VIRTIO_MEM") in ("y", "m")
    hotplug = vm or not lean or bool(set(f.gpus) & {"amd", "nvidia"}) or recorded_virtio_mem
    mx.flag("MEMORY_HOTPLUG", hotplug, why="ZONE_DEVICE/DEVICE_PRIVATE (GPU SVM) and VM balloons need it")
    mx.flag("MEMORY_HOTREMOVE", hotplug)
    mx.flag("ZONE_DEVICE", hotplug and not embedded, optional=True)
    if recorded_virtio_mem:
        mx.m("VIRTIO_MEM", why="target census requires memory hotplug")
    log_shift = m["log_buf_shift"] or (15 if minimal else 16 if lean else 17)
    mx.val("LOG_BUF_SHIFT", log_shift)
    mx.val("LOG_CPU_MAX_BUF_SHIFT", 12)
    mx.flag("KALLSYMS_ALL", m["kallsyms_all"])
    mx.flag("IKCONFIG", m["ikconfig"])
    mx.flag("IKCONFIG_PROC", m["ikconfig"])
    mx.flag("BASE_SMALL", m["base_small"])
    mx.flag("KEXEC", m["kexec"])
    mx.flag("KEXEC_FILE", m["kexec"], optional=True)
    mx.flag("KEXEC_HANDOVER", m["kexec"], optional=True, why="selects KEXEC_FILE in Linux 7.2+")
    mx.flag("CRASH_DUMP", m["kexec"] and not lean)
    mx.flag("PROC_VMCORE", m["kexec"] and not lean)
    mx.flag("PROC_KCORE", not lean)
    mx.flag("VM_EVENT_COUNTERS", not embedded)
    mx.n("PERCPU_STATS")
    mx.flag("TRIM_UNUSED_KSYMS", m["trim_unused_ksyms"], why="dead export elimination")
    mx.flag("LD_DEAD_CODE_DATA_ELIMINATION", m["dead_code_elimination"], optional=True, why="requires HAS_LD_DEAD_CODE_DATA_ELIMINATION (not selected by x86)")
    for sym in ("PAGE_POISONING", "DEBUG_PAGEALLOC", "PAGE_OWNER", "PAGE_TABLE_CHECK", "DEBUG_VM", "MEMTEST", "KASAN", "KMSAN", "KCSAN", "LOCKDEP", "PROVE_LOCKING",
                "DEBUG_ATOMIC_SLEEP", "DEBUG_PREEMPT", "DEBUG_KMEMLEAK", "DEBUG_OBJECTS", "DEBUG_STACK_USAGE", "LATENCYTOP", "DEBUG_MISC", "PRINTK_INDEX",
                "SLUB_DEBUG_ON", "HWPOISON_INJECT", "FAULT_INJECTION", "DEBUG_PER_CPU_MAPS", "DEBUG_TIMEKEEPING", "DEBUG_SG", "DEBUG_PLIST"):
        mx.n(sym)
    kfence = not extreme and not lean and not tiny
    mx.flag("KFENCE", kfence, why="pool is only reserved when kfence.sample_interval > 0")
    if kfence:
        mx.val("KFENCE_SAMPLE_INTERVAL", 0)
    mx.flag("UBSAN", sec["ubsan_bounds"])
    mx.flag("UBSAN_BOUNDS", sec["ubsan_bounds"])
    mx.n("UBSAN_TRAP")
    mx.n("UBSAN_SHIFT")
    mx.n("UBSAN_DIV_ZERO")
    mx.n("UBSAN_BOOL")
    mx.n("UBSAN_ENUM")
    mx.n("UBSAN_ALIGNMENT")


def _ops_compiler(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    s = p.sections
    c, sec = s["compiler"], s["security"]
    extreme, hardened = sec["profile"] == "extreme", sec["profile"] == "hardened"
    if c["optimize"] == "o3" and d.idx.has("CC_OPTIMIZE_FOR_PERFORMANCE_O3"):
        mx.choice(("CC_OPTIMIZE_FOR_PERFORMANCE", "CC_OPTIMIZE_FOR_PERFORMANCE_O3", "CC_OPTIMIZE_FOR_SIZE"), "CC_OPTIMIZE_FOR_PERFORMANCE_O3", why="optimize=o3")
    elif c["optimize"] == "size":
        mx.choice(("CC_OPTIMIZE_FOR_PERFORMANCE", "CC_OPTIMIZE_FOR_SIZE"), "CC_OPTIMIZE_FOR_SIZE", why="optimize=size")
    else:
        mx.choice(("CC_OPTIMIZE_FOR_PERFORMANCE", "CC_OPTIMIZE_FOR_SIZE"), "CC_OPTIMIZE_FOR_PERFORMANCE", why="optimize=o2")
    if d.toolchain == "llvm":
        mx.choice(("LTO_NONE", "LTO_CLANG_THIN", "LTO_CLANG_THIN_DIST", "LTO_CLANG_FULL"), {"none": "LTO_NONE", "thin": "LTO_CLANG_THIN", "thin_dist": "LTO_CLANG_THIN_DIST", "full": "LTO_CLANG_FULL"}[d.lto], why=f"lto={d.lto}")
    if d.toolchain == "gcc":
        mx.choice(("LTO_NONE", "LTO_CLANG_THIN", "LTO_CLANG_THIN_DIST", "LTO_CLANG_FULL"), "LTO_NONE")
    cfi_sym = "CFI" if d.idx.has("CFI") else "CFI_CLANG"
    cfi = bool(c["kcfi"]) and d.toolchain == "llvm"
    mx.flag(cfi_sym, cfi, why="kCFI")
    if cfi:
        mx.n("CFI_PERMISSIVE")
        mx.y("CFI_AUTO_DEFAULT", optional=True)
        mx.y("X86_KERNEL_IBT")
        mx.flag("CFI_ICALL_NORMALIZE_INTEGERS", d.rust, optional=True)
    else:
        mx.flag("X86_KERNEL_IBT", not extreme)
    mx.flag("AUTOFDO_CLANG", d.fdo in ("autofdo", "autofdo_propeller"), why="AutoFDO")
    mx.flag("PROPELLER_CLANG", d.fdo == "autofdo_propeller", why="Propeller")
    dwarf = ("DEBUG_INFO_NONE", "DEBUG_INFO_DWARF_TOOLCHAIN_DEFAULT", "DEBUG_INFO_DWARF4", "DEBUG_INFO_DWARF5")
    if d.btf:
        mx.choice(dwarf, "DEBUG_INFO_DWARF5", why="BTF needs DWARF")
        mx.n("DEBUG_INFO_REDUCED")
        mx.n("DEBUG_INFO_SPLIT")
        mx.y("DEBUG_INFO_BTF")
        mx.y("DEBUG_INFO_BTF_MODULES")
        mx.choice(("DEBUG_INFO_COMPRESSED_NONE", "DEBUG_INFO_COMPRESSED_ZLIB", "DEBUG_INFO_COMPRESSED_ZSTD"), "DEBUG_INFO_COMPRESSED_ZSTD", optional=True)
    else:
        match c["debug_info"]:
            case "none":
                mx.choice(dwarf, "DEBUG_INFO_NONE", why="debug_info=none")
                mx.n("DEBUG_INFO_BTF", optional=True)
            case "reduced":
                mx.choice(dwarf, "DEBUG_INFO_DWARF5")
                mx.y("DEBUG_INFO_REDUCED")
                mx.n("DEBUG_INFO_BTF", optional=True)
            case _:
                mx.choice(dwarf, "DEBUG_INFO_DWARF5")
                mx.n("DEBUG_INFO_REDUCED")
                mx.n("DEBUG_INFO_BTF", optional=True)
    if c["module_compress"] == "none":
        mx.n("MODULE_COMPRESS")
    else:
        mx.y("MODULE_COMPRESS")
        mx.choice(("MODULE_COMPRESS_GZIP", "MODULE_COMPRESS_XZ", "MODULE_COMPRESS_ZSTD"), f"MODULE_COMPRESS_{c['module_compress'].upper()}")
        mx.y("MODULE_COMPRESS_ALL")
        mx.y("MODULE_DECOMPRESS")
    mx.flag("RUST", d.rust, why=d.rust_reason or "Rust for Linux")
    if d.rust:
        mx.n("RUST_DEBUG_ASSERTIONS")
        mx.y("RUST_OVERFLOW_CHECKS")
        mx.n("SAMPLES_RUST")
    mx.flag("MODVERSIONS", c["modversions"])
    mx.n("MODULE_SRCVERSION_ALL")
    mx.n("MODULE_FORCE_LOAD")
    mx.n("MODULE_DEBUG")
    mx.n("MODULE_STATS")
    if d.toolchain == "llvm":
        mx.n("GCC_PLUGINS")
    mx.choice(("RANDSTRUCT_NONE", "RANDSTRUCT_FULL", "RANDSTRUCT_PERFORMANCE"), "RANDSTRUCT_NONE")
    mx.choice(("UNWINDER_ORC", "UNWINDER_FRAME_POINTER"), "UNWINDER_ORC")
    mx.choice(("INIT_STACK_NONE", "INIT_STACK_ALL_PATTERN", "INIT_STACK_ALL_ZERO"), "INIT_STACK_NONE" if extreme else "INIT_STACK_ALL_ZERO")
    mx.flag("ZERO_CALL_USED_REGS", hardened)
    mx.flag("FORTIFY_SOURCE", not extreme)
    mx.flag("BUG_ON_DATA_CORRUPTION", not extreme)
    mx.flag("LIST_HARDENED", not extreme)
    if s["boot"]["cmdline"] == "bake":
        params = flavor_cmdline(p, d.facts)
        if params:
            mx.y("CMDLINE_BOOL", why="baked flavor command line")
            mx.s("CMDLINE", " ".join(params))
            mx.n("CMDLINE_OVERRIDE")
    else:
        mx.n("CMDLINE_BOOL", optional=True)


def _ops_security(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    s = p.sections
    sec = s["security"]
    hardened = sec["profile"] == "hardened"
    mx.flag("HARDENED_USERCOPY", sec["hardened_usercopy"])
    mx.flag("INIT_ON_ALLOC_DEFAULT_ON", sec["init_on_alloc"])
    mx.flag("INIT_ON_FREE_DEFAULT_ON", sec["init_on_free"])
    match sec["stackprotector"]:
        case "strong":
            mx.y("STACKPROTECTOR")
            mx.y("STACKPROTECTOR_STRONG")
        case "regular":
            mx.y("STACKPROTECTOR")
            mx.n("STACKPROTECTOR_STRONG")
        case _:
            mx.n("STACKPROTECTOR")
            mx.n("STACKPROTECTOR_STRONG")
    mx.y("RANDOMIZE_KSTACK_OFFSET")
    mx.flag("RANDOMIZE_KSTACK_OFFSET_DEFAULT", sec["randomize_kstack"])
    mx.y("STRICT_KERNEL_RWX")
    mx.y("STRICT_MODULE_RWX")
    mx.y("STRICT_DEVMEM")
    mx.flag("IO_STRICT_DEVMEM", hardened)
    mx.flag("SECURITY_APPARMOR", sec["apparmor"])
    mx.flag("SECURITY_SELINUX", sec["selinux"])
    mx.n("SECURITY_SMACK")
    mx.n("SECURITY_TOMOYO")
    mx.n("SECURITY_APPARMOR_DEBUG")
    mx.s("LSM", "landlock,lockdown,yama,integrity" + (",apparmor" if sec["apparmor"] else "") + ",bpf")
    mx.flag("SECURITY_LOCKDOWN_LSM_EARLY", sec["lockdown_early"])
    mx.flag("SECURITY_DMESG_RESTRICT", hardened)
    mx.y("X86_USER_SHADOW_STACK")
    mx.choice(("IOMMU_DEFAULT_DMA_STRICT", "IOMMU_DEFAULT_DMA_LAZY", "IOMMU_DEFAULT_PASSTHROUGH"), "IOMMU_DEFAULT_DMA_STRICT" if hardened else "IOMMU_DEFAULT_DMA_LAZY")
    mx.choice(("X86_INTEL_TSX_MODE_OFF", "X86_INTEL_TSX_MODE_ON", "X86_INTEL_TSX_MODE_AUTO"), "X86_INTEL_TSX_MODE_OFF" if hardened else "X86_INTEL_TSX_MODE_AUTO")
    mx.flag("SCHED_STACK_END_CHECK", sec["profile"] != "extreme")
    mx.flag("DEBUG_LIST", hardened)
    mx.flag("DEBUG_NOTIFIERS", hardened)
    mx.n("STATIC_USERMODEHELPER")
    mx.y("BPF_UNPRIV_DEFAULT_OFF")
    if s["modules"]["sig_force"]:
        for sym in ("MODULE_SIG", "MODULE_SIG_FORCE", "MODULE_SIG_ALL", "MODULE_SIG_SHA512"):
            mx.y(sym, why="module signature enforcement")
        mx.s("MODULE_SIG_KEY", "certs/signing_key.pem")
        mx.s("MODULE_SIG_HASH", "sha512", optional=True)
    else:
        mx.n("MODULE_SIG_FORCE", optional=True)
        mx.flag("MODULE_SIG", hardened, optional=True)


def _ops_gaming(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    g = p.sections["gaming"]
    if g["ntsync"]:
        mx.m("NTSYNC", why="in-tree NT synchronization primitives")
    else:
        mx.n("NTSYNC")
    mx.y("INPUT_EVDEV")
    if g["controllers"]:
        mx.y("INPUT_JOYSTICK")
        mx.y("HIDRAW")
        mx.y("HID_GENERIC")
        mx.y("USB_HID")
        mx.y("NEW_LEDS", optional=True)
        mx.m("LEDS_CLASS", optional=True)
        mx.m("LEDS_CLASS_MULTICOLOR", optional=True)
        for sym in ("INPUT_UINPUT", "INPUT_JOYDEV", "JOYSTICK_XPAD", "HID_PLAYSTATION", "HID_SONY", "HID_NINTENDO", "HID_STEAM", "HID_MICROSOFT",
                    "HID_LOGITECH", "HID_LOGITECH_DJ", "HID_LOGITECH_HIDPP", "HID_MULTITOUCH", "INPUT_FF_MEMLESS", "HID_APPLE", "HID_WACOM"):
            mx.m(sym, why="controllers", optional=True)
        for sym in ("JOYSTICK_XPAD_FF", "JOYSTICK_XPAD_LEDS", "PLAYSTATION_FF", "SONY_FF", "NINTENDO_FF", "STEAM_FF", "LOGITECH_FF", "LOGIWHEELS_FF", "LOGIG940_FF", "LOGIRUMBLEPAD2_FF"):
            mx.y(sym, optional=True)
    mx.flag("USER_EVENTS", d.tracing == "full", optional=True)


FS_SYMBOLS: Final[dict[str, tuple[str, ...]]] = {
    "ext4": ("EXT4_FS", "EXT4_FS_POSIX_ACL", "EXT4_FS_SECURITY"), "btrfs": ("BTRFS_FS", "BTRFS_FS_POSIX_ACL"), "xfs": ("XFS_FS", "XFS_POSIX_ACL"),
    "f2fs": ("F2FS_FS", "F2FS_FS_POSIX_ACL", "F2FS_FS_SECURITY"), "vfat": ("VFAT_FS", "FAT_FS", "NLS_CODEPAGE_437", "NLS_ISO8859_1", "FAT_DEFAULT_UTF8"),
    "virtiofs": ("FUSE_FS", "VIRTIO_FS"), "exfat": ("EXFAT_FS",), "ntfs": ("NTFS_FS", "NTFS_FS_POSIX_ACL"), "ntfs3": ("NTFS3_FS", "NTFS3_LZX_XPRESS", "NTFS3_FS_POSIX_ACL"),
    "fuse": ("FUSE_FS",), "fuseblk": ("FUSE_FS",), "overlay": ("OVERLAY_FS",),
    "nfs": ("NFS_FS", "NFS_V4"), "nfs4": ("NFS_FS", "NFS_V4"), "cifs": ("CIFS",), "smb3": ("CIFS",), "erofs": ("EROFS_FS",), "bcachefs": ("BCACHEFS_FS",),
}


def _ops_storage(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    s, f = p.sections, d.facts
    st = s["storage"]
    portable = broad_hardware(p)
    lean = p.lean("lean")
    if f.has_nvme or st["nvme_poll_queues"] or portable:
        mx.y("BLK_DEV_NVME")
        mx.y("NVME_HWMON")
        mx.n("NVME_MULTIPATH")
    mx.flag("BLK_WBT", st["blk_wbt"])
    mx.flag("BLK_WBT_MQ", st["blk_wbt"])
    mx.y("MQ_IOSCHED_DEADLINE")
    mx.flag("MQ_IOSCHED_KYBER", st["io_scheduler"] == "kyber" or not lean)
    if st["io_scheduler"] == "bfq" or f.rotational:
        mx.y("IOSCHED_BFQ")
        mx.y("BFQ_GROUP_IOSCHED")
    elif lean:
        mx.n("IOSCHED_BFQ")
    else:
        mx.m("IOSCHED_BFQ")
    mx.flag("BLK_CGROUP_IOCOST", st["iocost"])
    mx.n("BLK_CGROUP_IOLATENCY")
    mx.flag("BLK_DEBUG_FS", not lean)
    mx.flag("BLK_SED_OPAL", not lean)
    mx.y("VFAT_FS")
    mx.y("FAT_FS")
    mx.y("NLS_CODEPAGE_437")
    mx.y("NLS_ISO8859_1")
    wanted = set(f.filesystems) | set(st["extra_filesystems"])
    for fstype in sorted(wanted):
        for sym in FS_SYMBOLS.get(fstype, ()):
            if fstype in (f.root_fs, "vfat"):
                mx.y(sym, why=f"boot/root filesystem {fstype} must be built-in")
            elif sym.endswith(("_POSIX_ACL", "_SECURITY", "_UTF8", "NLS_CODEPAGE_437", "NLS_ISO8859_1", "LZX_XPRESS", "NFS_V4")):
                mx.y(sym)
            else:
                mx.m(sym, why=f"filesystem {fstype} in use")
    if f.root_luks or portable:
        mx.y("DM_CRYPT", why="root on dm-crypt or portable")
        for sym in ("CRYPTO_AES", "CRYPTO_XTS", "CRYPTO_SHA256", "CRYPTO_SHA512", "CRYPTO_AES_NI_INTEL"):
            mx.y(sym)
    mx.y("FS_ENCRYPTION")
    mx.y("QUOTA")
    mx.y("BLOCK")


def _ops_power(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    s = p.sections
    pw = s["power"]
    mx.flag("WQ_POWER_EFFICIENT_DEFAULT", pw["wq_power_efficient"])
    mx.flag("ENERGY_MODEL", pw["energy_model"])
    mx.flag("SUSPEND", pw["suspend"])
    mx.flag("HIBERNATION", pw["hibernation"])
    if pw["hibernation"]:
        codec = pw["hibernation_compress"].upper()
        mx.y(f"CRYPTO_{codec}")
        mx.choice(("HIBERNATION_COMP_LZO", "HIBERNATION_COMP_LZ4"), f"HIBERNATION_COMP_{codec}")
    mx.n("PM_AUTOSLEEP")
    mx.n("PM_WAKELOCKS")
    mx.n("PM_DEBUG")
    mx.n("PM_ADVANCED_DEBUG")
    mx.n("PM_TRACE_RTC")
    aspm = {"default": "PCIEASPM_DEFAULT", "powersave": "PCIEASPM_POWERSAVE", "powersupersave": "PCIEASPM_POWER_SUPERSAVE", "performance": "PCIEASPM_PERFORMANCE"}
    mx.choice(aspm.values(), aspm[pw["pcie_aspm"]])
    mx.val("SND_HDA_POWER_SAVE_DEFAULT", pw["hda_power_save"], optional=True)
    if d.facts.battery:
        mx.y("ACPI_BATTERY", optional=True)
        mx.y("ACPI_AC", optional=True)
    mx.y("POWERCAP")
    mx.y("CPU_THERMAL", optional=True)
    mx.y("THERMAL_GOV_STEP_WISE", optional=True)


def _ops_network(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    n = p.sections["network"]
    retained = parse_dotconfig(_read(d.tree / ".config"))

    def algorithm(symbol: str, selected: bool) -> None:
        if not selected and retained.get(symbol) in ("m", "y"):
            mx.m(symbol, why="retained by target module census")
        else:
            mx.flag(symbol, selected)

    mx.y("TCP_CONG_ADVANCED")
    algorithm("TCP_CONG_CUBIC", n["congestion"] == "cubic")
    algorithm("TCP_CONG_BBR", n["congestion"] == "bbr")
    mx.flag("TCP_CONG_RENO", n["congestion"] == "reno", optional=True)
    cong = {"bbr": "DEFAULT_BBR", "cubic": "DEFAULT_CUBIC", "reno": "DEFAULT_RENO"}
    mx.choice(cong.values(), cong[n["congestion"]], why=f"congestion={n['congestion']}")
    mx.y("NET_SCHED")
    algorithm("NET_SCH_FQ", n["qdisc"] == "fq")
    algorithm("NET_SCH_FQ_CODEL", n["qdisc"] in ("fq_codel", "cake"))
    algorithm("NET_SCH_CAKE", n["qdisc"] == "cake")
    algorithm("NET_SCH_FQ_PIE", n["qdisc"] == "fq_pie" or not p.lean("lean"))
    qd = {"fq": "DEFAULT_FQ", "fq_codel": "DEFAULT_FQ_CODEL", "fq_pie": "DEFAULT_FQ_PIE", "pfifo_fast": "DEFAULT_PFIFO_FAST", "cake": "DEFAULT_FQ_CODEL"}
    mx.y("NET_SCH_DEFAULT")
    mx.choice(("DEFAULT_FQ", "DEFAULT_CODEL", "DEFAULT_FQ_CODEL", "DEFAULT_FQ_PIE", "DEFAULT_SFQ", "DEFAULT_PFIFO_FAST"), qd[n["qdisc"]],
              why="cake is applied via sysctl at runtime" if n["qdisc"] == "cake" else "")
    mx.flag("MPTCP", n["mptcp"])
    mx.flag("MPTCP_IPV6", n["mptcp"])
    mx.flag("XDP_SOCKETS", n["xdp"])
    mx.flag("XDP_SOCKETS_DIAG", n["xdp"])
    mx.flag("NF_CONNTRACK_PROCFS", n["nf_conntrack_procfs"])
    mx.y("BQL")
    mx.y("NET_RX_BUSY_POLL")
    mx.y("CGROUP_NET_PRIO")
    mx.y("CGROUP_NET_CLASSID")
    mx.y("BPF_STREAM_PARSER", optional=True)
    mx.y("NET_FLOW_LIMIT")


def _ops_virt(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    f = d.facts
    # A VM may be reported by systemd-detect-virt OR inferred from the
    # hypervisor CPU flag OR from a virtualized GPU (virtio/qxl/bochs/vmware).
    # If any of these hold we must keep the guest/virtio stack enabled,
    # otherwise a kernel built in a VM silently loses video (black screen)
    # because DRM_VIRTIO_GPU depends on VIRTIO_MENU.
    virt_detected, virt_reason = _is_virt_target(p, f)
    if virt_detected:
        for sym in ("HYPERVISOR_GUEST", "PARAVIRT", "PARAVIRT_SPINLOCKS", "KVM_GUEST", "VIRTIO_MENU", "VIRTIO_PCI", "VIRTIO_BLK", "VIRTIO_NET", "VIRTIO_CONSOLE",
                    "VIRTIO_BALLOON", "VIRTIO_INPUT", "VSOCKETS", "VIRTIO_VSOCKETS", "VIRTIO_MEM", "SCSI_VIRTIO", "HW_RANDOM_VIRTIO", "PAGE_REPORTING", "PTP_1588_CLOCK_KVM", "X86_HV_CALLBACK_VECTOR"):
            mx.y(sym, why=f"virtualized guest ({virt_reason})", optional=sym in ("PTP_1588_CLOCK_KVM", "X86_HV_CALLBACK_VECTOR", "VIRTIO_MEM"))
        if f.root_fs == "virtiofs":
            mx.y("FUSE_FS")
            mx.y("VIRTIO_FS")
        else:
            mx.m("VIRTIO_FS")
        if f.virt in ("microsoft", "hyperv"):
            for sym in ("HYPERV", "HYPERV_STORAGE", "HYPERV_NET", "HYPERV_BALLOON", "HYPERV_UTILS"):
                mx.m(sym)
        if f.virt == "vmware" or "vmware" in set(f.gpus):
            for sym in ("VMWARE_VMCI", "VMWARE_BALLOON", "VMWARE_PVSCSI", "VMXNET3", "DRM_VMWGFX"):
                mx.m(sym)
        return
    if p.g("meta", "bare_metal_only"):
        for sym in ("HYPERVISOR_GUEST", "PARAVIRT", "KVM_GUEST", "XEN", "VIRTIO_MENU", "HYPERV", "VMWARE_VMCI", "VBOXGUEST"):
            mx.n(sym, why="bare_metal_only")
    elif broad_hardware(p):
        for sym in ("HYPERVISOR_GUEST", "PARAVIRT", "PARAVIRT_SPINLOCKS", "KVM_GUEST", "VIRTIO_MENU", "VIRTIO_PCI", "VIRTIO_BLK", "VIRTIO_NET", "VIRTIO_CONSOLE"):
            mx.y(sym, why="portable_package paravirt/virtio support", optional=True)


def _has_nvidia_dkms(dkms_modules: Iterable[str]) -> bool:
    return any("nvidia" in m.lower() for m in dkms_modules)


def _gpu_hide_active() -> bool:
    try:
        state = Path("/var/lib/gpu-disable/state.json").read_text(encoding="utf-8", errors="replace")
        if '"action":"disabled"' in state.replace(" ", ""):
            return True
    except OSError:
        pass
    try:
        cmdline = Path("/proc/cmdline").read_text(encoding="utf-8", errors="replace")
        if "vfio-pci.ids=" in cmdline.replace("vfio_pci.", "vfio-pci."):
            return True
    except OSError:
        pass
    return False


def _ops_gpu(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    f = d.facts
    gpus = set(f.gpus)
    portable = broad_hardware(p)
    if "amd" in gpus or portable:
        mx.m("DRM_AMDGPU", why="AMD GPU present/portable")
        mx.y("DRM_AMD_DC", optional=True)
        mx.y("DRM_AMDGPU_SI", optional=True)
        mx.y("DRM_AMDGPU_CIK", optional=True)
        mx.y("DRM_AMDGPU_USERPTR", optional=True)
        mx.m("HSA_AMD", optional=True)
        mx.y("AMD_PRIVATE_COLOR", optional=True, why="enable AMD KMS color management for Gamescope/HDR")
    if "intel" in gpus or portable:
        drivers = ("DRM_I915", "DRM_XE")
        pruned = parse_dotconfig(_read(d.tree / ".config"))
        retained = [sym for sym in drivers if pruned.get(sym) in ("y", "m")]
        if p.g("modules", "mode") == "strict" and not portable and (d.tree / ".config").is_file():
            explicit = {str(sym).removeprefix("CONFIG_") for sym in p.g("modules", "keep_symbols")}
            explicit.update(str(sym).removeprefix("CONFIG_") for sym, value in p.g("dusky", "extra_config").items() if value in (True, "y", "m"))
            drivers = tuple(sym for sym in drivers if sym in retained or sym in explicit)
            if not drivers:
                raise ProfileError("Intel GPU detected but target census retained neither i915 nor xe; collect the target driver or add it to modules.keep_symbols")
        for sym in drivers:
            mx.m(sym, why="Intel GPU census/target")
        if "DRM_XE" in drivers:
            mx.y("DRM_XE_DISPLAY", optional=True)
    if not p.g("meta", "manifest_path") and _gpu_hide_active() and not portable:
        warn("gpu-disable-toggle looks ACTIVE (state=disabled or vfio-pci.ids on cmdline): "
             "PCI telemetry may be blind to the NVIDIA card, so this kernel may omit its "
             "driver. If you want the dGPU in this kernel, --enable it first and re-run "
             "'modprobed-db store' before rebuilding; if the disable is intentional, ignore this.")
    if "nvidia" in gpus or portable:
        nvidia_dkms = _has_nvidia_dkms(f.dkms_modules)
        if portable or not nvidia_dkms:
            if portable:
                reason = "portable target (host DKMS says nothing about the target machine)"
            else:
                reason = (f"NVIDIA GPU without nvidia-dkms "
                          f"({', '.join(f.dkms_modules) or 'no DKMS'} installed)")
            mx.m("DRM_NOUVEAU", why=reason)
        else:
            # localmodconfig's seed may already contain Nouveau; explicitly prune it.
            mx.n("DRM_NOUVEAU", why="target uses NVIDIA DKMS; do not add a second GPU driver for RC kernels")
    for gpu, sym in (("virtio", "DRM_VIRTIO_GPU"), ("qxl", "DRM_QXL"), ("bochs", "DRM_BOCHS"), ("vmware", "DRM_VMWGFX")):
        if gpu in gpus or portable:
            mx.m(sym, optional=True)
    mx.m("DRM", why="modular DRM core (mkinitcpio kms hook ships it in the initramfs)")
    mx.m("DRM_SIMPLEDRM", why="early firmware framebuffer console")
    mx.y("DRM_FBDEV_EMULATION")
    mx.y("DRM_PANIC", optional=True)
    if not d.rust or p.g("compiler", "lto") != "none":
        mx.n("DRM_PANIC_SCREEN_QR_CODE", optional=True, why="QR panic requires Rust; avoid LTO link mismatch")
        mx.s("DRM_PANIC_SCREEN", "kmsg", optional=True)


def _ops_extra(mx: Matrix, p: KernelProfile, d: Derived) -> None:
    for symbol in sorted(d.census_symbols):
        mx.m(symbol, why="target census driver missed by upstream localmodconfig")
    for sym in p.g("modules", "keep_symbols"):
        symbol = str(sym).removeprefix("CONFIG_")
        if d.idx.types.get(symbol) == "bool":
            mx.y(symbol, why="modules.keep_symbols")
        else:
            mx.m(symbol, why="modules.keep_symbols")
    for sym, val in p.g("dusky", "extra_config").items():
        symbol = str(sym).removeprefix("CONFIG_")
        match val:
            case bool():
                mx.flag(symbol, val, why="dusky.extra_config")
            case int():
                mx.val(symbol, val, why="dusky.extra_config")
            case "m":
                mx.m(symbol, why="dusky.extra_config")
            case "y":
                mx.y(symbol, why="dusky.extra_config")
            case "n":
                mx.n(symbol, why="dusky.extra_config")
            case str():
                mx.s(symbol, val, why="dusky.extra_config")
    mx.s("LOCALVERSION", p.localversion())
    mx.n("LOCALVERSION_AUTO")
    mx.s("DEFAULT_HOSTNAME", "(none)")


def build_config_matrix(p: KernelProfile, d: Derived) -> Matrix:
    mx = Matrix(d.idx)
    for builder in (_ops_core, _ops_sched, _ops_cpu, _ops_timing, _ops_memory, _ops_compiler, _ops_security, _ops_gaming, _ops_storage,
                    _ops_power, _ops_network, _ops_virt, _ops_gpu, _ops_extra):
        builder(mx, p, d)
    if p.g("modules", "localyesconfig"):
        for op in list(mx.ops):
            if op.action == "m":
                mx.add(replace(op, action="y"))
    return mx

# ---------------------------------------------------------------------------------------------------
# Apply / finalize / verify the configuration
# ---------------------------------------------------------------------------------------------------
def apply_matrix(tree: Path, mx: Matrix) -> None:
    rule("Apply Kconfig matrix")
    cfg = tree / ".config"
    ops = mx.ops
    symbols = {op.symbol for op in ops}
    retained = []
    for line in cfg.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^(?:# )?CONFIG_([A-Za-z0-9_]+)(?:=| is not set$)", line)
        if match is None or match[1] not in symbols:
            retained.append(line)
    # Write the same documented .config entries scripts/config produces, once.
    # olddefconfig still owns all choice and dependency resolution afterwards.
    cfg.write_text("\n".join((*retained, *(op.render() for op in ops))) + "\n", encoding="utf-8")
    ok(f"Applied {len(ops)} Kconfig operations in one file update")
    if mx.skipped:
        seen = sorted({op.symbol for op in mx.skipped})
        note(f"{len(seen)} symbols not offered by this tree were skipped: {', '.join(seen[:14])}{' ...' if len(seen) > 14 else ''}")


def finalize_config(tree: Path, env: Mapping[str, str]) -> None:
    rule("Resolve dependencies (olddefconfig)")
    run(["make", "olddefconfig"], cwd=tree, env=env)
    run(["make", "syncconfig"], cwd=tree, env=env)
    ok(".config resolved")


def parse_dotconfig(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("CONFIG_"):
            key, _, val = line[7:].partition("=")
            if val.startswith('"') and val.endswith('"') and len(val) >= 2:
                val = re.sub(r'\\(["\\])', r'\1', val[1:-1])
            out[key] = val
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            out[line[9:-11]] = "n"
    return out


VERIFY_HINTS: Final[dict[str, str]] = {
    "RUST": "Rust unavailable: see 'make LLVM=1 rustavailable'; RUST also requires !DEBUG_INFO_BTF || !LTO and !MODVERSIONS || GENDWARFKSYMS",
    "SCHED_CACHE": "cache-aware scheduling symbol missing or gated (needs SMP + SCHED_MC)",
    "PREEMPT_LAZY": "requires ARCH_HAS_PREEMPT_LAZY (x86-64 Linux >= 6.13)",
    "CFI": "kCFI needs clang -fsanitize=kcfi and is incompatible with GCC / FUNCTION_GRAPH_TRACER on some trees",
    "CFI_CLANG": "kCFI needs clang -fsanitize=kcfi",
    "AUTOFDO_CLANG": "requires clang >= 17 and CLANG_AUTOFDO_PROFILE",
    "PROPELLER_CLANG": "requires clang >= 19 and CLANG_PROPELLER_PROFILE_PREFIX",
    "X86_NATIVE_CPU": "native targeting requires this tree to provide X86_NATIVE_CPU",
    "LD_DEAD_CODE_DATA_ELIMINATION": "x86-64 does not select HAS_LD_DEAD_CODE_DATA_ELIMINATION upstream (inert without out-of-tree patches)",
    "NTSYNC": "in-tree ntsync requires Linux >= 6.14 without BROKEN",
    "SLUB_CPU_PARTIAL": "unavailable under PREEMPT_RT or SLUB_TINY",
    "SLAB_BUCKETS": "depends on !SLUB_TINY",
    "DEBUG_INFO_BTF": "needs pahole >= 1.16 (>= 1.27 recommended) and non-reduced DWARF",
    "X86_64_VERSION": "depends on GENERIC_CPU and a compiler that knows x86-64-v levels",
    "ZRAM_MULTI_COMP": "requires ZRAM=y|m",
    "RCU_LAZY": "requires RCU_NOCB_CPU (RCU_EXPERT)",
    "PREEMPT_DYNAMIC": "requires HAVE_PREEMPT_DYNAMIC and !PREEMPT_RT",
    "MPTCP_IPV6": "requires IPV6=y",
    "TRIM_UNUSED_KSYMS": "requires !COMPILE_TEST and MODULES",
    "PER_VMA_LOCK": "def_bool driven by ARCH_SUPPORTS_PER_VMA_LOCK && SMP",
    "INIT_STACK_ALL_ZERO": "requires a compiler supporting -ftrivial-auto-var-init=zero",
    "KFENCE_SAMPLE_INTERVAL": "only visible when KFENCE=y",
    "VFIO_PCI_DMABUF": "requires VFIO_PCI_CORE, PCI_P2PDMA (needs ZONE_DEVICE/MEMORY_HOTPLUG), and DMA_SHARED_BUFFER",
}


@dataclass(slots=True)
class VerifyReport:
    hard: list[tuple[Op, str | None]]
    soft: list[tuple[Op, str | None]]
    facts: list[str]

    @property
    def passed(self) -> bool:
        return not self.hard


def verify_config(tree: Path, p: KernelProfile, mx: Matrix, d: Derived) -> VerifyReport:
    rule("Verify .config contract")
    cfg_file = tree / ".config"
    if not cfg_file.is_file():
        raise VerifyError(".config does not exist after olddefconfig")
    cfg = parse_dotconfig(cfg_file.read_text(encoding="utf-8", errors="replace"))
    extra_soft = {str(s).removeprefix("CONFIG_") for s in p.g("verify", "optional_symbols")}
    hard: list[tuple[Op, str | None]] = []
    soft: list[tuple[Op, str | None]] = []
    requested = {str(k).removeprefix("CONFIG_") for k in p.g("dusky", "extra_config")} | {str(k).removeprefix("CONFIG_") for k in p.g("modules", "keep_symbols")}
    for op in [*mx.ops, *mx.skipped]:
        if op in mx.skipped and op.symbol not in requested:
            op = replace(op, optional=True)
        actual = cfg.get(op.symbol)
        match op.action:
            case "y":
                good = actual == "y"
            case "m":
                good = actual in ("m", "y")
            case "n":
                good = actual in (None, "n")
            case "val":
                good = actual == str(op.value)
            case _:
                good = actual == str(op.value)
        if not good:
            (soft if op.optional or op.symbol in extra_soft else hard).append((op, actual))
    s = p.sections

    def require(cond: bool, sym: str, expect: str, msg: str) -> None:
        if cond and cfg.get(sym) not in expect.split("|"):
            hard.append((Op("y", sym, why=msg), cfg.get(sym)))

    require(s["verify"]["require_ntsync"] and s["gaming"]["ntsync"], "NTSYNC", "y|m", "verify.require_ntsync")
    require(s["verify"]["require_btf"] and d.scx_class and s["scheduler"]["scx_enable_class"], "DEBUG_INFO_BTF", "y", "verify.require_btf")
    require(s["verify"]["require_sched_ext"] and s["scheduler"]["scx_enable_class"], "SCHED_CLASS_EXT", "y", "verify.require_sched_ext")
    require(s["cpu"]["arch"] == "native" and not s["cpu"]["march"], "X86_NATIVE_CPU", "y", "native CPU targeting")
    require(s["cache"]["sched_cache"], "SCHED_CACHE", "y", "cache-aware scheduling")
    require(s["rseq"]["slice_extension"], "RSEQ_SLICE_EXTENSION", "y", "rseq slice extension")
    require(s["power"]["rcu_lazy"], "RCU_LAZY", "y", "lazy RCU")
    require(True, "HZ", str(s["timing"]["hz"]), "timer frequency")
    require(True, "LOCALVERSION", p.localversion(), "LOCALVERSION")
    facts_out = [
        f"kernel {d.version} | LOCALVERSION {cfg.get('LOCALVERSION')} | HZ {cfg.get('HZ')} | preempt {'RT' if cfg.get('PREEMPT_RT') == 'y' else 'lazy' if cfg.get('PREEMPT_LAZY') == 'y' else 'full' if cfg.get('PREEMPT') == 'y' else 'voluntary/none'}"
        + (" +dynamic" if cfg.get("PREEMPT_DYNAMIC") == "y" else ""),
        f"scheduler {d.sched} | sched_ext {cfg.get('SCHED_CLASS_EXT', 'n')} | CAS {cfg.get('SCHED_CACHE', 'absent')} | BORE {cfg.get('SCHED_BORE', 'absent')}",
        f"toolchain {d.toolchain} | LTO {'full' if cfg.get('LTO_CLANG_FULL') == 'y' else 'thin' if cfg.get('LTO_CLANG_THIN') == 'y' else 'none'} | kCFI {cfg.get('CFI', cfg.get('CFI_CLANG', 'n'))} | Rust {cfg.get('RUST', 'n')} | BTF {cfg.get('DEBUG_INFO_BTF', 'n')} | AutoFDO {cfg.get('AUTOFDO_CLANG', 'n')}",
        f"memory: THP {'always' if cfg.get('TRANSPARENT_HUGEPAGE_ALWAYS') == 'y' else 'madvise' if cfg.get('TRANSPARENT_HUGEPAGE_MADVISE') == 'y' else 'never'} | SLUB_TINY {cfg.get('SLUB_TINY', 'n')} | MGLRU {cfg.get('LRU_GEN', 'n')} | ZRAM {cfg.get('ZRAM', 'n')} multi-comp {cfg.get('ZRAM_MULTI_COMP', 'n')} | ZSWAP {cfg.get('ZSWAP', 'n')} | DAMON {cfg.get('DAMON', 'n')} | LOG_BUF_SHIFT {cfg.get('LOG_BUF_SHIFT')} | NR_CPUS {cfg.get('NR_CPUS')}",
        f"cpu: native {cfg.get('X86_NATIVE_CPU', 'absent')} | X86_64_VERSION {cfg.get('X86_64_VERSION', 'absent')} | mitigations {cfg.get('CPU_MITIGATIONS', '?')} | amd_pstate mode {cfg.get('X86_AMD_PSTATE_DEFAULT_MODE', '?')} | NTSYNC {cfg.get('NTSYNC', 'n')}",
        f"modules: {sum(1 for v in cfg.values() if v == 'm')} =m, {sum(1 for v in cfg.values() if v == 'y')} =y | compress {'zstd' if cfg.get('MODULE_COMPRESS_ZSTD') == 'y' else 'other/none'} | TRIM_UNUSED_KSYMS {cfg.get('TRIM_UNUSED_KSYMS', 'n')}",
    ]
    for line in facts_out:
        note(line)
    if d.rust_reason:
        warn(f"Rust: {d.rust_reason}")
    if d.fdo_reason:
        warn(f"FDO: {d.fdo_reason}")
    if soft:
        note(f"{len(soft)} soft (dependency-gated) entries differ: " + ", ".join(f"{op.symbol}" for op, _ in soft[:12]) + (" ..." if len(soft) > 12 else ""))
    if hard:
        warn(f"{len(hard)} hard contract entries unmet:")
        for op, actual in hard[:30]:
            hint = VERIFY_HINTS.get(op.symbol) or op.why
            say(f"    {C.YELLOW}!{C.RESET} wanted {op.render():<48} got {actual if actual is not None else 'absent':<12} {C.DIM}{hint}{C.RESET}")
        if len(hard) > 30:
            say(f"    ... and {len(hard) - 30} more")
        if p.g("verify", "strict"):
            raise VerifyError(f"Verification contract failed ({len(hard)} hard entries). Fix the profile, add symbols to verify.optional_symbols, or set verify.strict=false.")
    else:
        ok(f"Contract satisfied: {len(mx)} operations verified against the resolved .config")
    return VerifyReport(hard, soft, facts_out)


def save_config_snapshot(tree: Path, p: KernelProfile) -> Path:
    CONFIG_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    dest = snapshot_path(p)
    shutil.copy2(tree / ".config", dest)
    ok(f"Config snapshot saved: {dest}")
    return dest


def kernelrelease(tree: Path, env: Mapping[str, str]) -> str:
    krel = tree / "include" / "config" / "kernel.release"
    if krel.is_file():
        txt = krel.read_text(encoding="utf-8").strip()
        if txt:
            return txt
    cp = run(["make", "-s", "kernelrelease"], cwd=tree, env=env)
    lines = [ln.strip() for ln in (cp.stdout or "").splitlines() if ln.strip() and not ln.startswith(("make", "scripts/"))]
    if not lines:
        raise BuildError("Kbuild did not report a kernel release")
    return lines[-1]


# ---------------------------------------------------------------------------------------------------
# ThinLTO persistent cache
# ---------------------------------------------------------------------------------------------------
def link_thinlto_cache(tree: Path, p: KernelProfile, d: Derived) -> None:
    """Ask LLD to manage its cache; never walk/delete a live cache from Python."""
    if d.lto != "thin":
        return
    jobs = p.g("compiler", "jobs") or auto_jobs(host_facts(), d.lto)
    flags = f"--thinlto-jobs={jobs}"
    if p.g("compiler", "thinlto_cache"):
        cache = (THINLTO_CACHE_DIR / p.name).resolve()
        cache.mkdir(parents=True, exist_ok=True)
        link = tree / ".thinlto-cache"
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            raise BuildError(f"Expected a cache symlink, found {link}; use --fresh")
        link.symlink_to(cache, target_is_directory=True)
        size = p.g("compiler", "thinlto_cache_size_gb")
        flags += f" --thinlto-cache-dir=.thinlto-cache --thinlto-cache-policy=cache_size=0%:cache_size_bytes={size}g"
    # Exercise the exact flags with this host's Clang/LLD before a long kernel build.
    with tempfile.TemporaryDirectory(prefix=".lld-probe-", dir=tree) as probe_dir:
        probe = Path(probe_dir)
        (probe / "probe.c").write_text("int dusky_lto_probe(void) { return 42; }\n")
        if p.g("compiler", "thinlto_cache"):
            (probe / ".thinlto-cache").mkdir()
        run(["clang", "-flto=thin", "-fPIC", "-c", "probe.c", "-o", "probe.o"], cwd=probe, timeout=30)
        cp = run(["ld.lld", "-shared", "probe.o", "-o", "probe.so", *shlex.split(flags)],
                 cwd=probe, check=False, timeout=30)
        if cp.returncode or not (probe / "probe.so").is_file():
            raise BuildError(f"LLD rejected ThinLTO linker flags before kernel build: {cp.stdout.strip() or f'exit {cp.returncode}'}")
    makefile = tree / "Makefile"
    marker = "\n# Dusky LLD cache and parallelism\n"
    text = makefile.read_text().split(marker)[0]
    makefile.write_text(text + marker + f"ifndef KBUILD_EXTMOD\nifeq ($(CONFIG_LTO_CLANG_THIN),y)\nKBUILD_LDFLAGS += {flags}\nendif\nendif\n")
    ok(f"LLD ThinLTO jobs={jobs}, cache={'enabled' if p.g('compiler', 'thinlto_cache') else 'disabled'}")


# ---------------------------------------------------------------------------------------------------
# Build environment & compilation (make pacman-pkg -> linux-<flavor>{,-headers})
# ---------------------------------------------------------------------------------------------------
def needs_headers(facts: HostFacts) -> bool:
    return bool(facts.dkms_modules) or bool(facts.tools.get("dkms"))


def resolve_build_headers(p: KernelProfile, facts: HostFacts) -> bool:
    match p.g("compiler", "headers"):
        case "always":
            return True
        case "never":
            return False
        case _:
            return needs_headers(facts)


@functools.cache
def compiler_has_flag(compiler: str, flag: str) -> bool:
    return run([compiler, flag, "-Werror", "-x", "c", "-fsyntax-only", "/dev/null"], check=False).returncode == 0


def build_env(p: KernelProfile, d: Derived, facts: HostFacts, epoch: float) -> dict[str, str]:
    env = toolchain_env(p)
    env["LANG"] = env["LC_ALL"] = "C.UTF-8"
    kbuild_user = (p.g("dusky", "user") or "").strip() or os.environ.get("KBUILD_BUILD_USER") or os.environ.get("SUDO_USER") or os.environ.get("USER") or os.environ.get("LOGNAME") or Path.home().name or "builduser"
    kbuild_host = (p.g("dusky", "hostname") or "").strip() or os.environ.get("KBUILD_BUILD_HOST") or platform.node() or "archlinux"
    env["KBUILD_BUILD_USER"] = kbuild_user
    env["KBUILD_BUILD_HOST"] = kbuild_host
    if p.g("dusky", "reproducible"):
        env["KBUILD_BUILD_VERSION"] = "1"
        env["KBUILD_BUILD_TIMESTAMP"] = datetime.fromtimestamp(epoch, UTC).strftime("%a %b %d %H:%M:%S UTC %Y")
        env["SOURCE_DATE_EPOCH"] = str(int(epoch))
    if d.toolchain == "llvm":
        env["LLVM"] = "1"
        env["LLVM_IAS"] = "1"
    else:
        env["CC"] = "gcc"
        env["HOSTCC"] = "gcc"
        env["LD"] = "ld.bfd"
    if p.g("compiler", "ccache") and have("ccache"):
        env["CC"] = "ccache clang" if d.toolchain == "llvm" else "ccache gcc"
        env["CCACHE_BASEDIR"] = str(SRC_DIR)
        env["CCACHE_DIR"] = str(CCACHE_DIR)
    kcflags = list(d.kcflags)
    if p.g("compiler", "optimize") == "o3":
        kcflags.append("-O3")
    # Preserve x86 kernel no-FPU contract when explicit -march enables vector features.
    if kcflags:
        kcflags += ["-mno-sse", "-mno-sse2", "-mno-mmx", "-mno-avx", "-mno-avx2"]
        # Match upstream X86_NATIVE_CPU: general kernel code cannot use APX EGPR yet.
        compiler = "clang" if d.toolchain == "llvm" else "gcc"
        if compiler_has_flag(compiler, "-mno-apx-features=egpr"):
            kcflags.append("-mno-apx-features=egpr")
    if kcflags:
        env["KCFLAGS"] = " ".join(kcflags)
    rustflags = list(d.krustflags)
    if d.rust and p.g("compiler", "optimize") == "o3":
        rustflags.append("-Copt-level=3")
    if d.rust and rustflags:
        env["KRUSTFLAGS"] = " ".join(rustflags)
    if d.fdo != "none":
        pdir = Path(p.g("compiler", "fdo_profile_dir")).expanduser() if p.g("compiler", "fdo_profile_dir") else STATE_DIR / "fdo" / p.name
        if (pdir / "kernel.afdo").is_file():
            env["CLANG_AUTOFDO_PROFILE"] = str(pdir / "kernel.afdo")
        if d.fdo == "autofdo_propeller":
            env["CLANG_PROPELLER_PROFILE_PREFIX"] = str(pdir / "propeller")
    jobs = p.g("compiler", "jobs") or auto_jobs(facts, d.lto)
    env["MAKEFLAGS"] = f"-j{jobs}"
    env["KCONFIG_NOTIMESTAMP"] = "1"
    env["ZSTD_CLEVEL"] = "9"
    return env


def check_disk_space(lto: str, *, installing: bool = False, building: bool = True) -> None:
    if building:
        BUILD_DIR.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(BUILD_DIR).free
        need = (30 if lto == "full" else 22) << 30
        if free < (8 << 30):
            raise BuildError(f"Only {fmt_bytes(free)} free in {BUILD_DIR}; a kernel build needs >= {fmt_bytes(need)}")
        if free < need:
            warn(f"{fmt_bytes(free)} free in {BUILD_DIR}; builds with debug info can exceed {fmt_bytes(need)}")
        # Check persistent storage disk if building in a separate RAM workspace
        p_dir = STORAGE.get("persistent_dir")
        if p_dir and Path(p_dir).exists() and Path(p_dir).resolve() != BUILD_DIR.resolve():
            p_free = shutil.disk_usage(p_dir).free
            if p_free < (10 << 30):
                warn(f"Persistent disk {p_dir} has only {fmt_bytes(p_free)} free; post-build cache sync needs ~10 GiB")
    if not installing:
        return
    boot = Path("/boot")
    if boot.exists():
        try:
            boot_free = shutil.disk_usage(boot).free
            if boot_free < 200 * 1024 * 1024:
                raise BuildError(f"Only {fmt_bytes(boot_free)} free in /boot; at least 200 MiB required for kernel image and initramfs")
        except OSError:
            pass
    mod_dir = Path("/usr/lib/modules")
    if mod_dir.exists():
        try:
            mod_free = shutil.disk_usage(mod_dir).free
            if mod_free < 500 * 1024 * 1024:
                warn(f"Low disk space on {mod_dir}: {fmt_bytes(mod_free)} free")
        except OSError:
            pass


def prune_obsolete_build_trees(profile_name: str, current_tree: str, keep_limit: int = 1) -> None:
    """Automatically remove obsolete build trees and packages to prevent persistent disk exhaustion.

    Retains the active tree plus up to keep_limit previous builds for fast rebuilds.
    Incomplete trees (failed or aborted before vmlinux) are pruned immediately.
    """
    src_parent = STORAGE.get("persistent_dir", BUILD_DIR) / "src"
    if not src_parent.is_dir():
        return
    pattern = f"+{profile_name}-"
    try:
        matching = [d for d in src_parent.iterdir() if d.is_dir() and pattern in d.name]
    except OSError:
        return

    to_check: list[Path] = []
    for d in matching:
        if d.name == current_tree:
            continue
        if not (d / "vmlinux").is_file():
            try:
                shutil.rmtree(d)
                ok(f"Pruned incomplete build workspace: {d.name}")
            except OSError:
                pass
        else:
            to_check.append(d)

    to_check.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for old_tree in to_check[keep_limit:]:
        try:
            size = sum(f.stat().st_size for f in old_tree.rglob("*") if f.is_file())
            shutil.rmtree(old_tree)
            ok(f"Pruned obsolete build tree: {old_tree.name} (freed {fmt_bytes(size)})")
        except OSError:
            pass


def check_pacman_preflight(require_install: bool = True) -> None:
    """Validate pacman readiness before starting compilation to prevent late install failures."""
    if not require_install:
        return
    db_lck = Path("/var/lib/pacman/db.lck")
    if db_lck.exists():
        holder = ""
        try:
            cp = run(["fuser", str(db_lck)], check=False, capture=True)
            holder = (cp.stdout or "").strip()
        except DuskyError:
            pass
        if not holder:
            try:
                cp = run(["pgrep", "-x", "pacman|paru|yay|makepkg"], check=False, capture=True)
                holder = (cp.stdout or "").strip()
            except DuskyError:
                pass
        if holder:
            raise BuildError(f"Pacman database is currently locked by active process PID(s): {holder} ({db_lck}). "
                             "Please complete or close other package manager operations before compiling.")
        else:
            warn(f"Stale pacman database lock detected ({db_lck}) but no active process found holding it. "
                 "If a previous package manager operation crashed, remove it with: sudo rm /var/lib/pacman/db.lck")
    try:
        run(["pacman", "-Qq", "pacman"], check=True, capture=True, timeout=10)
    except Exception as e:
        raise BuildError(f"Pacman sanity check failed before compilation: {e}")


def check_dependencies(p: KernelProfile, facts: HostFacts, d_toolchain: str, want_rust: bool) -> None:
    if os.geteuid() == 0:
        raise DependencyError("makepkg refuses to run as root; run this tool as a regular user (sudo is requested only for installation)")
    if platform.machine() != "x86_64":
        raise DependencyError("This engine targets x86-64 Arch Linux")
    if not have("pacman"):
        raise DependencyError("Arch Linux with pacman is required")
    packages = {"base-devel", "bc", "cpio", "kmod", "pahole", "python", "rsync", "libelf", "openssl", "gettext", "tar", "xz", "zstd", "curl", "git", "gnupg"}
    packages.update({"clang", "lld", "llvm"} if d_toolchain == "llvm" else {"gcc", "binutils"})
    if p.g("compiler", "ccache"):
        packages.add("ccache")
    if want_rust:
        packages.update({"rust", "rust-src", "rust-bindgen"})
    missing = [pkg for pkg in sorted(packages) if run(["pacman", "-Q", pkg], check=False).returncode]
    if missing:
        if not (interactive() or ASSUME_YES):
            raise DependencyError("Install dependencies interactively or use --yes: " + ", ".join(missing))
        if not ask_yes("Install missing build dependencies: " + ", ".join(missing) + "?", True):
            raise DependencyError("Missing dependencies: " + ", ".join(missing))
        PRIV.run(["pacman", "-S", "--needed", "--noconfirm", *missing], capture=False)
        if any(run(["pacman", "-Q", pkg], check=False).returncode for pkg in missing):
            raise DependencyError("Dependency installation incomplete")
    if p.g("modules", "modprobed_db") and not p.g("modules", "modprobed_db_path") and not p.g("meta", "manifest_path") and not have("modprobed-db"):
        if not (interactive() or ASSUME_YES):
            raise DependencyError("Install modprobed-db interactively or use --yes")
        if ask_yes("Install modprobed-db from AUR to collect this machine's modules?", True):
            if not install_aur_package("modprobed-db"):
                raise DependencyError("Could not install modprobed-db")
    if d_toolchain == "llvm" and version_tuple(tool_version(["clang", "--version"])) < (21,):
        raise DependencyError("Clang 21+ required; update the Arch toolchain")
    if p.g("compiler", "ccache") and have("ccache"):
        note("ccache available")
    clang_v = facts.tools.get("clang", "")
    if d_toolchain == "llvm" and clang_v and version_tuple(clang_v) < (19,):
        warn(f"clang {clang_v} detected; Linux 7.x LTO/kCFI/AutoFDO paths are validated with clang >= 21")
    pahole_v = facts.tools.get("pahole", "")
    if pahole_v and version_tuple(pahole_v) < (1, 27):
        warn(f"pahole {pahole_v} is old; BTF for Rust/LTO kernels needs >= 1.27")


def rust_probe(tree: Path, env: Mapping[str, str]) -> tuple[bool, str]:
    cp = run(["make", "rustavailable"], cwd=tree, env=env, check=False, timeout=300)
    return cp.returncode == 0, cp.stdout or ""


def prepare_extmod_build(tree: Path, d: Derived, env: Mapping[str, str]) -> None:
    """Keep the selected target/toolchain in the headers for future DKMS builds."""
    path = tree / "Makefile"
    text = path.read_text()
    start = "# Dusky external-module toolchain\n"
    end = "# End Dusky external-module toolchain\n"
    if start in text:
        a = text.index(start); b = text.index(end, a) + len(end)
        text = text[:a] + text[b:]
    anchor = "export KBUILD_EXTMOD\n"
    if anchor not in text:
        raise BuildError("Kbuild external-module layout changed; cannot prepare headers")
    # Some external-module wrappers (notably NVIDIA's) pass LD=ld on their
    # command line. That overrides ordinary Makefile assignments and cannot
    # link ThinLTO bitcode. Keep the linker paired with the built kernel.
    toolchain = "LLVM ?= 1\noverride LD := ld.lld\n" if d.toolchain == "llvm" else "CC = gcc\nLD = ld.bfd\n"
    text = text.replace(anchor, anchor + start + "ifdef KBUILD_EXTMOD\n" + toolchain + "endif\n" + end, 1)
    marker = "\n# Dusky external-module CPU flags\n"
    text = text.split(marker)[0]
    block = marker + "ifdef KBUILD_EXTMOD\n"
    if env.get("KCFLAGS"):
        block += f"KBUILD_CFLAGS += {env['KCFLAGS']}\n"
    if env.get("KRUSTFLAGS"):
        block += f"KBUILD_RUSTFLAGS += {env['KRUSTFLAGS']}\n"
    path.write_text(text + block + "endif\n")


def stage_runtime_package(tree: Path, p: KernelProfile, d: Derived) -> None:
    """Attach the resolved profile and boot services to upstream's kernel package."""
    stage = tree / ".dusky" / "runtime"
    stage.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SCRIPT_DIR / "kernel_runtime.py", stage / "runtime.py")
    (stage / "profile.json").write_text(json.dumps({"kernelrelease": d.kernelrelease, "profile": p.sections}, indent=2) + "\n")
    base = f"/usr/lib/dusky-kernel/{p.pkgbase}"
    unit = f"{p.pkgbase}-tuning.service"
    body = (f"[Unit]\nDescription=Kernel tuning for {p.pkgbase}\nConditionKernelVersion={d.kernelrelease}\n"
            "After=systemd-sysctl.service systemd-modules-load.service swap.target sys-kernel-debug.mount\n"
            f"[Service]\nType=oneshot\nExecStart=/usr/bin/python {base}/runtime.py {base}/profile.json\nRemainAfterExit=yes\n")
    units = []
    if p.g("runtime", "enabled"):
        (stage / unit).write_text(body)
        units.append(unit)
    if p.g("runtime", "enabled") and p.g("scheduler", "scx") != "none":
        unit = f"{p.pkgbase}-scheduler.service"
        (stage / unit).write_text(
            f"[Unit]\nDescription=Kernel scheduler for {p.pkgbase}\nConditionKernelVersion={d.kernelrelease}\n"
            f"After={p.pkgbase}-tuning.service\nConflicts=scx.service scx_loader.service\n"
            f"[Service]\nExecStart=/usr/bin/python {base}/runtime.py {base}/profile.json --scheduler\n")
        units.append(unit)
    pkgbuild = tree / "scripts/package/PKGBUILD"
    text = pkgbuild.read_text()
    marker = "\n# Dusky runtime package integration\n"
    end_marker = "# End Dusky runtime package integration\n"
    if marker in text:
        start = text.index(marker)
        end = text.index(end_marker, start) + len(end_marker)
        text = text[:start] + text[end:]
    # Rename the upstream implementation, keeping its internal body untouched.
    # Evaluated after upstream declares package_<pkgbase>, which calls _package dynamically.
    extra = marker + "eval \"$(declare -f _package | sed '1s/_package/_dusky_upstream_package/')\"\n"
    extra += '_package() {\n  _dusky_upstream_package\n'
    if p.g("runtime", "enabled"):
        extra += '  depends+=(python systemd util-linux kmod)\n'
    if p.g("runtime", "enabled") and p.g("scheduler", "scx") != "none":
        extra += '  depends+=(scx-scheds)\n'
    if p.g("runtime", "enabled"):
        extra += f'  install -Dm644 "${{srctree}}/.dusky/runtime/runtime.py" "${{pkgdir}}{base}/runtime.py"\n'
    extra += f'  install -Dm644 "${{srctree}}/.dusky/runtime/profile.json" "${{pkgdir}}{base}/profile.json"\n'
    for unit in units:
        extra += f'  install -Dm644 "${{srctree}}/.dusky/runtime/{unit}" "${{pkgdir}}/usr/lib/systemd/system/{unit}"\n'
        extra += '  install -dm755 "${pkgdir}/usr/lib/systemd/system/multi-user.target.wants"\n'
        extra += f'  ln -sf "../{unit}" "${{pkgdir}}/usr/lib/systemd/system/multi-user.target.wants/{unit}"\n'
    extra += '}\n'
    if resolve_build_headers(p, d.facts):
        extra += "eval \"$(declare -f _package-headers | sed '1s/_package-headers/_dusky_upstream_headers/')\"\n"
        dependencies = "clang lld llvm" if d.toolchain == "llvm" else "gcc binutils"
        extra += f"_package-headers() {{\n  _dusky_upstream_headers\n  depends+=({dependencies})\n}}\n"
    extra += end_marker
    anchor = 'for _p in "${pkgname[@]}"; do'
    if anchor not in text:
        raise BuildError("Upstream PKGBUILD layout changed; cannot attach runtime files")
    pkgbuild.write_text(text.replace(anchor, extra + anchor, 1))


def compile_kernel(tree: Path, p: KernelProfile, d: Derived, env: Mapping[str, str], facts: HostFacts) -> list[Path]:
    rule("Compile kernel & build pacman packages")
    stage_runtime_package(tree, p, d)
    jobs = p.g("compiler", "jobs") or auto_jobs(facts, d.lto)
    pkgdest = PKGDEST_DIR / p.name
    pkgdest.mkdir(parents=True, exist_ok=True)
    b_env = dict(env)
    b_env["PACMAN_PKGBASE"] = p.pkgbase
    b_env["PKGDEST"] = str(pkgdest)
    packager_val = os.environ.get("PACKAGER")
    if not packager_val:
        k_user = b_env.get("KBUILD_BUILD_USER") or os.environ.get("SUDO_USER") or os.environ.get("USER") or os.environ.get("LOGNAME") or Path.home().name or "builduser"
        k_host = b_env.get("KBUILD_BUILD_HOST") or platform.node() or "localhost"
        packager_val = f"{k_user} <{k_user}@{k_host}>"
    b_env["PACKAGER"] = packager_val
    b_env["PACMAN_EXTRAPACKAGES"] = "headers" if resolve_build_headers(p, d.facts) else ""
    b_env["MAKEFLAGS"] = f"-j{jobs}"
    b_env["MAKEPKGOPTS"] = "--force"
    b_env["ZSTD_CLEVEL"] = "9"
    info(f"pkgbase={p.pkgbase} jobs={jobs} lto={d.lto} toolchain={d.toolchain} headers={'yes' if b_env['PACMAN_EXTRAPACKAGES'] else 'no'} rust={'yes' if d.rust else 'no'}")
    if d.lto == "full":
        warn("Full LTO: the final vmlinux link is single-threaded and memory hungry; expect a long silent phase")
    start_wall = time.time()
    is_clean = not (tree / "vmlinux").exists()
    if is_clean:
        info("Build type: fresh compile (new kernel release or clean tree; no prior objects)")
    else:
        info("Build type: incremental rebuild (reusing existing object files for faster compilation)")
    if have("ccache"):
        note(f"ccache active ({CCACHE_DIR})")
    if d.lto == "thin":
        note(f"ThinLTO cache active ({THINLTO_CACHE_DIR})")
    expected_steps, expected_seconds = history_estimate(p.name, d.lto, current_jobs=jobs, is_clean=is_clean)
    with Live(p.pkgbase, expected_steps, expected_seconds, lto=d.lto) as live:
        ret = run_stream(["make", f"-j{jobs}", "pacman-pkg"], cwd=tree, env=b_env, on_line=live.feed)
        duration = live.elapsed
        steps = live.steps
        errors = list(live.errors)
        tail = list(live.tail)
    d.compile_duration = duration
    d.compile_steps = steps
    record_history({"profile": p.name, "version": d.version, "lto": d.lto, "toolchain": d.toolchain, "jobs": jobs, "duration": round(duration, 1),
                    "steps": steps, "clean": is_clean, "success": ret == 0, "ts": datetime.now(UTC).isoformat()})
    if ret != 0:
        err(f"Kernel build failed (exit {ret}) after {fmt_duration(duration)}")
        for line in (errors or tail)[-20:]:
            say(f"    {C.RED}{line}{C.RESET}")
        raise BuildError(f"make pacman-pkg failed (exit {ret}); full log: {JOURNAL.path}")
    pkgs = sorted((f for f in pkgdest.glob(f"{p.pkgbase}*.pkg.tar*") if not f.name.endswith(".sig") and f.stat().st_mtime >= start_wall - 5 and _pkg_file_pkgbase(f) == p.pkgbase), key=lambda f: f.name)
    if not pkgs:
        raise BuildError(f"No packages produced in {pkgdest}")
    ok(f"Built in {fmt_duration(duration)} ({steps:,} kbuild steps):")
    for f in pkgs:
        say(f"    {C.GREEN}•{C.RESET} {f.name} ({fmt_bytes(f.stat().st_size)})")
    # Prune older packages for this profile to prevent package directory bloat
    all_pkgs = sorted(pkgdest.glob(f"{p.pkgbase}*.pkg.tar*"), key=lambda f: f.stat().st_mtime, reverse=True)
    h_pkgs = [f for f in all_pkgs if "-headers-" in f.name and not f.name.endswith(".sig")]
    k_pkgs = [f for f in all_pkgs if "-headers-" not in f.name and not f.name.endswith(".sig")]
    for old_pkg in h_pkgs[2:] + k_pkgs[2:]:
        try:
            old_pkg.unlink(missing_ok=True)
            old_pkg.with_suffix(old_pkg.suffix + ".sig").unlink(missing_ok=True)
        except OSError:
            pass
    return pkgs


_DKMS_STATUS_RE: Final = re.compile(r"^([^/,]+)/([^,]+),\s*([^,]+),\s*[^:]+:\s*(.+)$")


def _kernelreleases_for_pkgbases(pkgbases: set[str]) -> dict[str, str]:
    """Map installed kernelrelease -> pkgbase via /usr/lib/modules/*/pkgbase."""
    found: dict[str, str] = {}
    mods = Path("/usr/lib/modules")
    if not mods.is_dir():
        return found
    for d in mods.iterdir():
        if not d.is_dir():
            continue
        try:
            base = (d / "pkgbase").read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            continue
        if base and base[0].strip() in pkgbases:
            found[d.name] = base[0].strip()
    return found


def audit_dkms(krel: str, pkgbase: str) -> bool:
    """Verify DKMS for the kernel just installed, never an older release of its flavor."""
    if not have("dkms"):
        return True
    try:
        targets = _kernelreleases_for_pkgbases({pkgbase})
    except OSError:
        targets = {}
    if targets.get(krel) != pkgbase:
        warn(f"Installed kernel {krel} has no matching pkgbase {pkgbase}")
        return False

    def status() -> tuple[set[tuple[str, str]], dict[tuple[str, str], str]] | None:
        try:
            cp = run(["dkms", "status"], check=False, timeout=60)
        except DuskyError as e:
            warn(f"dkms status failed: {e}")
            return None
        if cp.returncode:
            warn("dkms status failed; installed module state could not be verified")
            return None
        registered: set[tuple[str, str]] = set()
        current: dict[tuple[str, str], str] = {}
        for line in (cp.stdout or "").splitlines():
            head = re.match(r"^([^/,]+)/([^,:]+)", line.strip())
            if head:
                registered.add((head.group(1), head.group(2)))
            match = _DKMS_STATUS_RE.match(line.strip())
            if match and match.group(3) == krel:
                current[(match.group(1), match.group(2))] = match.group(4).strip()
        return registered, current

    before = status()
    if before is None:
        return False
    registered, current = before
    if not registered or all(current.get(module, "").startswith("installed") for module in registered):
        ok(f"DKMS modules up to date for {krel} ({pkgbase})")
        return True
    PRIV.ensure()
    result = PRIV.run(["dkms", "autoinstall", "-k", krel], check=False)
    after = status()
    if after is None:
        return False
    registered, current = after
    for mod, ver in sorted(registered):
        if current.get((mod, ver), "").startswith("built"):
            warn(f"DKMS {mod}/{ver} for {krel} is built but not installed; forcing installation")
            PRIV.run(["dkms", "install", "--force", f"{mod}/{ver}", "-k", krel], check=False)
    final = status()
    if final is None:
        return False
    registered, current = final
    missing = [f"{mod}/{ver} ({current.get((mod, ver), 'missing')})" for mod, ver in sorted(registered)
               if not current.get((mod, ver), "").startswith("installed")]
    if missing:
        warn(f"DKMS missing for {krel}: {', '.join(missing)}")
        if result.returncode:
            warn(f"dkms autoinstall exited {result.returncode}; review /var/lib/dkms/<module>/<version>/build/make.log")
        return False
    ok(f"DKMS modules up to date for {krel} ({pkgbase})")
    return True


def prepare_nvidia_compat() -> bool:
    """Prepare only a validated open-driver overlay on the actual install target."""
    if "nvidia" not in _gpu_vendors():
        return False
    package = run(["pacman", "-Q", "nvidia-open-dkms"], check=False)
    fields = package.stdout.split()
    if package.returncode or len(fields) != 2:
        return False  # Nouveau, proprietary or prebuilt modules are not this repair's target.
    version = fields[1].split(":", 1)[-1].rsplit("-", 1)[0]
    if version != kernel_nvidia.VERSION:
        warn(f"NVIDIA open {version} is outside the validated RTD3 repair; "
             "review compat/AGENTS.md and check --doctor after boot")
        return False
    registered = Path("/var/lib/dkms/nvidia")
    source = registered / kernel_nvidia.VERSION / "source"
    if not source.is_dir():
        raise BuildError(f"Installed nvidia-open-dkms {version} has no registered source at {source}")
    try:
        files = kernel_nvidia.repair_files(source, SCRIPT_DIR / "compat", Path("/etc/dkms"))
        PRIV.write_files(files)
    except (OSError, ValueError) as e:
        raise BuildError(f"NVIDIA compatibility preparation failed: {e}") from e
    info(f"NVIDIA open {kernel_nvidia.VERSION}: source checked; RTD3 compatibility verification enabled")
    return True


def verify_nvidia_compat(kernelrelease: str) -> None:
    """DKMS 'installed' is insufficient: require the fixed ELF import for this driver."""
    try:
        if not kernel_nvidia.target_needs_init_fs(Path("/usr/lib/modules") / kernelrelease / "build"):
            return

        def repaired() -> bool:
            version = run(["modinfo", "-k", kernelrelease, "-F", "version", "nvidia"]).stdout.strip()
            if version != kernel_nvidia.VERSION:
                raise ValueError(f"installed NVIDIA version {version!r} differs from validated {kernel_nvidia.VERSION}")
            filename = run(["modinfo", "-k", kernelrelease, "-F", "filename", "nvidia"]).stdout.strip()
            return kernel_nvidia.module_uses_init_fs(Path(filename))

        if not repaired():
            info(f"Rebuilding NVIDIA {kernel_nvidia.VERSION} for {kernelrelease} with the RTD3 fix")
            for action in ("build", "install"):
                PRIV.run(["dkms", action, f"nvidia/{kernel_nvidia.VERSION}", "-k", kernelrelease, "--force"], capture=False)
            if not repaired():
                raise ValueError("rebuilt NVIDIA module still lacks userspace_init_fs; boot promotion stopped")
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        raise BuildError(f"NVIDIA compatibility verification failed: {e}") from e
    ok(f"NVIDIA RTD3 compatibility verified in the installed module for {kernelrelease}")


def ensure_install_dependencies(p: KernelProfile) -> None:
    packages = ["mkinitcpio", "python", "systemd", "util-linux", "kmod"]
    if p.g("scheduler", "scx") != "none" and p.g("runtime", "enabled"):
        packages.append("scx-scheds")
    missing = [pkg for pkg in packages if run(["pacman", "-Q", pkg], check=False).returncode]
    if missing:
        if not (interactive() or ASSUME_YES) or not ask_yes("Install kernel runtime dependencies: " + ", ".join(missing) + "?", True):
            raise DependencyError("Missing installation dependencies: " + ", ".join(missing))
        PRIV.run(["pacman", "-S", "--needed", "--noconfirm", *missing], capture=False)


def prune_stale_modules(kernelrelease: str) -> None:
    """Remove orphaned module files that conflict with built-in modules.

    When kernel-modules-hook is installed or when rebuilding a kernel release
    after Kconfig symbols transition from =m to =y (built-in), pacman or ALPM
    hooks (such as 10-linux-modules-post.hook) restore older module files from
    /usr/lib/modules/backup/<krel> into /usr/lib/modules/<krel>. These stale
    module files conflict with built-in drivers, corrupt depmod's dependency
    graph, and cause 'Exec format error' at runtime when modprobe attempts
    to insert them for dependent drivers (e.g. asus-wmi).
    """
    mod_dir = Path(f"/usr/lib/modules/{kernelrelease}")
    builtin_file = mod_dir / "modules.builtin"
    if not builtin_file.is_file():
        return
    try:
        builtins = {
            line.strip().removeprefix("kernel/").removesuffix(".ko")
            for line in builtin_file.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip()
        }
    except OSError:
        return

    to_remove: list[str] = []
    kernel_dir = mod_dir / "kernel"
    if kernel_dir.is_dir():
        for path in kernel_dir.glob("**/*"):
            if not path.is_file():
                continue
            name = path.name
            if not any(name.endswith(ext) for ext in (".ko", ".ko.zst", ".ko.xz", ".ko.gz")):
                continue
            rel = path.relative_to(kernel_dir).as_posix()
            for ext in (".ko.zst", ".ko.xz", ".ko.gz", ".ko"):
                if rel.endswith(ext):
                    rel = rel[:-len(ext)]
                    break
            if rel in builtins:
                to_remove.append(str(path))

    if to_remove:
        info(f"Removing {len(to_remove)} stale module files conflicting with built-in drivers...")
        PRIV.run(["rm", "-f", *to_remove], capture=False)
        PRIV.run(["depmod", "-a", kernelrelease], capture=False)
        ok(f"Pruned {len(to_remove)} conflicting module files and updated module dependencies")


def install_packages(pkgs: Sequence[Path], profile: KernelProfile, kernelrelease: str) -> None:
    rule("Install packages (pacman -U)")
    ensure_install_dependencies(profile)
    PRIV.ensure()
    # Must precede pacman: its DKMS hook may build the driver immediately.
    nvidia_compat = prepare_nvidia_compat()
    # Ensure /etc/mkinitcpio.d/<pkgbase>.preset exists so the pacman mkinitcpio hook runs for this kernel
    preset_path = Path(f"/etc/mkinitcpio.d/{profile.pkgbase}.preset")
    if not preset_path.is_file():
        preset_content = (
            f"# mkinitcpio preset for {profile.pkgbase} (auto-generated by {APP_NAME})\n"
            f'ALL_config="/etc/mkinitcpio.conf"\n'
            f'ALL_kver="/boot/vmlinuz-{profile.pkgbase}"\n\n'
            f"PRESETS=('default' 'fallback')\n\n"
            f'default_image="/boot/initramfs-{profile.pkgbase}.img"\n\n'
            f'fallback_image="/boot/initramfs-{profile.pkgbase}-fallback.img"\n'
            f'fallback_options="-S autodetect"\n'
        )
        PRIV.write_files({preset_path: (preset_content, "0644")})
        ok(f"Created mkinitcpio preset: {preset_path}")

    install_started = time.time()
    PRIV.run(["pacman", "-U", "--noconfirm", *[str(x) for x in pkgs]], capture=False)
    ok("Kernel packages installed (mkinitcpio and DKMS pacman hooks have run)")
    prune_stale_modules(kernelrelease)
    # Fresh-install path: (re)assert the modprobed-db writer so future
    # strict localmodconfig builds keep accumulating modules. User unit -> no sudo.
    if profile.g("modules", "modprobed_db") and have("modprobed-db"):
        ensure_modprobed_db_service(prompt=False)
    # Same-version reinstalls can leave DKMS objects stale ("built" instead of
    # "installed", then Exec format error at modprobe). Audit and force-rebuild.
    if not audit_dkms(kernelrelease, profile.pkgbase):
        raise BuildError("Kernel installed, but DKMS failed; boot configuration was not promoted. Review DKMS logs before rebooting.")
    if nvidia_compat:
        verify_nvidia_compat(kernelrelease)

    # DKMS repairs happen after pacman hooks: include their resulting modules.
    # Also regenerate stale images when an alpm hook failed or did not run.
    initramfs_img = Path(f"/boot/initramfs-{profile.pkgbase}.img")
    if have("dkms") or not initramfs_img.is_file() or initramfs_img.stat().st_mtime < install_started:
        info(f"Generating initramfs for {profile.pkgbase} via mkinitcpio -p...")
        PRIV.run(["mkinitcpio", "-p", profile.pkgbase], capture=False)

# ---------------------------------------------------------------------------------------------------
# Bootloader integration
# ---------------------------------------------------------------------------------------------------
def base_cmdline_tokens(facts: HostFacts, *, drop_managed: bool = True) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for tok in shlex.split(facts.cmdline):
        key = tok.split("=", 1)[0]
        if key in ("BOOT_IMAGE", "initrd", "initrdefi") or (drop_managed and key in MANAGED_CMDLINE_KEYS):
            continue
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def uki_preset(pkgbase: str) -> bool:
    preset = _read(f"/etc/mkinitcpio.d/{pkgbase}.preset")
    return any(re.match(r"^\s*\w+_uki=", line) for line in preset.splitlines())


def write_bls_entries(p: KernelProfile, facts: HostFacts, d: Derived) -> None:
    if "systemd-boot" not in facts.bootloaders or not p.g("boot", "write_entries"):
        return
    if uki_preset(p.pkgbase):
        note("mkinitcpio builds a UKI for this flavor; systemd-boot discovers it automatically (no entry written)")
        return
    root = facts.xbootldr or facts.esp
    has_loader = Path(root, "loader").is_dir() if root else False
    if not has_loader and root:
        has_loader = PRIV.run(["test", "-d", f"{root}/loader"], check=False).returncode == 0
    if not root or not has_loader:
        warn("systemd-boot detected but no loader/ directory found on ESP/XBOOTLDR; skipping entries")
        return
    if Path(root).resolve() != Path("/boot").resolve():
        warn(f"Kernel images live in /boot but systemd-boot reads {root}; switch mkinitcpio to UKI or mount XBOOTLDR at /boot")
        return
    params = base_cmdline_tokens(facts, drop_managed=p.g("boot", "cmdline") != "print")
    if p.g("boot", "cmdline") == "entry":
        params += flavor_cmdline(p, facts)
    ucode: list[str] = []
    if not facts.microcode_hook:
        for img in ("intel-ucode.img", "amd-ucode.img"):
            if Path("/boot", img).is_file():
                ucode.append(f"initrd  /{img}")
    entries_dir = Path(root) / "loader" / "entries"
    files: dict[Path, tuple[str, str]] = {}
    default_body = [f"title   Dusky {p.name}", f"version {d.kernelrelease or d.version}", f"sort-key dusky-{p.suffix}", f"linux   /vmlinuz-{p.pkgbase}",
                    *ucode, f"initrd  /initramfs-{p.pkgbase}.img", "options " + " ".join(params)]
    files[entries_dir / f"{p.pkgbase}.conf"] = ("\n".join(default_body) + "\n", "0644")

    fallback_img = f"initramfs-{p.pkgbase}-fallback.img"
    fallback_conf = entries_dir / f"{p.pkgbase}-fallback.conf"
    has_fallback = (Path(root) / fallback_img).is_file() or (Path("/boot") / fallback_img).is_file()
    if has_fallback:
        fb_body = [f"title   Dusky {p.name} (fallback initramfs)", f"version {d.kernelrelease or d.version}", f"sort-key dusky-{p.suffix}", f"linux   /vmlinuz-{p.pkgbase}",
                   *ucode, f"initrd  /{fallback_img}", "options " + " ".join(params)]
        files[fallback_conf] = ("\n".join(fb_body) + "\n", "0644")
    elif fallback_conf.is_file():
        PRIV.run(["rm", "-f", str(fallback_conf)], check=False)

    PRIV.write_files(files)
    ok(f"systemd-boot entries written: {', '.join(f.name for f in files)}")

    if not p.g("boot", "set_default"):
        return
    # Update loader.conf only when explicitly selected in the profile.
    loader_conf = Path(root) / "loader" / "loader.conf"
    if loader_conf.is_file():
        txt = loader_conf.read_text(encoding="utf-8")
        if re.search(r"^default\s+", txt, re.M):
            new_txt = re.sub(r"^default\s+.*$", f"default {p.pkgbase}.conf", txt, flags=re.M)
        else:
            new_txt = f"default {p.pkgbase}.conf\n" + txt
        m_timeout = re.search(r"^timeout\s+(\d+)", new_txt, re.M)
        if m_timeout and int(m_timeout.group(1)) == 0:
            new_txt = re.sub(r"^timeout\s+\d+", "timeout 3", new_txt, flags=re.M)
        elif not m_timeout:
            new_txt += "timeout 3\n"
        PRIV.write_files({loader_conf: (new_txt, "0644")})
        ok(f"Updated {loader_conf} default -> {p.pkgbase}.conf (timeout 3s)")

    if have("bootctl"):
        PRIV.run(["bootctl", "set-default", f"{p.pkgbase}.conf"], check=False)
        ok(f"systemd-boot default entry set to {p.pkgbase}.conf")


def refresh_boot(p: KernelProfile, facts: HostFacts, d: Derived, *, kernel_install: bool) -> None:
    rule("Bootloader refresh")
    PRIV.ensure()
    if "grub" in facts.bootloaders and have("grub-mkconfig"):
        PRIV.run(["grub-mkconfig", "-o", "/boot/grub/grub.cfg"], capture=False)
        ok("GRUB configuration regenerated")
    if "refind" in facts.bootloaders:
        if not Path("/boot/refind_linux.conf").is_file() and have("mkrlconf"):
            PRIV.run(["mkrlconf"], check=False)
        ok("rEFInd auto-detects /boot/vmlinuz-* (options in /boot/refind_linux.conf)")
    if "limine" in facts.bootloaders and have("limine-update"):
        PRIV.run(["limine-update"], capture=False)
        ok("Limine entries updated")
    write_bls_entries(p, facts, d)
    if kernel_install and have("kernel-install") and d.kernelrelease:
        PRIV.run(["kernel-install", "add", d.kernelrelease, f"/usr/lib/modules/{d.kernelrelease}/vmlinuz"], check=False, capture=False)
    params = flavor_cmdline(p, facts)
    mode = p.g("boot", "cmdline")
    if mode == "bake":
        note("Flavor parameters are baked into CONFIG_CMDLINE (bootloader options still override): " + " ".join(params))
    else:
        info("Recommended kernel parameters for this flavor: " + " ".join(params))
    if not facts.bootloaders:
        warn("No supported bootloader detected (systemd-boot, GRUB, rEFInd, Limine); add /boot/vmlinuz-" + p.pkgbase + " manually")

# ---------------------------------------------------------------------------------------------------
# Cross-machine hardware bundles
# ---------------------------------------------------------------------------------------------------
def do_export_bundle(dest: Path | None, profile_name: str | None = None) -> Path:
    banner()
    rule("Export hardware bundle")
    if platform.machine() != "x86_64":
        raise ProfileError("Hardware bundles currently target x86-64 machines only")
    facts = host_facts()
    if not facts.uarch:
        if have("pacman") and (interactive() or ASSUME_YES) and ask_yes("Install clang to identify the target CPU precisely before exporting?", True):
            PRIV.run(["pacman", "-S", "--needed", "--noconfirm", "clang"], capture=False)
            host_facts.cache_clear()
            facts = host_facts()
        if not facts.uarch:
            warn("Precise CPU name unavailable; export will use the target ISA baseline. Install a current compiler on the target and re-export for exact CPU tuning.")
    hname = re.sub(r"[^a-z0-9]+", "_", platform.node().lower()).strip("_") or "machine"
    out = dest or (Path.home() / f"dusky_bundle_{hname}.tar.zst")
    out.parent.mkdir(parents=True, exist_ok=True)
    if have("modprobed-db"):
        run(["modprobed-db", "store"], check=False, timeout=60)

    selected = select_profile(ensure_profiles_exist(), profile_name, facts) if profile_name else None
    custom_db = selected.g("modules", "modprobed_db_path") if selected else ""
    if custom_db and not Path(custom_db).expanduser().is_absolute():
        custom_db = str(selected.path.parent / custom_db)
    db_src = resolve_modprobed_db(custom_db or None)
    manifest = facts.as_json() | {"format": "dusky_bundle_v3", "hostname": hname,
                                 "created_at": datetime.now(UTC).isoformat(), "app_version": APP_VERSION}
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        if selected is not None:
            (tmp / "profile.toml").write_text(render_profile_toml(selected.sections))
        if db_src is not None and _is_usable_db(db_src):
            try:
                shutil.copy2(db_src, tmp / "modprobed.db")
            except OSError as e:
                warn(f"could not stage modprobed.db ({db_src}): {e}")
                db_src = None
        if db_src is None:
            searched = ", ".join(str(c) for c in modprobed_db_candidates())
            warn(f"modprobed.db not found (searched: {searched}); bundle will contain lsmod.txt only -- run 'modprobed-db store' first")
        for src in (Path("/proc/cpuinfo"), Path("/proc/meminfo"), Path("/proc/cmdline")):
            (tmp / src.name).write_text(_read(src), encoding="utf-8")
        if have("lspci"):
            (tmp / "lspci.txt").write_text(run(["lspci", "-nn"], check=False).stdout or "", encoding="utf-8")
        if have("lsmod"):
            (tmp / "lsmod.txt").write_text(run(["lsmod"], check=False).stdout or "", encoding="utf-8")
        with tarfile.open(out, "w:zst") as tar:
            for f in sorted(tmp.iterdir()):
                tar.add(f, arcname=f.name)
    n = count_db_modules(db_src) if db_src else 0
    detail = f", {n} modules from {db_src}" if db_src else " (no modprobed.db; lsmod.txt fallback only)"
    ok(f"Exported {out} ({fmt_bytes(out.stat().st_size)}) -- uarch {facts.uarch or 'generic_v' + str(facts.psabi_level)}, {facts.threads} threads, {facts.mem_gib:.1f} GiB{detail}")
    return out


def do_import_bundle(src: Path, profile_name: str | None = None) -> str:
    banner()
    rule("Import hardware bundle")
    if not src.is_file():
        raise DuskyError(f"Bundle not found: {src}")
    bundled_profile = None
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        try:
            with tarfile.open(src, "r:*") as tar:
                tar.extractall(tmp, filter="data")
        except (OSError, tarfile.TarError, EOFError) as e:
            raise ProfileError(f"Cannot read hardware bundle {src}: {e}") from e
        if (tmp / "profile.toml").is_file():
            bundled_profile = load_profile(tmp / "profile.toml")
        mf = tmp / "manifest.json"
        if not mf.is_file():
            raise DuskyError("Invalid bundle: manifest.json missing")
        # Validate before persisting imports or interpreting hardware fields.
        probe = profile_from_tweaks("import-check", "Validate bundle", "import-check", {})
        probe.set("meta", "manifest_path", str(mf))
        target = target_facts_for_profile(probe, host_facts())
        manifest = json.loads(mf.read_text(encoding="utf-8"))
        hname = re.sub(r"[^a-z0-9]+", "_", str(manifest.get("hostname", "remote"))).strip("_") or "remote"
        import_dir = IMPORT_DIR / (hname + "-" + sha256_file(src)[:12])
        import_dir.mkdir(parents=True, exist_ok=True)
        db_path = ""
        mods: set[str] = set()
        if (tmp / "modprobed.db").is_file():
            for line in (tmp / "modprobed.db").read_text(encoding="utf-8", errors="replace").splitlines():
                m = _extract_module_name(line)
                if m:
                    mods.add(m)
        if (tmp / "lsmod.txt").is_file():
            for line in (tmp / "lsmod.txt").read_text(encoding="utf-8", errors="replace").splitlines():
                m = _extract_module_name(line)
                if m:
                    mods.add(m)
        if mods:
            (import_dir / "modprobed.db").write_text("\n".join(sorted(mods)) + "\n", encoding="utf-8")
            db_path = str(import_dir / "modprobed.db")
            note(f"Imported {len(mods)} unique modules for {hname} from bundle")
        else:
            warn("bundle contained neither a usable modprobed.db nor a parseable lsmod.txt")
        shutil.copy2(tmp / "manifest.json", import_dir / "manifest.json")
    if manifest.get("format") != "dusky_bundle_v3":
        raise ProfileError("Re-export the target with this version; bundle v3 required")
    if not db_path:
        raise ProfileError("No target module census; collect modules on the target and export again")
    prof = (bundled_profile if bundled_profile is not None and profile_name is None else
            select_profile(ensure_profiles_exist(), profile_name, host_facts())).clone()
    arch = target.uarch or {1: "generic", 2: "generic_v2", 3: "generic_v3", 4: "generic_v4"}[target.psabi_level]
    target_suffix = prof.suffix + "-" + hname.replace("_", "-")
    pname = f"remote_{hname}_{prof.name}"
    prof.set("meta", "name", pname)
    prof.set("meta", "description", f"{prof.description}; target {hname}")
    prof.set("meta", "suffix", target_suffix if len(target_suffix) <= 40 else target_suffix[:31] + "-" + hashlib.sha256(target_suffix.encode()).hexdigest()[:8])
    prof.set("meta", "portable_package", True)
    prof.set("meta", "manifest_path", str(import_dir / "manifest.json"))
    prof.set("cpu", "arch", arch)
    prof.set("cpu", "march", "")
    prof.set("cpu", "nr_cpus", 0)
    prof.set("modules", "modprobed_db", True)
    prof.set("modules", "modprobed_db_path", db_path)
    prof.set("modules", "allow_lsmod_fallback", False)
    # Boot-entry preference belongs to the target. do_build never installs remote builds.
    validate_profile(prof)
    cross_validate(prof, target_facts_for_profile(prof, host_facts()), force=True)
    dest_dir = PROFILES_DIR if os.access(PROFILES_DIR, os.W_OK) or not PROFILES_DIR.exists() else USER_PROFILES_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    original_name = pname
    serial = 2
    while (dest_dir / f"{pname}.toml").exists():
        pname = f"{original_name}_{serial}"
        serial += 1
    prof.set("meta", "name", pname)
    (dest_dir / f"{pname}.toml").write_text(render_profile_toml(prof.sections, header=f"imported from {src.name}"), encoding="utf-8")
    ok(f"Registered profile '{pname}' ({dest_dir / (pname + '.toml')}); build with: --profile {pname} --no-install")
    return pname


# ---------------------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------------------
KBUILD_ENV_KEYS: Final = (
    "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "RUSTFLAGS", "HOSTCFLAGS", "HOSTCXXFLAGS", "HOSTRUSTFLAGS", "HOSTLDFLAGS",
    "ARCH", "SUBARCH", "CROSS_COMPILE", "KBUILD_OUTPUT", "KCONFIG_CONFIG", "KBUILD_KCONFIG",
    "LOCALVERSION", "MAKEFLAGS", "MFLAGS", "KBUILD_MAKEFLAGS", "KCFLAGS", "KCPPFLAGS", "KAFLAGS",
    "KRUSTFLAGS", "RUSTFLAGS_KERNEL", "RUSTFLAGS_MODULE", "CFLAGS_KERNEL", "CFLAGS_MODULE",
    "AFLAGS_KERNEL", "AFLAGS_MODULE", "LDFLAGS_MODULE", "KBUILD_EXTMOD", "KBUILD_EXTMOD_OUTPUT",
    "LLVM", "LLVM_IAS", "CC", "CXX", "LD", "AR", "NM", "OBJCOPY", "OBJDUMP", "READELF", "STRIP",
    "HOSTCC", "HOSTCXX", "HOSTLD", "HOSTAR", "RUSTC", "BINDGEN", "CLANG_AUTOFDO_PROFILE",
    "CLANG_PROPELLER_PROFILE_PREFIX", "LDFLAGS_vmlinux", "KBUILD_LDFLAGS", "MAKEPKGOPTS",
)


def toolchain_env(p: KernelProfile) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in KBUILD_ENV_KEYS}
    env["ARCH"] = "x86"
    env["LANG"] = env["LC_ALL"] = "C.UTF-8"
    if p.g("compiler", "toolchain") == "llvm":
        env["LLVM"] = "1"
    else:
        env.update(CC="gcc", HOSTCC="gcc", HOSTCXX="g++", LD="ld.bfd")
    return env


def validate_rust_cpu(d: Derived) -> None:
    if not d.rust:
        return
    cp = run(["rustc", "--print", "target-cpus", "--target=x86_64-unknown-none"], timeout=30)
    supported = {line.split()[0] for line in cp.stdout.splitlines() if line.strip()}
    for cpu in (d.march, d.mtune):
        if cpu not in ("native", "generic") and cpu not in supported:
            raise ProfileError(f"rustc cannot target/tune for {cpu}; update Rust or disable compiler.rust")


def validate_cpu_flags(p: KernelProfile) -> None:
    arch = p.g("cpu", "arch")
    march = {"generic": "x86-64", "generic_v2": "x86-64-v2", "generic_v3": "x86-64-v3", "generic_v4": "x86-64-v4"}.get(arch, arch)
    flags = [f"-march={march}", *shlex.split(p.g("cpu", "march"))]
    compiler = "clang" if p.g("compiler", "toolchain") == "llvm" else "gcc"
    cp = run([compiler, *flags, "-x", "c", "-fsyntax-only", "/dev/null"], check=False)
    if cp.returncode:
        raise ProfileError(f"{compiler} cannot target {arch}: {cp.stdout.strip()}")


def show_configuration(profile: KernelProfile, diff: Sequence[str]) -> None:
    rule(f"Profile: {profile.name}")
    if profile.description:
        say(f"  {C.DIM}{profile.description}{C.RESET}")
    table(["setting", "value"], [[k, v] for k, v in profile.summarize()])
    if diff:
        say("")
        say(f"  {C.YELLOW}ephemeral overrides / auto-adjustments:{C.RESET}")
        for line in diff:
            say(f"    {C.YELLOW}{line}{C.RESET}")


@contextmanager
def build_workspace_lock():
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    with (BUILD_DIR / ".build.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise BuildError(f"Another build is using {BUILD_DIR}") from e
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def checkpoint_run(cmd):
    # The signal handler sets _ABORT; saving after Ctrl-C must still be allowed.
    aborted = _ABORT.is_set()
    _ABORT.clear()
    try:
        return run(cmd)
    finally:
        if aborted:
            _ABORT.set()


@contextmanager
def storage_session(use_ram: bool, tree_name: str):
    global THINLTO_CACHE_DIR, CCACHE_DIR, RAM_RESERVE_GIB
    if not use_ram:
        yield
        return
    persistent = BUILD_DIR
    old_lto, old_ccache = THINLTO_CACHE_DIR, CCACHE_DIR
    settings = STORAGE | {"persistent_dir": persistent}
    try:
        with kernel_storage.ram_workspace(settings, run, note, checkpoint_run, tree_name=tree_name) as ram:
            set_build_dir(ram)
            THINLTO_CACHE_DIR, CCACHE_DIR = ram / "thinlto-cache", ram / "ccache"
            RAM_RESERVE_GIB = settings["ram_reserve_gib"]
            yield
    except (kernel_storage.StorageError, OSError) as exc:
        raise BuildError(str(exc)) from exc
    finally:
        set_build_dir(persistent)
        THINLTO_CACHE_DIR, CCACHE_DIR = old_lto, old_ccache
        RAM_RESERVE_GIB = 0


def choose_ram_build(args: argparse.Namespace, lto: str = "thin", tree_name: str = "") -> bool:
    """Prefer RAM automatically when the mount and measured capacity permit it."""
    ready, detail = kernel_storage.ram_capacity(STORAGE, lto, tree_name)
    note(detail)
    return ready


def do_build(args: argparse.Namespace) -> int:
    banner()
    facts = host_facts()
    profiles = ensure_profiles_exist()
    profile = select_profile(profiles, args.profile, facts).clone()
    target_facts = target_facts_for_profile(profile, facts)
    diff = configure_profile_interactively(profile, target_facts, args)
    target_facts = target_facts_for_profile(profile, facts)
    if profile.g("meta", "manifest_path"):
        args.no_install = True

    set_build_dir(args.build_dir or STORAGE.get("persistent_dir", BUILD_DIR))
    if kernel_storage.ram_mount(BUILD_DIR):
        raise ProfileError("Persistent build directory is RAM-backed; configure storage.zram_dir for automatic RAM selection instead")

    while True:
        show_configuration(profile, diff)
        if getattr(args, "no_prompt", False) or ASSUME_YES or not interactive():
            break
        action = ask("Proceed with this configuration? [Y]es / [e]dit / [n]o", "y").strip().lower()
        if action in ("y", "yes"):
            break
        if action in ("n", "no", "q", "quit"):
            info("Aborted by user")
            return 0
        if action in ("e", "edit", "m", "menu"):
            diff.extend(run_wizard(profile, target_facts))
            diff = wizard_review_loop(profile, target_facts, diff, force=bool(getattr(args, "force", False)))
            if diff:
                offer_save_profile(profile)
            continue
        warn(f"Unrecognized response '{action}'. Choose [y]es to proceed, [e]dit to modify, or [n]o to abort.")

    check_dependencies(profile, facts, profile.g("compiler", "toolchain"), bool(profile.g("compiler", "rust")))
    with build_workspace_lock(), ExitStack() as workspace:
        if not args.no_install and not args.configure_only:
            PRIV.ensure()
            check_pacman_preflight(require_install=True)
        JOURNAL.open(profile.name)
        note(f"journal: {JOURNAL.path}")
        validate_cpu_flags(profile)
        host_facts.cache_clear()
        facts = host_facts()
        target_facts = target_facts_for_profile(profile, facts)
        rule("Kernel release")
        release = choose_release(profile, fetch_releases(), exact_pin=bool(args.pin or os.environ.get("DUSKY_PIN")))
        # Record the one-time choice in the resolved profile packaged with this build.
        profile.set("release", "pin", release.version)
        if release.moniker in CHANNEL_CHOICES:
            profile.set("release", "channel", release.moniker)
        if release.is_rc:
            profile.set("release", "allow_rc", True)
        tarball = obtain_tarball(release, bool(profile.g("release", "require_signature")))
        # Stock sources can reuse Make's configuration/command dependencies
        # across orchestration updates. Hardware/toolchain facts isolate native
        # objects; runtime payloads and build-file additions are restaged below.
        identity = json.dumps({"profile": profile.sections, "target": target_facts.as_json()}, sort_keys=True)
        build_id = profile.name + "-" + hashlib.sha256(identity.encode()).hexdigest()[:16]
        tree_name = tree_dir_for(release, build_id).name
        prune_obsolete_build_trees(profile.name, tree_name)
        use_ram = choose_ram_build(args, profile.g("compiler", "lto"), tree_name)
        workspace.enter_context(storage_session(use_ram, tree_name))
        check_disk_space(profile.g("compiler", "lto"), installing=not args.no_install and not args.configure_only)
        tree = unpack(tarball, release, build_id, bool(args.fresh))
        sched = "eevdf"
        env0 = toolchain_env(profile)
        seed_source = seed_config(tree, profile, env0, Path(args.seed_config).expanduser() if args.seed_config else None)
        run(["make", "olddefconfig"], cwd=tree, env=env0)
        db = ensure_modprobed_db(profile)
        census_symbols = localmodconfig(tree, profile, db, env0)
        idx = KconfigIndex.scan(tree)
        note(f"Kconfig index: {len(idx.symbols):,} symbols (x86 view), X86_64_VERSION range max {idx.x86_64_version_max}")
        target_facts = target_facts_for_profile(profile, facts)
        if target_facts is not facts:
            note(f"Target hardware profile: {target_facts.model} ({target_facts.vendor}), {target_facts.threads} threads, uarch={target_facts.uarch or 'generic_v' + str(target_facts.psabi_level)}, gpus={', '.join(target_facts.gpus) or 'none'}, virt={target_facts.virt}")
        rust_ok, rust_out = (rust_probe(tree, env0) if profile.g("compiler", "rust") else (False, ""))
        d = derive(profile, target_facts, idx, tree, sched, rust_ok, rust_out)
        d.census_symbols = census_symbols
        validate_rust_cpu(d)
        d.seed_source = seed_source
        mx = build_config_matrix(profile, d)
        apply_matrix(tree, mx)
        env = build_env(profile, d, facts, int((tree / ".dusky" / "source-epoch").read_text()))
        (tree / "include" / "config" / "kernel.release").unlink(missing_ok=True)
        prepare_extmod_build(tree, d, env)
        finalize_config(tree, env)
        d.kernelrelease = kernelrelease(tree, env)
        info(f"kernelrelease: {d.kernelrelease}")
        verify_config(tree, profile, mx, d)
        save_config_snapshot(tree, profile)
        (tree / ".dusky" / "build.json").write_text(json.dumps({"profile": profile.name, "kernelrelease": d.kernelrelease}))
        if args.print_matrix:
            rule("Kconfig matrix")
            for op in mx.ops:
                say(f"  {op.render():<56} {C.DIM}{op.why}{C.RESET}")
        if args.configure_only:
            ok(f"Configuration complete (--configure-only). Tree: {tree}")
            return 0
        link_thinlto_cache(tree, profile, d)
        compile_wall_start = time.monotonic()
        pkgs = compile_kernel(tree, profile, d, env, facts)
        d.kernelrelease = kernelrelease(tree, env)
        if args.no_install:
            rule("Done")
            ok(f"Packages built in {fmt_duration(d.compile_duration)} (--no-install). Install on the target with: python dusky_kernal_compile.py --install-pkg " + " ".join(shlex.quote(str(x)) for x in pkgs))
            send_notification("Kernel build complete", f"{d.kernelrelease} ({profile.name}) compiled in {fmt_duration(d.compile_duration)}", icon="dialog-information")
            return 0
        install_packages(pkgs, profile, d.kernelrelease)
        refresh_boot(profile, facts, d, kernel_install=bool(args.kernel_install))
        total_wall = time.monotonic() - compile_wall_start
        rule("Done")
        ok(f"{d.kernelrelease} ({profile.name}) installed as {profile.pkgbase}.")
        say(f"  {C.CYAN}⏱ Build and packaging:{C.RESET} {fmt_duration(d.compile_duration)}" + (f" ({d.compile_steps:,} steps)" if d.compile_steps else ""))
        say(f"  {C.CYAN}⏱ Build through boot setup:{C.RESET} {fmt_duration(total_wall)}")
        say(f"  {C.DIM}Reboot to test; roll back with --uninstall {profile.suffix}.{C.RESET}")
        send_notification("Kernel build complete", f"{d.kernelrelease} ({profile.name}) compiled in {fmt_duration(d.compile_duration)} (total: {fmt_duration(total_wall)})", icon="dialog-information")
        return 0


def do_list(_: argparse.Namespace) -> int:
    print_profile_table(ensure_profiles_exist(), host_facts())
    return 0


def do_show(args: argparse.Namespace) -> int:
    p = select_profile(ensure_profiles_exist(), args.profile, host_facts()).clone()
    diff = apply_overrides(p, Overrides.from_env_and_args(args)) + normalize_profile(p)
    validate_profile(p)
    cross_validate(p, target_facts_for_profile(p, host_facts()), force=bool(args.force))
    if args.json:
        say(json.dumps({"profile": p.name, "sections": p.sections, "diff": diff}, indent=2))
        return 0
    if args.dump_toml:
        sys.stdout.write(render_profile_toml(p.sections, header=f"resolved view of {p.name}"))
        return 0
    show_configuration(p, diff)
    return 0


def do_spec(_: argparse.Namespace) -> int:
    for sec, fields in PROFILE_SPEC.items():
        say(f"{C.BOLD}[{sec}]{C.RESET}")
        for f in fields:
            choices = f" choices={'|'.join(map(str, f.choices))}" if f.choices else ""
            rng = f" range={f.minimum}..{f.maximum}" if f.kind == "int" and (f.minimum is not None or f.maximum is not None) else ""
            say(f"  {f.key:<26} {f.kind:<5} default={toml_scalar(f.default):<28} {f.help}{choices}{rng}")
        say("")
    return 0


def latest_tree(profile_name: str | None = None) -> Path | None:
    if not SRC_DIR.is_dir():
        return None
    trees = [d for d in SRC_DIR.iterdir() if d.is_dir() and is_valid_kernel_tree(d)]
    if profile_name is not None:
        selected = []
        for tree in trees:
            try:
                metadata = json.loads((tree / ".dusky" / "build.json").read_text())
            except (OSError, ValueError):
                continue
            if metadata.get("profile") == profile_name:
                selected.append(tree)
        trees = selected
    return max(trees, key=lambda d: (d / ".config").stat().st_mtime if (d / ".config").exists() else d.stat().st_mtime) if trees else None


def do_matrix(args: argparse.Namespace) -> int:
    banner()
    facts = host_facts()
    p = select_profile(ensure_profiles_exist(), args.profile, facts).clone()
    diff = apply_overrides(p, Overrides.from_env_and_args(args)) + normalize_profile(p)
    validate_profile(p)
    tree = latest_tree(p.name)
    if tree is not None:
        idx = KconfigIndex.scan(tree)
        note(f"Using Kconfig index of {tree.name} ({len(idx.symbols):,} symbols)")
    else:
        idx = KconfigIndex(frozenset(), 3)
        note("No extracted source tree yet; showing the permissive matrix (every symbol assumed available)")
    facts = target_facts_for_profile(p, facts)
    cross_validate(p, facts)
    d = derive(p, facts, idx, tree or Path("."), p.g("scheduler", "type"), True, "")
    mx = build_config_matrix(p, d)
    show_configuration(p, diff)
    rule(f"Kconfig matrix ({len(mx)} ops)")
    for op in mx.ops:
        flag = " (soft)" if op.optional else ""
        say(f"  {op.render():<56} {C.DIM}{op.why}{flag}{C.RESET}")
    if mx.skipped:
        note(f"skipped (absent in tree): {', '.join(sorted({o.symbol for o in mx.skipped}))}")
    rule("Flavor command line")
    say("  " + " ".join(flavor_cmdline(p, facts)))
    return 0


def do_doctor(args: argparse.Namespace) -> int:
    facts = host_facts()
    nvidia = kernel_nvidia.power_status() if "nvidia" in facts.gpus else []
    if args.json:
        say(json.dumps({"facts": facts.as_json(), "nvidia": nvidia, "python": sys.version.split()[0], "paths": {"build": str(BUILD_DIR), "profiles": [str(d) for d in profile_dirs()],
                        "snapshots": str(CONFIG_SNAPSHOT_DIR), "logs": str(LOG_DIR)}}, indent=2))
        return 0
    banner()
    rule("Host")
    table(["item", "value"], [
        ["python", sys.version.split()[0]], ["running kernel", facts.kernel], ["cpu", f"{facts.model} ({facts.vendor}, {facts.cores}c/{facts.threads}t)"],
        ["uarch / psABI", f"{facts.uarch or 'unknown'} / x86-64-v{facts.psabi_level}"], ["LLC", f"{facts.llc_domains} domain(s), {facts.llc_kib // 1024} MiB"],
        ["memory", f"{facts.mem_gib:.1f} GiB RAM, {facts.swap_gib:.1f} GiB swap ({'disk swap present' if facts.disk_swap else 'no disk swap'})"],
        ["suggested footprint", suggest_footprint(facts.mem_gib)], ["virtualization", facts.virt], ["gpus", ", ".join(facts.gpus) or "none"],
        ["root fs", f"{facts.root_fs}{' on dm-crypt' if facts.root_luks else ''}; all: {', '.join(facts.filesystems)}"],
        ["storage", f"nvme={'yes' if facts.has_nvme else 'no'} rotational={'yes' if facts.rotational else 'no'}"], ["battery", "yes" if facts.battery else "no"],
        ["bootloaders", ", ".join(facts.bootloaders) or "none detected"], ["ESP / XBOOTLDR", f"{facts.esp or '-'} / {facts.xbootldr or '-'}"],
        ["mkinitcpio", f"compression={facts.initrd_compression} microcode_hook={'yes' if facts.microcode_hook else 'no'}"],
        ["DKMS modules", ", ".join(facts.dkms_modules) or "none"], ["sched_ext live", "yes" if facts.sched_ext_live else "no"],
        ["CAS knob", "present" if Path("/sys/kernel/debug/sched/llc_balancing/aggr_tolerance").exists() else "absent/debugfs not mounted"],
        ["amd_pstate", _read("/sys/devices/system/cpu/amd_pstate/status").strip() or "n/a"], ["THP", _read("/sys/kernel/mm/transparent_hugepage/enabled").strip() or "n/a"],
        ["zram", ", ".join(p.name for p in Path("/sys/block").glob("zram*")) or "none"],
    ])
    if nvidia:
        rule("NVIDIA runtime power (passive snapshot)")
        for gpu in nvidia:
            say(f"  {gpu['pci']}: {gpu['driver']} / {gpu['power_state']} / {gpu['runtime_status']} / control={gpu['control']}")
            say(textwrap.indent(gpu['nvidia_power'], "    "))
        note("D0 can be expected with active GPU clients or displays; a snapshot alone does not prove a PM fault")
    rule("Toolchain & Systems Integration")
    rows = []
    # (tool, category, is_required)
    tool_defs = [
        ("clang", "compiler (LLVM)", True),
        ("ld.lld", "linker (LLD)", True),
        ("llvm-ar", "archiver (LLVM)", True),
        ("rustc", "Rust-for-Linux", True),
        ("bindgen", "Rust-for-Linux", True),
        ("pahole", "BTF generation", True),
        ("make", "build automation", True),
        ("ccache", "compiler cache (optional)", False),
        ("makepkg", "Arch packaging", True),
        ("mkinitcpio", "initramfs generator", True),
        ("modprobed-db", "hardware module profiler", True),
        ("zram-generator", "ZRAM swap generator (optional)", False),
        ("perf", "kernel telemetry / AutoFDO", False),
        ("scx_lavd", "sched_ext gaming/latency", False),
        ("scx_bpfland", "sched_ext low-latency", False),
        ("dkms", "out-of-tree dynamic modules", False),
        ("bootctl", "systemd-boot management", "systemd-boot" in facts.bootloaders),
        ("kernel-install", "kernel install framework", False),
        ("grub-mkconfig", "GRUB bootloader", "grub" in facts.bootloaders),
        ("limine-update", "Limine bootloader", "limine" in facts.bootloaders),
        ("create_llvm_prof", "Google AutoFDO (optional)", False),
        ("aria2c", "multi-stream downloader (optional)", False),
    ]
    for name, cat, required in tool_defs:
        v = facts.tools.get(name, "")
        if v:
            status = f"{C.GREEN}{v}{C.RESET}"
        elif required:
            status = f"{C.RED}missing (required){C.RESET}"
        else:
            status = f"{C.DIM}not installed (optional){C.RESET}"
        rows.append([name, cat, status])
    table(["tool", "subsystem", "status"], rows)
    rule("Paths")
    free = shutil.disk_usage(BUILD_DIR if BUILD_DIR.exists() else Path.home()).free
    _db_resolved = resolve_modprobed_db()
    if _db_resolved is not None:
        _db_status = f"{_db_resolved} ({count_db_modules(_db_resolved)} modules)"
    else:
        _db_status = f"missing (searched: {', '.join(str(c) for c in modprobed_db_candidates())})"
    table(["path", "value"], [["profiles", ", ".join(str(d) for d in profile_dirs())], ["build dir", f"{BUILD_DIR} ({fmt_bytes(free)} free)"],
                              ["snapshots", str(CONFIG_SNAPSHOT_DIR)], ["tarballs", str(TARBALL_DIR)], ["ThinLTO cache", str(THINLTO_CACHE_DIR)], ["packages", str(PKGDEST_DIR)], ["logs", str(LOG_DIR)],
                              ["modprobed.db", _db_status]])
    hist = load_history()
    if hist:
        rule("Recent builds")
        table(["when", "profile", "version", "lto", "duration", "result"],
              [[h.get("ts", "")[:16], h.get("profile"), h.get("version"), h.get("lto"), fmt_duration(float(h.get("duration", 0))), "ok" if h.get("success") else "failed"] for h in hist[-8:]])
    return 0


def do_clean(args: argparse.Namespace) -> int:
    banner()
    rule("Clean artifacts")
    what = args.clean or "all"
    targets = {"src": SRC_DIR, "tarballs": TARBALL_DIR, "packages": PKGDEST_DIR, "thinlto": THINLTO_CACHE_DIR, "logs": LOG_DIR, "seeds": BUILD_DIR / "seeds"}
    chosen = list(targets) if what == "all" else [w.strip() for w in what.split(",")]
    with build_workspace_lock():
        for name in chosen:
            path = targets.get(name)
            if path is None:
                warn(f"Unknown clean target '{name}' (choose from: all, {', '.join(targets)})")
                continue
            if path.exists():
                size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                shutil.rmtree(path)
                ok(f"Removed {path} ({fmt_bytes(size)})")
            else:
                note(f"{path} already clean")
        return 0


def write_default_profiles(dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "custom.toml"
    if dest.exists():
        raise ProfileError(f"{dest} already exists; choose a new name in the profile manager")
    prof = profile_from_tweaks("custom", "Custom kernel", "dusky-custom", {})
    dest.write_text(render_profile_toml(prof.sections), encoding="utf-8")
    ok(f"Created {dest}; edit it before building")


def do_write_defaults(_: argparse.Namespace) -> int:
    write_default_profiles(PROFILES_DIR)
    return 0


def do_uninstall(args: argparse.Namespace) -> int:
    banner()
    flavor = args.uninstall.removeprefix("linux-")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", flavor):
        raise ProfileError("Invalid kernel flavor suffix")
    rule(f"Uninstall linux-{flavor}")
    installed = set((run(["pacman", "-Qq"], check=False).stdout or "").split())
    pkgs = [pkg for pkg in (f"linux-{flavor}-headers", f"linux-{flavor}") if pkg in installed]
    if pkgs:
        PRIV.run(["pacman", "-Rns", "--noconfirm", *pkgs], capture=False)
        ok(f"Removed packages: {', '.join(pkgs)}")
    else:
        warn(f"No installed packages named linux-{flavor}*")
    facts = host_facts()
    root = facts.xbootldr or facts.esp
    if root:
        entries = [Path(root, "loader", "entries", f"linux-{flavor}{suffix}.conf") for suffix in ("", "-fallback")]
        entries = [entry for entry in entries if entry.is_file()]
        if entries:
            PRIV.run(["rm", "-f", *[str(e) for e in entries]], check=False)
            ok(f"Removed boot entries: {', '.join(e.name for e in entries)}")
        loader_conf = Path(root) / "loader" / "loader.conf"
        if loader_conf.is_file():
            txt = loader_conf.read_text(encoding="utf-8")
            m = re.search(r"^default\s+(.+)$", txt, re.M)
            if m and f"linux-{flavor}" in m.group(1):
                remaining = [e.name for e in sorted((Path(root) / "loader" / "entries").glob("*.conf")) if f"linux-{flavor}" not in e.name]
                new_default = remaining[0] if remaining else "@saved"
                new_txt = re.sub(r"^default\s+.*$", f"default {new_default}", txt, flags=re.M)
                PRIV.write_files({loader_conf: (new_txt, "0644")})
                ok(f"Reset {loader_conf} default -> {new_default}")
                if have("bootctl"):
                    PRIV.run(["bootctl", "set-default", new_default], check=False)
    preset = Path(f"/etc/mkinitcpio.d/linux-{flavor}.preset")
    if preset.is_file():
        PRIV.run(["rm", "-f", str(preset)], check=False)
        ok(f"Cleaned up {preset}")
    if "grub" in facts.bootloaders and have("grub-mkconfig"):
        PRIV.run(["grub-mkconfig", "-o", "/boot/grub/grub.cfg"], capture=False)
    return 0


def _pkg_file_pkgbase(pkg: Path) -> str:
    """pkgbase (linux-<flavor>) for a pacman package file, via .PKGINFO or filename."""
    try:
        with tarfile.open(pkg, "r:*") as tf:
            try:
                member = tf.extractfile(".PKGINFO")
            except KeyError:
                member = None
            if member is not None:
                with member:
                    for raw in member.read().decode("utf-8", "replace").splitlines():
                        if raw.startswith("pkgname = "):
                            return raw.split("=", 1)[1].strip().removesuffix("-headers")
    except (tarfile.TarError, OSError, EOFError):
        pass
    stem = pkg.name
    for suf in (".pkg.tar.zst", ".pkg.tar.xz", ".pkg.tar.gz", ".pkg.tar.bz2", ".pkg.tar.lzo", ".pkg.tar"):
        if stem.endswith(suf):
            stem = stem[: -len(suf)]
            break
    parts = stem.rsplit("-", 3)
    if len(parts) == 4:
        return parts[0].removesuffix("-headers")
    raise ProfileError(f"Cannot determine pkgbase for package file: {pkg}")


def _resolve_install_profile(pkgbase: str, wanted: str | None, facts: HostFacts) -> KernelProfile:
    """Profile driving boot entries for a saved package: --profile, else the
    known profile with the same pkgbase, else defaults (base cmdline only)."""
    profiles = discover_profiles()
    if wanted:
        selected = select_profile(profiles, wanted, facts).clone()
        if selected.pkgbase != pkgbase:
            raise ProfileError(f"Profile {wanted} belongs to {selected.pkgbase}, not {pkgbase}")
        return selected
    for p in profiles:
        if p.pkgbase == pkgbase:
            return p.clone()
    suffix = pkgbase.removeprefix("linux-")
    info(f"No profile with pkgbase {pkgbase}; using defaults for boot entries (base cmdline only)")
    return profile_from_tweaks(f"install-{suffix}", f"Reinstall {pkgbase} from saved packages", suffix,
                               {"boot": {"cmdline": "print"}}, 90)


def packaged_profile(pkgs: Sequence[Path], pkgbase: str) -> KernelProfile | None:
    member_name = f"usr/lib/dusky-kernel/{pkgbase}/profile.json"
    for pkg in pkgs:
        try:
            with tarfile.open(pkg, "r:*") as tf:
                member = next((m for m in tf if m.name.removeprefix("./") == member_name), None)
                if member is None:
                    continue
                with tf.extractfile(member) as stream:
                    raw = json.load(stream)["profile"]
                sections, explicit = coerce(raw, pkg)
                result = KernelProfile(pkg, sections, explicit)
                validate_profile(result)
                if result.pkgbase != pkgbase:
                    raise ProfileError("Packaged profile does not match the kernel package")
                return result
        except (tarfile.TarError, OSError, ValueError, KeyError, TypeError) as e:
            raise ProfileError(f"Cannot read kernel metadata in {pkg}: {e}") from e
    return None


def packaged_kernelrelease(pkgs: Sequence[Path], pkgbase: str) -> str:
    """Read the exact kernel release from the package, not old installed module trees."""
    for pkg in pkgs:
        try:
            with tarfile.open(pkg, "r:*") as archive:
                for member in archive:
                    match = re.fullmatch(r"usr/lib/modules/([^/]+)/pkgbase", member.name.removeprefix("./"))
                    if not match or not member.isfile():
                        continue
                    with archive.extractfile(member) as stream:
                        if stream.read().decode("utf-8", "replace").strip() == pkgbase:
                            return match.group(1)
        except (tarfile.TarError, OSError, EOFError) as e:
            raise ProfileError(f"Cannot read kernel release from {pkg}: {e}") from e
    raise ProfileError(f"Kernel package for {pkgbase} has no usr/lib/modules/<release>/pkgbase")


def do_install_pkg(args: argparse.Namespace) -> int:
    banner()
    rule("Install saved kernel packages")
    PRIV.ensure()
    check_pacman_preflight(require_install=True)
    check_disk_space("none", installing=True, building=False)
    facts = host_facts()
    files: list[Path] = []
    for a in args.install_pkg:
        p = Path(a).expanduser()
        if not p.is_file():
            raise ProfileError(f"Package file not found: {p}")
        if ".pkg.tar" not in p.name:
            raise ProfileError(f"Not a pacman package file: {p}")
        files.append(p)
    groups: dict[str, list[Path]] = {}
    for f in files:
        groups.setdefault(_pkg_file_pkgbase(f), []).append(f)
    JOURNAL.open("install-pkg")
    note(f"journal: {JOURNAL.path}")
    for pkgbase, pkgs in sorted(groups.items()):
        wanted = getattr(args, "profile", None)
        profile = (packaged_profile(pkgs, pkgbase) if wanted is None else None) or _resolve_install_profile(pkgbase, wanted, facts)
        krel = packaged_kernelrelease(pkgs, pkgbase)
        info(f"{pkgbase}: using profile '{profile.name}' for preset and boot entries")
        install_packages(sorted(pkgs), profile, krel)
        d = Derived(facts=facts, idx=KconfigIndex(frozenset(), 3), tree=Path("."), version=krel, sched="eevdf",
                    toolchain="llvm", lto="none", btf=False, tracing="minimal", rust=False, rust_reason="",
                    fdo="none", fdo_reason="", kernelrelease=krel)
        refresh_boot(profile, facts, d, kernel_install=bool(getattr(args, "kernel_install", False)))
    rule("Done")
    ok("Saved packages installed; reboot to test.")
    send_notification("Kernel packages installed", ", ".join(sorted(groups)), icon="dialog-information")
    return 0


def do_fdo_record(args: argparse.Namespace) -> int:
    banner()
    rule("AutoFDO / Propeller profile recording")
    if args.fdo_record <= 0:
        raise ProfileError("FDO recording duration must be positive")
    facts = host_facts()
    p = select_profile(ensure_profiles_exist(), args.profile, facts)
    if not have("perf") or not (have("llvm-profgen") or have("create_llvm_prof")):
        raise DependencyError("perf and llvm-profgen (Arch llvm package) or create_llvm_prof are required")
    if args.fdo_propeller and not have("create_llvm_prof"):
        raise DependencyError("Propeller conversion requires create_llvm_prof from google/autofdo")
    tree = latest_tree(p.name)
    vmlinux = tree / "vmlinux" if tree else None
    if vmlinux is None or not vmlinux.is_file():
        raise BuildError("No vmlinux found; build the profile once with compiler.fdo=autofdo (profile-less first pass) before recording")
    metadata = json.loads((tree / ".dusky" / "build.json").read_text())
    if metadata["kernelrelease"] != os.uname().release:
        raise ProfileError("FDO recording requires booting the matching built kernel first")
    outdir = Path(p.g("compiler", "fdo_profile_dir")).expanduser() if p.g("compiler", "fdo_profile_dir") else STATE_DIR / "fdo" / p.name
    outdir.mkdir(parents=True, exist_ok=True)
    seconds = args.fdo_record
    perf_data = outdir / "perf.data"
    if facts.vendor == "intel":
        event = ["-e", "BR_INST_RETIRED.NEAR_TAKEN:k"]
    elif facts.vendor == "amd" and facts.flags & {"brs", "amd_lbr_v2"}:
        event = ["--pfm-events", "RETIRED_TAKEN_BRANCH_INSTRUCTIONS:k"]
    else:
        raise ProfileError("FDO recording requires Intel branch sampling or AMD BRS/amd_lbr_v2 support")
    info(f"Recording {seconds}s of system-wide kernel branch samples; run your representative workload now")
    PRIV.run(["perf", "record", *event, "-a", "-N", "-b", "-c", "500009", "-o", str(perf_data), "--", "sleep", str(seconds)], capture=False)
    PRIV.run(["chown", f"{os.getuid()}:{os.getgid()}", str(perf_data)], check=False)
    if have("llvm-profgen"):
        run(["llvm-profgen", "--kernel", f"--binary={vmlinux}", f"--perfdata={perf_data}", "-o", str(outdir / "kernel.afdo")], capture=False)
    else:
        run(["create_llvm_prof", f"--binary={vmlinux}", f"--profile={perf_data}", "--format=extbinary", f"--out={outdir / 'kernel.afdo'}"], capture=False)
    ok(f"AutoFDO profile written: {outdir / 'kernel.afdo'}")
    if args.fdo_propeller:
        run(["create_llvm_prof", f"--binary={vmlinux}", f"--profile={perf_data}", "--format=propeller", "--propeller_output_module_name",
             f"--out={outdir / 'propeller_cc_profile.txt'}", f"--propeller_symorder={outdir / 'propeller_ld_profile.txt'}"], capture=False)
        ok(f"Propeller profiles written to {outdir}")
    info(f"Set compiler.fdo=autofdo{'_propeller' if args.fdo_propeller else ''} and compiler.fdo_profile_dir={outdir} in the profile, then rebuild")
    return 0


# ---------------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------------
EPILOG: Final = textwrap.dedent(f"""\
    examples:
      %(prog)s --write-default-profiles        write the profile template
      %(prog)s --doctor                        host telemetry, toolchain and bootloader diagnostics
      %(prog)s -p battery                      review the profile, then build and install
      %(prog)s -p battery --wizard --no-install walk every knob, build packages only
      %(prog)s -p battery --configure-only --print-matrix
      %(prog)s --export-bundle / --import-bundle FILE   cross-machine hardware bundles
      %(prog)s --uninstall dusky-gaming        remove packages and boot entries
      %(prog)s --install-pkg PKG...          install saved packages + preset/initramfs/DKMS/boot entries
    environment: DUSKY_PROFILES_DIR DUSKY_BUILD_DIR DUSKY_THINLTO_CACHE DUSKY_PKGDEST DUSKY_CPU_ARCH DUSKY_LTO DUSKY_JOBS ...
    exit codes: 1 generic, 2 profile, 3 network, 4 verification, 5 build, 6 dependency, 130 aborted
    """)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="dusky_kernal_compile.py", description=f"{APP_NAME} v{APP_VERSION} -- {APP_TAGLINE}", epilog=EPILOG,
                                 formatter_class=argparse.RawDescriptionHelpFormatter, suggest_on_error=True, color=True)
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    ap.add_argument("-p", "--profile", metavar="NAME", help="profile to build (interactive picker when omitted)")
    mode = ap.add_argument_group("modes")
    mode.add_argument("-l", "--list-profiles", action="store_true", help="list profiles")
    mode.add_argument("--show", action="store_true", help="show the resolved profile")
    mode.add_argument("--dump-toml", action="store_true", help="with --show: print the resolved profile as TOML")
    mode.add_argument("--spec", action="store_true", help="print the profile schema")
    mode.add_argument("--print-matrix", action="store_true", help="print the Kconfig matrix (dry-run without a build, or after configuration during a build)")
    mode.add_argument("--doctor", action="store_true", help="system diagnostics")
    mode.add_argument("--clean", metavar="WHAT", nargs="?", const="all", help="clean all|src|tarballs|packages|thinlto|logs|seeds (comma separated)")
    mode.add_argument("--write-default-profiles", action="store_true", help="write the profile template")
    mode.add_argument("--export-bundle", nargs="?", const="", default=None, metavar="FILE", help="export a hardware bundle for remote builds")
    mode.add_argument("--import-bundle", type=Path, metavar="FILE", help="import a hardware bundle and register remote_<host>")
    mode.add_argument("--uninstall", metavar="FLAVOR", help="remove linux-<flavor>{,-headers} and boot entries")
    mode.add_argument("--install-pkg", nargs="+", metavar="PKG", help="install saved kernel packages by path (pacman -U), then refresh preset/initramfs, DKMS audit and bootloader entries")
    mode.add_argument("--fdo-record", type=int, metavar="SECONDS", help="record an AutoFDO profile for --profile (needs perf + llvm-profgen or create_llvm_prof)")
    mode.add_argument("--fdo-propeller", action="store_true", help="with --fdo-record: also emit Propeller profiles")
    mode.add_argument("--menu", action="store_true", help="interactive main menu")
    ov = ap.add_argument_group("profile overrides")
    ov.add_argument("--cpu-arch", metavar="ARCH")
    ov.add_argument("--modules-mode", choices=list(MODULES_MODE_CHOICES))
    ov.add_argument("--toolchain", choices=list(TOOLCHAIN_CHOICES))
    ov.add_argument("--lto", choices=list(LTO_CHOICES))
    ov.add_argument("--channel", choices=list(CHANNEL_CHOICES), help="preferred channel for the interactive release picker")
    ov.add_argument("--scheduler", choices=list(SCHED_CHOICES))
    ov.add_argument("--scx", metavar="SCHEDULER")
    ov.add_argument("--headers", choices=list(HEADERS_CHOICES))
    ov.add_argument("--no-headers", action="store_const", dest="headers", const="never")
    ov.add_argument("--footprint", choices=list(FOOTPRINT_CHOICES))
    ov.add_argument("--pin", metavar="VERSION", help="exact kernel version; bypasses the interactive release picker")
    ov.add_argument("--allow-rc", action="store_true", help="allow release candidates for this run (use --channel mainline for the newest)")
    ov.add_argument("-j", "--jobs", type=int)
    ov.add_argument("--no-rust", action="store_true")
    bh = ap.add_argument_group("build behaviour")
    bh.add_argument("--settings", type=Path, help="machine storage TOML (default: kernel_profiles/settings/kernel_settings.toml)")
    bh.add_argument("--ram-build", action="store_true", help=argparse.SUPPRESS)
    bh.add_argument("--build-dir", type=Path, metavar="DIR", help="persistent build directory; RAM workspace is configured separately")
    bh.add_argument("--wizard", action="store_true", help="always enter the granular configuration wizard")
    bh.add_argument("--no-prompt", action="store_true", help="build without the configuration review prompt")
    bh.add_argument("--fresh", action="store_true", help="re-extract the source tree")
    bh.add_argument("--seed-config", metavar="FILE", help="seed .config from FILE")
    bh.add_argument("--configure-only", action="store_true", help="stop after configuration + verification")
    bh.add_argument("--no-install", action="store_true", help="build packages but do not install")
    bh.add_argument("--kernel-install", action="store_true", help="also register via kernel-install(8)")
    bh.add_argument("--force", action="store_true", help="override bare_metal_only guards")
    bh.add_argument("-y", "--yes", action="store_true", help="assume defaults for every question")
    bh.add_argument("-v", "--verbose", action="store_true")
    bh.add_argument("--json", action="store_true", help="machine-readable output for --doctor/--show")
    bh.add_argument("--no-color", action="store_true")
    return ap


# ---------------------------------------------------------------------------------------------------
# Interactive menu
# ---------------------------------------------------------------------------------------------------
def install_aur_package(pkg: str) -> bool:
    """Install an AUR package using paru, yay, or direct makepkg without sudo.

    Must stay interactive-capable: AUR helpers invoke sudo internally for the
    install phase and prompt for PKGBUILD review / confirmation, so stdin must
    stay attached and no new session may detach from the terminal (unlike the
    non-interactive run() defaults). check=False so failures return False and
    the caller can warn instead of crashing with BuildError.
    """
    # Non-interactive / -y runs cannot answer prompts: pass --noconfirm.
    auto = ASSUME_YES or not interactive()
    extra = ["--noconfirm"] if auto else []
    # Skip PKGBUILD review only for fully automatic runs; interactive users
    # keep the security review prompt.
    review = ["--skipreview"] if auto else []
    try:
        if have("paru"):
            return run(["paru", "-S", "--needed", *extra, *review, pkg],
                       capture=False, stdin_null=False, own_group=False, check=False).returncode == 0
        if have("yay"):
            return run(["yay", "-S", "--needed", *extra, pkg],
                       capture=False, stdin_null=False, own_group=False, check=False).returncode == 0
        info(f"No AUR helper detected; building {pkg} directly from AUR via makepkg...")
        with tempfile.TemporaryDirectory(prefix=f"aur-{pkg}-") as tmp:
            clone = run(["git", "clone", f"https://aur.archlinux.org/{pkg}.git", str(tmp)],
                        capture=False, stdin_null=False, own_group=False, check=False)
            if clone.returncode != 0:
                return False
            return run(["makepkg", "-si", "--noconfirm", "--needed"], cwd=Path(tmp),
                       capture=False, stdin_null=False, own_group=False, check=False).returncode == 0
    except DuskyError as e:
        debug(f"AUR install failed: {e}")
        return False


def initialize_toolchains() -> None:
    rule("Toolchains & hardware profiler")
    official_pkgs = ["base-devel", "clang", "lld", "llvm", "rust", "rust-bindgen", "bc", "cpio", "kmod", "pahole", "perf", "curl", "gnupg", "terminus-font"]
    if ask_yes(f"Install official packages (pacman -S --needed {' '.join(official_pkgs)}) ?", True):
        PRIV.run(["pacman", "-S", "--needed", *official_pkgs], capture=False)

    if not have("modprobed-db"):
        if ask_yes("modprobed-db is an AUR package (tracks loaded modules for localmodconfig); install from AUR now?", True):
            if install_aur_package("modprobed-db"):
                ok("modprobed-db installed successfully from AUR")
            else:
                warn("Could not install modprobed-db automatically; install manually with: paru -S modprobed-db")

    if have("modprobed-db"):
        ensure_modprobed_db_service(prompt=True)

        db = resolve_modprobed_db()
        if db:
            ok(f"modprobed-db active: {db} ({count_db_modules(db)} modules logged)")
        else:
            warn("modprobed.db not found yet; keep using the machine before strict builds")


def live_telemetry() -> None:
    rule("Live hardware telemetry")
    facts = host_facts()
    load = _read("/proc/loadavg").split()[:3]
    mem = _read("/proc/meminfo")

    def kib(key: str) -> int:
        m = re.search(rf"^{key}:\s+(\d+)", mem, re.M)
        return int(m.group(1)) if m else 0

    used = kib("MemTotal") - kib("MemAvailable")
    rows = [["load", " ".join(load)], ["memory used", f"{used / 1048576:.2f} GiB of {facts.mem_gib:.1f} GiB (available {kib('MemAvailable') / 1048576:.2f} GiB)"],
            ["kernel slab", f"{kib('Slab') / 1024:.0f} MiB (SReclaimable {kib('SReclaimable') / 1024:.0f} MiB)"], ["page tables", f"{kib('PageTables') / 1024:.0f} MiB"],
            ["swap", f"{(kib('SwapTotal') - kib('SwapFree')) / 1048576:.2f} GiB used of {kib('SwapTotal') / 1048576:.2f} GiB"],
            ["LLC topology", f"{facts.llc_domains} L3 domain(s) x {facts.llc_kib // 1024} MiB"], ["sched_ext", _read("/sys/kernel/sched_ext/root/ops").strip() or ("enabled, no scheduler loaded" if facts.sched_ext_live else "not available")],
            ["cpufreq", f"{_read('/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor').strip() or 'n/a'} / EPP {_read('/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference').strip() or 'n/a'}"],
            ["cpuidle", _read("/sys/devices/system/cpu/cpuidle/current_governor").strip() or "n/a"], ["THP", _read("/sys/kernel/mm/transparent_hugepage/enabled").strip() or "n/a"],
            ["MGLRU", _read("/sys/kernel/mm/lru_gen/enabled").strip() or "n/a"], ["preempt", _read("/sys/kernel/debug/sched/preempt").strip() or "n/a (debugfs)"]]
    for z in Path("/sys/block").glob("zram*"):
        rows.append([z.name, f"{_read(z / 'comp_algorithm').strip()} disksize {int(_read(z / 'disksize').strip() or 0) >> 20} MiB"])
    db_telemetry = resolve_modprobed_db()
    rows.append(["modprobed.db", f"{db_telemetry} ({count_db_modules(db_telemetry)} modules)" if db_telemetry else "missing (run modprobed-db store)"])
    table(["metric", "value"], rows)


def config_manager_menu() -> None:
    while True:
        rule("Configuration manager")
        say(" 1) List profiles\n 2) Show a profile\n 3) Run the wizard on a profile and save as new profile\n 4) Write profile template\n 5) Print schema\n 6) Back\n")
        choice = ask_index("Select", 6, 6)
        facts = host_facts()
        match choice:
            case 1:
                print_profile_table(ensure_profiles_exist(), facts)
            case 2:
                p = select_profile(ensure_profiles_exist(), None, facts).clone()
                show_configuration(p, normalize_profile(p))
            case 3:
                p = select_profile(ensure_profiles_exist(), None, facts).clone()
                diff = run_wizard(p, facts)
                diff = wizard_review_loop(p, facts, diff, force=False)
                show_configuration(p, diff)
                offer_save_profile(p)
            case 4:
                do_write_defaults(argparse.Namespace())
            case 5:
                do_spec(argparse.Namespace())
            case _:
                return
        pause()


def bundle_manager_menu() -> None:
    rule("Bundle manager")
    say(" 1) Export this machine's hardware bundle\n 2) Import a bundle\n 3) Back\n")
    match ask_index("Select", 3, 3):
        case 1:
            do_export_bundle(None)
        case 2:
            do_import_bundle(Path(ask("Bundle path", "")).expanduser())
        case _:
            return


def interactive_menu() -> int:
    while True:
        say("")
        banner()
        say(f"{C.ACCENT}  Build a kernel in three steps{C.RESET}")
        say(" 1) Prepare: install tools and collect this machine's modules\n"
            " 2) Build: select a profile, review settings, compile and install\n"
            " 3) Build elsewhere: export / import target hardware\n"
            " 4) Install: use saved packages on this machine\n"
            " 5) Advanced: profiles, diagnostics and maintenance\n"
            " 6) Exit\n")
        try:
            choice = ask_index("Select", 6, 2)
        except KeyboardInterrupt:
            return 0
        try:
            match choice:
                case 1:
                    initialize_toolchains()
                case 2:
                    args = build_parser().parse_args([])
                    do_build(args)
                case 3:
                    bundle_manager_menu()
                case 4:
                    paths = ask("Saved package files (space-separated)", "")
                    if paths:
                        do_install_pkg(argparse.Namespace(install_pkg=shlex.split(paths), profile=None, kernel_install=False))
                case 5:
                    say(" 1) Profiles\n 2) Diagnostics\n 3) Live telemetry\n 4) Uninstall a kernel\n 5) Clean caches\n 6) Back\n")
                    match ask_index("Select", 6, 6):
                        case 1:
                            config_manager_menu()
                        case 2:
                            do_doctor(argparse.Namespace(json=False))
                        case 3:
                            live_telemetry()
                        case 4:
                            flavor = ask("Flavor suffix to uninstall (e.g. dusky-performance)", "")
                            if flavor:
                                do_uninstall(argparse.Namespace(uninstall=flavor))
                        case 5:
                            do_clean(argparse.Namespace(clean=ask("What to clean (all|src|tarballs|packages|thinlto|logs|seeds)", "packages,logs")))
                        case _:
                            continue
                case _:
                    return 0
        except KeyboardInterrupt:
            _ABORT.clear()
            warn("Action cancelled")
        except DuskyError as e:
            _ABORT.clear()
            err(str(e))
        pause()


def main(argv: Sequence[str] | None = None) -> int:
    global _VERBOSE, ASSUME_YES, STORAGE, CCACHE_DIR
    args = build_parser().parse_args(argv)
    try:
        STORAGE = kernel_storage.load_settings(args.settings or SCRIPT_DIR / "kernel_profiles" / "settings" / "kernel_settings.toml", XDG_CACHE, args.build_dir)
        CCACHE_DIR = STORAGE["ccache_dir"]
        set_build_dir(STORAGE["persistent_dir"])
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"Storage settings: {exc}\n")
        return 2
    _VERBOSE, ASSUME_YES = bool(args.verbose), bool(args.yes)
    if args.no_color or not sys.stdout.isatty() or os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        C.disable()
    install_signal_handlers()
    try:
        if args.export_bundle is not None:
            do_export_bundle(Path(args.export_bundle).expanduser() if args.export_bundle else None, args.profile)
            return 0
        if args.import_bundle:
            do_import_bundle(args.import_bundle.expanduser(), args.profile)
            return 0
        if args.uninstall:
            return do_uninstall(args)
        if args.install_pkg:
            return do_install_pkg(args)
        if args.fdo_record is not None:
            return do_fdo_record(args)
        if args.spec:
            return do_spec(args)
        if args.write_default_profiles:
            return do_write_defaults(args)
        if args.doctor:
            return do_doctor(args)
        if args.clean is not None:
            return do_clean(args)
        if args.list_profiles:
            return do_list(args)
        if args.show:
            return do_show(args)
        if args.print_matrix and not args.configure_only:
            return do_matrix(args)
        wants_build = bool(args.profile or args.configure_only or args.no_install or args.wizard or args.yes)
        if args.menu or (not wants_build and interactive()):
            return interactive_menu()
        return do_build(args)
    except DuskyError as e:
        err(str(e))
        if not isinstance(e, AbortError):
            send_notification("Kernel build failed", str(e)[:200], urgency="critical", icon="dialog-error")
        return e.exit_code
    except KeyboardInterrupt:
        _reap_all()
        sys.stdout.write(C.SHOW + "\n")
        warn("Interrupted -- child process groups terminated")
        return 130
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 0
    finally:
        _reap_all()
        PRIV.stop()
        JOURNAL.close()


if __name__ == "__main__":
    raise SystemExit(main())
