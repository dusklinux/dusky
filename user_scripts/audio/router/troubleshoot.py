#!/usr/bin/env python3
"""Inspect the virtual microphone and the selected application's PipeWire links."""

import argparse
import json
import subprocess

import audio_routing_output_to_mic as router


def run_cmd(*args: str) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Warning: {args[0]}: {exc}")
        return ""
    if result.returncode:
        print(f"Warning: {args[0]}: {result.stderr.strip() or result.returncode}")
        return ""
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", nargs="?", help="application to inspect (default: daemon target, or mpv)")
    args = parser.parse_args()
    status = router.ipc_send({"action": "ping"}) or {}
    target = args.app or status.get("target", router.DEFAULT_APP)

    print(":: PipeWire Port & Format Diagnostic")
    print(run_cmd("pw-cli", "--version") or "PipeWire version unavailable")
    dump = run_cmd("pw-dump", "--no-colors")
    if not dump:
        return 1
    try:
        graph = json.loads(dump)
    except json.JSONDecodeError as exc:
        print(f"Fatal: invalid PipeWire JSON: {exc}")
        return 1
    if not isinstance(graph, list):
        print("Fatal: expected a PipeWire object array")
        return 1

    snap = router.build_snapshot(graph, target)
    nodes = {
        obj["id"]: obj for obj in graph
        if obj.get("type") == "PipeWire:Interface:Node"
    }
    virt_id = snap.virt_node_id
    if virt_id is None:
        print(f"[!] Virtual node '{router.VIRT_NODE_NAME}' not found.")
    else:
        virt = nodes[virt_id]
        props = virt.get("info", {}).get("props", {})
        print(f"\n[VIRTUAL MIC] Node {virt_id}: {virt['info'].get('state', 'unknown')}")
        for key in ("media.class", "audio.format", "audio.rate", "audio.channels"):
            print(f"  {key}: {props.get(key, 'Not set')}")
        print(f"  Input ports: {snap.virt_ports}")
    default_source = run_cmd("pactl", "get-default-source")
    print(f"  Default source: {default_source or 'Unavailable'}")

    print(f"\n[APPLICATION STREAMS: {target}]")
    if not snap.streams:
        print("  No matching playback streams.")
    for stream in snap.streams.values():
        print(f"  Node {stream.node_id}: {stream.app_name} / {stream.media_name} ({stream.state})")
        print(f"    Output ports: {stream.ports}")

    print("\n[APPLICATION OUTBOUND / VIRTUAL MIC INBOUND LINKS]")
    found = False
    for obj in graph:
        if obj.get("type") != "PipeWire:Interface:Link":
            continue
        info = obj.get("info", {})
        src, dst = info.get("output-node-id"), info.get("input-node-id")
        if src not in snap.streams and (virt_id is None or dst != virt_id):
            continue
        found = True
        src_props = nodes.get(src, {}).get("info", {}).get("props", {})
        dst_props = nodes.get(dst, {}).get("info", {}).get("props", {})
        print(
            f"  Link {obj['id']}: {src_props.get('node.name', src)}:{info.get('output-port-id')}"
            f" -> {dst_props.get('node.name', dst)}:{info.get('input-port-id')}"
        )
        # Native DSP links often report only mediaType/mediaSubtype/format;
        # absent rate fields do not mean that negotiation failed.
        print(f"    State: {info.get('state', 'unknown')}; format: {json.dumps(info.get('format'))}")
        if info.get("error"):
            print(f"    Error: {info['error']}")
    if not found:
        print("  No matching links.")
    return 0 if virt_id is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
