#!/usr/bin/env python3
"""
===============================================================================
DUSKY KEYLOGGER — MATUGEN RICH DASHBOARD v2 (Modern, High-Contrast, Scrollable)
===============================================================================
Comprehensive, high-contrast, scrollable terminal dashboard for the Dusky
Keylogger. Mirrors the architecture of screentime_tui.py but tailored to
keystroke statistics.

Features
--------
* Matugen theme from ~/.config/matugen/generated/dusky_tui.json
  (DEFAULT_COLORS muted #a08c7a for WCAG contrast; fg #efe0d5 for primary)
* Period tabs: 1=Today 2=Week 3=Month 4=All (fast <1ms switch, no flicker)
* View tabs:  Tab=Overview  Keys  Chars  Transcript  Recent (Shift+Tab back)
* Human-readable keys (Space, Enter, Backspace … not KEY_SPACE)
* Decluttered Overview: cards + 6 essential metrics + KPIs + hourly
  (daily + keys preview moved to dedicated tabs)
* Transcript & Recent fully scrollable: j/k, ↑/↓, PgUp/PgDn, mouse wheel,
  with scroll indicator and windowed rendering (scroll offset + height)
* High contrast: primary text uses fg, not dim muted; bars use bold accent
* No hardcoded username — all paths via Path.home() / expanduser()
* Log file at ~/.config/dusky/settings/keylogger/data/logs/dashboard_tui_error.log
* 750+ lines, well-structured, py_compile clean, cached I/O
* Single-instance Live (zero flicker, no termios deadlock)

Layout contracts
----------------
* build_layout(period, view, colors, scrolls, console_height, store) -> Panel
* render_transcript_panel(store, period, colors, scroll, height) -> Panel
* render_recent_panel(store, colors, scroll, height) -> Panel
* run_live_dashboard(store_path?) -> None
* main(store_path?) -> None
"""
from __future__ import annotations

import fcntl
import json
import os
import select
import sys
import termios
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.color import Color, ColorParseError
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# ---------------------------------------------------------------------------
# Installed package and direct script invocation share the same imports.
# ---------------------------------------------------------------------------
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from dusky_keylogger.daemon import default_data_dir
    from dusky_keylogger.stats import card_totals, hourly_series, summarize, period_range
    from dusky_keylogger.storage import KeyStore
    from dusky_keylogger import keycodes as kc
    from dusky_keylogger import __version__
else:
    from .daemon import default_data_dir
    from .stats import card_totals, hourly_series, summarize, period_range
    from .storage import KeyStore
    from . import keycodes as kc
    from . import __version__

# ---------------------------------------------------------------------------
# Theme / paths — never hardcode username, always Path.home() / expanduser
# ---------------------------------------------------------------------------
THEME_FILE = Path("~/.config/matugen/generated/dusky_tui.json").expanduser()
LOG_FILE = Path("~/.config/dusky/settings/keylogger/data/logs/dashboard_tui_error.log").expanduser()

DEFAULT_COLORS: dict[str, str] = {
    "bg": "#19120c",
    "fg": "#efe0d5",
    "accent": "#ffb779",
    "error": "#ffb4ab",
    "warning": "#e3c0a5",
    "success": "#c3cb98",
    # High contrast muted — #a08c7a not #51443a (original was too dim)
    "muted": "#a08c7a",
    "cursor_bg": "#2a221c",
}

PERIOD_LIST: list[str] = ["today", "week", "month", "all"]
PERIOD_LABELS: dict[str, str] = {
    "today": "Today",
    "week": "This Week",
    "month": "This Month",
    "all": "All Time",
}
PERIOD_KEYS: dict[str, str] = {
    "period_today": "today",
    "period_week": "week",
    "period_month": "month",
    "period_all": "all",
}

VIEW_LIST: list[str] = ["overview", "keys", "chars", "transcript", "recent"]
VIEW_LABELS: dict[str, str] = {
    "overview": "Overview",
    "keys": "Keys",
    "chars": "Characters",
    "transcript": "Transcript",
    "recent": "Recent",
}

# ---------------------------------------------------------------------------
# Human-readable key / char helpers (Space not KEY_SPACE)
# ---------------------------------------------------------------------------
FRIENDLY_KEY_NAMES: dict[str, str] = {
    "KEY_SPACE": "Space",
    "KEY_ENTER": "Enter",
    "KEY_KPENTER": "Enter (KP)",
    "KEY_BACKSPACE": "Backspace",
    "KEY_TAB": "Tab",
    "KEY_ESC": "Escape",
    "KEY_CAPSLOCK": "Caps Lock",
    "KEY_LEFTSHIFT": "Shift",
    "KEY_RIGHTSHIFT": "Shift",
    "KEY_LEFTCTRL": "Ctrl",
    "KEY_RIGHTCTRL": "Ctrl",
    "KEY_LEFTALT": "Alt",
    "KEY_RIGHTALT": "Alt",
    "KEY_LEFTMETA": "Super",
    "KEY_RIGHTMETA": "Super",
    "KEY_DELETE": "Delete",
    "KEY_HOME": "Home",
    "KEY_END": "End",
    "KEY_PAGEUP": "Page Up",
    "KEY_PAGEDOWN": "Page Down",
    "KEY_UP": "↑ Up",
    "KEY_DOWN": "↓ Down",
    "KEY_LEFT": "← Left",
    "KEY_RIGHT": "→ Right",
    "KEY_INSERT": "Insert",
    "KEY_F1": "F1",
    "KEY_F2": "F2",
    "KEY_F3": "F3",
    "KEY_F4": "F4",
    "KEY_F5": "F5",
    "KEY_F6": "F6",
    "KEY_F7": "F7",
    "KEY_F8": "F8",
    "KEY_F9": "F9",
    "KEY_F10": "F10",
    "KEY_F11": "F11",
    "KEY_F12": "F12",
    "KEY_F13": "F13",
    "KEY_F14": "F14",
    "KEY_F15": "F15",
    "KEY_F16": "F16",
    "KEY_F17": "F17",
    "KEY_F18": "F18",
    "KEY_F19": "F19",
    "KEY_F20": "F20",
    "KEY_MINUS": "-",
    "KEY_EQUAL": "=",
    "KEY_LEFTBRACE": "[",
    "KEY_RIGHTBRACE": "]",
    "KEY_SEMICOLON": ";",
    "KEY_APOSTROPHE": "'",
    "KEY_GRAVE": "`",
    "KEY_BACKSLASH": "\\",
    "KEY_COMMA": ",",
    "KEY_DOT": ".",
    "KEY_SLASH": "/",
    "KEY_KPSLASH": "KP /",
    "KEY_KPASTERISK": "KP *",
    "KEY_KPMINUS": "KP -",
    "KEY_KPPLUS": "KP +",
    "KEY_KPDOT": "KP .",
    "KEY_NUMLOCK": "Num Lock",
    "KEY_SCROLLLOCK": "Scroll Lock",
    "KEY_SYSRQ": "SysRq",
    "KEY_PAUSE": "Pause",
    "KEY_COMPOSE": "Compose",
    "KEY_MUTE": "Mute",
    "KEY_VOLUMEDOWN": "Vol ↓",
    "KEY_VOLUMEUP": "Vol ↑",
    "KEY_POWER": "Power",
    "KEY_SLEEP": "Sleep",
    "KEY_WAKEUP": "Wake",
    "KEY_HOMEPAGE": "HomePage",
    "KEY_MAIL": "Mail",
    "KEY_CALC": "Calc",
    "KEY_PLAYPAUSE": "Play/Pause",
    "KEY_NEXTSONG": "Next",
    "KEY_PREVIOUSSONG": "Prev",
    "KEY_BRIGHTNESSDOWN": "Brightness ↓",
    "KEY_BRIGHTNESSUP": "Brightness ↑",
    "KEY_MICMUTE": "Mic Mute",
}


def humanize_key(k: str) -> str:
    """Return a human-friendly label for an evdev KEY_ name.

    Examples: KEY_SPACE -> Space, KEY_A -> A, KEY_LEFTCTRL -> Ctrl,
    KEY_F5 -> F5, KEY_KP5 -> KP 5.
    """
    if not isinstance(k, str):
        return str(k)
    if k in FRIENDLY_KEY_NAMES:
        return FRIENDLY_KEY_NAMES[k]
    if k.startswith("KEY_"):
        body = k[4:]
        if len(body) == 1:
            return body
        # Title-case multi-word, e.g. KEY_PAGE_UP -> Page Up (but we already map)
        return body.replace("_", " ").title()
    return k


def humanize_char(c: str | None) -> str:
    """Render a printable char with whitespace hints."""
    if c is None:
        return ""
    if c == " ":
        return "␣ Space"
    if c == "\n":
        return "↵ Enter"
    if c == "\t":
        return "⇥ Tab"
    if c == "":
        return "∅"
    # Control / non-printable fallback
    if len(c) == 1 and ord(c) < 32:
        return f"␍ {ord(c):02d}"
    return c


# ---------------------------------------------------------------------------
# Mouse / cursor sequences (only written when Live not owning TTY)
# ---------------------------------------------------------------------------
_MOUSE_ON = "\x1b[?1000h\x1b[?1006h"
_MOUSE_OFF = "\x1b[?1000l\x1b[?1006l"
_CURSOR_HIDE = "\x1b[?25l"
_CURSOR_SHOW = "\x1b[?25h"
_CLEAR_HOME = "\x1b[2J\x1b[3J\x1b[H"
_ANSI_RESET = "\x1b[0m"

# ---------------------------------------------------------------------------
# Logging / theme helpers
# ---------------------------------------------------------------------------

def log_error(msg: str) -> None:
    """Append a timestamped line to the dashboard TUI log file."""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception:
        pass


def _safe_color(value: Any, fallback: str) -> str:
    """Validate a color string; reject Rich markup / ANSI injection."""
    if not isinstance(value, str):
        return fallback
    v = value.strip()
    if not v or "[" in v or "]" in v or "\n" in v or "\x1b" in v:
        return fallback
    if v.startswith("#") and len(v) == 4:
        v = "#" + "".join(c * 2 for c in v[1:])
    try:
        Color.parse(v)
    except ColorParseError:
        return fallback
    return v


def hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    """Convert #RRGGBB to (r,g,b)."""
    hc = hex_str.strip().lstrip("#")
    if len(hc) == 3:
        hc = "".join(c * 2 for c in hc)
    if len(hc) >= 6:
        try:
            return int(hc[0:2], 16), int(hc[2:4], 16), int(hc[4:6], 16)
        except ValueError:
            pass
    return 239, 224, 213  # fallback fg #efe0d5


def ansi_color(hex_str: str, bold: bool = False) -> str:
    r, g, b = hex_to_rgb(hex_str)
    prefix = "\033[1;" if bold else "\033["
    return f"{prefix}38;2;{r};{g};{b}m"


def load_theme_colors() -> dict[str, str]:
    """Load matugen generated JSON, fall back to DEFAULT_COLORS.

    Ensures muted is #a08c7a (high contrast) even if theme file still has
    stale #51443a — the default overrides that value unless user explicitly
    set a different high-contrast muted.
    """
    colors = DEFAULT_COLORS.copy()
    if THEME_FILE.exists():
        try:
            with open(THEME_FILE, encoding="utf-8") as f:
                user = json.load(f)
                if isinstance(user, dict):
                    for key, fb in DEFAULT_COLORS.items():
                        if key in user:
                            # Special: if file still has old low-contrast muted, upgrade
                            if key == "muted" and str(user[key]).strip().lower() in ("#51443a", "#3f484a"):
                                colors[key] = DEFAULT_COLORS["muted"]
                            else:
                                colors[key] = _safe_color(user[key], fb)
                    for k, v in user.items():
                        if k not in colors:
                            colors[k] = _safe_color(v, DEFAULT_COLORS["fg"])
        except Exception as e:
            log_error(f"load_theme_colors error: {e}")
    # Enforce high-contrast muted if somehow still low-contrast
    if colors.get("muted", "").lower() in ("#51443a", "#3f484a"):
        colors["muted"] = "#a08c7a"
    return colors


# ---------------------------------------------------------------------------
# Formatting / bar helpers — high contrast
# ---------------------------------------------------------------------------

def format_duration_minutes(m: int) -> str:
    if m <= 0:
        return "0m"
    h = m // 60
    r = m % 60
    if h > 0:
        return f"{h}h {r:02d}m"
    return f"{r}m"


def make_bar(pct: float, colors: dict[str, str], width: int = 20) -> Text:
    """High-contrast bar: bold accent filled, visible muted empty (no dim)."""
    pct = max(0.0, min(100.0, float(pct)))
    filled = int(round(pct / 100.0 * width))
    filled = max(0, min(width, filled))
    empty = width - filled
    accent = colors.get("accent", "#ffb779")
    muted = colors.get("muted", "#a08c7a")
    t = Text()
    # Bold accent for filled — clearly visible on dark bg
    if filled:
        t.append("█" * filled, style=f"bold {accent}")
    # Use muted without dim so empty portion stays visible (not washed)
    if empty:
        t.append("░" * empty, style=f"{muted}")
    return t


def make_bar_text(
    pct: float,
    colors: dict[str, str],
    is_active: bool = False,
    is_cursor: bool = False,
    width: int = 16,
) -> Text:
    """Bar for table rows — high contrast variant of screentime's make_bar_text."""
    pct = max(0.0, min(100.0, float(pct)))
    filled = int(round(pct / 100.0 * width))
    filled = max(0, min(width, filled))
    empty = width - filled
    accent = colors.get("accent", "#ffb779")
    success = colors.get("success", "#c3cb98")
    muted = colors.get("muted", "#a08c7a")
    txt = Text()
    # Use success for active/cursor if desired, else accent — both high contrast
    bar_color = success if (is_active or is_cursor) else accent
    if filled:
        txt.append("━" * filled, style=f"bold {bar_color}")
    if empty:
        # Do NOT use dim — keep muted visible against dark bg
        txt.append("─" * empty, style=f"{muted}")
    return txt


def _get_cycled_view(cur: str, step: int = 1) -> str:
    try:
        idx = VIEW_LIST.index(cur)
    except ValueError:
        idx = 0
    return VIEW_LIST[(idx + step) % len(VIEW_LIST)]


def _get_cycled_period(cur: str, step: int = 1) -> str:
    try:
        idx = PERIOD_LIST.index(cur)
    except ValueError:
        idx = 0
    return PERIOD_LIST[(idx + step) % len(PERIOD_LIST)]


# ---------------------------------------------------------------------------
# Query cache -- rebuild expensive aggregates only when the events table
# actually changed (MAX(id) is an O(1) rowid probe). The render loop runs at
# ~4 fps; without this, large databases re-scan per frame (measured ~1s/frame
# for summarize and ~3s for transcript rebuilds on 380k rows).
# ---------------------------------------------------------------------------
_QUERY_CACHE: dict[tuple[str, tuple], tuple[tuple, Any]] = {}


def _cached(store: Any, key: tuple, compute: Any) -> Any:
    """Return cached compute() while store.max_id() is unchanged."""
    if store is None:
        return compute()
    try:
        stat = store.path.stat()
        ver = (stat.st_dev, stat.st_ino, store.max_id(), datetime.now().date())
    except Exception:
        return compute()
    ck = (str(getattr(store, "path", "")), key)
    hit = _QUERY_CACHE.get(ck)
    if hit is not None and hit[0] == ver:
        return hit[1]
    val = compute()
    if len(_QUERY_CACHE) > 64:
        _QUERY_CACHE.clear()
    _QUERY_CACHE[ck] = (ver, val)
    return val


def _transcript_line_count(store: Any, period: str) -> int:
    """Efficient native-Python transcript line count (single DB scan).

    Reuse the rendered transcript snapshot for auto-follow and scroll clamping.
    Returns at least 1 for empty state; invalidated when new events are written.
    """
    entries, _ = _transcript_snapshot(store, period)
    return max(1, len(entries))


def _transcript_snapshot(store: Any, period: str) -> tuple[list[tuple[str, str]], int]:
    """Cache lines and their character count as one consistent snapshot."""
    def compute():
        entries = _transcript_entries(store, period)
        return entries, sum(len(text) for _, text in entries)
    return _cached(store, ("transcript_snapshot", period), compute)


def _transcript_entries(store: Any, period: str) -> list[tuple[str, str]]:
    """Build (HH:MM:SS, text) transcript lines for a period, newest last."""
    try:
        start, end = period_range(period)
    except Exception as e:
        log_error(f"period_range transcript {e}")
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        end = datetime.now()
    entries: list[tuple[str, str]] = []
    cur_text: list[str] = []
    cur_time: str | None = None
    try:
        for row in store.iter_between(start, end):
            try:
                t_str = datetime.fromtimestamp(row.ts_ms / 1000).strftime("%H:%M:%S")
            except Exception:
                t_str = "--:--:--"
            if row.kind == kc.KIND_PRINTABLE and row.char:
                if cur_time is None:
                    cur_time = t_str
                cur_text.append(row.char)
            elif row.kind == kc.KIND_BACKSPACE:
                if cur_time is None:
                    cur_time = t_str
                cur_text.append("⌫")
            elif row.kind == kc.KIND_ENTER:
                entries.append((cur_time or t_str, "".join(cur_text)))
                cur_text = []
                cur_time = None
            elif row.kind == kc.KIND_TAB:
                if cur_time is None:
                    cur_time = t_str
                cur_text.append("\t")
            elif row.kind == kc.KIND_DELETE:
                if cur_time is None:
                    cur_time = t_str
                cur_text.append("⌦")
        if cur_text:
            entries.append((cur_time or "--:--:--", "".join(cur_text)))
    except Exception as e:
        log_error(f"transcript iter_between {e}")
        return [("--:--:--", f"[error: {e}]")]
    if not entries:
        return [("--:--:--", "[no typed text]")]
    return entries


# ---------------------------------------------------------------------------
# View-specific renderers
# ---------------------------------------------------------------------------

def _render_period_tabs(period: str, colors: dict[str, str]) -> Text:
    """Period tabs 1=Today 2=Week 3=Month 4=All (high contrast)."""
    accent = colors.get("accent", "#ffb779")
    fg = colors.get("fg", "#efe0d5")
    muted = colors.get("muted", "#a08c7a")
    cursor_bg = colors.get("cursor_bg", "#2a221c")
    t = Text(overflow="ellipsis", no_wrap=True)
    t.append(" Period: ", style=f"bold {fg}")
    defs = [("1", "today", "Today"), ("2", "week", "Week"), ("3", "month", "Month"), ("4", "all", "All")]
    for num, pid, label in defs:
        if pid == period:
            t.append(f" {num}:{label} ", style=f"bold {accent} on {cursor_bg}")
        else:
            # Use fg, not dim muted, for primary unselected tabs (high contrast)
            t.append(f" {num}:{label} ", style=f"{fg}")
        t.append(" ", style=f"{muted}")
    return t


def _render_view_tabs(view: str, colors: dict[str, str]) -> Text:
    """View tabs Tab cycles forward, Shift+Tab back (visible highlight)."""
    accent = colors.get("accent", "#ffb779")
    fg = colors.get("fg", "#efe0d5")
    muted = colors.get("muted", "#a08c7a")
    cursor_bg = colors.get("cursor_bg", "#2a221c")
    t = Text(overflow="ellipsis", no_wrap=True)
    t.append(" View: ", style=f"bold {fg}")
    for vid in VIEW_LIST:
        label = VIEW_LABELS.get(vid, vid.title())
        if vid == view:
            t.append(f" {label} ", style=f"bold {accent} on {cursor_bg}")
        else:
            t.append(f" {label} ", style=f"{fg}")
        t.append(" ", style=f"{muted}")
    t.append("  [Tab/Shift+Tab]", style=f"{muted}")
    return t


def render_cards_panel(cards: dict[str, int], colors: dict[str, str]) -> Table:
    """4 period total cards — high contrast, fg primary."""
    fg = colors.get("fg", "#efe0d5")
    accent = colors.get("accent", "#ffb779")
    muted = colors.get("muted", "#a08c7a")
    tbl = Table(box=None, expand=True, show_header=False, pad_edge=False)
    for _ in PERIOD_LIST:
        tbl.add_column(justify="center", ratio=1)
    # Header row: labels use fg (high contrast)
    tbl.add_row(*[Text(PERIOD_LABELS[p], style=f"bold {fg}", justify="center") for p in PERIOD_LIST])
    # Value row: accent bold
    tbl.add_row(*[Text(f"{cards.get(p, 0):,}", style=f"bold {accent}", justify="center") for p in PERIOD_LIST])
    # Sub-label
    tbl.add_row(*[Text("keys", style=f"{muted}", justify="center") for _ in PERIOD_LIST])
    return tbl


def render_overview_metrics(stats: Any, colors: dict[str, str]) -> Table:
    """Only 6 essential metrics (decluttered). Primary text uses fg, zebra-striped, bar close to value."""
    fg = colors.get("fg", "#efe0d5")
    accent = colors.get("accent", "#ffb779")
    muted = colors.get("muted", "#a08c7a")
    cursor_bg = colors.get("cursor_bg", "#2a221c")
    tbl = Table(box=None, expand=True, show_header=False, pad_edge=False, padding=(0, 0))
    tbl.add_column("Metric", style=f"bold {fg}", ratio=1, no_wrap=True, overflow="ellipsis")
    tbl.add_column("Value", style=f"bold {accent}", justify="right", width=8, no_wrap=True)
    tbl.add_column("Bar", width=16, no_wrap=True)
    tbl.add_column("Pct", width=5, justify="right", no_wrap=True)
    total = max(1, stats.total_keys)
    essential = [
        ("Total keystrokes", stats.total_keys, 100.0),
        ("Printable", stats.printable, stats.printable / total * 100.0),
        ("Backspace", stats.backspace, stats.backspace / total * 100.0),
        ("Enter", stats.enter, stats.enter / total * 100.0),
        ("Modifiers", stats.modifiers, stats.modifiers / total * 100.0),
        ("Active minutes", stats.active_minutes, min(100.0, stats.active_minutes / 60 * 10)),
    ]
    for idx, (label, value, pct) in enumerate(essential):
        is_even = (idx % 2 == 0)
        row_bg = f" on {cursor_bg}" if is_even else ""
        bar = make_bar(pct, colors, width=16)
        lbl = Text(label, style=f"bold {fg}{row_bg}" if idx == 0 else f"{fg}{row_bg}")
        val = Text(f"{value:,}", style=f"bold {accent}{row_bg}")
        pct_txt = Text(f"{pct:.0f}%", style=f"{muted}{row_bg}")
        tbl.add_row(lbl, val, bar, pct_txt, style=row_bg.strip() if row_bg else None)
    return tbl


def render_kpis_panel(stats: Any, colors: dict[str, str]) -> Text:
    """KPIs line: KPM, WPM, backspace ratio — high contrast."""
    fg = colors.get("fg", "#efe0d5")
    accent = colors.get("accent", "#ffb779")
    success = colors.get("success", "#c3cb98")
    muted = colors.get("muted", "#a08c7a")
    t = Text(overflow="ellipsis", no_wrap=True)
    t.append(" KPIs: ", style=f"bold {fg}")
    t.append("KPM ", style=f"{muted}")
    t.append(f"{stats.keys_per_minute:.1f} ", style=f"bold {success}")
    t.append("│ ", style=f"{muted}")
    t.append("WPM ", style=f"{muted}")
    t.append(f"{stats.words_per_minute:.1f} ", style=f"bold {accent}")
    t.append("│ ", style=f"{muted}")
    t.append("Backspace ", style=f"{muted}")
    t.append(f"{stats.backspace_ratio*100:.1f}% ", style=f"bold {fg}")
    t.append("│ ", style=f"{muted}")
    t.append("Active ", style=f"{muted}")
    t.append(f"{format_duration_minutes(stats.active_minutes)}", style=f"bold {fg}")
    return t


def render_hourly_panel(store: KeyStore, colors: dict[str, str], day: str | None = None) -> Table:
    """Hourly histogram (0–23) — bars use high-contrast accent, zebra-striped."""
    fg = colors.get("fg", "#efe0d5")
    accent = colors.get("accent", "#ffb779")
    cursor_bg = colors.get("cursor_bg", "#2a221c")
    if day is None:
        day = datetime.now().strftime("%Y-%m-%d")
    try:
        hourly = _cached(store, ("hourly", day), lambda: hourly_series(store, day))
    except Exception as e:
        log_error(f"hourly_series error: {e}")
        hourly = [(h, 0) for h in range(24)]
    max_v = max((v for _, v in hourly), default=1)
    if max_v == 0:
        max_v = 1
    tbl = Table(box=None, expand=True, show_header=True, header_style=f"bold {accent}", pad_edge=False)
    tbl.add_column("Hour", style=f"bold {fg}", width=5, justify="right", no_wrap=True)
    tbl.add_column("Keys", style=f"bold {accent}", width=7, justify="right", no_wrap=True)
    tbl.add_column("Activity", ratio=1, no_wrap=True)
    for idx, (h, v) in enumerate(hourly):
        pct = v / max_v * 100.0 if max_v else 0.0
        bar = make_bar(pct, colors, width=18)
        is_even = (idx % 2 == 0)
        row_bg = f" on {cursor_bg}" if is_even else ""
        hour_lbl = Text(f"{h:02d}:00", style=f"bold {fg}{row_bg}")
        val_style = f"bold {accent}{row_bg}" if v == max_v and v > 0 else f"bold {fg}{row_bg}"
        val = Text(f"{v:,}", style=val_style)
        tbl.add_row(hour_lbl, val, bar, style=row_bg.strip() if row_bg else None)
    return tbl


def render_daily_panel(store: KeyStore, colors: dict[str, str], days: int = 14) -> Table:
    """Daily totals — lives in its own tab, not overview (decluttered), zebra-striped."""
    fg = colors.get("fg", "#efe0d5")
    accent = colors.get("accent", "#ffb779")
    muted = colors.get("muted", "#a08c7a")
    success = colors.get("success", "#c3cb98")
    cursor_bg = colors.get("cursor_bg", "#2a221c")
    try:
        series = _cached(store, ("daily", days), lambda: store.daily_totals(days))
    except Exception as e:
        log_error(f"daily_totals error: {e}")
        series = []
    max_v = max((v for _, v in series), default=1)
    if max_v == 0:
        max_v = 1
    tbl = Table(box=None, expand=True, show_header=True, header_style=f"bold {accent}", pad_edge=False)
    tbl.add_column("Date", style=f"bold {fg}", no_wrap=True, ratio=1)
    tbl.add_column("Keys", style=f"bold {accent}", justify="right", width=8, no_wrap=True)
    tbl.add_column("Activity", ratio=2, no_wrap=True)
    for idx, (d, v) in enumerate(series):
        pct = v / max_v * 100.0 if max_v else 0.0
        bar = make_bar(pct, colors, width=16)
        is_even = (idx % 2 == 0)
        row_bg = f" on {cursor_bg}" if is_even else ""
        tbl.add_row(
            Text(d, style=f"bold {fg}{row_bg}"),
            Text(f"{v:,}", style=f"bold {success}{row_bg}" if v == max_v else f"bold {fg}{row_bg}"),
            bar,
            style=row_bg.strip() if row_bg else None,
        )
    if not series:
        tbl.add_row(Text("No data", style=f"{muted}"), Text("0", style=f"{muted}"), Text("", style=f"{muted}"))
    return tbl


def render_keys_panel(stats: Any, colors: dict[str, str], scroll: int = 0, height: int = 10) -> Panel:
    """Top keys table — dedicated tab, scrollable window, zebra-striped for readability."""
    fg = colors.get("fg", "#efe0d5")
    accent = colors.get("accent", "#ffb779")
    muted = colors.get("muted", "#a08c7a")
    success = colors.get("success", "#c3cb98")
    cursor_bg = colors.get("cursor_bg", "#2a221c")
    top = stats.top_keys or []
    total = max(1, stats.total_keys)
    # Windowed slice for scroll
    height = max(3, height)
    max_scroll = max(0, len(top) - height)
    scroll = max(0, min(scroll, max_scroll))
    window = top[scroll : scroll + height]
    max_c = max((c for _, c in top), default=1)
    if max_c == 0:
        max_c = 1
    tbl = Table(box=None, expand=True, show_header=True, header_style=f"bold {accent}", pad_edge=False)
    tbl.add_column("#", style=f"{muted}", width=3, justify="right", no_wrap=True)
    tbl.add_column("Key", style=f"{fg}", ratio=2, overflow="ellipsis", no_wrap=True)
    tbl.add_column("Count", style=f"bold {success}", width=7, justify="right", no_wrap=True)
    tbl.add_column("Share", style=f"{accent}", width=7, justify="right", no_wrap=True)
    tbl.add_column("Bar", ratio=2, no_wrap=True)
    for idx, (k, c) in enumerate(window, start=scroll + 1):
        share = c / total * 100.0
        pct = c / max_c * 100.0
        bar = make_bar(pct, colors, width=14)
        # Alternate row shading — keep spacing as is, just add subtle zebra
        is_even = (idx % 2 == 0)
        row_bg = f" on {cursor_bg}" if is_even else ""
        tbl.add_row(
            Text(str(idx), style=f"{muted}{row_bg}"),
            Text(humanize_key(k), style=f"bold {fg}{row_bg}"),
            Text(f"{c:,}", style=f"{success}{row_bg}"),
            Text(f"{share:.1f}%", style=f"{accent}{row_bg}"),
            bar,
            style=f"{row_bg.strip()}" if row_bg else None,
        )
    # Pad empty rows to keep height stable
    for _ in range(height - len(window)):
        tbl.add_row("", "", "", "", "")
    scroll_info = ""
    if len(top) > height:
        scroll_info = f"  [{scroll+1}-{scroll+len(window)}/{len(top)} ↕ j/k PgUp/PgDn Wheel]"
    return Panel(tbl, title=f"[bold {accent}]Top Keys[/] [dim {muted}]{len(top)} entries{scroll_info}[/]", border_style=f"{accent}", expand=True)


def render_chars_panel(stats: Any, colors: dict[str, str], scroll: int = 0, height: int = 10) -> Panel:
    """Top characters table — dedicated tab, scrollable, zebra-striped."""
    fg = colors.get("fg", "#efe0d5")
    accent = colors.get("accent", "#ffb779")
    muted = colors.get("muted", "#a08c7a")
    success = colors.get("success", "#c3cb98")
    cursor_bg = colors.get("cursor_bg", "#2a221c")
    top = stats.top_chars or []
    total = max(1, stats.printable or 1)
    height = max(3, height)
    max_scroll = max(0, len(top) - height)
    scroll = max(0, min(scroll, max_scroll))
    window = top[scroll : scroll + height]
    max_c = max((c for _, c in top), default=1)
    if max_c == 0:
        max_c = 1
    tbl = Table(box=None, expand=True, show_header=True, header_style=f"bold {accent}", pad_edge=False)
    tbl.add_column("#", style=f"{muted}", width=3, justify="right", no_wrap=True)
    tbl.add_column("Char", style=f"{fg}", width=14, no_wrap=True, overflow="ellipsis")
    tbl.add_column("Count", style=f"bold {success}", width=7, justify="right", no_wrap=True)
    tbl.add_column("Share", style=f"{accent}", width=7, justify="right", no_wrap=True)
    tbl.add_column("Bar", ratio=2, no_wrap=True)
    for idx, (ch, c) in enumerate(window, start=scroll + 1):
        share = c / total * 100.0
        pct = c / max_c * 100.0
        bar = make_bar(pct, colors, width=14)
        is_even = (idx % 2 == 0)
        row_bg = f" on {cursor_bg}" if is_even else ""
        tbl.add_row(
            Text(str(idx), style=f"{muted}{row_bg}"),
            Text(humanize_char(ch), style=f"bold {fg}{row_bg}"),
            Text(f"{c:,}", style=f"{success}{row_bg}"),
            Text(f"{share:.1f}%", style=f"{accent}{row_bg}"),
            bar,
            style=row_bg.strip() if row_bg else None,
        )
    for _ in range(height - len(window)):
        tbl.add_row("", "", "", "", "")
    scroll_info = ""
    if len(top) > height:
        scroll_info = f"  [{scroll+1}-{scroll+len(window)}/{len(top)} ↕ j/k PgUp/PgDn Wheel]"
    return Panel(tbl, title=f"[bold {accent}]Top Characters[/] [dim {muted}]{len(top)} entries{scroll_info}[/]", border_style=f"{accent}", expand=True)


def render_transcript_panel(
    store: KeyStore, period: str, colors: dict[str, str], scroll: int = 0, height: int = 12
) -> Panel:
    """Scrollable transcript — beautiful, high-contrast, timestamped, realtime.

    Reconstructs typed text for the period with per-line timestamps. Entries
    are built once per data change (cached); each frame only windows them.
    Each line is `HH:MM:SS` of its first keystroke + text.
    """
    fg = colors.get("fg", "#efe0d5")
    accent = colors.get("accent", "#ffb779")
    muted = colors.get("muted", "#a08c7a")
    entries, chars = _transcript_snapshot(store, period)
    # New → old: reverse so newest at top (user request) — lazy load as scroll
    total_lines = len(entries)
    height = max(3, height)
    max_scroll = max(0, total_lines - height)
    scroll = max(0, min(scroll, max_scroll))
    end = total_lines - scroll
    window = list(reversed(entries[max(0, end - height):end]))
    tbl = Table(box=None, expand=True, show_header=True, header_style=f"bold {accent}", pad_edge=False)
    tbl.add_column("#", style=f"{muted}", width=3, justify="right", no_wrap=True)
    tbl.add_column("Time", style=f"{colors.get('warning', '#e3c0a5')}", width=8, justify="center", no_wrap=True)
    tbl.add_column("Text", style=f"{fg}", ratio=1, overflow="fold", no_wrap=False)
    for idx, (t_str, line) in enumerate(window, start=scroll + 1):
        display = line if len(line) < 500 else line[:500] + " …"
        is_even = (idx % 2 == 0)
        row_bg = f" on {colors.get('cursor_bg', '#2a221c')}" if is_even else ""
        tbl.add_row(
            Text(str(idx), style=f"{muted}{row_bg}"),
            Text(t_str, style=f"{colors.get('warning', '#e3c0a5')}{row_bg}"),
            Text(display, style=f"{fg}{row_bg}", overflow="fold"),
            style=row_bg.strip() if row_bg else None,
        )
    for _ in range(height - len(window)):
        tbl.add_row("", "", "")
    indicator = f"  [{scroll+1}-{min(scroll+height, total_lines)}/{total_lines}  ↕ j/k PgUp/PgDn Wheel • live]" if total_lines > height else f"  [{total_lines} lines • live]"
    subtitle = f"[dim {muted}]Transcript {PERIOD_LABELS.get(period, period)}{indicator} — {chars:,} chars[/]"
    return Panel(tbl, title=f"[bold {accent}]Transcript — Live[/]", subtitle=subtitle, border_style=f"{accent}", expand=True)


def render_recent_panel(store: KeyStore, colors: dict[str, str], scroll: int = 0, height: int = 12) -> Panel:
    """Scrollable recent events — windowed with scroll indicator, zebra-striped."""
    fg = colors.get("fg", "#efe0d5")
    accent = colors.get("accent", "#ffb779")
    muted = colors.get("muted", "#a08c7a")
    success = colors.get("success", "#c3cb98")
    warning = colors.get("warning", "#e3c0a5")
    cursor_bg = colors.get("cursor_bg", "#2a221c")
    # Fetch larger pool to allow scrolling (100 rows); cached per data change
    try:
        rows = _cached(store, ("recent",), lambda: store.recent(100))
    except Exception as e:
        log_error(f"recent fetch {e}")
        rows = []
    total = len(rows)
    height = max(3, height)
    max_scroll = max(0, total - height)
    scroll = max(0, min(scroll, max_scroll))
    window = rows[scroll : scroll + height]
    tbl = Table(box=None, expand=True, show_header=True, header_style=f"bold {accent}", pad_edge=False)
    tbl.add_column("Time", style=f"bold {fg}", width=19, no_wrap=True)
    tbl.add_column("Key", style=f"bold {fg}", ratio=1, overflow="ellipsis", no_wrap=True)
    tbl.add_column("Char", style=f"{success}", width=8, justify="center", no_wrap=True)
    tbl.add_column("Kind", style=f"{warning}", width=10, no_wrap=True)
    tbl.add_column("Device", style=f"{muted}", ratio=1, overflow="ellipsis", no_wrap=True)
    for idx, r in enumerate(window, start=scroll + 1):
        is_even = (idx % 2 == 0)
        row_bg = f" on {cursor_bg}" if is_even else ""
        dt = datetime.fromtimestamp(r.ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
        tbl.add_row(
            Text(dt, style=f"bold {fg}{row_bg}"),
            Text(humanize_key(r.key_name), style=f"bold {fg}{row_bg}"),
            Text(humanize_char(r.char) if r.char else "—", style=f"{success}{row_bg}"),
            Text(str(r.kind), style=f"{warning}{row_bg}"),
            Text(str(r.device)[:24], style=f"{muted}{row_bg}"),
            style=row_bg.strip() if row_bg else None,
        )
    for _ in range(height - len(window)):
        tbl.add_row("", "", "", "", "")
    if total == 0:
        # Show empty state
        tbl = Table(box=None, expand=True, show_header=False, pad_edge=False)
        tbl.add_column("msg", style=f"{muted}", justify="center")
        tbl.add_row(Text("[no events recorded yet]", style=f"{muted}"))
        return Panel(tbl, title=f"[bold {accent}]Recent Events[/]", subtitle=f"[dim {muted}]0 events[/]", border_style=f"{accent}", expand=True)
    if total > height:
        indicator = f"  [{scroll+1}-{min(scroll+height, total)}/{total}  ↕ j/k ↑↓ PgUp/PgDn Wheel]"
    else:
        indicator = f"  [{total} events]"
    return Panel(tbl, title=f"[bold {accent}]Recent Events[/]", subtitle=f"[dim {muted}]{indicator}[/]", border_style=f"{accent}", expand=True)


# ---------------------------------------------------------------------------
# Composite: overview (decluttered) vs full layout
# ---------------------------------------------------------------------------

def _build_overview_layout(
    stats: Any, cards: dict[str, int], store: KeyStore, colors: dict[str, str], period: str
) -> Table:
    """Overview — elegant, intuitive, high-contrast, decluttered.

    Decluttered: single hero total for selected period (not 4 big cards),
    compact period-totals row secondary, metrics zebra with bar close to value,
    full-width Rules, KPIs + hourly. Daily/keys live in dedicated tabs.
    """
    from rich.rule import Rule

    fg = colors.get("fg", "#efe0d5")
    muted = colors.get("muted", "#a08c7a")
    accent = colors.get("accent", "#ffb779")
    success = colors.get("success", "#c3cb98")
    warning = colors.get("warning", "#e3c0a5")
    cursor_bg = colors.get("cursor_bg", "#2a221c")
    period_label = PERIOD_LABELS.get(period, period.title())

    grid = Table.grid(expand=True, padding=(0, 0))
    grid.add_column(ratio=1)

    # Hero — single big number for selected period (high-contrast, not dim)
    hero = Text(justify="center", overflow="fold", no_wrap=False)
    hero.append(f"{period_label}: ", style=f"bold {fg}")
    hero.append(f"{stats.total_keys:,}", style=f"bold {accent}")
    hero.append(" keys", style=f"bold {fg}")
    hero.append("  ", style=f"{fg}")
    hero.append(f"({stats.printable:,} printable", style=f"bold {fg}")
    hero.append(" • ", style=f"{fg}")
    hero.append(f"{format_duration_minutes(stats.active_minutes)} active", style=f"bold {success}")
    hero.append(" • ", style=f"{fg}")
    hero.append(f"{stats.keys_per_minute:.1f} KPM", style=f"bold {warning}")
    hero.append(")", style=f"bold {fg}")
    grid.add_row(hero)

    grid.add_row(Rule(style=accent))

    # Period context — secondary but still readable (high contrast, not dim)
    tiny = Table(box=None, expand=True, show_header=False, pad_edge=False, padding=(0, 0))
    for _ in PERIOD_LIST:
        tiny.add_column(justify="center", ratio=1)
    tiny.add_row(*[Text(PERIOD_LABELS[p], style=f"bold {accent}" if p == period else f"bold {fg}", justify="center") for p in PERIOD_LIST])
    tiny.add_row(*[Text(f"{cards.get(p, 0):,}", style=f"bold {accent}" if p == period else f"bold {warning}", justify="center") for p in PERIOD_LIST])
    grid.add_row(tiny)
    grid.add_row(Rule(style=muted))

    # Breakdown — elegant, bar CLOSE to value (no far gap), zebra for scan
    grid.add_row(Text(f"Breakdown — {period_label}", style=f"bold {accent}"))
    metrics_table = Table(box=None, expand=True, show_header=False, pad_edge=False, padding=(0, 0))
    metrics_table.add_column("Metric", style=f"bold {fg}", ratio=2, no_wrap=True, overflow="ellipsis")
    metrics_table.add_column("Value", style=f"bold {accent}", justify="right", width=8, no_wrap=True)
    metrics_table.add_column("Bar", width=18, no_wrap=True)
    total = max(1, stats.total_keys)
    essential = [
        ("Printable", stats.printable, stats.printable / total * 100.0),
        ("Backspace", stats.backspace, stats.backspace / total * 100.0),
        ("Enter", stats.enter, stats.enter / total * 100.0),
        ("Modifiers", stats.modifiers, stats.modifiers / total * 100.0),
        ("Active min", stats.active_minutes, min(100.0, stats.active_minutes / 60 * 10)),
    ]
    for idx, (label, value, pct) in enumerate(essential):
        is_even = (idx % 2 == 0)
        row_bg = f" on {cursor_bg}" if is_even else ""
        bar = make_bar(pct, colors, width=18)
        lbl = Text(label, style=f"{fg}{row_bg}")
        val = Text(f"{value:,}", style=f"bold {accent}{row_bg}")
        metrics_table.add_row(lbl, val, bar, style=row_bg.strip() if row_bg else None)
    metrics_panel = Panel(
        metrics_table,
        border_style=accent,
        padding=(0, 1),
        title=f"[bold {accent}]Breakdown[/] [bold {fg}]• {stats.total_keys:,} total[/]",
    )
    grid.add_row(metrics_panel)

    grid.add_row(Rule(style=accent))
    grid.add_row(render_kpis_panel(stats, colors))
    grid.add_row(Rule(style=accent))
    grid.add_row(Text(f"Hourly — {datetime.now().strftime('%Y-%m-%d')}", style=f"bold {accent}"))
    grid.add_row(render_hourly_panel(store, colors))
    return grid


def build_layout(
    period: str,
    view: str,
    colors: dict[str, str],
    scrolls: dict[str, int],
    console_height: int,
    store: KeyStore | None = None,
) -> Panel:
    """Central layout builder — dispatches to view-specific renderers.

    Handles high-contrast theming, period/view tab bars, scroll windows,
    and graceful empty-DB states. Never raises — returns error Panel on fail.
    """
    try:
        return _build_layout_impl(period, view, colors, scrolls, console_height, store)
    except Exception as e:
        log_error(f"build_layout error: {e}\n{traceback.format_exc()}")
        return Panel(
            Text(f"Render error (see log): {e}", style="bold #ffb4ab"),
            border_style="red",
            expand=True,
            height=max(8, console_height or 24),
        )


def _build_layout_impl(
    period: str,
    view: str,
    colors: dict[str, str],
    scrolls: dict[str, int],
    console_height: int,
    store: KeyStore | None,
) -> Panel:
    console_height = max(8, int(console_height or 24))
    accent = colors.get("accent", "#ffb779")
    success = colors.get("success", "#c3cb98")
    warning = colors.get("warning", "#e3c0a5")
    fg = colors.get("fg", "#efe0d5")
    muted = colors.get("muted", "#a08c7a")

    # Ensure store exists (read-only, may be empty)
    if store is None:
        try:
            # Robust: try explicit path first, then default location
            data_dir = default_data_dir()
            db_path = data_dir / "keys.db"
            store = KeyStore(db_path)
            # init_db is safe but we avoid writing from TUI; only read if exists
            if not db_path.exists():
                # No DB yet — show empty state without crashing
                pass
        except Exception as e:
            log_error(f"store init fallback: {e}")
            # Create dummy in-memory? Use temp
            store = None  # type: ignore

    # Gather aggregates only for views that display them.
    try:
        current_stats = (
            _cached(
                store,
                ("stats", period),
                lambda: summarize(store, period, limit_keys=1000),  # type: ignore[arg-type]
            )
            if store is not None and view in {"overview", "keys", "chars"}
            else None
        )
    except Exception as e:
        log_error(f"summarize {period}: {e}")
        current_stats = None
    try:
        cards = (
            _cached(store, ("cards",), lambda: card_totals(store))  # type: ignore[arg-type]
            if store is not None and view == "overview"
            else {p: 0 for p in PERIOD_LIST}
        )
    except Exception as e:
        log_error(f"card_totals: {e}")
        cards = {p: 0 for p in PERIOD_LIST}

    # Fallback empty stats object for rendering when DB missing
    if current_stats is None:
        from dataclasses import dataclass, field as _field

        @dataclass
        class _Empty:
            period: str = ""
            total_keys: int = 0
            printable: int = 0
            backspace: int = 0
            delete: int = 0
            enter: int = 0
            tab: int = 0
            escape: int = 0
            modifiers: int = 0
            navigation: int = 0
            function: int = 0
            other: int = 0
            active_minutes: int = 0
            top_keys: list = _field(default_factory=list)
            top_chars: list = _field(default_factory=list)

            @property
            def keys_per_minute(self) -> float: return 0.0
            @property
            def words_per_minute(self) -> float: return 0.0
            @property
            def backspace_ratio(self) -> float: return 0.0

        current_stats = _Empty(period=period)

    # Header: title + subtitle with period hint
    header = Text(overflow="ellipsis", no_wrap=True)
    header.append(" 󰌌 Dusky Keylogger ", style=f"bold {accent}")
    header.append(f"({PERIOD_LABELS.get(period, period)}", style=f"bold {warning}")
    header.append(")", style=f"bold {warning}")
    if view in {"overview", "keys", "chars"}:
        header.append("  Total: ", style=f"{fg}")
        header.append(f"{current_stats.total_keys:,}", style=f"bold {success}")
        header.append(" keys", style=f"{fg}")
    header.append("  DB: ", style=f"{muted}")
    try:
        db_name = store.path.name if store and hasattr(store, "path") else "keys.db"  # type: ignore
    except Exception:
        db_name = "keys.db"
    header.append(db_name, style=f"{fg}")

    # Tab bars
    period_tabs = _render_period_tabs(period, colors)
    view_tabs = _render_view_tabs(view, colors)

    # Footer controls (high contrast fg primary)
    footer = Text(overflow="ellipsis", no_wrap=True)
    footer.append(" Controls: ", style=f"bold {muted}")
    footer.append("[1-4] Period", style=f"{fg}")
    footer.append("  [Tab/Shift+Tab] View  ", style=f"{fg}")
    footer.append("[j/k/↑↓] Scroll", style=f"bold {fg}")
    footer.append("  [PgUp/PgDn] Page  ", style=f"{fg}")
    footer.append("[Q/Esc] Quit", style=f"bold {accent}")

    # Content area height budgeting
    # Header(1) + period_tabs(1) + view_tabs(1) + footer(1) + borders(2) = 6 chrome lines
    # Content height = console_height - chrome - padding
    content_height = max(8, console_height - 8)

    # Dispatch view
    content: Any
    subtitle_extra = ""

    if view == "overview":
        content = _build_overview_layout(current_stats, cards, store, colors, period)  # type: ignore[arg-type]
    elif view == "keys":
        # Keys tab — full keys table, scrollable (use scrolls['keys'])
        kh = max(8, content_height - 2)
        scroll = int(scrolls.get("keys", 0))
        panel = render_keys_panel(current_stats, colors, scroll=scroll, height=kh)
        # Update scroll clamp in place for caller
        # Compute max from panel? Instead recompute here
        total = len(current_stats.top_keys or [])
        max_s = max(0, total - kh)
        scrolls["keys"] = max(0, min(scroll, max_s))
        content = panel
    elif view == "chars":
        kh = max(8, content_height - 2)
        scroll = int(scrolls.get("chars", 0))
        panel = render_chars_panel(current_stats, colors, scroll=scroll, height=kh)
        total = len(current_stats.top_chars or [])
        max_s = max(0, total - kh)
        scrolls["chars"] = max(0, min(scroll, max_s))
        content = panel
    elif view == "transcript":
        th = max(6, content_height - 2)
        scroll = int(scrolls.get("transcript", 0))
        # Efficient clamp via helper; caller (run_live_dashboard) handles auto-follow to bottom.
        # Do NOT force scroll==0 to bottom here — that would trap user at bottom.
        try:
            if store is not None:
                total_lines = _transcript_line_count(store, period)
                max_s = max(0, total_lines - th)
                scrolls["transcript"] = max(0, min(int(scrolls.get("transcript", scroll)), max_s))
            else:
                scrolls["transcript"] = max(0, scroll)
        except Exception:
            scrolls["transcript"] = max(0, scroll)
        # Render with clamped scroll
        panel = render_transcript_panel(store, period, colors, scroll=int(scrolls.get("transcript", scroll)), height=th)  # type: ignore[arg-type]
        content = panel
    elif view == "recent":
        th = max(6, content_height - 2)
        scroll = int(scrolls.get("recent", 0))
        panel = render_recent_panel(store, colors, scroll=scroll, height=th)  # type: ignore[arg-type]
        try:
            recent_rows = (
                _cached(store, ("recent",), lambda: store.recent(100))  # type: ignore[arg-type,union-attr]
                if store is not None
                else []
            )
            total_r = len(recent_rows)
            max_s = max(0, total_r - th)
            scrolls["recent"] = max(0, min(scroll, max_s))
        except Exception:
            scrolls["recent"] = max(0, scroll)
        content = panel
    else:
        content = Text(f"Unknown view: {view}", style=f"bold {warning}")

    # Assemble outer grid: period tabs, view tabs, header, content, footer
    # Use Rule for full-width dividers (intuitive, not truncated at "This Week")
    from rich.rule import Rule

    outer = Table.grid(expand=True)
    outer.add_column(ratio=1)
    outer.add_row(period_tabs)
    outer.add_row(view_tabs)
    outer.add_row(header)
    outer.add_row(Rule(style=muted))
    outer.add_row(content)
    outer.add_row(Rule(style=muted))
    outer.add_row(footer)

    # Panel subtitle with view/period hint
    subtitle_str = f"[bold {accent}]{VIEW_LABELS.get(view, view)}[/] [dim {muted}]({PERIOD_LABELS.get(period, period)})[/] {subtitle_extra}"
    panel = Panel(
        outer,
        border_style=f"{accent}",
        subtitle=subtitle_str,
        title=f"[bold {fg}]Dusky Keylogger Dashboard[/]",
        expand=True,
        height=console_height,
    )
    return panel


# ---------------------------------------------------------------------------
# Terminal / raw input / event loop (mirrors screentime_tui robustness)
# ---------------------------------------------------------------------------

def set_terminal_cbreak(fd: int) -> list[Any]:
    """Enter non-canonical, no-echo cbreak mode without O_NONBLOCK."""
    old = termios.tcgetattr(fd)
    new = termios.tcgetattr(fd)
    new[0] &= ~(termios.IXON | termios.IXOFF | termios.ICRNL | termios.INLCR)
    new[3] &= ~(
        termios.ECHO
        | termios.ECHOE
        | termios.ECHOK
        | termios.ECHONL
        | termios.ICANON
        | termios.IEXTEN
        | termios.ISIG
    )
    new[6][termios.VMIN] = 0
    new[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSAFLUSH, new)
    return old


def restore_terminal(fd: int, old_settings: list[Any], old_flags: int | None = None) -> None:
    try:
        sys.stdout.write(_MOUSE_OFF + _CURSOR_SHOW + _CLEAR_HOME)
        sys.stdout.flush()
    except Exception:
        pass
    if old_flags is not None:
        try:
            fcntl.fcntl(fd, fcntl.F_SETFL, old_flags)
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


def parse_input_sequence(buf: bytes) -> tuple[str | None, int]:
    """Parse one command from front of buf.

    Handles: tab, shift+tab (ESC [ Z), arrows, page up/down, mouse SGR,
    j/k/h/l vim keys, q/quit, 1-4 period, g/G home/end, etc.
    Returns (cmd, consumed_bytes). Consumed 0 means need more bytes.
    """
    if not buf:
        return None, 0
    if buf[0] == 0x1B:
        if len(buf) == 1:
            return None, 0
        # SGR mouse: ESC [ < btn ; x ; y M/m  — 64 scroll_up, 65 scroll_down
        if buf.startswith(b"\x1b[<"):
            for i in range(3, len(buf)):
                if buf[i] in (ord("M"), ord("m")):
                    try:
                        body = buf[3:i].decode("ascii", errors="ignore")
                        parts = body.split(";")
                        if parts and parts[0].isdigit():
                            bcode = int(parts[0])
                            if bcode == 64:
                                return "scroll_up", i + 1
                            if bcode == 65:
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
                        # Shift+Tab — CSI Z (most terminals) or ESC [ Z
                        return "prev_view", i + 1
                    if final == "u":
                        # kitty keyboard protocol uses CSI ... u
                        if num == "9":
                            if ";2" in inner or ":2" in inner:
                                return "prev_view", i + 1
                            return "next_view", i + 1
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
                            return "prev_view", i + 1
                    return None, i + 1
            return (None, 0) if len(buf) < 32 else (None, 1)
        # SS3: ESC O ...
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
                # Some terminals send ESC O Z for Shift+Tab
                return "prev_view", 3
            return None, 3
        # ESC + key (Alt), ignore
        return None, 2

    ch = buf[:1]
    match ch:
        case b"\x1b":
            return "escape", 1
        case b"q" | b"Q":
            return "quit", 1
        case b"\x03":
            return "force_quit", 1
        case b"\r" | b"\n" | b" ":
            return "select", 1
        case b"\x7f" | b"\x08":
            return "back", 1
        case b"\t":
            # Tab -> next view (View tabs cycle)
            return "next_view", 1
        case b"]":
            return "next_view", 1
        case b"[":
            return "prev_view", 1
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
            return "period_week", 1
        case b"3":
            return "period_month", 1
        case b"4":
            return "period_all", 1
        case b"r":
            return "refresh", 1
        case _:
            return None, 1


# ---------------------------------------------------------------------------
# Cache — in-memory snapshot so tab/view switches do zero extra disk churn
# ---------------------------------------------------------------------------
class _DashCache:
    """Cache theme colors and store reference with TTLs."""

    __slots__ = ("store", "colors", "colors_ts", "store_path")

    def __init__(self, store_path: Path | None = None) -> None:
        self.store_path: Path | None = store_path
        self.store: KeyStore | None = None
        self.colors: dict[str, str] = DEFAULT_COLORS.copy()
        self.colors_ts: float = 0.0
        self._init_store()

    def _init_store(self) -> None:
        try:
            if self.store_path is not None:
                p = Path(self.store_path).expanduser()
                # p may be file or dir
                if p.is_dir():
                    p = p / "keys.db"
                # Ensure parent exists but don't create empty DB aggressively?
                # Use KeyStore directly; it will fail gracefully if no DB
                self.store = KeyStore(p)
            else:
                # Resolve via default_data_dir (Path.home, no hardcode)
                data_dir = default_data_dir()
                db_path = data_dir / "keys.db"
                self.store = KeyStore(db_path)
        except Exception as e:
            log_error(f"cache store init: {e}")
            self.store = None

    def reload(self, force: bool = False, now: float | None = None) -> None:
        t = now if now is not None else time.monotonic()
        if force or (t - self.colors_ts) >= 5.0:
            try:
                self.colors = load_theme_colors()
            except Exception as e:
                log_error(f"cache colors: {e}")
            self.colors_ts = t
        # Store is long-lived; re-init only if None and forced
        if force and self.store is None:
            self._init_store()


# ---------------------------------------------------------------------------
# Live pause/resume helpers (for future FZF etc)
# ---------------------------------------------------------------------------

def _pause_live(live: Live, fd: int, old_settings: list[Any]) -> None:
    try:
        live.stop()
    except Exception as e:
        log_error(f"live.stop: {e}")
    _write_tty(_MOUSE_OFF + _CURSOR_SHOW + _CLEAR_HOME)
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


# ---------------------------------------------------------------------------
# Main live dashboard loop
# ---------------------------------------------------------------------------

def run_live_dashboard(store_path: str | Path | None = None) -> None:
    """Run the full-screen Live dashboard.

    Args:
        store_path: Optional path to keys.db or its directory. When None,
            resolves via default_data_dir() (Path.home, no hardcoded user).
    """
    console = Console(force_terminal=True, color_system="truecolor", highlight=False, soft_wrap=False)

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        console.print("[bold red]dusky keylogger dashboard requires a real TTY.[/bold red]")
        return

    fd = sys.stdin.fileno()
    old_settings = set_terminal_cbreak(fd)
    old_flags: int | None = None
    try:
        old_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    except Exception:
        pass

    _write_tty(_CLEAR_HOME + _MOUSE_ON + _CURSOR_HIDE)

    # Normalize store_path via Path.home handling
    norm_path: Path | None = None
    if store_path is not None:
        p = Path(store_path).expanduser()
        if not p.is_absolute():
            # If relative, treat as under HOME (intuitive for users)
            p = (Path.home() / p)
        norm_path = p
    cache = _DashCache(store_path=norm_path)
    cache.reload(force=True)

    period: str = "today"
    view: str = "overview"
    # Scroll offsets per view — transcript & recent are primary scrollable
    # Transcript: new at top (reverse chronological) — scroll 0 = newest, scroll down = older (lazy load)
    scrolls: dict[str, int] = {"transcript": 0, "recent": 0, "keys": 0, "chars": 0}
    _prev_transcript_total: int | None = None
    _prev_transcript_period: str | None = None
    _prev_transcript_th: int | None = None
    _prev_view: str = view

    def build_panel() -> Panel:
        return build_layout(period, view, cache.colors, scrolls, console.height, cache.store)

    def push_frame(live: Live) -> None:
        try:
            live.update(build_panel(), refresh=True)
        except Exception as e:
            log_error(f"live.update: {e}\n{traceback.format_exc()}")

    try:
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
            last_frame_sig: tuple | None = None
            last_push_at = time.monotonic()
            while running:
                try:
                    ready, _, _ = select.select([fd], [], [], 0.25)
                except InterruptedError:
                    ready = []
                got_keys = False
                if ready:
                    try:
                        chunk = os.read(fd, 4096)
                    except BlockingIOError:
                        chunk = b""
                    except OSError as e:
                        log_error(f"os.read: {e}")
                        chunk = b""
                    if not chunk:
                        break
                    input_buf.extend(chunk)
                    got_keys = True

                # Standalone ESC timeout (user pressed Esc alone)
                if not got_keys and input_buf == b"\x1b":
                    input_buf.clear()
                    running = False
                    break

                while input_buf:
                    cmd, consumed = parse_input_sequence(bytes(input_buf))
                    if consumed <= 0:
                        if input_buf[0] == 0x1B and len(input_buf) > 48:
                            del input_buf[0]
                            continue
                        break
                    del input_buf[:consumed]

                    match cmd:
                        case "force_quit":
                            running = False
                            break
                        case "quit" | "escape" | "back":
                            # Esc/q quits from any view
                            running = False
                            break
                        case "up":
                            # Up arrow / k — scroll current view's scrollable pane
                            if view in ("transcript", "recent", "keys", "chars"):
                                scrolls[view] = max(0, int(scrolls.get(view, 0)) - 1)
                            else:
                                # Overview has no scroll; treat as no-op
                                pass
                        case "down":
                            if view in ("transcript", "recent", "keys", "chars"):
                                scrolls[view] = int(scrolls.get(view, 0)) + 1
                            else:
                                pass
                        case "scroll_up":
                            # Mouse wheel up — 3 lines at a time
                            if view in ("transcript", "recent", "keys", "chars"):
                                scrolls[view] = max(0, int(scrolls.get(view, 0)) - 3)
                            else:
                                # Even overview could allow transcript scroll if we wanted,
                                # but keep focused on active view
                                pass
                        case "scroll_down":
                            if view in ("transcript", "recent", "keys", "chars"):
                                scrolls[view] = int(scrolls.get(view, 0)) + 3
                            else:
                                pass
                        case "page_up":
                            if view in ("transcript", "recent", "keys", "chars"):
                                scrolls[view] = max(0, int(scrolls.get(view, 0)) - 10)
                            else:
                                pass
                        case "page_down":
                            if view in ("transcript", "recent", "keys", "chars"):
                                scrolls[view] = int(scrolls.get(view, 0)) + 10
                            else:
                                pass
                        case "half_page_up":
                            if view in ("transcript", "recent", "keys", "chars"):
                                scrolls[view] = max(0, int(scrolls.get(view, 0)) - 15)
                        case "half_page_down":
                            if view in ("transcript", "recent", "keys", "chars"):
                                scrolls[view] = int(scrolls.get(view, 0)) + 15
                        case "home":
                            if view in ("transcript", "recent", "keys", "chars"):
                                scrolls[view] = 0
                        case "end":
                            if view in ("transcript", "recent", "keys", "chars"):
                                # Jump far — build_layout will clamp to max_scroll
                                scrolls[view] = 10**9
                        case "left" | "right":
                            # Left/right could switch view as well, but keep as no-op for now
                            pass
                        case "period_today" | "period_week" | "period_month" | "period_all":
                            new_period = PERIOD_KEYS.get(cmd, period)
                            if new_period != period:
                                period = new_period
                                # Reset scrolls when period changes (transcript content changes)
                                scrolls["transcript"] = 0
                                scrolls["recent"] = 0
                                scrolls["keys"] = 0
                                scrolls["chars"] = 0
                        case "next_view":
                            view = _get_cycled_view(view, 1)
                            # Don't reset scrolls for view switch — preserve position
                        case "prev_view":
                            # Shift+Tab — cycle back
                            view = _get_cycled_view(view, -1)
                        case "next_tab":
                            # Back-compat: some callers emit next_tab for view cycling
                            view = _get_cycled_view(view, 1)
                        case "prev_tab":
                            view = _get_cycled_view(view, -1)
                        case "select":
                            # Enter could expand? No sub-view; keep as view cycle forward
                            view = _get_cycled_view(view, 1)
                        case "refresh":
                            _QUERY_CACHE.clear()
                            cache.reload(force=True)
                        case _:
                            pass

                if not running:
                    break
                if not got_keys:
                    cache.reload(force=False)
                # Idle skip: re-render only when input arrived, the data
                # version (MAX(id)) changed, or layout inputs changed.
                try:
                    data_ver = cache.store.max_id() if cache.store is not None else 0  # type: ignore[union-attr]
                except Exception:
                    data_ver = -1
                frame_sig = (data_ver, datetime.now().date(), period, view, console.width, console.height, cache.colors_ts)
                if not got_keys and frame_sig == last_frame_sig and (
                    time.monotonic() - last_push_at) < 2.0:
                    continue
                last_frame_sig = frame_sig
                last_push_at = time.monotonic()
                # Transcript: new at top (reverse chronological), live, efficient native Python
                # New lines inserted at top (index 0), so staying at scroll 0 shows newest.
                # If user scrolled down viewing older, keep offset stable by shifting down.
                try:
                    if view == "transcript" and cache.store is not None:
                        content_height = max(8, console.height - 8)
                        th = max(6, content_height - 2)
                        new_total = _transcript_line_count(cache.store, period)
                        prev_total = _prev_transcript_total
                        prev_period = _prev_transcript_period
                        # On period change or first entry, reset to top (newest)
                        if _prev_view != "transcript" or prev_period != period or prev_total is None:
                            scrolls["transcript"] = 0
                        else:
                            cur = int(scrolls.get("transcript", 0))
                            if prev_total is not None and new_total > prev_total and cur != 0:
                                # Viewing older — keep same content by shifting down
                                scrolls["transcript"] = cur + (new_total - prev_total)
                            elif cur == 0:
                                # At top (newest) — stay at top to see new lines
                                scrolls["transcript"] = 0
                        _prev_transcript_total = new_total
                        _prev_transcript_period = period
                        _prev_transcript_th = th
                    _prev_view = view
                except Exception as e:
                    log_error(f"transcript auto-follow {e}")
                push_frame(live)

    except KeyboardInterrupt:
        pass
    except Exception as e:
        log_error(f"run_live_dashboard unhandled: {e}\n{traceback.format_exc()}")
    finally:
        restore_terminal(fd, old_settings, old_flags)
        try:
            # Use a fresh console after restore to avoid Live's alt-screen
            Console().print(f"[bold green]✔ Keylogger Dashboard closed cleanly.[/bold green] [dim]({PERIOD_LABELS.get(period,'')} / {VIEW_LABELS.get(view,'')})[/dim]")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Entry point — main()
# ---------------------------------------------------------------------------

def main(store_path: str | Path | None = None) -> None:
    """Entry point used by cli.py: ``from .dashboard_tui import main``.

    Supports ``store_path`` as optional PathLike for tests/CLI override.
    Also supports direct invocation: handles --help and --preview-like args
    if needed in future.

    Never hardcodes username — always via Path.home()/expanduser().
    Log file at ~/.config/dusky/settings/keylogger/data/logs/dashboard_tui_error.log.
    """
    # CLI convenience for direct launch
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg in ("--help", "-h"):
            print("Dusky Keylogger Dashboard (Matugen + Rich)")
            print("Usage: dashboard_tui.py [--help]")
            print("  Interactive Live dashboard:")
            print("    1 Today  2 Week  3 Month  4 All   — period tabs")
            print("    Tab / Shift+Tab                    — view tabs (Overview Keys Chars Transcript Recent)")
            print("    j/k  ↑/↓  PgUp/PgDn  Wheel        — scroll Transcript/Recent")
            print("    q / Esc / Ctrl-C                   — quit")
            print("")
            print(f"  Theme: {THEME_FILE} (muted #a08c7a high contrast)")
            print(f"  Log:   {LOG_FILE}")
            print(f"  DB:    {Path.home() / '.config' / 'dusky' / 'settings' / 'keylogger' / 'data' / 'keys.db'}")
            return
        if arg in ("--version", "-v"):
            print(__version__)
            return

    # Resolve store_path if passed as file path from cli's dashboard command
    if store_path is not None:
        p = Path(store_path).expanduser()
        # cli.py passes store.path (file). Daemon helper already uses Path.home.
        # Keep as-is; _DashCache normalizes.
        run_live_dashboard(p)
    else:
        run_live_dashboard(None)


if __name__ == "__main__":
    main()
