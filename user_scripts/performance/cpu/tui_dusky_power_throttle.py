#!/usr/bin/env python3
import os
import sys
import json
import time
import shutil
import subprocess
from pathlib import Path
from typing import Any

# Dynamically resolve Dusky TUI root
_tui_root = Path(__file__).resolve().parents[2] / "dusky_tui"
if str(_tui_root) not in sys.path:
    sys.path.insert(0, str(_tui_root))

from python.frontend.core_types import ConfigItem
from python.engines.pkg_throttle import PkgThrottleEngine, safe_read_int, get_cpu_model

# ==============================================================================
# DYNAMIC HARDWARE DISCOVERY & SCHEMA GENERATION
# ==============================================================================

_engine = PkgThrottleEngine()
_domain = _engine.domain
_boot_data = _engine.get_boot_limits()

def _get_boot_val(file_name: str) -> int | None:
    val = _boot_data.get(file_name)
    if val is not None and val > 0:
        return val
    if _domain:
        val = safe_read_int(_domain / file_name)
        if val is not None and val > 0:
            return val
    return None

# Probe which constraints are physically supported by the CPU hardware
has_pl1 = bool(_domain and (_domain / "constraint_0_power_limit_uw").exists())
has_pl2 = bool(_domain and (_domain / "constraint_1_power_limit_uw").exists())
has_pl4 = bool(_domain and (_domain / "constraint_2_power_limit_uw").exists())
has_pl1_time = bool(_domain and (_domain / "constraint_0_time_window_us").exists() and safe_read_int(_domain / "constraint_0_time_window_us") is not None)
has_pl2_time = bool(_domain and (_domain / "constraint_1_time_window_us").exists() and safe_read_int(_domain / "constraint_1_time_window_us") is not None)

# Dynamically resolve values directly from active hardware sysfs / baseline
pl1_raw = _get_boot_val("constraint_0_power_limit_uw") if has_pl1 else None
pl2_raw = _get_boot_val("constraint_1_power_limit_uw") if has_pl2 else None
pl4_raw = _get_boot_val("constraint_2_power_limit_uw") if has_pl4 else None
pl1_time_raw = _get_boot_val("constraint_0_time_window_us") if has_pl1_time else None
pl2_time_raw = _get_boot_val("constraint_1_time_window_us") if has_pl2_time else None

pl1_def = (pl1_raw // 1_000_000) if pl1_raw else 65
pl2_def = (pl2_raw // 1_000_000) if pl2_raw else pl1_def
pl4_def = (pl4_raw // 1_000_000) if pl4_raw else (pl2_def * 2)
pl1_time_def = round(pl1_time_raw / 1_000_000, 2) if pl1_time_raw else 28.0
pl2_time_def = round(pl2_time_raw / 1_000_000, 4) if pl2_time_raw else 0.0024

ENGINE_TYPE = "pkg_throttle"
TARGET_FILE = "/sys/class/powercap"
APP_TITLE = "Dusky Power Limit Manager"
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
REQUIRE_ROOT = True

# Assemble dynamic tabs and schema items based on physical CPU capabilities
power_items: list[ConfigItem] = []
if has_pl1:
    power_items.append(
        ConfigItem(
            label="PL1 (Long-Term Limit)",
            key="pl1",
            type_="int",
            default=pl1_def,
            min_val=1,
            max_val=max(1000, pl1_def * 4),
            step=1,
            extended_help="Sustained long-term CPU package power limit envelope (in Watts). Applies under continuous high workloads."
        )
    )

if has_pl2:
    power_items.append(
        ConfigItem(
            label="PL2 (Short-Term Boost)",
            key="pl2",
            type_="int",
            default=pl2_def,
            min_val=1,
            max_val=max(1000, pl2_def * 4),
            step=1,
            extended_help="Maximum transient boost power envelope (in Watts). Sustained for the duration of the PL2 time window."
        )
    )

if has_pl4:
    power_items.append(
        ConfigItem(
            label="PL4 (Peak Limit)",
            key="pl4",
            type_="int",
            default=pl4_def,
            min_val=1,
            max_val=max(1000, pl4_def * 4),
            step=5,
            extended_help="Absolute physical hardware power spike clamp (in Watts). Prevents PSU protection triggers on rapid power transitions."
        )
    )

if not power_items:
    power_items.append(
        ConfigItem(
            label="RAPL Unavailable",
            key="unsupported",
            type_="action",
            default="N/A",
            extended_help="No supported RAPL / Powercap energy domains were discovered in /sys/class/powercap. Ensure the 'intel_rapl_msr' or 'amd_energy' driver is loaded."
        )
    )

time_items: list[ConfigItem] = []
if has_pl1_time:
    time_items.append(
        ConfigItem(
            label="PL1 Time Window",
            key="pl1_time",
            type_="float",
            default=pl1_time_def,
            min_val=0.001,
            max_val=max(150.0, pl1_time_def * 2),
            step=0.5,
            extended_help="Rolling averaging window (in seconds) for long-term PL1 enforcement."
        )
    )

if has_pl2_time:
    time_items.append(
        ConfigItem(
            label="PL2 Time Window",
            key="pl2_time",
            type_="float",
            default=pl2_time_def,
            min_val=0.0001,
            max_val=max(10.0, pl2_time_def * 4),
            step=0.0005,
            extended_help="Maximum duration envelope (in seconds) that the CPU package is permitted to boost up to PL2 power limits before scaling down."
        )
    )

SCHEMA: dict[int, list[ConfigItem]] = {0: power_items}
TABS = ["Power Limits"]

if time_items:
    SCHEMA[1] = time_items
    TABS.append("Time Windows")

TABS.append("Presets")
USER_PRESETS_TAB = "Presets"

TAB_NOTICES: dict[int, dict[str, str]] = {}
if has_pl4:
    TAB_NOTICES[0] = {
        "level": "warning",
        "position": "bottom",
        "message": "Setting PL4 too low can trigger a failsafe hardware lock (minimum clock throttle) to protect voltage regulators. Keep PL4 at its BIOS default unless you explicitly need to clamp peak currents."
    }

# ==============================================================================
# CLI HELPERS & STATUS REPORTING
# ==============================================================================

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

def parse_set_args(args_list: list[str]) -> list[tuple[str, str]]:
    """
    Parses key=value or key value pairs from CLI.
    Supports: ['pl1=65', 'pl2=90'], ['pl1', '65', 'pl2', '90'], ['pl1_time=28.0']
    """
    pairs: list[tuple[str, str]] = []
    i = 0
    while i < len(args_list):
        item = args_list[i]
        if "=" in item:
            k, v = item.split("=", 1)
            pairs.append((k.strip().lower(), v.strip()))
            i += 1
        elif i + 1 < len(args_list) and not args_list[i + 1].startswith("-"):
            pairs.append((item.strip().lower(), args_list[i + 1].strip()))
            i += 2
        else:
            i += 1
    return pairs

def display_status_table() -> None:
    """Renders a rich, comprehensive status table of CPU power limits and telemetry."""
    engine = PkgThrottleEngine()
    info = engine.get_power_limits()
    if not info:
        print("[-] Error: No active RAPL / Powercap domain found on this system.")
        return

    cpu_model = get_cpu_model()
    domain_path = info["domain"]
    domain_name = info["domain_name"]
    is_modified = info["modified"]
    persisted_file = info["persistent_file"]
    persisted_data = info["persistent_data"]
    limits = info["limits"]
    windows = info["time_windows"]
    telemetry = engine.get_telemetry()

    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        from rich.align import Align
    except ImportError:
        # Clean ASCII fallback
        print("=== Dusky Power Limit Manager ===")
        print(f"CPU Model   : {cpu_model}")
        print(f"RAPL Domain : {domain_name} ({domain_path})")
        print(f"Modified    : {is_modified} | Persisted: {persisted_data}")
        print("-" * 65)
        print(f"{'PARAMETER':<12} | {'CURRENT':<12} | {'BOOT DEFAULT':<14} | {'STATUS':<10}")
        print("-" * 65)
        for k, v in limits.items():
            if v["supported"]:
                st = "CUSTOM" if is_modified and v["current"] != v["boot"] else "STOCK"
                print(f"{k.upper():<12} | {str(v['current']) + ' ' + v['unit']:<12} | {str(v['boot']) + ' ' + v['unit']:<14} | {st:<10}")
        for k, v in windows.items():
            if v["supported"]:
                st = "CUSTOM" if is_modified and abs(v["current"] - v["boot"]) > 0.01 else "STOCK"
                print(f"{k:<12} | {str(v['current']) + ' ' + v['unit']:<12} | {str(v['boot']) + ' ' + v['unit']:<14} | {st:<10}")
        print("-" * 65)
        print(telemetry)
        return

    console = Console()
    header_text = (
        f"[bold magenta]Dusky Power Limit Manager[/bold magenta]\n"
        f"[dim]{cpu_model}  •  Domain: {domain_name} ({domain_path})[/dim]"
    )
    console.print(Align.center(Panel(header_text, border_style="cyan", expand=False)))

    # Power Limits Table
    p_table = Table(show_header=True, header_style="bold magenta", expand=True, title=" CPU Package Power Envelopes")
    p_table.add_column("Constraint", style="bold cyan", justify="left")
    p_table.add_column("Key", justify="center")
    p_table.add_column("Active Limit", justify="center")
    p_table.add_column("BIOS Baseline", justify="center")
    p_table.add_column("State", justify="center")

    for k, v in limits.items():
        if v["supported"]:
            curr = v["current"]
            boot = v["boot"]
            if is_modified and curr != boot:
                st_badge = "[bold yellow]CUSTOM[/bold yellow]"
                curr_style = f"[bold yellow]{curr} {v['unit']}[/bold yellow]"
            else:
                st_badge = "[bold green]STOCK[/bold green]"
                curr_style = f"[bold green]{curr} {v['unit']}[/bold green]"
            p_table.add_row(v["label"], f"`{k}`", curr_style, f"{boot} {v['unit']}", st_badge)

    console.print(p_table)

    # Time Windows Table
    if any(w["supported"] for w in windows.values()):
        t_table = Table(show_header=True, header_style="bold magenta", expand=True, title=" Thermal Averaging Time Windows")
        t_table.add_column("Window", style="bold cyan", justify="left")
        t_table.add_column("Key", justify="center")
        t_table.add_column("Active Duration", justify="center")
        t_table.add_column("BIOS Baseline", justify="center")
        t_table.add_column("State", justify="center")

        for k, v in windows.items():
            if v["supported"]:
                curr = v["current"]
                boot = v["boot"]
                if is_modified and abs(curr - boot) > 0.01:
                    st_badge = "[bold yellow]CUSTOM[/bold yellow]"
                    curr_style = f"[bold yellow]{curr:.4f} {v['unit']}[/bold yellow]"
                else:
                    st_badge = "[bold green]STOCK[/bold green]"
                    curr_style = f"[bold green]{curr:.4f} {v['unit']}[/bold green]"
                t_table.add_row(v["label"], f"`{k}`", curr_style, f"{boot:.4f} {v['unit']}", st_badge)

        console.print(t_table)

    # Telemetry and Persistence Banner
    persisted_str = ", ".join(f"{k}: {v}" for k, v in persisted_data.items()) if persisted_data else "None"
    status_summary = (
        f"[dim]Telemetry:[/dim] {telemetry}\n"
        f"[dim]Persistence ({persisted_file}):[/dim] [cyan]{persisted_str}[/cyan]"
    )
    console.print(Panel(status_summary, border_style="dim cyan", expand=True))

def monitor_telemetry() -> None:
    """Continuously prints live power consumption until interrupted."""
    ensure_root(sys.argv)
    engine = PkgThrottleEngine()
    print("[*] Monitoring CPU Package Power Telemetry (Press Ctrl+C to stop)...")
    try:
        while True:
            t = engine.get_telemetry()
            print(f"\r{t}", end="", flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[*] Monitoring stopped.")
        sys.exit(0)

# ==============================================================================
# ENTRY POINT & CLI DISPATCHER
# ==============================================================================

if __name__ == "__main__":
    args_raw = sys.argv[1:]

    # 1. Native CLI Subcommands
    if len(args_raw) > 0:
        cmd = args_raw[0].lower()

        # Status inspection (Unprivileged)
        if cmd in ("status", "--status", "info"):
            display_status_table()
            sys.exit(0)

        # Documentation export (Unprivileged)
        elif cmd in ("--export-docs", "export-docs"):
            print(f"# Configuration Reference: {APP_TITLE}\n")
            for tab_idx, items in SCHEMA.items():
                tab_name = TABS[tab_idx] if isinstance(TABS, dict) else TABS[tab_idx]
                print(f"## {tab_name}")
                for item in items:
                    if item.type_ in ("action", "preset", "menu"):
                        continue
                    print(f"### `{item.key}`")
                    print(f"- **Type:** `{item.type_}`")
                    print(f"- **Default:** `{item.default}`")
                    if item.extended_help:
                        print(f"\n> {item.extended_help.replace('**', '')}\n")
            sys.exit(0)

        # State export (Unprivileged)
        elif cmd in ("--export-state", "export-state"):
            engine = PkgThrottleEngine()
            print(json.dumps(engine.load_state(), indent=2))
            sys.exit(0)

        # Telemetry monitor
        elif cmd in ("monitor", "telemetry"):
            monitor_telemetry()
            sys.exit(0)

        # Restore saved limits (Used by dusky_cpu.service on boot)
        elif cmd in ("--restore", "restore"):
            ensure_root(sys.argv)
            engine = PkgThrottleEngine()
            if engine.restore_state():
                print("[OK] Successfully restored persistent CPU power limits.")
                sys.exit(0)
            else:
                print("[*] No persistent power limits state found to restore (or failed to restore).")
                sys.exit(0)

        # Revert to BIOS factory baseline
        elif cmd in ("default", "reset", "--default") and len(args_raw) == 1:
            ensure_root(sys.argv)
            engine = PkgThrottleEngine()
            ok, msg = engine.restore_defaults()
            if ok:
                print(f"[OK] {msg}")
                display_status_table()
                sys.exit(0)
            else:
                print(f"[-] Error: {msg}")
                sys.exit(1)

        # Headless key=value modification
        elif cmd == "set" or cmd.startswith("--set="):
            ensure_root(sys.argv)
            engine = PkgThrottleEngine()
            if cmd.startswith("--set="):
                pairs = parse_set_args([cmd[6:]])
            else:
                pairs = parse_set_args(args_raw[1:])

            if not pairs:
                print("[-] Error: Specify parameters to set, e.g. 'set pl1=65 pl2=90'")
                sys.exit(1)

            all_ok = True
            for key, val in pairs:
                ok, msg, _ = engine.write_value(key, "DEFAULT", val)
                tag = "[OK]" if ok else "[-]"
                print(f"{tag} {msg}")
                if not ok:
                    all_ok = False

            display_status_table()
            sys.exit(0 if all_ok else 1)

        # Standalone Help
        elif cmd in ("-h", "--help", "help"):
            print("Dusky CPU Power Limit Manager (Bleeding-Edge RAPL / Powercap Architecture)")
            print("\nUsage:")
            print("  python3 tui_dusky_power_throttle.py [COMMAND] [OPTIONS]\n")
            print("Commands:")
            print("  status                  Display comprehensive power limits, baseline, and telemetry")
            print("  set <k=v ...>           Apply power limits (e.g. set pl1=65 pl2=90 pl1_time=28.0)")
            print("  default                 Restore original boot/BIOS baseline limits")
            print("  restore                 Restore saved persistent configuration (dusky_pkg_power)")
            print("  monitor                 Continuously monitor live CPU package power draw")
            print("\nDusky Router Options:")
            print("  --export-docs           Generate markdown documentation for the schema")
            print("  --export-state          Export AST state as JSON to stdout")
            print("  --set KEY=VALUE         Headlessly set parameter via Dusky router")
            print("  --default               Reset all parameters to defaults via Dusky router")
            print("  (no arguments)          Launch interactive Dusky Textual TUI")
            sys.exit(0)

    # 2. Forward to Dusky TUI master router for interactive UI or standard schema flags
    main_py = Path(__file__).resolve().parents[2] / "dusky_tui" / "python" / "main" / "main.py"

    # Interactive TUI mode requires root privileges
    if not args_raw or (len(args_raw) == 1 and args_raw[0] in ("--tui", "-t")):
        ensure_root(sys.argv)

    cmd = [sys.executable, str(main_py), str(Path(__file__).resolve()), *args_raw]
    try:
        res = subprocess.run(cmd)
        sys.exit(res.returncode)
    except Exception as e:
        print(f"[-] Error delegating to dusky_tui: {e}")
        sys.exit(1)
