#!/usr/bin/env python3
"""
Systemd Service Manager for Dusky Control Center

Natively integrates systemd service toggling with bulletproof async handling.
Borrowed and hardened from dusky_tui/python/engines/systemd.py logic.

Features:
- Validates unit names strictly (no shell injection, no path traversal)
- Supports user vs system scope with proper privilege escalation via pkexec/polkit
- Async Gio.Subprocess with generation counters, cancellables, and timeouts
- Batch-capable: can query multiple units in single systemctl is-active call
- Efficient: single-shot check on page map, no continuous polling
- Polkit caching: auth_admin_keep (~5 min) keeps password for session via dusky_polkit
"""

from __future__ import annotations

import logging
import re
import shlex
from dataclasses import dataclass
from typing import Callable, Final, Literal

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

SYSTEMCTL_PATH: Final[str] = "/usr/bin/systemctl"
PKEXEC_PATH: Final[str] = "/usr/bin/pkexec"

# Valid systemd unit name: must be filesystem-safe and end with known suffix.
# Allow common suffixes for flexibility; spec focuses on .service but we permit others.
_UNIT_RE: Final[re.Pattern[str]] = re.compile(
    r"^[a-zA-Z0-9@._\-]+\.(?:service|timer|socket|target|path|mount|automount|swap|slice|scope|device)$"
)
_UNIT_BARE_RE: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9@._\-]+$")

VALID_SCOPES: Final[frozenset[str]] = frozenset({"system", "user"})
SCOPE_SYSTEM: Final[str] = "system"
SCOPE_USER: Final[str] = "user"

# Timeouts (seconds)
TIMEOUT_IS_ACTIVE: Final[int] = 4
TIMEOUT_TOGGLE: Final[int] = 45  # must allow polkit dialog interaction

# Type aliases
Scope = Literal["system", "user"]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _sanitize_unit(raw: object) -> str | None:
    """
    Validate and normalize a systemd unit name.
    Returns sanitized name or None if invalid.
    - Strips whitespace
    - Rejects absolute paths, slashes, shell metachars
    - Auto-appends .service if bare name given (e.g., "bluetooth")
    - Validates against strict regex
    """
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    if not name or name.startswith("-"):
        return None
    # Reject path traversal / shell injection vectors
    if "/" in name or "\\" in name or "\0" in name:
        return None
    if any(c in name for c in ";&|$`'\"()<>*?#!~[]{}"):
        return None
    # If already a valid unit with suffix, return as-is
    if _UNIT_RE.fullmatch(name):
        if len(name) > 256:
            return None
        return name
    # Bare name (no suffix) -> append .service and validate
    if _UNIT_BARE_RE.fullmatch(name):
        name = f"{name}.service"
        if _UNIT_RE.fullmatch(name) and len(name) <= 256:
            return name
        return None
    return None


def _sanitize_scope(raw: object) -> Scope:
    """Normalize scope to system|user, default system."""
    if isinstance(raw, str) and raw.strip().lower() == "user":
        return "user"
    return "system"


def _build_is_active_argv(scope: Scope, units: list[str]) -> list[str]:
    if scope == "user":
        return [SYSTEMCTL_PATH, "--user", "is-active", *units]
    return [SYSTEMCTL_PATH, "is-active", *units]


def _build_toggle_argv(scope: Scope, unit: str, enable: bool) -> list[str]:
    action = "enable" if enable else "disable"
    if scope == "user":
        return [SYSTEMCTL_PATH, "--user", action, "--now", unit]
    # System scope requires polkit privilege via pkexec.
    # pkexec will prompt via dusky_polkit and cache per auth_admin_keep (~5min).
    return [PKEXEC_PATH, SYSTEMCTL_PATH, action, "--now", unit]


# ─────────────────────────────────────────────────────────────────────────────
# Async Subprocess Infrastructure (similar to rows._run_shell_async but argv-based)
# ─────────────────────────────────────────────────────────────────────────────
class _ServiceCommandHandle:
    __slots__ = ("_proc", "_cancellable", "_lock", "_timeout_source_id")

    def __init__(self, proc: Gio.Subprocess, cancellable: Gio.Cancellable) -> None:
        self._proc = proc
        self._cancellable = cancellable
        self._lock = __import__("threading").Lock()
        self._timeout_source_id = 0

    def set_timeout_source(self, source_id: int) -> None:
        with self._lock:
            self._timeout_source_id = source_id

    def forget_timeout_source(self) -> None:
        with self._lock:
            self._timeout_source_id = 0

    def clear_timeout_source(self) -> None:
        with self._lock:
            source_id = self._timeout_source_id
            self._timeout_source_id = 0
        if source_id > 0:
            try:
                GLib.source_remove(source_id)
            except Exception:
                pass

    def cancel(self) -> None:
        self.clear_timeout_source()
        try:
            self._cancellable.cancel()
        except Exception:
            pass
        try:
            self._proc.force_exit()
        except Exception:
            pass


def _run_argv_async(
    argv: list[str],
    timeout_seconds: int,
    on_complete: Callable[[str | None, str | None, bool, int | None], None],
) -> _ServiceCommandHandle | None:
    """
    Spawn argv via Gio.Subprocess and invoke on_complete(stdout, stderr, success, exit_status)
    on the main thread via idle.

    stdout/stderr are stripped strings or None on spawn failure.
    success = proc.get_successful() (exit 0)
    exit_status = proc.get_exit_status() or None
    """
    cancellable = Gio.Cancellable()
    try:
        launcher = Gio.SubprocessLauncher.new(
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
        )
        proc = launcher.spawnv(argv)
    except GLib.Error as e:
        log.debug("Failed to spawn %.80s: %s", shlex.join(argv), e.message)
        GLib.idle_add(lambda message=e.message: (on_complete(None, message, False, None), GLib.SOURCE_REMOVE)[1])
        return None

    handle = _ServiceCommandHandle(proc, cancellable)

    def on_timeout() -> bool:
        handle.forget_timeout_source()
        handle.cancel()
        return GLib.SOURCE_REMOVE

    def on_communicate_finish(proc: Gio.Subprocess, result: Gio.AsyncResult) -> None:
        handle.clear_timeout_source()
        try:
            success, stdout_data, stderr_data = proc.communicate_utf8_finish(result)
            ok = success and proc.get_successful()
            exit_code = proc.get_exit_status() if proc.get_if_exited() else None
            # systemctl is-active returns non-zero for inactive (exit 3) – not a failure
            # For toggle, success == exit 0. For is-active we parse stdout.
            # Provide raw stdout/stderr
            if success:
                on_complete(
                    stdout_data.strip() if stdout_data else "",
                    stderr_data.strip() if stderr_data else "",
                    ok,
                    exit_code,
                )
            else:
                on_complete(None, None, False, exit_code)
        except GLib.Error as e:
            if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                # Timeout or user cancellation – surface as failure so UI can reset
                # (widget's generation/is_destroyed guards will ignore if widget is gone)
                on_complete(None, "Operation cancelled or timed out", False, None)
                return
            on_complete(None, e.message, False, None)

    if timeout_seconds > 0:
        handle.set_timeout_source(GLib.timeout_add_seconds(timeout_seconds, on_timeout))

    proc.communicate_utf8_async(None, cancellable, on_communicate_finish)
    return handle


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(slots=True, frozen=True)
class ServiceSpec:
    unit: str
    scope: Scope


@dataclass(slots=True, frozen=True)
class UnitStatus:
    load_state: str
    active_state: str
    sub_state: str
    unit_file_state: str


def check_unit_status_async(
    scope: Scope,
    unit: str,
    timeout: int = TIMEOUT_IS_ACTIVE,
    on_result: Callable[[UnitStatus | None], None] | None = None,
) -> _ServiceCommandHandle | None:
    """Read runtime and startup states in one current-systemd show query."""
    sanitized = _sanitize_unit(unit)
    if sanitized is None or scope not in VALID_SCOPES:
        if on_result:
            GLib.idle_add(lambda: (on_result(None), GLib.SOURCE_REMOVE)[1])
        return None
    argv = [SYSTEMCTL_PATH]
    if scope == "user":
        argv.append("--user")
    argv += ["show", "--no-pager", "--property=LoadState,ActiveState,SubState,UnitFileState", "--", sanitized]

    def complete(stdout: str | None, _stderr: str | None, success: bool, _status: int | None) -> None:
        result: UnitStatus | None = None
        if success and stdout:
            fields = dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)
            if all(key in fields for key in ("LoadState", "ActiveState", "SubState", "UnitFileState")):
                result = UnitStatus(fields["LoadState"], fields["ActiveState"], fields["SubState"], fields["UnitFileState"])
        if on_result:
            on_result(result)

    return _run_argv_async(argv, timeout, complete)


def normalize_service_spec(raw_unit: object, raw_scope: object = "system") -> ServiceSpec | None:
    """Validate inputs and return normalized spec or None."""
    unit = _sanitize_unit(raw_unit)
    if unit is None:
        return None
    scope = _sanitize_scope(raw_scope)
    return ServiceSpec(unit=unit, scope=scope)


def check_single_service_async(
    scope: Scope,
    unit: str,
    timeout: int = TIMEOUT_IS_ACTIVE,
    on_result: Callable[[bool | None], None] | None = None,
) -> _ServiceCommandHandle | None:
    """
    Async check if a single unit is active.
    Calls on_result(True=active, False=inactive, None=error) on main thread.
    """
    sanitized = _sanitize_unit(unit)
    if sanitized is None:
        if on_result:
            GLib.idle_add(lambda: (on_result(None), GLib.SOURCE_REMOVE)[1])
        return None
    if scope not in VALID_SCOPES:
        scope = "system"  # type: ignore[assignment]

    argv = _build_is_active_argv(scope, [sanitized])  # type: ignore[arg-type]

    def _on_complete(stdout: str | None, stderr: str | None, success: bool, exit_code: int | None) -> None:
        if on_result is None:
            return
        if stdout is None:
            log.debug("is-active failed for %s/%s: stderr=%r", scope, sanitized, stderr)
            GLib.idle_add(lambda: (on_result(None), GLib.SOURCE_REMOVE)[1])
            return
        # stdout is "active" or "inactive"/"failed"/"activating" etc.
        # For single unit, exit_code 0 => active, 3 => inactive
        # We treat only "active" as True, everything else False (including activating)
        normalized = stdout.strip().lower()
        is_active = (normalized == "active") if normalized else None
        GLib.idle_add(lambda: (on_result(is_active), GLib.SOURCE_REMOVE)[1])

    return _run_argv_async(argv, timeout, _on_complete)


def check_multiple_services_async(
    units_by_scope: dict[str, list[str]],
    timeout: int = TIMEOUT_IS_ACTIVE,
    on_result: Callable[[dict[tuple[str, str], bool | None]], None] | None = None,
) -> list[_ServiceCommandHandle]:
    """
    Batch check for multiple units, grouped by scope.
    Uses 1 subprocess per scope (max 2). Efficient for page-level checks.
    on_result receives mapping {(scope, unit): bool|None}
    """
    # sanitize
    sanitized_by_scope: dict[Scope, list[str]] = {"system": [], "user": []}
    for raw_scope, raw_units in units_by_scope.items():
        scope = _sanitize_scope(raw_scope)
        for raw_unit in raw_units:
            unit = _sanitize_unit(raw_unit)
            if unit:
                sanitized_by_scope[scope].append(unit)

    # dedupe while preserving order
    for scope in list(sanitized_by_scope.keys()):
        seen: set[str] = set()
        deduped: list[str] = []
        for u in sanitized_by_scope[scope]:
            if u not in seen:
                seen.add(u)
                deduped.append(u)
        sanitized_by_scope[scope] = deduped

    if not any(sanitized_by_scope.values()):
        if on_result:
            GLib.idle_add(lambda: (on_result({}), GLib.SOURCE_REMOVE)[1])
        return []

    pending = len([s for s in sanitized_by_scope.values() if s])
    results: dict[tuple[str, str], bool | None] = {}
    handles: list[_ServiceCommandHandle] = []
    # lock for thread safety of results dict (callbacks on main thread, but multiple scopes may complete concurrently)
    import threading

    lock = threading.Lock()

    def _maybe_emit() -> None:
        nonlocal pending
        pending -= 1
        if pending == 0 and on_result:
            # copy to avoid mutation after emit
            snapshot = dict(results)
            GLib.idle_add(lambda: (on_result(snapshot), GLib.SOURCE_REMOVE)[1])

    for scope, units in sanitized_by_scope.items():
        if not units:
            continue
        argv = _build_is_active_argv(scope, units)  # type: ignore[arg-type]

        # capture scope/units for closure
        def _make_cb(s: Scope, us: list[str]) -> Callable[[str | None, str | None, bool, int | None], None]:
            def _cb(stdout: str | None, stderr: str | None, success: bool, exit_code: int | None) -> None:
                # Parse stdout lines; systemctl is-active prints one line per unit in order
                if stdout is None:
                    for u in us:
                        with lock:
                            results[(s, u)] = None
                else:
                    lines = stdout.strip().splitlines() if stdout.strip() else []
                    # If output truncated or empty, treat missing as None
                    for idx, u in enumerate(us):
                        if idx < len(lines):
                            val = lines[idx].strip().lower() == "active"
                            with lock:
                                results[(s, u)] = val
                        else:
                            with lock:
                                results[(s, u)] = None
                    # If expiry: if stdout parsing mismatched, fill remainder
                    if len(lines) < len(us):
                        for u in us[len(lines) :]:
                            with lock:
                                if (s, u) not in results:
                                    results[(s, u)] = None
                _maybe_emit()

            return _cb

        h = _run_argv_async(argv, timeout, _make_cb(scope, units))
        if h:
            handles.append(h)

    return handles


def toggle_service_async(
    scope: Scope,
    unit: str,
    enable: bool,
    timeout: int = TIMEOUT_TOGGLE,
    on_complete: Callable[[bool, str], None] | None = None,
) -> _ServiceCommandHandle | None:
    """
    Async toggle service via systemctl enable/disable --now
    For system scope uses pkexec for polkit auth (caches via auth_admin_keep ~5min)
    on_complete(success: bool, message: str) called on main thread.

    Message is human readable; if auth required / cancelled, success=False and message
    contains hint.
    """
    sanitized = _sanitize_unit(unit)
    if sanitized is None:
        if on_complete:
            GLib.idle_add(lambda: (on_complete(False, f"Invalid unit: {unit!r}"), GLib.SOURCE_REMOVE)[1])
        return None
    if scope not in VALID_SCOPES:
        scope = "system"  # type: ignore[assignment]

    argv = _build_toggle_argv(scope, sanitized, enable)  # type: ignore[arg-type]

    def _on_done(stdout: str | None, stderr: str | None, success: bool, exit_code: int | None) -> None:
        if on_complete is None:
            return
        if stdout is None and stderr is None:
            # Spawn failed
            GLib.idle_add(lambda: (on_complete(False, "Failed to spawn systemctl"), GLib.SOURCE_REMOVE)[1])
            return
        # Combine outputs for error analysis
        err = (stderr or "").strip()
        out = (stdout or "").strip()
        combined_lower = f"{err} {out}".lower()

        if success:
            action = "Enabled" if enable else "Disabled"
            GLib.idle_add(lambda: (on_complete(True, f"{action} {sanitized}"), GLib.SOURCE_REMOVE)[1])
            return

        # Failure analysis – prioritize timeout
        if "timed out" in combined_lower or "operation cancelled or timed out" in combined_lower:
            GLib.idle_add(lambda: (on_complete(False, f"Action timed out after {timeout}s"), GLib.SOURCE_REMOVE)[1])
            return
        if any(
            token in combined_lower
            for token in (
                "interactive authentication required",
                "not authorized",
                "authentication required",
                "auth_admin",
                "polkit",
                "pkexec",
                "dismissed",
                "cancelled",
                "canceled",
            )
        ):
            # Could be user cancelled polkit dialog
            if "dismissed" in combined_lower or "cancelled" in combined_lower or "canceled" in combined_lower:
                GLib.idle_add(lambda: (on_complete(False, "Authentication cancelled"), GLib.SOURCE_REMOVE)[1])
            else:
                GLib.idle_add(lambda: (on_complete(False, "Authentication required – Polkit agent may be missing"), GLib.SOURCE_REMOVE)[1])
            return
        if "not-found" in combined_lower or "not found" in combined_lower or "could not be found" in combined_lower:
            GLib.idle_add(lambda: (on_complete(False, f"Unit not found: {sanitized}"), GLib.SOURCE_REMOVE)[1])
            return
        if "job failed" in combined_lower or "failed" in combined_lower:
            # Provide stderr excerpt
            excerpt = err[:120] or out[:120] or "Unknown error"
            GLib.idle_add(lambda: (on_complete(False, f"Failed: {excerpt}"), GLib.SOURCE_REMOVE)[1])
            return
        # Generic
        excerpt = err[:120] or out[:120] or f"Exit {exit_code}"
        GLib.idle_add(lambda: (on_complete(False, f"Failed: {excerpt}"), GLib.SOURCE_REMOVE)[1])

    return _run_argv_async(argv, timeout, _on_done)


# Convenience wrapper for legacy systemd.py borrowing
def is_valid_unit_name(name: str) -> bool:
    return _sanitize_unit(name) is not None
