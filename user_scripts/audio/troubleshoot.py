#!/usr/bin/env python3
# Architecture: Arch Linux / Wayland / Hyprland
# Target Ecosystem: PipeWire 1.4+ / WirePlumber Flow Diagnostics

import sys
import json
import subprocess

VIRT_NODE_NAME = "Virtual_Mic_Tx"

def run_cmd(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, text=True).strip()
    except subprocess.CalledProcessError:
        return ""

def main() -> None:
    print("==================================================")
    print(":: PipeWire Audio Flow Diagnostic")
    print("==================================================\n")

    # 1. Fetch raw graph
    dump_out = run_cmd(["pw-dump"])
    if not dump_out:
        print("Fatal: Could not communicate with PipeWire daemon.")
        sys.exit(1)
        
    graph = json.loads(dump_out)
    
    # 2. Locate Virtual Node
    virt_node = None
    for obj in graph:
        if obj.get("type") == "PipeWire:Interface:Node" and obj.get("info", {}).get("props", {}).get("node.name") == VIRT_NODE_NAME:
            virt_node = obj
            break

    if not virt_node:
        print(f"[!] Virtual Node '{VIRT_NODE_NAME}' NOT FOUND. Is the router script running?")
        sys.exit(1)

    virt_id = virt_node["id"]
    virt_state = virt_node.get("info", {}).get("state", "UNKNOWN")
    virt_err = virt_node.get("info", {}).get("error", "None")
    
    print(f"[VIRTUAL MIC] ID: {virt_id}")
    print(f"  State : {virt_state}")
    if virt_err and virt_err != "None":
        print(f"  Error : {virt_err}")
    
    # Check Volume via wpctl
    wp_vol = run_cmd(["wpctl", "get-volume", str(virt_id)])
    print(f"  Volume: {wp_vol if wp_vol else 'Unmanaged by WirePlumber'}")

    # 3. Trace Links flowing into the Virtual Node
    print("\n[ACTIVE INBOUND LINKS]")
    links = [obj for obj in graph if obj.get("type") == "PipeWire:Interface:Link" and obj.get("info", {}).get("input-node-id") == virt_id]
    
    if not links:
        print("  -> No applications are currently linked to the Virtual Mic.")
        sys.exit(0)

    for link in links:
        link_id = link["id"]
        info = link.get("info", {})
        state = info.get("state", "UNKNOWN")
        format_info = info.get("format", {})
        err = info.get("error", "None")
        
        src_node_id = info.get("output-node-id")
        src_port_id = info.get("output-port-id")
        dst_port_id = info.get("input-port-id")
        
        # Resolve Source Node Name
        src_name = "Unknown Source"
        for obj in graph:
            if obj["id"] == src_node_id:
                props = obj.get("info", {}).get("props", {})
                src_name = props.get("application.name") or props.get("node.name") or str(src_node_id)
                src_state = obj.get("info", {}).get("state", "UNKNOWN")
                break

        print(f"  Link ID {link_id}: {src_name} (Port {src_port_id}) -> Virtual Mic (Port {dst_port_id})")
        print(f"    Link State  : {state}")
        print(f"    Source State: {src_state}")
        if format_info:
            # Extract basic format details if negotiated
            audio_fmt = format_info.get("audio.format", "Unnegotiated")
            audio_rate = format_info.get("audio.rate", "Unnegotiated")
            print(f"    Format      : {audio_fmt} @ {audio_rate}Hz")
        else:
            print("    Format      : NEGOTIATION FAILED OR PENDING")
        
        if err and err != "None":
            print(f"    Error       : {err}")
        print("")

    print("==================================================")
    print(":: Diagnostic Complete")
    print("==================================================")

if __name__ == "__main__":
    main()
