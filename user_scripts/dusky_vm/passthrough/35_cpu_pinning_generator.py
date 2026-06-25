#!/usr/bin/env python3
"""
Phase 6: CPU Topology & Contiguous Pinning Generator
Target: Arch Linux, Python 3.14.5+
Philosophy: Smart core alignment (P/E Core detection, SMT grouping), idempotent XML modification.
"""

import os
import sys
import re
import tempfile
import subprocess
from pathlib import Path
from typing import Never, Tuple

# Standard user execution (libvirt group membership assumed)

# Ensure rich is installed
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.table import Table
except ImportError:
    print("\n[FATAL] 'python-rich' is missing. Please run: sudo pacman -S python-rich")
    sys.exit(1)

console = Console(force_terminal=True, force_interactive=True)

# ==============================================================================
# Helper functions
# ==============================================================================
def bail(msg: str) -> Never:
    """Exit gracefully with a clear error panel."""
    console.print(Panel(f"[bold red]FATAL ERROR:[/bold red] {msg}", border_style="red"))
    sys.exit(1)

def run_cmd(cmd: list, check: bool = True) -> Tuple[int, str]:
    """Execute shell commands and return exit code and stdout."""
    res = subprocess.run(cmd, capture_output=True, text=True)
    if check and res.returncode != 0:
        bail(f"Command failed: {' '.join(cmd)}\nError: {res.stderr.strip()}")
    return res.returncode, res.stdout.strip()

# ==============================================================================
# CPU TOPOLOGY DISCOVERY
# ==============================================================================
def get_cpu_topology() -> list[list[int]]:
    """
    Scans /sys/devices/system/cpu to map logical CPUs to physical cores.
    Returns a list of cores, where each core is a list of its sibling logical CPU IDs.
    """
    cpu_path = Path("/sys/devices/system/cpu")
    cores_dict = {}
    
    for cpu_dir in cpu_path.glob("cpu[0-9]*"):
        try:
            cpu_id = int(cpu_dir.name[3:])
            core_id_file = cpu_dir / "topology" / "core_id"
            siblings_file = cpu_dir / "topology" / "thread_siblings_list"
            package_id_file = cpu_dir / "topology" / "physical_package_id"
            
            if core_id_file.exists() and siblings_file.exists():
                core_id = int(core_id_file.read_text().strip())
                package_id = int(package_id_file.read_text().strip())
                siblings_str = siblings_file.read_text().strip()
                
                core_key = (package_id, core_id)
                if core_key not in cores_dict:
                    siblings = []
                    for part in siblings_str.split(','):
                        if '-' in part:
                            start, end = part.split('-')
                            siblings.extend(range(int(start), int(end) + 1))
                        else:
                            siblings.append(int(part))
                    cores_dict[core_key] = sorted(list(set(siblings)))
        except (ValueError, OSError):
            continue

    # Sort cores by their lowest logical CPU ID to maintain ordering
    sorted_cores = sorted(cores_dict.values(), key=lambda c: c[0])
    return sorted_cores

# ==============================================================================
# PINNING GENERATOR ALGORITHM
# ==============================================================================
def generate_pinning(vcpus: int, cores: list[list[int]]) -> Tuple[list[Tuple[int, int]], list[int]]:
    """
    Generates vCPU and Emulator pinning layouts based on CPU topology.
    Assumes P-cores have hyper-threading (SMT/siblings > 1) and E-cores do not (siblings = 1).
    """
    smt_cores = [c for c in cores if len(c) > 1]
    non_smt_cores = [c for c in cores if len(c) == 1]
    
    has_hybrid = len(smt_cores) > 0 and len(non_smt_cores) > 0
    
    v_mappings = []
    assigned_host_cpus = set()
    v_left = vcpus
    
    # 1. Allocate to P-cores (SMT cores) first, keeping threads of the same core paired
    for core in smt_cores:
        if v_left <= 0:
            break
        if v_left >= 2:
            v_idx = vcpus - v_left
            v_mappings.append((v_idx, core[0]))
            v_mappings.append((v_idx + 1, core[1]))
            assigned_host_cpus.add(core[0])
            assigned_host_cpus.add(core[1])
            v_left -= 2
        else:
            v_idx = vcpus - v_left
            v_mappings.append((v_idx, core[0]))
            assigned_host_cpus.add(core[0])
            v_left -= 1

    # 2. If we need more vCPUs, allocate to E-cores (Non-SMT cores)
    if v_left > 0:
        for core in non_smt_cores:
            if v_left <= 0:
                break
            v_idx = vcpus - v_left
            v_mappings.append((v_idx, core[0]))
            assigned_host_cpus.add(core[0])
            v_left -= 1

    # 3. Fallback: If we still need vCPUs, map round-robin to P-cores
    if v_left > 0:
        flat_all_cpus = [cpu for core in cores for cpu in core]
        for i in range(v_left):
            v_idx = vcpus - v_left + i
            host_cpu = flat_all_cpus[i % len(flat_all_cpus)]
            v_mappings.append((v_idx, host_cpu))
            assigned_host_cpus.add(host_cpu)

    # 4. Map emulator pin to the remaining E-cores or unassigned cores
    all_host_cpus = set(cpu for core in cores for cpu in core)
    unassigned_cpus = all_host_cpus - assigned_host_cpus
    
    if has_hybrid:
        e_cpus = set(cpu for core in non_smt_cores for cpu in core)
        emulator_cpus = sorted(list(unassigned_cpus & e_cpus))
        if not emulator_cpus:
            emulator_cpus = sorted(list(unassigned_cpus))
    else:
        emulator_cpus = sorted(list(unassigned_cpus))
        
    if not emulator_cpus:
        emulator_cpus = cores[-1]
        
    return v_mappings, emulator_cpus

# ==============================================================================
# IDEMPOTENT XML INJECTION (STRING BASED)
# ==============================================================================
def inject_cputune(xml_str: str, v_mappings: list[Tuple[int, int]], emulator_cpus: list[int]) -> str:
    """Idempotently injects or updates the <cputune> block in the libvirt domain XML."""
    # 1. Generate the cputune XML block
    cputune_lines = ["  <cputune>"]
    for v_idx, host_cpu in v_mappings:
        cputune_lines.append(f"    <vcpupin vcpu='{v_idx}' cpuset='{host_cpu}'/>")
        
    if len(emulator_cpus) > 1 and emulator_cpus == list(range(emulator_cpus[0], emulator_cpus[-1] + 1)):
        cpuset_str = f"{emulator_cpus[0]}-{emulator_cpus[-1]}"
    else:
        cpuset_str = ",".join(map(str, emulator_cpus))
    cputune_lines.append(f"    <emulatorpin cpuset='{cpuset_str}'/>")
    cputune_lines.append("  </cputune>")
    cputune_str = "\n".join(cputune_lines)

    # 2. Clean out any existing <cputune> block (handles multi-line matching)
    xml_cleaned = re.sub(r'\s*<cputune>.*?</cputune>', '', xml_str, flags=re.DOTALL)

    # 3. Find the <vcpu> block and insert the new cputune block right below it
    match = re.search(r'(<vcpu[^>]*>.*?</vcpu>)', xml_cleaned)
    if not match:
        bail("Could not locate <vcpu> element in the VM XML.")
        
    vcpu_block = match.group(1)
    replacement = f"{vcpu_block}\n{cputune_str}"
    
    # We do a direct string replace of the vcpu block (1 replacement limit)
    xml_new = xml_cleaned.replace(vcpu_block, replacement, 1)
    return xml_new

# ==============================================================================
# MAIN TERMINAL INTERFACE
# ==============================================================================
def get_vms() -> list[Tuple[str, str]]:
    """Query libvirt system instance for all VMs."""
    try:
        _, stdout = run_cmd(["virsh", "-c", "qemu:///system", "list", "--all"])
        vms = []
        for line in stdout.splitlines()[2:]:
            parts = line.split()
            if len(parts) >= 3:
                vms.append((parts[1], " ".join(parts[2:])))
            elif len(parts) == 2:
                vms.append((parts[0], parts[1]))
        return vms
    except Exception:
        return []

def main():
    console.clear()
    console.print(Panel("[bold green]Contiguous CPU Pinning Configuration Generator[/bold green]\nSupports Hybrid (Intel P/E Cores) & Uniform (AMD/Intel) Topologies", expand=False))

    # 1. Probe CPU Topology
    cores = get_cpu_topology()
    total_threads = sum(len(c) for c in cores)
    smt_cores = [c for c in cores if len(c) > 1]
    non_smt_cores = [c for c in cores if len(c) == 1]
    
    console.print(f"[bold blue]==>[/bold blue] [bold]Host CPU Detected:[/bold] {total_threads} logical processors")
    console.print(f"  - Physical P-Cores (SMT-enabled): [green]{len(smt_cores)}[/green] ({len(smt_cores)*2} threads)")
    console.print(f"  - Physical E-Cores (Non-SMT): [green]{len(non_smt_cores)}[/green] ({len(non_smt_cores)} threads)")

    # 2. Select target VM
    vms = get_vms()
    if not vms:
        bail("No virtual machines found in libvirt.")
        
    console.print("\n[bold cyan]Select VM to apply CPU Pinning configuration:[/bold cyan]")
    for idx, (name, state) in enumerate(vms):
        console.print(f"  [{idx + 1}] {name} [dim]({state})[/dim]")
        
    choice = Prompt.ask("\nChoice", choices=[str(i+1) for i in range(len(vms))], default="1")
    vm_name = vms[int(choice) - 1][0]
    
    # 3. Read VM XML to detect vCPUs
    _, xml_old = run_cmd(["virsh", "-c", "qemu:///system", "dumpxml", "--inactive", vm_name])
    
    # Parse vcpu count using regex to prevent stripping namespaces/comments
    match = re.search(r'<vcpu[^>]*>\s*(\d+)\s*</vcpu>', xml_old)
    if not match:
        bail(f"Could not read vCPU configuration from VM '{vm_name}' XML.")
        
    vcpus = int(match.group(1))
    console.print(f"\n[bold green]  ✓ Target VM '{vm_name}' is configured with {vcpus} vCPUs.[/bold green]")

    # 4. Generate Pinning
    v_mappings, emulator_cpus = generate_pinning(vcpus, cores)
    
    # Print proposal
    table = Table(title=f"Proposed CPU Pinning for {vm_name} ({vcpus} vCPUs)", header_style="bold magenta")
    table.add_column("vCPU", style="cyan", justify="center")
    table.add_column("Pinned to Host CPU", style="green", justify="center")
    table.add_column("Type", style="dim")
    
    for v_idx, host_cpu in v_mappings:
        core_type = "P-Core Thread" if host_cpu in [cpu for c in smt_cores for cpu in c] else "E-Core"
        table.add_row(str(v_idx), str(host_cpu), core_type)
        
    emulator_str = f"{emulator_cpus[0]}-{emulator_cpus[-1]}" if len(emulator_cpus) > 1 and emulator_cpus == list(range(emulator_cpus[0], emulator_cpus[-1] + 1)) else ",".join(map(str, emulator_cpus))
    table.add_row("Emulator", emulator_str, "Emulator / IO Overhead (E-Cores)")
    
    console.print()
    console.print(table)
    console.print()

    # 5. Apply
    confirm = Prompt.ask("[bold cyan]Apply this CPU pinning configuration?[/bold cyan]", choices=["y", "n"], default="y")
    if confirm.lower() == 'y':
        xml_new = inject_cputune(xml_old, v_mappings, emulator_cpus)
        
        # Redefine VM XML atomic-style
        fd, tmp_path_str = tempfile.mkstemp(prefix=f"kvm-pin-{vm_name}-", suffix=".xml")
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(xml_new)
            
            run_cmd(["virsh", "-c", "qemu:///system", "define", str(tmp_path)])
            console.print(f"[bold green]✓ Successfully configured CPU pinning for VM '{vm_name}' in libvirt![/bold green]")
            console.print("[yellow]Note: Changes will take effect on the next cold boot (shutdown & start) of the VM.[/yellow]\n")
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

if __name__ == "__main__":
    main()
