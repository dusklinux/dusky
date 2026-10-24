#!/usr/bin/env python3
"""
Battery History & Discharge Inspector
======================================
Comprehensive, high-resolution, hardware-independent battery telemetry inspector
for modern Linux (Hyprland / Wayland / CLI).

Compatible across all Linux hardware (ASUS, ThinkPad, Dell, HP, Framework, MacBook,
generic ACPI / SBS / PMIC). Queries rolling UPower history over D-Bus to analyze
daily charge dips, discharge speeds, wattage drain rates, and renders high-resolution
Braille trajectories and Rich summary tables.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Initialize console, respecting COLUMNS if provided in environment
console = Console(width=int(os.environ["COLUMNS"])) if "COLUMNS" in os.environ else Console()

# UPower Device State Constants
STATE_NAMES: dict[int, str] = {
    0: "Unknown",
    1: "Charging",
    2: "Discharging",
    3: "Empty",
    4: "Fully Charged",
    5: "Pending Charge",
    6: "Pending Discharge",
}

# Braille dot mapping for 2x4 sub-pixel cell
# (sub_x, sub_y) where sub_x in (0, 1) and sub_y in (0..3) from top to bottom
BRAILLE_MAP: dict[tuple[int, int], int] = {
    (0, 0): 0x01,
    (0, 1): 0x02,
    (0, 2): 0x04,
    (0, 3): 0x40,
    (1, 0): 0x08,
    (1, 1): 0x10,
    (1, 2): 0x20,
    (1, 3): 0x80,
}

# Unicode sparkline blocks
BLOCKS = "  ▂▃▄▅▆▇█"

# Hardened busctl command prefix targeting the system D-Bus bus with timeouts
BUSCTL_BASE: list[str] = [
    "busctl",
    "--system",
    "--no-pager",
    "--timeout=5s",
    "--json=short",
]


def run_busctl(args: list[str]) -> str:
    """Executes busctl with hardened timeouts and UTF-8 encoding."""
    cmd = BUSCTL_BASE + args
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=True)
        return res.stdout.strip()
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.strip() if exc.stderr else str(exc)
        raise RuntimeError(f"busctl query failed: {err}") from exc


def get_all_batteries() -> list[dict]:
    """
    Discovers all battery devices on the system using UPower D-Bus introspection.
    Hardware-independent: discovers BAT0, BAT1, BATT, CMB0, macsmc, or any custom ACPI name.
    """
    if not shutil.which("busctl"):
        console.print("[bold red]Error:[/bold red] 'busctl' not found. Ensure systemd is installed.")
        sys.exit(1)

    try:
        out = run_busctl(["call", "org.freedesktop.UPower", "/org/freedesktop/UPower", "org.freedesktop.UPower", "EnumerateDevices"])
        devs: list[str] = json.loads(out)["data"][0]
    except Exception as exc:
        console.print(f"[bold red]Failed to query UPower over D-Bus:[/bold red] {exc}")
        sys.exit(1)

    batteries: list[dict] = []
    for dev in devs:
        if "battery" in dev or "BAT" in dev:
            try:
                p_out = run_busctl(["call", "org.freedesktop.UPower", dev, "org.freedesktop.DBus.Properties", "GetAll", "s", "org.freedesktop.UPower.Device"])
                raw_props = json.loads(p_out)["data"][0]
                props = {k: v.get("data") for k, v in raw_props.items() if isinstance(v, dict)}

                # Device Type 2 is Battery in UPower specification
                dev_type = props.get("Type")
                if dev_type == 2:
                    batteries.append({
                        "path": dev,
                        "native": props.get("NativePath", dev.split("/")[-1]),
                        "vendor": props.get("Vendor", "Unknown"),
                        "model": props.get("Model", "Generic"),
                        "present": props.get("IsPresent", True),
                        "power_supply": props.get("PowerSupply", True),
                        "has_history": props.get("HasHistory", False),
                        "percentage": props.get("Percentage", 0.0),
                        "state": props.get("State", 0),
                        "props": props,
                    })
            except Exception:
                continue

    return batteries


def resolve_battery_device(batteries: list[dict], user_override: str | None) -> dict:
    """
    Resolves the target battery device across diverse hardware configurations
    (single battery, dual-battery ThinkPads, removable slices, etc.).
    """
    if not batteries:
        console.print("[bold red]Error:[/bold red] No battery devices detected on this system.")
        console.print("[dim]This tool requires a battery monitored by UPower (laptops, portable devices).[/dim]")
        sys.exit(1)

    if user_override:
        # Match by native path, partial name, or exact D-Bus object path
        needle = user_override.strip().lower()
        for b in batteries:
            if needle in b["native"].lower() or needle in b["path"].lower():
                return b
        console.print(f"[bold red]Error:[/bold red] Battery '{user_override}' not found.")
        console.print("Available batteries: " + ", ".join(b["native"] for b in batteries))
        sys.exit(1)

    # Filter batteries that are physically present in the system
    present_batteries = [b for b in batteries if b["present"]]
    if not present_batteries:
        # If none report present (rare firmware quirk), fallback to all
        present_batteries = batteries

    # If multiple batteries are present (e.g. ThinkPad dual batteries BAT0 + BAT1)
    if len(present_batteries) > 1:
        # Prefer the battery that is currently discharging or has active charge
        active_b = next((b for b in present_batteries if b["state"] == 2), None)
        if not active_b:
            active_b = next((b for b in present_batteries if b["has_history"]), present_batteries[0])
        return active_b

    return present_batteries[0]


def detect_charge_threshold(native_path: str, props: dict) -> int | None:
    """
    Detects hardware battery charge limit (threshold) across diverse hardware.
    Integrates with battery_limiter.sh, sysfs, and UPower.
    """
    # 1. Check UPower properties
    limit = props.get("ChargeEndThreshold")
    if isinstance(limit, (int, float)) and 0 < limit < 100:
        return int(limit)

    # 2. Check sysfs directly (ASUS, ThinkPad, Dell, Framework, System76, etc.)
    sysfs_candidates = [
        f"/sys/class/power_supply/{native_path}/charge_control_end_threshold",
        f"/sys/class/power_supply/{native_path}/stop_charge_thresh",
    ]
    for candidate in sysfs_candidates:
        if os.path.exists(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    val = f.read().strip()
                    if val.isdigit() and 0 < int(val) < 100:
                        return int(val)
            except Exception:
                pass

    # 3. Check user state configuration file (managed by battery_limiter.sh)
    state_file = Path.home() / ".config/dusky/settings/battery_limiter"
    if state_file.exists():
        try:
            val = state_file.read_text(encoding="utf-8").strip().rstrip("%")
            if val.isdigit() and 0 < int(val) < 100:
                return int(val)
        except Exception:
            pass

    return None


def query_upower_history(device: str, history_type: str, timespan_seconds: int) -> list[tuple[int, float, int]]:
    """Queries rolling history for charge or rate from UPower."""
    try:
        out = run_busctl(["call", "org.freedesktop.UPower", device, "org.freedesktop.UPower.Device", "GetHistory", "suu", history_type, str(timespan_seconds), "2000"])
        raw_list = json.loads(out)["data"][0]

        valid_records: list[tuple[int, float, int]] = []
        for r in raw_list:
            t = int(r[0])
            val = float(r[1])
            state = int(r[2])
            # Filter transient driver/initialization 0.0% Unknown states on wake/boot
            if val <= 0.0 and state == 0:
                continue
            valid_records.append((t, val, state))

        valid_records.sort(key=lambda x: x[0])
        return valid_records
    except Exception as exc:
        console.print(f"[bold red]Failed querying {history_type} history:[/bold red] {exc}")
        return []


def format_duration(seconds: float) -> str:
    """Formats seconds into concise human-readable duration."""
    mins = int(round(seconds / 60.0))
    hours = mins // 60
    rem_mins = mins % 60
    if hours > 0:
        return f"{hours}h {rem_mins:02d}m"
    return f"{rem_mins}m"


def extract_discharge_sessions(
    all_charge_pts: list[tuple[int, datetime, float, int]],
    all_rate_pts: list[tuple[int, datetime, float, int]],
) -> list[dict]:
    """Identifies individual continuous battery discharge sessions across the entire telemetry series."""
    sessions: list[dict] = []
    current_session: list[tuple[int, datetime, float, int]] = []

    for item in all_charge_pts:
        _, _, _, state = item
        if state == 2:  # Discharging
            current_session.append(item)
        else:
            if len(current_session) >= 2:
                start_t, start_dt, start_p, _ = current_session[0]
                end_t, end_dt, end_p, _ = current_session[-1]
                duration_sec = end_t - start_t
                if duration_sec >= 120 and start_p > end_p:
                    drop = start_p - end_p
                    drain_pct_per_hr = drop / (duration_sec / 3600.0)

                    # Correlate wattage rates recorded during this exact session window
                    session_rates = [
                        r for t, _, r, s_r in all_rate_pts
                        if start_t <= t <= end_t and s_r == 2 and r > 0
                    ]
                    avg_w = sum(session_rates) / len(session_rates) if session_rates else 0.0
                    peak_w = max(session_rates) if session_rates else 0.0

                    sessions.append({
                        "start_time": start_dt.strftime("%H:%M"),
                        "end_time": end_dt.strftime("%H:%M"),
                        "start_dt": start_dt,
                        "end_dt": end_dt,
                        "duration_sec": duration_sec,
                        "start_pct": start_p,
                        "end_pct": end_p,
                        "drop_pct": drop,
                        "speed_pct_hr": drain_pct_per_hr,
                        "avg_watts": avg_w,
                        "peak_watts": peak_w,
                    })
            current_session = []

    if len(current_session) >= 2:
        start_t, start_dt, start_p, _ = current_session[0]
        end_t, end_dt, end_p, _ = current_session[-1]
        duration_sec = end_t - start_t
        if duration_sec >= 120 and start_p > end_p:
            drop = start_p - end_p
            drain_pct_per_hr = drop / (duration_sec / 3600.0)
            session_rates = [
                r for t, _, r, s_r in all_rate_pts
                if start_t <= t <= end_t and s_r == 2 and r > 0
            ]
            avg_w = sum(session_rates) / len(session_rates) if session_rates else 0.0
            peak_w = max(session_rates) if session_rates else 0.0

            sessions.append({
                "start_time": start_dt.strftime("%H:%M"),
                "end_time": end_dt.strftime("%H:%M"),
                "start_dt": start_dt,
                "end_dt": end_dt,
                "duration_sec": duration_sec,
                "start_pct": start_p,
                "end_pct": end_p,
                "drop_pct": drop,
                "speed_pct_hr": drain_pct_per_hr,
                "avg_watts": avg_w,
                "peak_watts": peak_w,
            })

    return sessions


def render_metric_cards(
    props: dict,
    overall_min_rec: tuple[datetime, float] | None,
    fastest_session: dict | None,
    total_discharge_sec: float,
    session_count: int,
    plain_mode: bool = False,
):
    """Displays 4 summary metric highlight cards above the main table without emojis."""
    current_pct = props.get("Percentage", 0.0)
    state_code = props.get("State", 0)
    state_str = STATE_NAMES.get(state_code, "Unknown")
    capacity = props.get("Capacity", 0.0)
    energy_design = props.get("EnergyFullDesign", 0.0)
    rate = props.get("EnergyRate", 0.0)

    # Icon tokens (Nerd Font vs Plain ASCII)
    icon_low = "[LOW]" if plain_mode else "󰂎"
    icon_drain = "[DRAIN]" if plain_mode else "󱐋"
    icon_bat = "[BAT]" if plain_mode else "󰁹"
    icon_status = "[STAT]" if plain_mode else ("󰂄" if state_code == 1 else ("󰂎" if state_code == 2 else "󰚥"))

    # 1. Lowest Level Card
    if overall_min_rec:
        min_dt, min_val = overall_min_rec
        min_color = "bright_red" if min_val <= 20 else ("bright_yellow" if min_val <= 50 else "bright_green")
        card1 = f"[bold cyan]{icon_low} Lowest Point[/bold cyan]\n[{min_color}]{min_val:.1f}%[/{min_color}]  [dim]({min_dt.strftime('%a %b %d @ %H:%M')})[/dim]"
    else:
        card1 = f"[bold cyan]{icon_low} Lowest Point[/bold cyan]\n[dim]N/A[/dim]"

    # 2. Fastest Drain Card
    if fastest_session and fastest_session.get("speed_pct_hr", 0) > 0:
        speed = fastest_session["speed_pct_hr"]
        peak_w = fastest_session["peak_watts"]
        dt = fastest_session["start_dt"]
        w_str = f" ({peak_w:.1f}W)" if peak_w > 0 else ""
        card2 = f"[bold magenta]{icon_drain} Fastest Drain[/bold magenta]\n[bold bright_magenta]{speed:.1f}%/hr[/bold bright_magenta]{w_str}  [dim]({dt.strftime('%a %b %d')})[/dim]"
    else:
        card2 = f"[bold magenta]{icon_drain} Fastest Drain[/bold magenta]\n[dim]No discharge recorded[/dim]"

    # 3. Total Discharge Duration Card
    dur_str = format_duration(total_discharge_sec) if total_discharge_sec > 0 else "0m"
    card3 = f"[bold yellow]{icon_bat} On Battery[/bold yellow]\n[bold bright_yellow]{dur_str}[/bold bright_yellow]  [dim]({session_count} unplugged session{'s' if session_count != 1 else ''})[/dim]"

    # 4. Battery Health & Realtime Status
    status_col = "bright_green" if state_code in (1, 4) else ("bright_red" if state_code == 2 else "cyan")
    rate_str = f" ({rate:.1f}W)" if rate > 0 else ""

    # Hardware-independent health calculation: guard against missing design energy
    if energy_design > 0 and capacity > 0:
        cap_col = "bright_green" if capacity >= 80 else ("bright_yellow" if capacity >= 60 else "bright_red")
        health_str = f"Health: [{cap_col}]{capacity:.1f}%[/{cap_col}]"
    else:
        health_str = "Health: [dim]N/A[/dim]"

    card4 = f"[bold green]{icon_status} Current Status[/bold green]\n[{status_col}]{current_pct:.1f}% {state_str}[/{status_col}]{rate_str}  [dim]{health_str}[/dim]"

    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(card1, card2)
    grid.add_row("", "")
    grid.add_row(card3, card4)

    console.print(Panel(grid, title="[bold bright_cyan]Weekly Battery Telemetry Highlights[/bold bright_cyan]", border_style="bright_blue"))


def render_hardware_banner(
    props: dict,
    total_batteries: int,
    threshold: int | None,
    plain_mode: bool = False,
):
    """Displays hardware details footer, dual-battery alerts, and charge thresholds."""
    vendor = props.get("Vendor", "Generic")
    model = props.get("Model", "Unknown")
    native_path = props.get("NativePath", "BAT")
    energy_full = props.get("EnergyFull", 0.0)
    energy_design = props.get("EnergyFullDesign", 0.0)
    cycle_count = props.get("ChargeCycles", -1)

    icon_hw = "[HW]" if plain_mode else "󰌢"
    banner_items = [f"{icon_hw} Hardware: {vendor} {model} ({native_path})"]

    if energy_full > 0:
        if energy_design > 0:
            banner_items.append(f"Capacity: {energy_full:.1f} Wh / {energy_design:.1f} Wh")
        else:
            banner_items.append(f"Capacity: {energy_full:.1f} Wh")

    if cycle_count > 0:
        banner_items.append(f"Cycles: {cycle_count}")

    if threshold is not None:
        banner_items.append(f"[bold bright_green]Charge Limit: {threshold}% (Active)[/bold bright_green]")

    if total_batteries > 1:
        banner_items.append(f"[yellow]({total_batteries} batteries installed; use --device to switch)[/yellow]")

    console.print(Text.from_markup("  •  ".join(banner_items), style="dim white"))


def draw_bresenham_line(
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    max_w: int,
    max_h: int,
    pixels: dict[tuple[int, int], tuple[int, float]],
    state: int,
    pct: float,
):
    """Draws a continuous line of pixels into the pixel map using Bresenham's algorithm."""
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    curr_x, curr_y = x0, y0
    while True:
        if 0 <= curr_x < max_w and 0 <= curr_y < max_h:
            pixels[(curr_x, curr_y)] = (state, pct)
        if curr_x == x1 and curr_y == y1:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            curr_x += sx
        if e2 < dx:
            err += dx
            curr_y += sy


def render_braille_curve(
    records: list[tuple[int, datetime, float, int]],
    title_text: str,
    date_labels: list[tuple[float, str]],
    is_multi_day: bool = False,
    plain_mode: bool = False,
):
    """Renders a continuous, high-resolution Braille curve with exact 0-100% axis."""
    if not records:
        return

    available_width = max(50, min(120, console.width - 12))
    CELL_COLS = available_width
    CELL_ROWS = 10
    PIXEL_W = CELL_COLS * 2
    PIXEL_H = CELL_ROWS * 4

    pixels: dict[tuple[int, int], tuple[int, float]] = {}

    if is_multi_day:
        min_t = records[0][0]
        max_t = records[-1][0]
        span_t = max(1, max_t - min_t)

        prev_pt = None
        prev_t = None
        for t, _, p, s in records:
            px = int(((t - min_t) / span_t) * (PIXEL_W - 1))
            py = min(PIXEL_H - 1, max(0, int((p / 100.0) * (PIXEL_H - 1))))
            if prev_pt is not None:
                gap = t - prev_t
                if gap <= 6 * 3600:
                    draw_bresenham_line(prev_pt[0], prev_pt[1], px, py, PIXEL_W, PIXEL_H, pixels, s, p)
                else:
                    pixels[(px, py)] = (s, p)
            else:
                pixels[(px, py)] = (s, p)
            prev_pt = (px, py)
            prev_t = t
    else:
        prev_pt = None
        prev_dt = None
        for _, dt, p, s in records:
            sec = dt.hour * 3600 + dt.minute * 60 + dt.second
            px = int((sec / 86400.0) * (PIXEL_W - 1))
            py = min(PIXEL_H - 1, max(0, int((p / 100.0) * (PIXEL_H - 1))))
            if prev_pt is not None:
                gap = (dt - prev_dt).total_seconds()
                if gap <= 3 * 3600:
                    draw_bresenham_line(prev_pt[0], prev_pt[1], px, py, PIXEL_W, PIXEL_H, pixels, s, p)
                else:
                    pixels[(px, py)] = (s, p)
            else:
                pixels[(px, py)] = (s, p)
            prev_pt = (px, py)
            prev_dt = dt

    lines = []
    for cell_r in range(CELL_ROWS - 1, -1, -1):
        pct_val = int((cell_r + 1) * 10)
        label = f"{pct_val:3d}% ┤ " if pct_val % 20 == 0 or cell_r == CELL_ROWS - 1 else "     │ "
        line = Text(label, style="dim cyan")

        for cell_c in range(CELL_COLS):
            code = 0
            min_y = cell_r * 4
            cell_states = []
            cell_pcts = []
            for sub_x in (0, 1):
                px = cell_c * 2 + sub_x
                for sub_y in range(4):
                    py = min_y + (3 - sub_y)
                    if (px, py) in pixels:
                        code |= BRAILLE_MAP[(sub_x, sub_y)]
                        s_val, p_val = pixels[(px, py)]
                        cell_states.append(s_val)
                        cell_pcts.append(p_val)

            if plain_mode:
                # ASCII mode fallback for virtual console / plain environments
                if code:
                    ch = "^" if 1 in cell_states else ("v" if 2 in cell_states else ".")
                else:
                    ch = " "
            else:
                ch = chr(0x2800 + code) if code else " "

            if ch != " ":
                avg_p = sum(cell_pcts) / len(cell_pcts) if cell_pcts else 50.0
                if 1 in cell_states:
                    col = "bright_green"
                elif 2 in cell_states:
                    col = "bright_red" if avg_p <= 20 else ("bright_yellow" if avg_p <= 50 else "bright_magenta")
                else:
                    col = "bright_blue"
                line.append(ch, style=f"bold {col}")
            else:
                line.append(" ")
        lines.append(line)

    axis = Text("  0% └─" + "─" * CELL_COLS, style="dim cyan")

    # Time axis footer
    axis_str = [" "] * CELL_COLS
    for pos_pct, label in date_labels:
        col_idx = min(CELL_COLS - len(label), max(0, int(pos_pct * (CELL_COLS - 1))))
        for k, ch in enumerate(label):
            if col_idx + k < CELL_COLS:
                axis_str[col_idx + k] = ch

    footer = Text("       " + "".join(axis_str), style="bold bright_white")

    legend = (
        "([bright_green]Charge[/bright_green]  [bright_magenta]Drain[/bright_magenta]  [bright_red]Low[/bright_red]  [bright_blue]AC/Full[/bright_blue])"
        if plain_mode
        else "([bright_green]󰂄 Charge[/bright_green]  [bright_magenta]󰂎 Drain[/bright_magenta]  [bright_red]󱐋 Low[/bright_red]  [bright_blue]󰚥 AC/Full[/bright_blue])"
    )

    panel_body = Text("\n").join(lines + [axis, footer])
    console.print(
        Panel(
            panel_body,
            title=f"[bold bright_cyan]{title_text}[/bold bright_cyan]  {legend}",
            border_style="bright_blue",
        )
    )


def render_single_day_graph(
    day_records: list[tuple[int, datetime, float, int]],
    date_label: str,
    plain_mode: bool = False,
):
    """Helper to render a 24-hour day graph."""
    labels = [
        (0.00, "00:00"),
        (0.166, "04:00"),
        (0.333, "08:00"),
        (0.50, "12:00"),
        (0.666, "16:00"),
        (0.833, "20:00"),
        (0.98, "23:59"),
    ]
    render_braille_curve(
        day_records,
        f"24-Hour Battery Trajectory ({date_label})",
        labels,
        is_multi_day=False,
        plain_mode=plain_mode,
    )


def render_multi_day_graph(
    all_records: list[tuple[int, datetime, float, int]],
    plain_mode: bool = False,
):
    """Helper to render a full week / multi-day continuous trajectory."""
    if not all_records:
        return
    min_t = all_records[0][0]
    max_t = all_records[-1][0]
    span = max(1, max_t - min_t)

    labels = []
    num_markers = 5
    for i in range(num_markers + 1):
        frac = i / num_markers
        marker_t = min_t + frac * span
        dt = datetime.fromtimestamp(marker_t)
        labels.append((frac, dt.strftime("%b %d")))

    start_str = datetime.fromtimestamp(min_t).strftime("%Y-%m-%d")
    end_str = datetime.fromtimestamp(max_t).strftime("%Y-%m-%d")
    render_braille_curve(
        all_records,
        f"Continuous Trajectory ({start_str} to {end_str})",
        labels,
        is_multi_day=True,
        plain_mode=plain_mode,
    )


def display_battery_list(batteries: list[dict]):
    """Displays a summary table of all detected batteries on the device."""
    table = Table(
        title="󰁹 Detected System Batteries",
        header_style="bold bright_cyan",
        border_style="bright_blue",
    )
    table.add_column("Native Name", style="bold white")
    table.add_column("Vendor / Model", style="cyan")
    table.add_column("Present", justify="center")
    table.add_column("Status", justify="right")
    table.add_column("D-Bus Object Path", style="dim")

    for b in batteries:
        pres_str = "[green]Yes[/green]" if b["present"] else "[red]No[/red]"
        state_str = STATE_NAMES.get(b["state"], "Unknown")
        pct_str = f"{b['percentage']:.1f}% [{state_str}]"
        table.add_row(
            b["native"],
            f"{b['vendor']} {b['model']}",
            pres_str,
            pct_str,
            b["path"],
        )

    console.print(table)


def main():
    global console
    parser = argparse.ArgumentParser(
        description="Inspect battery history, daily dips, drain speeds, and trajectories across diverse Linux hardware."
    )
    parser.add_argument("--days", type=int, default=7, help="Number of past days to inspect (default: 7)")
    parser.add_argument("--wide", action="store_true", help="Force wide display mode showing 24h sparklines and drain rate")
    parser.add_argument(
        "--graph",
        nargs="?",
        const="week",
        default=None,
        help="Render graphical curve: 'week' (full multi-day), 'today', 'all', or specific date (YYYY-MM-DD)",
    )
    parser.add_argument("--sessions", action="store_true", help="Show granular table of individual discharge sessions")
    parser.add_argument("--device", type=str, default=None, help="Select battery device by name (e.g. BAT0, BAT1) or D-Bus path")
    parser.add_argument("--list-devices", action="store_true", help="List all detected battery devices and exit")
    parser.add_argument("--plain", action="store_true", help="Force plain ASCII mode (no Nerd Fonts or Braille)")
    parser.add_argument("--width", type=int, default=None, help="Explicit console width override")
    args = parser.parse_args()

    if args.days < 1:
        console.print("[bold red]Error:[/bold red] --days must be at least 1.")
        sys.exit(1)

    # Auto-detect Linux console virtual terminal without UTF-8 Nerd Fonts
    plain_mode = args.plain or os.environ.get("TERM") == "linux"

    if args.width:
        console = Console(width=args.width, height=40)
    elif args.wide:
        console = Console(width=max(120, console.width), height=40)

    # 1. Hardware-Independent Device Discovery
    all_batteries = get_all_batteries()
    if args.list_devices:
        display_battery_list(all_batteries)
        return

    battery_info = resolve_battery_device(all_batteries, args.device)
    device = battery_info["path"]
    props = battery_info["props"]
    native_path = battery_info["native"]

    # 2. Hardware Charge Limit Detection
    threshold = detect_charge_threshold(native_path, props)

    # 3. Query History
    timespan_seconds = args.days * 86400
    raw_charge = query_upower_history(device, "charge", timespan_seconds)
    raw_rate = query_upower_history(device, "rate", timespan_seconds)

    if not raw_charge:
        console.print(f"[yellow]No battery history records found in UPower for {native_path} in the requested period.[/yellow]")
        return

    # Parse and organize
    all_charge_pts: list[tuple[int, datetime, float, int]] = []
    days_charge: dict[date, list[tuple[int, datetime, float, int]]] = defaultdict(list)
    days_rate: dict[date, list[tuple[int, datetime, float, int]]] = defaultdict(list)

    for t, pct, state in raw_charge:
        dt = datetime.fromtimestamp(t)
        record = (t, dt, pct, state)
        days_charge[dt.date()].append(record)
        all_charge_pts.append(record)

    all_rate_pts: list[tuple[int, datetime, float, int]] = []
    for t, rate, state in raw_rate:
        dt = datetime.fromtimestamp(t)
        record = (t, dt, rate, state)
        days_rate[dt.date()].append(record)
        all_rate_pts.append(record)

    all_charge_pts.sort(key=lambda x: x[0])
    all_rate_pts.sort(key=lambda x: x[0])

    # 4. Global Discharge Session Extraction
    all_sessions = extract_discharge_sessions(all_charge_pts, all_rate_pts)
    total_discharge_sec = sum(s["duration_sec"] for s in all_sessions)
    fastest_session = max(all_sessions, key=lambda s: s["speed_pct_hr"], default=None)

    # Find overall minimum charge
    overall_min_rec: tuple[datetime, float] | None = None
    for _, dt, pct, _ in all_charge_pts:
        if overall_min_rec is None or pct < overall_min_rec[1]:
            overall_min_rec = (dt, pct)

    # Map sessions to days
    sessions_by_day: dict[date, list[dict]] = defaultdict(list)
    for s in all_sessions:
        sessions_by_day[s["start_dt"].date()].append(s)

    # 5. Render Metric Summary Cards
    render_metric_cards(
        props,
        overall_min_rec,
        fastest_session,
        total_discharge_sec,
        len(all_sessions),
        plain_mode=plain_mode,
    )

    # 6. Render Daily Summary Table
    is_wide = args.wide or console.width >= 105
    icon_cal = "[CAL]" if plain_mode else "󰃭"

    table = Table(
        title=f"{icon_cal} Daily Battery History & Discharge Summary (Past {args.days} Days)",
        header_style="bold bright_cyan",
        border_style="bright_blue",
    )
    table.add_column("Date", style="bold white", no_wrap=True)
    table.add_column("Day", style="dim", no_wrap=True)
    table.add_column("Lowest % (Time)", justify="right", style="bold", no_wrap=True)
    table.add_column("Peak % (Time)", justify="right", style="green", no_wrap=True)
    table.add_column("Discharge", justify="right", style="cyan", no_wrap=True)
    table.add_column("Max Drain", justify="right", style="bold magenta", no_wrap=True)
    if is_wide:
        table.add_column("Fastest Rate", justify="right", style="yellow", no_wrap=True)
        table.add_column("24h Activity Profile", style="bold", no_wrap=True)

    sorted_dates = sorted(days_charge.keys())
    for d in sorted_dates:
        c_records = sorted(days_charge[d], key=lambda x: x[0])
        r_records = sorted(days_rate[d], key=lambda x: x[0])

        pcts = [p for _, _, p, _ in c_records]
        if not pcts:
            continue

        min_record = min(c_records, key=lambda x: x[2])
        max_record = max(c_records, key=lambda x: x[2])
        min_pct = min_record[2]
        max_pct = max_record[2]
        min_time_str = min_record[1].strftime("%H:%M")
        max_time_str = max_record[1].strftime("%H:%M")

        # Discharge rates in Watts (if supported by hardware)
        drain_rates = [r for _, _, r, s in r_records if s == 2 and r > 0]
        max_drain_w = max(drain_rates) if drain_rates else 0.0

        day_sessions = sessions_by_day.get(d, [])
        day_discharge_sec = sum(s["duration_sec"] for s in day_sessions)
        day_fastest_pct_hr = max((s["speed_pct_hr"] for s in day_sessions), default=0.0)

        # 24h mini sparkline profile
        hourly_pct = [None] * 24
        for _, dt, p, _ in c_records:
            hourly_pct[dt.hour] = p

        sparkline = []
        for h in range(24):
            if hourly_pct[h] is not None:
                val = hourly_pct[h]
                idx = int((val / 100.0) * (len(BLOCKS) - 1))
                char = BLOCKS[idx]
                color = "bright_red" if val <= 20 else ("bright_yellow" if val <= 50 else "bright_green")
                sparkline.append(f"[{color}]{char}[/{color}]")
            else:
                sparkline.append("[dim]·[/dim]")

        min_color = "bright_red" if min_pct <= 20 else ("bright_yellow" if min_pct <= 50 else "bright_green")
        row_cells = [
            d.strftime("%Y-%m-%d"),
            d.strftime("%a"),
            f"[{min_color}]{min_pct:5.1f}% ({min_time_str})[/{min_color}]",
            f"{max_pct:5.1f}% ({max_time_str})",
            format_duration(day_discharge_sec) if day_discharge_sec > 0 else "0m (On AC)",
            f"{max_drain_w:5.1f} W" if max_drain_w > 0 else "-",
        ]
        if is_wide:
            row_cells.append(f"{day_fastest_pct_hr:5.1f}%/h" if day_fastest_pct_hr > 0 else "-")
            row_cells.append("".join(sparkline))

        table.add_row(*row_cells)

    console.print(table)
    render_hardware_banner(
        props,
        total_batteries=len(all_batteries),
        threshold=threshold,
        plain_mode=plain_mode,
    )

    # 7. Granular Discharge Sessions Table (--sessions)
    if args.sessions:
        icon_sess = "[SESS]" if plain_mode else "󰅐"
        session_table = Table(
            title=f"{icon_sess} Granular Battery Discharge Sessions",
            header_style="bold bright_magenta",
            border_style="magenta",
            expand=False,
        )
        session_table.add_column("Date", style="bold white", no_wrap=True)
        session_table.add_column("Window", style="cyan", no_wrap=True)
        session_table.add_column("Dur", justify="right", style="white", no_wrap=True)
        session_table.add_column("Drop", justify="right", style="yellow", no_wrap=True)
        session_table.add_column("Speed", justify="right", style="bold yellow", no_wrap=True)
        session_table.add_column("Avg W", justify="right", style="magenta", no_wrap=True)
        session_table.add_column("Peak W", justify="right", style="bold magenta", no_wrap=True)

        for s in all_sessions:
            date_label = s["start_dt"].strftime("%Y-%m-%d" if is_wide else "%m-%d")
            arrow = "->" if plain_mode else "→"
            drop_str = (
                f"{s['start_pct']:.0f}% {arrow} {s['end_pct']:.0f}% (-{s['drop_pct']:.0f}%)"
                if is_wide
                else f"{s['start_pct']:.0f}%{arrow}{s['end_pct']:.0f}% (-{s['drop_pct']:.0f}%)"
            )
            session_table.add_row(
                date_label,
                f"{s['start_time']}-{s['end_time']}",
                format_duration(s["duration_sec"]),
                drop_str,
                f"{s['speed_pct_hr']:.1f}%/h",
                f"{s['avg_watts']:.1f} W" if s["avg_watts"] > 0 else "-",
                f"{s['peak_watts']:.1f} W" if s["peak_watts"] > 0 else "-",
            )

        if all_sessions:
            console.print(session_table)
        else:
            console.print("[dim]No significant discharge sessions found in this period.[/dim]")

    # 8. High-Resolution Graphical Curves (--graph)
    if args.graph:
        graph_arg = args.graph.lower()
        if graph_arg == "week":
            render_multi_day_graph(all_charge_pts, plain_mode=plain_mode)
        elif graph_arg == "all":
            for d in sorted_dates:
                c_records = sorted(days_charge[d], key=lambda x: x[0])
                render_single_day_graph(c_records, f"{d.strftime('%Y-%m-%d (%a)')}", plain_mode=plain_mode)
        elif graph_arg == "today":
            today_date = date.today()
            if today_date in days_charge:
                c_records = sorted(days_charge[today_date], key=lambda x: x[0])
                render_single_day_graph(c_records, f"{today_date.strftime('%Y-%m-%d (%a)')}", plain_mode=plain_mode)
            else:
                console.print(f"[yellow]No records for today ({today_date}) yet.[/yellow]")
        else:
            try:
                target_date = datetime.strptime(args.graph, "%Y-%m-%d").date()
                if target_date in days_charge:
                    c_records = sorted(days_charge[target_date], key=lambda x: x[0])
                    render_single_day_graph(c_records, f"{target_date.strftime('%Y-%m-%d (%a)')}", plain_mode=plain_mode)
                else:
                    console.print(f"[yellow]No history records found for {target_date}.[/yellow]")
            except ValueError:
                console.print("[bold red]Invalid date format.[/bold red] Use 'week', 'today', 'all', or YYYY-MM-DD.")


if __name__ == "__main__":
    main()
