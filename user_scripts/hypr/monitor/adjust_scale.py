#!/usr/bin/env python3
# =============================================================================
# Dusky Monitor Scale Adjuster — Production Bleeding-Edge Edition
# Target: Arch Linux (rolling) | Python 3.14+ | Hyprland 0.56+ (Lua config)
# Standard: wp_fractional_scale_v1 (1/120 quantum) | XDG Base Directory Spec
#
# Design Principles:
#   • Mathematical perfection: calculates scale ladder via gcd(120*W, 120*H)
#     ensuring integer logical pixel dimensions accepted by Hyprland without
#     clamp errors or fallbacks.
#   • Full field preservation: reads existing hl.monitor block for the target
#     output in monitors.lua; preserves bitdepth, vrr, cm, sdr settings, and
#     reserved areas verbatim over IPC.
#   • Field ownership: persists ONLY the 'scale' field to monitors.lua.
#     Never writes 'transform', so ephemeral rotations made by screen_rotate.py
#     are never converted to permanent config.
#   • Eval-Verify-Persist: applies live over IPC first, polls live compositor
#     state to confirm, and persists to disk ONLY after verification succeeds.
#   • No clobber reload: does not call `hyprctl reload` (which clobbers rotations).
#     Direct eval applies instantly with 0ms flicker.
#   • Rich CLI: supports +, -, --set SCALE, --show, --list, --dry-run, --no-write, --reload.
#   • Zero hardcoded usernames: strict XDG Base Directory ($XDG_CONFIG_HOME,
#     $XDG_RUNTIME_DIR) with fallback to Path.home().
# =============================================================================

import argparse
import difflib
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, NoReturn

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

SCALE_Q: Final = 120  # wp_fractional_scale_v1 granularity denominator
SCALE_MIN_N: Final = 60    # 0.50x
SCALE_MAX_N: Final = 480   # 4.00x

MIN_LOGICAL_LONG: Final = 640
MIN_LOGICAL_SHORT: Final = 360

POLL_INTERVAL: Final = 0.1
POLL_TIMEOUT: Final = 2.5

DEBUG: Final = os.environ.get("DEBUG") == "1"

CONFIG_HEADER: Final = (
    "-- ==============================================================================\n"
    "-- USER CONFIGURATION: monitors.lua\n"
    "-- ==============================================================================\n\n"
)

# ---------------------------------------------------------------------------
# 1. Logging & Desktop Notifications
# ---------------------------------------------------------------------------
def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if sys.stderr.isatty() else text

def log_info(msg: str) -> None:  print(_c("34", "[INFO] ") + f" {msg}", file=sys.stderr)
def log_ok(msg: str) -> None:    print(_c("32", "[OK]   ") + f" {msg}", file=sys.stderr)
def log_warn(msg: str) -> None:  print(_c("33", "[WARN] ") + f" {msg}", file=sys.stderr)
def log_err(msg: str) -> None:   print(_c("31", "[ERROR]") + f" {msg}", file=sys.stderr)
def log_debug(msg: str) -> None:
    if DEBUG:
        print(_c("35", "[DEBUG]") + f" {msg}", file=sys.stderr)

def _short(text: str, limit: int) -> str:
    """Collapse whitespace and truncate with ellipsis so text fits the center pill."""
    flat = " ".join(str(text).split())
    if len(flat) <= limit:
        return flat
    return flat[: max(0, limit - 1)].rstrip() + "…"

def notify(title: str, body: str = "", urgency: str = "low", icon: str = "video-display", ms: int = 1800) -> None:
    if shutil.which("notify-send") is None:
        return
    # Normal pill is single-line (format shows summary only); critical keeps
    # two lines. Caps sized for the 300px pill so mako never ellipsizes.
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

# ---------------------------------------------------------------------------
# 2. Environment & Locking
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

    If an operation is currently active, or if less than min_interval seconds
    have elapsed since the last operation, drops the invocation immediately
    (zero waiting, zero queueing). This prevents runaway cascades when a keybind
    is held down under Hyprland key-repeat.
    """
    global _LOCK_HANDLE
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        handle = open(fd, "r+b", buffering=0)
    except OSError as exc:
        log_warn(f"Failed to open lockfile {lock_path}: {exc}")
        return False

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        handle.close()
        return False

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
    global _LOCK_HANDLE
    if _LOCK_HANDLE is not None:
        try:
            _LOCK_HANDLE.seek(0)
            _LOCK_HANDLE.truncate(0)
            _LOCK_HANDLE.write(f"{time.monotonic():.6f}\n".encode("ascii"))
            _LOCK_HANDLE.flush()
        except OSError:
            pass

# ---------------------------------------------------------------------------
# 3. IPC Helpers (hyprctl eval + keyword fallback)
# ---------------------------------------------------------------------------
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
        raise HyprError(f"hyprctl -j {subcommand} exited {proc.returncode}: {proc.stderr.strip()}")

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

def ipc_eval(lua_chunk: str, spec_str: str | None = None) -> tuple[bool, str]:
    """Tries `hyprctl eval` first, then falls back to `hyprctl keyword monitor`."""
    log_debug(f"IPC eval: {lua_chunk}")
    try:
        proc = subprocess.run(
            ["hyprctl", "eval", lua_chunk],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        reply = (proc.stdout or "").strip()
        if proc.returncode == 0 and reply.lower() == "ok":
            return True, reply
        log_warn(f"hyprctl eval reply: {reply!r} (exit {proc.returncode}); attempting keyword fallback")
    except (OSError, subprocess.TimeoutExpired) as exc:
        log_warn(f"hyprctl eval failed: {exc}; attempting keyword fallback")

    if spec_str:
        log_debug(f"IPC keyword: monitor {spec_str}")
        try:
            proc = subprocess.run(
                ["hyprctl", "keyword", "monitor", spec_str],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            reply = (proc.stdout or "").strip()
            if proc.returncode == 0 and reply.lower() == "ok":
                return True, reply
            return False, f"keyword fallback failed: {reply or proc.stderr.strip()}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"keyword fallback exception: {exc}"

    return False, "eval failed"

def list_monitors(soft: bool = False) -> list[dict[str, Any]]:
    payload = ipc_json("monitors", soft=soft)
    if not isinstance(payload, list) or not payload:
        if soft:
            return []
        raise HyprError("hyprctl returned no active monitors.")
    real = [m for m in payload if str(m.get("name", "")).strip().upper() != "FALLBACK"]
    if not real:
        if soft:
            return []
        raise HyprError("hyprctl returned no active physical monitors (compositor in synthetic fallback state).")
    return real

def pick_monitor(monitors: list[dict[str, Any]], target: str | None) -> dict[str, Any]:
    if not monitors:
        raise HyprError("No monitors found.")
    if target:
        found = next((m for m in monitors if m.get("name") == target), None)
        if found is None:
            clean = target[5:].strip() if target.startswith("desc:") else target
            found = next((m for m in monitors if clean.lower() in str(m.get("description", "")).lower()), None)
        if found is None:
            available = ", ".join(f"{m.get('name')} ({m.get('description', '')})" for m in monitors)
            raise HyprError(f"Monitor '{target}' not found. Available: {available}")
        return found
    return next((m for m in monitors if m.get("focused") is True), monitors[0])

def wait_for_monitor(name: str, predicate: Any, timeout: float = POLL_TIMEOUT) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        mons = list_monitors(soft=True)
        for m in mons:
            if m.get("name") == name:
                last = m
                if predicate(m):
                    return m
                break
        time.sleep(POLL_INTERVAL)
    return last

# ---------------------------------------------------------------------------
# 4. Wayland Fractional Scaling Mathematics
# ---------------------------------------------------------------------------
def clean_scale(n: int, w: int, h: int) -> bool:
    return n > 0 and (SCALE_Q * w) % n == 0 and (SCALE_Q * h) % n == 0

def scale_ladder(w: int, h: int, *, unchecked: bool = False) -> list[int]:
    """Generates exact ascending n (scale = n/120) accepted by Hyprland."""
    if unchecked:
        ns = sorted({round(s * SCALE_Q) for s in (
            0.5, 0.6, 0.75, 0.8, 0.9, 1.0, 1.1, 1.125, 1.15, 1.2, 1.25, 4 / 3,
            1.4, 1.5, 1.6, 5 / 3, 1.75, 1.8, 1.875, 2.0, 2.25, 2.4, 2.5, 8 / 3,
            2.8, 3.0, 3.5, 4.0
        )})
    else:
        g = math.gcd(SCALE_Q * w, SCALE_Q * h)
        ns = [n for n in range(SCALE_MIN_N, SCALE_MAX_N + 1) if g % n == 0]

    long_, short = max(w, h), min(w, h)
    return [
        n for n in ns
        if SCALE_MIN_N <= n <= SCALE_MAX_N
        and long_ * SCALE_Q >= MIN_LOGICAL_LONG * n
        and short * SCALE_Q >= MIN_LOGICAL_SHORT * n
    ]

def snap_scale(ipc_scale: float, w: int, h: int, hint: float | None = None) -> int:
    """Recovers the exact n/120 quantum from IPC 2-decimal representation."""
    if hint is not None and hint > 0 and abs(hint - ipc_scale) <= 0.0051:
        return max(1, round(hint * SCALE_Q))
    centre = ipc_scale * SCALE_Q
    for n in sorted(range(math.floor(centre - 0.61), math.ceil(centre + 0.61) + 1),
                    key=lambda val: (abs(val - centre), val)):
        if clean_scale(n, w, h):
            return n
    return max(1, round(centre))

def fmt_scale(n: int) -> str:
    val = n / SCALE_Q
    s = f"{val:.6f}".rstrip("0").rstrip(".")
    return s or "1"

def parse_scale_value(val_str: str, w: int, h: int, unchecked: bool = False) -> int:
    try:
        s = float(val_str)
    except ValueError:
        raise HyprError(f"Invalid scale value '{val_str}': must be a number.") from None
    if not (SCALE_MIN_N / SCALE_Q <= s <= SCALE_MAX_N / SCALE_Q):
        raise HyprError(f"Scale {s} outside supported range [{SCALE_MIN_N/SCALE_Q}, {SCALE_MAX_N/SCALE_Q}].")
    n = round(s * SCALE_Q)
    if not unchecked and not clean_scale(n, w, h):
        ladder = scale_ladder(w, h)
        nearest_n = min(ladder, key=lambda cand: abs(cand - n))
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
# 5. Lua Configuration Tokenizer & Rewriter
# ---------------------------------------------------------------------------
def lua_str(s: str) -> str:
    return json.dumps(str(s))

def lua_string_value(raw: str) -> str | None:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] in "\"'" and raw[-1] == raw[0]:
        return raw[1:-1].replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
    return None

def parse_lua_number(raw: str) -> float | None:
    try:
        return float(raw.strip())
    except ValueError:
        return None

@dataclass(slots=True)
class LuaField:
    key: str
    key_start: int
    value_start: int
    value_end: int
    trailing_sep: bool

@dataclass(slots=True)
class MonitorBlock:
    start: int
    open_brace: int
    close_brace: int
    end: int
    fields: list[LuaField] = field(default_factory=list)

    def get(self, key: str) -> LuaField | None:
        return next((f for f in self.fields if f.key == key), None)

def _skip_string(t: str, i: int) -> int:
    q, n, i = t[i], len(t), i + 1
    while i < n:
        c = t[i]
        if c == "\\":
            i += 2
        elif c == q or c == "\n":
            return i + 1
        else:
            i += 1
    return n

def _skip_comment(t: str, i: int) -> int:
    if not t.startswith("--", i):
        return i
    if t.startswith("--[[", i):
        close = t.find("]]", i + 4)
        return len(t) if close < 0 else close + 2
    eol = t.find("\n", i + 2)
    return len(t) if eol < 0 else eol + 1

def find_monitor_blocks(t: str) -> list[MonitorBlock]:
    blocks: list[MonitorBlock] = []
    i, n = 0, len(t)
    while i < n:
        if t.startswith("--", i):
            i = _skip_comment(t, i)
            continue
        if t[i] in "\"'":
            i = _skip_string(t, i)
            continue
        if t.startswith("hl.monitor", i):
            start = i
            i += len("hl.monitor")
            while i < n and t[i] in " \t\r\n":
                i += 1
            if i >= n or t[i] != "(":
                continue
            i += 1
            while i < n and t[i] in " \t\r\n":
                i += 1
            if i >= n or t[i] != "{":
                continue
            open_brace = i
            depth, in_field = 1, False
            cur_key = ""
            k_start = v_start = -1
            fields: list[LuaField] = []
            close_brace = -1
            i += 1
            while i < n and depth > 0:
                if t.startswith("--", i):
                    i = _skip_comment(t, i)
                    continue
                if t[i] in "\"'":
                    i = _skip_string(t, i)
                    continue
                c = t[i]
                if depth == 1:
                    if not in_field and (c.isalpha() or c == "_"):
                        m = re.match(r"[A-Za-z_]\w*", t[i:])
                        if m:
                            cur_key = m.group(0)
                            k_start = i
                            i += len(cur_key)
                            while i < n and t[i] in " \t\r\n":
                                i += 1
                            if i < n and t[i] == "=":
                                in_field = True
                                i += 1
                                while i < n and t[i] in " \t\r\n":
                                    i += 1
                                v_start = i
                            continue
                    elif in_field and c in ",}":
                        v_end = i
                        while v_end > v_start and t[v_end - 1] in " \t\r\n":
                            v_end -= 1
                        fields.append(LuaField(cur_key, k_start, v_start, v_end, c == ","))
                        in_field = False
                        cur_key = ""
                        k_start = v_start = -1
                        if c == "}":
                            depth -= 1
                            close_brace = i
                            i += 1
                            break
                        i += 1
                        continue
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        close_brace = i
                        i += 1
                        break
                i += 1
            while i < n and t[i] in " \t\r\n":
                i += 1
            if i < n and t[i] == ")":
                i += 1
                if close_brace >= 0:
                    blocks.append(MonitorBlock(start, open_brace, close_brace, i, fields))
            continue
        i += 1
    return blocks

def short_description(desc: str) -> str:
    m = re.match(r"^[A-Za-z0-9]+(?:\s+[A-Za-z0-9]+)*\s+([A-Za-z0-9_.-]+)", desc)
    return m.group(1) if m else desc

def block_targets(t: str, b: MonitorBlock, mon: dict[str, Any]) -> bool:
    f = b.get("output")
    if not f:
        return False
    val = lua_string_value(t[f.value_start:f.value_end])
    if val is None:
        return False
    name = str(mon.get("name", ""))
    desc = str(mon.get("description", ""))
    if val == name:
        return True
    if val.startswith("desc:"):
        prefix = val[5:].strip()
        if prefix and (desc.startswith(prefix) or short_description(desc).startswith(prefix)):
            return True
    return False

def scale_hint(t: str, b: MonitorBlock | None) -> float | None:
    if b is None:
        return None
    f = b.get("scale")
    return parse_lua_number(t[f.value_start:f.value_end]) if f else None

def set_field(t: str, b: MonitorBlock, key: str, literal: str) -> tuple[str, int]:
    f = b.get(key)
    if f is not None:
        return t[:f.value_start] + literal + t[f.value_end:], len(literal) - (f.value_end - f.value_start)
    delta = 0
    if b.fields and not b.fields[-1].trailing_sep:
        last_val_end = b.fields[-1].value_end
        t = t[:last_val_end] + "," + t[last_val_end:]
        delta += 1
        insert_at = b.close_brace + 1
    else:
        insert_at = b.close_brace
    insertion = f"    {key:<10}= {literal},\n"
    return t[:insert_at] + insertion + t[insert_at:], len(insertion) + delta

def update_matching_blocks(t: str, mon: dict[str, Any], key: str, literal: str) -> tuple[str, int]:
    blocks = [b for b in find_monitor_blocks(t) if block_targets(t, b, mon)]
    if not blocks:
        return t, 0
    delta = 0
    for b in blocks:
        b_adj = MonitorBlock(
            b.start + delta, b.open_brace + delta, b.close_brace + delta, b.end + delta,
            [LuaField(f.key, f.key_start + delta, f.value_start + delta, f.value_end + delta, f.trailing_sep) for f in b.fields]
        )
        t, d = set_field(t, b_adj, key, literal)
        delta += d
    return t, len(blocks)

def block_to_eval(t: str, b: MonitorBlock | None, output_name: str, overrides: dict[str, str], default_mode: str = "", default_pos: str = "") -> str:
    """Builds a hl.monitor() Lua call preserving every custom field from the block."""
    parts: list[str] = []
    seen: set[str] = set()
    if b is not None:
        for f in b.fields:
            if not f.key or f.key in seen:
                continue
            parts.append(f"{f.key} = {overrides.get(f.key, t[f.value_start:f.value_end])}")
            seen.add(f.key)
    if "output" not in seen:
        parts.insert(0, f"output = {lua_str(output_name)}")
        seen.add("output")
    for k, v in overrides.items():
        if k not in seen:
            parts.append(f"{k} = {v}")
            seen.add(k)
    if "mode" not in seen and default_mode:
        parts.append(f"mode = {lua_str(default_mode)}")
        seen.add("mode")
    if "position" not in seen and default_pos:
        parts.append(f"position = {lua_str(default_pos)}")
        seen.add("position")
    return "hl.monitor({ " + ", ".join(parts) + " })"

def append_block(t: str, fields: dict[str, str]) -> str:
    body = "\n".join(f"    {k:<10}= {v}," for k, v in fields.items())
    block = f"hl.monitor({{\n{body}\n}})\n"
    if t and not t.endswith("\n"):
        t += "\n"
    return t + ("\n" if t.strip() else "") + block

def resolve_mode_string(mon: dict[str, Any]) -> str:
    w = int(mon.get("width", 0))
    h = int(mon.get("height", 0))
    refresh = float(mon.get("refreshRate", 60.0))
    prefix = f"{w}x{h}@"
    best: tuple[float, str] | None = None
    for entry in mon.get("availableModes") or []:
        m = re.match(r"^(\d+)x(\d+)@([0-9.]+)(?:Hz)?$", str(entry), re.I)
        if not m or int(m.group(1)) != w or int(m.group(2)) != h:
            continue
        cand = (abs(float(m.group(3)) - refresh), f"{w}x{h}@{m.group(3)}")
        if best is None or cand < best:
            best = cand
    if best is None:
        return f"{w}x{h}@{refresh:.2f}"
    return best[1]

def read_config() -> str:
    return CONFIG_FILE.read_text(encoding="utf-8") if CONFIG_FILE.exists() else ""

def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    real = path.resolve() if path.exists() else path
    CONFIG_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_LOCK, "a+b") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            fd, tmp = tempfile.mkstemp(dir=real.parent, prefix=".monitors.lua.")
            tmp_path = Path(tmp)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                mode = real.stat().st_mode if real.exists() else 0o644
                os.chmod(tmp_path, mode & 0o7777)
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
    diff = list(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{CONFIG_FILE.name}", tofile=f"b/{CONFIG_FILE.name}"
    ))
    if not diff:
        print("  (no config changes)")
        return
    for line in diff:
        code = "32" if line.startswith("+") else "31" if line.startswith("-") else "36" if line.startswith("@") else ""
        print(_c(code, line.rstrip("\n")))

# ---------------------------------------------------------------------------
# 6. CLI Argument Parser & Runner
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="adjust_scale.py",
        description="Step or set the Hyprland fractional scale of a monitor (Lua config).",
        epilog="A bare '-' is parsed as stepping down, not an option flag.",
    )
    p.add_argument("direction", nargs="?", choices=["+", "-"],
                   help="step scale up (+) or down (-)")
    p.add_argument("--set", dest="explicit_scale", metavar="SCALE",
                   help="set an explicit scale value (e.g. 1.25, 1.5)")
    p.add_argument("--show", action="store_true",
                   help="print resolved monitor state as JSON and exit")
    p.add_argument("--list", action="store_true",
                   help="print valid scale ladder for the monitor and exit")
    p.add_argument("-m", "--monitor", default=os.environ.get("HYPR_SCALE_MONITOR") or None,
                   help="target monitor output name (default: focused monitor)")
    p.add_argument("-n", "--dry-run", action="store_true",
                   help="preview eval payload and config diff; modify nothing")
    p.add_argument("--no-write", action="store_true",
                   help="apply over IPC only; do not modify monitors.lua")
    p.add_argument("--reload", action="store_true",
                   help="additionally run `hyprctl reload` after writing config")
    p.add_argument("--unchecked", action="store_true",
                   default=os.environ.get("HYPR_SCALE_UNCHECKED") == "1",
                   help="bypass clean divisor checks (for debug.disable_scale_checks)")
    return p

def run() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Explicit validation to avoid argparse mutual exclusion bugs with positionals
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
    name = str(mon["name"])
    w, h = int(mon["width"]), int(mon["height"])
    transform = int(mon.get("transform", 0)) & 7
    x, y = int(mon.get("x", 0)), int(mon.get("y", 0))
    mode_str = resolve_mode_string(mon)

    original = read_config()
    blocks = [b for b in find_monitor_blocks(original) if block_targets(original, b, mon)]
    base = blocks[-1] if blocks else None

    cur_n = snap_scale(float(mon.get("scale", 1.0)), w, h, scale_hint(original, base))
    ladder = scale_ladder(w, h, unchecked=args.unchecked)

    if args.show:
        lw, lh = logical_size(w, h, cur_n, transform)
        print(json.dumps({
            "name": name,
            "description": mon.get("description", ""),
            "physical": f"{w}x{h}",
            "mode": mode_str,
            "position": f"{x}x{y}",
            "scale": cur_n / SCALE_Q,
            "scale_formatted": fmt_scale(cur_n),
            "transform": transform,
            "logical": f"{lw}x{lh}",
            "config": str(CONFIG_FILE),
        }, indent=2))
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
            edge = "at max" if args.direction == "+" else "at min" if args.direction == "-" else "limit"
            notify(f"{name} • {fmt_scale(cur_n)}× • {edge}", urgency="normal")
            return 0
        target_n = computed

    if target_n == cur_n:
        log_info(f"{name}: already at scale {fmt_scale(cur_n)}")
        notify(f"{name} • {fmt_scale(cur_n)}× • Already set")
        return 0

    literal = fmt_scale(target_n)
    target_float = target_n / SCALE_Q

    # 1) Build live eval payload preserving all fields from monitors.lua
    overrides = {"scale": literal, "transform": str(transform)}
    if base is None:
        overrides |= {"mode": lua_str(mode_str), "position": lua_str(f"{x}x{y}")}
    eval_chunk = block_to_eval(original, base, name, overrides, default_mode=mode_str, default_pos=f"{x}x{y}")
    spec_str = f"{name},{mode_str},{x}x{y},{literal},transform,{transform}"

    # 2) Prepare updated configuration text
    text = original if original.strip() else CONFIG_HEADER
    text, count = update_matching_blocks(text, mon, "scale", literal)
    if count == 0:
        text = append_block(text, {
            "output": lua_str(name),
            "mode": lua_str(mode_str),
            "position": lua_str(f"{x}x{y}"),
            "scale": literal,
        })

    log_info(f"{name}: scale {fmt_scale(cur_n)} → {literal}")

    if args.dry_run:
        print(f"-- Eval payload:\n{eval_chunk}\n")
        print(f"-- File diff for {CONFIG_FILE}:")
        show_diff(original, text)
        return 0

    # Wake monitor if sleeping (borrowed from hyprmoncfg enable_wake)
    if not mon.get("dpmsStatus", True):
        log_info(f"{name}: waking monitor from DPMS sleep")
        ipc_eval(f"hl.dsp.dpms({{ action = \"enable\", monitor = {lua_str(name)} }})")

    # 3) Apply live over IPC
    ok, reply = ipc_eval(eval_chunk, spec_str)
    if not ok:
        log_warn(f"Live IPC eval warning: {reply}; checking live state...")

    # 4) Verify against live compositor state
    def is_applied(m: dict[str, Any]) -> bool:
        return abs(float(m.get("scale", 0.0)) - target_float) <= 0.0051

    verified = wait_for_monitor(name, is_applied)
    if verified is None or not is_applied(verified):
        actual_val = f"{float(verified['scale']):.2f}" if verified else "none"
        raise HyprError(
            f"{name}: Compositor failed to apply scale {literal} (reported {actual_val}). "
            f"{CONFIG_FILE.name} left untouched."
        )

    update_debounce_timestamp()

    # 5) Persist to disk only after verification succeeds
    if not args.no_write:
        atomic_write(CONFIG_FILE, text)

    if args.reload:
        subprocess.run(["hyprctl", "reload"], capture_output=True, timeout=10, check=False)

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
