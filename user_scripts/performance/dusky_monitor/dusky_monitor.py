#!/usr/bin/env python3
"""Live Linux process monitor. Requires Python 3.15+, psutil, Textual and fzf.

Accounting follows htop's LinuxProcessTable.c (RSS, CPU time and saturating
I/O deltas) and btop's linux/btop_collect.cpp (RSS and interval CPU usage).
Disk rates use read_bytes/write_bytes, not character counts including cached I/O.
No GPU libraries, device nodes or sensor interfaces are accessed.
"""

import argparse
import asyncio
import json
import math
import os
import re
import shlex
import sys
import time
lazy import subprocess
lazy import signal
from collections import deque
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any


def parse_cli_args(args: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(
        description="Live process monitor: RSS RAM, interval CPU and disk throughput.",
        epilog="Keys: s cycles RAM/CPU/disk; 1/2/3 select them directly. "
               "v switches groups/PIDs; Space toggles highlighting. "
               "Disk rates need two samples; unavailable readings show —. "
               "Per-process network and GPU sorting are not supported.",
    )
    views = parser.add_mutually_exclusive_group()
    views.add_argument("--processes", dest="grouped", action="store_false",
                       help="show individual PIDs instead of executable groups")
    views.add_argument("--group", dest="grouped", action="store_true",
                       help="group by executable and user (default)")
    parser.set_defaults(grouped=True)
    parser.add_argument("-p", "--pid", help="comma-separated positive PIDs")
    parser.add_argument("-n", "--name", default="", help="fzf name filter (full: include arguments)")
    parser.add_argument("terms", nargs="*", metavar="MODE/NUMBER",
                        help="ram|cpu|disk|read|write, full|short, interval (1.5s), count (20); "
                             "legacy two-number form uses the smaller as interval")
    args = list(sys.argv[1:] if args is None else args)
    if args == ["help"]:
        parser.print_help()
        parser.exit()
    ns = parser.parse_intermixed_args(args)
    config = dict(sort_metric="ram", show_full=True, interval=2.0,
                  count=None, pids=[], name=ns.name, grouped=ns.grouped)
    if ns.pid is not None:
        try:
            config["pids"] = [int(pid) for pid in ns.pid.replace(",", " ").split()]
            if not config["pids"] or any(pid <= 0 for pid in config["pids"]):
                raise ValueError
        except ValueError:
            parser.error("--pid requires positive integer PIDs")
    numbers = []
    explicit_interval = None
    for term in ns.terms:
        match term.lower():
            case "ram" | "mem" | "memory": config["sort_metric"] = "ram"
            case "cpu" | "disk" | "read" | "write" as metric: config["sort_metric"] = metric
            case "full" | "path" | "args": config["show_full"] = True
            case "short" | "clean" | "simple" | "base": config["show_full"] = False
            case "net" | "network" | "gpu":
                parser.error("per-process network/GPU accounting is not supported")
            case _:
                try:
                    value = float(term.removesuffix("s"))
                    if not math.isfinite(value) or value < 0:
                        raise ValueError
                except ValueError:
                    parser.error(f"invalid argument: {term!r}")
                if term.endswith("s"):
                    explicit_interval = value
                else:
                    numbers.append((term, value))
    if len(numbers) > 2:
        parser.error("expected at most two plain numbers")
    if len(numbers) == 1:
        text, value = numbers[0]
        if "." in text:
            config["interval"] = value
        elif value.is_integer():
            config["count"] = int(value) or None
        else:
            parser.error("count must be an integer; suffix intervals with s")
    elif len(numbers) == 2:
        interval, count = sorted(value for _, value in numbers)
        if not count.is_integer():
            parser.error("count must be an integer")
        config["interval"], config["count"] = interval, int(count) or None
    if explicit_interval is not None:
        config["interval"] = explicit_interval
    config["interval"] = max(0.1, config["interval"])
    return config


# Parse help/errors before loading the UI dependencies. Installation is offline
# provisioning's responsibility; never modify the interpreter at application startup.
if __name__ == "__main__":
    cli_config = parse_cli_args()

try:
    import psutil
    from textual import on, events, work
    from textual.app import App, ComposeResult
    from textual.coordinate import Coordinate
    from textual.binding import Binding
    from textual.containers import Vertical, Horizontal, Container, VerticalScroll
    from textual.widgets import Input, DataTable, Label, OptionList
    from textual.widgets.option_list import Option
    from textual.screen import ModalScreen
    from textual.reactive import reactive
    from textual.theme import Theme
    from textual.color import Color, ColorParseError
    from rich.text import Text
except ImportError as error:
    raise SystemExit(
        f"Dusky Monitor dependency error: {error}. Provision psutil "
        "and textual for the Python interpreter launching this script; search requires fzf."
    ) from error


class ProcessSampler:
    """Single-owner sampler; baselines use PID and start time to detect PID reuse."""

    def __init__(self, pids: set[int]) -> None:
        self.pids = pids
        self.previous: dict[int, dict[str, Any]] = {}

    @staticmethod
    def optional[T](getter: Callable[[], T]) -> T | None:
        try:
            return getter()
        except (psutil.AccessDenied, psutil.ZombieProcess):
            return None

    def sample(self) -> list[dict[str, Any]]:
        rows = []
        previous = {}
        total_memory = psutil.virtual_memory().total
        cpu_limit = (psutil.cpu_count() or 1) * 100.0
        # process_iter caches objects but no longer checks PID reuse. Validate
        # each identity with the public API, retaining cached start times instead
        # of rereading system boot time once per PID on every scan.
        for proc in psutil.process_iter():
            if self.pids and proc.pid not in self.pids:
                continue
            try:
                if not proc.is_running():
                    continue
                # Read the symlink freshly: psutil.exe() caches across exec().
                # readlink does not open the executable or any GPU device.
                try:
                    executable = os.readlink(f"{psutil.PROCFS_PATH}/{proc.pid}/exe")
                except OSError:
                    executable = None
                with proc.oneshot():
                    created = self.optional(proc.create_time)
                    uids = self.optional(proc.uids) if executable else None
                    times = self.optional(proc.cpu_times)
                    memory = self.optional(proc.memory_info)
                    io = self.optional(proc.io_counters)
                    name = self.optional(proc.name) or "?"
                    cmdline = self.optional(proc.cmdline) or []
                    status = self.optional(proc.status) or "unknown"
                now = time.monotonic()
                old = self.previous.get(proc.pid)
                if created is None or old is None or old["created"] != created:
                    old = None
                elapsed = now - old["sampled"] if old else 0.0
                cpu_time = times.user + times.system if times else None
                cpu = read = write = None
                if elapsed > 0:
                    if cpu_time is not None and old["cpu_time"] is not None:
                        cpu = min(cpu_limit, max(0.0, cpu_time - old["cpu_time"]) * 100 / elapsed)
                    if io is not None and old["io"] is not None:
                        read = max(0, io.read_bytes - old["io"].read_bytes) / elapsed
                        write = max(0, io.write_bytes - old["io"].write_bytes) / elapsed
                previous[proc.pid] = dict(created=created, sampled=now, cpu_time=cpu_time, io=io)
                rss = memory.rss if memory else None
                rows.append(dict(
                    key=f"pid:{proc.pid}:{created}", count=1, partial=frozenset(),
                    executable=executable, uid=uids.real if uids else None,
                    pid=proc.pid, created=created, status=status, cpu=cpu, cpu_time=cpu_time,
                    rss=rss, ram_mb=rss / 1024**2 if rss is not None else None,
                    mem_perc=rss * 100 / total_memory if rss is not None else None,
                    read=read, write=write,
                    disk=read + write if read is not None and write is not None else None,
                    time=format_time(cpu_time) if cpu_time is not None else "—",
                    full_cmd=shlex.join(cmdline) if cmdline else f"[{name}]",
                    short_cmd=Path(cmdline[0]).name if cmdline else f"[{name}]",
                ))
            except psutil.NoSuchProcess:
                continue
        self.previous = previous
        return rows


INTERPRETER_NAME = re.compile(
    r"(?:python(?:\d+(?:\.\d+)*)?[dt]?|pypy(?:\d+(?:\.\d+)*)?|"
    r"(?:ba|da|z|k|fi|c|tc)?sh|node(?:js)?|perl(?:\d+(?:\.\d+)*)?|"
    r"ruby(?:\d+(?:\.\d+)*)?|lua(?:jit|\d+(?:\.\d+)*)?|java|php(?:\d+(?:\.\d+)*)?)"
)


def group_processes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate executable/user groups; incomplete readings remain lower bounds.

    RSS/CPU summation follows btop's collapsed-process aggregation. Grouping by
    executable keeps browser content processes together without app name rules.
    Interpreter processes and unknown executable/user identities stay separate.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        executable, uid = row["executable"], row["uid"]
        shared_runtime = executable and INTERPRETER_NAME.fullmatch(Path(executable).name)
        key = (f"exe:{uid}:{executable}" if executable and uid is not None and not shared_runtime
               else row["key"])
        groups.setdefault(key, []).append(row)
    result = []
    fields = ("cpu", "rss", "mem_perc", "read", "write", "cpu_time")
    for key, members in groups.items():
        leader = min(members, key=lambda row: (
            row["created"] if row["created"] is not None else float("inf"), row["pid"]
        ))
        item = {**leader, "key": key, "count": len(members)}
        partial = set()
        for field in fields:
            values = [row[field] for row in members if row[field] is not None]
            item[field] = sum(values) if values else None
            if len(values) != len(members):
                partial.add(field)
        item["ram_mb"] = item["rss"] / 1024**2 if item["rss"] is not None else None
        if "rss" in partial:
            partial.add("ram_mb")
        item["disk"] = (item["read"] or 0) + (item["write"] or 0) if (
            item["read"] is not None or item["write"] is not None
        ) else None
        if "read" in partial or "write" in partial:
            partial.add("disk")
        item["partial"] = frozenset(partial)
        item["time"] = format_time(item["cpu_time"]) if item["cpu_time"] is not None else "—"
        item["search_commands"] = tuple(row["full_cmd"] for row in members)
        item["search_names"] = tuple(dict.fromkeys(row["short_cmd"] for row in members))
        item["status"] = f"{len(members)} processes" if len(members) > 1 else leader["status"]
        result.append(item)
    return result


def search_entries(rows: list[dict[str, Any]], full: bool) -> tuple[tuple[str, str], ...]:
    """Search displayed names, or every member's arguments in full-command mode."""
    field, fallback = ("search_commands", "full_cmd") if full else ("search_names", "short_cmd")
    return tuple((row["key"], command) for row in rows
                 for command in row.get(field, (row[fallback],)))


async def fzf_match(entries: tuple[tuple[str, str], ...], query: str) -> set[str]:
    """Use fzf's documented filter mode, without occupying or blocking the terminal."""
    if not entries:
        return set()
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"FZF_DEFAULT_OPTS", "FZF_DEFAULT_OPTS_FILE"}}
    process = await asyncio.create_subprocess_exec(
        "fzf", "--filter=" + query, "--no-sort", "--extended", "--smart-case",
        "--scheme=default", "--read0", "--print0", "--delimiter=\t", "--nth=2..",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=environment,
    )
    # NUL records preserve tabs/newlines in commands. Only the text after the
    # first tab is searchable; numeric record IDs are never mistaken for PIDs.
    payload = b"".join(f"{index}\t{command}\0".encode("utf-8", errors="replace")
                       for index, (_, command) in enumerate(entries))
    try:
        output, error = await process.communicate(payload)
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            cleanup = asyncio.create_task(process.communicate())
            # Another keystroke or shutdown may cancel this worker again while
            # it is reaping the child. Finish closing its pipes before exiting.
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    pass
            cleanup.result()
    if process.returncode not in (0, 1):
        raise RuntimeError(error.decode("utf-8", errors="replace").strip() or
                           f"fzf exited with status {process.returncode}")
    return {entries[int(record.split(b"\t", 1)[0])][0]
            for record in output.split(b"\0") if record}


class ProcessTable(DataTable):
    """Overview starts without a highlight; Enter enables it and selects a row."""

    def enable_highlight(self) -> None:
        first = int(self.scroll_y)
        visible = max(1, self.size.height - self.header_height)
        if self.row_count and not first <= self.cursor_row < first + visible:
            self.set_reactive(DataTable.cursor_coordinate, Coordinate(min(first, self.row_count - 1), 0))
        self.show_cursor = True

    def action_select_cursor(self) -> None:
        if self.row_count:
            self.enable_highlight()
            super().action_select_cursor()


# =============================================================================
# NOTIFICATIONS (Wayland notification daemon)
# =============================================================================
async def dispatch_notification(title: str, message: str) -> None:
    try:
        process = await asyncio.create_subprocess_exec(
            "notify-send",
            "--app-name=dusky-glance-alert",
            "-u", "critical",
            title, message,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        await process.wait()
    except OSError:
        pass

# =============================================================================
# FORMATTING & UTILITY ARCHITECTURE
# =============================================================================
def format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

def format_speed(bytes_per_sec: float | None) -> str:
    if bytes_per_sec is None:
        return "—"
    if bytes_per_sec >= 1024 * 1024:
        return f"{bytes_per_sec / (1024 * 1024):.1f} MiB/s"
    elif bytes_per_sec >= 1024:
        return f"{bytes_per_sec / 1024:.1f} KiB/s"
    return f"{bytes_per_sec:.0f} B/s"

# =============================================================================
# BESPOKE UI WIDGETS (The Braille Graph Engine)
# =============================================================================
class BrailleGraph(Label):
    """Graph with two horizontal samples and four vertical dots per cell."""
    data: reactive[list[float]] = reactive(list)

    def __init__(self, *args: Any, anchor_zero: bool = True, min_range: float = 1.0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.anchor_zero = anchor_zero
        self.min_range = min_range

    def render(self) -> Text:
        width = self.size.width
        height = self.size.height

        if width <= 0 or height <= 0:
            return Text("")

        max_points = width * 2
        actual_data = list(self.data)

        if not actual_data:
            empty_line = chr(0x2800) * width
            return Text("\n".join([empty_line] * height), no_wrap=True, overflow="crop")

        # Scale only the visible window.
        if len(actual_data) > max_points:
            actual_data = actual_data[-max_points:]

        min_val = min(actual_data)
        max_val = max(actual_data)

        if self.anchor_zero:
            min_val = 0.0

        if max_val - min_val < self.min_range:
            max_val = min_val + self.min_range

        range_val = max_val - min_val

        pad_len = max_points - len(actual_data)
        padded_data = [None] * pad_len + actual_data

        total_dots_y = height * 4
        dots_data = []

        for v in padded_data:
            if v is None:
                dots_data.append(0)
            else:
                normalized = (v - min_val) / range_val
                dots = int(normalized * total_dots_y)
                dots_data.append(min(total_dots_y, max(0, dots)))

        lines = []
        for row in range(height):
            row_bottom_dot_idx = (height - row - 1) * 4
            line_chars = []

            for i in range(0, len(dots_data), 2):
                dl = dots_data[i] - row_bottom_dot_idx
                dr = dots_data[i+1] - row_bottom_dot_idx

                hl = min(4, max(0, dl))
                hr = min(4, max(0, dr))

                # Binary Bitmask mapping into standard unicode braille offsets (Left & Right dot structures)
                l_mask = [0, 0x40, 0x44, 0x46, 0x47][hl]
                r_mask = [0, 0x80, 0xA0, 0xB0, 0xB8][hr]

                line_chars.append(chr(0x2800 + l_mask + r_mask))

            lines.append("".join(line_chars))

        return Text("\n".join(lines), no_wrap=True, overflow="crop")


# =============================================================================
# MODALS & INTERFACE COMPONENTS
# =============================================================================
class SetThresholdScreen(ModalScreen[float | None]):
    def compose(self) -> ComposeResult:
        with Vertical(id="threshold-dialog"):
            yield Label("Set RSS Threshold Alert (MiB)", id="modal-title")
            yield Input(placeholder="e.g., 2048", id="threshold-input", type="number")
            with Horizontal(classes="modal-btn-container"):
                yield Label(" Cancel ", classes="modal-close-btn", id="cancel-btn")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    @on(Input.Submitted, "#threshold-input")
    def on_submit(self, event: Input.Submitted) -> None:
        try:
            val = float(event.value)
            self.dismiss(val if math.isfinite(val) and val >= 0 else None)
        except ValueError:
            self.dismiss(None)

    @on(events.Click, "#cancel-btn")
    def on_cancel(self) -> None:
        self.dismiss(None)


class ShortcutsInfoScreen(ModalScreen[None]):
    def compose(self) -> ComposeResult:
        with Vertical(id="shortcuts-dialog"):
            yield Label("KEYBOARD SCHEMATICS", id="modal-title")
            yield OptionList(id="shortcuts-list")
            with Horizontal(classes="modal-btn-container"):
                yield Label(" Close ", classes="modal-close-btn")

    def on_mount(self) -> None:
        ol = self.query_one(OptionList)
        bindings_info = [
            ("q, ctrl+c", "Quit"),
            ("Enter", "Track selected program or PID"),
            ("v / Space", "Switch groups/PIDs / Toggle highlight"),
            ("t", "Set RAM alert threshold for tracked process"),
            ("s / 1, 2, 3", "Cycle sort / Select RAM, CPU, disk"),
            ("4, 5", "Sort by disk reads / writes"),
            ("c", "Toggle full command"),
            ("/", "Search commands"),
            ("Escape", "Clear search / Close dialog"),
            ("left / right", "Scroll wide tables horizontally"),
            ("j, down", "Navigate cursor descending"),
            ("k, up", "Navigate cursor ascending"),
            ("ctrl+d", "Navigate descending (10 indices)"),
            ("ctrl+u", "Navigate ascending (10 indices)"),
            ("g", "Traverse to the absolute zenith"),
            ("G", "Traverse to the absolute nadir"),
        ]

        for keys, desc in bindings_info:
            txt = Text()
            txt.append(f"{keys:<20}", style=self.app.theme_colors.get("accent", "blue") + " bold")
            txt.append(" ➜ ", style=self.app.theme_colors.get("muted", "grey"))
            txt.append(desc, style=self.app.theme_colors.get("fg", "white"))
            ol.add_option(Option(txt, disabled=True))

    @on(events.Click, ".modal-close-btn")
    def on_close_click(self) -> None:
        self.dismiss(None)


class Shortcut(Label):
    def __init__(self, key_text: str, label: str, action_name: str | None = None, **kwargs: Any) -> None:
        super().__init__(classes="footer-shortcut", **kwargs)
        self.key_text = key_text
        self.label_text = label
        self.action_name = action_name

    def render(self) -> Text:
        txt = Text()
        if self.has_class("-active"):
            txt.append(f"[{self.key_text}] ", style=f"bold {self.app.theme_colors.get('bg', '#000')}")
            txt.append(self.label_text, style=f"bold {self.app.theme_colors.get('bg', '#000')}")
        else:
            txt.append(f"[{self.key_text}] ", style=self.app.theme_colors.get("accent", "blue"))
            txt.append(self.label_text, style=self.app.theme_colors.get("fg", "white"))
        return txt

    async def on_click(self) -> None:
        if self.action_name: await self.app.run_action(self.action_name)


class AppFooter(Horizontal):
    def compose(self) -> ComposeResult:
        yield Shortcut("q", "Quit", "quit")
        yield Shortcut("/", "Search", "focus_search")
        yield Shortcut("v", "View", "toggle_grouping")
        yield Shortcut("Space", "Cursor", "toggle_highlight")
        yield Shortcut("Enter", "Track", "select_row")
        yield Shortcut("t", "Alert", "set_threshold")
        yield Shortcut("c", "Args", "toggle_cmd")
        yield Shortcut("s", "Sort", "toggle_sort")
        yield Shortcut("?", "Help", "show_shortcuts")

# =============================================================================
# TRACKER PANOPTICON (Detail View)
# =============================================================================
class ProcessPanopticon(Container):
    def compose(self) -> ComposeResult:
        with Horizontal(id="panopticon-header"):
            yield Label("TRACKED: None", id="panopticon-title")
            yield Label("Status: N/A", id="panopticon-status")
            yield Label("Uptime: 00:00:00", id="panopticon-uptime")
            yield Label("Threshold: None", id="panopticon-threshold")

        with VerticalScroll(id="panopticon-command-scroll", can_focus=True):
            yield Label("", id="panopticon-command", markup=False)

        with Horizontal(id="panopticon-io-row"):
            yield Label("↓ Read: 0 B/s", id="panopticon-io-read")
            yield Label("↑ Write: 0 B/s", id="panopticon-io-write")

        with Horizontal(id="panopticon-graphs-row"):
            with Vertical(id="cpu-container", classes="graph-container"):
                yield Label("CPU Utilization (%):", classes="graph-title")
                yield BrailleGraph(id="panopticon-cpu-graph", anchor_zero=True, min_range=100.0)

            with Vertical(id="ram-container", classes="graph-container"):
                yield Label("Summed RSS (MiB):", classes="graph-title")
                yield BrailleGraph(id="panopticon-ram-graph", anchor_zero=True, min_range=50.0)

# =============================================================================
# MAIN APPLICATION: Dusky Monitor
# =============================================================================
class DuskyMonitorApp(App):
    CSS = """
    Screen { background: $background; layout: vertical; }

    #panopticon {
        height: 14; dock: top; display: none;
        border: solid $primary; border-title-color: $primary; border-title-style: bold;
        background: $background 50%; padding: 0 1; margin: 0 0 1 0;
    }
    #panopticon.-active { display: block; }

    #panopticon-header { height: 1; align: left middle; }
    #panopticon-header > Label { width: 1fr; color: $foreground; text-style: bold; }
    #panopticon-title { color: $primary; }
    #panopticon-command-scroll { height: 3; margin-top: 1; scrollbar-size: 1 0; }
    #panopticon-command { width: 100%; height: auto; color: $foreground; }

    #panopticon-io-row { height: 1; align: left middle; margin-top: 1; }
    #panopticon-io-row > Label { width: 1fr; color: $secondary; }

    #panopticon-graphs-row { height: 1fr; margin-top: 1; }
    .graph-container { width: 1fr; height: 1fr; }
    #cpu-container { margin-right: 2; }
    .graph-title { color: $secondary; height: 1; margin-bottom: 0; }

    /* Dual Braille Engine Orchestration */
    #panopticon-cpu-graph { width: 1fr; height: 1fr; color: $warning; text-style: none; overflow: hidden; }
    #panopticon-ram-graph { width: 1fr; height: 1fr; color: $success; text-style: none; overflow: hidden; }

    #main-box {
        width: 100%; height: 1fr;
        border: solid $primary 50%; border-title-color: $primary; border-title-style: bold; border-title-align: center;
        border-subtitle-color: $primary; border-subtitle-style: bold; border-subtitle-align: right;
        background: transparent; padding: 0 1;
    }

    DataTable { height: 1fr; border: none; background: transparent; scrollbar-size: 0 0; }

    #search-bar {
        dock: bottom; display: none; height: 2; margin: 0; padding: 0 1;
        border-top: solid $primary 30%; background: transparent;
    }
    #search-bar.-active { display: block; }
    #search-prompt { width: auto; height: 1; margin-right: 1; color: $primary; text-style: bold; }
    #search-hint { width: auto; height: 1; margin-left: 2; color: $text-muted; }
    #search_input {
        width: 1fr; min-width: 1; height: 1; margin: 0; padding: 0;
        border: none; background: transparent; color: $foreground;
    }
    #search_input:focus { border: none; background-tint: transparent; }

    #footer {
        height: auto; min-height: 2; dock: bottom;
        border-top: solid $secondary; padding: 0 2;
        background: transparent; align: left middle;
    }
    .footer-shortcut { padding: 0 1; background: transparent; }
    .footer-shortcut:hover { text-style: bold; color: $foreground; background: $primary 25%; }

    ShortcutsInfoScreen, SetThresholdScreen { align: center middle; background: rgba(0, 0, 0, 0.75); }
    #shortcuts-dialog, #threshold-dialog { width: 70; height: auto; max-height: 80%; background: $background; border: solid $primary; padding: 1 2; }
    #modal-title { color: $primary; margin-bottom: 1; text-style: bold; border-bottom: solid $secondary; content-align: center middle; width: 100%; }
    #threshold-input { margin-bottom: 1; }

    #shortcuts-list { height: 1fr; scrollbar-size: 0 0; background: transparent; border: none; }
    #shortcuts-list > .option-list--option { padding: 0 1; background: transparent; }

    .modal-btn-container { width: 100%; height: auto; align: center middle; margin-top: 1; background: transparent; }
    .modal-close-btn { background: $primary; color: $background; text-style: bold; padding: 0 2; width: auto; height: 1; margin: 0 1;}
    .modal-close-btn:hover { background: $foreground; color: $background; }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit", priority=True),
        Binding("q", "quit", "Quit"),
        Binding("v", "toggle_grouping", "Groups/PIDs"),
        Binding("space", "toggle_highlight", "Highlight"),
        Binding("s", "toggle_sort", "Cycle RAM/CPU/Disk"),
        Binding("1", "sort_metric('ram')", "RAM", show=False),
        Binding("2", "sort_metric('cpu')", "CPU", show=False),
        Binding("3", "sort_metric('disk')", "Disk", show=False),
        Binding("4", "sort_metric('read')", "Read", show=False),
        Binding("5", "sort_metric('write')", "Write", show=False),
        Binding("c", "toggle_cmd", "Toggle Command Mode"),
        Binding("t", "set_threshold", "Set Threshold"),
        Binding("/", "focus_search", "Search"),
        Binding("escape", "clear_search", "Clear Search", priority=True),
        Binding("?", "show_shortcuts", "Shortcuts"),
        Binding("f1", "show_shortcuts", "Shortcuts"),
        Binding("ctrl+d", "jump_down", "Jump Down", show=False),
        Binding("ctrl+u", "jump_up", "Jump Up", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("g", "scroll_top", "Top", show=False),
        Binding("G", "scroll_bottom", "Bottom", show=False),
    ]

    sort_by: reactive[str] = reactive("ram")
    search_query: reactive[str] = reactive("")
    refresh_interval: reactive[float] = reactive(2.0)
    show_full_cmd: reactive[bool] = reactive(False)
    max_count: reactive[int | None] = reactive(None)

    def __init__(self, cli_config: dict[str, Any], *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        self.grouped = cli_config["grouped"]
        self.sort_by = cli_config["sort_metric"]
        self.show_full_cmd = cli_config["show_full"]
        self.refresh_interval = cli_config["interval"]
        self.max_count = cli_config["count"]
        self.target_pids = set(cli_config["pids"])
        self.search_query = cli_config["name"]

        self.sampler = ProcessSampler(self.target_pids)
        self.rows: list[dict[str, Any]] = []
        self._sampling = False
        self.tracked_key: str | None = None
        self.view_rows: dict[str, dict[str, Any]] = {}
        self.theme_path = Path("~/.config/matugen/generated/dusky_tui.json").expanduser().resolve()
        self.last_theme_mtime: int = 0

        # Dual-Array State Injection
        self.tracked_ram_history: deque[float] = deque(maxlen=2000)
        self.tracked_cpu_history: deque[float] = deque(maxlen=2000)

        self.tracked_threshold_mb: float | None = None
        self.last_alert_time: float = float("-inf")

        self._search_task: asyncio.Task | None = None
        self._filter_worker = None
        self._filter_signature = None
        self._filter_keys: set[str] | None = None
        self._filter_reset_position = False

        self.theme_colors = {
            "bg": "#0d141b", "fg": "#dce3ee", "accent": "#9acbff",
            "error": "#ffb4ab", "warning": "#b9c6ea", "success": "#c2c1f9", "muted": "#404750"
        }

    def compose(self) -> ComposeResult:
        yield ProcessPanopticon(id="panopticon")
        with Vertical(id="main-box"):
            yield ProcessTable(id="process_table", cursor_type="row", show_cursor=False)
            with Horizontal(id="search-bar"):
                yield Label("Search ›", id="search-prompt")
                yield Input(id="search_input", placeholder="Filter processes…", compact=True)
                yield Label("Enter keep · Esc clear", id="search-hint")
        yield AppFooter(id="footer")

    def apply_theme_to_engine(self) -> None:
        self._theme_toggle = not getattr(self, "_theme_toggle", False)
        theme_name = "dusky_live_A" if self._theme_toggle else "dusky_live_B"

        custom_theme = Theme(
            name=theme_name,
            primary=self.theme_colors.get("accent", "#9acbff"),
            secondary=self.theme_colors.get("muted", "#404750"),
            background=self.theme_colors.get("bg", "#0d141b"),
            surface=self.theme_colors.get("bg", "#0d141b"),
            warning=self.theme_colors.get("warning", "#b9c6ea"),
            error=self.theme_colors.get("error", "#ffb4ab"),
            success=self.theme_colors.get("success", "#c2c1f9"),
            variables={
                "foreground": self.theme_colors.get("fg", "#dce3ee"),
            },
        )
        self.register_theme(custom_theme)
        self.theme = theme_name

        self.refresh_rows()
        for shortcut in self.query(Shortcut):
            shortcut.refresh()

    def load_theme_file(self) -> tuple[int, dict[str, str]]:
        stamp = self.theme_path.stat().st_mtime_ns
        if stamp == self.last_theme_mtime:
            return stamp, {}
        payload = json.loads(self.theme_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("theme must be a JSON object")
        colors = {}
        for key in self.theme_colors:
            if key in payload:
                if not isinstance(payload[key], str):
                    raise ValueError(f"theme color {key} must be a string")
                colors[key] = Color.parse(payload[key]).hex6
        return stamp, colors

    async def watch_theme_file(self) -> None:
        try:
            stamp, colors = await asyncio.to_thread(self.load_theme_file)
        except (OSError, ValueError, ColorParseError):
            return  # A missing or partially rewritten theme is retried next poll.
        if not self.is_running:
            return
        self.last_theme_mtime = stamp
        if colors:
            self.theme_colors.update(colors)
            self.apply_theme_to_engine()

    def on_mount(self) -> None:
        self.query_one("#main-box").border_title = " Dusky Monitor"

        table = self.query_one(DataTable)
        table.add_columns(
            "[primary bold]PID[/]",
            "PROCS",
            "[warning bold]CPU %[/]",
            "[success bold]MEM %[/]",
            "[text bold]ΣRSS (MiB)[/]",
            "READ/s",
            "WRITE/s",
            "DISK/s",
            "[secondary bold]TIME[/]",
            "[text bold]COMMAND[/]"
        )
        table.focus()

        if self.search_query:
            self.query_one("#search-bar").add_class("-active")
            self.query_one("#search_input", Input).value = self.search_query

        try:
            self.last_theme_mtime, colors = self.load_theme_file()
            self.theme_colors.update(colors)
        except (OSError, ValueError, ColorParseError):
            pass
        self.apply_theme_to_engine()
        self.update_table()
        self.set_interval(self.refresh_interval, self.update_table)
        self.set_interval(2.0, self.watch_theme_file)

    def sort_data(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        field = "rss" if self.sort_by == "ram" else self.sort_by
        # Apply resource ordering and the row limit after fzf filtering.
        ordered = sorted(data, key=lambda item: (
            item[field] if item[field] is not None else -1, -item["pid"]
        ), reverse=True)
        return ordered[:self.max_count] if self.max_count else ordered

    def update_table(self) -> None:
        if self.is_running and not self._sampling:
            self._sampling = True
            self.execute_heuristic_fetch()

    @work
    async def execute_heuristic_fetch(self) -> None:
        """One scan at a time; UI actions never cancel or overlap sampler threads."""
        try:
            rows = await asyncio.to_thread(self.sampler.sample)
            if not self.is_running:
                return
            self.rows = rows
            self.refresh_rows()
            if self.tracked_key is not None:
                tracked = self.view_rows.get(self.tracked_key)
                if tracked is None:
                    self._clear_tracking()
                else:
                    self._update_panopticon_dom(tracked)
        finally:
            self._sampling = False

    def refresh_rows(self, *, reset_position: bool = False) -> None:
        view = group_processes(self.rows) if self.grouped else self.rows
        self.view_rows = {row["key"]: row for row in view}
        self._filter_reset_position |= reset_position
        if not self.search_query.strip():
            if self._filter_worker is not None:
                self._filter_worker.cancel()
            self._filter_signature = None
            self._filter_keys = None
        else:
            signature = (self.search_query, search_entries(view, self.show_full_cmd))
            if signature != self._filter_signature:
                self._filter_signature = signature
                self._filter_keys = None
                self._filter_worker = self.run_worker(
                    partial(self._filter_rows, signature), group="search", exclusive=True,
                )
            if self._filter_keys is None:
                return
            view = [row for row in view if row["key"] in self._filter_keys]
        self._refresh_dom_matrix(self.sort_data(view), reset_position=self._filter_reset_position)
        self._filter_reset_position = False

    async def _filter_rows(self, signature: tuple[str, tuple[tuple[str, str], ...]]) -> None:
        query, entries = signature
        try:
            keys = await fzf_match(entries, query)
        except (OSError, RuntimeError) as error:
            self.notify(f"Search failed: {error}. Search requires fzf.", severity="error")
            keys = set()
        if self.is_running and signature == self._filter_signature:
            self._filter_keys = keys
            # Render the newest sample and sort settings, not an old snapshot.
            self.refresh_rows()

    def _clear_tracking(self) -> None:
        """Clear an explicit tracking target, independently of cursor position."""
        self.tracked_key = None
        self.tracked_threshold_mb = None
        self.query_one("#panopticon").remove_class("-active")

    def _update_panopticon_dom(self, data: dict[str, Any]) -> None:
        """Update the tracked process from the same sample used by the table."""
        now = time.monotonic()
        uptime_secs = max(0.0, time.time() - data["created"]) if data["created"] is not None else 0.0
        r_speed, w_speed = data["read"], data["write"]

        if data["ram_mb"] is not None:
            self.tracked_ram_history.append(data["ram_mb"])
        if data["cpu"] is not None:
            self.tracked_cpu_history.append(data["cpu"])

        if self.tracked_threshold_mb is not None and data["ram_mb"] is not None and data["ram_mb"] > self.tracked_threshold_mb:
            if now - self.last_alert_time > 15:
                self.run_worker(dispatch_notification(
                    "RSS threshold exceeded",
                    f"{data['short_cmd']} ({data['count']} processes): {data['ram_mb']:.1f} MiB",
                ), group="notifications")
                self.last_alert_time = now

        label = f"{data['short_cmd']} · {data['count']} processes" if self.grouped else f"PID {data['pid']}"
        self.query_one("#panopticon-title", Label).update(Text(f"TRACKED: {label}"))
        executable = data["executable"] or "unavailable"
        self.query_one("#panopticon-command", Label).update(
            f"Executable: {executable}\nCommand: {data['full_cmd']}"
        )
        self.query_one("#panopticon-status", Label).update(f"Status: [bold {self.theme_colors.get('accent')}]{str(data['status']).upper()}[/]")
        self.query_one("#panopticon-uptime", Label).update(f"Uptime: [bold]{format_time(uptime_secs)}[/]")

        thresh_str = f"{self.tracked_threshold_mb} MiB" if self.tracked_threshold_mb is not None else "Unconstrained"
        self.query_one("#panopticon-threshold", Label).update(f"Threshold Limit: [bold {self.theme_colors.get('error')}]{thresh_str}[/]")
        read_text = ("≥" if "read" in data["partial"] and r_speed is not None else "") + format_speed(r_speed)
        write_text = ("≥" if "write" in data["partial"] and w_speed is not None else "") + format_speed(w_speed)
        self.query_one("#panopticon-io-read", Label).update(f"↓ Read: [bold]{read_text}[/]")
        self.query_one("#panopticon-io-write", Label).update(f"↑ Write: [bold]{write_text}[/]")

        self.query_one("#panopticon-ram-graph", BrailleGraph).data = list(self.tracked_ram_history)
        self.query_one("#panopticon-cpu-graph", BrailleGraph).data = list(self.tracked_cpu_history)

    def _refresh_dom_matrix(self, sorted_data: list[dict[str, Any]], *, reset_position: bool = False) -> None:
        """Update rows without following identities or resetting the viewport."""
        with self.batch_update():
            table = self.query_one(DataTable)
            rank = 0 if reset_position else table.cursor_row
            x, y = table.scroll_x, 0 if reset_position else table.scroll_y
            # Public set_reactive bypasses cursor watchers during structural edits.
            # User navigation still uses move_cursor and scrolls normally.
            table.set_reactive(DataTable.cursor_coordinate, Coordinate(0, 0))
            wanted = {item["key"] for item in sorted_data}
            for key in list(table.rows):
                if key.value not in wanted:
                    table.remove_row(key)
            columns = tuple(table.columns)
            order = {}
            for position, item in enumerate(sorted_data):
                def number(field: str) -> str:
                    value = item[field]
                    if value is None:
                        return "—"
                    return ("≥" if field in item["partial"] else "") + f"{value:.1f}"

                def speed(field: str) -> str:
                    return ("≥" if field in item["partial"] and item[field] is not None else "") + format_speed(item[field])

                pid_style = f"bold {self.theme_colors['error']}" if item["key"] == self.tracked_key else self.theme_colors["accent"]
                cells = (
                    Text(str(item["pid"]), style=pid_style),
                    Text(str(item["count"])),
                    Text(number("cpu"), style=self.theme_colors["warning"]),
                    Text(number("mem_perc"), style=self.theme_colors["success"]),
                    Text(number("ram_mb")),
                    Text(speed("read")), Text(speed("write")), Text(speed("disk")),
                    Text(("≥" if "cpu_time" in item["partial"] and item["cpu_time"] is not None else "") + item["time"]),
                    Text(item["full_cmd"] if self.show_full_cmd else item["short_cmd"]),
                )
                order[item["pid"]] = position
                if item["key"] in table.rows:
                    previous = table.get_row(item["key"])
                    for column, old, new in zip(columns, previous, cells):
                        if old != new:
                            table.update_cell(item["key"], column, new, update_width=True)
                else:
                    table.add_row(*cells, key=item["key"])
            # Representatives are distinct PIDs because groups partition the scan.
            table.sort(key=lambda cells: order[int(str(cells[0]))])
            table.set_reactive(DataTable.cursor_coordinate, Coordinate(min(rank, max(0, table.row_count - 1)), 0))
            table.scroll_to(x=x, y=y, animate=False)
            view = "Programs" if self.grouped else "PIDs"
            scope = "commands + args" if self.show_full_cmd else "names"
            mode = self.sort_by.upper() + (f" | Filter: {scope}" if self.search_query.strip() else "")
            self.query_one("#main-box").border_subtitle = (
                f" {view} | Sort: {mode} | ≥ partial | Refresh: {self.refresh_interval}s "
            )

    # =========================================================================
    # EVENT DISPATCH & KEYBIND ARCHITECTURE
    # =========================================================================
    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        row = self.view_rows.get(event.row_key.value)
        if row is None:
            return
        self.query_one(ProcessTable).show_cursor = True
        if self.tracked_key == row["key"]:
            self._clear_tracking()
        elif row["created"] is not None or row["key"].startswith("exe:"):
            self.tracked_key = row["key"]
            self.last_alert_time = float("-inf")
            self.tracked_threshold_mb = None
            self.tracked_ram_history.clear()
            self.tracked_cpu_history.clear()
            self.query_one("#panopticon").add_class("-active")
            self._update_panopticon_dom(row)
        self.refresh_rows()

    def action_select_row(self) -> None:
        self.query_one(ProcessTable).action_select_cursor()

    def action_toggle_grouping(self) -> None:
        self.grouped = not self.grouped
        self._clear_tracking()
        self.refresh_rows(reset_position=True)

    def action_toggle_highlight(self) -> None:
        table = self.query_one(ProcessTable)
        if table.show_cursor:
            table.show_cursor = False
        else:
            table.enable_highlight()

    def action_jump_down(self) -> None:
        table = self.query_one(DataTable)
        if table.has_focus and table.row_count > 0:
            if table.show_cursor:
                table.move_cursor(row=min(table.cursor_row + 10, table.row_count - 1))
            else:
                table.scroll_relative(y=10, animate=False)

    def action_jump_up(self) -> None:
        table = self.query_one(DataTable)
        if table.has_focus and table.row_count > 0:
            if table.show_cursor:
                table.move_cursor(row=max(table.cursor_row - 10, 0))
            else:
                table.scroll_relative(y=-10, animate=False)

    def action_cursor_down(self) -> None:
        table = self.query_one(DataTable)
        if table.has_focus: table.action_cursor_down()

    def action_cursor_up(self) -> None:
        table = self.query_one(DataTable)
        if table.has_focus: table.action_cursor_up()

    def action_scroll_top(self) -> None:
        table = self.query_one(DataTable)
        if table.has_focus and table.row_count > 0: table.move_cursor(row=0)

    def action_scroll_bottom(self) -> None:
        table = self.query_one(DataTable)
        if table.has_focus and table.row_count > 0: table.move_cursor(row=table.row_count - 1)

    def action_toggle_sort(self) -> None:
        modes = ("ram", "cpu", "disk")
        self.action_sort_metric(modes[(modes.index(self.sort_by) + 1) % 3] if self.sort_by in modes else "ram")

    def action_sort_metric(self, metric: str) -> None:
        self.sort_by = metric
        self.refresh_rows(reset_position=True)

    def action_toggle_cmd(self) -> None:
        self.show_full_cmd = not self.show_full_cmd
        self.refresh_rows()

    def action_focus_search(self) -> None:
        search_input = self.query_one("#search_input", Input)
        self.query_one("#search-bar").add_class("-active")
        search_input.focus()

    def action_clear_search(self) -> None:
        if isinstance(self.screen, ModalScreen):
            self.screen.dismiss(None)
            return
        search_input = self.query_one("#search_input", Input)
        search_bar = self.query_one("#search-bar")
        if search_bar.has_class("-active"):
            if self._search_task:
                self._search_task.cancel()
            search_bar.remove_class("-active")
            search_input.value = ""
            self.search_query = ""
            self.query_one(DataTable).focus()
            self.refresh_rows(reset_position=True)

    @work
    async def action_set_threshold(self) -> None:
        if self.tracked_key is None or isinstance(self.screen, ModalScreen):
            return
        tracked = self.tracked_key
        threshold_value = await self.push_screen_wait(SetThresholdScreen())
        if threshold_value is not None and tracked == self.tracked_key:
            self.tracked_threshold_mb = threshold_value
            self.update_table()

    def action_show_shortcuts(self) -> None:
        if isinstance(self.screen, ShortcutsInfoScreen):
            self.screen.dismiss(None)
        elif not isinstance(self.screen, ModalScreen):
            self.push_screen(ShortcutsInfoScreen())

    def on_unmount(self) -> None:
        if self._search_task:
            self._search_task.cancel()

    @on(Input.Changed, "#search_input")
    async def on_input_changed(self, event: Input.Changed) -> None:
        if self._search_task:
            self._search_task.cancel()
        self._search_task = asyncio.create_task(self._debounced_search(event.value))

    async def _debounced_search(self, value: str) -> None:
        try:
            await asyncio.sleep(0.15)
            self.search_query = value
            self.refresh_rows(reset_position=True)
        except asyncio.CancelledError:
            pass

    @on(Input.Submitted, "#search_input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter must commit the text even when pressed before the debounce fires.
        if self._search_task:
            self._search_task.cancel()
        self.search_query = event.value
        self.refresh_rows(reset_position=True)
        self.query_one(DataTable).focus()

if __name__ == "__main__":
    app = DuskyMonitorApp(cli_config)
    previous_handler = signal.signal(signal.SIGTERM, lambda _number, _frame: app.exit())
    try:
        app.run()
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
    sys.exit(app.return_code or 0)
