#!/usr/bin/env python3
import os
import sys
lazy import json
lazy import time
lazy import shutil
lazy import subprocess
from pathlib import Path

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
    if val is not None and val >= 0:
        return val
    if _domain:
        val = safe_read_int(_domain / file_name)
        if val is not None and val >= 0:
            return val
    return None

# Probe which constraints are physically supported by the CPU hardware
has_pl1 = bool(_domain and (_domain / _engine.constraint_file("pl1")).exists())
has_pl2 = bool(_domain and (_domain / _engine.constraint_file("pl2")).exists())
has_pl4 = bool(_domain and (_domain / _engine.constraint_file("pl4")).exists())
has_pl1_time = bool(_domain and (_domain / _engine.constraint_file("pl1_time")).exists() and safe_read_int(_domain / _engine.constraint_file("pl1_time")) is not None)
has_pl2_time = bool(_domain and (_domain / _engine.constraint_file("pl2_time")).exists() and safe_read_int(_domain / _engine.constraint_file("pl2_time")) is not None)

# Dynamically resolve values directly from active hardware sysfs / baseline
pl1_raw = _get_boot_val(_engine.constraint_file("pl1")) if has_pl1 else None
pl2_raw = _get_boot_val(_engine.constraint_file("pl2")) if has_pl2 else None
pl4_raw = _get_boot_val(_engine.constraint_file("pl4")) if has_pl4 else None
pl1_time_raw = _get_boot_val(_engine.constraint_file("pl1_time")) if has_pl1_time else None
pl2_time_raw = _get_boot_val(_engine.constraint_file("pl2_time")) if has_pl2_time else None

pl1_def = pl1_raw / 1_000_000 if pl1_raw is not None else 0.0
pl2_def = pl2_raw / 1_000_000 if pl2_raw is not None else 0.0
pl4_def = pl4_raw / 1_000_000 if pl4_raw is not None else 0.0
pl1_time_def = pl1_time_raw / 1_000_000 if pl1_time_raw is not None else 0.0
pl2_time_def = pl2_time_raw / 1_000_000 if pl2_time_raw is not None else 0.0

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
            type_="float",
            default=pl1_def,
            min_val=0,
            max_val=max(1000, pl1_def * 4),
            step=1,
            extended_help="Average CPU package power in Watts over the PL1 time window. Short samples can exceed PL1, especially when PL2 is higher. This does not cap CPU usage, frequency, or temperature."
        )
    )

if has_pl2:
    power_items.append(
        ConfigItem(
            label="PL2 (Short-Term Boost)",
            key="pl2",
            type_="float",
            default=pl2_def,
            min_val=0,
            max_val=max(1000, pl2_def * 4),
            step=1,
            extended_help="Short-term CPU package power limit (in Watts), enforced over its averaging window. Actual boost duration depends on workload, temperatures, and other limits."
        )
    )

if has_pl4:
    power_items.append(
        ConfigItem(
            label="PL4 (Peak Limit)",
            key="pl4",
            type_="float",
            default=pl4_def,
            min_val=0,
            max_val=max(1000, pl4_def * 4),
            step=5,
            extended_help="Hardware peak power constraint in Watts, where supported. Its exact enforcement is platform dependent; it is not a guarantee for a software telemetry sample."
        )
    )

if not power_items:
    power_items.append(
        ConfigItem(
            label="RAPL Unavailable",
            key="unsupported",
            type_="action",
            default=":",
            read_only=True,
            extended_help="No supported RAPL / Powercap energy domains were discovered in /sys/class/powercap. Check whether the platform exposes writable package power constraints through powercap."
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
            min_val=0,
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
            min_val=0,
            max_val=max(10.0, pl2_time_def * 4),
            step=0.0005,
            extended_help="Averaging window (in seconds) for the short-term PL2 constraint; this is not a guaranteed boost duration."
        )
    )

SCHEMA: dict[int, list[ConfigItem]] = {0: power_items}
TABS = ["Power Limits"]

if time_items:
    SCHEMA[1] = time_items
    TABS.append("Time Windows")

TABS.append("Presets")
USER_PRESETS_TAB = "Presets"

POWER_NOTICE = (
    "PL1 and PL2 are averaged power limits, not instantaneous ceilings. "
    "For reduced bursts, set both (e.g. set 35 sets PL1 and supported PL2 to 35 W). "
    "Firmware and other power managers can change these settings after a write or resume."
)
TAB_NOTICES = {0: {"level": "info", "position": "top", "message": POWER_NOTICE}}

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
    Supports:
      - ['35'] or ['35w'] -> sets both pl1=35, pl2=35
      - ['pl1=65', 'pl2=90']
      - ['pl1', '65', 'pl2', '90']
      - ['pl1_time=28.0']
    """
    if len(args_list) == 1 and not args_list[0].startswith("-") and "=" not in args_list[0]:
        val = args_list[0].strip().rstrip("wW")
        try:
            float(val)
            return [("pl1", val), ("pl2", val)] if has_pl2 else [("pl1", val)]
        except ValueError:
            pass

    pairs: list[tuple[str, str]] = []
    i = 0
    while i < len(args_list):
        item = args_list[i]
        if "=" in item:
            k, v = item.split("=", 1)
            pairs.append((k.strip().lower(), v.strip().rstrip("wW")))
            i += 1
        elif i + 1 < len(args_list) and not args_list[i + 1].startswith("-"):
            pairs.append((item.strip().lower(), args_list[i + 1].strip().rstrip("wW")))
            i += 2
        else:
            raise ValueError(f"Missing value for {item}")
        if not pairs[-1][0] or not pairs[-1][1]:
            raise ValueError("Expected a nonempty key and value")
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
    package_lines = []
    for name, settings in info["packages"].items():
        enabled = info["enforcement"][name]
        enforcement = "on" if enabled == 1 else "OFF" if enabled == 0 else "unknown"
        values = ", ".join(f"{k}={v:g}{'s' if k.endswith('_time') else 'W'}" for k, v in settings.items())
        package_lines.append(f"{name}: enforcement {enforcement}; {values}")

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
        print(f"{'PARAMETER':<12} | {'CURRENT':<12} | {'BASELINE':<14} | {'STATUS':<10}")
        print("-" * 65)
        for k, v in limits.items():
            if v["supported"]:
                st = "CUSTOM" if v["current"] != v["boot"] else "STOCK"
                print(f"{k.upper():<12} | {str(v['current']) + ' ' + v['unit']:<12} | {str(v['boot']) + ' ' + v['unit']:<14} | {st:<10}")
        for k, v in windows.items():
            if v["supported"]:
                st = "CUSTOM" if abs(v["current"] - v["boot"]) > 0.0000005 else "STOCK"
                print(f"{k:<12} | {str(v['current']) + ' ' + v['unit']:<12} | {str(v['boot']) + ' ' + v['unit']:<14} | {st:<10}")
        print("-" * 65)
        plat_info = info.get("platform_extension") or info.get("asus_wmi", {})
        if plat_info.get("supported"):
            vendor = plat_info.get("vendor", "Platform Hardware Limit")
            print(f"{vendor}: Sustained (PL1) = {plat_info.get('pl1')} W | Burst (PL2) = {plat_info.get('pl2')} W")
            print("-" * 65)
        print(telemetry)
        print("\n".join(package_lines))
        print(POWER_NOTICE)
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
    p_table.add_column("Captured Baseline", justify="center")
    p_table.add_column("State", justify="center")

    for k, v in limits.items():
        if v["supported"]:
            curr = v["current"]
            boot = v["boot"]
            if curr != boot:
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
        t_table.add_column("Captured Baseline", justify="center")
        t_table.add_column("State", justify="center")

        for k, v in windows.items():
            if v["supported"]:
                curr = v["current"]
                boot = v["boot"]
                if abs(curr - boot) > 0.0000005:
                    st_badge = "[bold yellow]CUSTOM[/bold yellow]"
                    curr_style = f"[bold yellow]{curr:.6f} {v['unit']}[/bold yellow]"
                else:
                    st_badge = "[bold green]STOCK[/bold green]"
                    curr_style = f"[bold green]{curr:.6f} {v['unit']}[/bold green]"
                t_table.add_row(v["label"], f"`{k}`", curr_style, f"{boot:.6f} {v['unit']}", st_badge)

        console.print(t_table)

    # Telemetry, Hardware Platform, and Persistence Banner
    plat_info = info.get("platform_extension") or info.get("asus_wmi", {})
    summary_lines = []
    if plat_info.get("supported"):
        vendor = plat_info.get("vendor", "Platform Hardware Limit")
        p1 = plat_info.get("pl1", "N/A")
        p2 = plat_info.get("pl2", "N/A")
        summary_lines.append(f"[bold cyan]{vendor}:[/bold cyan] Sustained (PL1) = [bold green]{p1} W[/bold green]  •  Burst (PL2) = [bold green]{p2} W[/bold green]")
    summary_lines.append(f"[dim]Telemetry:[/dim] {telemetry}")
    summary_lines.extend(package_lines)
    persisted_str = ", ".join(f"{k}: {v}" for k, v in persisted_data.items()) if persisted_data else "None"
    summary_lines.append(f"[dim]Persistence ({persisted_file}):[/dim] [cyan]{persisted_str}[/cyan]")
    console.print(Panel("\n".join(summary_lines), border_style="dim cyan", expand=True))
    console.print(POWER_NOTICE, markup=False)

def monitor_telemetry() -> None:
    """Continuously prints live power consumption until interrupted."""
    ensure_root(sys.argv)
    engine = PkgThrottleEngine()
    print("[*] Monitoring CPU Package Power Telemetry (Press Ctrl+C to stop)...")
    print(POWER_NOTICE)
    try:
        while True:
            t = engine.get_telemetry()
            print(f"\r\x1b[2K{t}", end="", flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[*] Monitoring stopped.")
        sys.exit(0)
    finally:
        engine.shutdown()

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
                tab_name = TABS[tab_idx]
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
                print("[OK] Power restore completed (or no saved state).")
                sys.exit(0)
            else:
                print("[-] Failed to restore persistent CPU power limits.")
                sys.exit(1)

        # Revert to captured baseline
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
        elif cmd in ("set", "--set") or cmd.startswith("--set="):
            ensure_root(sys.argv)
            engine = PkgThrottleEngine()
            try:
                pairs = parse_set_args([args_raw[0][6:]] + args_raw[1:] if cmd.startswith("--set=") else args_raw[1:])
            except ValueError as exc:
                print(f"[-] {exc}")
                sys.exit(1)
            if not pairs:
                print("[-] Specify parameters, e.g. set pl1=65 pl2=90")
                sys.exit(1)
            all_ok, msg, _ = engine.write_batch([(k, "DEFAULT", v, "float") for k, v in pairs])
            print(f"{'[OK]' if all_ok else '[-]'} {msg}")

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
            print("  set 35                  Set PL1 and supported PL2 to 35 W (averaged limits)")
            print("  default                 Restore the captured baseline limits")
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
    if args_raw in (["--tui"], ["-t"]):
        args_raw = []

    cmd = [sys.executable, str(main_py), str(Path(__file__).resolve()), *args_raw]
    try:
        res = subprocess.run(cmd)
        sys.exit(res.returncode)
    except Exception as e:
        print(f"[-] Error delegating to dusky_tui: {e}")
        sys.exit(1)
