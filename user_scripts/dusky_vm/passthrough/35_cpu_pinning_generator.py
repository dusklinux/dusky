#!/usr/bin/env python3
"""
Phase 6: CPU Topology & Contiguous Pinning Generator
Target: Arch Linux rolling (Aug 2026) / Kernel 7.3+ / Python 3.15+ / systemd 262+ / libvirt 12.6+
Philosophy: Smart core alignment (P/E Core detection, SMT grouping), idempotent XML modification via ET.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Never

MIN_PY: tuple[int, int, int] = (3, 15, 0)

def _hard_exit(msg: str) -> Never:
    sys.stderr.write(f"\n[FATAL] {msg}\n\n")
    raise SystemExit(1)

if sys.version_info[:3] < MIN_PY:
    _hard_exit(f"Python {MIN_PY[0]}.{MIN_PY[1]}.{MIN_PY[2]}+ required; running {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}.")

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
# Shared primitives
# ==============================================================================
@dataclass(frozen=True, slots=True)
class Cmd:
    argv: list[str]
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0

def run(argv: list[str], *, check: bool = False, timeout: float = 60.0) -> Cmd:
    try:
        proc = subprocess.run(argv, check=False, timeout=timeout, text=True, capture_output=True, stdin=subprocess.DEVNULL)
        res = Cmd(argv, proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip())
    except subprocess.TimeoutExpired:
        res = Cmd(argv, 124, "", f"timeout after {timeout}s")
    except FileNotFoundError:
        res = Cmd(argv, 127, "", f"executable not found: {argv[0]}")
    if check and not res.ok:
        bail(f"Command failed (rc={res.code}): {' '.join(argv)}\n{res.err or res.out}")
    return res

def bail(msg: str) -> Never:
    console.print(Panel(f"[bold red]FATAL[/bold red] {msg}", border_style="red"))
    raise SystemExit(1)

def virsh(args: list[str], *, timeout: float = 60.0) -> Cmd:
    res = run(["virsh", "-c", "qemu:///system", *args], timeout=timeout)
    if not res.ok:
        # Fallback for users not yet in libvirt group or polkit without sudo
        alt = run(["sudo", "virsh", "-c", "qemu:///system", *args], timeout=timeout)
        if alt.ok or alt.code not in (124, 127):
            return alt
    return res

# ==============================================================================
# CPU TOPOLOGY DISCOVERY
# ==============================================================================
def get_cpu_topology() -> list[list[int]]:
    """
    Scans /sys/devices/system/cpu to map logical CPUs to physical cores.
    Returns a list of cores, where each core is a list of its sibling logical CPU IDs.
    """
    cpu_path = Path("/sys/devices/system/cpu")
    cores_dict: dict[tuple[int, int], list[int]] = {}

    for cpu_dir in cpu_path.glob("cpu[0-9]*"):
        try:
            package_id_file = cpu_dir / "topology" / "physical_package_id"
            siblings_file = cpu_dir / "topology" / "thread_siblings_list"
            if not siblings_file.exists():
                continue
            package_id = int(package_id_file.read_text(encoding="utf-8").strip()) if package_id_file.exists() else 0
            # core_id is used only for deterministic key; fallback to cpu_id if unreadable
            core_id_file = cpu_dir / "topology" / "core_id"
            try:
                core_id = int(core_id_file.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                core_id = int(cpu_dir.name[3:])
            siblings_str = siblings_file.read_text(encoding="utf-8").strip()
            core_key = (package_id, core_id)
            if core_key not in cores_dict:
                siblings: list[int] = []
                for part in siblings_str.split(","):
                    part = part.strip()
                    if not part:
                        continue
                    if "-" in part:
                        start, end = part.split("-", 1)
                        siblings.extend(range(int(start), int(end) + 1))
                    else:
                        siblings.append(int(part))
                cores_dict[core_key] = sorted(set(siblings))
        except (ValueError, OSError):
            continue

    sorted_cores = sorted(cores_dict.values(), key=lambda c: c[0] if c else 0)
    return sorted_cores

# ==============================================================================
# PINNING GENERATOR ALGORITHM
# ==============================================================================
def generate_pinning(vcpus: int, cores: list[list[int]]) -> tuple[list[tuple[int, int]], list[int]]:
    """
    Generates vCPU and Emulator pinning layouts based on CPU topology.
    P-cores (SMT) have >1 thread, E-cores have 1. Allocates P-cores first paired, then E-cores.
    """
    smt_cores = [c for c in cores if len(c) > 1]
    non_smt_cores = [c for c in cores if len(c) == 1]

    v_mappings: list[tuple[int, int]] = []
    assigned_host_cpus: set[int] = set()
    v_left = vcpus

    # 1. Allocate to P-cores first, keeping threads paired
    for core in smt_cores:
        if v_left <= 0:
            break
        if v_left >= 2:
            v_idx = vcpus - v_left
            v_mappings.append((v_idx, core[0]))
            v_mappings.append((v_idx + 1, core[1] if len(core) > 1 else core[0]))
            assigned_host_cpus.update(core[:2])
            v_left -= 2
        else:
            v_idx = vcpus - v_left
            v_mappings.append((v_idx, core[0]))
            assigned_host_cpus.add(core[0])
            v_left -= 1

    # 2. Allocate to E-cores
    if v_left > 0:
        for core in non_smt_cores:
            if v_left <= 0:
                break
            v_idx = vcpus - v_left
            v_mappings.append((v_idx, core[0]))
            assigned_host_cpus.add(core[0])
            v_left -= 1

    # 3. Fallback round-robin if still need vCPUs (oversubscribed)
    if v_left > 0:
        flat_all_cpus = [cpu for core in cores for cpu in core]
        if flat_all_cpus:
            for i in range(v_left):
                v_idx = vcpus - v_left + i
                host_cpu = flat_all_cpus[i % len(flat_all_cpus)]
                v_mappings.append((v_idx, host_cpu))
                assigned_host_cpus.add(host_cpu)

    # 4. Emulator pin to remaining E-cores or unassigned
    all_host_cpus = {cpu for core in cores for cpu in core}
    unassigned_cpus = all_host_cpus - assigned_host_cpus

    has_hybrid = bool(smt_cores and non_smt_cores)
    if has_hybrid:
        e_cpus = {cpu for core in non_smt_cores for cpu in core}
        emulator_cpus = sorted(unassigned_cpus & e_cpus)
        if not emulator_cpus:
            emulator_cpus = sorted(unassigned_cpus)
    else:
        emulator_cpus = sorted(unassigned_cpus)

    if not emulator_cpus:
        # No unassigned left (fully pinned domain) -> pin emulator to last core's siblings
        emulator_cpus = sorted(cores[-1]) if cores else [0]

    return v_mappings, emulator_cpus

# ==============================================================================
# IDEMPOTENT XML INJECTION via ET (not regex)
# ==============================================================================
def inject_cputune(xml_str: str, v_mappings: list[tuple[int, int]], emulator_cpus: list[int]) -> str:
    """Idempotently injects <cputune> via ET. Removes existing <cputune>, inserts after <vcpu>."""
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as exc:
        bail(f"Domain XML is not parseable: {exc}")

    # Remove existing cputune
    for old in root.findall("cputune"):
        root.remove(old)

    vcpu_elem = root.find("vcpu")
    if vcpu_elem is None:
        bail("Could not locate <vcpu> element in the VM XML.")

    cputune = ET.Element("cputune")
    for v_idx, host_cpu in v_mappings:
        ET.SubElement(cputune, "vcpupin", vcpu=str(v_idx), cpuset=str(host_cpu))

    if len(emulator_cpus) > 1 and emulator_cpus == list(range(emulator_cpus[0], emulator_cpus[-1] + 1)):
        cpuset_str = f"{emulator_cpus[0]}-{emulator_cpus[-1]}"
    else:
        cpuset_str = ",".join(map(str, emulator_cpus))
    ET.SubElement(cputune, "emulatorpin", cpuset=cpuset_str)

    # Insert cputune directly after vcpu
    children = list(root)
    try:
        vcpu_index = children.index(vcpu_elem)
    except ValueError:
        # Fallback append
        root.append(cputune)
    else:
        root.insert(vcpu_index + 1, cputune)

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode") + "\n"

# ==============================================================================
# MAIN TERMINAL INTERFACE
# ==============================================================================
def get_vms() -> list[tuple[str, str]]:
    """Query libvirt via --name + domstate (robust, no column split)."""
    res = virsh(["list", "--all", "--name"])
    if not res.ok:
        return []
    vms: list[tuple[str, str]] = []
    for name in res.out.split():
        if not name:
            continue
        state = virsh(["domstate", name]).out or "unknown"
        vms.append((name, state))
    return vms

def main() -> None:
    console.clear()
    console.print(Panel("[bold green]Contiguous CPU Pinning Configuration Generator[/bold green]\nSupports Hybrid (Intel P/E Cores) & Uniform (AMD/Intel) Topologies", expand=False))

    if shutil.which("virsh") is None:
        bail("virsh not found. Install libvirt and ensure you are in the 'libvirt' group.")

    cores = get_cpu_topology()
    if not cores:
        bail("Could not detect CPU topology from /sys/devices/system/cpu.")
    total_threads = sum(len(c) for c in cores)
    smt_cores = [c for c in cores if len(c) > 1]
    non_smt_cores = [c for c in cores if len(c) == 1]

    console.print(f"[bold blue]==>[/bold blue] [bold]Host CPU Detected:[/bold] {total_threads} logical processors")
    console.print(f"  - Physical P-Cores (SMT-enabled): [green]{len(smt_cores)}[/green] ({len(smt_cores)*2} threads)")
    console.print(f"  - Physical E-Cores (Non-SMT): [green]{len(non_smt_cores)}[/green] ({len(non_smt_cores)} threads)")

    vms = get_vms()
    if not vms:
        bail("No virtual machines found in libvirt.")

    console.print("\n[bold cyan]Select VM to apply CPU Pinning configuration:[/bold cyan]")
    for idx, (name, state) in enumerate(vms):
        console.print(f"  [{idx + 1}] {name} [dim]({state})[/dim]")

    choice = Prompt.ask("\nChoice", choices=[str(i+1) for i in range(len(vms))], default="1")
    vm_name = vms[int(choice) - 1][0]

    xml_res = virsh(["dumpxml", "--inactive", vm_name])
    if not xml_res.ok:
        bail(f"Could not read VM XML for '{vm_name}': {xml_res.err or xml_res.out}")
    xml_old = xml_res.out

    match = re.search(r"<vcpu[^>]*>\s*(\d+)\s*</vcpu>", xml_old)
    if not match:
        bail(f"Could not read vCPU configuration from VM '{vm_name}' XML.")
    vcpus = int(match.group(1))
    console.print(f"\n[bold green]  ✓ Target VM '{vm_name}' is configured with {vcpus} vCPUs.[/bold green]")

    v_mappings, emulator_cpus = generate_pinning(vcpus, cores)

    table = Table(title=f"Proposed CPU Pinning for {vm_name} ({vcpus} vCPUs)", header_style="bold magenta")
    table.add_column("vCPU", style="cyan", justify="center")
    table.add_column("Pinned to Host CPU", style="green", justify="center")
    table.add_column("Type", style="dim")

    smt_cpu_set = {cpu for c in smt_cores for cpu in c}
    for v_idx, host_cpu in v_mappings:
        core_type = "P-Core Thread" if host_cpu in smt_cpu_set else "E-Core"
        table.add_row(str(v_idx), str(host_cpu), core_type)

    emulator_str = f"{emulator_cpus[0]}-{emulator_cpus[-1]}" if len(emulator_cpus) > 1 and emulator_cpus == list(range(emulator_cpus[0], emulator_cpus[-1] + 1)) else ",".join(map(str, emulator_cpus))
    table.add_row("Emulator", emulator_str, "Emulator / IO Overhead (E-Cores)")

    console.print()
    console.print(table)
    console.print()

    confirm = Prompt.ask("[bold cyan]Apply this CPU pinning configuration?[/bold cyan]", choices=["y", "n"], default="y")
    if confirm.lower() != "y":
        console.print("[yellow]Aborted.[/yellow]")
        return

    xml_new = inject_cputune(xml_old, v_mappings, emulator_cpus)

    fd, tmp_path_str = tempfile.mkstemp(prefix=f"kvm-pin-{vm_name}-", suffix=".xml")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(xml_new)
            f.flush()
            os.fsync(f.fileno())
        res = virsh(["define", str(tmp_path)])
        if not res.ok:
            bail(f"virsh define failed: {res.err or res.out}")
        console.print(f"[bold green]✓ Successfully configured CPU pinning for VM '{vm_name}' in libvirt![/bold green]")
        console.print("[yellow]Note: Changes will take effect on the next cold boot (shutdown & start) of the VM.[/yellow]\n")
    finally:
        tmp_path.unlink(missing_ok=True)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n\n[bold red]! Interrupted by operator.[/bold red]\n")
        raise SystemExit(130) from None
