#!/usr/bin/env python3
#d: Interactive PAM faillock lockout policy manager (deny/unlock_time with live verification)

"""
225_faillock_timeout.py — Modern PAM faillock policy manager for Arch Linux.

Manages /etc/security/faillock.conf with partial updates, named presets,
atomic writes and retained backups. Unmanaged options and comments survive
updates. --verify exercises the installed PAM module using private service
configurations and tally files; it does not authenticate through sudo or
modify the system policy or existing account tallies.
"""

import argparse
import difflib
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Final

lazy import ctypes
lazy import ctypes.util
lazy import grp

# ==============================================================================
# Rich UI (guarded import — degrade to plain output when unavailable)
# ==============================================================================

RICH_AVAILABLE = False
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, IntPrompt, Prompt
    from rich.table import Table
    from rich.text import Text

    RICH_AVAILABLE = True
    console = Console()
    error_console = Console(stderr=True)
except ImportError:
    Console = Panel = Confirm = IntPrompt = Prompt = Table = Text = None  # type: ignore[assignment]
    console = error_console = None  # type: ignore[assignment]


_MARKUP_RE = re.compile(r"\[/?[a-zA-Z_][a-zA-Z0-9_= ]*?\]")


def _out(msg: object) -> None:
    if console:
        console.print(msg)
    else:
        print(_MARKUP_RE.sub("", str(msg)))


def _err(msg: object) -> None:
    if error_console:
        error_console.print(msg)
    else:
        print(_MARKUP_RE.sub("", str(msg)), file=sys.stderr)


# ==============================================================================
# Constants & Domain Model
# ==============================================================================

CONFIG_PATH: Final = Path("/etc/security/faillock.conf")
BACKUP_DIR: Final = Path("/etc/security/faillock.conf.backups")
VERIFY_LOG_DIR: Final = Path("/var/log/faillock-verify")
BUILTIN_DEFAULTS: Final = frozendict({
    "deny": 3,
    "fail_interval": 900,
    "unlock_time": 600,
    "dir": "/var/run/faillock",
})
MAX_TIME_INTERVAL: Final = 604800  # Linux-PAM: seven days


class Preset(StrEnum):
    """Named policy profiles selectable interactively or via --preset."""

    DEFAULT = "default"  # reset managed options to built-in defaults
    LENIENT = "lenient"  # high threshold, very short lock (original 225 intent)
    BALANCED = "balanced"  # moderate threshold, short lock
    STRICT = "strict"  # stock thresholds, explicitly pinned
    NEVER = "never"  # unlock_time=0 — permanent lock until manual reset


PRESET_TABLE: Final = frozendict({key: frozendict(values) for key, values in {
    Preset.DEFAULT: {},
    Preset.LENIENT: {
        "deny": 6, "fail_interval": 900, "unlock_time": 90,
        "even_deny_root": False,
    },
    Preset.BALANCED: {
        "deny": 5, "fail_interval": 900, "unlock_time": 300,
        "even_deny_root": False,
    },
    Preset.STRICT: {
        "deny": 3, "fail_interval": 900, "unlock_time": 600,
        "even_deny_root": False,
    },
    Preset.NEVER: {
        "deny": 5, "fail_interval": 900, "unlock_time": 0,
        "root_unlock_time": 0, "even_deny_root": True,
    },
}.items()})


@dataclass(frozen=True, slots=True)
class FaillockPolicy:
    """Effective faillock policy. ``None`` values mean 'not set in file'
    (built-in defaults then apply)."""

    deny: int | None = None
    fail_interval: int | None = None
    unlock_time: int | None = None
    root_unlock_time: int | None = None
    even_deny_root: bool = False
    admin_group: str | None = None
    tally_dir: str | None = None

    # -- effective values (built-in defaults when unset) ------------------
    @property
    def eff_deny(self) -> int:
        return self.deny if self.deny is not None else int(BUILTIN_DEFAULTS["deny"])

    @property
    def eff_fail_interval(self) -> int:
        return self.fail_interval if self.fail_interval is not None else int(BUILTIN_DEFAULTS["fail_interval"])

    @property
    def eff_unlock_time(self) -> int:
        return self.unlock_time if self.unlock_time is not None else int(BUILTIN_DEFAULTS["unlock_time"])

    @property
    def eff_root_unlock_time(self) -> int:
        return self.root_unlock_time if self.root_unlock_time is not None else self.eff_unlock_time

    @property
    def eff_tally_dir(self) -> str:
        return self.tally_dir if self.tally_dir is not None else str(BUILTIN_DEFAULTS["dir"])

    # -- parsing / rendering ---------------------------------------------
    ACTIVE_KEYS: ClassVar[tuple[str, ...]] = (
        "deny", "fail_interval", "unlock_time", "root_unlock_time",
        "even_deny_root", "admin_group", "dir",
    )

    def __post_init__(self) -> None:
        for key in ("deny", "fail_interval", "unlock_time", "root_unlock_time"):
            value = getattr(self, key)
            limit = 65535 if key == "deny" else MAX_TIME_INTERVAL
            if value is not None and (type(value) is not int or not 0 <= value <= limit):
                raise ValueError(f"{key} must be between 0 and {limit}")
        for key in ("admin_group", "tally_dir"):
            value = getattr(self, key)
            if value is not None and (not value or "#" in value or any(c in value for c in "\r\n\0")):
                raise ValueError(f"invalid {key}: {value!r}")
        if self.tally_dir is not None and not Path(self.tally_dir).is_absolute():
            raise ValueError("tally directory must be an absolute path")

    @classmethod
    def from_content(cls, content: str) -> FaillockPolicy:
        kwargs = {}
        for raw in content.splitlines():
            key, value = config_option(raw)
            if key not in cls.ACTIVE_KEYS:
                continue
            if key == "even_deny_root":
                kwargs[key] = True
            elif key in ("dir", "admin_group"):
                kwargs["tally_dir" if key == "dir" else key] = value
            else:
                if value == "never" and key in ("unlock_time", "root_unlock_time"):
                    kwargs[key] = 0
                elif value is not None and re.fullmatch(r"[0-9]+", value):
                    kwargs[key] = int(value)
                else:
                    raise ValueError(f"invalid {key} in configuration: {value!r}")
        return cls(**kwargs)

    @classmethod
    def from_file(cls, path: Path | None = None) -> FaillockPolicy:
        return cls.from_content(read_config(CONFIG_PATH if path is None else path))

    def to_content(self, original: str = "") -> str:
        """Replace managed options; retain all other lines verbatim."""
        lines = []
        for line in original.splitlines(keepends=True):
            if config_option(line)[0] not in self.ACTIVE_KEYS:
                lines.append(line)
            elif "#" in line:
                lines.append("#" + line.partition("#")[2])
        content = "".join(lines)
        if content and not content.endswith("\n"):
            content += "\n"
        active = []
        for key in self.ACTIVE_KEYS:
            value = getattr(self, "tally_dir" if key == "dir" else key)
            if key == "even_deny_root":
                if value:
                    active.append(key)
            elif value is not None:
                active.append(f"{key} = {value}")
        return content + "".join(f"{line}\n" for line in active)


def config_option(raw: str) -> tuple[str, str | None]:
    """Linux-PAM accepts whitespace around an optional equals sign."""
    line = raw.split("#", 1)[0].strip()
    if not line:
        return "", None
    option = re.fullmatch(r"([^\s=]+)\s*(?:=\s*)?(.*)", line)
    if option is None:
        raise ValueError(f"invalid configuration line: {raw!r}")
    return option[1], option[2] or None


def read_config(path: Path = CONFIG_PATH) -> str:
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            return stream.read()
    except FileNotFoundError:
        return ""


# ==============================================================================
# Duration utilities (human-friendly input)
# ==============================================================================

_DURATION_RE = re.compile(r"^(\d+)\s*(s|sec|secs|seconds?|m|min|mins|minutes?|h|hrs?|hours?)?$", re.IGNORECASE)

def parse_duration(raw: str) -> int:
    """Parses '90', '90s', '5m', '1h' (bare numbers = seconds). 'never'/'0' -> 0."""
    if raw.strip().lower() in ("never", "none", "off"):
        return 0
    m = _DURATION_RE.fullmatch(raw.strip())
    if not m:
        raise ValueError(f"invalid duration '{raw}' (use e.g. 90s, 5m, 1h, never)")
    value, unit = int(m.group(1)), (m.group(2) or "s").lower()
    multiplier = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
                  "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
                  "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600}[unit]
    return value * multiplier


def format_duration(seconds: int) -> str:
    """Renders seconds as '1 hour', '10 minutes', '90 seconds', or 'never'."""
    if seconds == 0:
        return "never (manual reset only)"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} hour{'s' if seconds // 3600 != 1 else ''}"
    if seconds % 60 == 0:
        return f"{seconds // 60} minute{'s' if seconds // 60 != 1 else ''}"
    return f"{seconds} seconds"


# ==============================================================================
# Atomic file IO and backups
# ==============================================================================

def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_atomic(dest: Path, content: str) -> bool:
    """Preserve existing mode/ownership; fsync before and after replacement."""
    dest = dest.resolve()
    temp: Path | None = None
    replaced = False
    try:
        metadata = dest.stat() if dest.exists() else None
        fd, name = tempfile.mkstemp(prefix=f".{dest.name}.", dir=dest.parent)
        temp = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            if metadata is not None:
                os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode) if metadata else 0o644)
            os.fsync(stream.fileno())
        temp.replace(dest)
        replaced = True
        fsync_directory(dest.parent)
        return True
    except OSError as exc:
        detail = "file replaced, but durability check failed" if replaced else "write failed"
        _err(f"[bold red]{dest}: {detail}: {exc}[/bold red]")
        return False
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def backup_with_retention(path: Path, keep: int = 10) -> Path:
    """Create a unique backup; pruning failure does not invalidate the backup."""
    if keep < 1:
        raise ValueError("backup retention must be at least one")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix=f"{path.name}.{datetime.now():%Y%m%d-%H%M%S-%f}.", dir=BACKUP_DIR,
    )
    bak = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream, path.open("rb") as source:
            shutil.copyfileobj(source, stream)
            stream.flush()
            shutil.copystat(path, bak)
            os.fsync(stream.fileno())
        fsync_directory(BACKUP_DIR)
    except OSError:
        bak.unlink(missing_ok=True)
        raise
    try:
        # copystat retains the source mtime; names contain the backup timestamp.
        backups = sorted((entry for entry in BACKUP_DIR.glob(f"{path.name}.*")
                          if entry != bak), reverse=True)
        for old in backups[keep - 1:]:
            old.unlink()
    except OSError as exc:
        _err(f"[yellow]Backup retained; could not prune older backups: {exc}[/yellow]")
    return bak


# ==============================================================================
# Privilege and user context
# ==============================================================================

@dataclass(frozen=True, slots=True)
class UserContext:
    username: str
    uid: int
    is_root: bool


def resolve_user_context() -> UserContext:
    """Resolves the real non-root user (SUDO_UID/PKEXEC_UID/loginuid fallbacks)."""
    is_root = os.geteuid() == 0
    real_uid = os.getuid()
    if is_root:
        escalation_uid = os.environ.get("SUDO_UID") or os.environ.get("PKEXEC_UID")
        if escalation_uid and escalation_uid.isdigit():
            real_uid = int(escalation_uid)
        elif "DOAS_USER" in os.environ:
            try:
                real_uid = pwd.getpwnam(os.environ["DOAS_USER"]).pw_uid
            except KeyError:
                pass
        else:
            try:
                loginuid = int(Path("/proc/self/loginuid").read_text(encoding="utf-8").strip())
                if loginuid != 4294967295:
                    real_uid = loginuid
            except (FileNotFoundError, ValueError, OSError):
                pass
    try:
        pw = pwd.getpwuid(real_uid)
    except KeyError:
        _err(f"[bold red]✖ Fatal: UID {real_uid} does not map to a system user.[/bold red]")
        sys.exit(1)
    return UserContext(username=pw.pw_name, uid=pw.pw_uid, is_root=is_root)


def ensure_root() -> None:
    """Self-elevates via sudo (password prompt), preserving arguments.

    Non-interactive sessions are only elevated when a valid sudo token is
    already cached — a failed password conversation would otherwise feed
    pam_faillock's failure counter (self-inflicted lockouts).
    """
    if os.geteuid() == 0:
        return
    if not shutil.which("sudo"):
        _err("[bold red]✖ Root required and sudo is unavailable. Run as root.[/bold red]")
        sys.exit(1)
    command = ["sudo", "-p", "[sudo] password for %u: "]
    if not sys.stdin.isatty():
        command.append("-n")
    command += ["--", sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]
    try:
        os.execvp("sudo", command)
    except OSError as exc:
        _err(f"[bold red]Elevation failed: {exc}[/bold red]")
        sys.exit(1)


def run(cmd: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    """Return text output for success, command failure, timeout or spawn error."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout,
                              env=os.environ | {"LC_ALL": "C"})
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or b""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return subprocess.CompletedProcess(cmd, 124, output, "command timed out")
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


# ==============================================================================
# Faillock runtime inspection
# ==============================================================================

def faillock_state(user: str, policy: FaillockPolicy) -> tuple[int, bool, float | None]:
    """Estimate lock state from valid rows, matching PAM's latest-record window.

    PAM service arguments can override the file; this is not an authentication
    result. Command errors are reported instead of guessing from binary size.
    """
    proc = run(["faillock", "--dir", policy.eff_tally_dir, "--user", user], timeout=10)
    if proc.returncode:
        raise RuntimeError(f"Cannot read tally: {proc.stderr.strip() or proc.stdout.strip()}")
    timestamps = []
    for line in proc.stdout.splitlines():
        if re.prefixmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} ", line):
            if line.rsplit(maxsplit=1)[-1] == "V":
                timestamps.append(datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S").timestamp())
    latest = max(timestamps, default=None)
    count = (sum(latest - stamp < policy.eff_fail_interval for stamp in timestamps)
             if latest is not None else 0)
    pw = pwd.getpwnam(user)
    is_admin = pw.pw_uid == 0
    if policy.admin_group:
        try:
            admin_gid = grp.getgrnam(policy.admin_group).gr_gid
            is_admin = is_admin or admin_gid in os.getgrouplist(user, pw.pw_gid)
        except KeyError:
            pass
    duration = policy.eff_root_unlock_time if is_admin else policy.eff_unlock_time
    locked = bool(policy.eff_deny and count >= policy.eff_deny)
    if is_admin and not policy.even_deny_root:
        locked = False
    if latest is not None and duration:
        locked = locked and int(time.time()) <= latest + duration
    return count, locked, latest


# ==============================================================================
# Rendering
# ==============================================================================

def render_state(policy: FaillockPolicy, ctx: UserContext) -> None:
    """Table of current file state + effective policy + live tally."""
    if not RICH_AVAILABLE:
        _out("Current policy:")
        _out(f"  deny={policy.eff_deny}  fail_interval={policy.eff_fail_interval}s  "
             f"unlock_time={policy.eff_unlock_time}s  root_unlock_time={policy.eff_root_unlock_time}s "
             f"({'active' if policy.even_deny_root else 'inactive'})  "
             f"even_deny_root={'yes' if policy.even_deny_root else 'no'}  dir={policy.eff_tally_dir}")
        count, locked, _ = faillock_state(ctx.username, policy)
        _out(f"  {ctx.username}: {count} failure(s) — estimated {'LOCKED' if locked else 'not locked'}")
        return

    table = Table(title="PAM faillock — Current Policy", header_style="bold cyan", expand=True)
    table.add_column("Option", style="bold white")
    table.add_column("In File", style="yellow")
    table.add_column("Effective", style="green")

    def row(label: str, raw: str, eff: str, warn: bool = False) -> None:
        table.add_row(label, raw, f"[bold red]{eff}[/bold red]" if warn else eff)

    status = lambda v: (str(v) if v is not None else "—")
    row("Failure threshold (deny)", status(policy.deny), str(policy.eff_deny))
    row("Counting window (fail_interval)", status(policy.fail_interval), f"{policy.eff_fail_interval}s ({format_duration(policy.eff_fail_interval)})")
    row("Lock duration (unlock_time)", status(policy.unlock_time), format_duration(policy.eff_unlock_time), warn=policy.eff_unlock_time == 0)
    row("Root lock duration", status(policy.root_unlock_time),
        format_duration(policy.eff_root_unlock_time) if policy.even_deny_root else "inactive (root excluded)")
    row("even_deny_root", "set" if policy.even_deny_root else "—", "yes" if policy.even_deny_root else "no")
    row("admin_group", status(policy.admin_group), status(policy.admin_group))
    row("Tally directory (dir)", status(policy.tally_dir), policy.eff_tally_dir)
    _out(table)

    count, locked, _ = faillock_state(ctx.username, policy)
    lock_txt = Text.from_markup(
        f"Current tally for [bold cyan]{ctx.username}[/bold cyan]: "
        f"{count} failure(s) — "
        + ("[bold red]LOCKED[/bold red]" if locked else "[bold green]not locked[/bold green]")
    )
    _out(Panel(lock_txt, title="Estimated Account State (service overrides may differ)", border_style="red" if locked else "green", expand=False))


def render_diff(old: str, new: str) -> None:
    """Unified diff of the policy file before/after."""
    if not RICH_AVAILABLE:
        _out("--- current ---")
        _out(old)
        _out("--- new ---")
        _out(new)
        return
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), "current", "new", lineterm=""):
        style = "bold green" if line.startswith("+") else "bold red" if line.startswith("-") else "dim"
        console.print(line, style=style, markup=False, highlight=False)



# ==============================================================================
# Apply flow
# ==============================================================================

def apply_policy(policy: FaillockPolicy, *, dry_run: bool = False) -> bool:
    """Preserve unmanaged settings; require a backup before replacing a file."""
    original = read_config(CONFIG_PATH)
    current = FaillockPolicy.from_content(original)
    if CONFIG_PATH.exists() and current == policy:
        _out("[bold green]Policy already matches — nothing to do.[/bold green]")
        return True
    content = policy.to_content(original)
    if FaillockPolicy.from_content(content) != policy:
        raise ValueError("Generated policy failed round-trip validation")
    render_diff(original, content)
    if dry_run:
        _out("[bold yellow]DRY-RUN — no changes made[/bold yellow]")
        return True
    backup = backup_with_retention(CONFIG_PATH) if CONFIG_PATH.exists() else None
    if read_config(CONFIG_PATH) != original:
        raise RuntimeError("Configuration changed while preparing the update; rerun")
    if policy.tally_dir is not None:
        Path(policy.tally_dir).mkdir(parents=True, exist_ok=True)
    if not write_atomic(CONFIG_PATH, content):
        return False
    if read_config(CONFIG_PATH) != content:
        raise RuntimeError(f"Written configuration differs from requested content; backup: {backup}")
    _out(f"[bold green]Applied → {CONFIG_PATH}[/bold green]")
    return True


# ==============================================================================
# Interactive wizard
# ==============================================================================

WIZARD_PRESETS: Final = [
    (Preset.BALANCED, "Balanced (recommended)", "5 failures → 5 min lock; root excluded"),
    (Preset.LENIENT, "Lenient — short lock", "6 failures → 90 s lock (original 225 intent)"),
    (Preset.STRICT, "Strict — stock values", "3 failures → 10 min lock (pam defaults, pinned)"),
    (Preset.NEVER, "Never auto-unlock", "Permanent lock until manual faillock --reset"),
    (Preset.DEFAULT, "Stock default template", "Reset managed options to pam defaults"),
]


def _select(prompt: str, options: list[str], default_idx: int = 1) -> str:
    """Numbered-choice selector (accepts 1-N, partial text, or exact text)."""
    while True:
        _out(f"[bold cyan]{prompt}[/bold cyan]")
        for i, o in enumerate(options, 1):
            _out(f"  [yellow]{i}[/yellow]. {o}" + ("  [dim](default)[/dim]" if i == default_idx else ""))
        raw = (Prompt.ask(">", default=str(default_idx)) or "").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        for o in options:
            if raw and (o.startswith(raw) or raw in o):
                return o
        _err("[bold red]✖ Invalid choice — enter a number (1-{}) or part of the option text.[/bold red]".format(len(options)))


_DURATION_MAP: Final = frozendict({
    "30 seconds": 30, "90 seconds": 90, "5 minutes": 300, "10 minutes": 600,
    "15 minutes": 900, "30 minutes": 1800, "1 hour": 3600, "never": 0,
})


def run_wizard(ctx: UserContext, dry_run: bool) -> bool:
    """Interactive preset + tuning flow (Rich prompts)."""
    profile_opts = [f"{p.value} — {label}" for p, label, _ in WIZARD_PRESETS]
    choice = _select("Select a policy profile", profile_opts)
    preset = Preset(choice.split(" — ", 1)[0])

    if preset is Preset.DEFAULT:
        policy = FaillockPolicy()
    else:
        base = PRESET_TABLE[preset]
        deny = IntPrompt.ask("Failures before lockout (deny)", default=int(base.get("deny", 5)), choices=[str(i) for i in range(1, 21)])
        unlock_sel = _select("Lock duration (unlock_time)",
                             ["30 seconds", "90 seconds", "5 minutes", "10 minutes", "15 minutes", "30 minutes", "1 hour", "never (manual reset only)"],
                             default_idx=list(_DURATION_MAP.values()).index(base["unlock_time"]) + 1)
        unlock_seconds = _DURATION_MAP["never" if unlock_sel.startswith("never") else unlock_sel]
        even_root = Confirm.ask("Apply locking to root as well (even_deny_root)?",
                                default=bool(base.get("even_deny_root", False)))
        root_seconds = None
        if even_root:
            root_sel = _select("Root lock duration (root_unlock_time)",
                               ["Same as regular users", "30 seconds", "5 minutes", "10 minutes", "30 minutes", "never"])
            root_seconds = unlock_seconds if root_sel == "Same as regular users" else _DURATION_MAP[root_sel]
        policy = FaillockPolicy(
            deny=deny,
            fail_interval=900,
            unlock_time=unlock_seconds,
            root_unlock_time=root_seconds,
            even_deny_root=even_root,
        )

    _out("\n[bold cyan]Resulting policy:[/bold cyan]")
    render_state(policy, ctx)
    if not dry_run and not Confirm.ask("Apply this policy?", default=True):
        _out("[bold yellow]Aborted by user.[/bold yellow]")
        return True
    return apply_policy(policy, dry_run=dry_run)


# ==============================================================================
# Empirical verification pipeline (--verify)
# ==============================================================================

class PamProbe:
    """Small binding to documented Linux-PAM APIs for private, silent services."""

    def __init__(self, confdir: Path, username: str) -> None:
        library = ctypes.util.find_library("pam")
        if library is None:
            raise RuntimeError("libpam is unavailable")
        self.lib = ctypes.CDLL(library)
        callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int,
                                        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)

        class Conversation(ctypes.Structure):
            _fields_ = [("conv", callback_type), ("appdata_ptr", ctypes.c_void_p)]

        # These services never prompt. Any unexpected conversation is an error.
        self.callback = callback_type(lambda *_: 19)  # PAM_CONV_ERR
        self.conversation = Conversation(self.callback, None)
        self.lib.pam_start_confdir.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(Conversation),
            ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p),
        ]
        self.lib.pam_start_confdir.restype = ctypes.c_int
        self.lib.pam_authenticate.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.pam_authenticate.restype = ctypes.c_int
        self.lib.pam_end.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.pam_end.restype = ctypes.c_int
        self.confdir = os.fsencode(confdir)
        self.username = username.encode("utf-8")

    def authenticate(self, service: str) -> int:
        handle = ctypes.c_void_p()
        status = self.lib.pam_start_confdir(service.encode("ascii"), self.username,
                                          ctypes.byref(self.conversation), self.confdir,
                                          ctypes.byref(handle))
        if status:
            raise RuntimeError(f"pam_start_confdir failed: {status}")
        try:
            status = self.lib.pam_authenticate(handle, 0x8000)  # PAM_SILENT
            return status
        finally:
            end_status = self.lib.pam_end(handle, status)
            if end_status:
                raise RuntimeError(f"pam_end failed: {end_status}")


def verify_policy(ctx: UserContext) -> bool:
    """Test installed module behavior without touching production policy/tallies.

    A short synthetic policy tests threshold, rejection, timed recovery,
    authsucc and CLI reset. This does not certify any production login service.
    """
    evidence = []
    start = datetime.now()

    def check(condition: bool, detail: str) -> None:
        evidence.append(f"{'PASS' if condition else 'FAIL'}: {detail}")
        _out(evidence[-1])
        if not condition:
            raise RuntimeError(detail)

    passed = False
    try:
        with tempfile.TemporaryDirectory(prefix="faillock-verify-") as directory:
            workspace = Path(directory)
            tally = workspace / "tally"
            tally.mkdir()
            config = workspace / "faillock.conf"
            test = FaillockPolicy(deny=3, fail_interval=900, unlock_time=2,
                                 root_unlock_time=2, even_deny_root=True,
                                 tally_dir=str(tally))
            config.write_text(test.to_content() + "silent\nnodelay\n", encoding="utf-8")
            option = f"conf={config}"
            services = {
                "probe": f"auth required pam_faillock.so preauth {option}\n",
                "failure": (f"auth requisite pam_faillock.so preauth {option}\n"
                            "auth required pam_deny.so\n"
                            f"auth required pam_faillock.so authfail {option}\n"),
                "success": (f"auth requisite pam_faillock.so preauth {option}\n"
                            "auth required pam_permit.so\n"
                            f"auth required pam_faillock.so authsucc {option}\n"),
            }
            for name, content in services.items():
                (workspace / name).write_text(content, encoding="utf-8")
            probe = PamProbe(workspace, ctx.username)
            check(probe.authenticate("probe") == 0, "empty tally permits preauth")
            for expected in range(1, 4):
                check(probe.authenticate("failure") == 7, f"synthetic failure {expected} returns PAM_AUTH_ERR")
                count, _, _ = faillock_state(ctx.username, test)
                check(count == expected, f"valid tally count is {expected}")
                check(probe.authenticate("probe") == (7 if expected == 3 else 0),
                      f"preauth enforces threshold after {expected} failures")
            check(probe.authenticate("success") == 7, "locked account rejects synthetic success")
            check(probe.authenticate("failure") == 7, "locked account rejects further failure")
            count, _, _ = faillock_state(ctx.username, test)
            check(count == 3, "locked attempts do not increment tally")
            _out("Waiting 3 seconds for the private test lock to expire…")
            time.sleep(3)
            check(probe.authenticate("probe") == 0, "PAM preauth permits access after expiry")
            check(probe.authenticate("failure") == 7, "new failure after expiry is rejected")
            count, _, _ = faillock_state(ctx.username, test)
            check(count == 1, "new failure after expiry restarts valid tally")
            check(probe.authenticate("success") == 0, "synthetic success is accepted after expiry")
            check(faillock_state(ctx.username, test)[0] == 0, "authsucc clears the tally")
            check(probe.authenticate("failure") == 7, "create failure for CLI reset test")
            result = run(["faillock", "--conf", str(config), "--dir", str(tally),
                          "--user", ctx.username, "--reset"], timeout=10)
            check(result.returncode == 0, "faillock CLI reset succeeds")
            check(faillock_state(ctx.username, test)[0] == 0, "CLI reset clears private tally")
        passed = True
    except (OSError, RuntimeError, ValueError) as exc:
        evidence.append(f"FAIL: {exc}")
        _err(evidence[-1])
    finally:
        try:
            VERIFY_LOG_DIR.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=f"verify-{start:%Y%m%d-%H%M%S-%f}.",
                                        suffix=".log", dir=VERIFY_LOG_DIR)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(f"# Isolated PAM module verification; user={ctx.username}\n")
                stream.write("\n".join(evidence) + "\n")
                stream.write(f"Result: {'PASS' if passed else 'FAIL / INTERRUPTED'}\n")
            _out(f"Verification log: {name}")
        except OSError as exc:
            _err(f"Cannot persist verification log: {exc}")
            passed = False
    _out("Isolated PAM module verification " + ("PASSED" if passed else "FAILED"))
    _out("Production login services and their PAM argument overrides were not tested.")
    return passed


# ==============================================================================
# CLI & main
# ==============================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage PAM faillock policy; verify the installed module in isolation.",
        allow_abbrev=False,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("-s", "--show", action="store_true", help="Show policy and estimated account state")
    mode.add_argument("-V", "--verify", action="store_true", help="Test installed PAM module with private policy/tallies")
    mode.add_argument("-i", "--interactive", action="store_true", help="Run interactive wizard")
    parser.add_argument("-p", "--preset", choices=list(Preset), help="Replace managed options with a preset")
    parser.add_argument("--deny", type=int, metavar="N", help="Failure threshold (0-65535; 0 disables lockout)")
    parser.add_argument("--unlock-time", type=parse_duration, metavar="DUR", help="Lock duration: 90s / 10m / 1h / never")
    parser.add_argument("--fail-interval", type=int, metavar="SEC", help="Counting window (0-604800 seconds)")
    parser.add_argument("--root-unlock-time", type=parse_duration, metavar="DUR", help="Root lock duration; enables root locking")
    root = parser.add_mutually_exclusive_group()
    root.add_argument("--even-deny-root", action="store_const", const=True, default=None, dest="even_deny_root", help="Lock root too")
    root.add_argument("--no-even-deny-root", action="store_const", const=False, dest="even_deny_root", help="Exclude root and remove its explicit duration")
    parser.add_argument("--dir", metavar="PATH", help="Absolute tally directory (created on apply)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="Preview changes without writes or elevation")
    parser.add_argument("-y", "--yes", action="store_true", help="Apply without confirmation")
    return parser


def confirm_apply() -> bool:
    if RICH_AVAILABLE:
        return Confirm.ask("Apply this policy?", default=True)
    return input("Apply this policy? [Y/n] ").strip().lower() in ("", "y", "yes")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    changes = {key: value for key, value in {
        "deny": args.deny, "unlock_time": args.unlock_time,
        "fail_interval": args.fail_interval, "root_unlock_time": args.root_unlock_time,
        "even_deny_root": args.even_deny_root, "tally_dir": args.dir,
    }.items() if value is not None}
    if (args.show or args.verify or args.interactive) and (args.preset or changes):
        parser.error("--show, --verify and --interactive cannot be combined with policy options")
    if args.verify and args.dry_run:
        parser.error("--verify cannot be combined with --dry-run")
    if args.root_unlock_time is not None and args.even_deny_root is False:
        parser.error("--root-unlock-time conflicts with --no-even-deny-root")
    if args.root_unlock_time is not None:
        changes["even_deny_root"] = True
    if args.even_deny_root is False:
        changes["root_unlock_time"] = None
    # Validate explicit values before requesting privileges.
    try:
        FaillockPolicy(**changes)
    except ValueError as exc:
        parser.error(str(exc))
    interactive = args.interactive or not (args.show or args.verify or args.preset or changes)
    if interactive and (not sys.stdin.isatty() or not RICH_AVAILABLE):
        parser.error("wizard requires a TTY and python-rich; use explicit options with --yes or --dry-run")
    if not (args.show or args.verify or args.dry_run or args.yes) and not sys.stdin.isatty():
        parser.error("non-interactive apply requires --yes or --dry-run")
    if not (args.show or args.dry_run):
        ensure_root()
    ctx = resolve_user_context()
    policy = FaillockPolicy.from_file()
    if args.show:
        render_state(policy, ctx)
        return 0
    if args.verify:
        return 0 if verify_policy(ctx) else 1
    if interactive:
        return 0 if run_wizard(ctx, dry_run=args.dry_run) else 1
    base = FaillockPolicy(**PRESET_TABLE[Preset(args.preset)]) if args.preset else policy
    new = replace(base, **changes)
    if not (args.yes or args.dry_run):
        render_state(new, ctx)
        if not confirm_apply():
            _out("Aborted by user.")
            return 130
    return 0 if apply_policy(new, dry_run=args.dry_run) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        _err("Aborted by user.")
        sys.exit(130)
    except (OSError, RuntimeError, ValueError) as exc:
        _err(f"[bold red]{exc}[/bold red]")
        sys.exit(1)
