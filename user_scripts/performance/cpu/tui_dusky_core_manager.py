#!/usr/bin/env python3
"""
Dusky CPU Core Manager
High-Performance Core Hotplug and Systemd CPU Affinity Manager for Arch Linux (Kernel 7.3+)
"""
import os
import sys
lazy import shutil
lazy import subprocess
lazy import argparse
from pathlib import Path

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

    total = len(all_cores)
    presets = ["unset"]

    if total > 1:
        presets.append(format_cpu_list([c for c in all_cores if c != 0]))

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
            presets.append(format_cpu_list(all_cores[:mid]))
            presets.append(format_cpu_list(all_cores[mid:]))
            if mid > 1:
                presets.append(format_cpu_list([c for c in all_cores[:mid] if c != 0]))

    if 0 in all_cores:
        presets.append("0")
    return list(dict.fromkeys(presets))


affinity_presets = generate_affinity_presets(p_cores, e_cores)

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
        lbl = f"CPU {c:02d} (Kernel Locked)" if is_locked else f"CPU {c:02d}"
        help_text = f"Toggle Performance Core {c} online/offline state."
        if is_locked:
            help_text += " (This CPU cannot be offlined through the supported kernel interface)."
        SCHEMA[tab_idx].append(
            ConfigItem(
                label=lbl,
                key=f"cpu{c}",
                type_="bool",
                default=True,
                read_only=is_locked,
                extended_help=help_text,
            )
        )
    tab_idx += 1

if e_cores:
    SCHEMA[tab_idx] = []
    for c in e_cores:
        is_locked = c in locked_cores
        lbl = f"CPU {c:02d} (Kernel Locked)" if is_locked else f"CPU {c:02d}"
        SCHEMA[tab_idx].append(
            ConfigItem(
                label=lbl,
                key=f"cpu{c}",
                type_="bool",
                default=True,
                read_only=is_locked,
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
            "Sets systemd's default process affinity and AllowedCPUs on user.slice and system.slice. "
            "Slice limits apply to existing workloads immediately; process affinity also applies to "
            "new services. Existing per-process masks may remain narrower until processes restart. "
            "CPU 0 stays online. Excluding it does not configure interrupts or isolate kernel work. "
            "Other slices, including machine.slice, are outside these slice limits. "
            "Use unset to clear Dusky's manager setting and these two slice CPU limits. "
            "Custom CPU lists such as 2-7 or 0,2,4 are supported."
        )
    )
]
tab_idx += 1

TAB_NOTICES = {
    TABS.index("System Affinity"): {
        "level": "warning",
        "position": "top",
        "message": "Slice limits apply live; existing process affinity masks may require a process restart to widen.",
    }
}


def ensure_root(argv: list[str]) -> None:
    """Seamlessly escalates to root via sudo if unprivileged."""
    if os.geteuid() == 0:
        return
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
    ok, msg, parsed = parse_cpu_list(" ".join(args_list), max(valid_cores) if valid_cores else 0)
    if ok and msg == "all":
        parsed = set(valid_cores)
    if not ok or not parsed or parsed - set(valid_cores):
        print(f"[-] Invalid CPU selection: {msg if not ok else 'select existing CPU IDs'}")
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
            table.add_row(f"CPU {core:02d}", arch, "[bold yellow] (Locked)[/bold yellow]", get_core_freq(core))
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


def batch_process_cores(cores_list: list[int], enable: bool, action_name: str) -> bool:
    """Batch sets online/offline status for a collection of cores with clear progress reporting."""
    all_ok = True
    print(f"Initiating {action_name} Sequence...")
    for core in cores_list:
        if core in locked_cores:
            if enable:
                print(f"CPU {core:02d}: Already online (Kernel Locked)")
            else:
                print(f"CPU {core:02d}: Skipped (Kernel Hotplug Protected)")
            continue
        success, msg = set_core_status(core, enable=enable)
        tag = "[OK]" if success else "[-]"
        print(f"{tag} CPU {core:02d}: {msg}")
        all_ok = success and all_ok
    return all_ok


if __name__ == "__main__":
    # The service restores both independent components, even if one fails.
    if sys.argv[1:] in (["--restore"], ["--restore-all"]):
        ensure_root(sys.argv)
        ok = CpuCoreEngine().restore_state()
        print("[OK] CPU core restore completed (or no saved state)." if ok else "[-] CPU core restore failed.")
        power_ok = True
        if sys.argv[1:] == ["--restore-all"]:
            power_script = Path(__file__).with_name("tui_dusky_power_throttle.py")
            try:
                power_ok = subprocess.run([sys.executable, str(power_script), "--restore"]).returncode == 0
            except OSError as exc:
                print(f"[-] Cannot launch power restore: {exc}")
                power_ok = False
        sys.exit(0 if ok and power_ok else 1)

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
    if len(sys.argv) == 1 or any(arg.split("=", 1)[0] in delegate_flags for arg in sys.argv[1:]):
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
        description="Dusky Advanced Hybrid CPU Core & Affinity Manager (Arch Linux Kernel 7.3+)",
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

        all_ok = True
        if args.command == "ecores-only":
            if not e_cores:
                print("[-] Error: ecores-only requires a hybrid CPU topology with Efficient Cores.")
                sys.exit(1)
            all_ok = batch_process_cores(e_cores, enable=True, action_name="E-Core Wakeup")
            if all_ok:
                all_ok = batch_process_cores(p_cores, enable=False, action_name="P-Core Shutdown")

        elif args.command == "pcores-only":
            if not e_cores:
                print("[-] Error: pcores-only requires a hybrid CPU topology.")
                sys.exit(1)
            all_ok = batch_process_cores(p_cores, enable=True, action_name="P-Core Wakeup")
            if all_ok:
                all_ok = batch_process_cores(e_cores, enable=False, action_name="E-Core Shutdown")

        elif args.command == "all-cores":
            all_ok = batch_process_cores(all_known_cores, enable=True, action_name="Global Wakeup") and all_ok

        elif args.command == "enable":
            target_cores = parse_core_args(args.cores, all_known_cores)
            all_ok = batch_process_cores(target_cores, enable=True, action_name="Targeted Wakeup") and all_ok

        elif args.command == "disable":
            target_cores = parse_core_args(args.cores, all_known_cores)
            all_ok = batch_process_cores(target_cores, enable=False, action_name="Targeted Shutdown") and all_ok

        elif args.command == "toggle":
            target_cores = parse_core_args(args.cores, all_known_cores)
            print("Initiating Targeted Toggle Sequence...")
            for core in target_cores:
                if core in locked_cores:
                    print(f"CPU {core:02d}: Skipped (Kernel Hotplug Protected)")
                    continue
                current_state = get_core_status(core)
                new_state = not current_state
                success, msg = set_core_status(core, enable=new_state)
                st_label = "ON" if new_state else "OFF"
                tag = "[OK]" if success else "[-]"
                print(f"{tag} CPU {core:02d}: Toggled -> {st_label} ({msg})")
                all_ok = success and all_ok

        # Save updated state and display live table
        engine = CpuCoreEngine()
        try:
            engine.save_persistent_state()
        except OSError as exc:
            print(f"[-] Persistence failed: {exc}")
            all_ok = False
        display_status_table()
        sys.exit(0 if all_ok else 1)
