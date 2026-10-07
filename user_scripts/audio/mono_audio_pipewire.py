#!/usr/bin/env python3
"""
System-wide mono toggle for PipeWire + pipewire-pulse on Arch Linux.

Audio path when enabled:
    application sink-inputs -> stereo null sink -> mono loopback -> previous default sink

Key properties:
    - single-instance lock
    - atomic JSON state file in XDG_RUNTIME_DIR
    - exact module ID tracking
    - routing restoration before stale resource cleanup
    - read-only status
    - readiness waits for sink, monitor source, and loopback stream
    - rollback on failure
"""

import argparse
import contextlib
import fcntl
import json
import os
lazy import shlex
import shutil
import subprocess
import sys
lazy import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

MONO_SINK_NAME = "mono_global_downmix"
MONO_MONITOR_NAME = f"{MONO_SINK_NAME}.monitor"

RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
STATE_FILE = RUNTIME_DIR / f"mono_audio_state_{os.getuid()}"
LOCK_FILE = RUNTIME_DIR / f"mono_audio_lock_{os.getuid()}"

INDICATOR_FILE = (
    Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    / "dusky/settings/mono_audio"
)

STATE_VERSION = 1

CMD_TIMEOUT = 5.0
LOAD_TIMEOUT = 10.0
WAIT_TIMEOUT = 5.0
WAIT_STEP = 0.05

NOTIFY_TIMEOUT_MS = 2000

PULSEAPP_OWNER_MODULE_IDS = {4294967295, 18446744073709551615}

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
    flags = os.O_RDONLY | os.O_DIRECTORY

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
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            os.fchmod(handle.fileno(), mode)
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
    except (OSError, UnicodeDecodeError, ValueError, TypeError):
        return None

    try:
        version = int(data.get("version", STATE_VERSION))
        if version != STATE_VERSION:
            raise ValueError(f"unsupported state version: {version}")

        phase = Phase(data.get("phase", Phase.ACTIVE.value))
        previous_default_sink = data.get("previous_default_sink", "")
        target_sink = data.get("target_sink", "")
        if not isinstance(previous_default_sink, str) or not isinstance(target_sink, str):
            raise TypeError("state sink names must be strings")
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
    except (TypeError, ValueError, OverflowError):
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
    try:
        STATE_FILE.unlink(missing_ok=True)
    except OSError as exc:
        raise MonoToggleError(f"Failed to remove state file: {exc}") from exc


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
        except BlockingIOError as exc:
            raise MonoToggleError("Another mono toggle instance is already running.") from exc
        except OSError as exc:
            raise MonoToggleError(f"Failed to acquire mono toggle lock: {exc}") from exc
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
    except (TypeError, ValueError, OverflowError):
        return None


def split_short_table_line(line: str, maxsplit: int) -> list[str]:
    if "\t" in line:
        return [part.strip() for part in line.split("\t", maxsplit)]
    return line.strip().split(None, maxsplit)


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
        run_command("pactl", "list", "short", "sinks"),
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
        run_command("pactl", "list", "short", "sources"),
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
        run_command("pactl", "list", "short", "modules"),
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
        run_command("pactl", "--format=json", "list", "sink-inputs"),
        "Failed to list sink inputs",
    )
    try:
        rows = json.loads(output)
        if not isinstance(rows, list):
            raise ValueError("expected an array of objects")
        return [
            SinkInputInfo(row["index"], row["sink"], _as_int(row.get("owner_module")))
            for row in rows
        ]
    except (ValueError, TypeError, KeyError) as exc:
        raise MonoToggleError(f"Invalid pactl sink-inputs JSON: {exc}") from exc


def get_default_sink() -> str:
    result = run_command("pactl", "get-default-sink")
    if result.returncode != 0 or result.timed_out or result.error:
        return ""
    return result.stdout.strip()


def set_default_sink(sink_name: str) -> None:
    require_success(
        run_command("pactl", "set-default-sink", sink_name, timeout=3.0),
        f"Failed to set default sink to {sink_name}",
    )
    # The configured default is acknowledged before WirePlumber selects the effective one.
    try:
        wait_until(lambda: get_default_sink() == sink_name, timeout=WAIT_TIMEOUT)
    except WaitTimeoutError as exc:
        raise MonoToggleError(f"Timed out waiting for default sink: {sink_name}") from exc


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
        if module_name not in {"module-null-sink", "module-loopback"}:
            continue
        args = parse_module_args(module_args)
        if module_name == "module-null-sink" and args.get("sink_name") == MONO_SINK_NAME:
            null_ids.append(module_id)
        elif module_name == "module-loopback" and args.get("source") == MONO_MONITOR_NAME:
            loopback_ids.append(module_id)

    return null_ids, loopback_ids


def mono_artifacts_present() -> bool:
    mono_present = sink_exists(MONO_SINK_NAME)
    null_ids, loopback_ids = discover_mono_modules()
    return mono_present or bool(null_ids) or bool(loopback_ids)


def runtime_mono_status() -> tuple[bool, bool, list[int], list[int]]:
    sinks = dict(list_sinks())
    mono_sink_id = next((index for index, name in sinks.items() if name == MONO_SINK_NAME), None)
    null_ids, loopback_ids = discover_mono_modules()
    stream_present = bool(loopback_ids) and any(
        stream.owner_module in loopback_ids
        and stream.sink_id in sinks
        and stream.sink_id != mono_sink_id
        for stream in list_sink_inputs()
    )

    active = mono_sink_id is not None and bool(null_ids) and stream_present
    any_artifacts = mono_sink_id is not None or bool(null_ids) or bool(loopback_ids)
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
        except MonoToggleError as exc:
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


def cleanup_mono_resources() -> None:
    # Discover ownership now: saved numeric IDs can be reused after a server restart.
    null_ids, loopback_ids = discover_mono_modules()

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


def verify_input_moves(
    targets: dict[int, str],
    attempt_errors: dict[int, str],
    *,
    context: str,
) -> int:
    if not targets:
        return 0

    moved = 0
    settled = 0
    failures: list[str] = []

    def ready() -> bool:
        nonlocal moved, settled
        sinks = dict(list_sinks())
        inputs = {stream.input_id: stream for stream in list_sink_inputs()}
        moved = 0
        failures.clear()
        for input_id, target in targets.items():
            current = inputs.get(input_id)
            if current is None:
                continue  # The application closed while moving.
            if sinks.get(current.sink_id) == target:
                moved += 1
            else:
                # A pending WirePlumber update can override an acknowledged move.
                # Reapply only accepted requests, bounded by the readiness deadline.
                if input_id not in attempt_errors:
                    result = run_command(
                        "pactl", "move-sink-input", str(input_id), target, timeout=3.0,
                    )
                    if result.returncode != 0 and not is_no_such_entity_error(result):
                        attempt_errors[input_id] = command_error(result)
                detail = attempt_errors.get(input_id, "routing has not settled")
                failures.append(f"{input_id} -> {target}: {detail}")
        settled = settled + 1 if not failures else 0
        return settled >= 2

    # Confirm consecutive snapshots before unloading the sink: pending policy updates
    # can arrive after pactl first publishes the requested route.
    try:
        wait_until(ready, timeout=WAIT_TIMEOUT)
    except WaitTimeoutError as exc:
        detail = "; ".join(failures) or "routing could not be verified"
        raise MonoToggleError(f"Failed to {context}: {detail}{wait_timeout_detail(exc)}") from exc
    return moved


def move_application_inputs_to_sink(
    target_sink: str,
    *,
    skip_owner_modules: set[int],
) -> int:
    target_sink_id = get_sink_id(target_sink)
    if target_sink_id is None:
        raise MonoToggleError(f"Target sink not found: {target_sink}")

    targets: dict[int, str] = {}
    attempt_errors: dict[int, str] = {}

    for sink_input in list_sink_inputs():
        if not is_moveable_application_input(sink_input, skip_owner_modules=skip_owner_modules):
            continue
        if sink_input.sink_id == target_sink_id:
            continue

        targets[sink_input.input_id] = target_sink

        result = run_command(
            "pactl",
            "move-sink-input",
            str(sink_input.input_id),
            target_sink,
            timeout=3.0,
        )
        if result.returncode != 0 and not is_no_such_entity_error(result):
            attempt_errors[sink_input.input_id] = command_error(result)

    return verify_input_moves(
        targets, attempt_errors, context=f"move application streams to {target_sink}"
    )


def restore_audio_routing(
    restore_inputs: dict[str, str],
    *,
    fallback_sink: str,
    skip_owner_modules: set[int],
) -> int:
    mono_sink_id = get_sink_id(MONO_SINK_NAME)

    available_sinks = {name for _, name in list_sinks()}
    fallback_available = (
        fallback_sink != MONO_SINK_NAME
        and fallback_sink in available_sinks
    )

    targets: dict[int, str] = {}
    attempt_errors: dict[int, str] = {}

    for sink_input in list_sink_inputs():
        if (
            (mono_sink_id is None or sink_input.sink_id != mono_sink_id)
            and str(sink_input.input_id) not in restore_inputs
        ):
            continue
        if not is_moveable_application_input(sink_input, skip_owner_modules=skip_owner_modules):
            continue

        preferred_sink = restore_inputs.get(str(sink_input.input_id), "")
        target_sink = ""

        if preferred_sink and preferred_sink != MONO_SINK_NAME and preferred_sink in available_sinks:
            target_sink = preferred_sink
        elif fallback_available:
            target_sink = fallback_sink

        if not target_sink:
            raise MonoToggleError(f"No output sink available to restore stream {sink_input.input_id}")

        targets[sink_input.input_id] = target_sink

    # Snapshot destinations before changing the default: WirePlumber may move streams
    # automatically. Saved streams are included even on retry after such a move.
    # Then restore explicit routes against the new default.
    set_default_sink(fallback_sink)
    for input_id, target_sink in targets.items():
        result = run_command(
            "pactl", "move-sink-input", str(input_id), target_sink, timeout=3.0,
        )
        if result.returncode != 0 and not is_no_such_entity_error(result):
            attempt_errors[input_id] = command_error(result)

    return verify_input_moves(targets, attempt_errors, context="restore application streams")


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
    available_sinks = [name for _, name in list_sinks() if name != MONO_SINK_NAME]
    candidates = [state.previous_default_sink, state.target_sink] if state is not None else []
    candidates.append(get_default_sink())
    for name in candidates:
        if name in available_sinks:
            return name

    # Recover the original output from the live loopback when the state file was lost.
    for _, module_name, module_args in list_modules():
        if module_name != "module-loopback":
            continue
        args = parse_module_args(module_args)
        if args.get("source") == MONO_MONITOR_NAME and args.get("sink") in available_sinks:
            return args["sink"]
    return available_sinks[0] if available_sinks else None


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


def mono_is_active() -> tuple[bool, MonoState | None]:
    """Normalize stale resources once, only for operations that change audio."""
    state = load_state()
    active, any_artifacts, null_ids, loopback_ids = runtime_mono_status()
    if state is not None and (
        (null_ids and state.null_module_id is not None and state.null_module_id not in null_ids)
        or (
            loopback_ids
            and state.loopback_module_id is not None
            and state.loopback_module_id not in loopback_ids
        )
    ):
        state = None  # The server restarted or these resources belong to another run.

    if any_artifacts and (not active or (state is not None and state.phase is Phase.ENABLING)):
        rollback_failed_enable(state or MonoState())
        return False, None

    if not any_artifacts:
        state = None
    if state is None:
        clear_state()
    set_indicator_state(active)
    return active, state


# -----------------------------------------------------------------------------
# Core operations
# -----------------------------------------------------------------------------


def rollback_failed_enable(state: MonoState) -> None:
    errors: list[str] = []
    restore_sink = None
    try:
        restore_sink = choose_restore_sink(state)
        if restore_sink:
            _, loopback_ids = discover_mono_modules()
            restore_audio_routing(
                state.restore_inputs,
                fallback_sink=restore_sink,
                skip_owner_modules=set(loopback_ids),
            )
    except MonoToggleError as exc:
        errors.append(str(exc))

    # Attempt cleanup even when routing restoration failed. Retain state if unload fails.
    try:
        cleanup_mono_resources()
    except MonoToggleError as exc:
        raise MonoToggleError("; ".join([*errors, str(exc)])) from exc
    clear_state()
    set_indicator_state(False)
    if errors:
        raise MonoToggleError("Rollback routing failed: " + "; ".join(errors))


def enable_mono() -> None:
    target_sink = choose_initial_target_sink()
    state = MonoState(
        phase=Phase.ENABLING,
        previous_default_sink=target_sink,
        target_sink=target_sink,
        restore_inputs=capture_restore_inputs(),
    )
    write_state(state)

    try:
        # Keep application ports stereo; downmix in the loopback so newly opened
        # streams can return to stereo without changing their output channel count.
        state.null_module_id = load_module(
            "module-null-sink",
            f"sink_name={MONO_SINK_NAME}",
            "sink_properties=device.description=Mono_Global_Downmix",
            "channels=2",
            "channel_map=front-left,front-right",
        )
        write_state(state)

        wait_for_sink(MONO_SINK_NAME)
        wait_for_source(MONO_MONITOR_NAME)

        state.loopback_module_id = load_module(
            "module-loopback",
            f"source={MONO_MONITOR_NAME}",
            f"sink={shlex.quote(target_sink)}",
            "channels=1",
            "channel_map=mono",
            "source_dont_move=true",
            "sink_dont_move=true",
            "latency_msec=10",
        )
        write_state(state)

        wait_for_loopback_stream(state.loopback_module_id, target_sink)

        set_default_sink(MONO_SINK_NAME)

        move_application_inputs_to_sink(
            MONO_SINK_NAME,
            skip_owner_modules={state.loopback_module_id},
        )

        state.phase = Phase.ACTIVE
        write_state(state)
        set_indicator_state(True)
        notify("Audio", "Switched to Mono 🔊")
    except (Exception, KeyboardInterrupt) as exc:
        try:
            rollback_failed_enable(state)
        except MonoToggleError as rollback_error:
            raise MonoToggleError(f"Enable failed: {exc}; rollback failed: {rollback_error}") from exc
        raise


def disable_mono(active: bool, state: MonoState | None) -> None:
    if not active:
        clear_state()
        set_indicator_state(False)
        return

    _, discovered_loopback_ids = discover_mono_modules()
    loopback_skip_modules = set(discovered_loopback_ids)

    restore_sink = choose_restore_sink(state)

    if restore_sink:
        restore_audio_routing(
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
    active, any_artifacts, null_ids, loopback_ids = runtime_mono_status()
    state = load_state()

    if not active or (state is not None and state.phase is Phase.ENABLING):
        return "incomplete" if any_artifacts else "disabled"

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
            if args.action == "status":
                print(status_text())
            else:
                active, state = mono_is_active()
                action = args.action
                if action == "toggle":
                    action = "disable" if active else "enable"
                if action == "disable":
                    disable_mono(active, state)
                elif not active:
                    enable_mono()

        return 0
    except MonoToggleError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        notify("Audio Error", str(exc), urgency="critical")
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
