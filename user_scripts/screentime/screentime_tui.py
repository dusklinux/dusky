#!/usr/bin/env python3
"""Matugen-themed Rich dashboard and fzf explorer for Hyprland screentime."""

import argparse
import json
import os
lazy import select
lazy import shlex
lazy import signal
lazy import subprocess
import sys
lazy import termios
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from screentime_common import DATA_DIR, DATA_FILE, THEME_FILE, HyprlandIPC, validate_data, valid_number

lazy from rich.console import Console
lazy from rich.live import Live
lazy from rich.markup import escape
lazy from rich.panel import Panel
lazy from rich.table import Table
lazy from rich.text import Text

LOG_FILE = DATA_DIR / "screentime_error.log"
_IPC = HyprlandIPC(timeout=0.1)

DEFAULT_COLORS: dict[str, str] = {
    "bg": "#0e1416",
    "fg": "#dee3e5",
    "accent": "#82d3e2",
    "error": "#ffb4ab",
    "warning": "#b1cbd0",
    "success": "#bbc5ea",
    "muted": "#3f484a",
    "cursor_bg": "#1c2528",
}

PERIOD_KEYS = frozendict({
    "period_today": "today",
    "period_yesterday": "yesterday",
    "period_week": "week",
    "period_month": "month",
    "period_all": "all",
})

PERIOD_LIST: list[str] = ["today", "yesterday", "week", "month", "all"]


def get_cycled_period(current_key: str, step: int = 1) -> str:
    """Cycle forward (+1) or backward (-1) through PERIOD_LIST."""
    try:
        idx = PERIOD_LIST.index(current_key)
    except ValueError:
        idx = 0
    return PERIOD_LIST[(idx + step) % len(PERIOD_LIST)]

# Mouse / cursor control (written only when Live does not own the TTY)
_MOUSE_ON = "\x1b[?1000h\x1b[?1006h"
_MOUSE_OFF = "\x1b[?1000l\x1b[?1006l"
_CURSOR_HIDE = "\x1b[?25l"
_CURSOR_SHOW = "\x1b[?25h"
_ANSI_RESET = "\x1b[0m"


# =============================================================================
# LOGGING / THEME / DATA HELPERS
# =============================================================================
def log_error(err_msg: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] {err_msg}\n")
    except Exception:
        pass


def _safe_color(value: Any, fallback: str) -> str:
    if isinstance(value, str):
        value = value.strip()
        if value.startswith("#") and len(value) in (4, 7) and all(c in "0123456789abcdefABCDEF" for c in value[1:]):
            if len(value) == 4:
                return "#" + "".join(c * 2 for c in value[1:])
            return value
    return fallback


def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    hex_clean = hex_str.strip().lstrip("#")
    if len(hex_clean) == 3:
        hex_clean = "".join(c * 2 for c in hex_clean)
    if len(hex_clean) >= 6:
        try:
            return int(hex_clean[0:2], 16), int(hex_clean[2:4], 16), int(hex_clean[4:6], 16)
        except ValueError:
            pass
    return 222, 227, 229


def ansi_color(hex_str: str, bold: bool = False) -> str:
    r, g, b = hex_to_rgb(hex_str)
    prefix = "\033[1;" if bold else "\033["
    return f"{prefix}38;2;{r};{g};{b}m"


def load_theme_colors() -> dict[str, str]:
    colors = DEFAULT_COLORS.copy()
    if THEME_FILE.exists():
        try:
            with open(THEME_FILE, "r", encoding="utf-8") as f:
                user_colors = json.load(f)
                if isinstance(user_colors, dict):
                    for key, fallback in DEFAULT_COLORS.items():
                        if key in user_colors:
                            colors[key] = _safe_color(user_colors[key], fallback)
                    for key, val in user_colors.items():
                        if key not in colors:
                            colors[key] = _safe_color(val, DEFAULT_COLORS["fg"])
        except Exception as e:
            log_error(f"load_theme_colors error: {e}")
    return colors


def load_screentime_data() -> dict[str, dict[str, Any]]:
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return validate_data(data)
        except (OSError, ValueError) as e:
            raise ValueError(f"Cannot read usage history: {e}") from e
    return {}


def get_active_hypr_window() -> tuple[str, str]:
    data = _IPC.query("j/activewindow")
    if isinstance(data, dict):
        return str(data.get("class", "")).strip(), str(data.get("title", "")).strip()
    return "", ""


def simplify_category(cat: str) -> str:
    if not isinstance(cat, str):
        return "System"
    match cat:
        case "Terminal & Shell" | "TerminalEmulator":
            return "Terminal"
        case "Web Browser":
            return "Browser"
        case "Audio & Video" | "AudioVideo" | "Multimedia player":
            return "Media"
        case "Agentic Platform":
            return "AI"
        case "Development":
            return "Dev"
        case "Utilities" | "System" | "System Controls" | "System Settings":
            return "System"
        case "Virtual machine viewer/manager" | "Virtual Machine":
            return "VM"
        case "Office":
            return "Office"
        case "Gaming" | "Game":
            return "Gaming"
        case "Graphics":
            return "Graphics"
        case _:
            if len(cat) > 15:
                return f"{cat[:12]}..."
            return cat


def format_duration(seconds: int | float) -> str:
    if not valid_number(seconds) or seconds <= 0:
        return "0s"
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    match (h > 0, m > 0):
        case (True, _):
            return f"{h}h {m:02d}m {s:02d}s"
        case (False, True):
            return f"{m}m {s:02d}s"
        case _:
            return f"{s}s"


def single_line(value: str) -> str:
    return " ".join("".join(c if ord(c) >= 32 and ord(c) != 127 else " " for c in value).split())


def _period_tabs(range_key: str, colors: dict[str, str], width: int) -> Text:
    tabs = Text(overflow="ellipsis", no_wrap=True)
    labels = {"today": "Today", "yesterday": "Yesterday", "week": "7 Days", "month": "30 Days", "all": "All Time"}
    if width < 75:
        tabs.append(f" {labels[range_key]}  [Tab/1–5] Period", style=f"bold {colors['accent']}")
        return tabs
    for number, key in enumerate(PERIOD_LIST, 1):
        style = f"bold {colors['accent']} on {colors['cursor_bg']}" if key == range_key else f"dim {colors['fg']}"
        tabs.append(f" {number}:{labels[key]} ", style=style)
    return tabs


def _footer(details: bool, colors: dict[str, str], width: int) -> Text:
    action = "Back" if details else "Details"
    if width < 75:
        label = f" Enter:{action}  ?:Help  Q:{'Back' if details else 'Quit'}"
    else:
        label = f" ↑↓:Move  Enter:{action}  Tab:Period  F:Search  ?:Help  Q:{'Back' if details else 'Quit'}"
    return Text(label, style=colors['fg'], overflow="ellipsis", no_wrap=True)


def _usage_table(title: str, colors: dict[str, str], width: int) -> tuple[Table, tuple[int, ...]]:
    table = Table(box=None, expand=True, show_header=True, header_style=f"bold {colors['accent']}")
    table.add_column("", width=1, justify="center", no_wrap=True)
    table.add_column(title, ratio=3, overflow="ellipsis", no_wrap=True)
    table.add_column("Time", min_width=8, max_width=12, justify="right", no_wrap=True)
    columns = (0, 1, 2)
    if width >= 60:
        table.add_column("Share", width=7, justify="right", no_wrap=True)
        columns += (3,)
    if width >= 100:
        table.add_column("Share Bar", width=16, no_wrap=True)
        columns += (4,)
    table.add_column("", width=1, justify="center", no_wrap=True)
    return table, columns + (5,)


def render_help(console_width: int, console_height: int, colors: dict[str, str]) -> Panel:
    text = Text(style=colors['fg'])
    text.append("Navigation\n", style=f"bold {colors['accent']}")
    text.append("↑/↓ or j/k: move · wheel: scroll\nEnter/→: details · Esc/←: back\n1–5 or Tab/Shift-Tab: period\nPgUp/PgDn or Ctrl-U/D: page\nHome/End or g/G: first/last\nF or /: search · R: refresh\nQ: back/quit · Ctrl-C: quit\n\n")
    text.append("About the data\n", style=f"bold {colors['accent']}")
    text.append("Times show saved focused usage.\nIdle, locked and sleeping time is excluded.\nShare is the selected period's total.\nDetails shares are within that app.\nSession counts are focus visits.\nFocused now reports window focus.\nUpdates follow the save interval.\n\n")
    text.append("No history? Start tracking:\nsystemctl --user start dusky_screentime.service\n")
    # Small terminals retain the essential navigation and a way out.
    if console_height < 22 or console_width < 60:
        text = Text("↑↓ / j,k: move\nEnter: details · Esc: back\nTab / 1–5: period\nF: search · R: refresh\nQ: back/quit · Ctrl-C: quit\nTimes show saved focused usage.", style=colors['fg'])
    return Panel(text, title="Screentime Help", subtitle="Esc / Enter / ? to close", border_style=colors['accent'], height=console_height, expand=True)


def make_bar_text(
    percent: float,
    colors: dict[str, str],
    is_active: bool = False,
    width: int = 16,
) -> Text:
    percent = max(0.0, min(100.0, float(percent)))
    filled = int(round((percent / 100.0) * width))
    filled = max(0, min(width, filled))
    empty = width - filled

    accent = colors.get("accent", "#82d3e2")
    success = colors.get("success", "#bbc5ea")
    muted = colors.get("muted", "#3f484a")

    txt = Text()
    bar_color = success if is_active else accent
    txt.append("━" * filled, style=f"bold {bar_color}")
    txt.append("─" * empty, style=f"dim {muted}")
    return txt


def aggregate_by_range(
    raw_data: dict[str, dict[str, Any]], range_key: str
) -> tuple[dict[str, dict[str, Any]], float, str]:
    if range_key not in PERIOD_LIST:
        raise ValueError(f"Unknown period: {range_key}")
    today_date = datetime.now()
    today_str = today_date.strftime("%Y-%m-%d")
    yesterday_str = (today_date - timedelta(days=1)).strftime("%Y-%m-%d")

    target_days: list[str] = []
    display_label = ""

    match range_key:
        case "today":
            target_days = [today_str]
            display_label = "Today"
        case "yesterday":
            target_days = [yesterday_str]
            display_label = "Yesterday"
        case "week":
            target_days = [
                (today_date - timedelta(days=d)).strftime("%Y-%m-%d")
                for d in range(7)
            ]
            display_label = "Past 7 Days"
        case "month":
            target_days = [
                (today_date - timedelta(days=d)).strftime("%Y-%m-%d")
                for d in range(30)
            ]
            display_label = "Past 30 Days"
        case _:
            target_days = sorted(raw_data.keys(), reverse=True)
            display_label = "All Time"

    agg: dict[str, dict[str, Any]] = {}
    total_time = 0

    # Process in reverse chronological order so most recent app metadata is retained
    for day in sorted(target_days, reverse=True):
        if day not in raw_data or not isinstance(raw_data[day], dict):
            continue
        for cls, info in raw_data[day].items():
            if not isinstance(info, dict):
                continue
            dur = info.get("duration", 0)
            if not valid_number(dur) or dur <= 0:
                continue
            if cls not in agg:
                agg[cls] = {
                    "name": str(info.get("name", cls)),
                    "category": str(info.get("category", "Application")),
                    "icon": str(info.get("icon", "")),
                    "duration": 0,
                    "sessions": 0,
                    "titles": {},
                }
            agg[cls]["duration"] += dur
            sessions = info.get("sessions", 1)
            if valid_number(sessions):
                agg[cls]["sessions"] += int(sessions)
            total_time += dur

            titles_dict = info.get("titles")
            if isinstance(titles_dict, dict):
                for t_title, t_dur in titles_dict.items():
                    if valid_number(t_dur) and t_dur > 0:
                        agg[cls]["titles"][str(t_title)] = (
                            agg[cls]["titles"].get(str(t_title), 0) + t_dur
                        )

    return agg, total_time, display_label


# =============================================================================
# COLOR-CODED RICH FZF PREVIEW RENDERER (Clean Markup, Zero Raw ANSI Escape Leaks)
# =============================================================================
def render_fzf_preview(app_class: str, range_key: str = "today") -> None:
    preview_width = os.environ.get("FZF_PREVIEW_COLUMNS", "")
    console = Console(
        width=int(preview_width) if preview_width.isdigit() and int(preview_width) > 0 else None,
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        soft_wrap=False,
    )
    colors = load_theme_colors()
    agg, total_time, label = aggregate_by_range(load_screentime_data(), range_key)
    target_class = app_class.strip()
    info = agg.get(target_class)
    if info is None:
        for cls, candidate in agg.items():
            if cls.lower() == target_class.lower() or candidate["name"].lower() == target_class.lower():
                info, target_class = candidate, cls
                break
    if info is None:
        console.print(Text(f"No usage for {single_line(target_class)} ({label}).", style=colors['warning']))
        return
    duration = info["duration"]
    share = duration / total_time * 100 if total_time else 0
    console.print(Text(single_line(info["name"]), style=f"bold {colors['accent']}", no_wrap=True, overflow="ellipsis"))
    console.print(Text(f"{simplify_category(info['category'])} · {label}", style=colors['warning'], no_wrap=True, overflow="ellipsis"))
    console.print(Text(f"{format_duration(duration)} · {share:.1f}% of total · {info['sessions']} sessions", style=colors['success'], no_wrap=True, overflow="ellipsis"))
    if console.width >= 80:
        console.print(Text(f"Class: {single_line(target_class)} · Icon: {single_line(info['icon'])}", style=colors['muted'], no_wrap=True, overflow="ellipsis"))
    console.print(Text("Window titles / documents", style=f"bold {colors['accent']}"))
    table = Table(box=None, show_header=True, header_style=f"bold {colors['accent']}", expand=True, pad_edge=False)
    table.add_column("Time", style=colors['success'], min_width=8, max_width=12, justify="right", no_wrap=True)
    show_share = console.width >= 50
    if show_share:
        table.add_column("Share", style=colors['accent'], width=6, justify="right", no_wrap=True)
    table.add_column("Title / Document", style=colors['fg'], ratio=1, no_wrap=True, overflow="ellipsis")
    for title, seconds in sorted(info["titles"].items(), key=lambda item: item[1], reverse=True):
        cells: list[str | Text] = [format_duration(seconds)]
        if show_share:
            cells.append(f"{seconds / duration * 100:.1f}%")
        cells.append(Text(single_line(title)))
        table.add_row(*cells)
    if not info["titles"]:
        console.print(Text("No window titles recorded.", style=colors['muted']))
    else:
        console.print(table)


# =============================================================================
# COLOR-CODED INTERACTIVE FZF EXPLORER MODE
# =============================================================================
def run_fzf_explorer(range_key: str = "today") -> bool:
    raw_data = load_screentime_data()
    agg, total_time, r_label = aggregate_by_range(raw_data, range_key)
    colors = load_theme_colors()

    if not agg:
        print(f"[!] No screentime data available for the selected period ({range_key}).")
        return True

    accent_c = ansi_color(colors.get("accent", "#82d3e2"), bold=True)
    success_c = ansi_color(colors.get("success", "#bbc5ea"), bold=True)
    warning_c = ansi_color(colors.get("warning", "#b1cbd0"))
    fg_c = ansi_color(colors.get("fg", "#dee3e5"))
    muted_c = ansi_color(colors.get("muted", "#3f484a"))

    sorted_apps = sorted(agg.items(), key=lambda x: x[1]["duration"], reverse=True)

    lines = []
    for cls, info in sorted_apps:
        dur = info["duration"]
        share = (dur / total_time * 100.0) if total_time > 0 else 0.0
        name = " ".join(info.get("name", cls).split())
        cat = " ".join(simplify_category(info.get("category", "Application")).split())
        name = "".join(c for c in name if ord(c) >= 32 and ord(c) != 127)
        cat = "".join(c for c in cat if ord(c) >= 32 and ord(c) != 127)

        disp = (
            f"{accent_c}{name:<26}{_ANSI_RESET} "
            f"{muted_c}│{_ANSI_RESET} {success_c}{format_duration(dur):<10}{_ANSI_RESET} "
            f"{muted_c}│{_ANSI_RESET} {warning_c}{share:5.1f}%{_ANSI_RESET} "
            f"{muted_c}│{_ANSI_RESET} {fg_c}{cat:<12}{_ANSI_RESET}"
        )
        lines.append(json.dumps(cls, ensure_ascii=True) + "\t" + disp.replace("\t", " ").replace("\n", " ").replace("\r", " "))

    script_path = str(Path(__file__).resolve())
    preview_cmd = f"{shlex.quote(sys.executable)} {shlex.quote(script_path)} --preview-json {{1}} {shlex.quote(range_key)}"

    visual_header = (
        f" {accent_c}{'APPLICATION':<26}{_ANSI_RESET} "
        f"{muted_c}│{_ANSI_RESET} {accent_c}{'TIME':<10}{_ANSI_RESET} "
        f"{muted_c}│{_ANSI_RESET} {accent_c}{'SHARE':<6}{_ANSI_RESET} "
        f"{muted_c}│{_ANSI_RESET} {accent_c}{'CATEGORY':<12}{_ANSI_RESET}"
    )

    fzf_cmd = [
        "fzf",
        "--ansi",
        "--no-height",
        "--delimiter=\t",
        "--with-nth=2..",
        "--read0",
        "--no-multi-line",
        "--with-shell=/bin/sh -c",
        "--no-hscroll",
        "--highlight-line",
        "--prompt= 󱎫 Screentime ❯ ",
        "--pointer=❯ ",
        "--marker=✔ ",
        "--layout=reverse",
        "--border=rounded",
        f"--border-label= 󱎫 Dusky Screentime Explorer ({r_label}) [Alt+C: Copy Summary] ",
        "--border-label-pos=3",
        "--info=hidden",
        f"--header={visual_header}",
        "--header-first",
        f"--color=bg+:{colors.get('muted', '#3f484a')},bg:{colors.get('bg', '#0e1416')},spinner:{colors.get('accent', '#82d3e2')}",
        f"--color=fg:{colors.get('fg', '#dee3e5')},fg+:{colors.get('fg', '#dee3e5')},header:{colors.get('accent', '#82d3e2')},info:{colors.get('accent', '#82d3e2')}",
        f"--color=pointer:{colors.get('success', '#bbc5ea')},marker:{colors.get('success', '#bbc5ea')},prompt:{colors.get('accent', '#82d3e2')}",
        f"--color=hl:{colors.get('accent', '#82d3e2')},hl+:{colors.get('accent', '#82d3e2')},border:{colors.get('muted', '#3f484a')},label:{colors.get('accent', '#82d3e2')}",
        f"--preview={preview_cmd}",
        "--preview-window=right,45%,border-left,wrap,<110(down,60%,border-top)",
        "--bind=alt-c:execute-silent(printf '%s\n' {2} | wl-copy)",
    ]

    input_data = ("\0".join(lines) + "\0").encode("utf-8")
    try:
        # This embedded UI owns its input format, bindings and terminal layout.
        environment = os.environ.copy()
        environment.pop("FZF_DEFAULT_OPTS", None)
        environment.pop("FZF_DEFAULT_OPTS_FILE", None)
        proc = subprocess.run(fzf_cmd, input=input_data, capture_output=True, env=environment)
        if proc.returncode not in (0, 1, 130):
            message = proc.stderr.decode("utf-8", errors="replace").strip()
            log_error(f"fzf exited {proc.returncode}: {message}")
            print(f"[!] fzf failed: {message}", file=sys.stderr)
            return False
    except OSError as e:
        log_error(f"fzf error: {e}")
        print(f"[!] Cannot open search: {e}", file=sys.stderr)
        return False
    return True


# =============================================================================
# LIVE RICH TERMINAL DASHBOARD LAYOUTS (Overview & App Details)
# =============================================================================
def render_dashboard_layout(
    range_key: str,
    colors: dict[str, str],
    scroll_offset: int,
    cursor_idx: int,
    console_height: int,
    raw_data: dict[str, dict[str, Any]] | None = None,
    active_window: tuple[str, str] | None = None,
    console_width: int = 80,
    summary: tuple[dict[str, dict[str, Any]], float, str] | None = None,
) -> tuple[Panel, int, int, int]:
    try:
        return _render_dashboard_layout_impl(
            range_key, colors, scroll_offset, cursor_idx, console_height, raw_data, active_window, console_width, summary
        )
    except Exception as e:
        log_error(f"render_dashboard_layout error: {e}\n{traceback.format_exc()}")
        err = Panel(
            Text(f"Render error (see log). Period={range_key}: {e}", style="bold red"),
            border_style="red",
            expand=True,
            height=max(8, console_height or 24),
        )
        return err, scroll_offset, cursor_idx, 0


def _render_dashboard_layout_impl(
    range_key: str,
    colors: dict[str, str],
    scroll_offset: int,
    cursor_idx: int,
    console_height: int,
    raw_data: dict[str, dict[str, Any]] | None,
    active_window: tuple[str, str] | None,
    console_width: int,
    summary: tuple[dict[str, dict[str, Any]], float, str] | None,
) -> tuple[Panel, int, int, int]:
    if raw_data is None:
        raw_data = load_screentime_data()
    agg, total_time, r_name = summary if summary is not None else aggregate_by_range(raw_data, range_key)
    if active_window is None:
        active_cls, _active_title = get_active_hypr_window()
    else:
        active_cls, _active_title = active_window

    console_height = max(8, int(console_height or 24))

    accent = colors.get("accent", "#82d3e2")
    success = colors.get("success", "#bbc5ea")
    warning = colors.get("warning", "#b1cbd0")
    fg = colors.get("fg", "#dee3e5")
    muted = colors.get("muted", "#3f484a")
    cursor_bg = colors.get("cursor_bg", "#1c2528")

    period_tabs = _period_tabs(range_key, colors, console_width)

    header_text = Text(overflow="ellipsis", no_wrap=True)
    header_text.append(" 󱎫 Dusky Screentime ", style=f"bold {accent}")
    header_text.append(f"({r_name})", style=f"bold {warning}")
    header_text.append("  Total: ", style=f"{fg}")
    header_text.append(f"{format_duration(total_time)}", style=f"bold {success}")
    header_text.append("  Apps: ", style=f"{fg}")
    header_text.append(f"{len(agg)}", style=f"bold {fg}")

    if active_cls:
        header_text.append("   Focused: ", style=f"bold {success}")
        header_text.append(single_line(active_cls), style=f"bold {success}")
    else:
        header_text.append("   Focused: ", style=f"dim {muted}")
        header_text.append("none", style=f"dim {muted}")

    table, columns = _usage_table("Application" if console_width < 60 else "Application & Category", colors, console_width)

    sorted_apps = sorted(agg.items(), key=lambda x: x[1]["duration"], reverse=True)
    total_apps = len(sorted_apps)

    visible_rows = max(1, console_height - 7)
    max_scroll = max(0, total_apps - visible_rows)

    if total_apps > 0:
        cursor_idx = max(0, min(cursor_idx, total_apps - 1))
        if cursor_idx < scroll_offset:
            scroll_offset = cursor_idx
        elif cursor_idx >= scroll_offset + visible_rows:
            scroll_offset = cursor_idx - visible_rows + 1
    else:
        cursor_idx = 0
        scroll_offset = 0

    scroll_offset = max(0, min(scroll_offset, max_scroll))
    page_apps = sorted_apps[scroll_offset : scroll_offset + visible_rows]

    if total_apps > visible_rows:
        thumb_h = max(1, int(round((visible_rows / total_apps) * visible_rows)))
        max_thumb_top = max(0, visible_rows - thumb_h)
        if max_scroll > 0:
            thumb_top = int(round((scroll_offset / max_scroll) * max_thumb_top))
        else:
            thumb_top = 0
    else:
        thumb_h = visible_rows
        thumb_top = 0

    for idx_in_page, (cls, info) in enumerate(page_apps):
        global_idx = scroll_offset + idx_in_page
        dur = info["duration"]
        share = (dur / total_time * 100.0) if total_time > 0 else 0.0
        is_active = (cls.lower() == active_cls.lower() and active_cls != "")
        is_cursor = (global_idx == cursor_idx)

        if is_active:
            status_cell = Text("●", style=f"bold {success}")
        elif is_cursor:
            status_cell = Text("▸", style=f"bold {accent}")
        else:
            status_cell = Text("·", style=f"dim {muted}")

        app_name = single_line(info.get("name", cls))
        cat = single_line(simplify_category(info.get("category", "Application"))) if console_width >= 60 else ""

        bg_style = f"on {cursor_bg}" if is_cursor else ""

        app_cell = Text()
        if is_active:
            app_cell.append(f"{app_name}", style=f"bold {success}")
            app_cell.append(f"  ({cat})" if cat else "", style=f"dim {success}")
        elif is_cursor:
            app_cell.append(f"{app_name}", style=f"bold {fg}")
            app_cell.append(f"  ({cat})" if cat else "", style=f"dim {warning}")
        else:
            app_cell.append(f"{app_name}", style=f"bold {fg}" if global_idx == 0 else f"{fg}")
            app_cell.append(f"  ({cat})" if cat else "", style=f"dim {warning}")

        dur_cell = Text(format_duration(dur), style=f"bold {success}" if is_active or is_cursor or global_idx == 0 else f"{success}")
        share_cell = Text(f"{share:.1f}%", style=f"bold {success}" if is_active else f"{accent}")
        bar_cell = make_bar_text(share, colors, is_active, width=16)

        if total_apps > visible_rows:
            if thumb_top <= idx_in_page < thumb_top + thumb_h:
                scroll_cell = Text("┃", style=f"bold {accent}")
            else:
                scroll_cell = Text("│", style=f"dim {muted}")
        else:
            scroll_cell = Text("")

        if bg_style:
            status_cell.stylize(bg_style)
            app_cell.stylize(bg_style)
            dur_cell.stylize(bg_style)
            share_cell.stylize(bg_style)
            bar_cell.stylize(bg_style)

        cells = (status_cell, app_cell, dur_cell, share_cell, bar_cell, scroll_cell)
        table.add_row(*(cells[index] for index in columns))

    rows_rendered = len(page_apps)
    if rows_rendered < visible_rows:
        for _ in range(visible_rows - rows_rendered):
            table.add_row(*("" for _ in columns))

    footer_text = _footer(False, colors, console_width)
    if console_width < 75:
        header_text = Text(f" Total: {format_duration(total_time)} · Apps: {len(agg)}", style=fg, no_wrap=True)

    layout_group = Table.grid(expand=True)
    layout_group.add_row(period_tabs)
    layout_group.add_row(header_text)
    layout_group.add_row(table)
    layout_group.add_row(footer_text)

    if total_apps > visible_rows:
        subtitle_str = (
            f"[bold {accent}]Item {cursor_idx + 1} of {total_apps}[/] "
            f"[dim]({scroll_offset + 1}–{min(scroll_offset + visible_rows, total_apps)} visible | j/k/Wheel to scroll)[/dim]"
        )
    elif total_apps > 0:
        subtitle_str = f"[bold {accent}]Item {cursor_idx + 1} of {total_apps}[/] [dim](Live Dashboard)[/dim]"
    else:
        safe_name = str(r_name).replace("[", "").replace("]", "")
        subtitle_str = f"[bold {warning}]No screentime data recorded for {safe_name}[/]"

    panel = Panel(
        layout_group,
        border_style=f"{accent}",
        subtitle=subtitle_str,
        expand=True,
        height=console_height,
    )
    return panel, scroll_offset, cursor_idx, max_scroll


def render_details_layout(
    target_app_class: str,
    range_key: str,
    colors: dict[str, str],
    details_scroll: int,
    details_cursor: int,
    console_height: int,
    raw_data: dict[str, dict[str, Any]] | None = None,
    active_window: tuple[str, str] | None = None,
    console_width: int = 80,
    summary: tuple[dict[str, dict[str, Any]], float, str] | None = None,
) -> tuple[Panel, int, int, int]:
    """
    Renders the in-TUI Deep Dive Details layout for a specific application.
    Supports full scroll navigation through all recorded window titles and documents.
    """
    if raw_data is None:
        raw_data = load_screentime_data()
    agg, total_time, r_name = summary if summary is not None else aggregate_by_range(raw_data, range_key)
    if active_window is None:
        active_cls, _active_title = get_active_hypr_window()
    else:
        active_cls, _active_title = active_window

    console_height = max(8, int(console_height or 24))

    accent = colors.get("accent", "#82d3e2")
    success = colors.get("success", "#bbc5ea")
    warning = colors.get("warning", "#b1cbd0")
    fg = colors.get("fg", "#dee3e5")
    muted = colors.get("muted", "#3f484a")
    cursor_bg = colors.get("cursor_bg", "#1c2528")

    # Resolve target info
    app_class_clean = target_app_class.strip()
    target_info = agg.get(app_class_clean)
    if not target_info:
        for cls, info in agg.items():
            if cls.lower() == app_class_clean.lower() or info.get("name", "").lower() == app_class_clean.lower():
                target_info = info
                app_class_clean = cls
                break

    period_tabs = _period_tabs(range_key, colors, console_width)

    if not target_info:
        empty_grid = Table.grid(expand=True)
        empty_grid.add_row(period_tabs)
        empty_grid.add_row(Text(f"\n  ✖ No screentime data recorded for '{app_class_clean}' in period: {r_name}\n", style=f"bold {warning}"))
        empty_grid.add_row(Text("  [Esc/Enter/q] Back to Dashboard   [1-5] Switch Period", style=f"bold {accent}"))
        panel = Panel(
            empty_grid,
            border_style=f"{warning}",
            subtitle=f"[bold {warning}]Empty Details ({r_name})[/]",
            expand=True,
            height=console_height,
        )
        return panel, 0, 0, 0

    name = single_line(target_info.get("name", app_class_clean))
    cat = simplify_category(target_info.get("category", "Application"))
    icon = target_info.get("icon", "")
    dur = target_info.get("duration", 0)
    sessions = target_info.get("sessions", 1)
    share = (dur / total_time * 100.0) if total_time > 0 else 0.0
    is_active = (app_class_clean.lower() == active_cls.lower() and active_cls != "")

    # Top metadata block
    meta_line = Text(overflow="ellipsis", no_wrap=True)
    meta_line.append(" 󱎫 ", style=f"bold {accent}")
    meta_line.append(f"{name}", style=f"bold {success}" if is_active else f"bold {fg}")
    meta_line.append(f" ({cat})", style=f"dim {warning}")
    meta_line.append("   Class: ", style=f"dim {muted}")
    meta_line.append(f"{app_class_clean}", style=f"{fg}")
    meta_line.append("   Icon: ", style=f"dim {muted}")
    meta_line.append(f"{icon or 'default'}", style=f"{success}")

    stats_line = Text(overflow="ellipsis", no_wrap=True)
    stats_line.append("   Total Time: ", style=f"{fg}")
    stats_line.append(f"{format_duration(dur)}", style=f"bold {success}")
    stats_line.append("   Share: ", style=f"{fg}")
    stats_line.append(f"{share:.1f}%", style=f"bold {accent}")
    stats_line.append("   Sessions: ", style=f"{fg}")
    stats_line.append(f"{sessions}", style=f"bold {fg}")
    if is_active:
        stats_line.append("   [FOCUSED NOW]", style=f"bold {success}")
    if console_width < 75:
        stats_line = Text(f" {format_duration(dur)} · {share:.1f}% · {sessions} sessions", style=fg, no_wrap=True)

    # Title breakdown table
    titles_sorted = sorted(
        target_info.get("titles", {}).items(), key=lambda x: x[1], reverse=True
    )
    total_titles = len(titles_sorted)

    table, columns = _usage_table("Window Title / Document", colors, console_width)
    compact_height = console_height < 12

    # Calculate layout space: console_height minus header lines and footer
    visible_rows = max(1, console_height - (7 if compact_height else 9))
    max_scroll = max(0, total_titles - visible_rows)

    if total_titles > 0:
        details_cursor = max(0, min(details_cursor, total_titles - 1))
        if details_cursor < details_scroll:
            details_scroll = details_cursor
        elif details_cursor >= details_scroll + visible_rows:
            details_scroll = details_cursor - visible_rows + 1
    else:
        details_cursor = 0
        details_scroll = 0

    details_scroll = max(0, min(details_scroll, max_scroll))
    page_titles = titles_sorted[details_scroll : details_scroll + visible_rows]

    if total_titles > visible_rows:
        thumb_h = max(1, int(round((visible_rows / total_titles) * visible_rows)))
        max_thumb_top = max(0, visible_rows - thumb_h)
        if max_scroll > 0:
            thumb_top = int(round((details_scroll / max_scroll) * max_thumb_top))
        else:
            thumb_top = 0
    else:
        thumb_h = visible_rows
        thumb_top = 0

    for idx_in_page, (t_title, t_dur) in enumerate(page_titles):
        global_idx = details_scroll + idx_in_page
        t_share = (t_dur / dur * 100.0) if dur > 0 else 0.0
        is_cursor = (global_idx == details_cursor)

        status_cell = Text("▸" if is_cursor else "·", style=f"bold {accent}" if is_cursor else f"dim {muted}")
        title_cell = Text(single_line(str(t_title)), style=f"bold {fg}" if is_cursor or global_idx == 0 else f"{fg}")
        dur_cell = Text(format_duration(t_dur), style=f"bold {success}" if is_cursor or global_idx == 0 else f"{success}")
        share_cell = Text(f"{t_share:.1f}%", style=f"bold {accent}" if is_cursor else f"{accent}")
        bar_cell = make_bar_text(t_share, colors, width=16)

        if total_titles > visible_rows:
            if thumb_top <= idx_in_page < thumb_top + thumb_h:
                scroll_cell = Text("┃", style=f"bold {accent}")
            else:
                scroll_cell = Text("│", style=f"dim {muted}")
        else:
            scroll_cell = Text("")

        bg_style = f"on {cursor_bg}" if is_cursor else ""
        if bg_style:
            status_cell.stylize(bg_style)
            title_cell.stylize(bg_style)
            dur_cell.stylize(bg_style)
            share_cell.stylize(bg_style)
            bar_cell.stylize(bg_style)

        cells = (status_cell, title_cell, dur_cell, share_cell, bar_cell, scroll_cell)
        table.add_row(*(cells[index] for index in columns))

    rows_rendered = len(page_titles)
    if rows_rendered < visible_rows:
        for _ in range(visible_rows - rows_rendered):
            table.add_row(*("" for _ in columns))

    divider = Text(" ─" * 40, style=f"dim {muted}", overflow="ellipsis", no_wrap=True)

    footer_text = _footer(True, colors, console_width)

    layout_group = Table.grid(expand=True)
    layout_group.add_row(period_tabs)
    if not compact_height:
        layout_group.add_row(meta_line)
    layout_group.add_row(stats_line)
    if not compact_height:
        layout_group.add_row(divider)
    layout_group.add_row(table)
    layout_group.add_row(footer_text)

    if total_titles > visible_rows:
        subtitle_str = (
            f"[bold {accent}]Item {details_cursor + 1} of {total_titles}[/] "
            f"[dim]({details_scroll + 1}–{min(details_scroll + visible_rows, total_titles)} visible | j/k/Wheel to scroll)[/dim]"
        )
    elif total_titles > 0:
        subtitle_str = f"[bold {accent}]Item {details_cursor + 1} of {total_titles}[/] [dim](Titles Breakdown)[/dim]"
    else:
        subtitle_str = f"[bold {warning}]No window titles recorded for {escape(name)}[/]"

    panel = Panel(
        layout_group,
        border_style=f"{accent}",
        subtitle=subtitle_str,
        expand=True,
        height=console_height,
    )
    return panel, details_scroll, details_cursor, max_scroll


# =============================================================================
# TERMINAL / RAW INPUT / EVENT LOOP
# =============================================================================
def set_terminal_cbreak(fd: int) -> list[Any]:
    """Enter non-canonical, no-echo mode WITHOUT O_NONBLOCK."""
    old_settings = termios.tcgetattr(fd)
    new_settings = termios.tcgetattr(fd)

    new_settings[0] &= ~(termios.IXON | termios.IXOFF | termios.ICRNL | termios.INLCR)
    new_settings[3] &= ~(
        termios.ECHO
        | termios.ECHOE
        | termios.ECHOK
        | termios.ECHONL
        | termios.ICANON
        | termios.IEXTEN
        | termios.ISIG
    )
    new_settings[6][termios.VMIN] = 0
    new_settings[6][termios.VTIME] = 0

    termios.tcsetattr(fd, termios.TCSAFLUSH, new_settings)
    return old_settings


def restore_terminal(fd: int, old_settings: list[Any]) -> None:
    try:
        sys.stdout.write(_MOUSE_OFF + _CURSOR_SHOW)
        sys.stdout.flush()
    except Exception:
        pass
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    except Exception:
        pass


def _write_tty(data: str) -> None:
    try:
        sys.stdout.write(data)
        sys.stdout.flush()
    except Exception:
        pass


def parse_input_sequence(buf: bytes | bytearray) -> tuple[str | None, int]:
    """Parse one command from the front of *buf*."""
    if not buf:
        return None, 0

    # ----- Escape / CSI / mouse / SS3 -----
    if buf[0] == 0x1B:
        if len(buf) == 1:
            return None, 0
        if buf[1] == 0x1B:
            return "escape", 1

        # SGR mouse: ESC [ < btn ; x ; y M/m
        if buf.startswith(b"\x1b[<"):
            for i in range(3, len(buf)):
                if buf[i] in (ord("M"), ord("m")):
                    try:
                        body = buf[3:i].decode("ascii", errors="ignore")
                        parts = body.split(";")
                        if buf[i] == ord("M") and parts and parts[0].isdigit():
                            b_code = int(parts[0])
                            if b_code == 64:
                                return "scroll_up", i + 1
                            if b_code == 65:
                                return "scroll_down", i + 1
                    except Exception:
                        pass
                    return None, i + 1
            return (None, 0) if len(buf) < 64 else (None, 1)

        # CSI: ESC [
        if buf[1] == ord("["):
            if len(buf) < 3:
                return None, 0
            for i in range(2, len(buf)):
                if 0x40 <= buf[i] <= 0x7E:
                    final = chr(buf[i])
                    inner = buf[2:i].decode("ascii", errors="ignore")
                    num = inner.split(";")[0] if inner else ""
                    if final == "A":
                        return "up", i + 1
                    if final == "B":
                        return "down", i + 1
                    if final == "C":
                        return "right", i + 1
                    if final == "D":
                        return "left", i + 1
                    if final == "H":
                        return "home", i + 1
                    if final == "F":
                        return "end", i + 1
                    if final == "Z":
                        return "prev_tab", i + 1
                    if final == "u":
                        if num == "9":
                            if ";2" in inner or ":2" in inner:
                                return "prev_tab", i + 1
                            return "next_tab", i + 1
                    if final == "~":
                        if num == "5":
                            return "page_up", i + 1
                        if num == "6":
                            return "page_down", i + 1
                        if num in {"1", "7"}:
                            return "home", i + 1
                        if num in {"4", "8"}:
                            return "end", i + 1
                        if num == "27" and (";2;9" in inner or ":2:9" in inner):
                            return "prev_tab", i + 1
                    return None, i + 1
            return (None, 0) if len(buf) < 32 else (None, 1)

        # SS3: ESC O A  (application cursor keys)
        if buf[1] == ord("O"):
            if len(buf) < 3:
                return None, 0
            if buf[2] == ord("A"):
                return "up", 3
            if buf[2] == ord("B"):
                return "down", 3
            if buf[2] == ord("C"):
                return "right", 3
            if buf[2] == ord("D"):
                return "left", 3
            if buf[2] == ord("H"):
                return "home", 3
            if buf[2] == ord("F"):
                return "end", 3
            if buf[2] in (ord("Z"), ord("I"), ord("i")):
                return "prev_tab", 3
            return None, 3

        # ESC + regular key (Alt+key) — consume both, ignore
        return None, 2

    ch = buf[:1]
    match ch:
        case b"\x1b":
            return "escape", 1
        case b"?":
            return "help", 1
        case b"q" | b"Q":
            return "quit", 1
        case b"\x03":
            return "force_quit", 1
        case b"\r" | b"\n" | b" ":
            return "select", 1
        case b"\x7f" | b"\x08":
            return "back", 1
        case b"\t" | b"]":
            return "next_tab", 1
        case b"[":
            return "prev_tab", 1
        case b"j" | b"s":
            return "down", 1
        case b"k" | b"w":
            return "up", 1
        case b"h" | b"a":
            return "left", 1
        case b"l" | b"d":
            return "right", 1
        case b"\x04":
            return "page_down", 1
        case b"\x15":
            return "page_up", 1
        case b"\x06":
            return "half_page_down", 1
        case b"\x02":
            return "half_page_up", 1
        case b"g":
            return "home", 1
        case b"G":
            return "end", 1
        case b"1":
            return "period_today", 1
        case b"2":
            return "period_yesterday", 1
        case b"3":
            return "period_week", 1
        case b"4":
            return "period_month", 1
        case b"5":
            return "period_all", 1
        case b"f" | b"F" | b"/":
            return "fzf", 1
        case b"r" | b"R":
            return "refresh", 1
        case _:
            return None, 1


class _DashCache:
    """Parse changed files only and reuse period totals between live frames."""

    def __init__(self) -> None:
        self.raw_data: dict[str, dict[str, Any]] = {}
        self.raw_ts = self.active_ts = self.colors_ts = 0.0
        self.active: tuple[str, str] = ("", "")
        self.colors = DEFAULT_COLORS.copy()
        self.raw_stamp: tuple[int, int, int] | None = None
        self.colors_stamp: tuple[int, int, int] | None = None
        self.error = ""
        self.summaries: dict[str, tuple[dict[str, dict[str, Any]], float, str]] = {}
        self.summary_date = datetime.now().date()

    @staticmethod
    def _stamp(path: Path) -> tuple[int, int, int] | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return stat.st_mtime_ns, stat.st_size, stat.st_ino

    def summary(self, range_key: str) -> tuple[dict[str, dict[str, Any]], float, str]:
        today = datetime.now().date()
        if today != self.summary_date:
            self.summaries.clear()
            self.summary_date = today
        if range_key not in self.summaries:
            self.summaries[range_key] = aggregate_by_range(self.raw_data, range_key)
        return self.summaries[range_key]

    def reload(self, force: bool = False, now: float | None = None) -> bool:
        t = now if now is not None else time.monotonic()
        changed = False
        if force or (t - self.raw_ts) >= 2.0:
            stamp = None
            try:
                stamp = self._stamp(DATA_FILE)
                if force or stamp != self.raw_stamp or self.error:
                    self.raw_data = load_screentime_data()
                    self.raw_stamp = stamp
                    self.summaries.clear()
                    self.error = ""
                    changed = True
            except (OSError, ValueError) as error:
                if stamp is None:
                    self.raw_stamp = None
                message = str(error)
                if message != self.error:
                    log_error(message)
                    changed = True
                self.error = message
            self.raw_ts = t
        if force or (t - self.active_ts) >= 1.0:
            active = get_active_hypr_window()
            changed |= active != self.active
            self.active = active
            self.active_ts = t
        if force or (t - self.colors_ts) >= 5.0:
            try:
                stamp = self._stamp(THEME_FILE)
                if force or stamp != self.colors_stamp:
                    self.colors_stamp = stamp
                    self.colors = load_theme_colors()
                    changed = True
            except OSError as error:
                log_error(f"Cannot read theme: {error}")
            self.colors_ts = t
        today = datetime.now().date()
        if today != self.summary_date:
            self.summaries.clear()
            self.summary_date = today
            changed = True
        return changed


def _pause_live(live: Live, fd: int, old_settings: list[Any]) -> None:
    """Leave alt-screen + cbreak so a child process (FZF) can own the TTY."""
    try:
        live.stop()
    except Exception as e:
        log_error(f"live.stop: {e}")
    _write_tty(_MOUSE_OFF + _CURSOR_SHOW)
    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    except Exception as e:
        log_error(f"pause termios restore: {e}")


def _resume_live(live: Live, fd: int) -> None:
    try:
        set_terminal_cbreak(fd)
    except Exception as e:
        log_error(f"resume cbreak: {e}")
    _write_tty(_MOUSE_ON + _CURSOR_HIDE)
    try:
        live.start(refresh=True)
    except Exception as e:
        log_error(f"live.start: {e}")


def run_live_dashboard() -> None:
    console = Console(
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        soft_wrap=False,
    )

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit("screentime_tui requires an interactive terminal")

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    old_sigterm = signal.getsignal(signal.SIGTERM)

    def terminate(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    cache = _DashCache()

    help_open = False
    current_view: str = "dashboard"  # "dashboard" or "details"
    selected_app_class: str = ""

    range_key = "today"
    scroll_offset = 0
    cursor_idx = 0

    details_scroll = 0
    details_cursor = 0

    def build_panel() -> Panel:
        nonlocal scroll_offset, cursor_idx, details_scroll, details_cursor
        if console.width < 32 or console.height < 8:
            return Panel(Text("Resize to at least 32 × 8. Ctrl-C quits."), border_style=cache.colors['warning'], height=max(1, console.height))
        if help_open:
            return render_help(console.width, console.height, cache.colors)
        if current_view == "details":
            panel, details_scroll, details_cursor, _ = render_details_layout(
                selected_app_class,
                range_key,
                cache.colors,
                details_scroll,
                details_cursor,
                console.height,
                raw_data=cache.raw_data,
                active_window=cache.active,
                console_width=console.width,
                summary=cache.summary(range_key),
            )
        else:
            panel, scroll_offset, cursor_idx, _ = render_dashboard_layout(
                range_key,
                cache.colors,
                scroll_offset,
                cursor_idx,
                console.height,
                raw_data=cache.raw_data,
                active_window=cache.active,
                console_width=console.width,
                summary=cache.summary(range_key),
            )
        if cache.error:
            panel.title = Text("History unreadable · showing last valid data · R to retry", style=cache.colors['warning'])
        return panel

    def push_frame(live: Live) -> None:
        try:
            live.update(build_panel(), refresh=True)
        except Exception as e:
            log_error(f"live.update/refresh: {e}\n{traceback.format_exc()}")

    try:
        signal.signal(signal.SIGTERM, terminate)
        set_terminal_cbreak(fd)
        _write_tty(_MOUSE_ON + _CURSOR_HIDE)
        cache.reload(force=True)
        panel = build_panel()

        with Live(
            panel,
            console=console,
            screen=True,
            auto_refresh=False,
            redirect_stdout=False,
            redirect_stderr=False,
            transient=True,
            vertical_overflow="crop",
        ) as live:
            input_buf = bytearray()
            running = True
            previous_size = console.size

            while running:
                try:
                    ready, _, _ = select.select([fd], [], [], 0.25)
                except InterruptedError:
                    ready = []

                got_keys = False
                input_pending = bool(input_buf)
                if ready:
                    try:
                        chunk = os.read(fd, 4096)
                    except BlockingIOError:
                        continue
                    except OSError as e:
                        log_error(f"os.read: {e}")
                        break

                    if not chunk:
                        break
                    input_buf.extend(chunk)
                    got_keys = True

                # Standalone Escape key detection on select timeout
                if not got_keys and input_buf == b"\x1b":
                    input_buf.clear()
                    if help_open:
                        help_open = False
                    elif current_view == "details":
                        current_view = "dashboard"
                    else:
                        running = False
                        break
                elif not got_keys and input_buf:
                    # Discard an abandoned partial escape sequence on timeout.
                    input_buf.clear()

                while input_buf:
                    cmd, consumed = parse_input_sequence(input_buf)
                    if consumed <= 0:
                        if input_buf[0] == 0x1B and len(input_buf) > 48:
                            del input_buf[0]
                            continue
                        break
                    del input_buf[:consumed]

                    if help_open and cmd != "force_quit":
                        if cmd in {"help", "quit", "escape", "back", "select", "left"}:
                            help_open = False
                        continue

                    match cmd:
                        case "help":
                            help_open = True
                        case "force_quit":
                            running = False
                            break
                        case "quit":
                            if current_view == "details":
                                current_view = "dashboard"
                            else:
                                running = False
                                break
                        case "escape" | "back":
                            if current_view == "details":
                                current_view = "dashboard"
                            else:
                                running = False
                                break
                        case "up":
                            if current_view == "details":
                                details_cursor = max(0, details_cursor - 1)
                            else:
                                cursor_idx = max(0, cursor_idx - 1)
                        case "down":
                            if current_view == "details":
                                details_cursor += 1
                            else:
                                cursor_idx += 1
                        case "scroll_up":
                            if current_view == "details":
                                details_cursor = max(0, details_cursor - 3)
                            else:
                                cursor_idx = max(0, cursor_idx - 3)
                        case "scroll_down":
                            if current_view == "details":
                                details_cursor += 3
                            else:
                                cursor_idx += 3
                        case "page_up":
                            if current_view == "details":
                                details_cursor = max(0, details_cursor - max(1, console.height - 9))
                            else:
                                cursor_idx = max(0, cursor_idx - max(1, console.height - 7))
                        case "page_down":
                            if current_view == "details":
                                details_cursor += max(1, console.height - 9)
                            else:
                                cursor_idx += max(1, console.height - 7)
                        case "half_page_up":
                            if current_view == "details":
                                details_cursor = max(0, details_cursor - max(1, (console.height - 9) // 2))
                            else:
                                cursor_idx = max(0, cursor_idx - max(1, (console.height - 7) // 2))
                        case "half_page_down":
                            if current_view == "details":
                                details_cursor += max(1, (console.height - 9) // 2)
                            else:
                                cursor_idx += max(1, (console.height - 7) // 2)
                        case "home":
                            if current_view == "details":
                                details_cursor = 0
                            else:
                                cursor_idx = 0
                        case "end":
                            if current_view == "details":
                                details_cursor = 10**9
                            else:
                                cursor_idx = 10**9
                        case "left":
                            if current_view == "details":
                                current_view = "dashboard"
                        case "select" | "right":
                            if current_view == "dashboard":
                                agg, _, _ = cache.summary(range_key)
                                sorted_apps = sorted(agg.items(), key=lambda x: x[1]["duration"], reverse=True)
                                if sorted_apps:
                                    cursor_idx = max(0, min(cursor_idx, len(sorted_apps) - 1))
                                    selected_app_class = sorted_apps[cursor_idx][0]
                                    details_cursor = 0
                                    details_scroll = 0
                                    current_view = "details"
                            else:
                                current_view = "dashboard"
                        case "period_today" | "period_yesterday" | "period_week" | "period_month" | "period_all":
                            range_key = PERIOD_KEYS[cmd]
                            if current_view == "dashboard":
                                cursor_idx = 0
                                scroll_offset = 0
                            else:
                                details_cursor = 0
                                details_scroll = 0
                        case "next_tab":
                            range_key = get_cycled_period(range_key, 1)
                            if current_view == "dashboard":
                                cursor_idx = 0
                                scroll_offset = 0
                            else:
                                details_cursor = 0
                                details_scroll = 0
                        case "prev_tab":
                            range_key = get_cycled_period(range_key, -1)
                            if current_view == "dashboard":
                                cursor_idx = 0
                                scroll_offset = 0
                            else:
                                details_cursor = 0
                                details_scroll = 0
                        case "fzf":
                            _pause_live(live, fd, old_settings)
                            try:
                                run_fzf_explorer(range_key)
                            finally:
                                _resume_live(live, fd)
                                cache.reload(force=True)
                        case "refresh":
                            cache.reload(force=True)
                        case _:
                            pass

                if not running:
                    break

                changed = cache.reload(force=False)
                size = console.size
                if got_keys or input_pending or changed or size != previous_size:
                    push_frame(live)
                previous_size = size

    except KeyboardInterrupt:
        pass
    except Exception as e:
        log_error(f"run_live_dashboard unhandled exception: {e}\n{traceback.format_exc()}")
        raise SystemExit(f"Dashboard failed; see {LOG_FILE}: {e}") from None
    finally:
        restore_terminal(fd, old_settings)
        signal.signal(signal.SIGTERM, old_sigterm)
        try:
            console.print("[bold green]Screentime Dashboard closed.[/bold green]")
        except Exception:
            pass


# =============================================================================
# CLI ENTRY POINT
# =============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="View saved focused-application usage.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--fzf", "-i", nargs="?", const="today", choices=PERIOD_LIST, metavar="PERIOD", help="search applications (default: today)")
    modes.add_argument("--preview", nargs="+", metavar="CLASS", help="preview CLASS [PERIOD] (default: today)")
    modes.add_argument("--preview-json", nargs=2, help=argparse.SUPPRESS)
    parser.epilog = "Periods: today, yesterday, week (7 days), month (30 days), all. Dashboard: ? opens help."
    argv = sys.argv[1:]
    if argv and argv[0] in {"fzf", "explore"}:
        argv = ["--fzf", *argv[1:]]
    args = parser.parse_args(argv)
    if args.fzf:
        if not run_fzf_explorer(args.fzf):
            raise SystemExit(1)
    elif args.preview_json:
        value, period = args.preview_json
        try:
            app_class = json.loads(value)
        except ValueError:
            parser.error("preview class must be a JSON string")
        if not isinstance(app_class, str):
            parser.error("preview class must be a JSON string")
        if period not in PERIOD_LIST:
            parser.error(f"unknown period: {period}")
        render_fzf_preview(app_class, period)
    elif args.preview:
        period = "today"
        words = args.preview
        if len(words) > 1:
            if words[-1] not in PERIOD_LIST:
                parser.error("expected a valid period after the application class")
            period = words[-1]
            words = words[:-1]
        render_fzf_preview(" ".join(words), period)
    else:
        run_live_dashboard()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from None
