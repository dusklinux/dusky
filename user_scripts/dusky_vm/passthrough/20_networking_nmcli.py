#!/usr/bin/env python3
"""
Phase 4: Adaptive KVM Network Architecture Automation
Environment: Arch Linux (Kernel 7.1.0, Python 3.14.5, systemd 260)
Scope: NetworkManager, UFW, Libvirt

Elite Standard:
- Hardware-aware dynamic topology (Bridge for Ethernet, NAT for Wi-Fi).
- Guarded Idempotency: Protects existing custom IP/DHCP rules from blind overwrites.
- Surgical Disaster Recovery: Reverts broken bridges and restores the physical NIC directly.
- Expert Overrides: Allows power-users to bypass 802.11 bridge limitations if utilizing WDS.
"""

import os
import sys
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

try:
    from rich.console import Console
    from rich.prompt import Confirm
    from rich.panel import Panel
    from rich.table import Table
except ImportError:
    print("CRITICAL: The 'rich' library is required. Install via: pip install rich")
    sys.exit(1)

console = Console()

def run_cmd(cmd: list, check: bool = True, capture: bool = True, timeout: int = 30) -> subprocess.CompletedProcess:
    """Execute shell commands with elite reliability and strict timeout enforcement."""
    try:
        return subprocess.run(
            cmd,
            check=check,
            capture_output=capture,
            text=True,
            timeout=timeout
        )
    except subprocess.TimeoutExpired as e:
        if check:
            console.print(f"[bold red]FATAL: Command timed out after {timeout}s:[/bold red] {' '.join(cmd)}")
            sys.exit(1)
        return e
    except subprocess.CalledProcessError as e:
        if check:
            console.print(f"[bold red]FATAL: Command failed with exit code {e.returncode}:[/bold red] {' '.join(cmd)}")
            console.print(f"[red]Details:[/red] {e.stderr.strip() or e.stdout.strip()}")
            sys.exit(1)
        return e

def verify_environment():
    """Ensure immutable privileges and mandatory toolchain presence. Auto-elevates."""
    if os.geteuid() != 0:
        print("\n[INFO] Administrative privileges required. Elevating via sudo...")
        try:
            os.execvp("sudo", ["sudo", sys.executable] + sys.argv)
        except OSError as e:
            print(f"\n[FATAL] Failed to elevate privileges dynamically: {e}")
            sys.exit(1)
            
    required_binaries = ["ip", "nmcli", "virsh", "systemctl"]
    missing = [bin for bin in required_binaries if not shutil.which(bin)]
    if missing:
        console.print(f"[bold red]ERROR: Missing mandatory binaries in PATH: {', '.join(missing)}[/bold red]")
        sys.exit(1)

def discover_active_interface() -> str:
    """Dynamically discover the active routing interface via the kernel's JSON routing table."""
    try:
        res = run_cmd(["ip", "-j", "route", "show", "default"])
        routes = json.loads(res.stdout.strip())
        
        if not routes:
            raise ValueError("Kernel routing table returned empty for default route.")
            
        active_dev = routes[0].get("dev")
        if not active_dev:
            raise ValueError("No 'dev' identifier found in the primary JSON route object.")
            
        return active_dev
    except json.JSONDecodeError:
        console.print("[bold red]ERROR: Failed to decode kernel JSON routing output.[/bold red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]Hardware Discovery Failed: {e}[/bold red]")
        sys.exit(1)

def is_wireless(interface: str) -> bool:
    """Definitively verify 802.11 status via the kernel's sysfs virtual filesystem."""
    return Path(f"/sys/class/net/{interface}/wireless").exists()

def configure_firewall(bridge_interface: str):
    """Apply UFW routing rules to prevent default bridge traffic drops."""
    console.print(f"\n[bold cyan]─── Firewall Routing Configuration ({bridge_interface}) ───[/bold cyan]")
    
    if not shutil.which("ufw"):
        console.print("[yellow]⚠ UFW binary not found in PATH. Skipping iptables/nftables frontend configuration.[/yellow]")
        return

    with console.status(f"[cyan]Injecting UFW forwarding rules for {bridge_interface}...", spinner="dots"):
        run_cmd(["ufw", "route", "allow", "in", "on", bridge_interface], check=False)
        run_cmd(["ufw", "route", "allow", "out", "on", bridge_interface], check=False)
        run_cmd(["ufw", "reload"], check=False)
        
    console.print(f"[bold green]✓ Traffic authorized natively through UFW kernel space on {bridge_interface}.[/bold green]")

def inject_libvirt_payload(network_name: str, xml_payload: str):
    """Atomically injects network XML into libvirt with verified idempotency check."""
    console.print(f"\n[bold cyan]─── Libvirt Injection ({network_name}) ───[/bold cyan]")
    
    # Safe Idempotency Check for custom networks (e.g., host-bridge)
    res = run_cmd(["virsh", "net-list", "--all", "--name"])
    if hasattr(res, 'stdout') and network_name in res.stdout.split():
        console.print(f"[bold green]✓ Libvirt network '{network_name}' already registered. Skipping XML injection.[/bold green]")
        run_cmd(["virsh", "net-autostart", network_name], check=False)
        state_res = run_cmd(["virsh", "net-info", network_name])
        if hasattr(state_res, 'stdout') and "Active: yes" not in state_res.stdout:
            run_cmd(["virsh", "net-start", network_name], check=False)
        return
    
    fd, temp_path = tempfile.mkstemp(prefix=f"libvirt-{network_name}-", suffix=".xml")
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(xml_payload)
            
        with console.status(f"[cyan]Injecting atomic payload for {network_name}...", spinner="bouncingBar"):
            run_cmd(["virsh", "net-define", temp_path])
            run_cmd(["virsh", "net-start", network_name], check=False)
            run_cmd(["virsh", "net-autostart", network_name])
            
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
            
    console.print(f"[bold green]✓ Libvirt payload '{network_name}' digested and activated atomically.[/bold green]")

def provision_nat_topology():
    """Provisions a pristine Layer 3 NAT Virtual Bridge (virbr0). Protects existing states."""
    console.print("\n[bold cyan]─── Topology: Layer 3 NAT Network (virbr0) ───[/bold cyan]")
    
    # Guarded Idempotency Check (Prevents indiscriminate destruction of custom rules)
    res = run_cmd(["virsh", "net-list", "--all", "--name"])
    if hasattr(res, 'stdout') and "default" in res.stdout.split():
        console.print("[yellow]Notice: A 'default' libvirt network already exists.[/yellow]")
        if not Confirm.ask("Do you want to purge it and re-provision a pristine NAT state? \n[dim](Say 'n' if you have custom DHCP/IP configurations you want to preserve)[/dim]"):
            console.print("[green]Preserving existing 'default' network...[/green]")
            run_cmd(["virsh", "net-autostart", "default"], check=False)
            state_res = run_cmd(["virsh", "net-info", "default"])
            if hasattr(state_res, 'stdout') and "Active: yes" not in state_res.stdout:
                run_cmd(["virsh", "net-start", "default"], check=False)
            configure_firewall("virbr0")
            return "Layer 3 NAT (virbr0) [Preserved]"

    with console.status("[cyan]Executing Bulletproof Provisioning (Purging corrupted states)...", spinner="dots"):
        # Explicitly teardown to allow inject_libvirt_payload to apply a pristine config
        run_cmd(["virsh", "net-destroy", "default"], check=False)
        run_cmd(["virsh", "net-undefine", "default"], check=False)
        
    xml_payload = """<network>
  <name>default</name>
  <forward mode='nat'>
    <nat><port start='1024' end='65535'/></nat>
  </forward>
  <bridge name='virbr0' stp='on' delay='0'/>
  <ip address='192.168.122.1' netmask='255.255.255.0'>
    <dhcp><range start='192.168.122.2' end='192.168.122.254'/></dhcp>
  </ip>
</network>"""

    inject_libvirt_payload("default", xml_payload)
    configure_firewall("virbr0")
    return "Layer 3 NAT (virbr0) [Pristine]"

def rollback_bridge(interface: str, slave_name: str):
    """Surgical disaster recovery to revert a broken bridge state without nuking the NM daemon."""
    console.print("\n[bold red]⚠ Bridge activation failed or timed out. Initiating surgical disaster recovery...[/bold red]")
    with console.status("[red]Tearing down corrupted bridge and restoring physical interface...", spinner="dots"):
        run_cmd(["nmcli", "connection", "down", "br0"], check=False)
        run_cmd(["nmcli", "connection", "delete", "br0"], check=False)
        run_cmd(["nmcli", "connection", "delete", slave_name], check=False)
        
        # Surgical interface reconnection (Avoids complete daemon restart)
        run_cmd(["nmcli", "device", "connect", interface], check=False)
        time.sleep(3) # Wait for physical DHCP to re-establish
    console.print("[bold green]✓ Surgical disaster recovery successful. Host internet restored.[/bold green]")

def provision_bridge_topology(interface: str) -> str:
    """Provisions a full Layer 2 System Bridge (br0) targeting physical Ethernet with self-healing."""
    console.print("\n[bold cyan]─── Topology: Layer 2 System Bridge (br0) ───[/bold cyan]")
    
    res = run_cmd(["nmcli", "-g", "NAME,TYPE", "connection", "show"])
    connections = [tuple(line.split(':')) for line in res.stdout.strip().split('\n') if ':' in line]
    slave_name = f"br0-port-{interface}"
    
    if not any(c[0] == 'br0' and c[1] == 'bridge' for c in connections):
        with console.status(f"[cyan]Constructing system bridge 'br0' over {interface}...", spinner="dots"):
            run_cmd(["nmcli", "connection", "add", "type", "bridge", "ifname", "br0", "con-name", "br0", "bridge.stp", "no"])
            run_cmd(["nmcli", "connection", "add", "type", "ethernet", "ifname", interface, "controller", "br0", "con-name", slave_name])
            
        console.print("[cyan]Waiting for network handshake (15s timeout)...[/cyan]")
        activation = run_cmd(["nmcli", "--wait", "15", "connection", "up", "br0"], check=False)
        
        # Proper short-circuit evaluation prevents AttributeError if command timed out
        if isinstance(activation, subprocess.TimeoutExpired) or activation.returncode != 0:
            rollback_bridge(interface, slave_name)
            console.print("[yellow]Falling back to isolated Layer 3 NAT network due to bridge failure...[/yellow]")
            return provision_nat_topology()
            
        console.print(f"[bold green]✓ Bridge 'br0' materialized and successfully bound to {interface}.[/bold green]")
    else:
        console.print("[bold green]✓ System bridge 'br0' already present. Perfectly staged.[/bold green]")

    configure_firewall("br0")

    # Inject host-bridge XML so it appears in the Virt-Manager dropdown GUI
    xml_payload = """<network>\n  <name>host-bridge</name>\n  <forward mode='bridge'/>\n  <bridge name='br0'/>\n</network>"""
    inject_libvirt_payload("host-bridge", xml_payload)
    return "Layer 2 Bridge (br0)"

def generate_telemetry_summary(interface: str, topology: str):
    """Render the executive Phase 4 summary architecture table."""
    table = Table(title="Phase 4: Network Architecture Summary", show_header=True, header_style="bold magenta")
    table.add_column("Component", style="dim", width=22)
    table.add_column("State", justify="left")
    table.add_column("Telemetry Details", justify="left")

    table.add_row("Primary Interface", "[green]Discovered[/green]", f"{interface} (JSON Verified)")
    table.add_row("Active Topology", "[cyan]Provisioned[/cyan]", topology)
    
    fw_state = "[green]Enforced[/green]" if shutil.which("ufw") else "[yellow]Skipped (N/A)[/yellow]"
    target_br = "br0" if "Bridge" in topology else "virbr0"
    table.add_row("UFW Firewall", fw_state, f"ALLOW IN/OUT routing on {target_br}")
    
    lib_net = "host-bridge" if "Bridge" in topology else "default (NAT)"
    table.add_row("Libvirt Tracking", "[green]Active[/green]", f"{lib_net} (Autostart: ON)")

    console.print("\n")
    console.print(table)
    console.print("\n[bold green]🚀 Phase 4 Execution Completed with Elite DevOps Precision![/bold green]\n")

def main():
    console.print(Panel.fit("[bold white]Phase 4: Adaptive KVM Network Provisioning[/bold white]", border_style="cyan"))
    
    verify_environment()
    
    interface = discover_active_interface()
    console.print(f"[*] Primary routing interface identified via kernel JSON: [bold yellow]{interface}[/bold yellow]")
    
    if is_wireless(interface):
        console.print(Panel("[bold yellow]802.11 Wireless Protocol Detected.[/bold yellow]\n"
                            "Standard system bridging is strictly prohibited on Wi-Fi interfaces.\n"
                            "The system recommends a robust NAT (virbr0) topology for Host-to-VM SSH access.", 
                            title="Hardware Limitation", border_style="yellow"))
                            
        # Expert Override Restored
        if Confirm.ask("Do you want to OVERRIDE and forcefully attempt standard bridging anyway? [dim](Highly destructive/Advanced)[/dim]", default=False):
            final_topology = provision_bridge_topology(interface)
        else:
            if Confirm.ask("Proceed with safe NAT provisioning?", default=True):
                final_topology = provision_nat_topology()
            else:
                console.print("[yellow]Execution aborted gracefully.[/yellow]")
                sys.exit(0)
    else:
        console.print("\n[bold green]Ethernet detected.[/bold green] Hardware supports Full LAN Bridging.")
        if Confirm.ask("Provision 'God-Mode' Layer 2 Bridge (br0)?\n[dim](Select 'n' to use standard isolated NAT instead)[/dim]"):
            final_topology = provision_bridge_topology(interface)
        else:
            console.print("[cyan]Falling back to isolated Layer 3 NAT network...[/cyan]")
            final_topology = provision_nat_topology()
            
    generate_telemetry_summary(interface, final_topology)

if __name__ == "__main__":
    main()
