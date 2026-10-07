#!/usr/bin/env python3
"""
Rich UI Interactive Speed Test Runner for Dusky Network Manager.
Executes live network speed measurement for a crisp 10-second duration,
rendering a clean, unbordered live speed gauge, sparkline graph, and metrics.
"""

import sys
lazy import urllib.request
lazy import json
import os
lazy import math
import time
lazy import subprocess
lazy import shutil
lazy import select
lazy import signal
lazy import termios
lazy import tty
lazy from pathlib import Path

from rich.console import Console
lazy from rich.live import Live
lazy from rich.text import Text
lazy from rich.table import Table
lazy from rich.align import Align

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
        str(Path.home() / ".local" / "bin" / "dusky-network-speedtest"),
        str(Path.home() / "user_scripts" / "network_manager" / "dusky-network-speedtest"),
        "/usr/local/bin/dusky-network-speedtest",
        "/usr/bin/dusky-network-speedtest",
    ]
    for c in candidates:
        if os.access(c, os.X_OK):
            return c
    found = shutil.which("dusky-network-speedtest")
    if found:
        return found
    return "dusky-network-speedtest"

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
    user_bin = str(Path.home() / ".local" / "bin")
    if user_bin not in env.get("PATH", ""):
        env["PATH"] = f"{user_bin}:{env.get('PATH', '')}"

    if not os.access(script_path, os.X_OK) and not shutil.which(script_path):
        return run_phase_native(direction, live)

    try:
        proc = subprocess.Popen(
            [script_path, direction],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
    except Exception:
        return run_phase_native(direction, live)

    start_time = time.monotonic()
    pending = bytearray()
    measurement_window_ended = False

    def stop_child() -> None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()

    try:
        while True:
            elapsed = time.monotonic() - start_time

            # Check for user cancel keypress
            if check_cancel_key():
                user_cancelled = True
                stop_child()
                break

            # Check for 10-second automatic timeout completion
            if elapsed >= PHASE_TIMEOUT_SECONDS:
                measurement_window_ended = True
                stop_child()
                break

            # Read available bytes; readline can wait indefinitely for a newline.
            if proc.stdout:
                try:
                    r, _, _ = select.select([proc.stdout], [], [], 0.1)
                    if not r:
                        if proc.poll() is not None:
                            break
                        continue
                    chunk = os.read(proc.stdout.fileno(), 4096)
                    if chunk:
                        pending.extend(chunk)
                    elif proc.poll() is not None:
                        break
                except OSError:
                    break
            else:
                break

            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n")
                pending = bytearray(rest)
                try:
                    val = float(line.strip())
                    if not math.isfinite(val) or val < 0:
                        continue
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

    finally:
        if proc.poll() is None:
            stop_child()
        if proc.stdout:
            proc.stdout.close()
    if user_cancelled:
        return None, True

    if pending.strip():
        try:
            value = float(pending.strip())
            if math.isfinite(value) and value >= 0:
                samples.append(value)
        except ValueError:
            pass
    completed = proc.returncode == 0 or (measurement_window_ended and proc.returncode in {-signal.SIGTERM, -signal.SIGKILL})
    if not completed or not samples:
        return None, False
    final_val = (sum(samples[-5:]) / len(samples[-5:])) if len(samples) >= 5 else samples[-1]
    return final_val, False

def run_phase_native(direction: str, live: Live) -> tuple[float | None, bool]:
    label = "DOWNLOAD" if direction == "down" else "UPLOAD"
    color = "cyan" if direction == "down" else "magenta"

    samples: list[float] = []
    peak: float = 0.0
    current: float = 0.0
    user_cancelled = False
    failed = False
    start_time = time.monotonic()
    response = None

    if direction == "down":
        try:
            url = "https://speed.cloudflare.com/__down?bytes=50000000"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            response = urllib.request.urlopen(req, timeout=5)
            downloaded = 0
            last_sample_time = time.monotonic()
            while True:
                elapsed = time.monotonic() - start_time
                if check_cancel_key():
                    user_cancelled = True
                    break
                if elapsed >= PHASE_TIMEOUT_SECONDS:
                    break

                chunk = response.read(65536)
                if not chunk:
                    break
                downloaded += len(chunk)

                now = time.monotonic()
                elapsed = max(now - start_time, 0.000001)
                if now - last_sample_time >= 0.15:
                    current = (downloaded * 8) / (elapsed * 1_000_000)
                    peak = max(peak, current)
                    samples.append(current)
                    last_sample_time = now

                    avg = (sum(samples) / len(samples)) if samples else 0.0
                    sparkline = make_sparkline(samples, width=28)
                    scale_max = max(100.0, peak * 1.2)
                    pct = min(1.0, current / scale_max)

                    grid = Table.grid(expand=True)
                    grid.add_column(justify="center")
                    grid.add_row(Text(f"󰓅 DUSKY {label} SPEED TEST", style=f"bold {color}"))
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

            if downloaded and not samples:
                samples.append((downloaded * 8) / (max(time.monotonic() - start_time, 0.000001) * 1_000_000))
        except Exception as e:
            failed = True
            console.print(Text(f"Native speed test error: {e}", style="bold red"))
        finally:
            if response is not None:
                response.close()

    else:
        try:
            url = "https://speed.cloudflare.com/__up"
            chunk_size = 500_000
            data_chunk = b"0" * chunk_size
            uploaded = 0
            while True:
                elapsed = time.monotonic() - start_time
                if check_cancel_key():
                    user_cancelled = True
                    break
                if elapsed >= PHASE_TIMEOUT_SECONDS:
                    break

                req = urllib.request.Request(
                    url, data=data_chunk,
                    headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/octet-stream"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=5):
                    pass
                uploaded += chunk_size

                now = time.monotonic()
                elapsed = max(now - start_time, 0.000001)
                current = (uploaded * 8) / (elapsed * 1_000_000)
                peak = max(peak, current)
                samples.append(current)

                avg = (sum(samples) / len(samples)) if samples else 0.0
                sparkline = make_sparkline(samples, width=28)
                scale_max = max(100.0, peak * 1.2)
                pct = min(1.0, current / scale_max)

                grid = Table.grid(expand=True)
                grid.add_column(justify="center")
                grid.add_row(Text(f"󰓅 DUSKY {label} SPEED TEST", style=f"bold {color}"))
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

        except Exception as e:
            failed = True
            console.print(Text(f"Native upload test error: {e}", style="bold red"))

    if user_cancelled:
        return None, True
    if failed or not samples:
        return None, False

    final_val = (sum(samples[-5:]) / len(samples[-5:])) if len(samples) >= 5 else samples[-1]
    return round(final_val, 1), False

def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    result_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.home() / ".cache" / "dusky_tui" / "speedtest_last.json"
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

    def cancel_on_signal(signum, frame):
        raise KeyboardInterrupt

    previous_termination_handler = signal.signal(signal.SIGTERM, cancel_on_signal)
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
                summary_grid.add_row(Text("✕ SPEED TEST CANCELLED BY USER", style="bold red"))
            elif (mode in ("full", "down") and down_res is None) or (mode in ("full", "up") and up_res is None):
                summary_grid.add_row(Text("✕ SPEED TEST FAILED OR INCOMPLETE", style="bold red"))
            else:
                summary_grid.add_row(Text("✓ DUSKY SPEED TEST COMPLETE", style="bold green"))
            summary_grid.add_row(Text(""))

            summary_table = Table(show_header=True, header_style="bold yellow", show_edge=False, box=None)
            summary_table.add_column("Metric", justify="left", style="bold white")
            summary_table.add_column("Result", justify="right", style="bold cyan")

            if down_res is not None:
                summary_table.add_row("↓ Download Speed", f"{down_res:.1f} Mbps")
            if up_res is not None:
                summary_table.add_row("↑ Upload Speed", f"{up_res:.1f} Mbps")

            summary_grid.add_row(Align.center(summary_table))
            summary_grid.add_row(Text(""))
            summary_grid.add_row(Text("Returning to Dusky TUI...", style="dim italic"))

            live.update(summary_grid)
            time.sleep(0.8)

    except KeyboardInterrupt:
        was_cancelled = True
    finally:
        signal.signal(signal.SIGTERM, previous_termination_handler)
        if old_settings and sys.stdin.isatty():
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            except Exception:
                pass

    expected = ("down", "up") if mode == "full" else (mode,)
    measured = {"down": down_res, "up": up_res}
    status = "cancelled" if was_cancelled else "complete" if all(measured.get(part) is not None for part in expected) else "failed"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    replacement = result_path.with_name(result_path.name + ".tmp")
    replacement.write_text(json.dumps({"down": down_res, "up": up_res, "status": status, "time": time.time()}), encoding="utf-8")
    replacement.replace(result_path)

if __name__ == "__main__":
    main()
