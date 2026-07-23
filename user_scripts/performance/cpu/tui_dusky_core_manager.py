#!/usr/bin/env python3
import sys
sys.dont_write_bytecode = True
import json
from pathlib import Path

tui_root = Path(__file__).resolve().parents[2] / "dusky_tui"
if str(tui_root) not in sys.path:
    sys.path.insert(0, str(tui_root))

import sys
from pathlib import Path

_dusky_root = Path.home() / "user_scripts" / "dusky_tui"
if str(_dusky_root) not in sys.path:
    sys.path.insert(0, str(_dusky_root))

import sys
from pathlib import Path

_DUSKY_TUI_ROOT = Path.home() / "user_scripts" / "dusky_tui"
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.frontend.core_types import ConfigItem

# Topology hydration (re-use logic from old script)
def safe_read(path: Path, default: str = "") -> str:
    try:
        if path.is_file():
            return path.read_text().strip()
    except OSError:
        pass
    return default

def detect_topology() -> tuple[list[int], list[int], set[int]]:
    p_cores = []
    e_cores = []
    locked_cores = set()
    cpu_sysfs = Path("/sys/devices/system/cpu")
    cpu_nodes = sorted([node for node in cpu_sysfs.glob("cpu[0-9]*") if node.is_dir()], key=lambda p: int(p.name[3:]))
    original_states = {}

    for node in cpu_nodes:
        cpu_id = int(node.name[3:])
        online_file = node / "online"
        if not online_file.exists():
            locked_cores.add(cpu_id)
            continue
        current_state = safe_read(online_file)
        original_states[cpu_id] = current_state
        if current_state == "0":
            try:
                online_file.write_text("1")
                # Wait for sysfs
                topology_dir = node / "topology"
                import time
                for _ in range(20):
                    if topology_dir.exists() and (topology_dir / "core_cpus_list").exists():
                        break
                    time.sleep(0.005)
            except OSError:
                pass

    cppc_perf = {}
    for node in cpu_nodes:
        cpu_id = int(node.name[3:])
        perf_str = safe_read(node / "acpi_cppc" / "highest_perf")
        if perf_str.isdigit():
            cppc_perf[cpu_id] = int(perf_str)

    cppc_classified = False
    if cppc_perf:
        unique_perfs = sorted(list(set(cppc_perf.values())))
        if len(unique_perfs) > 1:
            midpoint = (unique_perfs[0] + unique_perfs[-1]) / 2
            for cpu_id in [int(n.name[3:]) for n in cpu_nodes]:
                perf = cppc_perf.get(cpu_id, unique_perfs[0])
                if perf > midpoint:
                    p_cores.append(cpu_id)
                else:
                    e_cores.append(cpu_id)
            cppc_classified = True

    if not cppc_classified:
        smt_siblings = {}
        for node in cpu_nodes:
            cpu_id = int(node.name[3:])
            topology_dir = node / "topology"
            core_cpus = safe_read(topology_dir / "core_cpus_list")
            siblings = []
            if core_cpus:
                if "," in core_cpus:
                    siblings = [int(x) for x in core_cpus.split(",") if x.isdigit()]
                elif "-" in core_cpus:
                    try:
                        start, end = map(int, core_cpus.split("-"))
                        siblings = list(range(start, end + 1))
                    except ValueError:
                        pass
            if not siblings:
                siblings = [cpu_id]
            smt_siblings[cpu_id] = siblings

        for node in cpu_nodes:
            cpu_id = int(node.name[3:])
            topology_dir = node / "topology"
            core_type_val = safe_read(topology_dir / "core_type")
            if core_type_val in ("1", "0x10", "intel_atom"):
                e_cores.append(cpu_id)
            elif core_type_val in ("2", "0x20", "intel_core"):
                p_cores.append(cpu_id)
            else:
                siblings = smt_siblings.get(cpu_id, [cpu_id])
                if len(siblings) > 1:
                    p_cores.append(cpu_id)
                else:
                    is_sibling_of_smt = False
                    for other_id, sib_list in smt_siblings.items():
                        if other_id != cpu_id and cpu_id in sib_list and len(sib_list) > 1:
                            is_sibling_of_smt = True
                            break
                    if is_sibling_of_smt:
                        p_cores.append(cpu_id)
                    else:
                        e_cores.append(cpu_id)

    for cpu_id, original_state in original_states.items():
        if original_state == "0":
            try: Path(f"/sys/devices/system/cpu/cpu{cpu_id}/online").write_text("0")
            except OSError: pass

    all_found = sorted(p_cores + e_cores)
    if not locked_cores and all_found:
        locked_cores.add(all_found[0])

    if not p_cores and e_cores:
        p_cores = e_cores
        e_cores = []

    return sorted(p_cores), sorted(e_cores), locked_cores

p_cores, e_cores, locked_cores = detect_topology()

ENGINE_TYPE = "cpu_core"
TARGET_FILE = "/sys/devices/system/cpu"
APP_TITLE = "Dusky CPU Core Manager"
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
REQUIRE_ROOT = True

TABS = []
if p_cores:
    TABS.append("Performance Cores")
if e_cores:
    TABS.append("Efficient Cores")
TABS.append("Presets")

USER_PRESETS_TAB = "Presets"

SCHEMA = {}
tab_idx = 0

if p_cores:
    SCHEMA[tab_idx] = []
    for c in p_cores:
        is_locked = c in locked_cores
        lbl = f"CPU {c:02d} (BSP Locked)" if is_locked else f"CPU {c:02d}"
        SCHEMA[tab_idx].append(
            ConfigItem(
                label=lbl,
                key=f"cpu{c}",
                type_="bool",
                default=True,
                extended_help=f"Toggle Performance Core {c} online/offline state."
            )
        )
    tab_idx += 1

if e_cores:
    SCHEMA[tab_idx] = []
    for c in e_cores:
        is_locked = c in locked_cores
        lbl = f"CPU {c:02d} (BSP Locked)" if is_locked else f"CPU {c:02d}"
        SCHEMA[tab_idx].append(
            ConfigItem(
                label=lbl,
                key=f"cpu{c}",
                type_="bool",
                default=True,
                extended_help=f"Toggle Efficient Core {c} online/offline state."
            )
        )

# Delegator block
if __name__ == "__main__":
    import sys
    import subprocess
    import argparse
    from pathlib import Path

    if len(sys.argv) > 1 and sys.argv[1] == "--restore":
        from python.engines.cpu_core import CpuCoreEngine
        engine = CpuCoreEngine()
        if engine.restore_state():
            print("[OK] Successfully restored persistent CPU core states.")
            sys.exit(0)
        else:
            print("[*] No persistent CPU core states found to restore (or failed to restore).")
            sys.exit(0)

    from python.engines.cpu_core import get_core_status, get_core_freq, set_core_status

    # 1. Parse core arguments helper
    def parse_core_args(args_list, valid_cores):
        cores = set()
        for arg in args_list:
            if "-" in arg:
                start, end = sorted(map(int, arg.split("-")))
                cores.update(range(start, end + 1))
            else:
                cores.add(int(arg))
        invalid_cores = [c for c in cores if c not in valid_cores]
        if invalid_cores:
            print(f"[-] Hardware Error: CPUs {invalid_cores} do not exist.")
            sys.exit(1)
        return sorted(list(cores))

    # 2. Display status table helper
    def display_status_table():
        try:
            from rich.console import Console
            from rich.table import Table
            from rich.panel import Panel
            from rich.align import Align
        except ImportError:
            # Fallback borderless print
            print(f"{'CORE':<10} | {'TYPE':<8} | {'ST':<8} | {'FREQUENCY':<10}")
            print("-" * 45)
            for core in sorted(p_cores + e_cores):
                arch = "P-Core" if core in p_cores else "E-Core"
                if core in locked_cores:
                    status = "Locked"
                else:
                    status = "ON" if get_core_status(core) else "OFF"
                print(f"CPU {core:02d}     | {arch:<8} | {status:<8} | {get_core_freq(core)}")
            return

        console = Console()
        console.print(Align.center(Panel("[bold magenta]Dusky CPU Core Manager[/bold magenta]", border_style="cyan", expand=False)))
        table = Table(show_header=True, header_style="bold magenta", expand=True)
        table.add_column("CORE", justify="center")
        table.add_column("TYPE", justify="center")
        table.add_column("ST", justify="center")
        table.add_column("FREQUENCY", justify="center")

        for core in sorted(p_cores + e_cores):
            arch = "[bold cyan]P-Core[/bold cyan]" if core in p_cores else "[bold green]E-Core[/bold green]"
            if core in locked_cores:
                table.add_row(f"CPU {core:02d}", arch, "[bold yellow] (BSP)[/bold yellow]", get_core_freq(core))
            else:
                status = get_core_status(core)
                st_icon = "[bold green]●[/bold green]" if status else "[dim red]○[/dim red]"
                freq = get_core_freq(core) if status else "---"
                table.add_row(f"CPU {core:02d}", arch, st_icon, freq)
        console.print(table)

    # 3. Batch process cores helper
    def batch_process_cores(cores_list, enable, action_name):
        print(f"Initiating {action_name} Sequence...")
        for core in cores_list:
            if core in locked_cores:
                continue
            success, msg = set_core_status(core, enable=enable)
            print(f"CPU {core:02d}: {msg}")

    # Check if we should delegate to dusky_tui main.py
    # Delegation triggers: no arguments, or standard main.py flags
    delegate_flags = {"--export-state", "--set", "--default", "--restore", "--reset-key", "interactive", "-h", "--help"}
    if len(sys.argv) == 1 or any(arg in delegate_flags for arg in sys.argv):
        main_py = Path(__file__).resolve().parents[2] / "dusky_tui" / "python" / "main" / "main.py"
        cmd = [sys.executable, str(main_py), str(Path(__file__).resolve()), *sys.argv[1:]]
        try:
            res = subprocess.run(cmd)
            sys.exit(res.returncode)
        except Exception as e:
            print(f"[-] Error delegating to dusky_tui: {e}")
            sys.exit(1)

    # Natively handle custom core manager subcommands
    parser = argparse.ArgumentParser(description="Advanced Hybrid Core Hotplug Manager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status")
    subparsers.add_parser("ecores-only")
    subparsers.add_parser("pcores-only")
    subparsers.add_parser("all-cores")

    toggle_p = subparsers.add_parser("toggle")
    toggle_p.add_argument("cores", nargs="+")
    enable_p = subparsers.add_parser("enable")
    enable_p.add_argument("cores", nargs="+")
    disable_p = subparsers.add_parser("disable")
    disable_p.add_argument("cores", nargs="+")

    args = parser.parse_args()
    all_known_cores = p_cores + e_cores

    if args.command == "status":
        display_status_table()
    else:
        if args.command == "ecores-only":
            if not e_cores:
                print("[-] Error: ecores-only requires a hybrid topology.")
                sys.exit(1)
            batch_process_cores(e_cores, enable=True, action_name="E-Core Wakeup")
            batch_process_cores(p_cores, enable=False, action_name="P-Core Shutdown")
        elif args.command == "pcores-only":
            if not e_cores:
                print("[-] Error: pcores-only requires a hybrid topology.")
                sys.exit(1)
            batch_process_cores(p_cores, enable=True, action_name="P-Core Wakeup")
            batch_process_cores(e_cores, enable=False, action_name="E-Core Shutdown")
        elif args.command == "all-cores":
            batch_process_cores(all_known_cores, enable=True, action_name="Global Wakeup")
        elif args.command == "enable":
            target_cores = parse_core_args(args.cores, all_known_cores)
            batch_process_cores(target_cores, enable=True, action_name="Targeted Wakeup")
        elif args.command == "disable":
            target_cores = parse_core_args(args.cores, all_known_cores)
            batch_process_cores(target_cores, enable=False, action_name="Targeted Shutdown")
        elif args.command == "toggle":
            target_cores = parse_core_args(args.cores, all_known_cores)
            for core in target_cores:
                if core in locked_cores:
                    continue
                current_state = get_core_status(core)
                set_core_status(core, enable=not current_state)
        
        # Save updated core states persistently
        from python.engines.cpu_core import CpuCoreEngine
        engine = CpuCoreEngine()
        engine.save_persistent_state()
        display_status_table()
