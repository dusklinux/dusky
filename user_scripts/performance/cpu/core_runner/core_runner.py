#!/usr/bin/env python3
"""
Dusky Core Affinity Wrapper - Final Golden Release
Python 3.14+ | Optimized for Arch Linux Kernel 7.1.2+
"""

import os
import sys
import subprocess
import argparse
import shutil
import json
import signal
import time
from pathlib import Path
from typing import Any

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
except ImportError:
    print("\033[91m[X] The 'rich' library is missing. Install via: sudo pacman -S python-rich\033[0m")
    sys.exit(1)

console = Console()

CACHE_FILE = Path("/var/tmp/core_runner_topology.json")

# ==========================================
# Low-Level Core Utilities
# ==========================================
def safe_read(path: Path, default: str = "") -> str:
    """Safely reads sysfs hardware files."""
    try:
        if path.is_file():
            return path.read_text().strip()
    except OSError:
        pass
    return default

def parse_cpu_list(cpu_list_str: str) -> list[int]:
    """Robustly parses sysfs cpu lists like '0-3,8-11' into discrete integers."""
    cores: set[int] = set()
    for part in cpu_list_str.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            try:
                start_str, end_str = part.split('-')
                start, end = int(start_str), int(end_str)
                cores.update(range(start, end + 1))
            except ValueError:
                pass
        elif part.isdigit():
            cores.add(int(part))
    return sorted(list(cores))

def get_core_status(cpu_id: int) -> bool:
    """Checks if a core is actively online in the OS."""
    path = Path(f"/sys/devices/system/cpu/cpu{cpu_id}/online")
    if not path.exists():
        return True  # BSP (Core 0) is permanently online and locked
    return safe_read(path, "1") == "1"

def get_helper_path() -> str:
    """Returns the absolute path of core_helper.py."""
    return str(Path(__file__).parent.resolve() / "core_helper.py")

def batch_wake_cores(cpu_ids: list[int]) -> bool:
    """Wakes multiple sleeping cores using the core_helper script via sudo."""
    if not cpu_ids:
        return True
    cpu_ids_str = ",".join(map(str, cpu_ids))
    helper = get_helper_path()
    try:
        subprocess.run(['sudo', helper, '--online', cpu_ids_str], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Kernel state transition might be asynchronous, wait/retry check
        for _ in range(10):
            if all(get_core_status(c) for c in cpu_ids):
                return True
            time.sleep(0.05)
        return False
    except Exception:
        return False

def batch_offline_cores(cpu_ids: list[int]) -> bool:
    """Offlines multiple active cores using the core_helper script via sudo."""
    if not cpu_ids:
        return True
    cpu_ids_str = ",".join(map(str, cpu_ids))
    helper = get_helper_path()
    try:
        subprocess.run(['sudo', helper, '--offline', cpu_ids_str], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Kernel state transition might be asynchronous, wait/retry check
        for _ in range(10):
            if all(not get_core_status(c) for c in cpu_ids):
                return True
            time.sleep(0.05)
        return False
    except Exception:
        return False

# ==========================================
# Topology Detection Engine with Cache & Fallbacks
# ==========================================
def get_system_signature() -> dict[str, Any]:
    """Generates a system signature to validate the cached topology."""
    cpu_model = "unknown"
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu_model = line.split(":", 1)[1].strip()
                break
    except Exception:
        pass

    total_cores = 0
    try:
        possible = Path("/sys/devices/system/cpu/possible").read_text().strip()
        cores = parse_cpu_list(possible)
        total_cores = len(cores)
    except Exception:
        pass

    machine_id = ""
    for path in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        try:
            if path.is_file():
                machine_id = path.read_text().strip()
                break
        except Exception:
            pass

    return {
        "cpu_model": cpu_model,
        "total_cores": total_cores,
        "machine_id": machine_id
    }

def load_cached_topology() -> dict[int, dict[str, Any]] | None:
    """Loads and validates the cached topology."""
    if not CACHE_FILE.is_file():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text())
        sig = get_system_signature()
        cached_sig = data.get("system_signature", {})
        if sig == cached_sig:
            topology = {}
            for k, v in data.get("topology", {}).items():
                topology[int(k)] = v
            return topology
    except Exception:
        pass
    return None

def save_cached_topology(topology: dict[int, dict[str, Any]]) -> None:
    """Saves the topology to the cache file with world-readable permissions."""
    try:
        serialized_topology = {str(k): v for k, v in topology.items()}
        data = {
            "system_signature": get_system_signature(),
            "topology": serialized_topology
        }
        CACHE_FILE.write_text(json.dumps(data, indent=2))
        CACHE_FILE.chmod(0o644)
    except Exception:
        pass

def detect_topology() -> dict[int, dict[str, Any]]:
    """
    Intelligently maps physical hardware to determine 
    Performance vs Efficiency cores using CPPC, Core Type, SMT structures, and frequency fallbacks.
    """
    # 1. Try loading valid cached topology
    cached = load_cached_topology()
    if cached is not None:
        for cpu_id, data in cached.items():
            data["online"] = get_core_status(cpu_id)
        return cached

    # 2. Cache is missing or invalid. Check if any cores are offline.
    cpu_sysfs = Path("/sys/devices/system/cpu")
    cpu_nodes = sorted([node for node in cpu_sysfs.glob("cpu[0-9]*") if node.is_dir()], key=lambda p: int(p.name[3:]))
    cpu_ids = [int(node.name[3:]) for node in cpu_nodes]

    offline_cores = [cpu_id for cpu_id in cpu_ids if not get_core_status(cpu_id)]

    woke_any = False
    if offline_cores:
        # Attempt to wake all offline cores temporarily to read their properties
        woke_any = batch_wake_cores(offline_cores)

    # 3. Read topology from sysfs
    topology: dict[int, dict[str, Any]] = {}

    # Pre-read CPPC (ACPI) highest performance metrics if available
    cppc_perf: dict[int, int] = {}
    for cpu_id in cpu_ids:
        node = cpu_sysfs / f"cpu{cpu_id}"
        perf_str = safe_read(node / "acpi_cppc" / "highest_perf")
        if perf_str.isdigit():
            cppc_perf[cpu_id] = int(perf_str)

    # Calculate CPPC midpoint to identify P vs E
    cppc_midpoint = 0.0
    if cppc_perf:
        unique_perfs = sorted(list(set(cppc_perf.values())))
        if len(unique_perfs) > 1:
            cppc_midpoint = (unique_perfs[0] + unique_perfs[-1]) / 2.0

    # Determine SMT sibling groups
    smt_siblings: dict[int, list[int]] = {}
    for cpu_id in cpu_ids:
        node = cpu_sysfs / f"cpu{cpu_id}"
        core_cpus = safe_read(node / "topology" / "core_cpus_list")
        siblings = parse_cpu_list(core_cpus) if core_cpus else [cpu_id]
        smt_siblings[cpu_id] = siblings

    # Read cpufreq max frequencies for fallback heuristics
    max_freqs: dict[int, int] = {}
    for cpu_id in cpu_ids:
        freq_str = safe_read(cpu_sysfs / "cpufreq" / f"policy{cpu_id}" / "cpuinfo_max_freq")
        if freq_str.isdigit():
            max_freqs[cpu_id] = int(freq_str)

    max_freq_midpoint = 0.0
    if max_freqs:
        unique_freqs = sorted(list(set(max_freqs.values())))
        if len(unique_freqs) > 1:
            max_freq_midpoint = (unique_freqs[0] + unique_freqs[-1]) / 2.0

    # Classify each core node
    for cpu_id in cpu_ids:
        node = cpu_sysfs / f"cpu{cpu_id}"
        core_type_val = safe_read(node / "topology" / "core_type")
        c_type = "P"

        # Check 1: CPPC Disparity (Best for AMD / Newer Intel)
        if cppc_perf and cppc_midpoint > 0:
            c_type = "P" if cppc_perf.get(cpu_id, 0) > cppc_midpoint else "E"
        # Check 2: Intel explicit core_type flag
        elif core_type_val in ("1", "0x10", "intel_atom"):
            c_type = "E"
        elif core_type_val in ("2", "0x20", "intel_core"):
            c_type = "P"
        # Check 3: Max Frequency heuristic (Works even when offline/no-cppc)
        elif max_freqs and max_freq_midpoint > 0:
            c_type = "P" if max_freqs.get(cpu_id, 0) > max_freq_midpoint else "E"
        # Check 4: SMT Fallback heuristic
        else:
            siblings = smt_siblings.get(cpu_id, [cpu_id])
            if len(siblings) > 1:
                c_type = "P"
            else:
                is_sibling_of_smt = any(
                    other_id != cpu_id and cpu_id in sib_list and len(sib_list) > 1 
                    for other_id, sib_list in smt_siblings.items()
                )
                c_type = "E" if not is_sibling_of_smt else "P"

        topology[cpu_id] = {
            "type": c_type,
            "online": get_core_status(cpu_id),
            "smt_group": smt_siblings.get(cpu_id, [cpu_id])
        }

    # Failsafe: Symmetric Processors (Treat all as P-Cores if no mixed types exist)
    has_p = any(data["type"] == "P" for data in topology.values())
    has_e = any(data["type"] == "E" for data in topology.values())
    if not (has_p and has_e):
        for data in topology.values():
            data["type"] = "P"

    # Save to cache
    save_cached_topology(topology)

    # 5. Restore cores we woke up back to offline state
    if woke_any and offline_cores:
        batch_offline_cores(offline_cores)
        for cpu_id in offline_cores:
            if cpu_id in topology:
                topology[cpu_id]["online"] = False

    return topology

def print_beautiful_help() -> None:
    """Renders a beautiful rich help dashboard."""
    console.print(Panel(
        "[bold green]Dusky Core Affinity Wrapper[/bold green]",
        border_style="green",
        box=box.ROUNDED,
        expand=False
    ))

    console.print("\n[bold yellow]Usage Patterns:[/bold yellow]")
    usage_table = Table(show_header=False, box=None, padding=(0, 4, 0, 0))
    usage_table.add_row("  [bold green]core[/bold green] [white]<core1> <core2> ... <command> [args...][/white]", "[dim]Direct core index routing[/dim]")
    usage_table.add_row("  [bold green]core[/bold green] [white]-h | --help[/white]", "[dim]Show this help dashboard[/dim]")
    usage_table.add_row("  [bold green]core[/bold green] [white]-s | --status[/white]", "[dim]View current CPU topology status[/dim]")
    usage_table.add_row("  [bold green]core[/bold green] [white]-t <type> <command>[/white]", "[dim]Bind to P-Cores, E-Cores, or All[/dim]")
    usage_table.add_row("  [bold green]core[/bold green] [white]-c <custom_list> <command>[/white]", "[dim]Bind to custom list, e.g. 0,2-4[/dim]")
    console.print(usage_table)

    table = Table(
        title="\n[bold green]Command Line Options[/bold green]",
        title_style="bold green",
        show_header=True,
        header_style="bold green",
        box=box.ROUNDED,
        border_style="dim green"
    )
    table.add_column("Flag / Option", style="bold green", width=20)
    table.add_column("Allowed Values", style="cyan", width=25)
    table.add_column("Description", style="white")

    table.add_row("-h, --help", "None", "Display this beautiful help dashboard.")
    table.add_row("-s, --status", "None", "Show CPU core topology (P vs E cores) and online states.")
    table.add_row("-t, --type", "pcores | ecores | all", "Target hardware tier (default: pcores).")
    table.add_row("-c, --custom", "Comma/dash list (e.g. 0-2,4)", "Explicit CPU core pinning mask.")
    table.add_row("-d, --detach", "None", "Run application and wrapper in background (detached).")
    table.add_row("command [args...]", "Exec and params", "Target command to launch with core affinity.")

    console.print(table)

    console.print(Panel(
        "[bold green]Under the Hood:[/bold green]\n"
        "• [bold white]Dynamic Topology[/bold white]: Reads structure from cached system signature to avoid offline core errors.\n"
        "• [bold white]Power State Bridge[/bold white]: Wakes target offline cores via passwordless sudo helper if required.\n"
        "• [bold white]Affinity Pinning[/bold white]: Launches target applications bound to selected cores using taskset.\n"
        "• [bold white]Core Restorations[/bold white]: Intercepts signals (SIGINT/SIGTERM/SIGHUP) to return woken cores back to sleep.",
        border_style="dim green",
        expand=False
    ))

def display_status(topology: dict[int, dict[str, Any]]) -> None:
    """Renders the topology status cleanly to the terminal."""
    console.print(Panel("[bold cyan]System Hardware Topology & Current Status[/bold cyan]", expand=False))
    
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Core ID", justify="center")
    table.add_column("Architecture", justify="center")
    table.add_column("Current State", justify="center")

    for cpu_id, data in topology.items():
        arch_str = "[bold cyan]P-Core[/bold cyan]" if data["type"] == "P" else "[bold green]E-Core[/bold green]"
        state_str = "[bold green]● Online[/bold green]" if data["online"] else "[dim red]○ Offline[/dim red]"
        table.add_row(f"CPU {cpu_id}", arch_str, state_str)

    console.print(table)

def run_target_command(taskset_cmd: list[str], offline_targets_to_restore: list[int]) -> int:
    """
    Executes the command, forwards SIGINT, SIGTERM, and SIGQUIT to the child process,
    and cleans up core states in a finally block.
    """
    proc = None
    original_handlers = {}
    signals_to_catch = [signal.SIGINT, signal.SIGTERM, signal.SIGQUIT, signal.SIGHUP]

    def signal_handler(signum: int, frame: Any) -> None:
        if proc and proc.poll() is None:
            try:
                proc.send_signal(signum)
            except OSError:
                pass

    try:
        # Set up signal handlers to forward to the child
        for sig in signals_to_catch:
            original_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, signal_handler)

        proc = subprocess.Popen(taskset_cmd)
        
        # Wait for the process to exit
        returncode = proc.wait()
        return returncode
    except Exception as e:
        console.print(f"[bold red]Execution Error:[/bold red] {e}")
        return 1
    finally:
        # Restore signal handlers
        for sig, handler in original_handlers.items():
            signal.signal(sig, handler)
        
        # Restore offline core states
        if offline_targets_to_restore:
            console.print(f"\n[bold yellow]Cleaning up: putting cores {offline_targets_to_restore} back to sleep...[/bold yellow]")
            if batch_offline_cores(offline_targets_to_restore):
                console.print("[bold green]✔ Cores returned to sleep successfully.[/bold green]")
            else:
                console.print("[bold red]✖ Warning: Failed to put cores back to sleep.[/bold red]")

def main() -> None:
    if not shutil.which("taskset"):
        console.print("[bold red]Critical Error:[/bold red] 'taskset' utility not found. Please install the 'util-linux' package.")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Advanced Smart Core Affinity Wrapper", add_help=False)
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("-s", "--status", action="store_true", help="Print detailed topology and exit")
    parser.add_argument("-t", "--type", choices=["pcores", "ecores", "all"], default="pcores", help="Target architecture tier (default: pcores)")
    parser.add_argument("-c", "--custom", type=str, help="Custom comma/dash separated core list (e.g., 0,2-4,6)")
    parser.add_argument("-d", "--detach", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Application executable and arguments")
    
    args = parser.parse_args()

    if args.help:
        print_beautiful_help()
        sys.exit(0)

    topology = detect_topology()

    if args.status:
        display_status(topology)
        sys.exit(0)

    # Standardize command execution list
    if not args.command:
        console.print("[bold red]Execution Error:[/bold red] No target command provided.")
        sys.exit(1)

    if args.command[0] == "--":
        args.command = args.command[1:]
        if not args.command:
            console.print("[bold red]Execution Error:[/bold red] No target command provided after '--'.")
            sys.exit(1)

    args.command[0] = os.path.expanduser(args.command[0])

    # Resolve target cores
    target_cores: list[int] = []
    
    if args.custom:
        target_cores = parse_cpu_list(args.custom)
        invalid_cores = [c for c in target_cores if c not in topology]
        if invalid_cores:
            console.print(f"[bold red]Hardware Error:[/bold red] Cores {invalid_cores} do not exist on this CPU.")
            sys.exit(1)
    else:
        match args.type:
            case "all":
                target_cores = list(topology.keys())
            case "pcores":
                target_cores = [c for c, d in topology.items() if d["type"] == "P"]
            case "ecores":
                target_cores = [c for c, d in topology.items() if d["type"] == "E"]
                if not target_cores:
                    console.print("[bold yellow]Notice:[/bold yellow] No E-Cores exist on this system. Falling back to P-Cores.")
                    target_cores = [c for c, d in topology.items() if d["type"] == "P"]

    if not target_cores:
        console.print("[bold red]Fatal Error:[/bold red] Unable to map target cores.")
        sys.exit(1)

    # Manage Hotplug State / Wake Cores Safely
    offline_targets = [c for c in target_cores if not topology[c]["online"]]
    if offline_targets:
        console.print(Panel(
            f"[bold yellow]Wake Sequence Initiated[/bold yellow]\n"
            f"Target cores {offline_targets} are currently in a deep offline sleep state.\n"
            "Requesting temporary escalation to bridge hardware power state...",
            border_style="yellow", expand=False
        ))
        
        if batch_wake_cores(offline_targets):
            console.print(f"[bold green]✔ Link established. Hardware woken successfully.[/bold green]")
        else:
            console.print("[bold red]✖ ACPI Error: Failed to alter hardware state. Execution aborted.[/bold red]")
            sys.exit(1)

    # Hand off to application execution
    target_cores_str = ",".join(map(str, target_cores))
    console.print(f"[bold green]🚀 Bounding execution to cores:[/bold green] [white]{target_cores_str}[/white]")
    
    taskset_cmd = ["taskset", "-c", target_cores_str] + args.command
    
    if args.detach:
        try:
            # First fork
            pid = os.fork()
            if pid > 0:
                # Parent process exits immediately
                sys.exit(0)
        except OSError as e:
            console.print(f"[bold red]Fork Error:[/bold red] {e}")
            sys.exit(1)

        # Decouple process session and group
        os.setsid()
        os.umask(0)

        try:
            # Second fork
            pid = os.fork()
            if pid > 0:
                # First child exits
                sys.exit(0)
        except OSError as e:
            sys.exit(1)

        # Avoid keeping cwd locked
        try:
            os.chdir('/')
        except OSError:
            pass

        # Redirect standard file descriptors at OS level
        sys.stdout.flush()
        sys.stderr.flush()

        si = open(os.devnull, 'r')
        so = open(os.devnull, 'w')
        se = open(os.devnull, 'w')

        os.dup2(si.fileno(), sys.stdin.fileno())
        os.dup2(so.fileno(), sys.stdout.fileno())
        os.dup2(se.fileno(), sys.stderr.fileno())

    exit_code = run_target_command(taskset_cmd, offline_targets)
    sys.exit(exit_code)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
