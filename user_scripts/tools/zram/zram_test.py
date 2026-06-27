#!/usr/bin/env python3
# =============================================================================
# Dusky RAM Analyzer
# Target: Arch Linux Cutting-Edge (Python 3.14+, systemd 260+)
# Scope: Interactive, mouse-driven TUI for ZRAM and memory forensic analysis.
# =============================================================================

import os
import sys
import subprocess
from pathlib import Path

# --- Dependency Check & Privilege Escalation ---
if os.geteuid() != 0:
    print("[INFO] Root privileges required to read ZRAM sysfs blocks. Escalating...")
    os.execvp("sudo", ["sudo", sys.executable, os.path.abspath(__file__)] + sys.argv[1:])

try:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Button, Static, Label
    from rich.table import Table
    from rich.panel import Panel
except ImportError:
    print("The 'textual' library is required for the interactive UI.")
    print("Please install it: sudo pacman -S python-textual")
    sys.exit(1)


# --- Global Memory Hog Logic ---
class MemoryHog:
    def __init__(self) -> None:
        self.hog_memory: list[bytearray] = []
        self.chunk_size_mb = 250

    def add(self) -> None:
        """Generates a 3:1 compressible synthetic memory block of EXACTLY 250MB."""
        one_mb = 1048576  # Exactly 1 MB in bytes
        random_size = one_mb // 3
        static_size = one_mb - random_size
        
        random_part = os.urandom(random_size)
        static_part = (b"DUSKY_ZRAM_" * (static_size // 11 + 1))[:static_size]
        
        # Base chunk is now mathematically guaranteed to be exactly 1,048,576 bytes
        base_1mb = random_part + static_part
        self.hog_memory.append(bytearray(base_1mb) * self.chunk_size_mb)

    def free(self) -> None:
        if self.hog_memory:
            self.hog_memory.pop()

    def clear(self) -> None:
        self.hog_memory.clear()

    @property
    def total_mb(self) -> int:
        return len(self.hog_memory) * self.chunk_size_mb


# --- Data Parsers ---
def format_bytes(b: float) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024.0:
            return f"{b:.2f} {unit}"
        b /= 1024.0
    return f"{b:.2f} PB"

def get_meminfo() -> dict[str, int]:
    data = {}
    try:
        lines = Path("/proc/meminfo").read_text().splitlines()
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                data[parts[0].strip(":")] = int(parts[1]) * 1024
    except Exception:
        pass
    return data

def get_swap_breakdown() -> dict[str, int]:
    stats = {"zram_total": 0, "zram_used": 0, "disk_total": 0, "disk_used": 0}
    try:
        lines = Path("/proc/swaps").read_text().splitlines()[1:]
        for line in lines:
            parts = line.split()
            if len(parts) >= 4:
                size, used = int(parts[2]) * 1024, int(parts[3]) * 1024
                if "zram" in parts[0]:
                    stats["zram_total"] += size
                    stats["zram_used"] += used
                else:
                    stats["disk_total"] += size
                    stats["disk_used"] += used
    except Exception:
        pass
    return stats

def get_zram_stats(dev: str = "zram0") -> dict[str, int]:
    stats = {"orig_size": 0, "compr_size": 0, "mem_used_total": 0, "mem_limit": 0}
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

def get_sysctl(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except Exception:
        return "N/A"


# --- UI Panel Builders (Rich inside Textual) ---
def build_sys_mem_panel() -> Panel:
    table = Table(expand=True, border_style="cyan", show_header=False)
    table.add_column("Metric", style="bold white")
    table.add_column("Value", justify="right", style="green")
    
    mem = get_meminfo()
    swp = get_swap_breakdown()
    total_ram = mem.get("MemTotal", 1)
    used_ram = total_ram - mem.get("MemAvailable", 0)
    
    table.add_row("Total Physical RAM", format_bytes(total_ram))
    table.add_row("Used RAM (Active)", f"{format_bytes(used_ram)} ({(used_ram/total_ram)*100:.1f}%)")
    table.add_row("Available RAM (Shock Absorber)", format_bytes(mem.get("MemAvailable", 0)))
    table.add_row("Buffers / Cache", format_bytes(mem.get("Buffers", 0) + mem.get("Cached", 0)))
    table.add_section()
    table.add_row("Total Swap Space", format_bytes(mem.get("SwapTotal", 0)))
    table.add_row("  ↳ [bold magenta]ZRAM Swap Used[/bold magenta]", format_bytes(swp["zram_used"]))
    table.add_row("  ↳ [bold red]Disk Swap Used[/bold red]", f"{format_bytes(swp['disk_used'])} (Spillover)")
    
    return Panel(table, title="[bold cyan]System Memory Topology", border_style="cyan")

def build_top_proc_panel() -> Panel:
    table = Table(expand=True, border_style="blue")
    table.add_column("PID", style="dim cyan")
    table.add_column("RAM %", justify="right", style="red")
    table.add_column("RSS (In RAM)", justify="right", style="yellow")
    table.add_column("VSZ (Allocated)", justify="right", style="magenta")
    table.add_column("Process", style="bold white")
    
    try:
        # Added VSZ to prove that total requested memory != resident memory when ZRAM swaps it out.
        out = subprocess.run(["ps", "-eo", "pid,%mem,rss,vsz,comm", "--sort=-%mem"], capture_output=True, text=True, check=True).stdout
        for line in out.strip().split("\n")[1:6]:
            parts = line.split(None, 4)
            if len(parts) == 5:
                pid, pmem, rss, vsz, comm = parts
                table.add_row(pid, f"{pmem}%", f"{int(rss) / 1024:.1f} MB", f"{int(vsz) / 1024:.1f} MB", comm[:20])
    except Exception as e:
        table.add_row("Error", "", "", "", f"Fetch failed: {e}")
        
    return Panel(table, title="[bold blue]Top 5 Memory-Intensive Processes", border_style="blue")

def build_zram_panel() -> Panel:
    table = Table(expand=True, border_style="magenta", show_header=False)
    table.add_column("Metric", style="bold white")
    table.add_column("Value", justify="right", style="yellow")
    
    zram = get_zram_stats("zram0")
    orig, compr = zram["orig_size"], zram["compr_size"]
    ratio = (orig / compr) if compr > 0 else 0.0
    limit = format_bytes(zram["mem_limit"]) if zram["mem_limit"] > 0 else "Unlimited"
    
    table.add_row("Data Pushed to Swap", format_bytes(orig))
    table.add_row("Compressed Size", format_bytes(compr))
    table.add_row("Compression Ratio", f"[bold green]{ratio:.2f}x[/bold green]")
    table.add_row("Actual RAM Consumed", format_bytes(zram["mem_used_total"]))
    table.add_row("Systemd Resident Limit", limit)
    
    return Panel(table, title="[bold magenta]/dev/zram0 Diagnostics", border_style="magenta")

def build_vm_panel() -> Panel:
    table = Table(expand=True, border_style="green", show_header=False)
    table.add_column("Kernel Parameter", style="bold white")
    table.add_column("Live Value", justify="right", style="green")
    
    table.add_row("vm.swappiness", get_sysctl("/proc/sys/vm/swappiness"))
    table.add_row("vm.watermark_scale_factor", get_sysctl("/proc/sys/vm/watermark_scale_factor"))
    table.add_row("vm.vfs_cache_pressure", get_sysctl("/proc/sys/vm/vfs_cache_pressure"))
    table.add_row("vm.page-cluster", get_sysctl("/proc/sys/vm/page-cluster"))
    
    # Live DAMON Reclaim Polling (Perfectly aligns panel to 5 rows)
    w_high = get_sysctl("/sys/module/damon_reclaim/parameters/wmarks_high")
    w_mid = get_sysctl("/sys/module/damon_reclaim/parameters/wmarks_mid")
    w_low = get_sysctl("/sys/module/damon_reclaim/parameters/wmarks_low")
    
    if w_high != "N/A":
        table.add_row("damon.wmarks (H/M/L)", f"{w_high} / {w_mid} / {w_low}")
    else:
        table.add_row("damon.wmarks", "N/A")
    
    return Panel(table, title="[bold green]Live Kernel VM Policies", border_style="green")


# --- Main Textual Application ---
class DuskyRAMAnalyzer(App):
    TITLE = "DUSKY RAM ANALYZER"
    
    CSS = """
    Screen {
        layout: vertical;
    }
    #custom_header {
        dock: top;
        width: 100%;
        background: #005f87; /* Clean, dark blue */
        color: white;
        text-style: bold;
        content-align: center middle;
        height: 1;
        padding: 0;
    }
    #main_container {
        layout: horizontal;
        height: 1fr;
        padding: 1 1 0 1;
    }
    .column {
        width: 1fr;
        height: 100%;
        padding: 0 1;
    }
    #controls {
        dock: bottom;
        layout: horizontal;
        align: center middle;
        width: 100%;
        height: 1;
        margin-bottom: 1;
        background: transparent; /* Removes the ugly gray block entirely */
    }
    
    /* Flat, elegant buttons. 
       Bypassing variants entirely to avoid the internal 'black box' rendering flaw. 
    */
    .flat-button {
        height: 1;
        border: none;
        padding: 0 2;
        margin: 0 1;
        min-width: 14;
        text-style: bold;
    }
    .flat-button:hover {
        text-style: reverse bold;
    }
    
    #btn_add { background: #2e8b57; color: white; }
    #btn_free { background: #d78700; color: black; }
    #btn_clear { background: #d70000; color: white; }
    #btn_quit { background: #005f87; color: white; }

    #load_tracker {
        content-align: center middle;
        margin-left: 3;
        height: 1;
        text-style: bold;
        color: #d7d7af;
        background: transparent;
    }
    """

    BINDINGS = [
        ("+", "add_load", "Add"),
        ("=", "add_load", "Add"),
        ("-", "free_load", "Free"),
        ("_", "free_load", "Free"),
        ("c", "clear_load", "Clear"),
        ("q", "quit", "Quit")
    ]

    def __init__(self) -> None:
        super().__init__()
        self.hog = MemoryHog()

    def compose(self) -> ComposeResult:
        # Ultra-minimalist 1-line header
        yield Label("DUSKY RAM ANALYZER", id="custom_header")
        
        with Horizontal(id="main_container"):
            # Left Column
            with Vertical(classes="column"):
                self.sys_widget = Static(build_sys_mem_panel())
                self.top_proc_widget = Static(build_top_proc_panel())
                yield self.sys_widget
                yield self.top_proc_widget
                
            # Right Column
            with Vertical(classes="column"):
                self.zram_widget = Static(build_zram_panel())
                self.vm_widget = Static(build_vm_panel())
                yield self.zram_widget
                yield self.vm_widget

        # Sleek, completely flat control bar floating perfectly in the center
        with Horizontal(id="controls"):
            yield Button("+ Add 250MB", id="btn_add", classes="flat-button")
            yield Button("- Free 250MB", id="btn_free", classes="flat-button")
            yield Button("c Clear All", id="btn_clear", classes="flat-button")
            yield Button("q Quit", id="btn_quit", classes="flat-button")
            self.load_label = Label("Artificial Load: 0 MB", id="load_tracker")
            yield self.load_label

    def on_mount(self) -> None:
        # Refresh the dashboards automatically every 1 second
        self.set_interval(1.0, self.update_dashboards)

    def update_dashboards(self) -> None:
        self.sys_widget.update(build_sys_mem_panel())
        self.top_proc_widget.update(build_top_proc_panel())
        self.zram_widget.update(build_zram_panel())
        self.vm_widget.update(build_vm_panel())
        self.load_label.update(f"Artificial Load: {self.hog.total_mb} MB")

    # --- Event Handlers (Mouse & Keyboard) ---
    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "btn_add":
                self.action_add_load()
            case "btn_free":
                self.action_free_load()
            case "btn_clear":
                self.action_clear_load()
            case "btn_quit":
                self.action_quit()

    def action_add_load(self) -> None:
        self.hog.add()
        self.update_dashboards()

    def action_free_load(self) -> None:
        self.hog.free()
        self.update_dashboards()

    def action_clear_load(self) -> None:
        self.hog.clear()
        self.update_dashboards()

    def action_quit(self) -> None:
        self.hog.clear()  # Guarantee memory release before death
        self.exit()


if __name__ == "__main__":
    app = DuskyRAMAnalyzer()
    app.run()
    print("\n[OK] Dusky RAM Analyzer terminated. Artificial memory loads released successfully.")
