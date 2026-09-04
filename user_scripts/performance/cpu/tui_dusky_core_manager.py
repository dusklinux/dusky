#!/usr/bin/env python3
"""
Dusky CPU Core Manager
High-Performance Core Hotplug and Systemd CPU Affinity Manager for Arch Linux (Kernel 7.2+)
"""
import os
import sys
from pathlib import Path

# Enable bytecode caching for maximum startup performance
sys.dont_write_bytecode = False
os.environ.pop("PYTHONDONTWRITEBYTECODE", None)

_tui_root = Path(__file__).resolve().parents[2] / "dusky_tui"
if str(_tui_root) not in sys.path:
    sys.path.insert(0, str(_tui_root))

from python.frontend.core_types import ConfigItem
from python.engines.cpu_core import (
    detect_topology,
    get_core_status,
    get_core_freq,
    set_core_status,
    CpuCoreEngine,
    format_cpu_list,
    parse_cpu_list,
)

p_cores, e_cores, locked_cores = detect_topology()

ENGINE_TYPE = "cpu_core"
TARGET_FILE = "/sys/devices/system/cpu"
APP_TITLE = "Dusky CPU Core Manager"
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
REQUIRE_ROOT = True


def generate_affinity_presets(p_cores: list[int], e_cores: list[int]) -> list[str]:
    """
    Dynamically generates CPUAffinity presets matching the machine's hardware topology.
    Scales generically from 2-core machines to 128+ core systems.
    """
    all_cores = sorted(p_cores + e_cores)
    if not all_cores:
        return ["unset"]

    max_idx = max(all_cores)
    total = len(all_cores)
    presets = ["unset"]

    if total > 1:
        presets.append(f"1-{max_idx}" if max_idx > 1 else "1")

    if p_cores and e_cores:
        p_str = format_cpu_list(p_cores)
        e_str = format_cpu_list(e_cores)
        if p_str:
            presets.append(p_str)
        if e_str:
            presets.append(e_str)
        p_no_zero = [c for c in p_cores if c != 0]
        if p_no_zero:
            presets.append(format_cpu_list(p_no_zero))
    else:
        if total >= 4:
            mid = total // 2
            presets.append(f"0-{mid - 1}")
            presets.append(f"{mid}-{max_idx}")
            if mid > 1:
                presets.append(f"1-{mid - 1}")

    presets.append("0")
    return list(dict.fromkeys(presets))


affinity_presets = generate_affinity_presets(p_cores, e_cores)
max_core_id = max(p_cores + e_cores) if (p_cores or e_cores) else 0

TABS: list[str] = []
if p_cores:
    TABS.append("Performance Cores")
if e_cores:
    TABS.append("Efficient Cores")
TABS.append("System Affinity")
TABS.append("Presets")

USER_PRESETS_TAB = "Presets"

SCHEMA: dict[int, list[ConfigItem]] = {}
tab_idx = 0

if p_cores:
    SCHEMA[tab_idx] = []
    for c in p_cores:
        is_locked = c in locked_cores
        lbl = f"CPU {c:02d} (BSP Locked)" if is_locked else f"CPU {c:02d}"
        help_text = f"Toggle Performance Core {c} online/offline state."
        if is_locked:
            help_text += " (Bootstrap Processor locked by Linux kernel hotplug protection)."
        SCHEMA[tab_idx].append(
            ConfigItem(
                label=lbl,
                key=f"cpu{c}",
                type_="bool",
                default=True,
                extended_help=help_text,
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
                extended_help=f"Toggle Efficient Core {c} online/offline state.",
            )
        )
    tab_idx += 1

SCHEMA[tab_idx] = [
    ConfigItem(
        label="System CPU Affinity",
        key="systemd_cpu_affinity",
        scope="DEFAULT",
        type_="string",
        options=affinity_presets,
        default="unset",
        group="systemd Process Scheduling",
        extended_help=(
            "**Systemd Process CPU Affinity (`CPUAffinity=`)**\n\n"
            "Configures which CPU cores systemd (PID 1) and all descendant user sessions, "
            "desktop applications, and background services are allowed to execute on.\n\n"
            "**Why this is essential for Core 0:**\n"
            f"Because Linux kernel 6.6+ permanently forbids hotplug-offlining Core 0 (BSP), "
            f"setting CPU Affinity to `1-{max_core_id}` is the official runtime mechanism to ensure user applications "
            "and system daemons NEVER run on Core 0, leaving Core 0 dedicated exclusively to kernel interrupts.\n\n"
            "**Common Configurations & Presets:**\n"
            "- `unset`: Normal scheduling across all CPU cores.\n"
            f"- `1-{max_core_id}`: Exclude Core 0 (frees bootstrap core for kernel IRQs and timing).\n"
            "- P-Cores / E-Cores: Restrict all systemd workloads to high-power or high-efficiency cores.\n\n"
            "*Note:* Fully configurable. Presets adapt to your hardware topology, and you can type any custom range (e.g. `2-7`, `0,2,4`, `1-15`). Applied live via `systemctl daemon-reexec`."
        )
    )
]
tab_idx += 1


def ensure_root(argv: list[str]) -> None:
    """Seamlessly escalates to root via sudo if unprivileged."""
    if os.geteuid() == 0:
        return
    import shutil
    sudo_bin = shutil.which("sudo")
    if not sudo_bin:
        print("[-] Error: Root privileges required, but sudo is not installed.")
        sys.exit(1)
    try:
        os.execv(sudo_bin, [sudo_bin, sys.executable, *argv])
    except OSError as e:
        print(f"[-] Failed to escalate via sudo: {e}")
        sys.exit(1)


def parse_core_args(args_list: list[str], valid_cores: list[int]) -> list[int]:
    """
    Parses a variety of user input formats for CPU IDs and ranges:
    e.g. ['1', '2', '3'], ['1-3'], ['1,2,3'], ['1-3,5,7-9'], ['1 - 3, 5']
    Returns a sorted list of unique validated core IDs.
    """
    valid_set = set(valid_cores)
    parsed: set[int] = set()

    for arg in args_list:
        tokens = [t.strip() for t in arg.split(",") if t.strip()]
        for token in tokens:
            if "-" in token:
                parts = [p.strip() for p in token.split("-")]
                if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                    print(f"[-] Syntax Error: Invalid core range '{token}'. Expected format like '1-3'.")
                    sys.exit(1)
                start, end = int(parts[0]), int(parts[1])
                if start > end:
                    start, end = end, start
                parsed.update(range(start, end + 1))
            else:
                if not token.isdigit():
                    print(f"[-] Syntax Error: Invalid CPU identifier '{token}'. Expected integer ID.")
                    sys.exit(1)
                parsed.add(int(token))

    invalid = sorted([c for c in parsed if c not in valid_set])
    if invalid:
        max_valid = max(valid_cores) if valid_cores else 0
        print(f"[-] Hardware Error: CPUs {invalid} do not exist (valid hardware range: 0-{max_valid}).")
        sys.exit(1)

    return sorted(parsed)


def display_status_table() -> None:
    """Displays the core status table with Rich formatting or fallback borderless table."""
    engine = CpuCoreEngine()
    cfg_aff = engine.get_systemd_affinity()
    eff_aff = engine.get_effective_affinity()
    pid1_aff = engine.get_pid1_affinity()
    all_known = sorted(p_cores + e_cores)

    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        from rich.align import Align
    except ImportError:
        print(f"{'CORE':<10} | {'TYPE':<8} | {'ST':<8} | {'FREQUENCY':<10}")
        print("-" * 45)
        for core in all_known:
            arch = "P-Core" if core in p_cores else "E-Core"
            if core in locked_cores:
                status = "Locked"
            else:
                status = "ON" if get_core_status(core) else "OFF"
            print(f"CPU {core:02d}     | {arch:<8} | {status:<8} | {get_core_freq(core)}")
        print("-" * 45)
        print(f"systemd CPUAffinity: {cfg_aff}  |  Active Allowed: {eff_aff}  |  PID 1 Allowed: {pid1_aff}")
        return

    console = Console()
    console.print(Align.center(Panel("[bold magenta]Dusky CPU Core Manager[/bold magenta]", border_style="cyan", expand=False)))
    table = Table(show_header=True, header_style="bold magenta", expand=True)
    table.add_column("CORE", justify="center")
    table.add_column("TYPE", justify="center")
    table.add_column("ST", justify="center")
    table.add_column("FREQUENCY", justify="center")

    for core in all_known:
        arch = "[bold cyan]P-Core[/bold cyan]" if core in p_cores else "[bold green]E-Core[/bold green]"
        if core in locked_cores:
            table.add_row(f"CPU {core:02d}", arch, "[bold yellow] (BSP)[/bold yellow]", get_core_freq(core))
        else:
            status = get_core_status(core)
            st_icon = "[bold green]●[/bold green]" if status else "[dim red]○[/dim red]"
            freq = get_core_freq(core) if status else "---"
            table.add_row(f"CPU {core:02d}", arch, st_icon, freq)
    console.print(table)
    console.print(
        f"[bold cyan]systemd CPUAffinity:[/bold cyan] [bold green]{cfg_aff}[/bold green]  |  "
        f"[bold cyan]Active Allowed:[/bold cyan] [bold yellow]{eff_aff}[/bold yellow]  |  "
        f"[bold cyan]PID 1 Allowed:[/bold cyan] [bold yellow]{pid1_aff}[/bold yellow]\n"
    )


def batch_process_cores(cores_list: list[int], enable: bool, action_name: str) -> None:
    """Batch sets online/offline status for a collection of cores with clear progress reporting."""
    print(f"Initiating {action_name} Sequence...")
    for core in cores_list:
        if core in locked_cores:
            if enable:
                print(f"CPU {core:02d}: Already online (BSP Locked)")
            else:
                print(f"CPU {core:02d}: Skipped (BSP Locked - Kernel Hotplug Protected)")
            continue
        success, msg = set_core_status(core, enable=enable)
        tag = "[OK]" if success else "[-]"
        print(f"{tag} CPU {core:02d}: {msg}")


if __name__ == "__main__":
    import subprocess
    import argparse

    # 1. Check for persistent state restoration
    if "--restore" in sys.argv:
        ensure_root(sys.argv)
        engine = CpuCoreEngine()
        if engine.restore_state():
            print("[OK] Successfully restored persistent CPU core states.")
            sys.exit(0)
        else:
            print("[*] No persistent CPU core states found to restore (or failed to restore).")
            sys.exit(0)

    # 2. Check for dusky_tui delegation
    delegate_flags = {
        "--export-state",
        "--export-docs",
        "--set",
        "--default",
        "--reset-key",
        "--backup",
        "--log",
        "interactive",
    }
    if len(sys.argv) == 1 or any(arg in delegate_flags for arg in sys.argv):
        main_py = Path(__file__).resolve().parents[2] / "dusky_tui" / "python" / "main" / "main.py"
        cmd = [sys.executable, str(main_py), str(Path(__file__).resolve()), *sys.argv[1:]]
        try:
            res = subprocess.run(cmd)
            sys.exit(res.returncode)
        except Exception as e:
            print(f"[-] Error delegating to dusky_tui: {e}")
            sys.exit(1)

    # 3. Natively handle custom core manager subcommands
    parser = argparse.ArgumentParser(
        description="Dusky Advanced Hybrid CPU Core & Affinity Manager (Arch Linux Kernel 7.2+)",
        epilog=(
            "Interactive Mode:\n"
            "  Run without arguments to launch the full graphical Textual TUI.\n\n"
            "TUI Headless Flags:\n"
            "  --export-state       Export active core AST state as JSON\n"
            "  --export-docs        Generate Markdown documentation reference\n"
            "  --set KEY=VAL        Headlessly apply a configuration value\n"
            "  --default            Restore all cores to default online states\n"
            "  --restore            Restore persistent saved states from disk\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Display core online states, topology, frequencies, and affinity")
    subparsers.add_parser("ecores-only", help="Enable all E-cores and offline toggleable P-cores")
    subparsers.add_parser("pcores-only", help="Enable all P-cores and offline all E-cores")
    subparsers.add_parser("all-cores", help="Bring all CPU cores online")

    aff_p = subparsers.add_parser("affinity", help="Inspect or set systemd CPU affinity")
    aff_p.add_argument("mask", nargs="?", default=None, help="Core range or 'unset' (e.g. 1-19, 0-15, unset)")

    toggle_p = subparsers.add_parser("toggle", help="Toggle one or more CPU cores")
    toggle_p.add_argument("cores", nargs="+", help="CPU IDs or ranges (e.g. 1 2 3, 1-4, 2,4,6)")

    enable_p = subparsers.add_parser("enable", help="Bring specified CPU cores online")
    enable_p.add_argument("cores", nargs="+", help="CPU IDs or ranges (e.g. 1-4, 5, 6)")

    disable_p = subparsers.add_parser("disable", help="Take specified CPU cores offline")
    disable_p.add_argument("cores", nargs="+", help="CPU IDs or ranges (e.g. 1-4, 5, 6)")

    args = parser.parse_args()
    all_known_cores = sorted(p_cores + e_cores)

    if args.command == "status":
        display_status_table()

    elif args.command == "affinity":
        engine = CpuCoreEngine()
        if args.mask is None:
            cfg = engine.get_systemd_affinity()
            eff = engine.get_effective_affinity()
            pid1 = engine.get_pid1_affinity()
            print(f"systemd CPUAffinity: {cfg} (Active: {eff} | PID 1: {pid1})")
        else:
            ensure_root(sys.argv)
            ok, msg = engine.set_systemd_affinity(args.mask)
            if ok:
                print(f"[OK] {msg}")
                eff = engine.get_effective_affinity()
                pid1 = engine.get_pid1_affinity()
                print(f"[*] Live Active Allowed Mask: {eff} | PID 1: {pid1}")
            else:
                print(f"[-] Error: {msg}")
                sys.exit(1)

    else:
        # All core modification commands require root
        ensure_root(sys.argv)

        if args.command == "ecores-only":
            if not e_cores:
                print("[-] Error: ecores-only requires a hybrid CPU topology with Efficient Cores.")
                sys.exit(1)
            batch_process_cores(e_cores, enable=True, action_name="E-Core Wakeup")
            batch_process_cores(p_cores, enable=False, action_name="P-Core Shutdown")

        elif args.command == "pcores-only":
            if not e_cores:
                print("[-] Error: pcores-only requires a hybrid CPU topology.")
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
            print("Initiating Targeted Toggle Sequence...")
            for core in target_cores:
                if core in locked_cores:
                    print(f"CPU {core:02d}: Skipped (BSP Locked - Kernel Hotplug Protected)")
                    continue
                current_state = get_core_status(core)
                new_state = not current_state
                success, msg = set_core_status(core, enable=new_state)
                st_label = "ON" if new_state else "OFF"
                tag = "[OK]" if success else "[-]"
                print(f"{tag} CPU {core:02d}: Toggled -> {st_label} ({msg})")

        # Save updated state and display live table
        engine = CpuCoreEngine()
        engine.save_persistent_state()
        display_status_table()
