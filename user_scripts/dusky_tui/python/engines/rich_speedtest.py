#!/usr/bin/env python3
"""
Rich UI Interactive Speed Test Runner for Dusky Network Manager.
Executes fast.com speed test via omarchy-network-speedtest for a crisp 10-second duration,
rendering a clean, unbordered live speed gauge, sparkline graph, and metrics.
"""

import sys
import os
import time
import subprocess
import shutil
import select
import termios
import tty
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.text import Text
from rich.table import Table
from rich.align import Align

console = Console()

SPARKLINE_BLOCKS = [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
PHASE_TIMEOUT_SECONDS = 10.0  # Automatic 10-second test duration

def make_sparkline(samples: list[float], width: int = 28) -> str:
    if not samples:
        return " " * width
    window = samples[-width:]
    max_val = max(window) or 1.0
    res = []
    for val in window:
        idx = int((val / max_val) * (len(SPARKLINE_BLOCKS) - 1))
        idx = max(0, min(len(SPARKLINE_BLOCKS) - 1, idx))
        res.append(SPARKLINE_BLOCKS[idx])
    return "".join(res).rjust(width)

def find_speedtest_script() -> str:
    candidates = [
        "/mnt/zram1/network/omarchy-network-speedtest",
        "/mnt/zram1/omarchy-quattro/bin/omarchy-network-speedtest",
    ]
    for c in candidates:
        if os.access(c, os.X_OK):
            return c
    found = shutil.which("omarchy-network-speedtest")
    if found:
        return found
    return "omarchy-network-speedtest"

def check_cancel_key() -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        r, _, _ = select.select([sys.stdin], [], [], 0.01)
        if r:
            ch = sys.stdin.read(1)
            if ch in ("q", "Q", "\x1b", "\x03", "\r", "\n", " "):
                return True
    except Exception:
        pass
    return False

def run_phase(direction: str, script_path: str, live: Live) -> tuple[float | None, bool]:
    label = "DOWNLOAD" if direction == "down" else "UPLOAD"
    icon = "⬇" if direction == "down" else "⬆"
    color = "cyan" if direction == "down" else "magenta"

    samples: list[float] = []
    peak: float = 0.0
    current: float = 0.0
    user_cancelled = False

    env = dict(os.environ)
    env["PATH"] = f"/mnt/zram1/network:/mnt/zram1/omarchy-quattro/bin:{env.get('PATH', '')}"

    try:
        proc = subprocess.Popen(
            [script_path, direction],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env
        )
    except Exception as e:
        console.print(f"[bold red]Failed to start speedtest process: {e}[/bold red]")
        return None, False

    start_time = time.time()

    while True:
        elapsed = time.time() - start_time

        # Check for user cancel keypress
        if check_cancel_key():
            user_cancelled = True
            proc.terminate()
            try: proc.wait(timeout=1)
            except Exception: proc.kill()
            break

        # Check for 10-second automatic timeout completion
        if elapsed >= PHASE_TIMEOUT_SECONDS:
            proc.terminate()
            try: proc.wait(timeout=1)
            except Exception: proc.kill()
            break

        line = proc.stdout.readline() if proc.stdout else ""
        if not line and proc.poll() is not None:
            break

        line_clean = line.strip()
        if line_clean:
            try:
                val = float(line_clean)
                current = val
                peak = max(peak, val)
                samples.append(val)
            except ValueError:
                pass

        avg = (sum(samples) / len(samples)) if samples else 0.0
        sparkline = make_sparkline(samples, width=28)

        scale_max = max(100.0, peak * 1.2)
        pct = min(1.0, current / scale_max)

        # Unbordered Grid Layout
        grid = Table.grid(expand=True)
        grid.add_column(justify="center")

        grid.add_row(Text(f"🚀 DUSKY {label} SPEED TEST", style=f"bold {color}"))
        grid.add_row(Text(""))

        speed_text = Text()
        speed_text.append(f"{current:.1f}", style=f"bold underline {color}")
        speed_text.append(" Mbps", style="bold white")
        grid.add_row(Align.center(speed_text))

        grid.add_row(Text(""))
        bar_text = Text()
        bar_text.append("Gauge: [", style="dim")
        bar_cells = int(pct * 30)
        bar_text.append("█" * bar_cells, style=f"bold {color}")
        bar_text.append("░" * (30 - bar_cells), style="dim")
        bar_text.append("]", style="dim")
        grid.add_row(Align.center(bar_text))

        grid.add_row(Text(""))
        spark_text = Text()
        spark_text.append("Live Graph: ", style="bold dim")
        spark_text.append(sparkline, style=f"bold {color}")
        grid.add_row(Align.center(spark_text))

        grid.add_row(Text(""))
        stats_table = Table(show_header=False, show_edge=False, box=None, padding=(0, 2))
        stats_table.add_column(style="dim", justify="right")
        stats_table.add_column(style="bold white", justify="left")
        stats_table.add_row("Peak Speed:", f"{peak:.1f} Mbps")
        stats_table.add_row("Average Speed:", f"{avg:.1f} Mbps")
        stats_table.add_row("Time Left:", f"{max(0.0, PHASE_TIMEOUT_SECONDS - elapsed):.1f}s")
        stats_table.add_row("Samples Gathered:", f"{len(samples)}")
        grid.add_row(Align.center(stats_table))

        grid.add_row(Text(""))
        grid.add_row(Text("Press [q] or [Esc] at any time to stop & return to Dusky TUI", style="bold dim yellow"))

        live.update(grid)

    if user_cancelled:
        return None, True

    final_val = (sum(samples[-5:]) / len(samples[-5:])) if len(samples) >= 5 else (samples[-1] if samples else 0.0)
    return round(final_val, 1), False

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    script = find_speedtest_script()

    down_res: float | None = None
    up_res: float | None = None
    was_cancelled = False

    old_settings = None
    if sys.stdin.isatty():
        try:
            old_settings = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
        except Exception:
            old_settings = None

    try:
        with Live(console=console, refresh_per_second=10) as live:
            if mode in ("full", "down"):
                down_res, was_cancelled = run_phase("down", script, live)
                if not was_cancelled:
                    time.sleep(0.3)

            if not was_cancelled and mode in ("full", "up"):
                up_res, was_cancelled = run_phase("up", script, live)
                if not was_cancelled:
                    time.sleep(0.3)

            # Final Summary
            summary_grid = Table.grid(expand=True)
            summary_grid.add_column(justify="center")

            if was_cancelled:
                summary_grid.add_row(Text("🛑 SPEED TEST CANCELLED BY USER", style="bold red"))
            else:
                summary_grid.add_row(Text("✨ DUSKY SPEED TEST COMPLETE ✨", style="bold green"))
            summary_grid.add_row(Text(""))

            summary_table = Table(show_header=True, header_style="bold yellow", show_edge=False, box=None)
            summary_table.add_column("Metric", justify="left", style="bold white")
            summary_table.add_column("Result", justify="right", style="bold cyan")

            if down_res is not None:
                summary_table.add_row("⬇ Download Speed", f"{down_res:.1f} Mbps")
            if up_res is not None:
                summary_table.add_row("⬆ Upload Speed", f"{up_res:.1f} Mbps")

            summary_grid.add_row(Align.center(summary_table))
            summary_grid.add_row(Text(""))
            summary_grid.add_row(Text("Returning to Dusky TUI...", style="dim italic"))

            live.update(summary_grid)
            time.sleep(0.8)

    finally:
        if old_settings and sys.stdin.isatty():
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            except Exception:
                pass

    # Save results to temporary result file for NetworkManagerEngine readback
    if down_res is not None or up_res is not None:
        res_file = Path.home() / ".cache" / "dusky_tui" / "speedtest_last.json"
        res_file.parent.mkdir(parents=True, exist_ok=True)
        import json
        with open(res_file, "w") as f:
            json.dump({"down": down_res, "up": up_res, "time": time.time()}, f)

if __name__ == "__main__":
    main()
