#!/usr/bin/env python3
"""Route application audio output to a virtual microphone via PipeWire.

Architecture : Arch Linux / Wayland / Hyprland
Ecosystem    : PipeWire 1.4+ / WirePlumber
Python       : 3.14+

Usage:
    audio_router.py                     # GUI popup (control / start)
    audio_router.py --daemon [APP]      # background routing daemon
    audio_router.py --status            # print daemon status
    audio_router.py --stop              # stop running daemon
    audio_router.py --waybar            # output Waybar JSON status
"""

from __future__ import annotations

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
MAX_LINK_RETRIES = 3
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
    virt_node_id: int | None = None
    virt_ports: ChannelMap = field(default_factory=dict)
    our_links: dict[PortPair, TrackedLink] = field(default_factory=dict)
    prev_ports: dict[int, ChannelMap] = field(default_factory=dict)
    failed_pairs: dict[PortPair, int] = field(default_factory=dict)
    shutdown_event: asyncio.Event = field(default_factory=asyncio.Event)
    lock_fd: int | None = None
    virt_missing_count: int = 0


@dataclass(slots=True)
class GraphSnapshot:
    streams: dict[int, StreamInfo]
    running_ids: set[int]
    virt_node_id: int | None
    virt_ports: ChannelMap
    graph_links: dict[PortPair, str]


# ──────────────────────────────────────────────────────────────────
# Path helpers
# ──────────────────────────────────────────────────────────────────

def _runtime_dir() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
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
    stdout: int | None = asyncio.subprocess.PIPE,
    stderr: int | None = asyncio.subprocess.PIPE,
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
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.communicate()
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
        return json.loads(_decode_output(result.stdout))
    except json.JSONDecodeError as exc:
        print(f"Warning: pw-dump returned invalid JSON: {exc}", file=sys.stderr)
        return []


def sync_pw_dump() -> list[GraphObject]:
    """Synchronous pw-dump for non-async contexts."""
    try:
        result = subprocess.run(
            ["pw-dump"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=PW_DUMP_TIMEOUT,
            check=False,
        )
        if result.returncode != 0 or not result.stdout:
            return []
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        return []


def _extract_channel(props: GraphObject) -> str:
    ch = props.get("audio.channel")
    if isinstance(ch, str) and ch:
        return ch.upper()

    name = props.get("port.name", "")
    if isinstance(name, str):
        upper = name.upper()
        if "FL" in upper:
            return "FL"
        if "FR" in upper:
            return "FR"
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

    running_ids = {
        node_id
        for node_id, stream in streams.items()
        if stream.state == "running"
    }

    return GraphSnapshot(
        streams=streams,
        running_ids=running_ids,
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


async def destroy_link(out_port: int, in_port: int) -> None:
    """Destroy a PipeWire link between two ports."""
    await run_command(
        "pw-link",
        "-d",
        str(out_port),
        str(in_port),
        timeout=LINK_TIMEOUT,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )


def compute_desired_links(
    snap: GraphSnapshot,
    target_ids: set[int],
) -> dict[PortPair, int]:
    """Compute desired links for currently linkable stream nodes."""
    desired: dict[PortPair, int] = {}
    if not snap.virt_ports:
        return desired

    for node_id in target_ids:
        stream = snap.streams.get(node_id)
        if stream is None or not stream.ports:
            continue

        if len(stream.ports) == 1:
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

async def cleanup_stale_modules() -> bool:
    """Remove orphaned Virtual_Mic_Tx modules from prior runs."""
    result = await run_command(
        "pactl",
        "list",
        "modules",
        "short",
        timeout=5.0,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    if result.timed_out or result.error or result.returncode != 0:
        return False

    removed = False
    for line in _decode_output(result.stdout).splitlines():
        fields = line.split("\t", 2)
        if len(fields) < 3:
            continue

        module_id, module_name, args = fields
        if module_name == "module-null-sink" and VIRT_NODE_NAME in args:
            print(f":: Removing stale module (ID: {module_id})")
            await run_command(
                "pactl",
                "unload-module",
                module_id,
                timeout=5.0,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            removed = True

    return removed


async def create_virtual_mic() -> str:
    """Create the virtual mic and return its module ID."""
    result = await run_command(
        "pactl",
        "load-module",
        "module-null-sink",
        "media.class=Audio/Source/Virtual",
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


async def destroy_virtual_mic(module_id: str | None, *, announce: bool = True) -> None:
    """Unload the virtual mic module."""
    if not module_id:
        return

    if announce:
        print(f":: Tearing down virtual mic (Module ID: {module_id})")

    await run_command(
        "pactl",
        "unload-module",
        module_id,
        timeout=5.0,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )


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

    for pair in list(ds.our_links):
        await destroy_link(*pair)
    ds.our_links.clear()
    ds.prev_ports.clear()
    ds.failed_pairs.clear()
    ds.virt_node_id = None
    ds.virt_ports.clear()

    if ds.virt_module_id is not None:
        await destroy_virtual_mic(ds.virt_module_id, announce=False)
        ds.virt_module_id = None

    attempt = 0
    backoff = 0.5

    while not ds.shutdown_event.is_set():
        attempt += 1
        await cleanup_stale_modules()

        created_module_id: str | None = None
        try:
            created_module_id = await create_virtual_mic()
            ds.virt_module_id = created_module_id

            virt_node_id, virt_ports = await wait_for_virtual_mic(
                ds.target_app,
                ds.shutdown_event,
            )
            ds.virt_node_id = virt_node_id
            ds.virt_ports = virt_ports
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
                await destroy_virtual_mic(created_module_id, announce=False)
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
    """Main routing loop."""
    print(f":: Monitoring for '{ds.target_app}' streams. Ctrl+C to stop.\n")
    prev_running: set[int] = set()
    prev_all: set[int] = set()

    while not ds.shutdown_event.is_set():
        try:
            async with asyncio.timeout(POLL_INTERVAL):
                await ds.shutdown_event.wait()
            break
        except TimeoutError:
            pass

        graph = await async_pw_dump()
        if not graph:
            continue

        snap = build_snapshot(graph, ds.target_app)

        # Virtual mic sanity / recovery
        if snap.virt_node_id is None or len(snap.virt_ports) < 2:
            ds.virt_missing_count += 1
            if ds.virt_missing_count == 1:
                print("Warning: Virtual mic missing from graph.", file=sys.stderr)
            elif ds.virt_missing_count >= 2:
                await recover_virtual_mic(ds)
                prev_running.clear()
                prev_all.clear()
            continue

        ds.virt_missing_count = 0
        ds.phase = DaemonPhase.READY
        ds.virt_node_id = snap.virt_node_id
        ds.virt_ports = snap.virt_ports

        all_ids = set(snap.streams)

        # Log stream changes
        for node_id in sorted(all_ids - prev_all):
            stream = snap.streams[node_id]
            label = f"'{stream.media_name}'" if stream.media_name else f"node {node_id}"
            print(f":: Stream appeared: {stream.app_name} — {label} ({stream.state})")

        for node_id in sorted(prev_all - all_ids):
            print(f":: Stream gone: node {node_id}")

        for node_id in sorted(snap.running_ids - prev_running):
            if node_id in prev_all:
                stream = snap.streams[node_id]
                label = f"'{stream.media_name}'" if stream.media_name else f"node {node_id}"
                print(f":: Stream activated: {stream.app_name} — {label}")

        prev_all = all_ids
        prev_running = snap.running_ids.copy()

        # Detect port recycling
        needs_relink: set[int] = set()

        for node_id, stream in snap.streams.items():
            old_ports = ds.prev_ports.get(node_id)
            if old_ports is not None and old_ports != stream.ports:
                stale_pairs = [
                    pair
                    for pair, tracked in ds.our_links.items()
                    if tracked.node_id == node_id
                ]
                if stale_pairs:
                    print(
                        f":: Port recycled on node {node_id}: "
                        f"destroying {len(stale_pairs)} stale link(s)"
                    )
                    for pair in stale_pairs:
                        await destroy_link(*pair)
                        ds.our_links.pop(pair, None)
                        ds.failed_pairs.pop(pair, None)
                needs_relink.add(node_id)

            ds.prev_ports[node_id] = dict(stream.ports)

        # Detect disappeared nodes
        gone_nodes = set(ds.prev_ports) - all_ids
        for node_id in gone_nodes:
            stale_pairs = [
                pair
                for pair, tracked in ds.our_links.items()
                if tracked.node_id == node_id
            ]
            for pair in stale_pairs:
                await destroy_link(*pair)
                ds.our_links.pop(pair, None)
                ds.failed_pairs.pop(pair, None)
            ds.prev_ports.pop(node_id, None)

        # Cleanup zombie links
        now = time.monotonic()
        zombies: list[PortPair] = []

        for pair, tracked in list(ds.our_links.items()):
            graph_state = snap.graph_links.get(pair)
            age = now - tracked.created_at

            if graph_state is None:
                if age > INIT_LINK_MAX_AGE:
                    zombies.append(pair)
                continue

            if graph_state == "error":
                zombies.append(pair)
                continue

            if graph_state == "init" and age > INIT_LINK_MAX_AGE:
                zombies.append(pair)
                continue

            tracked.last_state = graph_state or "unknown"

        for pair in zombies:
            tracked = ds.our_links.pop(pair, None)
            if tracked and snap.graph_links.get(pair) is not None:
                await destroy_link(*pair)
                needs_relink.add(tracked.node_id)

        # Compute desired links for running nodes only
        linkable = (snap.running_ids | needs_relink) & all_ids
        linkable = {
            node_id
            for node_id in linkable
            if snap.streams.get(node_id) is not None
            and snap.streams[node_id].state == "running"
        }

        desired = compute_desired_links(snap, linkable)

        # Determine missing links
        existing_healthy = {
            pair
            for pair, tracked in ds.our_links.items()
            if tracked.last_state in ("active", "paused")
        }
        missing = set(desired) - existing_healthy - set(ds.our_links)
        missing = {
            pair
            for pair in missing
            if ds.failed_pairs.get(pair, 0) < MAX_LINK_RETRIES
        }

        # Create missing links concurrently
        if missing:
            sem = asyncio.Semaphore(MAX_CONCURRENT_LINKS)

            async def _do_link(pair: PortPair) -> tuple[PortPair, str]:
                async with sem:
                    return pair, await create_link(*pair)

            results = await asyncio.gather(
                *(_do_link(pair) for pair in missing),
                return_exceptions=True,
            )

            created = 0
            for result in results:
                if isinstance(result, BaseException):
                    continue

                pair, err = result
                node_id = desired.get(pair, 0)

                if not err:
                    ds.our_links[pair] = TrackedLink(
                        out_port=pair[0],
                        in_port=pair[1],
                        node_id=node_id,
                        created_at=time.monotonic(),
                        last_state="init",
                    )
                    ds.failed_pairs.pop(pair, None)
                    created += 1
                else:
                    count = ds.failed_pairs.get(pair, 0) + 1
                    ds.failed_pairs[pair] = count
                    if count >= MAX_LINK_RETRIES:
                        stream = snap.streams.get(node_id)
                        label = stream.media_name if stream else f"node {node_id}"
                        print(
                            f"Warning: Giving up on link {pair[0]}->{pair[1]} "
                            f"({label}): {err}",
                            file=sys.stderr,
                        )

            if created:
                print(
                    f":: Linked {created} port(s) across "
                    f"{len(linkable)} running stream(s) → '{VIRT_NODE_NAME}'"
                )


# ──────────────────────────────────────────────────────────────────
# IPC server
# ──────────────────────────────────────────────────────────────────

async def ipc_handler(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    ds: DaemonState,
    graph_fn,
) -> None:
    """Handle a single IPC client connection."""
    try:
        raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
        if not raw:
            return

        request = json.loads(raw.decode())
        action = request.get("action")
        action = action if isinstance(action, str) else ""

        match action:
            case "ping":
                response = {"ok": True, "phase": ds.phase.value}

            case "status":
                graph = await graph_fn()
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
                        "target": ds.target_app,
                        "phase": ds.phase.value,
                        "virt_node": VIRT_NODE_NAME,
                        "active_links": active_links,
                        "total_tracked": len(ds.our_links),
                        "streams": streams_info,
                    },
                }

            case "list_apps":
                graph = await graph_fn()
                apps = discover_audio_apps(graph) if graph else []
                response = {"ok": True, "apps": apps}

            case "set_target":
                raw_app = request.get("app")
                new_target = raw_app.strip() if isinstance(raw_app, str) else ""

                if not new_target:
                    response = {"ok": False, "error": "No app specified"}
                elif new_target.lower() == ds.target_app.lower():
                    response = {"ok": True}
                else:
                    for pair in list(ds.our_links):
                        await destroy_link(*pair)
                    ds.our_links.clear()
                    ds.prev_ports.clear()
                    ds.failed_pairs.clear()
                    ds.target_app = new_target
                    print(f":: Target changed to '{new_target}'")
                    response = {"ok": True}

            case "stop":
                ds.phase = DaemonPhase.STOPPING
                ds.shutdown_event.set()
                response = {"ok": True}

            case _:
                response = {"ok": False, "error": f"Unknown action: {action}"}

        writer.write(json.dumps(response).encode() + b"\n")
        await writer.drain()
    except (asyncio.TimeoutError, json.JSONDecodeError, UnicodeDecodeError, OSError):
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
            lambda reader, writer: ipc_handler(reader, writer, ds, async_pw_dump),
            path=str(sock_path),
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
        return json.loads(payload.decode())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def daemon_is_running(timeout: float = 1.0) -> bool:
    resp = ipc_send({"action": "ping"}, timeout=timeout)
    return resp is not None and resp.get("ok") is True


def request_set_target(app: str) -> bool:
    resp = ipc_send({"action": "set_target", "app": app})
    return resp is not None and resp.get("ok") is True


def request_stop_daemon(timeout: float = 3.0) -> bool:
    if not daemon_is_running(timeout=0.25):
        return True

    resp = ipc_send({"action": "stop"}, timeout=1.0)
    if not resp or resp.get("ok") is not True:
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not daemon_is_running(timeout=0.25):
            return True
        time.sleep(0.1)

    return not daemon_is_running(timeout=0.25)


# ──────────────────────────────────────────────────────────────────
# Daemon entry
# ──────────────────────────────────────────────────────────────────

async def daemon_main(target_app: str) -> int:
    """Async daemon entry point."""
    target_app = target_app.strip() or DEFAULT_APP
    ds = DaemonState(target_app=target_app)
    server: asyncio.Server | None = None

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

        ds.virt_module_id = await create_virtual_mic()
        print(f":: Virtual mic created (Module ID: {ds.virt_module_id})")

        virt_node_id, virt_ports = await wait_for_virtual_mic(
            ds.target_app,
            ds.shutdown_event,
        )
        ds.virt_node_id = virt_node_id
        ds.virt_ports = virt_ports
        ds.phase = DaemonPhase.READY
        print(f":: Virtual mic ready (Node ID: {virt_node_id}, Ports: {virt_ports})")

        await monitor_loop(ds)
        return 0

    except StartupError as exc:
        print(f"Fatal: {exc}", file=sys.stderr)
        return 1

    finally:
        ds.phase = DaemonPhase.STOPPING

        if server is not None:
            server.close()
            with contextlib.suppress(Exception):
                await server.wait_closed()

        for pair in list(ds.our_links):
            await destroy_link(*pair)
        ds.our_links.clear()

        await destroy_virtual_mic(ds.virt_module_id, announce=ds.virt_module_id is not None)
        ds.virt_module_id = None

        with contextlib.suppress(OSError):
            _sock_path().unlink()

        release_instance_lock(ds)


# ──────────────────────────────────────────────────────────────────
# GUI popup
# ──────────────────────────────────────────────────────────────────

def _spawn_daemon(target: str) -> bool:
    """Spawn the daemon and wait until it reports READY."""
    target = target.strip()
    if not target:
        return False

    proc = subprocess.Popen(
        [sys.executable, str(_script_path()), "--daemon", target],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if proc.poll() is not None and not daemon_is_running(timeout=0.25):
            return False

        resp = ipc_send({"action": "status"}, timeout=0.25)
        if resp and resp.get("ok"):
            phase = resp.get("data", {}).get("phase")
            if phase == DaemonPhase.READY.value:
                return True

        time.sleep(0.1)

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

            row_target = Adw.ActionRow(
                title="Target",
                subtitle=str(data.get("target", "?")),
            )
            status_group.add(row_target)

            row_phase = Adw.ActionRow(
                title="Phase",
                subtitle=str(data.get("phase", "unknown")),
            )
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
                    row = Adw.ActionRow(
                        title=str(name),
                        subtitle=(
                            f"Node {stream.get('node_id')} · "
                            f"{stream.get('links', 0)} link(s)"
                        ),
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
    import shutil

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
    if not daemon_is_running():
        print("Daemon is not running.")
        return 0

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
        help="run the routing daemon in the background",
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
