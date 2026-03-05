#!/usr/bin/env python3
# Architecture: Arch Linux / Wayland / Hyprland
# Target Ecosystem: PipeWire 1.4+ / WirePlumber
# Language: Python 3.14

import sys
import json
import time
import atexit
import signal
import subprocess

TARGET_APP = sys.argv[1] if len(sys.argv) > 1 else "mpv"
VIRT_NODE_NAME = "Virtual_Mic_Tx"
VIRT_MODULE_ID = None

def cleanup() -> None:
    """Destroy the virtual node module, guarded against double invocation."""
    global VIRT_MODULE_ID
    if VIRT_MODULE_ID:
        print(f"\n:: Tearing down virtual node module (ID: {VIRT_MODULE_ID})...")
        subprocess.run(
            ["pactl", "unload-module", VIRT_MODULE_ID],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        VIRT_MODULE_ID = None

def handle_signal(signum, frame) -> None:
    """SIGTERM/SIGHUP handler — cleanup then exit."""
    cleanup()
    sys.exit(0)

atexit.register(cleanup)
signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGHUP, handle_signal)

def get_pw_graph(*, fatal: bool = True) -> list[dict]:
    """Capture and parse the live PipeWire object graph."""
    try:
        out = subprocess.check_output(["pw-dump"], text=True)
        return json.loads(out)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        if fatal:
            print(f"Fatal: Failed to query PipeWire daemon: {e}", file=sys.stderr)
            sys.exit(1)
        return []

def find_nodes(graph: list[dict]) -> tuple[int | None, int | None]:
    """Locate the target application stream and virtual node in the graph."""
    target_id = None
    virt_id = None
    for obj in graph:
        if obj.get("type") != "PipeWire:Interface:Node":
            continue
        props = obj.get("info", {}).get("props", {})
        if props.get("node.name") == VIRT_NODE_NAME:
            virt_id = obj["id"]
            
        if props.get("media.class") == "Stream/Output/Audio":
            # Shotgun approach: search all string property values for the app name
            if any(isinstance(v, str) and TARGET_APP.lower() in v.lower() for v in props.values()):
                target_id = obj["id"]
    return target_id, virt_id

def find_ports(graph: list[dict], target_node_id: int, virt_node_id: int) -> tuple[list[int], list[int]]:
    """Extract sorted output/input port IDs for the target and virtual nodes."""
    target_out: list[int] = []
    virt_in: list[int] = []
    for obj in graph:
        if obj.get("type") != "PipeWire:Interface:Port":
            continue
        info = obj.get("info", {})
        
        # Safely extract parent node ID
        parent = info.get("props", {}).get("node.id")
        if parent is not None:
            parent = int(parent)
            
        direction = str(info.get("direction", "")).lower()
        
        if direction == "output" and parent == target_node_id:
            target_out.append(obj["id"])
        elif direction == "input" and parent == virt_node_id:
            virt_in.append(obj["id"])
            
    # Crucial step: Sort IDs to guarantee FL/FR channel order alignment
    target_out.sort()
    virt_in.sort()
    return target_out, virt_in

def resolve_stereo_pairs(graph: list[dict], target_id: int, virt_id: int) -> tuple[list[int], list[int]] | None:
    """Resolve port pairs with mono-to-stereo fallback."""
    target_ports, virt_ports = find_ports(graph, target_id, virt_id)
    if len(target_ports) == 1:
        target_ports *= 2
    if len(virt_ports) == 1:
        virt_ports *= 2
    if len(target_ports) < 2 or len(virt_ports) < 2:
        return None
    return target_ports[:2], virt_ports[:2]

def links_exist(graph: list[dict], out_ports: list[int], in_ports: list[int]) -> bool:
    """Check whether all required port-to-port links already exist natively in the graph."""
    needed = set(zip(out_ports, in_ports))
    if not needed:
        return False
    for obj in graph:
        if obj.get("type") != "PipeWire:Interface:Link":
            continue
        info = obj.get("info", {})
        needed.discard((info.get("output-port-id"), info.get("input-port-id")))
    return not needed

def create_links(out_ports: list[int], in_ports: list[int]) -> None:
    """Create pw-link connections."""
    for op, ip in zip(out_ports, in_ports):
        subprocess.run(
            ["pw-link", str(op), str(ip)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

def main() -> None:
    global VIRT_MODULE_ID
    print(f":: Initializing Virtual Audio Node routing for [{TARGET_APP}]...")

    # 1. Create virtual source node
    try:
        out = subprocess.check_output(
            [
                "pactl", "load-module", "module-null-sink",
                "media.class=Audio/Source/Virtual",
                f"sink_name={VIRT_NODE_NAME}",
                "channel_map=front-left,front-right",
            ],
            text=True,
        )
        VIRT_MODULE_ID = out.strip()
        print(f":: Virtual node instantiated. (Module ID: {VIRT_MODULE_ID})")
    except subprocess.CalledProcessError:
        print("Fatal: Failed to load virtual node via pipewire-pulse.", file=sys.stderr)
        sys.exit(1)

    # 2. Wait for virtual node to materialize in graph
    virt_node_id = None
    for _ in range(30):
        graph = get_pw_graph()
        _, virt_node_id = find_nodes(graph)
        if virt_node_id is not None:
            break
        time.sleep(0.1)

    if virt_node_id is None:
        print("Fatal: Virtual node did not appear in PipeWire graph.", file=sys.stderr)
        sys.exit(1)

    print(f":: Virtual node confirmed. (Node ID: {virt_node_id})")
    print(f":: Monitoring for '{TARGET_APP}' streams... Press Ctrl+C to stop.\n")

    # 3. Continuous monitoring loop
    current_target_id = None
    linked = False

    try:
        while True:
            # fatal=False prevents transient pw-dump failures from crashing the daemon
            graph = get_pw_graph(fatal=False)
            if not graph:
                time.sleep(1)
                continue

            target_id, fresh_virt_id = find_nodes(graph)

            # Virtual node sanity check
            if fresh_virt_id is None:
                if linked or current_target_id is not None:
                    print("Warning: Virtual node vanished from graph.", file=sys.stderr)
                linked = False
                current_target_id = None
                time.sleep(1)
                continue

            virt_node_id = fresh_virt_id

            # Target stream absent (e.g., audio paused)
            if target_id is None:
                if linked:
                    print(f":: Stream '{TARGET_APP}' ended or paused. Waiting for playback to resume...")
                linked = False
                current_target_id = None
                time.sleep(1)
                continue

            # New or changed stream (e.g., audio resumed)
            if target_id != current_target_id:
                print(f":: Stream detected: '{TARGET_APP}' (Node ID: {target_id})")
                current_target_id = target_id
                linked = False

            # Resolve stereo port pairs
            pairs = resolve_stereo_pairs(graph, target_id, virt_node_id)
            if pairs is None:
                time.sleep(0.5)
                continue

            tp, vp = pairs

            # Verify or establish links natively in the graph
            if not links_exist(graph, tp, vp):
                if linked:
                    print(":: Links lost. Re-linking...")
                create_links(tp, vp)
                linked = True
                print(f":: Routing active -> '{VIRT_NODE_NAME}'")
            elif not linked:
                linked = True
                print(f":: Routing active -> '{VIRT_NODE_NAME}' (links verified)")

            time.sleep(1)

    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
