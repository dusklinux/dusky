#!/usr/bin/env python3
# Architecture: Arch Linux / Wayland / Hyprland
# Target Ecosystem: PipeWire 1.4+ / WirePlumber Port Diagnostics
# Language: Python 3.14

import sys
import json
import subprocess

VIRT_NODE_NAME = "Virtual_Mic_Tx"


def run_cmd(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, text=True).strip()
    except subprocess.CalledProcessError:
        return ""


def resolve_node_name(graph: list[dict], node_id: int | None) -> str:
    if node_id is None:
        return "Unknown"
    for obj in graph:
        if obj.get("type") == "PipeWire:Interface:Node" and obj.get("id") == node_id:
            return obj.get("info", {}).get("props", {}).get("node.name", "Unknown")
    return "Unknown"


def get_node_id(obj: dict) -> int | None:
    raw = obj.get("info", {}).get("props", {}).get("node.id")
    if raw is not None:
        try:
            return int(raw)
        except (ValueError, TypeError):
            return None
    return None


def main() -> None:
    print("==================================================")
    print(":: PipeWire Deep Port & Format Diagnostic")
    print("==================================================")

    # PipeWire version
    pw_version = run_cmd(["pw-cli", "--version"])
    if pw_version:
        for line in pw_version.splitlines():
            print(f"  {line}")
    else:
        print("  Warning: Could not query PipeWire version.")
    print()

    # Parse graph
    dump_out = run_cmd(["pw-dump"])
    if not dump_out:
        print("Fatal: Could not communicate with PipeWire daemon.")
        sys.exit(1)

    try:
        graph: list[dict] = json.loads(dump_out)
    except json.JSONDecodeError as e:
        print(f"Fatal: PipeWire dump returned invalid JSON: {e}")
        sys.exit(1)

    # Locate Virtual Node
    virt_node = None
    for obj in graph:
        if obj.get("type") == "PipeWire:Interface:Node":
            if obj.get("info", {}).get("props", {}).get("node.name") == VIRT_NODE_NAME:
                virt_node = obj
                break

    if not virt_node:
        print(f"[!] Virtual Node '{VIRT_NODE_NAME}' NOT FOUND. Ensure router script is running.")
        sys.exit(1)

    virt_id: int = virt_node["id"]

    # Virtual Mic node properties
    virt_props = virt_node.get("info", {}).get("props", {})
    virt_state = virt_node.get("info", {}).get("state", "UNKNOWN")
    print(f"[VIRTUAL MIC] Node ID: {virt_id} (State: {virt_state})")
    print(f"  audio.format  : {virt_props.get('audio.format', 'Not set')}")
    print(f"  audio.rate    : {virt_props.get('audio.rate', 'Not set')}")
    print(f"  audio.channels: {virt_props.get('audio.channels', 'Not set')}")

    # Virtual Mic ports
    virt_ports = [
        o for o in graph
        if o.get("type") == "PipeWire:Interface:Port" and get_node_id(o) == virt_id
    ]
    print(f"  Total Ports Exposed: {len(virt_ports)}")
    for p in virt_ports:
        p_id = p["id"]
        p_dir = p.get("info", {}).get("direction", "unknown")
        p_name = p.get("info", {}).get("props", {}).get("port.name", "Unknown")
        print(f"    -> Port {p_id} [{p_dir.upper()}] : {p_name}")

    # Default source
    default_source = run_cmd(["pactl", "get-default-source"])
    if default_source:
        print(f"  Default Source : {default_source}")

    # Locate active Firefox nodes
    print("\n[ACTIVE FIREFOX STREAMS]")
    ff_nodes = [
        o for o in graph
        if o.get("type") == "PipeWire:Interface:Node"
        and o.get("info", {}).get("props", {}).get("media.class") == "Stream/Output/Audio"
        and "firefox" in str(o.get("info", {}).get("props", {})).lower()
    ]

    if not ff_nodes:
        print("  -> No Firefox audio streams detected. Is media playing?")

    ff_node_ids: set[int] = set()
    for ff in ff_nodes:
        ff_id: int = ff["id"]
        ff_node_ids.add(ff_id)
        state = ff.get("info", {}).get("state", "UNKNOWN")
        print(f"  Firefox Node ID: {ff_id} (State: {state})")

        ff_ports = [
            o for o in graph
            if o.get("type") == "PipeWire:Interface:Port" and get_node_id(o) == ff_id
        ]
        print(f"    Total Ports Exposed: {len(ff_ports)}")
        for p in ff_ports:
            p_id = p["id"]
            p_dir = p.get("info", {}).get("direction", "unknown")
            p_chan = p.get("info", {}).get("props", {}).get("audio.channel", "Unknown")
            print(f"      -> Port {p_id} [{p_dir.upper()}] : Channel '{p_chan}'")

    # Inspect links TO Virtual Mic
    print("\n[LINKS -> VIRTUAL MIC]")
    inbound_links = [
        o for o in graph
        if o.get("type") == "PipeWire:Interface:Link"
        and o.get("info", {}).get("input-node-id") == virt_id
    ]

    if not inbound_links:
        print("  -> No inbound links attached to Virtual Mic.")
    else:
        for link in inbound_links:
            l_id = link["id"]
            info = link.get("info", {})
            state = info.get("state", "UNKNOWN")
            fmt = info.get("format") or {}
            err = info.get("error")

            src_port = info.get("output-port-id")
            dst_port = info.get("input-port-id")
            src_node_id = info.get("output-node-id")
            src_node_name = resolve_node_name(graph, src_node_id)

            audio_fmt = fmt.get("audio.format", "UNNEGOTIATED")
            audio_rate = fmt.get("audio.rate", "UNNEGOTIATED")

            print(f"  Link {l_id}: Port {src_port} (Node {src_node_id} '{src_node_name}') -> Port {dst_port}")
            print(f"    State : {state}")
            print(f"    Format: {audio_fmt} @ {audio_rate}Hz")
            if err is not None:
                print(f"    Error : {err}")

    # Inspect where Firefox streams are ACTUALLY routed
    print("\n[FIREFOX OUTBOUND ROUTING]")
    if not ff_node_ids:
        print("  -> No Firefox nodes to inspect.")
    else:
        ff_outbound = [
            o for o in graph
            if o.get("type") == "PipeWire:Interface:Link"
            and o.get("info", {}).get("output-node-id") in ff_node_ids
        ]
        if not ff_outbound:
            print("  -> No outbound links from Firefox nodes found.")
        else:
            for link in ff_outbound:
                l_id = link["id"]
                info = link.get("info", {})
                state = info.get("state", "UNKNOWN")
                fmt = info.get("format") or {}
                err = info.get("error")

                src_port = info.get("output-port-id")
                src_node_id = info.get("output-node-id")
                dst_port = info.get("input-port-id")
                dst_node_id = info.get("input-node-id")
                dst_node_name = resolve_node_name(graph, dst_node_id)

                is_virt = dst_node_id == virt_id
                marker = " [-> VIRTUAL MIC]" if is_virt else ""

                audio_fmt = fmt.get("audio.format", "UNNEGOTIATED")
                audio_rate = fmt.get("audio.rate", "UNNEGOTIATED")

                print(f"  Link {l_id}: Firefox Node {src_node_id} Port {src_port} -> Node {dst_node_id} '{dst_node_name}' Port {dst_port}{marker}")
                print(f"    State : {state}")
                print(f"    Format: {audio_fmt} @ {audio_rate}Hz")
                if err is not None:
                    print(f"    Error : {err}")

    print("\n==================================================")
    print(":: Diagnostic Complete")
    print("==================================================")


if __name__ == "__main__":
    main()
