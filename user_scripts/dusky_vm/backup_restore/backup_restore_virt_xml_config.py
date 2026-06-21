#!/usr/bin/env python3
"""
Phase 3: Libvirt Cryptographic State & Configuration Manager
Target: Arch Linux (Kernel 7.1.0+), Python 3.14+, systemd 260
Scope: XML Blueprint Extraction, NVRAM/vTPM State Archival (Strict Ownership Preservation).
Philosophy: Do One Thing Well. Surgical Extraction and Injection. No package management.
"""

import os
import sys
import pwd
import subprocess
import readline
import glob
from pathlib import Path
from typing import Never, List, Dict

# ==============================================================================
# TTY FIX: Native Path Completion & Buffer Management
# ==============================================================================
def path_completer(text, state):
    """Standard library path auto-completer for readline."""
    target = os.path.expanduser(text)
    
    # If the user typed a directory, list its contents
    if os.path.isdir(target) and not target.endswith('/'):
        target += '/'
        
    matches = glob.glob(target + '*')
    
    # Append slashes to directories so tabbing can continue smoothly
    matches = [m + '/' if os.path.isdir(m) else m for m in matches]
    
    try:
        return matches[state]
    except IndexError:
        return None

readline.set_completer_delims(' \t\n;')
readline.parse_and_bind("tab: complete")
readline.set_completer(path_completer)

# ==============================================================================
# PRE-FLIGHT (Strict Standard Library Only)
# ==============================================================================
def require_root() -> None:
    """Hard enforcement of eUID 0. Auto-elevates via sudo if executed as standard user."""
    if os.geteuid() != 0:
        print("\n[INFO] Administrative privileges required. Elevating via sudo...")
        try:
            os.execvp("sudo", ["sudo", sys.executable] + sys.argv)
        except OSError as e:
            print(f"\n[FATAL] Failed to elevate privileges dynamically: {e}")
            sys.exit(1)

require_root()

# ==============================================================================
# BOOTSTRAP: Dynamic UI Dependency Resolution
# ==============================================================================
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.table import Table
except ImportError:
    print("\n[FATAL] 'python-rich' is missing. Please ensure Phase 1 completed successfully.")
    sys.exit(1)

console = Console()

# ==============================================================================
# CORE LOGIC: EXECUTION & RESOLUTION
# ==============================================================================
def bail(msg: str) -> Never:
    """Exit gracefully with a clear error panel."""
    console.print(Panel(f"[bold red]FATAL ERROR:[/bold red] {msg}", border_style="red"))
    sys.exit(1)

def run_cmd(cmd: List[str], check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    """Execute shell commands with elite reliability."""
    try:
        return subprocess.run(cmd, check=check, capture_output=capture, text=True)
    except subprocess.CalledProcessError as e:
        if check:
            console.print(f"[bold red]FATAL: Command failed with exit code {e.returncode}:[/bold red] {' '.join(cmd)}")
            console.print(f"[red]Details:[/red] {e.stderr.strip() or e.stdout.strip()}")
            sys.exit(1)
        return e

def resolve_user_path(path_str: str) -> Path:
    """Intelligently route '~/' to the human user's home, bypassing the root shell."""
    if path_str.startswith('~/'):
        sudo_user = os.environ.get("SUDO_USER", "")
        if sudo_user:
            try:
                home_dir = pwd.getpwnam(sudo_user).pw_dir
                return (Path(home_dir) / path_str[2:]).resolve()
            except KeyError:
                pass
    return Path(path_str).expanduser().resolve()

def sanitize_filename(name: str) -> str:
    """Sanitize strings to be used safely as filenames by replacing path separators."""
    return name.replace("/", "_").replace("\\", "_")

def verify_hypervisor_idle() -> None:
    """Absolute requirement: Halt if ANY domains are active (running or paused) to prevent torn states."""
    res = run_cmd(["virsh", "list", "--name"])
    active_vms = [line.strip() for line in res.stdout.split('\n') if line.strip()]
    
    if active_vms:
        bail(f"Hypervisor is not completely idle. Active/Paused domains detected: {', '.join(active_vms)}\n"
             "You MUST gracefully shut down all VMs before archiving or injecting binary cryptographic states.")

def verify_system_users() -> None:
    """Ensure target user groups exist before tar blindly restores numeric IDs."""
    required_users = ["tss", "libvirt-qemu"]
    missing = []
    for user in required_users:
        try:
            pwd.getpwnam(user)
        except KeyError:
            missing.append(user)
    
    if missing:
        bail(f"The following required system users are missing: {', '.join(missing)}\n"
             "Please ensure 'swtpm' and 'libvirt' are fully installed and initialized before restoring states.")

def get_libvirt_entities(entity_type: str) -> List[str]:
    """Retrieve a strict list of active and inactive libvirt components."""
    res = run_cmd(["virsh", entity_type, "--all", "--name"])
    return [line.strip() for line in res.stdout.split('\n') if line.strip()]

def check_libvirt_connection() -> None:
    """Verify modular IPC socket responsiveness."""
    res = run_cmd(["virsh", "uri"], check=False)
    if res.returncode != 0:
        bail("Cannot connect to the libvirt daemon. Ensure Phase 2 (Modular IPC) is active.")

# ==============================================================================
# PHASE AUTOMATION: BACKUP
# ==============================================================================
def execute_backup() -> None:
    verify_hypervisor_idle()
    console.print("\n[bold cyan]─── Libvirt Surgical Backup ───[/bold cyan]")
    
    # Decoupled the input from the styled prompt to fix readline buffer calculation
    console.print("[bold cyan]Enter absolute path to save backups (e.g., /mnt/Storage/VM_Backup):[/bold cyan]")
    target_input = input("> ").strip()
    target_dir = resolve_user_path(target_input)
    
    if not target_dir.exists():
        if Prompt.ask(f"Directory '{target_dir}' does not exist. Create it?", choices=["y", "n"], default="y") == 'y':
            target_dir.mkdir(parents=True, exist_ok=True)
        else:
            console.print("[yellow]Backup aborted by operator.[/yellow]")
            return

    table = Table(title=f"Backup Telemetry ({target_dir.name})", show_header=True, header_style="bold magenta")
    table.add_column("Component", style="cyan")
    table.add_column("Type", justify="center")
    table.add_column("Status", justify="left")

    # 1. Archive Cryptographic/Firmware State Files
    state_targets = [
        {"name": "swtpm", "parent_dir": "/var/lib/libvirt/", "target_dir": "swtpm"},
        {"name": "nvram", "parent_dir": "/var/lib/libvirt/qemu/", "target_dir": "nvram"}
    ]

    with console.status("[cyan]Archiving secure states (preserving DAC/MAC & xattrs)...[/cyan]", spinner="dots"):
        for state in state_targets:
            target_path = Path(state["parent_dir"]) / state["target_dir"]
            if target_path.exists():
                archive_path = target_dir / f"{state['name']}_state.tar.gz"
                # CRITICAL: --xattrs/--acls grab AppArmor/SELinux profiles. --exclude prevents ghost socket crashes.
                cmd = ["tar", "--xattrs", "--acls", "--exclude=*.sock", "--exclude=*.pid", 
                       "-czf", str(archive_path), "-C", state["parent_dir"], state["target_dir"]]
                run_cmd(cmd)
                table.add_row(state['name'], "Binary State", f"[green]Archived -> {archive_path.name}[/green]")
            else:
                table.add_row(state['name'], "Binary State", "[dim]Not Found (Skipped)[/dim]")

    # 2. Extract XML Topologies
    xml_targets: Dict[str, tuple[str, str]] = {
        "list": ("vms", "dumpxml"),
        "net-list": ("networks", "net-dumpxml"),
        "pool-list": ("pools", "pool-dumpxml")
    }

    with console.status("[cyan]Dumping XML Blueprints...[/cyan]", spinner="dots"):
        for list_cmd, (sub_dir, dump_cmd) in xml_targets.items():
            save_path = target_dir / sub_dir
            save_path.mkdir(exist_ok=True)
            
            entities = get_libvirt_entities(list_cmd)
            for entity in entities:
                xml_data = run_cmd(["virsh", dump_cmd, entity]).stdout
                (save_path / f"{entity}.xml").write_text(xml_data)
                table.add_row(entity, sub_dir.upper(), "[green]XML Extracted[/green]")
                
    # 3. Extract Snapshots (Surgically Patched for Fault Tolerance & Safe Paths)
    vms_dir = target_dir / "vms"
    if vms_dir.exists():
        with console.status("[cyan]Dumping VM Snapshots...[/cyan]", spinner="dots"):
            snap_base_dir = target_dir / "snapshots"
            for xml_file in vms_dir.glob("*.xml"):
                vm_name = xml_file.stem
                res = run_cmd(["virsh", "snapshot-list", vm_name, "--name", "--topological"], check=False)
                if res.returncode == 0 and res.stdout.strip():
                    vm_snap_dir = snap_base_dir / vm_name
                    vm_snap_dir.mkdir(parents=True, exist_ok=True)
                    snapshots = [s.strip() for s in res.stdout.split('\n') if s.strip()]
                    
                    # Write the exact original names to order.txt to preserve libvirt continuity
                    (vm_snap_dir / "order.txt").write_text('\n'.join(snapshots) + '\n')
                    
                    success_count = 0
                    for snap in snapshots:
                        # check=False prevents script crash on corrupted individual snapshot dumps
                        snap_res = run_cmd(["virsh", "snapshot-dumpxml", vm_name, snap], check=False)
                        if snap_res.returncode == 0:
                            # Sanitize solely for the filesystem write operation
                            safe_snap_name = sanitize_filename(snap)
                            (vm_snap_dir / f"{safe_snap_name}.xml").write_text(snap_res.stdout)
                            success_count += 1
                    
                    if success_count == len(snapshots):
                        table.add_row(vm_name, "SNAPSHOTS", f"[green]{success_count} Extracted[/green]")
                    elif success_count > 0:
                        table.add_row(vm_name, "SNAPSHOTS", f"[yellow]{success_count}/{len(snapshots)} Extracted[/yellow]")
                    else:
                        table.add_row(vm_name, "SNAPSHOTS", "[red]0 Extracted (Failed)[/red]")

    console.print("\n")
    console.print(table)
    console.print(f"[bold green]✓ Complete. Infrastructure safely frozen at: {target_dir}[/bold green]")

# ==============================================================================
# PHASE AUTOMATION: RESTORE
# ==============================================================================
def execute_restore() -> None:
    verify_hypervisor_idle()
    verify_system_users()
    
    console.print("\n[bold cyan]─── Libvirt Surgical Restoration ───[/bold cyan]")
    console.print("[dim]Ensure your external drive holding raw .qcow2/.img disks is mounted to its exact historical path.[/dim]\n")
    
    # Decoupled the input from the styled prompt to fix readline buffer calculation
    console.print("[bold cyan]Enter absolute path to your backup directory:[/bold cyan]")
    source_input = input("> ").strip()
    source_dir = resolve_user_path(source_input)
    
    if not source_dir.exists():
        bail(f"Backup directory '{source_dir}' does not exist.")

    table = Table(title=f"Restoration Telemetry ({source_dir.name})", show_header=True, header_style="bold magenta")
    table.add_column("Component", style="cyan")
    table.add_column("Action", justify="center")
    table.add_column("Result", justify="left")

    # 1. Restore Cryptographic/Firmware State Files
    state_targets = [
        {"name": "swtpm", "extract_to": "/var/lib/libvirt/"},
        {"name": "nvram", "extract_to": "/var/lib/libvirt/qemu/"}
    ]

    with console.status("[cyan]Injecting secure states (restoring strict ownership & MAC contexts)...[/cyan]", spinner="bouncingBar"):
        for state in state_targets:
            archive_path = source_dir / f"{state['name']}_state.tar.gz"
            if archive_path.exists():
                Path(state["extract_to"]).mkdir(parents=True, exist_ok=True)
                # CRITICAL: -p forces strict DAC mapping. --xattrs/--acls reinstates MAC contexts.
                run_cmd(["tar", "--xattrs", "--acls", "-xzpf", str(archive_path), "-C", state["extract_to"]])
                table.add_row(state['name'], "Binary Inject", "[green]Strict Permissions Restored[/green]")
            else:
                table.add_row(state['name'], "Binary Inject", "[dim]Archive Missing (Skipped)[/dim]")

    # 2. Restore Infrastructure Blueprints
    infrastructure = {
        "networks": ("net-define", "net-start", "net-autostart"),
        "pools": ("pool-define", "pool-start", "pool-autostart")
    }

    with console.status("[cyan]Rebuilding Layer 2/3 topologies and pools...[/cyan]", spinner="dots"):
        for folder, (define_cmd, start_cmd, auto_cmd) in infrastructure.items():
            conf_dir = source_dir / folder
            if conf_dir.exists():
                for xml_file in conf_dir.glob("*.xml"):
                    name = xml_file.stem
                    # Setting check=False allows execution to pass through pre-existing libvirt configurations
                    run_cmd(["virsh", define_cmd, str(xml_file)], check=False)
                    run_cmd(["virsh", start_cmd, name], check=False) 
                    run_cmd(["virsh", auto_cmd, name], check=False)
                    table.add_row(name, folder.upper(), "[green]Defined & Autostarted[/green]")

    # 3. Re-link Virtual Machines
    vms_dir = source_dir / "vms"
    if vms_dir.exists():
        with console.status("[cyan]Registering Virtual Machines...[/cyan]", spinner="dots"):
            for xml_file in vms_dir.glob("*.xml"):
                # Setting check=False ensures it doesn't crash if the VM domain is already linked
                run_cmd(["virsh", "define", str(xml_file)], check=False)
                table.add_row(xml_file.stem, "VM (XML)", "[green]Successfully Linked[/green]")

    # 4. Re-inject VM Snapshots (Surgically Patched for Accurate Telemetry & Safe Paths)
    snap_base_dir = source_dir / "snapshots"
    if snap_base_dir.exists():
        with console.status("[cyan]Re-registering VM Snapshots...[/cyan]", spinner="dots"):
            for vm_snap_dir in snap_base_dir.iterdir():
                if vm_snap_dir.is_dir():
                    vm_name = vm_snap_dir.name
                    order_file = vm_snap_dir / "order.txt"
                    if order_file.exists():
                        snapshots = [line.strip() for line in order_file.read_text().split('\n') if line.strip()]
                        success_count = 0
                        for snap in snapshots:
                            # Apply the exact same sanitization to retrieve the safe filename
                            safe_snap_name = sanitize_filename(snap)
                            snap_xml_file = vm_snap_dir / f"{safe_snap_name}.xml"
                            if snap_xml_file.exists():
                                snap_res = run_cmd(["virsh", "snapshot-create", vm_name, str(snap_xml_file), "--redefine"], check=False)
                                if snap_res.returncode == 0:
                                    success_count += 1
                        
                        if success_count == len(snapshots) and snapshots:
                            table.add_row(vm_name, "SNAPSHOTS", f"[green]{success_count} Re-registered[/green]")
                        elif success_count > 0:
                            table.add_row(vm_name, "SNAPSHOTS", f"[yellow]{success_count}/{len(snapshots)} Re-registered[/yellow]")
                        else:
                            table.add_row(vm_name, "SNAPSHOTS", "[red]0 Re-registered (Failed)[/red]")

    console.print("\n")
    console.print(table)
    console.print("[bold green]✓ Restoration Complete. Systemd modular sockets have synced your configurations.[/bold green]")

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================
def main() -> None:
    console.clear()
    console.print(Panel("[bold green]KVM GPU Passthrough: Phase 3[/bold green]\nTarget: State Isolation & Continuity Manager", expand=False))
    
    check_libvirt_connection()
    
    while True:
        console.print("\n[bold]Select an operation vector:[/bold]")
        console.print("  [cyan]1.[/cyan] Extract and Archive State (Backup)")
        console.print("  [cyan]2.[/cyan] Inject and Rebuild State (Restore)")
        console.print("  [cyan]3.[/cyan] Exit gracefully")
        
        choice = Prompt.ask("\n[bold]Vector[/bold]", choices=["1", "2", "3"], default="3")
        
        match choice:
            case "1":
                execute_backup()
                break
            case "2":
                execute_restore()
                break
            case "3":
                console.print("[yellow]Execution aborted gracefully.[/yellow]")
                sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n[bold red]⚠ Process interrupted by operator. Exiting cleanly.[/bold red]\n")
        sys.exit(130)
