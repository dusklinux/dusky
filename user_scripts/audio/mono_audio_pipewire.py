#!/usr/bin/env python3
"""
System-wide mono toggle for PipeWire + pipewire-pulse on Arch Linux.

Audio path when enabled:
    application sink-inputs -> 1ch null sink -> loopback -> previous default sink

Key properties:
    - single-instance lock
    - atomic JSON state file in XDG_RUNTIME_DIR
    - exact module ID tracking
    - stale resource cleanup
    - readiness waits for sink, monitor source, and loopback stream
    - rollback on failure
"""

import argparse
import contextlib
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

MONO_SINK_NAME = "mono_global_downmix"
MONO_MONITOR_NAME = f"{MONO_SINK_NAME}.monitor"

RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
STATE_FILE = RUNTIME_DIR / f"mono_audio_state_{os.getuid()}"
LOCK_FILE = RUNTIME_DIR / f"mono_audio_lock_{os.getuid()}"

INDICATOR_FILE = Path.home() / ".config/dusky/settings/mono_audio"

STATE_VERSION = 1

CMD_TIMEOUT = 5.0
LOAD_TIMEOUT = 10.0
WAIT_TIMEOUT = 5.0
WAIT_STEP = 0.05

NOTIFY_TIMEOUT_MS = 2000

PULSEAPP_OWNER_MODULE_IDS = {4294967295, 18446744073709551615}

SINK_INPUT_HEADER_RE = re.compile(r"^\s*Sink Input #(\d+)$")

type SinkRow = tuple[int, str]
type SourceRow = tuple[int, str]
type ModuleRow = tuple[int, str, str]

# -----------------------------------------------------------------------------
# Data structures
# -----------------------------------------------------------------------------


class Phase(StrEnum):
    ENABLING = "enabling"
    ACTIVE = "active"


class MonoToggleError(RuntimeError):
    """Fatal mono toggle error."""


class WaitTimeoutError(TimeoutError):
    """Predicate did not become ready before the timeout."""

    def __init__(self, last_error: Exception | None = None) -> None:
        super().__init__("timed out")
        self.last_error = last_error


@dataclass(slots=True)
class CommandResult:
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str = ""


@dataclass(slots=True)
class SinkInputInfo:
    input_id: int
    sink_id: int | None = None
    owner_module: int | None = None


@dataclass(slots=True)
class MonoState:
    version: int = STATE_VERSION
    phase: Phase = Phase.ENABLING
    previous_default_sink: str = ""
    target_sink: str = ""
    null_module_id: int | None = None
    loopback_module_id: int | None = None
    restore_inputs: dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


# -----------------------------------------------------------------------------
# Command helpers
# -----------------------------------------------------------------------------


def _decode_subprocess_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def run_command(
    *args: str,
    timeout: float = CMD_TIMEOUT,
    force_c_locale: bool = True,
) -> CommandResult:
    env = dict(os.environ)
    if force_c_locale:
        env["LC_ALL"] = "C"
        env["LANG"] = "C"

    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            returncode=None,
            stdout=_decode_subprocess_output(exc.stdout),
            stderr=_decode_subprocess_output(exc.stderr),
            timed_out=True,
        )
    except OSError as exc:
        return CommandResult(returncode=None, error=str(exc))

    return CommandResult(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def command_error(result: CommandResult) -> str:
    if result.error:
        return result.error
    if result.timed_out:
        return "timeout"
    message = (result.stderr or result.stdout).strip()
    if message:
        return message
    if result.returncode is None:
        return "unknown error"
    return f"exit status {result.returncode}"


def require_success(result: CommandResult, context: str) -> str:
    if result.returncode != 0 or result.timed_out or result.error:
        raise MonoToggleError(f"{context}: {command_error(result)}")
    return result.stdout


def is_no_such_entity_error(result: CommandResult) -> bool:
    message = "\n".join(part for part in (result.error, result.stderr, result.stdout) if part)
    return "No such entity" in message


def ensure_dependencies() -> None:
    if shutil.which("pactl") is None:
        raise MonoToggleError("Required command not found: pactl")


def ensure_runtime_dir() -> None:
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MonoToggleError(f"Runtime directory is not available: {RUNTIME_DIR}: {exc}") from exc

    if not RUNTIME_DIR.is_dir():
        raise MonoToggleError(f"Runtime path is not a directory: {RUNTIME_DIR}")

    if not os.access(RUNTIME_DIR, os.R_OK | os.W_OK | os.X_OK):
        raise MonoToggleError(f"Runtime directory is not accessible: {RUNTIME_DIR}")


def ensure_audio_server() -> None:
    require_success(
        run_command("pactl", "info", timeout=3.0),
        "Failed to talk to pipewire-pulse",
    )


# -----------------------------------------------------------------------------
# File helpers
# -----------------------------------------------------------------------------


def fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY

    try:
        fd = os.open(path, flags)
    except OSError:
        return

    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        fsync_directory(path.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_path)


def set_indicator_state(enabled: bool) -> None:
    value = "True" if enabled else "False"
    with contextlib.suppress(OSError):
        atomic_write_text(INDICATOR_FILE, f"{value}\n", mode=0o644)


def write_state(state: MonoState) -> None:
    payload = {
        "version": state.version,
        "phase": state.phase.value,
        "previous_default_sink": state.previous_default_sink,
        "target_sink": state.target_sink,
        "null_module_id": state.null_module_id,
        "loopback_module_id": state.loopback_module_id,
        "restore_inputs": state.restore_inputs,
        "created_at": state.created_at,
    }
    try:
        atomic_write_text(
            STATE_FILE,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
    except OSError as exc:
        raise MonoToggleError(f"Failed to write state file: {exc}") from exc


def load_state() -> MonoState | None:
    try:
        raw = STATE_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError("state file root is not an object")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        with contextlib.suppress(OSError):
            STATE_FILE.unlink()
        return None

    try:
        version = int(data.get("version", STATE_VERSION))
        if version != STATE_VERSION:
            raise ValueError(f"unsupported state version: {version}")

        phase_raw = str(data.get("phase", Phase.ACTIVE.value))
        try:
            phase = Phase(phase_raw)
        except ValueError:
            phase = Phase.ACTIVE

        previous_default_sink = str(data.get("previous_default_sink", ""))
        target_sink = str(data.get("target_sink", ""))
        null_module_id = _as_int(data.get("null_module_id"))
        loopback_module_id = _as_int(data.get("loopback_module_id"))
        created_at = float(data.get("created_at", time.time()))

        restore_inputs_raw = data.get("restore_inputs", {})
        if not isinstance(restore_inputs_raw, dict):
            raise TypeError("restore_inputs is not an object")

        restore_inputs: dict[str, str] = {}
        for key, value in restore_inputs_raw.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise TypeError("restore_inputs contains non-string entries")
            restore_inputs[key] = value
    except (TypeError, ValueError):
        with contextlib.suppress(OSError):
            STATE_FILE.unlink()
        return None

    return MonoState(
        version=version,
        phase=phase,
        previous_default_sink=previous_default_sink,
        target_sink=target_sink,
        null_module_id=null_module_id,
        loopback_module_id=loopback_module_id,
        restore_inputs=restore_inputs,
        created_at=created_at,
    )


def clear_state() -> None:
    with contextlib.suppress(OSError):
        STATE_FILE.unlink()


# -----------------------------------------------------------------------------
# Locking
# -----------------------------------------------------------------------------


@contextlib.contextmanager
def instance_lock() -> Iterator[None]:
    try:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise MonoToggleError(f"Failed to open lock file: {exc}") from exc

    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise MonoToggleError("Another mono toggle instance is already running.") from exc
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)


# -----------------------------------------------------------------------------
# Parsing helpers
# -----------------------------------------------------------------------------


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def split_short_table_line(line: str, maxsplit: int) -> list[str]:
    if "\t" in line:
        return [part.strip() for part in line.split("\t", maxsplit)]
    return line.strip().split(None, maxsplit)


def unique_ids(values: Iterable[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def parse_module_args(module_args: str) -> dict[str, str]:
    if not module_args:
        return {}

    try:
        tokens = shlex.split(module_args, posix=True)
    except ValueError:
        return {}

    parsed: dict[str, str] = {}
    for token in tokens:
        key, sep, value = token.partition("=")
        if sep:
            parsed[key] = value
    return parsed


# -----------------------------------------------------------------------------
# pactl queries
# -----------------------------------------------------------------------------


def list_sinks() -> list[SinkRow]:
    output = require_success(
        run_command("pactl", "list", "sinks", "short"),
        "Failed to list sinks",
    )
    sinks: list[SinkRow] = []
    for line in output.splitlines():
        parts = split_short_table_line(line, 4)
        if len(parts) < 2:
            continue
        sink_id = _as_int(parts[0])
        sink_name = parts[1]
        if sink_id is not None and sink_name:
            sinks.append((sink_id, sink_name))
    return sinks


def list_sources() -> list[SourceRow]:
    output = require_success(
        run_command("pactl", "list", "sources", "short"),
        "Failed to list sources",
    )
    sources: list[SourceRow] = []
    for line in output.splitlines():
        parts = split_short_table_line(line, 4)
        if len(parts) < 2:
            continue
        source_id = _as_int(parts[0])
        source_name = parts[1]
        if source_id is not None and source_name:
            sources.append((source_id, source_name))
    return sources


def list_modules() -> list[ModuleRow]:
    output = require_success(
        run_command("pactl", "list", "modules", "short"),
        "Failed to list modules",
    )
    modules: list[ModuleRow] = []
    for line in output.splitlines():
        parts = split_short_table_line(line, 2)
        if len(parts) < 2:
            continue
        module_id = _as_int(parts[0])
        module_name = parts[1]
        module_args = parts[2] if len(parts) >= 3 else ""
        if module_id is not None and module_name:
            modules.append((module_id, module_name, module_args))
    return modules


def list_sink_inputs() -> list[SinkInputInfo]:
    output = require_success(
        run_command("pactl", "list", "sink-inputs"),
        "Failed to list sink inputs",
    )
    sink_inputs: list[SinkInputInfo] = []
    current: SinkInputInfo | None = None

    for line in output.splitlines():
        match = SINK_INPUT_HEADER_RE.match(line)
        if match:
            if current is not None:
                sink_inputs.append(current)
            current = SinkInputInfo(input_id=int(match.group(1)))
            continue

        if current is None:
            continue

        stripped = line.strip()
        if stripped.startswith("Owner Module:"):
            current.owner_module = _as_int(stripped.split(":", 1)[1].strip())
        elif stripped.startswith("Sink:"):
            current.sink_id = _as_int(stripped.split(":", 1)[1].strip())

    if current is not None:
        sink_inputs.append(current)

    return sink_inputs


def get_default_sink() -> str:
    result = run_command("pactl", "get-default-sink")
    if result.returncode != 0 or result.timed_out or result.error:
        return ""
    return result.stdout.strip()


def get_sink_id(sink_name: str) -> int | None:
    for sink_id, name in list_sinks():
        if name == sink_name:
            return sink_id
    return None


def sink_exists(sink_name: str) -> bool:
    return get_sink_id(sink_name) is not None


def source_exists(source_name: str) -> bool:
    return any(name == source_name for _, name in list_sources())


def discover_mono_modules() -> tuple[list[int], list[int]]:
    null_ids: list[int] = []
    loopback_ids: list[int] = []

    for module_id, module_name, module_args in list_modules():
        args = parse_module_args(module_args)
        if module_name == "module-null-sink" and args.get("sink_name") == MONO_SINK_NAME:
            null_ids.append(module_id)
        elif module_name == "module-loopback" and args.get("source") == MONO_MONITOR_NAME:
            loopback_ids.append(module_id)

    return null_ids, loopback_ids


def loopback_stream_exists(loopback_module_ids: set[int]) -> bool:
    if not loopback_module_ids:
        return False

    for sink_input in list_sink_inputs():
        owner_module = sink_input.owner_module
        if owner_module is not None and owner_module in loopback_module_ids:
            return True

    return False


def mono_artifacts_present() -> bool:
    mono_present = sink_exists(MONO_SINK_NAME)
    null_ids, loopback_ids = discover_mono_modules()
    return mono_present or bool(null_ids) or bool(loopback_ids)


def runtime_mono_status() -> tuple[bool, bool, list[int], list[int]]:
    mono_present = sink_exists(MONO_SINK_NAME)
    null_ids, loopback_ids = discover_mono_modules()
    stream_present = loopback_stream_exists(set(loopback_ids)) if loopback_ids else False

    active = mono_present and bool(null_ids) and bool(loopback_ids) and stream_present
    any_artifacts = mono_present or bool(null_ids) or bool(loopback_ids)

    return active, any_artifacts, null_ids, loopback_ids


# -----------------------------------------------------------------------------
# Wait helpers
# -----------------------------------------------------------------------------


def wait_until(predicate: Callable[[], bool], *, timeout: float, step: float = WAIT_STEP) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None

    while True:
        try:
            if predicate():
                return
            last_error = None
        except Exception as exc:
            last_error = exc

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WaitTimeoutError(last_error)

        time.sleep(min(step, remaining))


def wait_timeout_detail(exc: WaitTimeoutError) -> str:
    if exc.last_error is None:
        return ""
    detail = str(exc.last_error).strip()
    return f" ({detail})" if detail else ""


def wait_for_sink(sink_name: str) -> None:
    try:
        wait_until(lambda: sink_exists(sink_name), timeout=WAIT_TIMEOUT)
    except WaitTimeoutError as exc:
        cause = exc.last_error if exc.last_error is not None else exc
        raise MonoToggleError(
            f"Timed out waiting for sink: {sink_name}{wait_timeout_detail(exc)}"
        ) from cause


def wait_for_source(source_name: str) -> None:
    try:
        wait_until(lambda: source_exists(source_name), timeout=WAIT_TIMEOUT)
    except WaitTimeoutError as exc:
        cause = exc.last_error if exc.last_error is not None else exc
        raise MonoToggleError(
            f"Timed out waiting for source: {source_name}{wait_timeout_detail(exc)}"
        ) from cause


def wait_for_loopback_stream(loopback_module_id: int, target_sink: str) -> None:
    def ready() -> bool:
        target_sink_id = get_sink_id(target_sink)
        if target_sink_id is None:
            return False

        for sink_input in list_sink_inputs():
            if sink_input.owner_module == loopback_module_id and sink_input.sink_id == target_sink_id:
                return True
        return False

    try:
        wait_until(ready, timeout=WAIT_TIMEOUT)
    except WaitTimeoutError as exc:
        cause = exc.last_error if exc.last_error is not None else exc
        raise MonoToggleError(
            f"Timed out waiting for mono loopback stream to appear{wait_timeout_detail(exc)}"
        ) from cause


# -----------------------------------------------------------------------------
# Module operations
# -----------------------------------------------------------------------------


def load_module(module_name: str, *module_args: str) -> int:
    output = require_success(
        run_command("pactl", "load-module", module_name, *module_args, timeout=LOAD_TIMEOUT),
        f"Failed to load {module_name}",
    ).strip()

    module_id = _as_int(output)
    if module_id is None:
        raise MonoToggleError(f"Failed to load {module_name}: invalid module id: {output!r}")
    return module_id


def cleanup_mono_resources(
    *,
    extra_null_ids: Iterable[int] = (),
    extra_loopback_ids: Iterable[int] = (),
) -> None:
    discovered_null_ids, discovered_loopback_ids = discover_mono_modules()

    loopback_ids = unique_ids([*discovered_loopback_ids, *extra_loopback_ids])
    null_ids = unique_ids([*discovered_null_ids, *extra_null_ids])

    unload_errors: list[str] = []

    for module_id in loopback_ids:
        result = run_command("pactl", "unload-module", str(module_id))
        if result.returncode != 0 and not is_no_such_entity_error(result):
            unload_errors.append(f"{module_id}: {command_error(result)}")

    for module_id in null_ids:
        result = run_command("pactl", "unload-module", str(module_id))
        if result.returncode != 0 and not is_no_such_entity_error(result):
            unload_errors.append(f"{module_id}: {command_error(result)}")

    try:
        wait_until(lambda: not mono_artifacts_present(), timeout=2.0)
    except WaitTimeoutError as exc:
        detail = f" ({'; '.join(unload_errors)})" if unload_errors else wait_timeout_detail(exc)
        raise MonoToggleError(
            f"Timed out waiting for mono resources to disappear after unload{detail}"
        ) from (exc.last_error if exc.last_error is not None else exc)


# -----------------------------------------------------------------------------
# Sink-input movement
# -----------------------------------------------------------------------------


def is_moveable_application_input(
    sink_input: SinkInputInfo,
    *,
    skip_owner_modules: set[int],
) -> bool:
    if sink_input.owner_module is not None and sink_input.owner_module in skip_owner_modules:
        return False

    if sink_input.owner_module is None:
        return True

    return sink_input.owner_module in PULSEAPP_OWNER_MODULE_IDS


def capture_restore_inputs() -> dict[str, str]:
    sink_name_by_id = {sink_id: sink_name for sink_id, sink_name in list_sinks()}

    restore_map: dict[str, str] = {}
    for sink_input in list_sink_inputs():
        if not is_moveable_application_input(sink_input, skip_owner_modules=set()):
            continue
        if sink_input.sink_id is None:
            continue

        sink_name = sink_name_by_id.get(sink_input.sink_id, "")
        if sink_name and sink_name != MONO_SINK_NAME:
            restore_map[str(sink_input.input_id)] = sink_name

    return restore_map


def move_application_inputs_to_sink(
    target_sink: str,
    *,
    skip_owner_modules: set[int],
) -> int:
    target_sink_id = get_sink_id(target_sink)
    if target_sink_id is None:
        raise MonoToggleError(f"Target sink not found: {target_sink}")

    candidates: list[int] = []
    attempt_errors: dict[int, str] = {}

    for sink_input in list_sink_inputs():
        if not is_moveable_application_input(sink_input, skip_owner_modules=skip_owner_modules):
            continue
        if sink_input.sink_id == target_sink_id:
            continue

        candidates.append(sink_input.input_id)

        result = run_command(
            "pactl",
            "move-sink-input",
            str(sink_input.input_id),
            target_sink,
            timeout=3.0,
        )
        if result.returncode != 0 and not is_no_such_entity_error(result):
            attempt_errors[sink_input.input_id] = command_error(result)

    current_target_sink_id = get_sink_id(target_sink)
    if current_target_sink_id is None:
        raise MonoToggleError(f"Target sink vanished: {target_sink}")

    current_inputs = {sink_input.input_id: sink_input for sink_input in list_sink_inputs()}

    failed_ids: list[str] = []
    moved = 0

    for input_id in candidates:
        current = current_inputs.get(input_id)
        if current is None:
            continue
        if current.sink_id == current_target_sink_id:
            moved += 1
            continue

        error_text = attempt_errors.get(input_id)
        failed_ids.append(f"{input_id} ({error_text})" if error_text else str(input_id))

    if failed_ids:
        raise MonoToggleError(
            "Failed to move application sink inputs to mono sink: " + ", ".join(failed_ids)
        )

    return moved


def restore_inputs_from_mono(
    restore_inputs: dict[str, str],
    *,
    fallback_sink: str | None,
    skip_owner_modules: set[int],
) -> int:
    mono_sink_id = get_sink_id(MONO_SINK_NAME)
    if mono_sink_id is None:
        return 0

    available_sinks = {name for _, name in list_sinks()}
    fallback_available = (
        fallback_sink is not None
        and fallback_sink != MONO_SINK_NAME
        and fallback_sink in available_sinks
    )

    moved = 0

    for sink_input in list_sink_inputs():
        if sink_input.sink_id != mono_sink_id:
            continue
        if not is_moveable_application_input(sink_input, skip_owner_modules=skip_owner_modules):
            continue

        preferred_sink = restore_inputs.get(str(sink_input.input_id), "")
        target_sink = ""

        if preferred_sink and preferred_sink != MONO_SINK_NAME and preferred_sink in available_sinks:
            target_sink = preferred_sink
        elif fallback_available and fallback_sink is not None:
            target_sink = fallback_sink

        if not target_sink:
            continue

        result = run_command(
            "pactl",
            "move-sink-input",
            str(sink_input.input_id),
            target_sink,
            timeout=3.0,
        )
        if result.returncode == 0:
            moved += 1

    return moved


# -----------------------------------------------------------------------------
# Sink selection
# -----------------------------------------------------------------------------


def choose_initial_target_sink() -> str:
    available_sinks = [sink_name for _, sink_name in list_sinks() if sink_name != MONO_SINK_NAME]
    if not available_sinks:
        raise MonoToggleError("No audio output sink is available")

    default_sink = get_default_sink()
    if default_sink and default_sink in available_sinks:
        return default_sink

    return available_sinks[0]


def choose_restore_sink(state: MonoState | None) -> str | None:
    available_sinks = [sink_name for _, sink_name in list_sinks() if sink_name != MONO_SINK_NAME]
    available_set = set(available_sinks)

    candidates: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if not name or name == MONO_SINK_NAME or name in seen:
            return
        seen.add(name)
        candidates.append(name)

    if state is not None:
        add(state.previous_default_sink)
        add(state.target_sink)

    add(get_default_sink())

    for sink_name in available_sinks:
        add(sink_name)

    for sink_name in candidates:
        if sink_name in available_set:
            return sink_name

    return None


# -----------------------------------------------------------------------------
# Notifications
# -----------------------------------------------------------------------------


def notify(summary: str, body: str, *, urgency: str = "low", timeout_ms: int = NOTIFY_TIMEOUT_MS) -> None:
    if shutil.which("notify-send") is None:
        return

    run_command(
        "notify-send",
        "-u",
        urgency,
        "-t",
        str(timeout_ms),
        summary,
        body,
        timeout=2.0,
        force_c_locale=False,
    )


# -----------------------------------------------------------------------------
# State detection
# -----------------------------------------------------------------------------


def normalize_stale_state() -> MonoState | None:
    state = load_state()
    active, any_artifacts, null_ids, loopback_ids = runtime_mono_status()

    if active:
        if state is not None:
            state_matches_runtime = True

            if state.null_module_id is not None and state.null_module_id not in null_ids:
                state_matches_runtime = False
            if state.loopback_module_id is not None and state.loopback_module_id not in loopback_ids:
                state_matches_runtime = False

            if not state_matches_runtime:
                clear_state()
                state = None
            elif state.phase is Phase.ENABLING:
                state.phase = Phase.ACTIVE
                with contextlib.suppress(MonoToggleError):
                    write_state(state)

        set_indicator_state(True)
        return state

    if not any_artifacts:
        if state is not None:
            clear_state()
        set_indicator_state(False)
        return None

    cleanup_mono_resources()
    clear_state()
    set_indicator_state(False)
    return None


def mono_is_active() -> tuple[bool, MonoState | None]:
    state = normalize_stale_state()
    active, _, _, _ = runtime_mono_status()
    return active, state


# -----------------------------------------------------------------------------
# Core operations
# -----------------------------------------------------------------------------


def rollback_failed_enable(state: MonoState) -> None:
    restore_sink = choose_restore_sink(state)
    skip_modules: set[int] = set()
    if state.loopback_module_id is not None:
        skip_modules.add(state.loopback_module_id)

    if restore_sink:
        run_command("pactl", "set-default-sink", restore_sink, timeout=3.0)
        restore_inputs_from_mono(
            state.restore_inputs,
            fallback_sink=restore_sink,
            skip_owner_modules=skip_modules,
        )

    cleanup_mono_resources(
        extra_null_ids=[state.null_module_id] if state.null_module_id is not None else [],
        extra_loopback_ids=[state.loopback_module_id] if state.loopback_module_id is not None else [],
    )
    clear_state()
    set_indicator_state(False)


def enable_mono() -> None:
    active, _ = mono_is_active()
    if active:
        return

    target_sink = choose_initial_target_sink()
    state = MonoState(
        phase=Phase.ENABLING,
        previous_default_sink=target_sink,
        target_sink=target_sink,
        restore_inputs=capture_restore_inputs(),
    )
    write_state(state)

    try:
        state.null_module_id = load_module(
            "module-null-sink",
            f"sink_name={MONO_SINK_NAME}",
            "sink_properties=device.description=Mono_Global_Downmix",
            "channels=1",
            "channel_map=mono",
        )
        write_state(state)

        wait_for_sink(MONO_SINK_NAME)
        wait_for_source(MONO_MONITOR_NAME)

        state.loopback_module_id = load_module(
            "module-loopback",
            f"source={MONO_MONITOR_NAME}",
            f"sink={target_sink}",
            "source_dont_move=true",
            "sink_dont_move=true",
            "latency_msec=10",
        )
        write_state(state)

        wait_for_loopback_stream(state.loopback_module_id, target_sink)

        require_success(
            run_command("pactl", "set-default-sink", MONO_SINK_NAME, timeout=3.0),
            "Failed to set mono sink as default",
        )

        move_application_inputs_to_sink(
            MONO_SINK_NAME,
            skip_owner_modules={state.loopback_module_id},
        )

        state.phase = Phase.ACTIVE
        write_state(state)
        set_indicator_state(True)
        notify("Audio", "Switched to Mono 🔊")
    except Exception:
        with contextlib.suppress(Exception):
            rollback_failed_enable(state)
        raise


def disable_mono() -> None:
    active, state = mono_is_active()
    if not active:
        clear_state()
        set_indicator_state(False)
        return

    _, discovered_loopback_ids = discover_mono_modules()
    loopback_skip_modules = set(discovered_loopback_ids)

    restore_sink = choose_restore_sink(state)

    if restore_sink:
        require_success(
            run_command("pactl", "set-default-sink", restore_sink, timeout=3.0),
            f"Failed to restore default sink to {restore_sink}",
        )

        restore_inputs_from_mono(
            state.restore_inputs if state is not None else {},
            fallback_sink=restore_sink,
            skip_owner_modules=loopback_skip_modules,
        )

    cleanup_mono_resources()
    clear_state()
    set_indicator_state(False)

    if not restore_sink:
        raise MonoToggleError("Mono resources were removed, but no non-mono sink was available to restore")

    notify("Audio", "Switched to Stereo 🎧")


def status_text() -> str:
    active, state = mono_is_active()
    null_ids, loopback_ids = discover_mono_modules()

    if not active:
        return "disabled"

    parts = ["enabled"]
    if state is not None and state.previous_default_sink:
        parts.append(f"restore={state.previous_default_sink}")
    if null_ids:
        parts.append(f"null_module={null_ids[0]}")
    if loopback_ids:
        parts.append(f"loopback_module={loopback_ids[0]}")
    return " ".join(parts)


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Toggle system-wide mono output for PipeWire on Arch Linux.",
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=("toggle", "enable", "disable", "status"),
        default="toggle",
    )
    return parser.parse_args(argv)


def main() -> int:
    try:
        args = parse_args(sys.argv[1:])
        ensure_runtime_dir()
        ensure_dependencies()
        ensure_audio_server()

        with instance_lock():
            match args.action:
                case "toggle":
                    active, _ = mono_is_active()
                    if active:
                        disable_mono()
                    else:
                        enable_mono()
                case "enable":
                    enable_mono()
                case "disable":
                    disable_mono()
                case "status":
                    print(status_text())

        return 0
    except MonoToggleError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        notify("Audio Error", str(exc), urgency="critical")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
