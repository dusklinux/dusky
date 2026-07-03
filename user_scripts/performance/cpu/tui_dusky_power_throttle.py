#!/usr/bin/env python3
"""
Dusky Power Throttle (v3.1 - Master)
CPU Package Power Limiter via RAPL
Arch Linux | Kernel 7.1+ | Intel/AMD
"""

import os
import sys
import time
import argparse
import json
import fcntl
import shutil
from pathlib import Path
from typing import Any

# ==========================================
# 1. Auto-Privilege & Dependencies
# ==========================================
if os.geteuid() != 0:
    print("\033[93m[!] Elevating to root privileges...\033[0m")
    sys.stdout.flush()
    os.execvp("sudo", ["sudo", sys.executable] + sys.argv)

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.align import Align
except ImportError:
    print("\033[93m[!] Missing 'rich' library. Auto-installing via pacman...\033[0m")
    import subprocess
    try:
        subprocess.run(["pacman", "-S", "--needed", "--noconfirm", "--quiet", "python-rich"], check=True)
    except subprocess.CalledProcessError:
        print("\033[91m[X] Failed to install dependencies. Please run: sudo pacman -S python-rich\033[0m")
        sys.exit(1)
    os.execvp(sys.executable, [sys.executable] + sys.argv)

console = Console()

# ==========================================
# 2. Hardware Telemetry & Core Logic
# ==========================================
RAPL_BASE = Path("/sys/class/powercap")
STATE_FILE = Path("/dev/shm/dusky_rapl_state.json")

def format_time(us: int) -> str:
    """Dynamically scales microseconds to the most readable metric."""
    if us >= 1_000_000: return f"{us / 1_000_000:.1f}s"
    if us >= 1_000: return f"{us / 1_000:.1f}ms"
    return f"{us}µs"

class FastEnergyReader:
    """Context Manager for zero-overhead, deterministic sysfs polling."""
    def __init__(self, path: Path):
        self.fd = os.open(path, os.O_RDONLY)
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
    
    def read(self) -> int | None:
        try:
            os.lseek(self.fd, 0, os.SEEK_SET)
            val = os.read(self.fd, 32).decode().strip()
            return int(val)
        except (OSError, ValueError):
            return None
            
    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass

def find_package_domain() -> Path | None:
    for d in sorted(RAPL_BASE.glob("*rapl*")):
        name_file = d / "name"
        if name_file.exists() and name_file.read_text().strip() == "package-0":
            if (d / "constraint_0_power_limit_uw").exists():
                return d.resolve()
    return None

def safe_read_int(p: Path) -> int | None:
    try:
        return int(p.read_text().strip())
    except (OSError, ValueError):
        return None

def safe_write(p: Path, val: int) -> bool:
    try:
        p.write_text(str(val))
        return True
    except OSError:
        return False

def get_power_info(domain: Path) -> dict[str, Any]:
    info: dict[str, Any] = {
        "energy_uj": safe_read_int(domain / "energy_uj"),
        "max_energy_range_uj": safe_read_int(domain / "max_energy_range_uj"),
        "enabled": safe_read_int(domain / "enabled"),
        "name": (domain / "name").read_text().strip() if (domain / "name").exists() else "unknown"
    }
    
    for f in domain.glob("constraint_*"):
        if f.is_file() and not f.name.endswith("_name"):
            info[f.name] = safe_read_int(f)
            
    for nf in domain.glob("constraint_*_name"):
        info[nf.name] = nf.read_text().strip()
        
    return info

# ==========================================
# 3. Throttle Management & State
# ==========================================
class PowerThrottle:
    def __init__(self):
        self.domain = find_package_domain()
        if not self.domain:
            console.print("[bold red][X] No RAPL package domain found. Power limiting unsupported on this hardware.[/bold red]")
            sys.exit(1)

    def _atomic_state_update(self, callback) -> None:
        STATE_FILE.touch(mode=0o644, exist_ok=True)
        with open(STATE_FILE, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                try:
                    f.seek(0)
                    data = json.load(f)
                except (json.JSONDecodeError, ValueError):
                    data = {"boot": self._capture_power_limits(), "modified": False}
                
                updated_data = callback(data)
                
                if updated_data is not None:
                    f.seek(0)
                    f.truncate()
                    json.dump(updated_data, f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _capture_power_limits(self) -> dict[str, int]:
        result = {}
        for c in ["constraint_0_power_limit_uw", "constraint_1_power_limit_uw", "constraint_2_power_limit_uw"]:
            val = safe_read_int(self.domain / c)
            if val is not None:
                result[c] = val
        return result

    def _persist_boot_values(self) -> None:
        def update_boot(data):
            if not data.get("boot"):
                data["boot"] = self._capture_power_limits()
            return data
        self._atomic_state_update(update_boot)

    def restore(self) -> None:
        def do_restore(data):
            boot = data.get("boot", {})
            for key, val in boot.items():
                safe_write(self.domain / key, val)
            console.print("[bold green][+] Power limits restored to original BIOS values.[/bold green]")
            return {"boot": boot, "modified": False}
        self._atomic_state_update(do_restore)

    def set_limit(self, pl1: int | None = None, pl2: int | None = None, pl4: int | None = None,
                  pl1_time: int | None = None, pl2_time: int | None = None, 
                  save_as_default: bool = False) -> dict[str, int]:
        self._persist_boot_values()
        result = {}
        
        operations = {
            "pl1": ("constraint_0_power_limit_uw", pl1),
            "pl2": ("constraint_1_power_limit_uw", pl2),
            "pl4": ("constraint_2_power_limit_uw", pl4),
            "pl1_time": ("constraint_0_time_window_us", pl1_time),
            "pl2_time": ("constraint_1_time_window_us", pl2_time),
        }

        for name, (sysfs_file, value) in operations.items():
            if value is not None:
                if safe_write(self.domain / sysfs_file, value):
                    result[f"{name}_set"] = value

        time.sleep(0.1) # Sync with kernel
        
        for name, sysfs_file in [("pl1", "constraint_0_power_limit_uw"), 
                                 ("pl2", "constraint_1_power_limit_uw"),
                                 ("pl4", "constraint_2_power_limit_uw")]:
            actual = safe_read_int(self.domain / sysfs_file)
            if actual is not None:
                result[f"{name}_actual"] = actual

        def flag_modified(data):
            data["modified"] = True
            if save_as_default:
                data["boot"] = self._capture_power_limits()
            return data
            
        self._atomic_state_update(flag_modified)
        return result

    def status(self) -> dict[str, Any]:
        info = get_power_info(self.domain)
        pkg_power = None
        max_energy = info.get("max_energy_range_uj", 0) or 0
        
        energy_file = self.domain / "energy_uj"
        if energy_file.exists():
            with FastEnergyReader(energy_file) as reader:
                e1 = reader.read()
                t1 = time.perf_counter()
                time.sleep(0.3)
                e2 = reader.read()
                t2 = time.perf_counter()
                
                if e1 is not None and e2 is not None and (t2 - t1) > 0:
                    if e2 < e1 and max_energy > 0:
                        e2 += max_energy 
                    pkg_power = (e2 - e1) / 1_000_000 / (t2 - t1)
            
        info["power_watts"] = pkg_power
        return info

    def monitor(self, interval: float = 1.0, count: int | None = None) -> None:
        energy_file = self.domain / "energy_uj"
        if not energy_file.exists():
            console.print("[bold red][X] Energy telemetry missing. Cannot monitor.[/bold red]")
            return
            
        max_energy = safe_read_int(self.domain / "max_energy_range_uj") or 0
        pl1_base = (safe_read_int(self.domain / "constraint_0_power_limit_uw") or 0) // 1_000_000
        pl2_base = (safe_read_int(self.domain / "constraint_1_power_limit_uw") or 0) // 1_000_000
        pl4_base = (safe_read_int(self.domain / "constraint_2_power_limit_uw") or 0) // 1_000_000
        dynamic_max = max(pl4_base, pl2_base * 1.2, pl1_base * 1.5, 100.0)

        console.print(f"[bold cyan]RAPL Power Monitor[/bold cyan] (Interval: {interval}s | Range: 0-{int(dynamic_max)}W | Ctrl+C to stop)")
        
        t_start = time.monotonic()
        n = 0
        
        try:
            with FastEnergyReader(energy_file) as reader:
                while count is None or n < count:
                    cols = shutil.get_terminal_size().columns
                    ts = time.monotonic() - t_start
                    
                    e1 = reader.read()
                    t1 = time.perf_counter()
                    time.sleep(interval)
                    e2 = reader.read()
                    t2 = time.perf_counter()
                    
                    p = None
                    if e1 is not None and e2 is not None and (t2 - t1) > 0:
                        if e2 < e1 and max_energy > 0:
                            e2 += max_energy
                        p = (e2 - e1) / 1_000_000 / (t2 - t1)

                    if p is None:
                        line = f"[{ts:7.1f}s]  Power: N/A"
                    else:
                        bar_w = max(10, cols - 45)
                        filled = max(0, min(bar_w, int((p / dynamic_max) * bar_w)))
                        bar = "█" * filled + "░" * (bar_w - filled)
                        
                        pl1_raw = safe_read_int(self.domain / "constraint_0_power_limit_uw")
                        pl1_current = pl1_raw // 1_000_000 if pl1_raw else 0
                        line = f"[{ts:7.1f}s]  {bar}  {p:6.1f} W  (limit: PL1={pl1_current}W)"
                    
                    sys.stdout.write(f"\r{line:<{cols}}")
                    sys.stdout.flush()
                    n += 1
        except KeyboardInterrupt:
            print() 

# ==========================================
# 4. TUI/CLI Display Routines
# ==========================================
def display_status(throttle: PowerThrottle) -> None:
    s = throttle.status()
    console.print(Align.center(Panel("[bold magenta]Dusky Power Throttle[/bold magenta]", border_style="cyan", expand=False)))
    
    pl1_w = (s.get("constraint_0_power_limit_uw") or 0) // 1_000_000
    pl2_w = (s.get("constraint_1_power_limit_uw") or 0) // 1_000_000
    pl4_w = (s.get("constraint_2_power_limit_uw") or 0) // 1_000_000
    
    pl1_time_str = format_time(s.get("constraint_0_time_window_us") or 0)
    pl2_time_str = format_time(s.get("constraint_1_time_window_us") or 0)
    
    power = s.get("power_watts")
    power_str = f"{power:.1f} W" if power is not None else "N/A"

    table = Table(show_header=False, expand=True, box=None)
    table.add_column("Property", style="bold cyan")
    table.add_column("Value", style="bold white")
    
    table.add_row("RAPL Domain", s.get('name', 'unknown'))
    table.add_row("Package Power", power_str)
    table.add_row("PL1 (Long-Term)", f"[yellow]{pl1_w} W[/yellow] [dim](Window: {pl1_time_str})[/dim]")
    table.add_row("PL2 (Short-Term)", f"[yellow]{pl2_w} W[/yellow] [dim](Window: {pl2_time_str})[/dim]")
    if pl4_w:
        table.add_row("PL4 (Peak Limit)", f"[yellow]{pl4_w} W[/yellow]")

    console.print(table)

# ==========================================
# 5. Argument Parsing & Main Execution
# ==========================================
def main() -> None:
    # Graceful fallback: Default to 'status' if run without arguments
    if len(sys.argv) == 1:
        sys.argv.append("status")

    throttle = PowerThrottle()

    parser = argparse.ArgumentParser(
        description="CPU Package Power Throttle via RAPL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show current power and limits")
    sub.add_parser("info", help="Show detailed raw RAPL sysfs dump")
    
    p_set = sub.add_parser("set", help="Set power limits")
    p_set.add_argument("--pl1", type=int, default=None, help="Long-term power limit (PL1) in watts")
    p_set.add_argument("--pl2", type=int, default=None, help="Short-term power limit (PL2) in watts")
    p_set.add_argument("--pl4", type=int, default=None, help="Peak transient power limit (PL4) in watts")
    p_set.add_argument("--pl1-time", type=float, default=None, help="PL1 averaging window in seconds (e.g. 80.0)")
    p_set.add_argument("--pl2-time", type=float, default=None, help="PL2 time window in seconds (e.g. 0.0024)")
    p_set.add_argument("--save", action="store_true", help="Save current values as new BIOS defaults")

    p_reset = sub.add_parser("reset", help="Restore original BIOS limits")
    p_reset.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    p_mon = sub.add_parser("monitor", help="Live power monitor")
    p_mon.add_argument("-i", "--interval", type=float, default=1.0, help="Sampling interval in seconds")
    p_mon.add_argument("-n", "--count", type=int, default=None, help="Number of samples to take")

    p_raw = sub.add_parser("raw", help="Output current status as JSON")
    p_raw.add_argument("--watch", action="store_true", help="Continuously output JSON lines")

    args = parser.parse_args()

    match args.command:
        case "status":
            display_status(throttle)

        case "info":
            s = throttle.status()
            table = Table(title="Raw Sysfs RAPL Output", show_header=True, header_style="bold magenta")
            table.add_column("Parameter", style="cyan")
            table.add_column("Value", style="green")
            
            for k, v in sorted(s.items()):
                if v is None:
                    display_v = "[dim]N/A[/dim]"
                elif k.endswith("_power_limit_uw") and isinstance(v, int):
                    display_v = f"{v} µW ({v // 1_000_000} W)"
                elif k.endswith("_time_window_us") and isinstance(v, int):
                    display_v = f"{v} µs ({format_time(v)})"
                elif k in ("energy_uj", "max_energy_range_uj") and isinstance(v, int):
                    display_v = f"{v} µJ"
                elif k == "power_watts":
                    display_v = f"{v:.1f} W"
                else:
                    display_v = str(v)
                table.add_row(k, display_v)
            console.print(table)

        case "set":
            if all(v is None for v in [args.pl1, args.pl2, args.pl4, args.pl1_time, args.pl2_time]):
                console.print("[bold red][X] Specify at least one bound: --pl1, --pl2, --pl4, --pl1-time, or --pl2-time[/bold red]")
                sys.exit(1)

            pl1_uw = args.pl1 * 1_000_000 if args.pl1 is not None else None
            pl2_uw = args.pl2 * 1_000_000 if args.pl2 is not None else None
            pl4_uw = args.pl4 * 1_000_000 if args.pl4 is not None else None
            
            pl1_time_us = int(args.pl1_time * 1_000_000) if args.pl1_time is not None else None
            pl2_time_us = int(args.pl2_time * 1_000_000) if args.pl2_time is not None else None

            if pl1_uw is not None and pl1_uw < 1_000_000:
                sys.exit("PL1 minimum is 1W")
            if pl2_uw is not None and pl2_uw < 1_000_000:
                sys.exit("PL2 minimum is 1W")
            if pl4_uw is not None and pl4_uw < 1_000_000:
                sys.exit("PL4 minimum is 1W")

            result = throttle.set_limit(pl1=pl1_uw, pl2=pl2_uw, pl4=pl4_uw, 
                                        pl1_time=pl1_time_us, pl2_time=pl2_time_us, 
                                        save_as_default=args.save)
            
            console.print("[bold green][+] Power limits applied successfully:[/bold green]")
            
            for param, label in [("pl1", "PL1 (Long-Term)"), ("pl2", "PL2 (Short-Term)"), ("pl4", "PL4 (Peak)")]:
                if f"{param}_set" in result:
                    actual = result.get(f"{param}_actual", result[f"{param}_set"]) // 1_000_000
                    target = result[f"{param}_set"] // 1_000_000
                    color = "green" if actual == target else "yellow"
                    console.print(f"    {label:<18}: [{color}]{target} W[/{color}]  (Actual Verified: {actual} W)")
                    
            if args.pl1_time is not None:
                console.print(f"    PL1 Time Window   : [green]{args.pl1_time}s[/green]")
            if args.pl2_time is not None:
                console.print(f"    PL2 Time Window   : [green]{args.pl2_time}s[/green]")
                
            if args.save:
                console.print("[bold yellow][i] Applied limits have been saved as the new boot default.[/bold yellow]")

        case "reset":
            if not args.force:
                console.print("[bold yellow][!] This will restore original BIOS package power limits.[/bold yellow]")
                try:
                    confirm = input("Continue? [y/N] ").strip().lower()
                    if confirm != "y":
                        sys.exit("Aborted.")
                except (EOFError, KeyboardInterrupt):
                    sys.exit("\nAborted.")
            throttle.restore()

        case "raw":
            s = throttle.status()
            s["constraint_0_power_limit_w"] = (s.get("constraint_0_power_limit_uw") or 0) // 1_000_000
            s["constraint_1_power_limit_w"] = (s.get("constraint_1_power_limit_uw") or 0) // 1_000_000
            s["constraint_2_power_limit_w"] = (s.get("constraint_2_power_limit_uw") or 0) // 1_000_000
            
            if args.watch:
                energy_file = throttle.domain / "energy_uj"
                if energy_file.exists():
                    max_energy = s.get("max_energy_range_uj", 0) or 0
                    try:
                        with FastEnergyReader(energy_file) as reader:
                            while True:
                                e1 = reader.read()
                                t1 = time.perf_counter()
                                time.sleep(args.interval)
                                e2 = reader.read()
                                t2 = time.perf_counter()
                                
                                p = None
                                if e1 is not None and e2 is not None and (t2 - t1) > 0:
                                    if e2 < e1 and max_energy > 0:
                                        e2 += max_energy
                                    p = (e2 - e1) / 1_000_000 / (t2 - t1)
                                    
                                pl1_raw = safe_read_int(throttle.domain / "constraint_0_power_limit_uw")
                                pl2_raw = safe_read_int(throttle.domain / "constraint_1_power_limit_uw")
                                pl4_raw = safe_read_int(throttle.domain / "constraint_2_power_limit_uw")
                                    
                                out = {
                                    "timestamp": time.time(), 
                                    "power_w": round(p, 2) if p is not None else None,
                                    "pl1_w": pl1_raw // 1_000_000 if pl1_raw else None,
                                    "pl2_w": pl2_raw // 1_000_000 if pl2_raw else None,
                                    "pl4_w": pl4_raw // 1_000_000 if pl4_raw else None
                                }
                                print(json.dumps(out))
                                sys.stdout.flush()
                    except KeyboardInterrupt:
                        pass
            else:
                print(json.dumps(s, indent=2, default=str))

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
