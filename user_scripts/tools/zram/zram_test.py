#!/usr/bin/env python3
# =============================================================================
# Elite Arch Linux ZRAM Forensic Stress-Tester
# Target: Arch Linux Cutting-Edge (Python 3.14+, systemd 260+)
# Scope: Live-monitors ZRAM compression metrics while dynamically inflating RAM.
# =============================================================================

import os
import sys
import subprocess
import select
import termios
import tty
import time
from pathlib import Path

# --- Dependency Check ---
try:
    from rich.live import Live
    from rich.layout import Layout
    from rich.panel import Panel
    from rich.table import Table
    from rich.align import Align
    from rich.text import Text
except ImportError:
    print("The 'rich' UI library is required.")
    print("Please install it: sudo pacman -S python-rich")
    sys.exit(1)

# --- Privilege Escalation ---
if os.geteuid() != 0:
    print("[INFO] Root privileges required to read ZRAM sysfs blocks. Escalating...")
    os.execvp("sudo", ["sudo", sys.executable, os.path.abspath(__file__)] + sys.argv[1:])

# --- Global Memory Hog ---
# We store the synthetic payloads here to prevent Python's garbage collector from freeing them.
HOG_MEMORY = []
CHUNK_SIZE_MB = 250

def generate_synthetic_chunk(size_mb: int) -> bytearray:
    """
    Generates a memory block that compresses to roughly 3:1 ratio, 
    mimicking real-world mixed data workloads (like browser caches).
    """
    # 33% incompressible random data, 67% highly compressible repeating strings
    random_part = os.urandom(1024 * 1024)
    static_part = b"ZRAM_STRESS_TEST_DATA_" * (1024 * 1024 * 2 // 22 + 1)
    base_1mb = random_part + static_part[:(1024 * 1024 * 2)]
    
    return bytearray(base_1mb) * size_mb

def inflate_memory():
    HOG_MEMORY.append(generate_synthetic_chunk(CHUNK_SIZE_MB))

def deflate_memory():
    if HOG_MEMORY:
        HOG_MEMORY.pop()

# --- System Metrics Parsing ---
def get_meminfo() -> dict:
    data = {}
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].strip(":")
                    val = int(parts[1]) * 1024  # Convert kB to Bytes
                    data[key] = val
    except Exception:
        pass
    return data

def get_zram_stats(dev="zram0") -> dict:
    stats = {
        "orig_size": 0,
        "compr_size": 0,
        "mem_used_total": 0,
        "mem_limit": 0,
    }
    try:
        mm_stat = Path(f"/sys/block/{dev}/mm_stat").read_text().split()
        if len(mm_stat) >= 4:
            stats["orig_size"] = int(mm_stat[0])
            stats["compr_size"] = int(mm_stat[1])
            stats["mem_used_total"] = int(mm_stat[2])
            stats["mem_limit"] = int(mm_stat[3])
    except Exception:
        pass
    return stats

def format_bytes(b: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024.0:
            return f"{b:.2f} {unit}"
        b /= 1024.0
    return f"{b:.2f} PB"

# --- UI Generation ---
def generate_layout() -> Layout:
    layout = Layout(name="root")
    layout.split(
        Layout(name="header", size=3),
        Layout(name="main", ratio=1),
        Layout(name="footer", size=3)
    )
    layout["main"].split_row(
        Layout(name="sys_mem"),
        Layout(name="zram_mem")
    )
    return layout

def update_ui(layout: Layout):
    meminfo = get_meminfo()
    zram = get_zram_stats("zram0")
    
    # --- System Memory Table ---
    sys_table = Table(expand=True, border_style="cyan")
    sys_table.add_column("Metric", style="bold white")
    sys_table.add_column("Value", justify="right", style="green")
    
    total_ram = meminfo.get("MemTotal", 1)
    used_ram = total_ram - meminfo.get("MemAvailable", 0)
    ram_percent = (used_ram / total_ram) * 100 if total_ram > 0 else 0
    
    sys_table.add_row("Total Physical RAM", format_bytes(total_ram))
    sys_table.add_row("Used RAM (Active)", f"{format_bytes(used_ram)} ({ram_percent:.1f}%)")
    sys_table.add_row("Available RAM", format_bytes(meminfo.get("MemAvailable", 0)))
    sys_table.add_row("Buffers / Cache", format_bytes(meminfo.get("Buffers", 0) + meminfo.get("Cached", 0)))
    sys_table.add_row("Total Swap Space", format_bytes(meminfo.get("SwapTotal", 0)))
    sys_table.add_row("Swap In Use", format_bytes(meminfo.get("SwapTotal", 0) - meminfo.get("SwapFree", 0)))
    
    layout["sys_mem"].update(Panel(sys_table, title="[bold cyan]System Memory State", border_style="cyan"))

    # --- ZRAM Table ---
    zram_table = Table(expand=True, border_style="magenta")
    zram_table.add_column("Metric", style="bold white")
    zram_table.add_column("Value", justify="right", style="yellow")
    
    orig = zram["orig_size"]
    compr = zram["compr_size"]
    ratio = (orig / compr) if compr > 0 else 0.0
    
    limit_str = format_bytes(zram['mem_limit']) if zram['mem_limit'] > 0 else "Unlimited"
    
    zram_table.add_row("Data Pushed to Swap", format_bytes(orig))
    zram_table.add_row("Compressed Size", format_bytes(compr))
    zram_table.add_row("Compression Ratio", f"{ratio:.2f}x")
    zram_table.add_row("Actual RAM Consumed", format_bytes(zram["mem_used_total"]))
    zram_table.add_row("Systemd Resident Limit", limit_str)
    
    layout["zram_mem"].update(Panel(zram_table, title="[bold magenta]/dev/zram0 Diagnostics", border_style="magenta"))

    # --- Header & Footer ---
    layout["header"].update(Panel(
        Align.center(Text("Platinum ZRAM Forensic Stress-Tester", style="bold white on blue")),
        style="blue"
    ))
    
    current_artificial_load = len(HOG_MEMORY) * CHUNK_SIZE_MB
    footer_text = Text.from_markup(
        f"Controls: [bold green][+][/bold green] Add {CHUNK_SIZE_MB}MB | "
        f"[bold red][-][/bold red] Free {CHUNK_SIZE_MB}MB | "
        f"[bold yellow][q][/bold yellow] Quit   ||   "
        f"Artificial Load: [bold cyan]{current_artificial_load} MB[/bold cyan]"
    )
    layout["footer"].update(Panel(Align.center(footer_text), border_style="white"))

# --- Main Loop & Keystroke Capture ---
def main():
    layout = generate_layout()
    
    # Setup non-blocking terminal input
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    
    try:
        tty.setcbreak(fd)
        with Live(layout, refresh_per_second=4, screen=True):
            while True:
                update_ui(layout)
                
                # Check for keyboard input without blocking the rich UI refresh
                if select.select([sys.stdin], [], [], 0.2)[0]:
                    key = sys.stdin.read(1).lower()
                    
                    if key == 'q':
                        break
                    elif key in ['+', '=']:
                        inflate_memory()
                    elif key in ['-', '_']:
                        deflate_memory()

    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        # Clean up memory explicitly
        HOG_MEMORY.clear()
        print("\n[OK] Diagnostics terminated. Artificial memory loads released successfully.")

if __name__ == "__main__":
    main()
