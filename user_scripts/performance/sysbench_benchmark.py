#!/usr/bin/env python3
"""
sysbench_benchmark.py

A Python 3.14 rewrite of the Sysbench Ultimate Dashboard.
Provides a comprehensive CPU, Memory, and Threads benchmark dashboard
supporting interactive TUI menus and non-interactive command line usage.
Handles online/offline core detection and temporary CPU governor optimization.
"""

from __future__ import annotations

import argparse
import contextlib
import glob
import os
import re
import shutil
import subprocess
import sys


# --- Colors ---
RED = "\033[0;31m"
GREEN = "\033[0;32m"
BLUE = "\033[0;34m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
BOLD = "\033[1m"
NC = "\033[0m"


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def get_cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", "r") as fh:
            for line in fh:
                if line.strip().lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    try:
        out = subprocess.run(["lscpu"], capture_output=True, text=True, check=True).stdout
        for line in out.splitlines():
            if line.strip().lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "Unknown CPU"


def get_online_cpus() -> list[int]:
    try:
        with open("/sys/devices/system/cpu/online", "r") as fh:
            content = fh.read().strip()
            cpus = []
            for part in content.split(","):
                if "-" in part:
                    start, end = part.split("-")
                    cpus.extend(range(int(start), int(end) + 1))
                else:
                    cpus.append(int(part))
            return sorted(cpus)
    except Exception:
        pass
    return list(range(os.cpu_count() or 1))


def check_deps() -> None:
    if shutil.which("sysbench"):
        return
    eprint(f"{RED}Error: Required command 'sysbench' is missing.{NC}")
    eprint(f"\n{YELLOW}Install missing dependencies:{NC}")
    eprint(f"  Arch/Manjaro:  {CYAN}sudo pacman -S sysbench{NC}")
    sys.exit(1)


def print_header(cpu_model: str, online_cores: list[int]) -> None:
    print(f"{CYAN}============================================================{NC}")
    print(f"{BOLD}        SYSBENCH ULTIMATE PYTHON DASHBOARD                  {NC}")
    print(f"{CYAN}============================================================{NC}")
    print(f"System: {BOLD}{cpu_model}{NC}")
    print(f"Online Logical Cores: {BOLD}{len(online_cores)}{NC} (IDs: {','.join(map(str, online_cores))})")
    print(f"{CYAN}------------------------------------------------------------{NC}")


def validate_cores(cores_str: str) -> bool:
    try:
        subprocess.run(["taskset", "-c", cores_str, "true"], capture_output=True, check=True)
        return True
    except Exception:
        return False


def calc_threads(cores_str: str) -> int:
    # Estimate thread count by calling taskset and parsing count
    # Handles complex formats like '0-3,5-7'
    count = 0
    list_parts = cores_str.split(",")
    for part in list_parts:
        if "-" in part:
            start, end = part.split("-")
            count += abs(int(end) - int(start)) + 1
        else:
            count += 1
    return max(count, 1)


@contextlib.contextmanager
def optimize_cpu_performance():
    """
    Temporarily set CPU scaling governors of online CPUs to performance and enable turbo.
    Restores original settings on exit.
    """
    sudo_pwd = "2345"
    original_governors = {}
    original_no_turbo = None
    no_turbo_path = "/sys/devices/system/cpu/intel_pstate/no_turbo"
    
    gov_files = glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor")
    
    for f in gov_files:
        try:
            with open(f, "r") as fh:
                original_governors[f] = fh.read().strip()
        except Exception:
            pass
            
    if os.path.exists(no_turbo_path):
        try:
            with open(no_turbo_path, "r") as fh:
                original_no_turbo = fh.read().strip()
        except Exception:
            pass

    def set_values(gov_val, turbo_val):
        py_cmds = ["import os", "import glob"]
        if gov_val:
            py_cmds.append(
                f"for f in glob.glob('/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor'):\n"
                f"    try: open(f, 'w').write('{gov_val}')\n"
                f"    except Exception: pass"
            )
        if turbo_val is not None:
            py_cmds.append(
                f"if os.path.exists('{no_turbo_path}'):\n"
                f"    try: open('{no_turbo_path}', 'w').write('{turbo_val}')\n"
                f"    except Exception: pass"
            )
        if not py_cmds:
            return
        script = "\n".join(py_cmds)
        cmd = ["sudo", "-S", "python3", "-c", script]
        try:
            subprocess.run(cmd, input=f"{sudo_pwd}\n", text=True, capture_output=True, check=True)
        except Exception as exc:
            eprint(f"Warning: Failed to optimize CPU performance: {exc}")

    print(f"\n{YELLOW}[CPU Opt] Setting scaling governors to 'performance' and enabling Turbo Boost...{NC}")
    set_values("performance", "0")
    
    try:
        yield
    finally:
        print(f"{YELLOW}[CPU Opt] Restoring original scaling governors and Turbo Boost settings...{NC}")
        py_restore = ["import os"]
        for f, gov in original_governors.items():
            py_restore.append(
                f"try: open({f!r}, 'w').write({gov!r})\n"
                f"except Exception: pass"
            )
        if original_no_turbo is not None:
            py_restore.append(
                f"if os.path.exists({no_turbo_path!r}):\n"
                f"    try: open({no_turbo_path!r}, 'w').write({original_no_turbo!r})\n"
                f"    except Exception: pass"
            )
        if py_restore:
            script = "\n".join(py_restore)
            cmd = ["sudo", "-S", "python3", "-c", script]
            try:
                subprocess.run(cmd, input=f"{sudo_pwd}\n", text=True, capture_output=True, check=True)
            except Exception as exc:
                eprint(f"Warning: Failed to restore CPU settings: {exc}")


def parse_latency_block(stdout: str) -> tuple[dict[str, str], str]:
    metrics = {"min": "N/A", "avg": "N/A", "max": "N/A", "95th": "N/A"}
    latency_sec = False
    unit = "ms"
    for line in stdout.splitlines():
        if "latency (ms):" in line.lower():
            latency_sec = True
            unit = "ms"
        elif "latency (us):" in line.lower():
            latency_sec = True
            unit = "us"
        elif "latency (sec):" in line.lower():
            latency_sec = True
            unit = "sec"
            
        if latency_sec:
            line_strip = line.strip().lower()
            if line_strip.startswith("min:"):
                metrics["min"] = line.split(":", 1)[1].strip()
            elif line_strip.startswith("avg:"):
                metrics["avg"] = line.split(":", 1)[1].strip()
            elif line_strip.startswith("max:"):
                metrics["max"] = line.split(":", 1)[1].strip()
            elif "95th percentile:" in line_strip:
                metrics["95th"] = line.split(":", 1)[1].strip()
            elif line_strip == "" and metrics["min"] != "N/A":
                break
    return metrics, unit


def run_sysbench_cmd(test_type: str, args_list: list[str], thread_count: int, run_time: int, cores: str | None = None) -> str:
    cmd = []
    if cores:
        cmd.extend(["taskset", "-c", cores])
        
    cmd.extend([
        "sysbench",
        test_type,
        f"--threads={thread_count}",
        f"--time={run_time}",
        "--events=0",
        "--report-interval=1",
        *args_list,
        "run"
    ])
    
    print(f"\n{BLUE}Executing benchmark:{NC} {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return proc.stdout


def run_cpu_test(thread_count: int, run_time: int, cores: str | None = None) -> None:
    print(f"\n{BOLD}CPU BENCHMARK{NC}")
    print("Calculating Prime numbers up to 50,000...")
    
    stdout = run_sysbench_cmd(
        "cpu",
        ["--cpu-max-prime=50000"],
        thread_count,
        run_time,
        cores
    )
    
    # Parse CPU speed
    events_per_sec = "N/A"
    total_events = "N/A"
    for line in stdout.splitlines():
        if "events per second:" in line:
            events_per_sec = line.split(":", 1)[1].strip()
        elif "total number of events:" in line:
            total_events = line.split(":", 1)[1].strip()
            
    lat, unit = parse_latency_block(stdout)
    
    print(f"\n{GREEN}--- CPU Benchmark Results ---{NC}")
    print(f"  CPU Speed (events/sec):  {BOLD}{events_per_sec}{NC}")
    print(f"  Total Events processed:   {total_events}")
    print(f"  Latency ({unit}):")
    print(f"    Min:                   {lat['min']}")
    print(f"    Avg:                   {lat['avg']}")
    print(f"    Max:                   {lat['max']}")
    print(f"    95th Percentile:       {lat['95th']}")
    print(f"{GREEN}-----------------------------{NC}")


def run_memory_test(thread_count: int, run_time: int, cores: str | None = None, mode: str = "seq_read", block_size: str | None = None, scope: str | None = None) -> None:
    print(f"\n{BOLD}MEMORY BENCHMARK{NC}")
    
    # Map friendly mode strings to sysbench params
    # Default parameters:
    # 1) seq_read (Large Blocks) -> read, seq, local, 64M
    # 2) rnd_read (Small Blocks) -> read, rnd, global, 4K
    # 3) seq_write (Large Blocks) -> write, seq, local, 64M
    
    if mode == "rnd_read":
        oper = "read"
        access_mode = "rnd"
        default_scope = "global"
        default_block = "4K"
    elif mode == "seq_write":
        oper = "write"
        access_mode = "seq"
        default_scope = "local"
        default_block = "64M"
    else: # seq_read
        oper = "read"
        access_mode = "seq"
        default_scope = "local"
        default_block = "64M"
        
    actual_block = block_size or default_block
    actual_scope = scope or default_scope
    
    print(f"Running Memory Test: {oper.upper()} | {access_mode.upper()} | Scope: {actual_scope} | Block: {actual_block}")
    
    stdout = run_sysbench_cmd(
        "memory",
        [
            f"--memory-block-size={actual_block}",
            f"--memory-access-mode={access_mode}",
            f"--memory-scope={actual_scope}",
            "--memory-total-size=500G",
            f"--memory-oper={oper}"
        ],
        thread_count,
        run_time,
        cores
    )
    
    # Parse throughput using generic parser
    transferred = "N/A"
    throughput = "N/A"
    for line in stdout.splitlines():
        match = re.search(r"([\d\.]+)\s+(\w+)\s+transferred\s+\(([\d\.]+)\s+([\w/]+)\)", line)
        if match:
            transferred = f"{match.group(1)} {match.group(2)}"
            throughput = f"{match.group(3)} {match.group(4)}"
            break
            
    lat, unit = parse_latency_block(stdout)
    
    print(f"\n{GREEN}--- Memory Benchmark Results ---{NC}")
    print(f"  Transfer Throughput:     {BOLD}{throughput}{NC}")
    print(f"  Total Memory Copied:     {transferred}")
    print(f"  Latency ({unit}):")
    print(f"    Min:                   {lat['min']}")
    print(f"    Avg:                   {lat['avg']}")
    print(f"    Max:                   {lat['max']}")
    print(f"    95th Percentile:       {lat['95th']}")
    print(f"{GREEN}--------------------------------{NC}")


def run_threads_test(thread_count: int, run_time: int, cores: str | None = None) -> None:
    print(f"\n{BOLD}THREADS (SCHEDULER) BENCHMARK{NC}")
    print("Testing kernel scheduler performance with thread contention...")
    
    stdout = run_sysbench_cmd(
        "threads",
        ["--thread-locks=1"],
        thread_count,
        run_time,
        cores
    )
    
    total_events = "N/A"
    for line in stdout.splitlines():
        if "total number of events:" in line:
            total_events = line.split(":", 1)[1].strip()
            
    lat, unit = parse_latency_block(stdout)
    
    print(f"\n{GREEN}--- Threads Benchmark Results ---{NC}")
    print(f"  Total Events processed:   {BOLD}{total_events}{NC}")
    print(f"  Latency ({unit}):")
    print(f"    Min:                   {lat['min']}")
    print(f"    Avg:                   {lat['avg']}")
    print(f"    Max:                   {lat['max']}")
    print(f"    95th Percentile:       {lat['95th']}")
    print(f"{GREEN}---------------------------------{NC}")


def prompt_cores(online_cores: list[int]) -> tuple[str | None, int]:
    while True:
        print(f"\n{YELLOW}--- Core Selection ---{NC}")
        print(f"1) {GREEN}All Cores{NC} (Default, active online cores)")
        print(f"2) {GREEN}Core 0 Only{NC} (P-Core test)")
        last_core = online_cores[-1]
        print(f"3) {GREEN}Last Core Only{NC} (Core {last_core} - E-Core test)")
        print(f"4) {GREEN}Custom Range{NC} (e.g., 0-3 or 0,2,4)")
        print(f"q) {RED}Cancel / Back{NC}")
        
        choice = input("Select option [1]: ").strip()
        if choice == "" or choice == "1":
            cores_str = ",".join(map(str, online_cores))
            return cores_str, len(online_cores)
        elif choice == "2":
            return "0", 1
        elif choice == "3":
            return str(last_core), 1
        elif choice == "4":
            custom = input("Enter core list (e.g., 0-3 or 0,2,4): ").strip()
            if not custom:
                print(f"{RED}Error: No cores specified.{NC}")
                continue
            if not validate_cores(custom):
                print(f"{RED}Error: Invalid core list or cores are offline/out of range.{NC}")
                continue
            return custom, calc_threads(custom)
        elif choice.lower() == "q":
            return None, 0
        else:
            print(f"{RED}Invalid option.{NC}")


def prompt_duration() -> int | None:
    while True:
        print(f"\n{YELLOW}--- Duration Selection ---{NC}")
        print(f"1) {GREEN}10 Seconds{NC} (Default)")
        print(f"2) {GREEN}1 Minute{NC} (Stability)")
        print(f"3) {GREEN}Custom Time{NC}")
        print(f"q) {RED}Cancel / Back{NC}")
        
        choice = input("Select option [1]: ").strip()
        if choice == "" or choice == "1":
            return 10
        elif choice == "2":
            return 60
        elif choice == "3":
            custom = input("Enter seconds (1-86400): ").strip()
            try:
                val = int(custom)
                if 1 <= val <= 86400:
                    return val
                print(f"{RED}Please enter an integer between 1 and 86400.{NC}")
            except ValueError:
                print(f"{RED}Invalid integer.{NC}")
        elif choice.lower() == "q":
            return None
        else:
            print(f"{RED}Invalid option.{NC}")


def interactive_menu(cpu_model: str, online_cores: list[int]) -> None:
    while True:
        print_header(cpu_model, online_cores)
        print("1) CPU Speedometer")
        print("2) RAM Speedometer (Bandwidth/Latency)")
        print("3) Scheduler Latency")
        print(f"q) {RED}Quit{NC}")
        print(f"{CYAN}------------------------------------------------------------{NC}")
        
        choice = input("Select: ").strip().lower()
        if choice == "q":
            print(f"{YELLOW}Exiting. Goodbye!{NC}")
            break
        elif choice == "":
            continue
        elif choice in {"1", "2", "3"}:
            cores, thread_count = prompt_cores(online_cores)
            if cores is None:
                continue
            run_time = prompt_duration()
            if run_time is None:
                continue
                
            opt_inp = input("Temporarily optimize CPU for performance? [y/N]: ").strip().lower()
            cpu_opt = optimize_cpu_performance() if opt_inp == "y" else contextlib.nullcontext()
            
            with cpu_opt:
                if choice == "1":
                    run_cpu_test(thread_count, run_time, cores)
                elif choice == "2":
                    print(f"\n{YELLOW}--- Memory Test Mode ---{NC}")
                    print("1) Sequential Read (64M blocks, Local scope - Max Bandwidth)")
                    print("2) Random Read (4K blocks, Global scope - Latency/IOPS)")
                    print("3) Sequential Write (64M blocks, Local scope)")
                    mem_choice = input("Select Mode [1]: ").strip()
                    if mem_choice == "2":
                        run_memory_test(thread_count, run_time, cores, "rnd_read")
                    elif mem_choice == "3":
                        run_memory_test(thread_count, run_time, cores, "seq_write")
                    else:
                        run_memory_test(thread_count, run_time, cores, "seq_read")
                elif choice == "3":
                    run_threads_test(thread_count, run_time, cores)
            
            input("\nPress Enter to return to main menu...")
        else:
            print(f"{RED}Invalid option.{NC}")
            input("\nPress Enter to continue...")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sysbench Ultimate Benchmark Dashboard."
    )
    parser.add_argument(
        "--test",
        choices=["cpu", "memory", "threads"],
        help="Run a specific benchmark non-interactively.",
    )
    parser.add_argument(
        "--time",
        type=int,
        default=10,
        help="Run time limit in seconds (default: 10).",
    )
    parser.add_argument(
        "--cores",
        help="Cores to pin the test to (e.g. 0-3, 0, or 19). Defaults to all online cores.",
    )
    parser.add_argument(
        "--performance",
        action="store_true",
        help="Temporarily optimize scaling governors and enable Turbo Boost during test.",
    )
    parser.add_argument(
        "--memory-mode",
        choices=["seq_read", "rnd_read", "seq_write"],
        default="seq_read",
        help="Memory benchmark mode (default: seq_read).",
    )
    parser.add_argument(
        "--memory-block-size",
        help="Custom memory block size (e.g. 4K, 64M).",
    )
    parser.add_argument(
        "--memory-scope",
        choices=["global", "local"],
        help="Custom memory scope.",
    )
    args = parser.parse_args()

    check_deps()
    
    cpu_model = get_cpu_model()
    online_cores = get_online_cpus()

    if args.test:
        # Non-interactive CLI
        cores = args.cores
        if cores:
            if not validate_cores(cores):
                eprint(f"{RED}Error: Invalid cores specification: {cores}{NC}")
                return 2
            thread_count = calc_threads(cores)
        else:
            cores = ",".join(map(str, online_cores))
            thread_count = len(online_cores)
            
        cpu_opt = optimize_cpu_performance() if args.performance else contextlib.nullcontext()
        
        with cpu_opt:
            if args.test == "cpu":
                run_cpu_test(thread_count, args.time, cores)
            elif args.test == "memory":
                run_memory_test(
                    thread_count,
                    args.time,
                    cores,
                    mode=args.memory_mode,
                    block_size=args.memory_block_size,
                    scope=args.memory_scope
                )
            elif args.test == "threads":
                run_threads_test(thread_count, args.time, cores)
    else:
        # Interactive UI
        interactive_menu(cpu_model, online_cores)
        
    return 0


if __name__ == "__main__":
    sys.exit(main())
