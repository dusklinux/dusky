#!/usr/bin/env python3
# =============================================================================
# Dusky Monitor Scale Adjuster — Production Bleeding-Edge Edition
# Target: Arch Linux (rolling) | Linux 7.3+ | Python 3.15+ | Hyprland git (Lua)
# Standard: Wayland exclusive | wp_fractional_scale_v1 (1/120) | XDG Base Dir
#
# Design Principles:
#   • Mathematical perfection: computes exact fractional ladders via
#     gcd(120*W, 120*H) with safe logical floors guaranteeing 1.0x and
#     non-empty ladders across all aspect ratios, resolutions, and rotations.
#   • Full Lua syntax fidelity: zero-dependency single-pass Lua lexer/AST
#     supporting bare and bracketed keys (["k"], ['k'], [[k]]), optional call
#     parentheses, comma/semicolon separators, nested tables/calls, and
#     single/multi-line comments without ever reformatting untouched lines.
#   • Full field preservation: merges matching active hl.monitor blocks
#     verbatim into live `hyprctl eval` payloads (preserving cm, bitdepth,
#     vrr, sdr*, icc, reserved_area, mirror) while skipping disabled blocks.
#   • Strict field ownership: persists ONLY 'scale' to monitors.lua; never
#     writes 'transform' so ephemeral rotations remain ephemeral.
#   • Zero-corruption persistence: Eval-Verify-Persist with DPMS wake,
#     race-free re-read under CONFIG_LOCK, two-stage pre-write verification
#     (pure-Python round-trip + native luac/luajit/lua parse check), and
#     symlink-preserving atomic replacement.
# =============================================================================

import argparse
import difflib
import errno
import fcntl
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

# ---------------------------------------------------------------------------
# 0. Constants & Paths (XDG Compliant)
# ---------------------------------------------------------------------------
XDG_CONFIG_HOME: Final = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
XDG_RUNTIME_DIR: Final = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())

CONFIG_DIR: Final = XDG_CONFIG_HOME / "hypr" / "edit_here" / "source"
CONFIG_FILE: Final = CONFIG_DIR / "monitors.lua"
LOCK_FILE: Final = XDG_RUNTIME_DIR / f"hypr-adjust-scale-{os.getuid()}.lock"
CONFIG_LOCK: Final = XDG_RUNTIME_DIR / f"hypr-monitors-lua-{os.getuid()}.lock"

NOTIFY_APP: Final = "hypr-scale"
NOTIFY_TAG: Final = "hypr-scale"

SCALE_Q: Final = 120          # wp_fractional_scale_v1 granularity denominator
SCALE_MIN_N: Final = 60       # 0.50x
SCALE_MAX_N: Final = 480      # 4.00x
IPC_SCALE_TOL: Final = 0.0051 # Hyprland JSON IPC 2-decimal rounding envelope

MIN_LOGICAL_LONG: Final = 640
MIN_LOGICAL_SHORT: Final = 360

POLL_INTERVAL: Final = 0.1
POLL_TIMEOUT: Final = 2.5
DPMS_WAKE_TIMEOUT: Final = 1.5

DEBUG: Final = os.environ.get("DEBUG") == "1"

CONFIG_HEADER: Final = (
    "-- ==============================================================================\n"
    "-- USER CONFIGURATION: monitors.lua\n"
    "-- ==============================================================================\n\n"
)

LUA_KEYWORDS: Final[frozenset[str]] = frozenset({
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function",
    "goto", "if", "in", "local", "nil", "not", "or", "repeat", "return",
    "then", "true", "until", "while",
})

_RE_LONG_OPEN: Final = re.compile(r"\[(=*)\[")
_RE_IDENT: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RE_NUMBER: Final = re.compile(
    r"0[xX][0-9a-fA-F]+(?:\.[0-9a-fA-F]*)?(?:[pP][+-]?\d+)?"
    r"|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
)
_RE_MODE: Final = re.compile(r"^(\d+)x(\d+)@([0-9.]+)(?:Hz)?$", re.IGNORECASE)
_RE_DESC_PORT: Final = re.compile(r"\s+\([^)]*\)\s*$")
_RE_SHORT_DESC: Final = re.compile(r"^[A-Za-z0-9]+(?:\s+[A-Za-z0-9]+)*\s+([A-Za-z0-9_.-]+)")

_LUA_MULTI_OPS: Final[tuple[str, ...]] = (
    "...", "==", "~=", "<=", ">=", "//", "<<", ">>", "..", "::",
)


# ---------------------------------------------------------------------------
# 1. Logging, Notifications & Exceptions
# ---------------------------------------------------------------------------
def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if code and sys.stderr.isatty() else text


def log_info(msg: str) -> None:
    print(_c("34", "[INFO] ") + f" {msg}", file=sys.stderr)


def log_ok(msg: str) -> None:
    print(_c("32", "[OK]   ") + f" {msg}", file=sys.stderr)


def log_warn(msg: str) -> None:
    print(_c("33", "[WARN] ") + f" {msg}", file=sys.stderr)


def log_err(msg: str) -> None:
    print(_c("31", "[ERROR]") + f" {msg}", file=sys.stderr)


def log_debug(msg: str) -> None:
    if DEBUG:
        print(_c("35", "[DEBUG]") + f" {msg}", file=sys.stderr)


def _short(text: str, limit: int) -> str:
    """Collapse whitespace and truncate with ellipsis so text fits notification pills."""
    flat = " ".join(str(text).split())
    if len(flat) <= limit:
        return flat
    return flat[: max(0, limit - 1)].rstrip() + "…"


def notify(
    title: str,
    body: str = "",
    urgency: str = "low",
    icon: str = "video-display",
    ms: int = 1800,
) -> None:
    if shutil.which("notify-send") is None:
        return
    title = _short(title, 34)
    body = _short(body, 36)
    try:
        subprocess.run(
            [
                "notify-send",
                f"--app-name={NOTIFY_APP}",
                f"--icon={icon}",
                f"--urgency={urgency}",
                f"--expire-time={ms}",
                f"--hint=string:x-canonical-private-synchronous:{NOTIFY_TAG}",
                title,
                body,
            ],
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


class HyprError(Exception):
    pass


class LuaLexError(HyprError):
    pass


# ---------------------------------------------------------------------------
# 2. Environment Guard & Debounce Locking
# ---------------------------------------------------------------------------
def guard_environment() -> None:
    if os.geteuid() == 0:
        raise HyprError("Refusing to run as root: hyprctl requires the active user Wayland session.")
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        raise HyprError("HYPRLAND_INSTANCE_SIGNATURE is unset: not inside an active Hyprland session.")
    if shutil.which("hyprctl") is None:
        raise HyprError("'hyprctl' executable not found in PATH.")


_LOCK_HANDLE: Any = None


def acquire_debounce_lock(lock_path: Path, min_interval: float = 0.15) -> bool:
    """Non-blocking flock with monotonic timestamp debounce.

    Returns False ONLY on lock contention or debounce interval drop (caller exits 0).
    On unexpected filesystem OSErrors, logs a warning and returns True so user
    commands are not silently swallowed.
    """
    global _LOCK_HANDLE
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        handle = open(fd, "r+b", buffering=0)
    except OSError as exc:
        log_warn(f"Debounce lock unavailable ({lock_path}: {exc}); continuing unlocked")
        return True

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return False
    except OSError as exc:
        handle.close()
        if exc.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
            return False
        log_warn(f"Debounce flock failed ({lock_path}: {exc}); continuing unlocked")
        return True

    now = time.monotonic()
    try:
        raw = handle.read().decode("ascii", errors="ignore").strip()
        if raw:
            elapsed = now - float(raw)
            if 0.0 <= elapsed < min_interval:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
                return False
    except (ValueError, OSError):
        pass

    try:
        handle.seek(0)
        handle.truncate(0)
        handle.write(f"{now:.6f}\n".encode("ascii"))
        handle.flush()
    except OSError:
        pass

    _LOCK_HANDLE = handle
    return True


def update_debounce_timestamp() -> None:
    """Updates the monotonic timestamp in the lockfile to mark operation completion."""
    if _LOCK_HANDLE is not None:
        try:
            _LOCK_HANDLE.seek(0)
            _LOCK_HANDLE.truncate(0)
            _LOCK_HANDLE.write(f"{time.monotonic():.6f}\n".encode("ascii"))
            _LOCK_HANDLE.flush()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 3. Safe Numeric Coercion & Pure-Lua IPC Helpers
# ---------------------------------------------------------------------------
def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        f = float(value)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def ipc_json(subcommand: str, timeout: float = 3.0, soft: bool = False) -> Any:
    try:
        proc = subprocess.run(
            ["hyprctl", "-j", subcommand],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        if soft:
            return None
        raise HyprError(f"hyprctl -j {subcommand} failed: {exc}") from exc

    if proc.returncode != 0:
        if soft:
            return None
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise HyprError(f"hyprctl -j {subcommand} exited {proc.returncode}: {detail}")

    raw = proc.stdout.strip()
    if not raw:
        if soft:
            return None
        raise HyprError(f"hyprctl -j {subcommand} returned empty output.")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        if soft:
            return None
        raise HyprError(f"Invalid JSON from hyprctl -j {subcommand}: {exc}") from exc


def ipc_eval(lua_chunk: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Evaluates a Lua chunk directly against Hyprland's Lua config engine."""
    log_debug(f"IPC eval: {lua_chunk}")
    try:
        proc = subprocess.run(
            ["hyprctl", "eval", lua_chunk],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"hyprctl eval failed: {exc}"

    reply = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode == 0 and reply.lower() == "ok":
        return True, reply
    detail = reply or err or f"exit {proc.returncode}"
    return False, f"hyprctl eval rejected ({detail})"


def list_monitors(soft: bool = False) -> list[dict[str, Any]]:
    payload = ipc_json("monitors", soft=soft)
    if not isinstance(payload, list) or not payload:
        if soft:
            return []
        raise HyprError("hyprctl returned no active monitors.")
    real = [
        m for m in payload
        if isinstance(m, dict) and str(m.get("name") or "").strip().upper() != "FALLBACK"
    ]
    if not real:
        if soft:
            return []
        raise HyprError("hyprctl returned no active physical monitors (compositor in synthetic fallback state).")
    return real


def normalize_description(desc: str) -> str:
    return _RE_DESC_PORT.sub("", desc).strip()


def short_description(desc: str) -> str:
    clean = normalize_description(desc)
    m = _RE_SHORT_DESC.match(clean)
    return m.group(1) if m else clean


def pick_monitor(monitors: list[dict[str, Any]], target: str | None) -> dict[str, Any]:
    if not monitors:
        raise HyprError("No monitors found.")
    if target:
        found = next((m for m in monitors if m.get("name") == target), None)
        if found is None:
            clean = target[5:].strip() if target.startswith("desc:") else target.strip()
            if clean:
                found = next(
                    (
                        m for m in monitors
                        if str(m.get("description") or "").startswith(clean)
                        or normalize_description(str(m.get("description") or "")).startswith(clean)
                    ),
                    None,
                )
            if found is None and clean:
                found = next(
                    (m for m in monitors if clean.lower() in str(m.get("description") or "").lower()),
                    None,
                )
        if found is None:
            available = ", ".join(f"{m.get('name')} ({m.get('description', '')})" for m in monitors)
            raise HyprError(f"Monitor '{target}' not found. Available: {available}")
        return found
    return next((m for m in monitors if m.get("focused") is True), monitors[0])


def wait_for_monitor(
    name: str,
    predicate: Callable[[dict[str, Any]], bool],
    timeout: float = POLL_TIMEOUT,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while True:
        mons = list_monitors(soft=True)
        for m in mons:
            if m.get("name") == name:
                last = m
                if predicate(m):
                    return m
                break
        if time.monotonic() >= deadline:
            return last
        time.sleep(POLL_INTERVAL)


def wake_monitor_dpms(name: str) -> None:
    """Wakes a sleeping monitor using Hyprland's Lua dispatcher API and waits for settle."""
    log_info(f"{name}: waking monitor from DPMS sleep")
    wake_chunk = f'hl.dispatch(hl.dsp.dpms({{ action = "enable", monitor = {lua_str(name)} }}))'
    ok, reply = ipc_eval(wake_chunk)
    if not ok:
        log_warn(f"{name}: DPMS wake warning: {reply}")
        return
    settled = wait_for_monitor(
        name,
        lambda m: bool(m.get("dpmsStatus", False)),
        timeout=DPMS_WAKE_TIMEOUT,
    )
    if not settled or not bool(settled.get("dpmsStatus", False)):
        log_warn(f"{name}: DPMS wake timed out waiting for dpmsStatus=true; proceeding")


# ---------------------------------------------------------------------------
# 4. Wayland Fractional Scaling Mathematics
# ---------------------------------------------------------------------------
def clean_scale(n: int, w: int, h: int) -> bool:
    return n > 0 and w > 0 and h > 0 and (SCALE_Q * w) % n == 0 and (SCALE_Q * h) % n == 0


def scale_ladder(w: int, h: int, *, unchecked: bool = False) -> list[int]:
    """Generates exact ascending n (scale = n/120) accepted by Hyprland.

    Caps effective logical floors to the panel's native dimensions so 1.0x
    (n=120) is guaranteed valid and the returned ladder is never empty across
    all resolutions and aspect ratios.
    """
    if w <= 0 or h <= 0:
        raise HyprError(f"Invalid physical monitor dimensions: {w}x{h}")

    if unchecked:
        ns = sorted({
            round(s * SCALE_Q)
            for s in (
                0.5, 0.6, 0.75, 0.8, 0.9, 1.0, 1.1, 1.125, 1.15, 1.2, 1.25, 4 / 3,
                1.4, 1.5, 1.6, 5 / 3, 1.75, 1.8, 1.875, 2.0, 2.25, 2.4, 2.5, 8 / 3,
                2.8, 3.0, 3.5, 4.0,
            )
        })
    else:
        g = math.gcd(SCALE_Q * w, SCALE_Q * h)
        ns = [n for n in range(SCALE_MIN_N, SCALE_MAX_N + 1) if g % n == 0]

    long_, short = max(w, h), min(w, h)
    eff_min_long = min(MIN_LOGICAL_LONG, long_)
    eff_min_short = min(MIN_LOGICAL_SHORT, short)

    filtered = [
        n for n in ns
        if SCALE_MIN_N <= n <= SCALE_MAX_N
        and long_ * SCALE_Q >= eff_min_long * n
        and short * SCALE_Q >= eff_min_short * n
    ]
    return filtered or [SCALE_Q]


def snap_scale(ipc_scale: float, w: int, h: int, hint: float | None = None) -> int:
    """Recovers the exact n/120 quantum from Hyprland's 2-decimal IPC representation."""
    if not math.isfinite(ipc_scale) or ipc_scale <= 0:
        return SCALE_Q
    if hint is not None and math.isfinite(hint) and hint > 0 and abs(hint - ipc_scale) <= IPC_SCALE_TOL:
        return max(1, round(hint * SCALE_Q))

    centre = ipc_scale * SCALE_Q
    lo = max(1, math.ceil(centre - 0.61))
    hi = max(lo, math.floor(centre + 0.61))
    candidates = [
        n for n in range(lo, hi + 1)
        if abs((n / SCALE_Q) - ipc_scale) <= IPC_SCALE_TOL and clean_scale(n, w, h)
    ]
    if candidates:
        candidates.sort(key=lambda val: (abs(val - centre), val))
        return candidates[0]
    return max(1, round(centre))


def fmt_scale(n: int) -> str:
    val = n / SCALE_Q
    s = f"{val:.6f}".rstrip("0").rstrip(".")
    return s or "1"


def parse_scale_value(val_str: str, w: int, h: int, unchecked: bool = False) -> int:
    cleaned = val_str.strip()
    if cleaned.lower() == "auto":
        raise HyprError("Scale 'auto' is not a fixed numeric quantum; pass a numeric scale (e.g. 1, 1.25, 1.5).")
    try:
        s = float(cleaned)
    except ValueError:
        raise HyprError(f"Invalid scale value '{val_str}': must be a finite number.") from None
    if not math.isfinite(s):
        raise HyprError(f"Invalid scale value '{val_str}': must be a finite number.")

    min_s, max_s = SCALE_MIN_N / SCALE_Q, SCALE_MAX_N / SCALE_Q
    if not (min_s <= s <= max_s):
        raise HyprError(f"Scale {s} outside supported range [{min_s}, {max_s}].")

    n = round(s * SCALE_Q)
    if not unchecked and not clean_scale(n, w, h):
        ladder = scale_ladder(w, h)
        if not ladder:
            raise HyprError(f"No valid clean scale values found for {w}x{h}.")
        nearest_n = min(ladder, key=lambda cand: (abs(cand - n), cand))
        log_warn(f"Scale {s} does not divide {w}x{h} cleanly. Snapping to nearest valid: {fmt_scale(nearest_n)}")
        return nearest_n
    return n


def logical_size(w: int, h: int, n: int, transform: int) -> tuple[int, int]:
    tw = h if (transform & 1) else w
    th = w if (transform & 1) else h
    return round((tw * SCALE_Q) / n), round((th * SCALE_Q) / n)


def next_scale(cur_n: int, direction: str, ladder: list[int]) -> int | None:
    if direction == "+":
        ups = [n for n in ladder if n > cur_n]
        return ups[0] if ups else None
    downs = [n for n in ladder if n < cur_n]
    return downs[-1] if downs else None


# ---------------------------------------------------------------------------
# 5. Pure-Python Lua Lexer, AST Parser & Reverse-Span Rewriter
# ---------------------------------------------------------------------------
def lua_str(s: str) -> str:
    return json.dumps(str(s), ensure_ascii=False)


def _lua_key(k: str) -> str:
    if _RE_IDENT.fullmatch(k) and k not in LUA_KEYWORDS:
        return k
    return f"[{lua_str(k)}]"


def lua_string_value(raw: str) -> str | None:
    """Decodes a Lua short string or long-bracket string literal accurately."""
    s = raw.strip()
    if len(s) < 2:
        return None

    # Long-bracket string: [=[...]=]
    m_open = _RE_LONG_OPEN.match(s)
    if m_open:
        eqs = m_open.group(1)
        close_delim = f"]{eqs}]"
        if not s.endswith(close_delim) or len(s) < len(m_open.group(0)) + len(close_delim):
            return None
        body = s[m_open.end(): len(s) - len(close_delim)]
        if body.startswith("\r\n"):
            body = body[2:]
        elif body.startswith("\n"):
            body = body[1:]
        return body

    # Short string: "..." or '...'
    q = s[0]
    if q not in ('"', "'") or s[-1] != q:
        return None
    inner = s[1:-1]
    out: list[str] = []
    i, n = 0, len(inner)
    simple_escapes: dict[str, str] = {
        "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r",
        "t": "\t", "v": "\v", "\\": "\\", '"': '"', "'": "'",
    }
    while i < n:
        ch = inner[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= n:
            return None
        esc = inner[i]
        if esc in simple_escapes:
            out.append(simple_escapes[esc])
            i += 1
        elif esc == "\n":
            out.append("\n")
            i += 1
        elif esc == "\r":
            out.append("\n")
            i += 2 if (i + 1 < n and inner[i + 1] == "\n") else 1
        elif esc == "z":
            i += 1
            while i < n and inner[i] in " \t\r\n\f\v":
                i += 1
        elif esc == "x" and i + 2 < n:
            hex_part = inner[i + 1: i + 3]
            try:
                out.append(chr(int(hex_part, 16)))
                i += 3
            except ValueError:
                return None
        elif esc == "u" and i + 1 < n and inner[i + 1] == "{":
            close_u = inner.find("}", i + 2)
            if close_u < 0:
                return None
            try:
                out.append(chr(int(inner[i + 2: close_u], 16)))
                i = close_u + 1
            except ValueError:
                return None
        elif esc.isdigit():
            j = i
            while j < n and j < i + 3 and inner[j].isdigit():
                j += 1
            code = int(inner[i:j])
            if code > 255:
                return None
            out.append(chr(code))
            i = j
        else:
            out.append(esc)
            i += 1
    return "".join(out)


def parse_lua_number(raw: str) -> float | None:
    s = raw.strip()
    if not s:
        return None
    try:
        val = float(s)
        return val if math.isfinite(val) else None
    except ValueError:
        return None


@dataclass(slots=True, frozen=True)
class LuaToken:
    kind: str   # IDENT | NUMBER | STRING | LSTRING | SYMBOL
    text: str
    start: int
    end: int


@dataclass(slots=True)
class TableEntry:
    key: str | None
    key_raw: str
    entry_start: int
    entry_end: int
    key_start: int
    key_end: int
    value_start: int
    value_end: int
    sep_pos: int | None
    sep_char: str | None


@dataclass(slots=True)
class MonitorBlock:
    start: int
    open_brace: int
    close_brace: int
    end: int
    has_parens: bool
    entries: list[TableEntry] = field(default_factory=list)

    def get_all(self, key: str) -> list[TableEntry]:
        return [e for e in self.entries if e.key == key]

    def get_last(self, key: str) -> TableEntry | None:
        for e in reversed(self.entries):
            if e.key == key:
                return e
        return None


def tokenize_lua(text: str) -> list[LuaToken]:
    """Single-pass pure-Python Lua lexer emitting non-comment tokens with exact offsets."""
    tokens: list[LuaToken] = []
    i, n = 0, len(text)

    while i < n:
        ch = text[i]

        # Whitespace
        if ch in " \t\r\n\f\v":
            i += 1
            continue

        # Comments: --... or --[=[...]=]
        if ch == "-" and i + 1 < n and text[i + 1] == "-":
            m_long = _RE_LONG_OPEN.match(text, i + 2)
            if m_long:
                close_delim = f"]{m_long.group(1)}]"
                close_pos = text.find(close_delim, m_long.end())
                if close_pos < 0:
                    raise LuaLexError(f"Unterminated long block comment at offset {i}")
                i = close_pos + len(close_delim)
            else:
                nl = text.find("\n", i + 2)
                i = n if nl < 0 else nl + 1
            continue

        # Long-bracket string: [=[...]=]
        if ch == "[":
            m_long = _RE_LONG_OPEN.match(text, i)
            if m_long:
                close_delim = f"]{m_long.group(1)}]"
                close_pos = text.find(close_delim, m_long.end())
                if close_pos < 0:
                    raise LuaLexError(f"Unterminated long string at offset {i}")
                end = close_pos + len(close_delim)
                tokens.append(LuaToken("LSTRING", text[i:end], i, end))
                i = end
                continue

        # Short string: "..." or '...'
        if ch in ('"', "'"):
            start = i
            q = ch
            i += 1
            closed = False
            while i < n:
                c = text[i]
                if c == "\\":
                    if i + 1 >= n:
                        raise LuaLexError(f"Unterminated short string escape at offset {start}")
                    nxt = text[i + 1]
                    if nxt == "\r" and i + 2 < n and text[i + 2] == "\n":
                        i += 3
                    elif nxt == "z":
                        i += 2
                        while i < n and text[i] in " \t\r\n\f\v":
                            i += 1
                    else:
                        i += 2
                elif c == q:
                    i += 1
                    closed = True
                    break
                elif c in ("\n", "\r"):
                    raise LuaLexError(f"Unterminated short string at offset {start}")
                else:
                    i += 1
            if not closed:
                raise LuaLexError(f"Unterminated short string at offset {start}")
            tokens.append(LuaToken("STRING", text[start:i], start, i))
            continue

        # Numbers (before '.' symbol so '.5' parses as NUMBER while '..' stays SYMBOL)
        if ch.isdigit() or (ch == "." and i + 1 < n and text[i + 1].isdigit()):
            m_num = _RE_NUMBER.match(text, i)
            if m_num:
                end = m_num.end()
                tokens.append(LuaToken("NUMBER", m_num.group(0), i, end))
                i = end
                continue

        # Identifiers / keywords
        if ch.isalpha() or ch == "_":
            m_id = _RE_IDENT.match(text, i)
            if m_id:
                end = m_id.end()
                tokens.append(LuaToken("IDENT", m_id.group(0), i, end))
                i = end
                continue

        # Multi-character Lua operators/symbols
        matched_op = False
        for op in _LUA_MULTI_OPS:
            if text.startswith(op, i):
                end = i + len(op)
                tokens.append(LuaToken("SYMBOL", op, i, end))
                i = end
                matched_op = True
                break
        if matched_op:
            continue

        # Single-character punctuation/operator
        tokens.append(LuaToken("SYMBOL", ch, i, i + 1))
        i += 1

    return tokens


def _parse_table_entries(
    text: str,
    tokens: list[LuaToken],
    open_brace_idx: int,
) -> tuple[list[TableEntry], int]:
    """Parses all top-level entries inside a Lua table constructor starting at '{'."""
    entries: list[TableEntry] = []
    n_toks = len(tokens)
    k = open_brace_idx + 1

    while k < n_toks:
        tk = tokens[k]
        if tk.kind == "SYMBOL" and tk.text == "}":
            return entries, k

        entry_start = tk.start
        key: str | None = None
        key_raw = ""
        key_start = entry_start
        key_end = entry_start

        # Case 1: bare identifier key -> name = value
        if (
            tk.kind == "IDENT"
            and k + 1 < n_toks
            and tokens[k + 1].kind == "SYMBOL"
            and tokens[k + 1].text == "="
        ):
            key = tk.text
            key_raw = text[tk.start:tk.end]
            key_start = tk.start
            key_end = tk.end
            k += 2

        # Case 2: bracketed key -> [expr] = value (including ["name"], ['name'], [[name]])
        elif tk.kind == "SYMBOL" and tk.text == "[":
            m = k + 1
            b_d = p_d = 0
            k_d = 1
            while m < n_toks and k_d > 0:
                mt = tokens[m]
                if mt.kind == "SYMBOL":
                    if mt.text == "[":
                        k_d += 1
                    elif mt.text == "]":
                        k_d -= 1
                        if k_d == 0:
                            break
                    elif mt.text == "{":
                        b_d += 1
                    elif mt.text == "}":
                        b_d -= 1
                    elif mt.text == "(":
                        p_d += 1
                    elif mt.text == ")":
                        p_d -= 1
                m += 1

            if (
                k_d == 0
                and m + 1 < n_toks
                and tokens[m + 1].kind == "SYMBOL"
                and tokens[m + 1].text == "="
            ):
                key_start = tk.start
                key_end = tokens[m].end
                key_raw = text[key_start:key_end]
                if m == k + 2 and tokens[k + 1].kind in ("STRING", "LSTRING"):
                    key = lua_string_value(text[tokens[k + 1].start:tokens[k + 1].end])
                else:
                    key = None
                k = m + 2

        # Scan value expression tracking nested {}, (), []
        val_first_idx = k
        val_last_idx = -1
        b_d = p_d = k_d = 0
        sep_pos: int | None = None
        sep_char: str | None = None

        while k < n_toks:
            vt = tokens[k]
            if b_d == 0 and p_d == 0 and k_d == 0 and vt.kind == "SYMBOL":
                if vt.text in (",", ";"):
                    sep_pos = vt.start
                    sep_char = vt.text
                    k += 1
                    break
                if vt.text == "}":
                    break
            if vt.kind == "SYMBOL":
                if vt.text == "{":
                    b_d += 1
                elif vt.text == "}":
                    b_d -= 1
                elif vt.text == "(":
                    p_d += 1
                elif vt.text == ")":
                    p_d -= 1
                elif vt.text == "[":
                    k_d += 1
                elif vt.text == "]":
                    k_d -= 1
            val_last_idx = k
            k += 1

        if val_last_idx >= val_first_idx:
            value_start = tokens[val_first_idx].start
            value_end = tokens[val_last_idx].end
        else:
            value_start = value_end = entry_start

        entries.append(
            TableEntry(
                key=key,
                key_raw=key_raw,
                entry_start=entry_start,
                entry_end=value_end,
                key_start=key_start,
                key_end=key_end,
                value_start=value_start,
                value_end=value_end,
                sep_pos=sep_pos,
                sep_char=sep_char,
            )
        )

    return entries, -1


def find_monitor_blocks(text: str, tokens: list[LuaToken] | None = None) -> list[MonitorBlock]:
    """Locates all hl.monitor({...}) and hl.monitor{...} calls in a Lua source buffer."""
    toks = tokenize_lua(text) if tokens is None else tokens
    blocks: list[MonitorBlock] = []
    i, n_toks = 0, len(toks)

    while i + 3 < n_toks:
        t0, t1, t2 = toks[i], toks[i + 1], toks[i + 2]
        if (
            t0.kind == "IDENT" and t0.text == "hl"
            and t1.kind == "SYMBOL" and t1.text == "."
            and t2.kind == "IDENT" and t2.text == "monitor"
        ):
            # Reject chained access such as other.hl.monitor(...) or obj:hl.monitor(...)
            if i > 0 and toks[i - 1].kind == "SYMBOL" and toks[i - 1].text in (".", ":"):
                i += 1
                continue

            j = i + 3
            has_parens = False
            open_brace_idx = -1

            if (
                toks[j].kind == "SYMBOL" and toks[j].text == "("
                and j + 1 < n_toks
                and toks[j + 1].kind == "SYMBOL" and toks[j + 1].text == "{"
            ):
                has_parens = True
                open_brace_idx = j + 1
            elif toks[j].kind == "SYMBOL" and toks[j].text == "{":
                has_parens = False
                open_brace_idx = j
            else:
                i += 1
                continue

            entries, close_brace_idx = _parse_table_entries(text, toks, open_brace_idx)
            if close_brace_idx < 0:
                i += 1
                continue

            if has_parens:
                if (
                    close_brace_idx + 1 < n_toks
                    and toks[close_brace_idx + 1].kind == "SYMBOL"
                    and toks[close_brace_idx + 1].text == ")"
                ):
                    block_end = toks[close_brace_idx + 1].end
                    next_i = close_brace_idx + 2
                else:
                    i = close_brace_idx + 1
                    continue
            else:
                block_end = toks[close_brace_idx].end
                next_i = close_brace_idx + 1

            blocks.append(
                MonitorBlock(
                    start=t0.start,
                    open_brace=toks[open_brace_idx].start,
                    close_brace=toks[close_brace_idx].start,
                    end=block_end,
                    has_parens=has_parens,
                    entries=entries,
                )
            )
            i = next_i
            continue

        i += 1

    return blocks


def is_disabled_block(text: str, block: MonitorBlock) -> bool:
    entry = block.get_last("disabled")
    if entry is None:
        return False
    return text[entry.value_start:entry.value_end].strip() == "true"


def block_targets(text: str, block: MonitorBlock, mon: dict[str, Any]) -> bool:
    if is_disabled_block(text, block):
        return False
    entry = block.get_last("output")
    if entry is None:
        return False
    val = lua_string_value(text[entry.value_start:entry.value_end])
    if not val:
        return False

    name = str(mon.get("name") or "")
    if val == name:
        return True
    if val.startswith("desc:"):
        prefix = val[5:].strip()
        if not prefix:
            return False
        desc = str(mon.get("description") or "")
        desc_clean = normalize_description(desc)
        if (
            desc.startswith(prefix)
            or desc_clean.startswith(prefix)
            or short_description(desc_clean).startswith(prefix)
        ):
            return True
    return False


def scale_hint(text: str, blocks: list[MonitorBlock]) -> float | None:
    for block in reversed(blocks):
        entry = block.get_last("scale")
        if entry is not None:
            raw = text[entry.value_start:entry.value_end].strip()
            # Quoted values such as "auto" cleanly yield None
            if lua_string_value(raw) is not None:
                return None
            return parse_lua_number(raw)
    return None


def block_to_eval(
    text: str,
    blocks: list[MonitorBlock],
    output_name: str,
    overrides: dict[str, str],
    default_mode: str = "",
    default_pos: str = "",
) -> str:
    """Builds a live hl.monitor() Lua call preserving every field across active blocks."""
    merged: dict[str, str] = {"output": lua_str(output_name)}
    for block in blocks:
        for entry in block.entries:
            if entry.key is None or entry.key == "output":
                continue
            merged[entry.key] = text[entry.value_start:entry.value_end]

    for k, v in overrides.items():
        merged[k] = v

    if "mode" not in merged and default_mode:
        merged["mode"] = lua_str(default_mode)
    if "position" not in merged and default_pos:
        merged["position"] = lua_str(default_pos)

    parts = [f"{_lua_key(k)} = {v}" for k, v in merged.items()]
    return "hl.monitor({ " + ", ".join(parts) + " })"


def _leading_ws(s: str) -> str:
    i = 0
    while i < len(s) and s[i] in (" ", "\t"):
        i += 1
    return s[:i]


def _infer_multiline_style(text: str, block: MonitorBlock, key: str, literal: str) -> tuple[str, str]:
    """Infers field indentation and '=' alignment style from existing entries."""
    block_line_start = text.rfind("\n", 0, block.start) + 1
    block_indent = _leading_ws(text[block_line_start:block.start])
    field_indent = block_indent + "    "

    eq_columns: list[int] = []
    has_padded_eq = False

    for entry in block.entries:
        e_line_start = text.rfind("\n", 0, entry.entry_start) + 1
        prefix = text[e_line_start:entry.entry_start]
        if prefix.strip(" \t") == "":
            field_indent = prefix
            if entry.key is not None and entry.key_raw == entry.key:
                eq_pos = text.find("=", entry.key_end, entry.value_start)
                if eq_pos >= 0:
                    col = eq_pos - entry.entry_start
                    eq_columns.append(col)
                    if eq_pos - entry.key_end > 1:
                        has_padded_eq = True

    if has_padded_eq and eq_columns and len(set(eq_columns)) == 1:
        pad = max(1, eq_columns[0] - len(key))
        return block_indent, f"{field_indent}{key}{' ' * pad}= {literal},"
    return block_indent, f"{field_indent}{key} = {literal},"


def plan_set_field(
    text: str,
    block: MonitorBlock,
    key: str,
    literal: str,
) -> list[tuple[int, int, str]]:
    """Computes non-overlapping (start, end, replacement) edits for a single block."""
    matches = block.get_all(key)
    edits: list[tuple[int, int, str]] = []

    if matches:
        # Update the last matching key in place (Lua last-key-wins semantics)
        last_match = matches[-1]
        edits.append((last_match.value_start, last_match.value_end, literal))

        # Deduplicate any earlier duplicate keys cleanly without disturbing comments
        for dup in matches[:-1]:
            rm_start = dup.entry_start
            rm_end = (dup.sep_pos + 1) if dup.sep_pos is not None else dup.entry_end
            line_start = text.rfind("\n", 0, rm_start) + 1
            nl = text.find("\n", rm_end)
            line_end = (nl + 1) if nl != -1 else len(text)
            if (
                text[line_start:rm_start].strip(" \t") == ""
                and text[rm_end:line_end].strip(" \t\r\n") == ""
            ):
                edits.append((line_start, line_end, ""))
            else:
                if rm_end < block.close_brace and text[rm_end] == " ":
                    rm_end += 1
                edits.append((rm_start, rm_end, ""))
        return edits

    is_multiline = "\n" in text[block.open_brace:block.close_brace]

    if not is_multiline:
        if not block.entries:
            edits.append((block.open_brace + 1, block.close_brace, f" {key} = {literal} "))
            return edits
        last = block.entries[-1]
        if last.sep_pos is not None:
            sep = last.sep_char or ","
            edits.append((last.sep_pos + 1, last.sep_pos + 1, f" {key} = {literal}{sep}"))
        else:
            edits.append((last.value_end, last.value_end, f", {key} = {literal}"))
        return edits

    # Multi-line table insertion
    block_indent, field_line = _infer_multiline_style(text, block, key, literal)
    close_line_start = text.rfind("\n", block.open_brace, block.close_brace) + 1
    close_prefix_is_ws = text[close_line_start:block.close_brace].strip(" \t") == ""

    if not block.entries:
        if close_prefix_is_ws:
            edits.append((close_line_start, close_line_start, f"{field_line}\n"))
        else:
            edits.append((block.close_brace, block.close_brace, f"\n{field_line}\n{block_indent}"))
        return edits

    last = block.entries[-1]
    if close_prefix_is_ws:
        if last.sep_pos is None:
            # Insert comma strictly before any trailing whitespace or inline '--' comment
            edits.append((last.value_end, last.value_end, ","))
        edits.append((close_line_start, close_line_start, f"{field_line}\n"))
    else:
        if last.sep_pos is None:
            if last.value_end == block.close_brace:
                edits.append((block.close_brace, block.close_brace, f",\n{field_line}\n{block_indent}"))
            else:
                edits.append((last.value_end, last.value_end, ","))
                edits.append((block.close_brace, block.close_brace, f"\n{field_line}\n{block_indent}"))
        else:
            edits.append((block.close_brace, block.close_brace, f"\n{field_line}\n{block_indent}"))
    return edits


def apply_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    """Applies non-overlapping (start, end, replacement) spans in reverse offset order."""
    if not edits:
        return text
    ordered = sorted(edits, key=lambda e: (e[0], e[1]), reverse=True)
    prev_start = len(text) + 1
    result = text
    for start, end, repl in ordered:
        if start < 0 or end < start or end > len(text) or end > prev_start:
            raise HyprError(f"Internal edit span error: ({start}, {end}) overlaps previous at {prev_start}")
        result = result[:start] + repl + result[end:]
        prev_start = start
    return result


def append_block(text: str, fields: dict[str, str]) -> str:
    body = "\n".join(f"    {k:<10}= {v}," for k, v in fields.items())
    block = f"hl.monitor({{\n{body}\n}})\n"
    if text and not text.endswith("\n"):
        text += "\n"
    return text + ("\n" if text.strip() else "") + block


def update_config_text(
    text: str,
    mon: dict[str, Any],
    literal: str,
    mode_str: str,
    pos_str: str,
) -> tuple[str, int, bool]:
    """Updates matching active monitor blocks or appends a new block if none match."""
    base_text = text if text.strip() else CONFIG_HEADER
    blocks = [b for b in find_monitor_blocks(base_text) if block_targets(base_text, b, mon)]
    if not blocks:
        updated = append_block(
            base_text,
            {
                "output": lua_str(str(mon["name"])),
                "mode": lua_str(mode_str),
                "position": lua_str(pos_str),
                "scale": literal,
            },
        )
        return updated, 1, True

    with_scale = [b for b in blocks if b.get_all("scale")]
    if with_scale:
        targets = with_scale
    else:
        primary = next(
            (b for b in reversed(blocks) if b.get_last("mode") or b.get_last("position")),
            blocks[-1],
        )
        targets = [primary]

    all_edits: list[tuple[int, int, str]] = []
    for block in targets:
        all_edits.extend(plan_set_field(base_text, block, "scale", literal))

    return apply_edits(base_text, all_edits), len(targets), False


def resolve_mode_string(mon: dict[str, Any]) -> str:
    w = _safe_int(mon.get("width"))
    h = _safe_int(mon.get("height"))
    refresh = _safe_float(mon.get("refreshRate"), 60.0)
    best: tuple[float, str] | None = None
    for entry in mon.get("availableModes") or []:
        m = _RE_MODE.match(str(entry))
        if not m or int(m.group(1)) != w or int(m.group(2)) != h:
            continue
        cand = (abs(float(m.group(3)) - refresh), f"{w}x{h}@{m.group(3)}")
        if best is None or cand < best:
            best = cand
    if best is None:
        return f"{w}x{h}@{refresh:.2f}"
    return best[1]


# ---------------------------------------------------------------------------
# 6. Two-Stage Pre-Write Verification Gate & Atomic Persistence
# ---------------------------------------------------------------------------
def _verify_balanced_delimiters(tokens: list[LuaToken]) -> None:
    pairs = {"{": "}", "(": ")", "[": "]"}
    closers = {"}", ")", "]"}
    stack: list[tuple[str, int]] = []
    for tok in tokens:
        if tok.kind != "SYMBOL":
            continue
        if tok.text in pairs:
            stack.append((pairs[tok.text], tok.start))
        elif tok.text in closers:
            if not stack or stack[-1][0] != tok.text:
                raise HyprError(f"Pre-write validation failed: unmatched '{tok.text}' at offset {tok.start}")
            stack.pop()
    if stack:
        expected, pos = stack[-1]
        raise HyprError(f"Pre-write validation failed: unclosed delimiter (expected '{expected}' for offset {pos})")


def _verify_native_lua_syntax(candidate: str) -> None:
    """Stage 2: zero-execution compile-only syntax check using luac, luajit, or lua."""
    luac = shutil.which("luac")
    if luac:
        cmd = [luac, "-p", "-"]
        tool = "luac"
    elif luajit := shutil.which("luajit"):
        cmd = [
            luajit, "-e",
            "local s=io.read('*a'); local f,e=loadstring(s,'monitors.lua'); "
            "if not f then io.stderr:write(tostring(e)..'\\n'); os.exit(1) end",
        ]
        tool = "luajit"
    elif lua := shutil.which("lua"):
        cmd = [
            lua, "-e",
            "local s=io.read('*a'); local f,e=load(s,'monitors.lua','t'); "
            "if not f then io.stderr:write(tostring(e)..'\\n'); os.exit(1) end",
        ]
        tool = "lua"
    else:
        log_debug("No native luac/luajit/lua binary in PATH; Stage 1 pure-Python validation passed")
        return

    try:
        proc = subprocess.run(
            cmd,
            input=candidate,
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log_warn(f"Native syntax check ({tool}) could not execute: {exc}")
        return

    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
        raise HyprError(f"Pre-write Lua syntax validation failed ({tool}): {detail}")


def validate_lua_output(
    candidate: str,
    mon: dict[str, Any],
    expected_literal: str,
    expected_block_count: int,
) -> None:
    """Two-stage pre-write gate guaranteeing valid, uncorrupted Lua before disk write."""
    # Stage 1: Always-on pure-Python full-file lex, balance, and round-trip AST check
    tokens = tokenize_lua(candidate)
    _verify_balanced_delimiters(tokens)
    all_blocks = find_monitor_blocks(candidate, tokens)
    if len(all_blocks) != expected_block_count:
        raise HyprError(
            f"Pre-write validation failed: expected {expected_block_count} hl.monitor blocks, "
            f"parsed {len(all_blocks)}."
        )

    matching = [b for b in all_blocks if block_targets(candidate, b, mon)]
    if not matching:
        raise HyprError("Pre-write validation failed: target monitor block missing after edit.")

    found_expected_scale = False
    for block in matching:
        for idx in range(len(block.entries) - 1):
            if block.entries[idx].sep_pos is None:
                raise HyprError(
                    f"Pre-write validation failed: missing separator after entry "
                    f"'{block.entries[idx].key_raw or idx}' in hl.monitor block."
                )
        scale_entries = block.get_all("scale")
        if len(scale_entries) > 1:
            raise HyprError("Pre-write validation failed: duplicate 'scale' keys remain in block.")
        if len(scale_entries) == 1:
            raw_val = candidate[scale_entries[0].value_start:scale_entries[0].value_end].strip()
            if raw_val == expected_literal:
                found_expected_scale = True

    if not found_expected_scale:
        raise HyprError(
            f"Pre-write validation failed: expected scale '{expected_literal}' not recovered in round-trip parse."
        )

    # Stage 2: Native Lua compile-only parser check (luac / luajit / lua)
    _verify_native_lua_syntax(candidate)


def read_config(path: Path = CONFIG_FILE) -> str:
    real = path.resolve(strict=False)
    return real.read_text(encoding="utf-8") if real.exists() else ""


def persist_scale_atomic(
    path: Path,
    mon: dict[str, Any],
    literal: str,
    mode_str: str,
    pos_str: str,
) -> None:
    """Re-reads, updates, validates, and atomically replaces monitors.lua under CONFIG_LOCK."""
    CONFIG_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_LOCK, "a+b") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            real = path.resolve(strict=False)
            real.parent.mkdir(parents=True, exist_ok=True)

            fresh = real.read_text(encoding="utf-8") if real.exists() else ""
            orig_blocks = find_monitor_blocks(fresh)
            updated, _, appended = update_config_text(fresh, mon, literal, mode_str, pos_str)
            expected_count = len(orig_blocks) + (1 if appended else 0)

            validate_lua_output(updated, mon, literal, expected_count)

            mode = real.stat().st_mode if real.exists() else 0o644
            fd, tmp = tempfile.mkstemp(dir=real.parent, prefix=".monitors.lua.")
            tmp_path = Path(tmp)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(updated)
                    f.flush()
                    os.fsync(f.fileno())
                    os.fchmod(f.fileno(), mode & 0o7777)
                os.replace(tmp_path, real)
                dir_fd = os.open(real.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError as exc:
                tmp_path.unlink(missing_ok=True)
                raise HyprError(f"Atomic write to {real} failed: {exc}") from exc
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)


def show_diff(old: str, new: str) -> None:
    diff = list(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{CONFIG_FILE.name}",
            tofile=f"b/{CONFIG_FILE.name}",
        )
    )
    if not diff:
        print("  (no config changes)")
        return
    for line in diff:
        code = (
            "32" if line.startswith("+")
            else "31" if line.startswith("-")
            else "36" if line.startswith("@")
            else ""
        )
        print(_c(code, line.rstrip("\n")))


# ---------------------------------------------------------------------------
# 7. CLI Argument Parser & Main Runner
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="adjust_scale.py",
        description="Step or set the Hyprland fractional scale of a monitor (Lua config).",
        epilog="A bare '-' is parsed as stepping down, not an option flag.",
    )
    p.add_argument(
        "direction",
        nargs="?",
        choices=["+", "-"],
        help="step scale up (+) or down (-)",
    )
    p.add_argument(
        "--set",
        dest="explicit_scale",
        metavar="SCALE",
        help="set an explicit scale value (e.g. 1.25, 1.5)",
    )
    p.add_argument(
        "--show",
        action="store_true",
        help="print resolved monitor state as JSON and exit",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="print valid scale ladder for the monitor and exit",
    )
    p.add_argument(
        "-m",
        "--monitor",
        default=os.environ.get("HYPR_SCALE_MONITOR") or None,
        help="target monitor output name or desc: prefix (default: focused monitor)",
    )
    p.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="preview eval payload and config diff; modify nothing",
    )
    p.add_argument(
        "--no-write",
        action="store_true",
        help="apply over IPC only; do not modify monitors.lua",
    )
    p.add_argument(
        "--reload",
        action="store_true",
        help="additionally run `hyprctl reload` after writing config",
    )
    p.add_argument(
        "--unchecked",
        action="store_true",
        default=os.environ.get("HYPR_SCALE_UNCHECKED") == "1",
        help="bypass clean divisor checks (for debug.disable_scale_checks)",
    )
    return p


def run() -> int:
    parser = build_parser()
    args = parser.parse_args()

    actions_given = sum((
        args.direction is not None,
        args.explicit_scale is not None,
        bool(args.show),
        bool(args.list),
    ))
    if actions_given == 0:
        parser.error("choose a direction (+ or -), --set SCALE, --list, or --show")
    if actions_given > 1:
        parser.error("choose exactly one of: direction (+/-), --set, --list, --show")

    if not (args.show or args.list or args.dry_run):
        if not acquire_debounce_lock(LOCK_FILE, min_interval=0.15):
            return 0

    guard_environment()

    mon = pick_monitor(list_monitors(), args.monitor)
    name = str(mon.get("name") or "")
    if not name:
        raise HyprError("Selected monitor has an empty output name.")

    w = _safe_int(mon.get("width"))
    h = _safe_int(mon.get("height"))
    if w <= 0 or h <= 0:
        raise HyprError(f"{name}: compositor reported invalid dimensions ({w}x{h}).")

    transform = _safe_int(mon.get("transform")) & 7
    x = _safe_int(mon.get("x"))
    y = _safe_int(mon.get("y"))
    pos_str = f"{x}x{y}"
    mode_str = resolve_mode_string(mon)
    ipc_scale = _safe_float(mon.get("scale"), 1.0)

    original = read_config()
    all_blocks = find_monitor_blocks(original)
    matching_blocks = [b for b in all_blocks if block_targets(original, b, mon)]

    cur_n = snap_scale(ipc_scale, w, h, scale_hint(original, matching_blocks))
    ladder = scale_ladder(w, h, unchecked=args.unchecked)

    if args.show:
        lw, lh = logical_size(w, h, cur_n, transform)
        print(
            json.dumps(
                {
                    "name": name,
                    "description": mon.get("description", ""),
                    "physical": f"{w}x{h}",
                    "mode": mode_str,
                    "position": pos_str,
                    "scale": cur_n / SCALE_Q,
                    "scale_formatted": fmt_scale(cur_n),
                    "transform": transform,
                    "logical": f"{lw}x{lh}",
                    "config": str(CONFIG_FILE),
                },
                indent=2,
            )
        )
        return 0

    if args.list:
        cur_s = fmt_scale(cur_n)
        print(f"{name}  {w}x{h}  transform {transform}  current scale: {cur_s}")
        for n in ladder:
            lw, lh = logical_size(w, h, n, transform)
            s_str = fmt_scale(n)
            bullet = "●" if n == cur_n else "○"
            print(f"  {bullet} {s_str:>8}  →  {lw}x{lh} logical")
        return 0

    if args.explicit_scale is not None:
        target_n = parse_scale_value(args.explicit_scale, w, h, unchecked=args.unchecked)
    else:
        computed = next_scale(cur_n, args.direction, ladder)
        if computed is None:
            log_warn(f"{name}: scale limit reached at {fmt_scale(cur_n)}")
            edge = "at max" if args.direction == "+" else "at min"
            notify(f"{name} • {fmt_scale(cur_n)}× • {edge}", urgency="normal")
            return 0
        target_n = computed

    if target_n == cur_n:
        log_info(f"{name}: already at scale {fmt_scale(cur_n)}")
        notify(f"{name} • {fmt_scale(cur_n)}× • Already set")
        return 0

    literal = fmt_scale(target_n)
    target_float = target_n / SCALE_Q

    # 1) Build live pure-Lua eval payload preserving all active fields verbatim
    overrides = {"scale": literal, "transform": str(transform)}
    eval_chunk = block_to_eval(
        original,
        matching_blocks,
        name,
        overrides,
        default_mode=mode_str,
        default_pos=pos_str,
    )

    log_info(f"{name}: scale {fmt_scale(cur_n)} → {literal}")

    if args.dry_run:
        preview, _, appended = update_config_text(original, mon, literal, mode_str, pos_str)
        validate_lua_output(preview, mon, literal, len(all_blocks) + (1 if appended else 0))
        print(f"-- Eval payload:\n{eval_chunk}\n")
        print(f"-- File diff for {CONFIG_FILE}:")
        show_diff(original, preview)
        return 0

    # 2) Wake monitor if sleeping
    if mon.get("dpmsStatus") is False:
        wake_monitor_dpms(name)

    # 3) Apply live over pure-Lua IPC
    already_within_tol = abs(ipc_scale - target_float) <= IPC_SCALE_TOL
    ok, reply = ipc_eval(eval_chunk)
    if not ok:
        if already_within_tol:
            raise HyprError(
                f"{name}: {reply}; cannot verify scale {literal} against matching 2-decimal IPC float. "
                f"{CONFIG_FILE.name} left untouched."
            )
        log_warn(f"Live IPC eval warning: {reply}; verifying live compositor state...")

    # 4) Verify against live compositor state
    def is_applied(m: dict[str, Any]) -> bool:
        return abs(_safe_float(m.get("scale"), 0.0) - target_float) <= IPC_SCALE_TOL

    verified = wait_for_monitor(name, is_applied)
    if verified is None or not is_applied(verified):
        actual_val = f"{_safe_float(verified.get('scale'), 0.0):.2f}" if verified else "none"
        raise HyprError(
            f"{name}: Compositor failed to apply scale {literal} (reported {actual_val}). "
            f"{CONFIG_FILE.name} left untouched."
        )

    update_debounce_timestamp()

    # 5) Re-read, validate, and persist atomically under CONFIG_LOCK
    if not args.no_write:
        persist_scale_atomic(CONFIG_FILE, mon, literal, mode_str, pos_str)

    if args.reload:
        try:
            reload_proc = subprocess.run(
                ["hyprctl", "reload"],
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )
            reload_reply = (reload_proc.stdout or "").strip()
            if reload_proc.returncode != 0 or (reload_reply and reload_reply.lower() != "ok"):
                detail = reload_reply or reload_proc.stderr.strip() or f"exit {reload_proc.returncode}"
                log_warn(f"hyprctl reload warning: {detail}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            log_warn(f"hyprctl reload failed: {exc}")

    lw, lh = logical_size(w, h, target_n, transform)
    log_ok(f"{name}: scale {literal} applied → {lw}x{lh} logical px")
    notify(f"{name} • {literal}× • {lw}×{lh}")
    return 0


def main() -> None:
    try:
        sys.exit(run())
    except HyprError as exc:
        log_err(str(exc))
        notify("Scale error", str(exc), urgency="critical", icon="dialog-error", ms=5000)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
