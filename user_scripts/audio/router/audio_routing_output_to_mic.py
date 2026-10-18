#!/usr/bin/env python3
"""Route application audio output to a virtual microphone via PipeWire.

Architecture : Arch Linux / Wayland / Hyprland
Ecosystem    : PipeWire 1.4+ / WirePlumber
Python       : 3.15+

Usage:
    audio_routing_output_to_mic.py                     # GUI popup (control / start)
    audio_routing_output_to_mic.py --daemon [APP]      # foreground routing daemon
    audio_routing_output_to_mic.py --status            # print daemon status
    audio_routing_output_to_mic.py --stop              # stop running daemon
    audio_routing_output_to_mic.py --waybar            # output Waybar JSON status
"""

import argparse
import asyncio
import contextlib
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

lazy import shlex
lazy import shutil

# ──────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────

VIRT_NODE_NAME = "Virtual_Mic_Tx"
SOCK_NAME = "audio_router.sock"
LOCK_NAME = "audio_router.lock"
DEFAULT_APP = "mpv"

PW_DUMP_TIMEOUT = 5.0
LINK_TIMEOUT = 2.0
POLL_INTERVAL = 0.5
INIT_LINK_MAX_AGE = 3.0
MAX_RETRY_DELAY = 5.0
MAX_CONCURRENT_LINKS = 4
MODULE_WAIT_TIMEOUT = 5.0
MODULE_WAIT_STEP = 0.1

APP_MATCH_KEYS: tuple[str, ...] = (
    "application.name",
    "application.process.binary",
    "node.name",
)

type GraphObject = dict[str, Any]
type PortPair = tuple[int, int]
type ChannelMap = dict[str, int]


# ──────────────────────────────────────────────────────────────────
# Exceptions / enums
# ──────────────────────────────────────────────────────────────────

class StartupError(RuntimeError):
    """Fatal daemon startup or recovery error."""


class DaemonPhase(StrEnum):
    STARTING = "starting"
    READY = "ready"
    RECOVERING = "recovering"
    STOPPING = "stopping"


# ──────────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class StreamInfo:
    node_id: int
    state: str
    app_name: str
    media_name: str
    ports: ChannelMap


@dataclass(slots=True)
class TrackedLink:
    out_port: int
    in_port: int
    node_id: int
    created_at: float
    last_state: str = "unknown"


@dataclass(slots=True)
class ProcResult:
    returncode: int | None
    stdout: bytes = b""
    stderr: bytes = b""
    timed_out: bool = False
    error: str = ""


@dataclass
class DaemonState:
    target_app: str = DEFAULT_APP
    phase: DaemonPhase = DaemonPhase.STARTING
    virt_module_id: str | None = None
    our_links: dict[PortPair, TrackedLink] = field(default_factory=dict)
    failed_pairs: dict[PortPair, float] = field(default_factory=dict)
    graph: list[GraphObject] = field(default_factory=list)
    shutdown_event: asyncio.Event = field(default_factory=asyncio.Event)
    stopped_event: asyncio.Event = field(default_factory=asyncio.Event)
    cleanup_ok: bool = False
    lock_fd: int | None = None
    virt_missing_count: int = 0


@dataclass(slots=True)
class GraphSnapshot:
    streams: dict[int, StreamInfo]
    virt_node_id: int | None
    virt_ports: ChannelMap
    graph_links: dict[PortPair, str]


# ──────────────────────────────────────────────────────────────────
# Path helpers
# ──────────────────────────────────────────────────────────────────

def _runtime_dir() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime)


def _sock_path() -> Path:
    return _runtime_dir() / SOCK_NAME


def _lock_path() -> Path:
    return _runtime_dir() / LOCK_NAME


def _script_path() -> Path:
    return Path(__file__).resolve()


# ──────────────────────────────────────────────────────────────────
# Generic helpers
# ──────────────────────────────────────────────────────────────────

def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decode_output(data: bytes) -> str:
    return data.decode(errors="replace").strip()


def _proc_error_message(result: ProcResult) -> str:
    if result.error:
        return result.error
    if result.timed_out:
        return "timeout"
    message = _decode_output(result.stderr) or _decode_output(result.stdout)
    if message:
        return message
    if result.returncode is None:
        return "unknown error"
    return f"exit status {result.returncode}"


async def run_command(
    *args: str,
    timeout: float,
    stdout: int | None = subprocess.PIPE,
    stderr: int | None = subprocess.PIPE,
) -> ProcResult:
    """Run a subprocess safely and kill it on timeout."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=stdout,
            stderr=stderr,
        )
    except OSError as exc:
        return ProcResult(returncode=None, error=str(exc))

    try:
        async with asyncio.timeout(timeout):
            out, err = await proc.communicate()
        return ProcResult(
            returncode=proc.returncode,
            stdout=out or b"",
            stderr=err or b"",
        )
    except (TimeoutError, asyncio.CancelledError) as exc:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.communicate()
        if isinstance(exc, asyncio.CancelledError):
            raise
        return ProcResult(returncode=proc.returncode, timed_out=True)


# ──────────────────────────────────────────────────────────────────
# Instance lock
# ──────────────────────────────────────────────────────────────────

def acquire_instance_lock(ds: DaemonState) -> None:
    """Acquire the singleton daemon lock."""
    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        raise StartupError("Another daemon instance is already running.") from exc

    ds.lock_fd = fd


def release_instance_lock(ds: DaemonState) -> None:
    """Release the singleton daemon lock."""
    if ds.lock_fd is None:
        return
    with contextlib.suppress(OSError):
        fcntl.flock(ds.lock_fd, fcntl.LOCK_UN)
    with contextlib.suppress(OSError):
        os.close(ds.lock_fd)
    ds.lock_fd = None


# ──────────────────────────────────────────────────────────────────
# PipeWire graph parsing
# ──────────────────────────────────────────────────────────────────

async def async_pw_dump() -> list[GraphObject]:
    """Capture the live PipeWire object graph asynchronously."""
    result = await run_command(
        "pw-dump",
        "--no-colors",
        timeout=PW_DUMP_TIMEOUT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    if result.timed_out or result.error or result.returncode not in (0, None):
        if result.timed_out or result.error:
            print(
                f"Warning: pw-dump failed: {_proc_error_message(result)}",
                file=sys.stderr,
            )
        return []

    try:
        graph = json.loads(result.stdout)
        return graph if isinstance(graph, list) else []
    except json.JSONDecodeError as exc:
        print(f"Warning: pw-dump returned invalid JSON: {exc}", file=sys.stderr)
        return []


def sync_pw_dump() -> list[GraphObject]:
    """Synchronous pw-dump for non-async contexts."""
    try:
        result = subprocess.run(
            ["pw-dump", "--no-colors"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=PW_DUMP_TIMEOUT,
            check=False,
        )
        if result.returncode != 0 or not result.stdout:
            return []
        graph = json.loads(result.stdout)
        return graph if isinstance(graph, list) else []
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        return []


def _extract_channel(props: GraphObject) -> str:
    ch = props.get("audio.channel")
    if isinstance(ch, str) and ch:
        return ch.upper()

    name = props.get("port.name", "")
    if isinstance(name, str):
        upper = name.upper().rsplit("_", 1)[-1]
        if upper == "FL":
            return "FL"
        if upper == "FR":
            return "FR"
        if upper == "MONO":
            return "MONO"
    return ""


def _matches_target(props: GraphObject, target: str) -> bool:
    """Case-insensitive exact match on known application identity keys."""
    target_lower = target.lower()
    for key in APP_MATCH_KEYS:
        value = props.get(key)
        if isinstance(value, str) and value.lower() == target_lower:
            return True
    return False


def _node_state(obj: GraphObject) -> str:
    """Extract the node state string from a pw-dump object."""
    state_raw = obj.get("info", {}).get("state")
    if isinstance(state_raw, str):
        return state_raw.lower()

    fallback = obj.get("info", {}).get("props", {}).get("node.state", "unknown")
    return fallback.lower() if isinstance(fallback, str) else "unknown"


def build_snapshot(graph: list[GraphObject], target: str) -> GraphSnapshot:
    """Parse a pw-dump graph into a structured snapshot."""
    streams: dict[int, StreamInfo] = {}
    virt_node_id: int | None = None
    virt_ports: ChannelMap = {}
    graph_links: dict[PortPair, str] = {}

    # Pass 1: identify relevant nodes
    for obj in graph:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue

        props = obj.get("info", {}).get("props", {})
        node_id = _as_int(obj.get("id"))
        if node_id is None:
            continue

        if props.get("node.name") == VIRT_NODE_NAME:
            virt_node_id = node_id
            continue

        if props.get("media.class") != "Stream/Output/Audio":
            continue

        if _matches_target(props, target):
            app_name = props.get("application.name")
            media_name = props.get("media.name")
            streams[node_id] = StreamInfo(
                node_id=node_id,
                state=_node_state(obj),
                app_name=app_name if isinstance(app_name, str) and app_name else "unknown",
                media_name=media_name if isinstance(media_name, str) else "",
                ports={},
            )

    # Pass 2: collect ports
    for obj in graph:
        if obj.get("type") != "PipeWire:Interface:Port":
            continue

        info = obj.get("info", {})
        props = info.get("props", {})
        parent = _as_int(props.get("node.id"))
        port_id = _as_int(obj.get("id"))
        if parent is None or port_id is None:
            continue

        direction = str(info.get("direction", "")).lower()
        channel = _extract_channel(props)

        if parent == virt_node_id and direction == "input" and channel:
            virt_ports[channel] = port_id
        elif parent in streams and direction == "output" and channel:
            streams[parent].ports[channel] = port_id

    # Pass 3: collect links
    for obj in graph:
        if obj.get("type") != "PipeWire:Interface:Link":
            continue

        info = obj.get("info", {})
        out_port = _as_int(info.get("output-port-id"))
        in_port = _as_int(info.get("input-port-id"))
        if out_port is None or in_port is None:
            continue

        state = str(info.get("state", "unknown")).lower()
        graph_links[(out_port, in_port)] = state

    return GraphSnapshot(
        streams=streams,
        virt_node_id=virt_node_id,
        virt_ports=virt_ports,
        graph_links=graph_links,
    )


def discover_audio_apps(graph: list[GraphObject]) -> list[str]:
    """Find all unique application names with audio output streams."""
    apps: set[str] = set()

    for obj in graph:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue

        props = obj.get("info", {}).get("props", {})
        if props.get("media.class") != "Stream/Output/Audio":
            continue

        name = props.get("application.name")
        if isinstance(name, str) and name:
            apps.add(name)

    return sorted(apps, key=str.lower)


# ──────────────────────────────────────────────────────────────────
# Link management
# ──────────────────────────────────────────────────────────────────

async def create_link(out_port: int, in_port: int) -> str:
    """Create a PipeWire link. Returns an error message, or empty on success."""
    result = await run_command(
        "pw-link",
        str(out_port),
        str(in_port),
        timeout=LINK_TIMEOUT,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    if result.timed_out:
        return "timeout"
    if result.error:
        return result.error

    stderr_text = _decode_output(result.stderr)
    if "file exists" in stderr_text.lower():
        return ""

    return "" if result.returncode == 0 else stderr_text or _proc_error_message(result)


async def destroy_link(out_port: int, in_port: int) -> str:
    """Disconnect ports; retain ownership until success or graph disappearance."""
    result = await run_command(
        "pw-link", "--disconnect", str(out_port), str(in_port), timeout=LINK_TIMEOUT,
    )
    return "" if result.returncode == 0 else _proc_error_message(result)


def compute_desired_links(snap: GraphSnapshot) -> dict[PortPair, int]:
    """Map the snapshot's matching playback channels to the virtual source."""
    desired: dict[PortPair, int] = {}
    if not snap.virt_ports:
        return desired

    for node_id, stream in snap.streams.items():
        if not stream.ports:
            continue

        if len(stream.ports) == 1 and "MONO" in stream.ports:
            only_port = next(iter(stream.ports.values()))
            for virt_port in snap.virt_ports.values():
                desired[(only_port, virt_port)] = node_id
        else:
            for channel in ("FL", "FR"):
                if channel in stream.ports and channel in snap.virt_ports:
                    desired[(stream.ports[channel], snap.virt_ports[channel])] = node_id

    return desired


# ──────────────────────────────────────────────────────────────────
# Virtual mic setup / teardown
# ──────────────────────────────────────────────────────────────────

async def virtual_module_ids() -> set[str]:
    """Find exact router modules using IDs from pactl's documented short format."""
    result = await run_command("pactl", "list", "short", "modules", timeout=5.0)
    if result.returncode != 0:
        raise StartupError(f"Could not list virtual mic modules: {_proc_error_message(result)}")

    modules: set[str] = set()
    for line in _decode_output(result.stdout).splitlines():
        # The installed pactl's JSON module records omit IDs.
        fields = line.split("\t")
        if len(fields) < 3 or fields[1] != "module-null-sink":
            continue
        module_id, _, arguments = fields[:3]
        # A malformed unrelated module must not prevent routing.
        with contextlib.suppress(ValueError):
            if f"sink_name={VIRT_NODE_NAME}" in shlex.split(arguments):
                modules.add(module_id)
    return modules


async def cleanup_stale_modules() -> bool:
    """Remove orphaned router modules; surface failures so recovery can retry."""
    modules = await virtual_module_ids()
    for module_id in modules:
        print(f":: Removing stale module (ID: {module_id})")
        result = await run_command("pactl", "unload-module", module_id, timeout=5.0)
        if result.returncode != 0:
            raise StartupError(f"Could not remove stale module: {_proc_error_message(result)}")
    return bool(modules)


async def create_virtual_mic() -> str:
    """Create the virtual mic and return its module ID."""
    result = await run_command(
        "pactl",
        "load-module",
        "module-null-sink",
        "sink_properties=media.class=Audio/Source/Virtual",
        f"sink_name={VIRT_NODE_NAME}",
        "channel_map=front-left,front-right",
        "format=float32le",
        "rate=48000",
        timeout=10.0,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    if result.timed_out or result.error or result.returncode != 0:
        raise StartupError(f"Failed to create virtual mic: {_proc_error_message(result)}")

    module_id = _decode_output(result.stdout)
    if not module_id:
        raise StartupError("Failed to create virtual mic: empty module ID")

    return module_id


async def destroy_virtual_mic(module_id: str | None, *, announce: bool = True) -> bool:
    """Verify ownership and retry transient teardown failures within ten seconds."""
    if module_id is None:
        return True
    error = "unknown error"
    try:
        async with asyncio.timeout(10.0):
            for attempt in range(3):
                try:
                    if module_id not in await virtual_module_ids():
                        return True
                    if announce:
                        print(f":: Tearing down virtual mic (Module ID: {module_id})")
                    result = await run_command("pactl", "unload-module", module_id, timeout=5.0)
                    if result.returncode == 0:
                        return True
                    error = _proc_error_message(result)
                except StartupError as exc:
                    error = str(exc)
                if attempt < 2:
                    await asyncio.sleep(0.1)
    except TimeoutError:
        error = "teardown exceeded ten seconds"
    print(f"Warning: Could not unload virtual mic: {error}", file=sys.stderr)
    return False


async def wait_for_virtual_mic(
    target: str,
    shutdown_event: asyncio.Event | None = None,
) -> tuple[int, ChannelMap]:
    """Wait for the virtual mic node and its input ports to appear."""
    deadline = time.monotonic() + MODULE_WAIT_TIMEOUT

    while time.monotonic() < deadline:
        if shutdown_event is not None and shutdown_event.is_set():
            raise StartupError("Shutdown requested during virtual mic initialization")

        graph = await async_pw_dump()
        if graph:
            snap = build_snapshot(graph, target)
            if snap.virt_node_id is not None and len(snap.virt_ports) >= 2:
                return snap.virt_node_id, snap.virt_ports

        await asyncio.sleep(MODULE_WAIT_STEP)

    raise StartupError("Virtual mic did not materialize.")


async def recover_virtual_mic(ds: DaemonState) -> None:
    """Recreate the virtual mic after it disappears."""
    if ds.shutdown_event.is_set():
        return

    print("Warning: Virtual mic disappeared; starting recovery.", file=sys.stderr)
    ds.phase = DaemonPhase.RECOVERING
    ds.virt_missing_count = 0

    # Removing the virtual module also removes all its links.
    ds.our_links.clear()
    ds.failed_pairs.clear()

    attempt = 0
    backoff = 0.5

    while not ds.shutdown_event.is_set():
        attempt += 1
        created_module_id: str | None = None
        try:
            await cleanup_stale_modules()
            ds.virt_module_id = None
            if ds.shutdown_event.is_set():
                return
            created_module_id = await create_virtual_mic()
            ds.virt_module_id = created_module_id

            virt_node_id, virt_ports = await wait_for_virtual_mic(
                ds.target_app,
                ds.shutdown_event,
            )
            if ds.shutdown_event.is_set():
                return
            ds.phase = DaemonPhase.READY
            print(
                f":: Virtual mic recovered "
                f"(Module ID: {created_module_id}, Node ID: {virt_node_id})"
            )
            return
        except StartupError as exc:
            print(
                f"Warning: Recovery attempt {attempt} failed: {exc}",
                file=sys.stderr,
            )
            if created_module_id is not None:
                if await destroy_virtual_mic(created_module_id, announce=False):
                    ds.virt_module_id = None

        try:
            async with asyncio.timeout(backoff):
                await ds.shutdown_event.wait()
        except TimeoutError:
            pass

        backoff = min(backoff * 2, 3.0)


# ──────────────────────────────────────────────────────────────────
# Monitor loop
# ──────────────────────────────────────────────────────────────────

async def monitor_loop(ds: DaemonState) -> None:
    """Reconcile links from one graph snapshot; only this task mutates links."""
    print(f":: Monitoring for '{ds.target_app}' streams. Ctrl+C to stop.\n")

    while not ds.shutdown_event.is_set():
        target = ds.target_app
        graph = await async_pw_dump()
        ds.graph = graph
        if not graph and not ds.shutdown_event.is_set():
            ds.phase = DaemonPhase.RECOVERING
            for tracked in ds.our_links.values():
                tracked.last_state = "unknown"
        if graph and target == ds.target_app and not ds.shutdown_event.is_set():
            snap = build_snapshot(graph, target)
            if snap.virt_node_id is None or len(snap.virt_ports) < 2:
                ds.virt_missing_count += 1
                if ds.virt_missing_count >= 2:
                    await recover_virtual_mic(ds)
            else:
                ds.virt_missing_count = 0
                ds.phase = DaemonPhase.READY
                await reconcile_links(ds, snap, target)

        try:
            async with asyncio.timeout(POLL_INTERVAL):
                await ds.shutdown_event.wait()
        except TimeoutError:
            pass


async def reconcile_links(ds: DaemonState, snap: GraphSnapshot, target: str) -> None:
    # Link idle streams too: a suspended stream may need a link before it runs.
    desired = compute_desired_links(snap)
    now = time.monotonic()
    for pair, tracked in list(ds.our_links.items()):
        state = snap.graph_links.get(pair)
        stale = pair not in desired or tracked.node_id != desired.get(pair)
        broken = state == "error" or (
            state not in ("active", "paused")
            and now - tracked.created_at > INIT_LINK_MAX_AGE
        )
        if stale or broken:
            if state is not None:
                error = await destroy_link(*pair)
                if error:
                    tracked.last_state = state
                    print(f"Warning: Disconnect {pair[0]}->{pair[1]} failed; will retry: {error}", file=sys.stderr)
                    continue
            ds.our_links.pop(pair, None)
            if broken and not stale and state is not None:
                ds.failed_pairs[pair] = now + MAX_RETRY_DELAY
        else:
            tracked.last_state = state or "unknown"

    ds.failed_pairs = {pair: retry for pair, retry in ds.failed_pairs.items() if pair in desired}
    missing = {
        pair for pair in desired
        if pair not in ds.our_links and now >= ds.failed_pairs.get(pair, 0.0)
    }
    if not missing or target != ds.target_app or ds.shutdown_event.is_set():
        return

    sem = asyncio.Semaphore(MAX_CONCURRENT_LINKS)

    async def link(pair: PortPair) -> tuple[PortPair, str]:
        async with sem:
            if target != ds.target_app or ds.shutdown_event.is_set():
                return pair, "Target changed or shutdown requested"
            return pair, await create_link(*pair)

    results = await asyncio.gather(*(link(pair) for pair in missing))
    created = 0
    for pair, error in results:
        if error:
            if target == ds.target_app and not ds.shutdown_event.is_set():
                ds.failed_pairs[pair] = time.monotonic() + MAX_RETRY_DELAY
                print(f"Warning: Link {pair[0]}->{pair[1]} failed; will retry: {error}", file=sys.stderr)
            continue

        # Track first: even a failed rollback must remain owned and retryable.
        ds.our_links[pair] = TrackedLink(
            out_port=pair[0], in_port=pair[1], node_id=desired[pair],
            created_at=time.monotonic(), last_state="init",
        )
        ds.failed_pairs.pop(pair, None)
        if target != ds.target_app or ds.shutdown_event.is_set():
            error = await destroy_link(*pair)
            if not error:
                ds.our_links.pop(pair, None)
            else:
                print(f"Warning: Could not roll back link {pair}: {error}", file=sys.stderr)
        else:
            created += 1
    if created:
        print(f":: Linked {created} port(s) → '{VIRT_NODE_NAME}'")


# ──────────────────────────────────────────────────────────────────
# IPC server
# ──────────────────────────────────────────────────────────────────

async def ipc_handler(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    ds: DaemonState,
) -> None:
    """Handle a single IPC client connection."""
    try:
        raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if not raw:
            return

        request = json.loads(raw.decode())
        if not isinstance(request, dict):
            return
        action = request.get("action")
        action = action if isinstance(action, str) else ""

        match action:
            case "ping":
                response = {"ok": True, "phase": ds.phase.value, "target": ds.target_app, "pid": os.getpid()}

            case "status":
                graph = ds.graph
                snap = build_snapshot(graph, ds.target_app) if graph else None
                streams_info: list[dict[str, Any]] = []

                active_links = sum(
                    1
                    for tracked in ds.our_links.values()
                    if tracked.last_state in ("active", "paused")
                )

                if snap is not None:
                    for node_id, stream in sorted(snap.streams.items()):
                        node_links = sum(
                            1
                            for tracked in ds.our_links.values()
                            if tracked.node_id == node_id
                            and tracked.last_state in ("active", "paused")
                        )
                        streams_info.append(
                            {
                                "node_id": node_id,
                                "state": stream.state,
                                "app_name": stream.app_name,
                                "media_name": stream.media_name,
                                "links": node_links,
                            }
                        )

                response = {
                    "ok": True,
                    "data": {
                        "pid": os.getpid(),
                        "target": ds.target_app,
                        "phase": ds.phase.value,
                        "virt_node": VIRT_NODE_NAME,
                        "active_links": active_links,
                        "total_tracked": len(ds.our_links),
                        "streams": streams_info,
                    },
                }

            case "list_apps":
                graph = ds.graph
                apps = discover_audio_apps(graph) if graph else []
                response = {"ok": True, "apps": apps}

            case "set_target":
                raw_app = request.get("app")
                new_target = raw_app.strip() if isinstance(raw_app, str) else ""

                if ds.shutdown_event.is_set():
                    response = {"ok": False, "error": "Daemon is stopping"}
                elif not new_target:
                    response = {"ok": False, "error": "No app specified"}
                elif new_target.lower() == ds.target_app.lower():
                    response = {"ok": True}
                else:
                    ds.target_app = new_target
                    print(f":: Target changed to '{new_target}'")
                    response = {"ok": True}

            case "stop":
                ds.shutdown_event.set()
                ds.phase = DaemonPhase.STOPPING
                await ds.stopped_event.wait()
                response = {"ok": ds.cleanup_ok}
                if not ds.cleanup_ok:
                    response["error"] = "Virtual microphone cleanup failed"

            case _:
                response = {"ok": False, "error": f"Unknown action: {action}"}

        writer.write(json.dumps(response).encode() + b"\n")
        await writer.drain()
    except (TimeoutError, ValueError, OSError):
        pass
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()


async def start_ipc_server(ds: DaemonState) -> asyncio.Server:
    """Start the Unix socket IPC server."""
    sock_path = _sock_path()
    sock_path.parent.mkdir(parents=True, exist_ok=True)

    if sock_path.exists():
        resp = ipc_send({"action": "ping"}, timeout=0.25)
        if resp and resp.get("ok") is True:
            raise StartupError("IPC socket is already in use.")
        with contextlib.suppress(OSError):
            sock_path.unlink()

    try:
        server = await asyncio.start_unix_server(
            lambda reader, writer: ipc_handler(reader, writer, ds),
            path=str(sock_path),
            cleanup_socket=True,
        )
    except OSError as exc:
        raise StartupError(f"Could not start IPC server: {exc}") from exc

    with contextlib.suppress(OSError):
        sock_path.chmod(0o600)

    return server


# ──────────────────────────────────────────────────────────────────
# IPC client
# ──────────────────────────────────────────────────────────────────

def ipc_send(request: dict[str, Any], timeout: float = 3.0) -> dict[str, Any] | None:
    """Send an IPC request to the daemon and return the JSON response."""
    sock_path = _sock_path()
    if not sock_path.exists():
        return None

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(sock_path))
            sock.sendall(json.dumps(request).encode() + b"\n")

            data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break

        if not data:
            return None

        payload = data.split(b"\n", 1)[0]
        response = json.loads(payload)
        return response if isinstance(response, dict) else None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def daemon_is_running(timeout: float = 1.0) -> bool:
    resp = ipc_send({"action": "ping"}, timeout=timeout)
    return resp is not None and resp.get("ok") is True


def request_set_target(app: str) -> bool:
    resp = ipc_send({"action": "set_target", "app": app})
    return resp is not None and resp.get("ok") is True


def request_stop_daemon(timeout: float = 20.0) -> bool:
    """Retry stop through startup, then wait for complete daemon cleanup."""
    deadline = time.monotonic() + timeout
    acknowledged = False
    while time.monotonic() < deadline:
        if not acknowledged:
            response = ipc_send({"action": "stop"}, timeout=max(0.01, deadline - time.monotonic()))
            if response and response.get("ok") is False:
                return False
            acknowledged = bool(response and response.get("ok") is True)
        try:
            # Do not create a runtime directory or lock file merely to stop.
            with _lock_path().open("r+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except FileNotFoundError:
            return True
        except BlockingIOError:
            pass
        except OSError as exc:
            print(f"Could not check daemon lock: {exc}", file=sys.stderr)
            return False
        time.sleep(0.1)
    return False


# ──────────────────────────────────────────────────────────────────
# Daemon entry
# ──────────────────────────────────────────────────────────────────

async def daemon_main(target_app: str) -> int:
    """Async daemon entry point."""
    target_app = target_app.strip() or DEFAULT_APP
    ds = DaemonState(target_app=target_app)
    server: asyncio.Server | None = None
    exit_code = 0

    print(f":: Initializing audio routing for [{target_app}]...")

    try:
        acquire_instance_lock(ds)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, ds.shutdown_event.set)

        server = await start_ipc_server(ds)

        if await cleanup_stale_modules():
            await asyncio.sleep(0.5)

        if ds.shutdown_event.is_set():
            raise StartupError("Shutdown requested during startup")
        ds.virt_module_id = await create_virtual_mic()
        print(f":: Virtual mic created (Module ID: {ds.virt_module_id})")

        virt_node_id, virt_ports = await wait_for_virtual_mic(
            ds.target_app,
            ds.shutdown_event,
        )
        ds.graph = await async_pw_dump()
        if not ds.shutdown_event.is_set():
            ds.phase = DaemonPhase.READY
            print(f":: Virtual mic ready (Node ID: {virt_node_id}, Ports: {virt_ports})")
            await monitor_loop(ds)

    except StartupError as exc:
        if ds.shutdown_event.is_set():
            print(":: Shutdown requested.")
        else:
            print(f"Fatal: {exc}", file=sys.stderr)
            exit_code = 1

    finally:
        ds.phase = DaemonPhase.STOPPING

        if server is not None:
            server.close()

        # Module removal destroys its links in one operation, regardless of stream count.
        try:
            if not await destroy_virtual_mic(ds.virt_module_id):
                exit_code = 1
        finally:
            ds.our_links.clear()
            ds.virt_module_id = None
            ds.cleanup_ok = exit_code == 0
            ds.stopped_event.set()
            try:
                # Let pending stop requests write the cleanup result before closing clients.
                await asyncio.sleep(0)
                if server is not None:
                    server.close_clients()
                    await server.wait_closed()
            finally:
                release_instance_lock(ds)

    return exit_code


# ──────────────────────────────────────────────────────────────────
# GUI popup
# ──────────────────────────────────────────────────────────────────

def _spawn_daemon(target: str) -> bool:
    """Spawn the daemon and wait until it reports READY."""
    target = target.strip()
    if not target:
        return False

    try:
        proc = subprocess.Popen(
            [sys.executable, str(_script_path()), "--daemon", target],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        print(f"Failed to launch daemon: {exc}", file=sys.stderr)
        return False

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False

        resp = ipc_send({"action": "ping"}, timeout=0.25)
        if resp and resp.get("ok") and resp.get("pid") == proc.pid:
            phase = resp.get("phase")
            if phase == DaemonPhase.READY.value:
                return True

        time.sleep(0.1)

    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    return False


def _try_libadwaita_popup(running: bool, status: dict[str, Any] | None, apps: list[str]) -> bool:
    """Attempt to show a GTK4/Libadwaita popup."""
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk
    except (ImportError, ValueError):
        return False

    result: dict[str, str | None] = {"action": None, "app": None}
    app = Adw.Application(application_id="dev.audiorouter.popup")

    def on_activate(application: Adw.Application) -> None:
        win = Adw.Window(
            title="Audio Router",
            default_width=420,
            default_height=-1,
            application=application,
        )

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        win.set_content(box)

        header = Adw.HeaderBar()
        box.append(header)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        box.append(scrolled)

        clamp = Adw.Clamp(maximum_size=400)
        scrolled.set_child(clamp)

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            margin_top=24,
            margin_bottom=24,
            margin_start=16,
            margin_end=16,
        )
        clamp.set_child(content)

        if running and status:
            data = status.get("data", {})
            streams = data.get("streams", [])
            active = [stream for stream in streams if stream.get("state") == "running"]
            idle = [stream for stream in streams if stream.get("state") != "running"]

            status_group = Adw.PreferencesGroup(title="Status")
            content.append(status_group)

            row_target = Adw.ActionRow(use_markup=False, title="Target")
            row_target.set_subtitle(str(data.get("target", "?")))
            status_group.add(row_target)

            row_phase = Adw.ActionRow(use_markup=False, title="Phase")
            row_phase.set_subtitle(str(data.get("phase", "unknown")))
            status_group.add(row_phase)

            row_streams = Adw.ActionRow(
                title="Streams",
                subtitle=f"{len(active)} active / {len(idle)} idle",
            )
            status_group.add(row_streams)

            row_links = Adw.ActionRow(
                title="Links",
                subtitle=f"{data.get('active_links', 0)} active",
            )
            status_group.add(row_links)

            if active:
                stream_group = Adw.PreferencesGroup(title="Active Streams")
                content.append(stream_group)

                for stream in active:
                    name = stream.get("media_name") or f"Node {stream.get('node_id')}"
                    row = Adw.ActionRow(use_markup=False)
                    row.set_title(str(name))
                    row.set_subtitle(
                        f"Node {stream.get('node_id')} · {stream.get('links', 0)} link(s)"
                    )
                    row.add_prefix(
                        Gtk.Image.new_from_icon_name("audio-volume-high-symbolic")
                    )
                    stream_group.add(row)

            if apps:
                target_group = Adw.PreferencesGroup(title="Change Target")
                content.append(target_group)

                string_list = Gtk.StringList()
                current_idx = 0
                current_target = str(data.get("target", ""))

                for idx, app_name in enumerate(apps):
                    string_list.append(app_name)
                    if app_name.lower() == current_target.lower():
                        current_idx = idx

                combo_row = Adw.ComboRow(
                    title="Application",
                    model=string_list,
                )
                combo_row.set_selected(current_idx)
                target_group.add(combo_row)

                def on_apply(_button) -> None:
                    selected = string_list.get_string(combo_row.get_selected())
                    if selected and selected.lower() != current_target.lower():
                        result["action"] = "set_target"
                        result["app"] = selected
                    win.close()

                def on_stop(_button) -> None:
                    result["action"] = "stop"
                    win.close()

                btn_box = Gtk.Box(
                    orientation=Gtk.Orientation.HORIZONTAL,
                    spacing=12,
                    halign=Gtk.Align.END,
                    margin_top=8,
                )
                content.append(btn_box)

                stop_btn = Gtk.Button(label="Stop Daemon")
                stop_btn.add_css_class("destructive-action")
                stop_btn.connect("clicked", on_stop)
                btn_box.append(stop_btn)

                apply_btn = Gtk.Button(label="Apply & Close")
                apply_btn.add_css_class("suggested-action")
                apply_btn.connect("clicked", on_apply)
                btn_box.append(apply_btn)
            else:
                def on_stop(_button) -> None:
                    result["action"] = "stop"
                    win.close()

                btn_box = Gtk.Box(
                    orientation=Gtk.Orientation.HORIZONTAL,
                    halign=Gtk.Align.END,
                    margin_top=8,
                )
                content.append(btn_box)

                stop_btn = Gtk.Button(label="Stop Daemon")
                stop_btn.add_css_class("destructive-action")
                stop_btn.connect("clicked", on_stop)
                btn_box.append(stop_btn)

        else:
            info_label = Gtk.Label(
                label="No routing daemon is running.",
                css_classes=["dim-label"],
                margin_bottom=8,
            )
            content.append(info_label)

            if apps:
                start_group = Adw.PreferencesGroup(title="Start Routing")
                content.append(start_group)

                string_list = Gtk.StringList()
                for app_name in apps:
                    string_list.append(app_name)

                combo_row = Adw.ComboRow(
                    title="Target Application",
                    model=string_list,
                )
                start_group.add(combo_row)

                def on_start(_button) -> None:
                    selected = string_list.get_string(combo_row.get_selected())
                    if selected:
                        result["action"] = "start"
                        result["app"] = selected
                    win.close()

                def on_cancel(_button) -> None:
                    win.close()

                btn_box = Gtk.Box(
                    orientation=Gtk.Orientation.HORIZONTAL,
                    spacing=12,
                    halign=Gtk.Align.END,
                    margin_top=8,
                )
                content.append(btn_box)

                cancel_btn = Gtk.Button(label="Cancel")
                cancel_btn.connect("clicked", on_cancel)
                btn_box.append(cancel_btn)

                start_btn = Gtk.Button(label="Start")
                start_btn.add_css_class("suggested-action")
                start_btn.connect("clicked", on_start)
                btn_box.append(start_btn)
            else:
                no_app_label = Gtk.Label(
                    label="No audio applications detected.",
                    css_classes=["dim-label"],
                )
                content.append(no_app_label)

        win.present()

    app.connect("activate", on_activate)
    app.run(None)

    match result["action"]:
        case "stop":
            if request_stop_daemon():
                print(":: Daemon stopped.")
            else:
                print("Failed to stop daemon.", file=sys.stderr)
        case "set_target" if result["app"]:
            if request_set_target(result["app"]):
                print(f":: Target changed to '{result['app']}'.")
            else:
                print("Failed to change target.", file=sys.stderr)
        case "start" if result["app"]:
            if _spawn_daemon(result["app"]):
                print(f":: Daemon started for '{result['app']}'.")
            else:
                print("Failed to start daemon.", file=sys.stderr)

    return True


def _try_yad_popup(running: bool, status: dict[str, Any] | None, apps: list[str]) -> bool:
    """Fallback YAD popup."""
    if not shutil.which("yad"):
        return False

    if running and status:
        data = status.get("data", {})
        streams = data.get("streams", [])
        active = [stream for stream in streams if stream.get("state") == "running"]

        stream_text = "\n".join(
            f"  {stream.get('media_name') or 'Node ' + str(stream.get('node_id'))} "
            f"({stream.get('links', 0)} links)"
            for stream in active
        ) or "  (none)"

        info_text = (
            f"Target: {data.get('target', '?')}\n"
            f"Phase: {data.get('phase', 'unknown')}\n"
            f"Active streams: {len(active)}\n"
            f"Active links: {data.get('active_links', 0)}\n\n"
            f"Streams:\n{stream_text}"
        )

        app_options = "!".join(apps) if apps else str(data.get("target", DEFAULT_APP))

        try:
            result = subprocess.run(
                [
                    "yad",
                    "--form",
                    "--no-markup",
                    "--title=Audio Router",
                    "--width=400",
                    "--text",
                    info_text,
                    "--field=Target:CB",
                    app_options,
                    "--button=Apply:0",
                    "--button=Stop Daemon:2",
                    "--button=Cancel:1",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=60,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            return True

        if result.returncode == 0:
            selected = result.stdout.strip().split("|")[0].strip()
            current_target = str(data.get("target", ""))
            if selected and selected.lower() != current_target.lower():
                if request_set_target(selected):
                    print(f":: Target changed to '{selected}'.")
                else:
                    print("Failed to change target.", file=sys.stderr)
        elif result.returncode == 2:
            if request_stop_daemon():
                print(":: Daemon stopped.")
            else:
                print("Failed to stop daemon.", file=sys.stderr)

        return True

    if not apps:
        with contextlib.suppress(Exception):
            subprocess.run(
                [
                    "yad",
                    "--info",
                    "--title=Audio Router",
                    "--text=No audio applications detected.",
                    "--button=OK:0",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        return True

    app_options = "!".join(apps)
    try:
        result = subprocess.run(
            [
                "yad",
                "--form",
                "--no-markup",
                "--title=Audio Router — Start",
                "--width=400",
                "--text=No daemon running. Select target to start:",
                "--field=Application:CB",
                app_options,
                "--button=Start:0",
                "--button=Cancel:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=60,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return True

    if result.returncode == 0:
        selected = result.stdout.strip().split("|")[0].strip()
        if selected:
            if _spawn_daemon(selected):
                print(f":: Daemon started for '{selected}'.")
            else:
                print("Failed to start daemon.", file=sys.stderr)

    return True


def _try_terminal_menu(running: bool, status: dict[str, Any] | None, apps: list[str]) -> bool:
    """Last-resort terminal menu."""
    if running and status:
        data = status.get("data", {})
        streams = data.get("streams", [])
        active = [stream for stream in streams if stream.get("state") == "running"]

        print("\n╔══ Audio Router ═══════════════════════╗")
        print(f"║  Target : {str(data.get('target', '?')):<28}║")
        print(f"║  Phase  : {str(data.get('phase', 'unknown')):<28}║")
        print(f"║  Links  : {data.get('active_links', 0)} active{' ' * 21}║")
        print("╠═══════════════════════════════════════╣")
        print("║  1. Change target                     ║")
        print("║  2. Stop daemon                       ║")
        print("║  3. Cancel                            ║")
        print("╚═══════════════════════════════════════╝")

        try:
            choice = input("\nChoice: ").strip()
        except (EOFError, KeyboardInterrupt):
            return True

        if choice == "1" and apps:
            print("\nAvailable apps:")
            for idx, app_name in enumerate(apps, 1):
                print(f"  {idx}. {app_name}")
            try:
                selected_idx = int(input("Select: ").strip()) - 1
                if 0 <= selected_idx < len(apps):
                    if request_set_target(apps[selected_idx]):
                        print(f":: Target changed to '{apps[selected_idx]}'.")
                    else:
                        print("Failed to change target.", file=sys.stderr)
            except (ValueError, EOFError, KeyboardInterrupt):
                pass
        elif choice == "2":
            if request_stop_daemon():
                print(":: Daemon stopped.")
            else:
                print("Failed to stop daemon.", file=sys.stderr)

    else:
        if not apps:
            print("No audio applications detected.")
            return True

        print("\n╔══ Audio Router — Start ════════════════╗")
        print("║  No daemon running.                    ║")
        print("║  Select target application:            ║")
        print("╠════════════════════════════════════════╣")
        for idx, app_name in enumerate(apps, 1):
            print(f"║  {idx}. {app_name:<36}║")
        print("║  0. Cancel                             ║")
        print("╚════════════════════════════════════════╝")

        try:
            selected_idx = int(input("\nSelect: ").strip())
            if 1 <= selected_idx <= len(apps):
                app_name = apps[selected_idx - 1]
                if _spawn_daemon(app_name):
                    print(f":: Daemon started for '{app_name}'.")
                else:
                    print("Failed to start daemon.", file=sys.stderr)
        except (ValueError, EOFError, KeyboardInterrupt):
            pass

    return True


def gui_main() -> int:
    """Entry point for popup mode."""
    running = daemon_is_running()
    status: dict[str, Any] | None = None
    apps: list[str] = []

    if running:
        status = ipc_send({"action": "status"})
        if not status or not status.get("ok"):
            status = {
                "ok": True,
                "data": {
                    "target": "Unknown",
                    "phase": "unknown",
                    "virt_node": VIRT_NODE_NAME,
                    "active_links": 0,
                    "total_tracked": 0,
                    "streams": [],
                },
            }

        apps_resp = ipc_send({"action": "list_apps"})
        apps = apps_resp.get("apps", []) if apps_resp and apps_resp.get("ok") else []
    else:
        graph = sync_pw_dump()
        apps = discover_audio_apps(graph) if graph else []

    if running and status:
        current_target = status.get("data", {}).get("target")
        if current_target:
            apps = [current_target, *(app for app in apps if app.lower() != current_target.lower())]

    if _try_libadwaita_popup(running, status, apps):
        return 0
    if _try_yad_popup(running, status, apps):
        return 0
    _try_terminal_menu(running, status, apps)
    return 0


# ──────────────────────────────────────────────────────────────────
# CLI commands
# ──────────────────────────────────────────────────────────────────

def cli_status() -> int:
    """Print daemon status."""
    if not daemon_is_running():
        print("Daemon is not running.")
        return 1

    resp = ipc_send({"action": "status"})
    if not resp or not resp.get("ok"):
        print("Failed to get status.", file=sys.stderr)
        return 1

    data = resp["data"]
    streams = data.get("streams", [])
    active = [stream for stream in streams if stream.get("state") == "running"]

    print(f"PID      : {data['pid']}")
    print(f"Target   : {data['target']}")
    print(f"Phase    : {data['phase']}")
    print(f"Virtual  : {data['virt_node']}")
    print(f"Links    : {data['active_links']} active / {data['total_tracked']} tracked")
    print(f"Streams  : {len(active)} running / {len(streams)} total")

    if active:
        print("\nActive streams:")
        for stream in active:
            name = stream.get("media_name") or "(unnamed)"
            print(
                f"  Node {stream['node_id']:>4} — "
                f"{name} ({stream.get('links', 0)} links)"
            )

    return 0


def cli_stop() -> int:
    """Tell the daemon to shut down."""
    if request_stop_daemon():
        print(":: Daemon stopped.")
        return 0

    print("Failed to stop daemon.", file=sys.stderr)
    return 1


def cli_waybar() -> int:
    """Output Waybar JSON."""
    payload: dict[str, Any]

    if not daemon_is_running():
        payload = {
            "text": "󰍭",
            "tooltip": "Routing daemon stopped",
            "class": "inactive",
        }
    else:
        resp = ipc_send({"action": "status"})
        if resp and resp.get("ok"):
            data = resp.get("data", {})
            target = data.get("target", "Unknown")
            phase = data.get("phase", "unknown")
            links = data.get("active_links", 0)
            payload = {
                "text": "󰍬",
                "tooltip": f"Routing: {target}\nPhase: {phase}\nActive Links: {links}",
                "class": "active",
            }
        else:
            payload = {
                "text": "󰍬",
                "tooltip": "Routing daemon running (status unknown)",
                "class": "active",
            }

    print(json.dumps(payload, ensure_ascii=False))
    return 0


# ──────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--daemon",
        nargs="?",
        const=DEFAULT_APP,
        metavar="APP",
        help="run the routing daemon in the foreground",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="print daemon status",
    )
    group.add_argument(
        "--stop",
        action="store_true",
        help="stop the running daemon",
    )
    group.add_argument(
        "--waybar",
        action="store_true",
        help="output Waybar JSON status",
    )

    return parser.parse_args(argv)


def main() -> int:
    args = parse_args(sys.argv[1:])

    if args.daemon is not None:
        return asyncio.run(daemon_main(args.daemon))
    if args.status:
        return cli_status()
    if args.stop:
        return cli_stop()
    if args.waybar:
        return cli_waybar()
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
