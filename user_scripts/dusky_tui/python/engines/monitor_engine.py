#!/usr/bin/env python3
"""
===============================================================================
DUSKY TUI: HYPRLAND MONITOR & WORKSPACE LUA ENGINE
===============================================================================
Production Lua engine for Hyprland git monitor and workspace configuration.
Incorporates exact Wayland wp_fractional_scale_v1 mathematics (1/120 quantum,
6-decimal precision parity with adjust_scale.py), cross-process CONFIG_LOCK
coordination, two-stage pre/post-write Lua syntax verification with atomic
rollback, relative spatial positioning, hardware enrichment, and workspace rules.
"""

from contextlib import contextmanager
import os
import re
import threading
lazy import errno
lazy import fcntl
lazy import json
lazy import math
lazy from math.integer import gcd
lazy import subprocess
lazy import tempfile
lazy from pathlib import Path
lazy from typing import Any

from python.engines.lua import HyprlandLuaEngine
from python.shared.config_io import atomic_write, read_text, boolean
from python.shared.lua_source import (
    apply_edits,
    calls,
    fields,
    find_table_bounds,
    format_lua_key,
    lua_string,
    replace_field,
    replace_nested_field,
    string_value,
    validate_lua_syntax,
)

# =============================================================================
# 1. WAYLAND / HYPRLAND FRACTIONAL SCALING MATHEMATICS (wp_fractional_scale_v1)
# =============================================================================
# In Hyprland, fractional scale factors operate on a 1/120 quantum grid.
# A scale s = n / 120 is sharp iff (120 * W) % n == 0 and (120 * H) % n == 0.

HYPRLAND_SCALE_STEPS: int = 120
MIN_SCALE: float = 0.5
MAX_SCALE: float = 4.0
PRECISION: int = 6
IPC_SCALE_TOL: float = 0.0051

MIN_LOGICAL_LONG: int = 640
MIN_LOGICAL_SHORT: int = 360

_RE_DESC_PORT = re.compile(r"\s+\([^)]*\)\s*$")
_RE_SHORT_DESC = re.compile(r"^[A-Za-z0-9]+(?:\s+[A-Za-z0-9]+)*\s+([A-Za-z0-9_.-]+)")
_RE_MODE_DIM = re.compile(r"([0-9]+)x([0-9]+)(?:@[^\s]+)?")

_FLOCK_TLS = threading.local()


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        f = float(value)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return bool(value)


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_exact_int(
    value: Any,
    field_name: str,
    *,
    lo: int | None = None,
    hi: int | None = None,
    allowed: set[int] | None = None,
) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Invalid integer value for {field_name}: {value!r}")
    if isinstance(value, int):
        iv = value
    else:
        raw = str(value).strip()
        try:
            iv = int(raw)
        except ValueError:
            try:
                fv = float(raw)
            except ValueError as exc:
                raise ValueError(f"Invalid integer value for {field_name}: {value!r}") from exc
            if not math.isfinite(fv) or not fv.is_integer():
                raise ValueError(f"Value for {field_name} must be an integer, got {value!r}")
            iv = int(fv)
    if allowed is not None and iv not in allowed:
        opts = ", ".join(str(x) for x in sorted(allowed))
        raise ValueError(f"Invalid {field_name}={iv}; allowed values: {opts}")
    if lo is not None and iv < lo:
        raise ValueError(f"{field_name}={iv} is below minimum {lo}")
    if hi is not None and iv > hi:
        raise ValueError(f"{field_name}={iv} exceeds maximum {hi}")
    return iv


def _default_config_path() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if configured:
        cand = Path(configured).expanduser()
        base = cand if cand.is_absolute() else (Path.home() / ".config")
    else:
        base = Path.home() / ".config"
    return (base / "hypr" / "edit_here" / "source" / "monitors.lua").resolve(strict=False)


def _config_lock_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime:
        cand = Path(runtime)
        base = cand if cand.is_absolute() else Path(tempfile.gettempdir())
    else:
        base = Path(tempfile.gettempdir())
    return base / f"hypr-monitors-lua-{os.getuid()}.lock"


def sharp_at_numerator(width: int, height: int, numerator: int) -> bool:
    """Tests if scale factor (numerator / 120) yields exact integer logical coordinates."""
    if width <= 0 or height <= 0 or numerator <= 0:
        return False
    return (HYPRLAND_SCALE_STEPS * width) % numerator == 0 and (HYPRLAND_SCALE_STEPS * height) % numerator == 0


def is_sharp(width: int, height: int, scale: float) -> bool:
    """Strictly verifies whether `scale` lies on the 1/120 grid and divides cleanly."""
    if not math.isfinite(scale) or scale <= 0 or width <= 0 or height <= 0:
        return False
    numerator = round(scale * HYPRLAND_SCALE_STEPS)
    if numerator <= 0 or abs((numerator / HYPRLAND_SCALE_STEPS) - scale) > 1e-4:
        return False
    return sharp_at_numerator(width, height, numerator)


def _grid_numerators(
    width: int,
    height: int,
    min_scale: float = MIN_SCALE,
    max_scale: float = MAX_SCALE,
) -> list[int]:
    if width <= 0 or height <= 0:
        return [HYPRLAND_SCALE_STEPS]

    lo_s = max(min_scale, MIN_SCALE)
    hi_s = min(max_scale, MAX_SCALE)
    min_num = max(1, math.ceil(lo_s * HYPRLAND_SCALE_STEPS - 1e-9))
    max_num = math.floor(hi_s * HYPRLAND_SCALE_STEPS + 1e-9)
    if max_num < min_num:
        return [HYPRLAND_SCALE_STEPS]

    g = gcd(width * HYPRLAND_SCALE_STEPS, height * HYPRLAND_SCALE_STEPS)
    long_, short = max(width, height), min(width, height)
    eff_min_long = min(MIN_LOGICAL_LONG, long_)
    eff_min_short = min(MIN_LOGICAL_SHORT, short)

    nums = [
        num for num in range(min_num, max_num + 1)
        if g % num == 0
        and long_ * HYPRLAND_SCALE_STEPS >= eff_min_long * num
        and short * HYPRLAND_SCALE_STEPS >= eff_min_short * num
    ]
    return nums or [HYPRLAND_SCALE_STEPS]


def grid_scales(
    width: int,
    height: int,
    min_scale: float = MIN_SCALE,
    max_scale: float = MAX_SCALE,
) -> list[float]:
    """Generates all sharp 1/120 scales respecting safe logical resolution floors."""
    return [
        round(num / HYPRLAND_SCALE_STEPS, PRECISION)
        for num in _grid_numerators(width, height, min_scale, max_scale)
    ]


def clean_scale(width: int, height: int, requested: float) -> float:
    """Rounds up to the nearest valid sharp scale on the 1/120 grid within safe bounds."""
    if not math.isfinite(requested) or width <= 0 or height <= 0 or requested <= 0:
        return 1.0
    nums = _grid_numerators(width, height, MIN_SCALE, MAX_SCALE)
    req_n = math.ceil(requested * HYPRLAND_SCALE_STEPS - 1e-9)
    for num in nums:
        if num >= req_n:
            return round(num / HYPRLAND_SCALE_STEPS, PRECISION)
    return round(nums[-1] / HYPRLAND_SCALE_STEPS, PRECISION)


def closest_sharp(
    width: int,
    height: int,
    requested: float,
    min_scale: float = MIN_SCALE,
    max_scale: float = MAX_SCALE,
) -> float:
    """Finds the sharp 1/120 scale closest to the requested float."""
    if not math.isfinite(requested) or width <= 0 or height <= 0 or requested <= 0:
        return 1.0
    all_sharp = grid_scales(width, height, min_scale, max_scale)
    if not all_sharp:
        return 1.0
    return min(all_sharp, key=lambda s: (abs(s - requested), s))


def snap_scale(
    ipc_scale: float,
    width: int,
    height: int,
    hint: float | None = None,
) -> float:
    """Recovers exact 1/120 quantum from Hyprland's 2-decimal IPC float using persisted hint."""
    if not math.isfinite(ipc_scale) or ipc_scale <= 0 or width <= 0 or height <= 0:
        return 1.0
    if hint is not None and math.isfinite(hint) and hint > 0 and abs(hint - ipc_scale) <= IPC_SCALE_TOL:
        return round(max(1, round(hint * HYPRLAND_SCALE_STEPS)) / HYPRLAND_SCALE_STEPS, PRECISION)

    centre = ipc_scale * HYPRLAND_SCALE_STEPS
    lo = max(1, math.ceil(centre - 0.61))
    hi = max(lo, math.floor(centre + 0.61))
    candidates = [
        n for n in range(lo, hi + 1)
        if abs((n / HYPRLAND_SCALE_STEPS) - ipc_scale) <= IPC_SCALE_TOL
        and sharp_at_numerator(width, height, n)
    ]
    if candidates:
        candidates.sort(key=lambda val: (abs(val - centre), val))
        return round(candidates[0] / HYPRLAND_SCALE_STEPS, PRECISION)
    return round(max(1, round(centre)) / HYPRLAND_SCALE_STEPS, PRECISION)


def preset_choices(width: int, height: int) -> list[float]:
    """Generates recommended sharp scale presets deduplicated and bounded by logical floors."""
    sharp = grid_scales(width, height, 1.0, MAX_SCALE)
    largest = sharp[-1] if sharp else 1.0
    presets = [1.0, 1.25, 1.5, 1.6, 2.0, 3.0]
    if max(width, height) >= 5120 and sharp and sharp[-1] >= 4.0:
        presets.append(4.0)

    by_key: dict[float, dict[str, Any]] = {}
    for idx, req in enumerate(presets):
        eff = clean_scale(width, height, req)
        if eff > largest:
            continue
        key = round(eff * 100) / 100
        dist = abs(req - eff)
        if key not in by_key or dist < by_key[key]["dist"]:
            by_key[key] = {"val": eff, "idx": idx, "dist": dist}

    kept = sorted(by_key.values(), key=lambda c: c["idx"])
    return [c["val"] for c in kept]


def format_scale(value: float) -> str:
    """Formats scale float cleanly at 6-decimal precision, stripping trailing zeros."""
    if not math.isfinite(value) or value <= 0:
        return "1"
    fmt = f"{value:.{PRECISION}f}".rstrip("0").rstrip(".")
    return fmt if fmt else "1"


def logical_size(width: int, height: int, scale: float, transform: int = 0) -> tuple[int, int]:
    """Computes exact logical bounding box dimensions accounting for scale and rotation."""
    if not math.isfinite(scale) or scale <= 0:
        scale = 1.0
    eff_w = int(round(width / scale))
    eff_h = int(round(height / scale))
    if transform & 1:
        return eff_h, eff_w
    return eff_w, eff_h


# =============================================================================
# 2. HARDWARE ENRICHMENT & RELATIVE SPATIAL POSITIONING
# =============================================================================

def is_internal_connector(name: str) -> bool:
    """Reports whether a connector name belongs to an internal laptop screen (eDP, LVDS, DSI)."""
    low = _safe_str(name).lower().strip()
    return low.startswith(("edp", "lvds", "dsi"))


def normalize_description(desc: str) -> str:
    return _RE_DESC_PORT.sub("", _safe_str(desc)).strip()


def short_description(desc: str) -> str:
    clean = normalize_description(desc)
    m = _RE_SHORT_DESC.match(clean)
    return m.group(1) if m else clean


def normalize_vrr(raw: Any) -> str:
    """Normalizes compositor VRR mode into standard string ('0'=off, '1'=on, '2'=fullscreen-only)."""
    if isinstance(raw, bool):
        return "1" if raw else "0"
    if isinstance(raw, (int, float)) and math.isfinite(float(raw)):
        val = int(raw)
        return str(val) if val in (0, 1, 2) else "0"
    s = _safe_str(raw).strip().lower()
    if s == "true":
        return "1"
    if s in ("false", "off", ""):
        return "0"
    return s if s in ("0", "1", "2") else "0"


def monitor_health(m: dict[str, Any]) -> str:
    """Assesses live monitor hardware health and power state safely against JSON nulls."""
    name = _safe_str(m.get("name")).strip()
    if name.upper() == "FALLBACK":
        return "synthetic"
    if _safe_bool(m.get("disabled"), False):
        return "off"
    if not _safe_bool(m.get("dpmsStatus"), True):
        return "sleeping"
    if _safe_int(m.get("width")) <= 0 or _safe_int(m.get("height")) <= 0:
        return "no_signal"
    return "usable"


def _hyprctl_ok(args: list[str], timeout: float = 2.0) -> bool:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and (proc.stdout or "").strip().lower() == "ok"


def wake_displays(outputs: list[str] | None = None) -> bool:
    """Wakes displays from DPMS sleep via Hyprland Lua `hyprctl eval` with dispatch fallback.

    Preserves `desc:` monitor selectors verbatim and verifies stdout == 'ok'.
    """
    if outputs is None:
        eval_chunk = 'hl.dispatch(hl.dsp.dpms({ action = "enable" }))'
        if _hyprctl_ok(["hyprctl", "eval", eval_chunk]):
            return True
        return _hyprctl_ok(["hyprctl", "dispatch", "dpms", "on"])

    all_ok = True
    any_target = False
    for out in outputs:
        target = _safe_str(out).strip()
        if not target:
            continue
        any_target = True
        eval_chunk = f'hl.dispatch(hl.dsp.dpms({{ action = "enable", monitor = {lua_string(target)} }}))'
        if _hyprctl_ok(["hyprctl", "eval", eval_chunk]):
            continue
        if not _hyprctl_ok(["hyprctl", "dispatch", "dpms", "on", target]):
            all_ok = False
    return all_ok if any_target else True


def calculate_ppi(width: int, height: int, physical_w_mm: int, physical_h_mm: int) -> tuple[float, float]:
    """Calculates display PPI and diagonal size in inches from physical EDID dimensions."""
    if physical_w_mm <= 0 or physical_h_mm <= 0 or width <= 0 or height <= 0:
        return 0.0, 0.0
    w_in = physical_w_mm / 25.4
    h_in = physical_h_mm / 25.4
    diagonal_in = math.sqrt(w_in ** 2 + h_in ** 2)
    diag_pixels = math.sqrt(width ** 2 + height ** 2)
    ppi = diag_pixels / diagonal_in if diagonal_in > 0 else 0.0
    return round(ppi, 1), round(diagonal_in, 1)


def detect_bitdepth(format_str: str) -> int:
    """Detects color bitdepth from Hyprland output pixel format string."""
    fmt = _safe_str(format_str).upper()
    if any(x in fmt for x in ("2101010", "101010", "RGB10", "BGR10")):
        return 10
    if any(x in fmt for x in ("16161616F", "RGBA16F", "RGB16F")):
        return 16
    return 8


def calculate_relative_positions(monitors: list[dict[str, Any]]) -> dict[str, list[tuple[str, str, str]]]:
    """Calculates relative spatial positions exclusively across active connected monitors."""
    positions_map: dict[str, list[tuple[str, str, str]]] = {}
    monitor_bounds: dict[str, tuple[int, int, int, int]] = {}
    active_monitors: list[dict[str, Any]] = []

    for m in monitors:
        if not isinstance(m, dict):
            continue
        name = _safe_str(m.get("name")).strip()
        if not name or monitor_health(m) not in ("usable", "sleeping"):
            continue
        w = _safe_int(m.get("width"))
        h = _safe_int(m.get("height"))
        if w <= 0 or h <= 0:
            continue
        s = _safe_float(m.get("scale"), 1.0)
        if s <= 0:
            s = 1.0
        t = _safe_int(m.get("transform")) & 7
        x = _safe_int(m.get("x"))
        y = _safe_int(m.get("y"))
        lw, lh = logical_size(w, h, s, t)
        monitor_bounds[name] = (x, y, lw, lh)
        active_monitors.append(m)

    for m in active_monitors:
        name = _safe_str(m.get("name")).strip()
        cur_x, cur_y, my_w, my_h = monitor_bounds[name]
        opts: list[tuple[str, str, str]] = [
            (f"{cur_x}x{cur_y}", f"Current ({cur_x}x{cur_y})", "Active canvas coordinates"),
            ("0x0", "Origin (0x0)", "Top-left of virtual desktop space"),
        ]

        for peer_name, (px, py, pw, ph) in monitor_bounds.items():
            if peer_name == name:
                continue

            rx = px + pw
            ry_center = py + round((ph - my_h) / 2)
            opts.append((f"{rx}x{ry_center}", f"Right of {peer_name} (Centered)", f"Offset: {rx}x{ry_center}"))
            if ry_center != py:
                opts.append((f"{rx}x{py}", f"Right of {peer_name} (Top)", f"Offset: {rx}x{py}"))

            lx = px - my_w
            ly_center = py + round((ph - my_h) / 2)
            opts.append((f"{lx}x{ly_center}", f"Left of {peer_name} (Centered)", f"Offset: {lx}x{ly_center}"))
            if ly_center != py:
                opts.append((f"{lx}x{py}", f"Left of {peer_name} (Top)", f"Offset: {lx}x{py}"))

            by_y = py + ph
            bx_center = px + round((pw - my_w) / 2)
            opts.append((f"{bx_center}x{by_y}", f"Below {peer_name} (Centered)", f"Offset: {bx_center}x{by_y}"))

            ay_y = py - my_h
            ax_center = px + round((pw - my_w) / 2)
            opts.append((f"{ax_center}x{ay_y}", f"Above {peer_name} (Centered)", f"Offset: {ax_center}x{ay_y}"))

        positions_map[name] = opts

    return positions_map


# =============================================================================
# 3. MONITOR & WORKSPACE LUA ENGINE
# =============================================================================

def _format_new_block(method: str, entries: list[tuple[str, str]]) -> str:
    body = "\n".join(f"    {format_lua_key(k):<10}= {v}," for k, v in entries)
    return f"hl.{method}({{\n{body}\n}})"


def _format_new_config_block(sections: dict[str, list[str]]) -> str:
    sec_lines: list[str] = []
    for section, keys in sections.items():
        inner = "\n".join(f"        {format_lua_key(k)} = nil," for k in keys)
        sec_lines.append(f"    {format_lua_key(section)} = {{\n{inner}\n    }},")
    return "hl.config({\n" + "\n".join(sec_lines) + "\n})"


def _split_scope_key(full_key: str) -> tuple[str, str]:
    """Slash-safe split of `<scope>/<field>` into `(scope, field)`."""
    scope, sep, field_name = full_key.rpartition("/")
    return (scope, field_name) if sep else ("", full_key)


class MonitorLuaEngine(HyprlandLuaEngine):
    """Specialized gatekeeper for Hyprland git monitors and workspace rules."""

    def __init__(self, config_path: str | Path | None = None):
        resolved = (
            _default_config_path()
            if config_path is None
            else Path(config_path).expanduser().resolve(strict=False)
        )
        super().__init__(config_path=str(resolved))
        self._scope_map: dict[str, str] = {}
        self._monitor_resolutions: dict[str, tuple[int, int]] = {}
        self._monitor_metadata: dict[str, dict[str, Any]] = {}
        self._loaded_signature: tuple[int, int] | None = None

    @contextmanager
    def _config_flock(self):
        """Thread-local reentrant cross-process flock coordinated with adjust_scale.py."""
        depth = getattr(_FLOCK_TLS, "depth", 0)
        if depth > 0:
            _FLOCK_TLS.depth = depth + 1
            try:
                yield
            finally:
                _FLOCK_TLS.depth -= 1
            return

        lock_path = _config_lock_path()
        handle = None
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
            handle = os.fdopen(fd, "r+b", buffering=0)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            if handle is not None:
                handle.close()
                handle = None
            if exc.errno not in (errno.EACCES, errno.EROFS, errno.ENOENT, errno.EPERM):
                raise

        _FLOCK_TLS.depth = 1
        try:
            yield
        finally:
            _FLOCK_TLS.depth = 0
            if handle is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()

    def _file_signature(self) -> tuple[int, int] | None:
        real = Path(self.config_path).resolve(strict=False)
        try:
            st = real.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _restore_snapshot(self, existed: bool, snapshot: bytes | None) -> None:
        real = Path(self.config_path).resolve(strict=False)
        if not existed:
            try:
                real.unlink(missing_ok=True)
            except OSError:
                pass
            return
        if snapshot is None:
            return
        mode = real.stat().st_mode if real.exists() else 0o644
        fd, tmp = tempfile.mkstemp(dir=real.parent, prefix=".monitors.lua.rollback.")
        tmp_path = Path(tmp)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(snapshot)
                f.flush()
                os.fchmod(f.fileno(), mode & 0o7777)
                os.fsync(f.fileno())
            os.replace(tmp_path, real)
            dir_fd = os.open(real.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            tmp_path.unlink(missing_ok=True)

    def _get_valid_scale(self, requested_scale: float, phys_w: int, phys_h: int) -> str:
        """Coerces any finite positive scale into the nearest sharp 1/120 scale within bounds."""
        if not math.isfinite(requested_scale) or requested_scale <= 0:
            raise ValueError("Scale must be finite and positive; use auto for automatic scaling.")
        eff = closest_sharp(phys_w, phys_h, requested_scale, MIN_SCALE, MAX_SCALE)
        return format_scale(eff)

    def load_state(self) -> dict[str, Any]:
        state = super().load_state()
        self._ast_state = dict(state)
        self._loaded_signature = self._file_signature()
        self._scope_map.clear()
        self._monitor_resolutions.clear()
        self._monitor_metadata.clear()

        try:
            res = subprocess.run(["hyprctl", "-j", "monitors", "all"], capture_output=True, text=True, timeout=3)
            raw = (res.stdout or "").strip()
            if raw and raw[0] not in ("[", "{"):
                lines = raw.splitlines()
                for i, line in enumerate(lines):
                    if line.strip().startswith(("[", "{")):
                        raw = "\n".join(lines[i:])
                        break
            parsed_monitors = json.loads(raw) if raw else []
            live_monitors = parsed_monitors if isinstance(parsed_monitors, list) else []
        except Exception:
            live_monitors = []

        live_monitors = [
            m for m in live_monitors
            if isinstance(m, dict) and _safe_str(m.get("name")).strip().upper() != "FALLBACK"
        ]
        name_by_id = {
            _safe_str(m.get("id")): _safe_str(m.get("name")).strip()
            for m in live_monitors
            if m.get("id") is not None and _safe_str(m.get("name")).strip()
        }

        # Pre-index unique monitor AST scopes in a slash-safe manner
        ast_monitor_scopes: list[str] = []
        seen_ast_scopes: set[str] = set()
        for k in state:
            scope, _ = _split_scope_key(k)
            if scope.startswith("monitor/") and scope not in seen_ast_scopes:
                seen_ast_scopes.add(scope)
                ast_monitor_scopes.append(scope)

        normalized_state: dict[str, Any] = {}
        available_monitor_names: list[str] = []
        active_monitor_names: list[str] = []

        for m in live_monitors:
            name = _safe_str(m.get("name")).strip()
            desc = _safe_str(m.get("description")).strip()
            if not name:
                continue
            available_monitor_names.append(name)

            health = monitor_health(m)
            if health in ("usable", "sleeping"):
                active_monitor_names.append(name)

            phys_w = _safe_int(m.get("width"), 1920)
            phys_h = _safe_int(m.get("height"), 1080)
            if phys_w <= 0:
                phys_w = 1920
            if phys_h <= 0:
                phys_h = 1080

            pw_mm = _safe_int(m.get("physicalWidth"), 0)
            ph_mm = _safe_int(m.get("physicalHeight"), 0)
            ppi, diag_in = calculate_ppi(phys_w, phys_h, pw_mm, ph_mm)
            detected_bpc = detect_bitdepth(_safe_str(m.get("currentFormat")))
            refresh_rate = _safe_float(m.get("refreshRate"), 60.0)
            if refresh_rate <= 0:
                refresh_rate = 60.0

            raw_mirror = _safe_str(m.get("mirrorOf")).strip()
            live_mirror = "" if raw_mirror.lower() in ("", "none") else name_by_id.get(raw_mirror, raw_mirror)

            hw_details = _safe_dict(m.get("hardwareDetails"))
            vrr_capable = _safe_bool(hw_details.get("vrrCapable"), False)
            hdr_capable = _safe_bool(hw_details.get("hdr"), False)
            bt2020_capable = _safe_bool(hw_details.get("bt2020"), False)
            dpms_status = _safe_bool(m.get("dpmsStatus"), True)
            live_vrr = normalize_vrr(m.get("vrr", 0))

            self._monitor_metadata[name] = {
                "make": _safe_str(m.get("make")),
                "model": _safe_str(m.get("model")),
                "serial": _safe_str(m.get("serial")),
                "description": desc,
                "ppi": ppi,
                "diagonal_inches": diag_in,
                "bitdepth": detected_bpc,
                "refresh_rate": refresh_rate,
                "current_mode": f"{phys_w}x{phys_h}@{refresh_rate:.2f}",
                "vrr_capable": vrr_capable,
                "hdr_capable": hdr_capable,
                "bt2020_capable": bt2020_capable,
                "dpms_status": dpms_status,
                "mirror_of": live_mirror,
                "health": health,
            }

            ui_scope = f"monitor/{name}"
            ast_scope = ui_scope

            # Match desc: rules using Hyprland prefix semantics (stripping trailing '(PORT)')
            if desc and ui_scope not in seen_ast_scopes:
                desc_clean = normalize_description(desc)
                desc_short = short_description(desc_clean)
                for cand_scope in ast_monitor_scopes:
                    _, _, cand_id = cand_scope.partition("/")
                    if cand_id.startswith("desc:"):
                        prefix = cand_id[5:].strip()
                        if prefix and (
                            desc.startswith(prefix)
                            or desc_clean.startswith(prefix)
                            or desc_short.startswith(prefix)
                        ):
                            ast_scope = cand_scope
                            break

            self._scope_map[ui_scope] = ast_scope
            self._monitor_resolutions[name] = (phys_w, phys_h)
            self._monitor_resolutions[ui_scope] = (phys_w, phys_h)
            self._monitor_resolutions[ast_scope] = (phys_w, phys_h)

            prefix = ast_scope + "/"
            for k, v in state.items():
                if k.startswith(prefix):
                    sub_key = k[len(prefix):]
                    if sub_key == "reserved_area" and isinstance(v, dict):
                        normalized_state[f"{ui_scope}/{sub_key}"] = dict(v)
                    else:
                        normalized_state[f"{ui_scope}/{sub_key}"] = v

            # Recover sharp scale using persisted monitors.lua hint when numeric
            persisted_scale = state.get(f"{ast_scope}/scale")
            scale_hint_val: float | None = None
            if persisted_scale is not None and not isinstance(persisted_scale, bool):
                raw_hint = _safe_str(persisted_scale).strip()
                if raw_hint.lower() != "auto":
                    parsed_hint = _safe_float(raw_hint, -1.0)
                    if parsed_hint > 0:
                        scale_hint_val = parsed_hint

            cur_scale = _safe_float(m.get("scale"), 1.0)
            if cur_scale <= 0:
                cur_scale = 1.0
            clean_cur_scale = format_scale(snap_scale(cur_scale, phys_w, phys_h, hint=scale_hint_val))

            is_disabled = _safe_bool(
                state.get(f"{ast_scope}/disabled", m.get("disabled", False)),
                False,
            )
            normalized_state[f"{ui_scope}/disabled"] = is_disabled
            normalized_state[f"{ui_scope}/enabled"] = not is_disabled

            _, _, output_id = ast_scope.partition("/")
            live_transform = _safe_int(m.get("transform"), 0) & 7
            live_cm = _safe_str(m.get("colorManagementPreset"), "auto").strip() or "auto"
            live_sdr_bright = _safe_float(m.get("sdrBrightness"), 1.0)
            live_sdr_sat = _safe_float(m.get("sdrSaturation"), 1.0)

            defaults: dict[str, Any] = {
                "output": output_id,
                "enabled": not is_disabled,
                "disabled": is_disabled,
                "mode": "preferred",
                "position": "auto",
                "scale": clean_cur_scale,
                "transform": str(live_transform),
                "vrr": live_vrr,
                "bitdepth": str(detected_bpc),
                "cm": live_cm,
                "sdr_eotf": "default",
                "sdrbrightness": str(live_sdr_bright),
                "sdrsaturation": str(live_sdr_sat),
                "mirror": live_mirror,
                "icc": "",
                "reserved_area": 0,
                "supports_wide_color": "0",
                "supports_hdr": "0",
                "sdr_min_luminance": 0.2,
                "sdr_max_luminance": 80,
                "min_luminance": -1.0,
                "max_luminance": -1,
                "max_avg_luminance": -1,
            }

            for key, default_val in defaults.items():
                state_key = f"{ui_scope}/{key}"
                if state_key not in normalized_state:
                    normalized_state[state_key] = default_val

        # Preserve AST config blocks for offline monitors (slash-safe scope check)
        matched_ast_scopes = set(self._scope_map.values())
        for k, v in state.items():
            scope, _ = _split_scope_key(k)
            if scope.startswith("monitor/") and scope in matched_ast_scopes:
                continue
            normalized_state[k] = v

        # -------------------------------------------------------------
        # WORKSPACE RULES (SECTION 6): Load or initialize Workspaces 1..10
        # -------------------------------------------------------------
        primary_monitor = (
            active_monitor_names[0]
            if active_monitor_names
            else (available_monitor_names[0] if available_monitor_names else "")
        )
        for ws_idx in range(1, 11):
            ws_str = str(ws_idx)
            ws_scope = f"workspace_rule/{ws_str}"

            ws_mon_key = f"{ws_scope}/monitor"
            ws_def_key = f"{ws_scope}/default"
            ws_per_key = f"{ws_scope}/persistent"

            normalized_state[ws_mon_key] = _safe_str(state.get(ws_mon_key), primary_monitor) if ws_mon_key in state else primary_monitor
            normalized_state[ws_def_key] = _safe_bool(state[ws_def_key], False) if ws_def_key in state else (ws_idx == 1)
            normalized_state[ws_per_key] = _safe_bool(state[ws_per_key], False) if ws_per_key in state else False

        # -------------------------------------------------------------
        # GLOBAL SETTINGS (SECTION 7): Power, Render, and VFR
        # -------------------------------------------------------------
        global_defaults: dict[str, Any] = {
            "debug/vfr": True,
            "debug/overlay": False,
            "misc/vrr": "0",
            "render/cm_sdr_eotf": "auto",
            "render/direct_scanout": "0",
            "render/cm_auto_hdr": "0",
        }
        primary_file = self._primary_path()
        if primary_file.exists():
            try:
                cached_pri = primary_file.read_text(encoding="utf-8").strip()
                if cached_pri:
                    primary_monitor = cached_pri
            except Exception:
                pass
        for key, value in global_defaults.items():
            normalized_state.setdefault(key, value)
        normalized_state["DEFAULT/primary_monitor"] = primary_monitor

        self.cache = normalized_state
        return normalized_state

    @staticmethod
    def _primary_path() -> Path:
        configured = os.environ.get("XDG_CONFIG_HOME", "").strip()
        if configured:
            cand = Path(configured).expanduser()
            base = cand if cand.is_absolute() else (Path.home() / ".config")
        else:
            base = Path.home() / ".config"
        return (base / "hypr" / "primary_monitor").resolve(strict=False)

    def write_batch(self, changes: list[tuple[str, str, Any, str]]) -> tuple[bool, str, str]:
        if not changes:
            return True, "No pending changes.", ""
        with self._lock:
            with self._config_flock():
                try:
                    return self._write_monitor_batch(changes)
                except (OSError, UnicodeError, ValueError, TypeError, OverflowError) as exc:
                    return False, f"Monitor edit failed: {exc}", ""

    def _find_semantic_block(
        self,
        content: str,
        method: str,
        identity: str,
        pending_keys: list[str],
    ) -> tuple[int, int] | None:
        id_key = "output" if method == "monitor" else "workspace"
        matches: list[tuple[int, int, bool, bool]] = []

        for a, b, inner_a, inner_b in calls(content, method):
            try:
                open_pos, close_end = find_table_bounds(content, inner_a, inner_b)
                flds = fields(content, open_pos, close_end)
            except ValueError:
                continue
            if id_key not in flds:
                continue
            val_start, val_end = flds[id_key]
            decoded = string_value(content[val_start:val_end])
            if decoded != identity:
                continue

            is_disabled = False
            if method == "monitor" and "disabled" in flds:
                ds, de = flds["disabled"]
                is_disabled = content[ds:de].strip() == "true"

            is_primary = bool(
                method != "monitor"
                or "mode" in flds
                or "position" in flds
                or any(k in flds for k in pending_keys)
            )
            matches.append((a, b, is_disabled, is_primary))

        if not matches:
            return None

        if method == "monitor" and "disabled" not in pending_keys:
            active = [m for m in matches if not m[2]]
            if active:
                matches = active

        primaries = [m for m in matches if m[3]]
        chosen = primaries[-1] if primaries else matches[-1]
        return (chosen[0], chosen[1])

    def _write_monitor_batch(self, changes: list[tuple[str, str, Any, str]]) -> tuple[bool, str, str]:
        # Refresh disk state if not loaded or if modified externally (e.g., by adjust_scale.py)
        disk_sig = self._file_signature()
        if (not self.loaded_files and Path(self.config_path).exists()) or (
            disk_sig is not None and disk_sig != self._loaded_signature
        ):
            self.load_state()
            if not self.loaded_files and Path(self.config_path).exists():
                return False, "Cannot evaluate monitor configuration.", ""

        primary: str | None = None
        translated: dict[tuple[str, str], tuple[str, str, Any, str]] = {}
        deleting_keys: set[tuple[str, str]] = set()
        requested_modes = {
            self._scope_map.get(scope, scope): _safe_str(value)
            for key, scope, value, _ in changes
            if key == "mode"
        }

        for key, scope, value, kind in changes:
            if key == "primary_monitor" and scope in ("DEFAULT", "workspace", "globals"):
                primary = _safe_str(value).strip()
                if any(c in primary for c in "\n\r\0"):
                    raise ValueError("Invalid primary monitor name.")
                continue

            ast_scope = self._scope_map.get(scope, scope)
            deleting = value is None or str(value) in {"nil", "__DELETE__"}

            if ast_scope.startswith("monitor/") and key == "enabled":
                key = "disabled"
                if not deleting:
                    value, kind = str(not boolean(value)).lower(), "bool"

            if ast_scope.startswith("monitor/") and not deleting:
                if key in ("output", "mode", "position", "mirror", "icc", "cm", "sdr_eotf"):
                    kind = "string"
                elif key == "transform":
                    value, kind = str(_parse_exact_int(value, "transform", lo=0, hi=7)), "int"
                elif key == "vrr":
                    value, kind = str(_parse_exact_int(value, "vrr", lo=0, hi=2)), "int"
                elif key == "bitdepth":
                    value, kind = str(_parse_exact_int(value, "bitdepth", allowed={8, 10})), "int"
                elif key == "reserved_area":
                    # Preserve structured four-edge reserved_area tables untouched
                    if isinstance(value, dict):
                        continue
                    value, kind = str(_parse_exact_int(value, "reserved_area", lo=0)), "int"
                elif key == "scale":
                    raw_scale = _safe_str(value).strip()
                    if raw_scale.lower() == "auto":
                        value, kind = "auto", "string"
                    else:
                        scale = float(raw_scale)
                        if not math.isfinite(scale) or scale <= 0:
                            raise ValueError("Scale must be finite and positive; use auto for automatic scaling.")
                        resolution = (
                            self._monitor_resolutions.get(scope)
                            or self._monitor_resolutions.get(ast_scope)
                            or self._monitor_resolutions.get(scope.removeprefix("monitor/"))
                        )
                        mode = requested_modes.get(
                            ast_scope,
                            getattr(self, "_ast_state", {}).get(ast_scope + "/mode", ""),
                        )
                        if match := _RE_MODE_DIM.fullmatch(_safe_str(mode).strip()):
                            resolution = (int(match[1]), int(match[2]))
                        value = self._get_valid_scale(scale, *resolution) if resolution else format_scale(scale)
                        kind = "float"
                elif key in ("sdrbrightness", "sdrsaturation", "sdr_min_luminance", "min_luminance"):
                    fv = float(value)
                    if not math.isfinite(fv):
                        raise ValueError(f"{key} must be a finite number.")
                    value, kind = str(fv), "float"
                elif key in ("sdr_max_luminance", "max_luminance", "max_avg_luminance"):
                    value, kind = str(_parse_exact_int(value, key)), "int"

            if ast_scope.startswith("workspace_rule/") and not deleting and key in ("workspace", "monitor"):
                kind = "string"

            translated[key, ast_scope] = (key, ast_scope, value, kind)
            if deleting:
                deleting_keys.add((key, ast_scope))
            else:
                deleting_keys.discard((key, ast_scope))

        ast_state = getattr(self, "_ast_state", {})
        existing_sites = {(c["method"], c["id"]) for c in self._call_sites}

        def is_missing(scope: str, key: str) -> bool:
            if (key, scope) in deleting_keys:
                return False
            if f"{scope}/{key}" in ast_state:
                return False
            method, sep, identity = scope.partition("/")
            if sep and ((method == "monitor" and key == "output") or (method == "workspace_rule" and key == "workspace")):
                return (method, identity) not in existing_sites
            return True

        def transform(content: str) -> str:
            block_updates: dict[tuple[int, int], list[str]] = {}
            config_updates: dict[tuple[int, int], dict[str, list[str]]] = {}
            new_config_sections: dict[str, list[str]] = {}
            additions: list[str] = []

            grouped: dict[str, list[str]] = {}
            for key, scope, _, _ in translated.values():
                if is_missing(scope, key):
                    grouped.setdefault(scope, []).append(key)

            existing_config_calls: list[tuple[int, int, set[str]]] = []
            for a, b, inner_a, inner_b in calls(content, "config"):
                try:
                    open_pos, close_end = find_table_bounds(content, inner_a, inner_b)
                    sec_names = set(fields(content, open_pos, close_end).keys())
                except ValueError:
                    sec_names = set()
                existing_config_calls.append((a, b, sec_names))

            for scope, keys in grouped.items():
                method, sep, identity = scope.partition("/")
                if method in ("monitor", "workspace_rule") and sep:
                    span = self._find_semantic_block(content, method, identity, keys)
                    if span is not None:
                        block_updates.setdefault(span, []).extend(keys)
                    else:
                        id_key = "output" if method == "monitor" else "workspace"
                        entries = [(id_key, lua_string(identity))]
                        entries.extend((k, "nil") for k in keys if k != id_key)
                        additions.append(_format_new_block(method, entries))
                elif method in ("misc", "debug", "render") and not sep:
                    target_cfg: tuple[int, int] | None = None
                    for a, b, sec_names in reversed(existing_config_calls):
                        if method in sec_names:
                            target_cfg = (a, b)
                            break
                    if target_cfg is None and existing_config_calls:
                        a, b, _ = existing_config_calls[-1]
                        target_cfg = (a, b)
                    if target_cfg is not None:
                        config_updates.setdefault(target_cfg, {}).setdefault(method, []).extend(keys)
                    else:
                        new_config_sections.setdefault(method, []).extend(keys)

            edits: list[tuple[int, int, str]] = []
            for (a, b), keys in block_updates.items():
                block = content[a:b]
                for k in dict.fromkeys(keys):
                    block = replace_field(block, k, "nil")
                edits.append((a, b, block))

            for (a, b), sec_map in config_updates.items():
                block = content[a:b]
                for section, keys in sec_map.items():
                    for k in dict.fromkeys(keys):
                        block = replace_nested_field(block, section, k, "nil")
                edits.append((a, b, block))

            updated = apply_edits(content, edits)
            if new_config_sections:
                deduped = {s: list(dict.fromkeys(ks)) for s, ks in new_config_sections.items()}
                additions.append(_format_new_config_block(deduped))

            if additions:
                if updated and not updated.endswith("\n"):
                    updated += "\n"
                sep_prefix = "\n" if updated.strip() else ""
                updated += sep_prefix + "\n\n".join(additions) + "\n"

            # Stage 1 + Stage 2 pre-write verification on transformed intermediate Lua
            validate_lua_syntax(updated)
            return updated

        if translated:
            real_cfg = Path(self.config_path).resolve(strict=False)
            existed = real_cfg.exists()
            snapshot = real_cfg.read_bytes() if existed else None

            missing = any(is_missing(scope, key) for key, scope, _, _ in translated.values())
            result = (
                self.write_transformed(list(translated.values()), transform)
                if missing
                else super().write_batch(list(translated.values()))
            )
            if not result[0]:
                return result

            # Post-write two-stage syntax verification with atomic rollback on failure
            if real_cfg.exists():
                written = real_cfg.read_text(encoding="utf-8")
                try:
                    validate_lua_syntax(written)
                except ValueError as exc:
                    self._restore_snapshot(existed, snapshot)
                    return False, f"Monitor edit aborted (Lua syntax validation failed): {exc}", ""
            self._loaded_signature = self._file_signature()

        if primary is not None:
            path = self._primary_path()
            try:
                _, expected = read_text(path)
                atomic_write(path, primary + "\n", expected)
                self.cache["DEFAULT/primary_monitor"] = primary
            except (OSError, UnicodeError) as exc:
                context = " (Lua changes already saved)" if translated else ""
                return False, f"Primary monitor save failed{context}: {exc}", ""

        # Power on any newly enabled monitors from DPMS sleep
        enabled_monitors = [
            scope.removeprefix("monitor/")
            for key, scope, value, _ in changes
            if scope.startswith("monitor/") and key == "enabled" and boolean(value)
        ]
        if enabled_monitors:
            wake_displays(enabled_monitors)

        return True, "Monitor configuration saved.", ""
