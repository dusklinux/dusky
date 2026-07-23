#!/usr/bin/env python3.14
"""
==============================================================================
Description: Dusky Keybinds Cheatsheet
Language:    Python 3.14.6+ (Native Generics, Type Aliases, Match Statements)
Design:      Dynamic Matugen Palette, Pure Minimalist Layout
==============================================================================
"""

from __future__ import annotations

import json
import sys
import termios
import tty
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# ── runtime gate ────────────────────────────────────────────────────────────
if sys.version_info < (3, 14):
    sys.exit(
        f"│ FATAL │ Python 3.14+ required  ·  running {sys.version.split()[0]}"
    )

# ── Matugen Dynamic Palette Integration ──────────────────────────────────────
MATUGEN_PATH: Final[Path] = Path.home() / ".config/matugen/generated/dusky_tui.json"


class Palette:
    BG: str       = "#1d100a"
    FG: str       = "#f8ddd2"
    ACCENT: str   = "#ffb694"
    ERROR: str    = "#ffb4ab"
    WARNING: str  = "#efbc94"
    SUCCESS: str  = "#f0be79"
    MUTED: str    = "#c8b0a6"

    @classmethod
    def load(cls) -> None:
        if MATUGEN_PATH.exists():
            try:
                data = json.loads(MATUGEN_PATH.read_text(encoding="utf-8"))
                cls.BG = data.get("bg", cls.BG)
                cls.FG = data.get("fg", cls.FG)
                cls.ACCENT = data.get("accent", cls.ACCENT)
                cls.ERROR = data.get("error", cls.ERROR)
                cls.WARNING = data.get("warning", cls.WARNING)
                cls.SUCCESS = data.get("success", cls.SUCCESS)
                cls.MUTED = cls.FG
            except Exception:
                pass


Palette.load()
C = Palette  # short alias used everywhere below


# ── domain ──────────────────────────────────────────────────────────────────
class Tag(StrEnum):
    ACCENT  = "accent"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER  = "danger"
    MUTED   = "muted"


def tag_fg(tag: Tag) -> str:
    """Python 3.14 structural match → Matugen foreground colour."""
    match tag:
        case Tag.ACCENT:  return C.ACCENT
        case Tag.SUCCESS: return C.SUCCESS
        case Tag.WARNING: return C.WARNING
        case Tag.DANGER:  return C.ERROR
        case Tag.MUTED:   return C.FG


MODS: Final[frozenset[str]] = frozenset({
    "SUPER", "ALT", "CTRL", "SHIFT", "MOUSE", "DRAG", "SCROLL", "PRINT",
})


@dataclass(frozen=True, slots=True)
class Bind:
    keys: tuple[str, ...]
    action: str
    tag: Tag = Tag.ACCENT


@dataclass(frozen=True, slots=True)
class Category:
    title: str
    icon: str
    accent: str
    binds: tuple[Bind, ...]


# ── catalogue · 4×9 · intentional symmetry ──────────────────────────────────
CATALOGUE: Final[tuple[Category, ...]] = (
    Category(
        title="LAUNCHERS  &  SEARCH",
        icon="󰀻",
        accent=C.ACCENT,
        binds=(
            Bind(("SUPER", "Q"),             "Launch Terminal",         Tag.ACCENT),
            Bind(("SUPER", "W"),             "Launch Web Browser",      Tag.ACCENT),
            Bind(("SUPER", "E"),             "Launch File Manager",     Tag.ACCENT),
            Bind(("SUPER", "R"),             "Open Text Editor",        Tag.ACCENT),
            Bind(("ALT", "SPACE"),           "App Launcher & Search",   Tag.SUCCESS),
            Bind(("SUPER", "SPACE"),         "System Quick Menu",       Tag.SUCCESS),
            Bind(("SUPER", "V"),             "Clipboard Manager",       Tag.ACCENT),
            Bind(("SUPER", "G"),             "Image Search & Lens",     Tag.SUCCESS),
            Bind(("SUPER", "CTRL", "SPACE"), "Emoji Picker & Insert",   Tag.SUCCESS),
        ),
    ),
    Category(
        title="WINDOW  TILING  &  POSITION",
        icon="󰝣",
        accent=C.SUCCESS,
        binds=(
            Bind(("SUPER", "C"),                "Close Focused Window",   Tag.DANGER),
            Bind(("SUPER", "A"),                "Toggle Fullscreen",      Tag.WARNING),
            Bind(("SUPER", "D"),                "Toggle Smart Float",     Tag.ACCENT),
            Bind(("SUPER", "Y"),                "Toggle Window Split",    Tag.ACCENT),
            Bind(("SUPER", "X"),                "Pin Window (Sticky)",    Tag.ACCENT),
            Bind(("SUPER", "SHIFT", "A"),       "Maximize Window",        Tag.WARNING),
            Bind(("SUPER", "H/J/K/L"),          "Focus Directionally",    Tag.MUTED),
            Bind(("SUPER", "SHIFT", "H/J/K/L"), "Move Window Position",   Tag.MUTED),
            Bind(("SUPER", "DRAG"),             "Move / Resize Window",   Tag.MUTED),
        ),
    ),
    Category(
        title="WORKSPACES  &  NAVIGATION",
        icon="󰽙",
        accent=C.WARNING,
        binds=(
            Bind(("SUPER", "1..9"),          "Switch Workspace 1–9",     Tag.ACCENT),
            Bind(("SUPER", "SHIFT", "1..9"),   "Move Window to WS 1–9",    Tag.WARNING),
            Bind(("SUPER", "ALT", "1..9"),     "Silent Move Window to WS", Tag.MUTED),
            Bind(("SUPER", "TAB"),             "Last Active Workspace",    Tag.SUCCESS),
            Bind(("SUPER", "Z"),               "Toggle Scratchpad",        Tag.SUCCESS),
            Bind(("SUPER", "SHIFT", "Z"),      "Send to Scratchpad",       Tag.WARNING),
            Bind(("SUPER", "SHIFT", "M"),      "Special Spotify WS",       Tag.ACCENT),
            Bind(("SUPER", "SCROLL"),          "Cycle Workspaces",         Tag.MUTED),
            Bind(("SUPER", "SHIFT", "TAB"),    "Cycle Next Workspace",     Tag.SUCCESS),
        ),
    ),
    Category(
        title="SYSTEM  ·  TOOLS  ·  CONTROLS",
        icon="󰒓",
        accent=C.ERROR,
        binds=(
            Bind(("SUPER", "M"),             "Lock Screen",             Tag.DANGER),
            Bind(("ALT", "F4"),              "Power & Logout Menu",     Tag.DANGER),
            Bind(("CTRL", "SHIFT", "ESC"),   "Activity Monitor",        Tag.ACCENT),
            Bind(("SUPER", "S"),             "Quick Screenshot",        Tag.SUCCESS),
            Bind(("SUPER", "SHIFT", "S"),    "Screenshot & Annotate",   Tag.SUCCESS),
            Bind(("SUPER", "B"),             "Color Picker",            Tag.ACCENT),
            Bind(("SUPER", "ALT", "O"),      "AI Ollama Chat TUI",      Tag.ACCENT),
            Bind(("ALT", "1 / 2 / 3"),       "Wi-Fi / BT / Audio TUI",  Tag.MUTED),
            Bind(("SUPER", "SHIFT", "R"),    "Reload Hyprland Config",  Tag.DANGER),
        ),
    ),
)


# ── pure render helpers ─────────────────────────────────────────────────────
def _pill(token: str, cat_accent: str) -> Text:
    """Concise keycap token using Matugen colors."""
    bare = token.strip()
    out = Text()
    if bare in MODS:
        out.append(f"{bare}", style=f"bold {C.FG}")
    elif any(ch in bare for ch in ("..", "/", "1..9")):
        out.append(f"{bare}", style=f"bold {C.WARNING}")
    else:
        out.append(f"{bare}", style=f"bold {cat_accent}")
    return out


def chord(keys: tuple[str, ...], cat_accent: str) -> Text:
    """Keycaps joined by a delicate +."""
    line = Text()
    for i, k in enumerate(keys):
        if i:
            line.append(" + ", style=f"bold {C.FG}")
        line.append_text(_pill(k, cat_accent))
    return line


def action_cell(b: Bind, cat_accent: str) -> Text:
    """Status gem + high-contrast verb."""
    fg = tag_fg(b.tag)
    cell = Text()
    cell.append("● ", style=fg)
    cell.append(b.action, style=f"bold {fg}")
    return cell


def build_card(cat: Category, index: int) -> Panel:
    """One category card — 2 clean columns, zero dots truncation."""
    tbl = Table(
        show_header=True,
        header_style=f"bold underline {C.FG}",
        show_edge=False,
        box=None,
        expand=True,
        pad_edge=False,
        padding=(0, 1),
    )
    tbl.add_column("COMBINATION", justify="left", no_wrap=True, width=21)
    tbl.add_column("ACTION", justify="left", no_wrap=True)

    for b in cat.binds:
        tbl.add_row(chord(b.keys, cat.accent), action_cell(b, cat.accent))

    title = Text()
    title.append(f" {cat.icon} ", style=f"bold {cat.accent}")
    title.append(f" {index:02d} ", style=f"bold {C.BG} on {cat.accent}")
    title.append(f"  {cat.title} ", style=f"bold {cat.accent}")

    return Panel(
        tbl,
        title=title,
        title_align="left",
        border_style=cat.accent,
        box=box.ROUNDED,
        padding=(0, 1),
        expand=True,
    )


def render(console: Console) -> Group:
    """Pure minimal dashboard — centered header title + 2x2 category grid."""
    cards = [build_card(cat, i) for i, cat in enumerate(CATALOGUE, 1)]

    if console.width >= 110:
        grid = Table.grid(expand=True, padding=(0, 2))
        grid.add_column(ratio=1)
        grid.add_column(ratio=1)
        grid.add_row(cards[0], cards[1])
        grid.add_row(Text(""), Text(""))
        grid.add_row(cards[2], cards[3])
    else:
        grid = Table.grid(expand=True, padding=(0, 0))
        grid.add_column(ratio=1)
        for i, card in enumerate(cards):
            grid.add_row(card)
            if i < len(cards) - 1:
                grid.add_row(Text(""))

    brand = Text()
    brand.append(" 󰌌  DUSKY CHEATSHEET ", style=f"bold {C.BG} on {C.ACCENT}")

    return Group(
        Text(""),
        Align.center(brand),
        Text(""),
        grid,
    )


# ── interaction ─────────────────────────────────────────────────────────────
def wait_for_key() -> None:
    """Raw single-keystroke barrier; always restore terminal attrs."""
    if not sys.stdin.isatty():
        return
    fd = sys.stdin.fileno()
    prev = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        sys.stdin.read(1)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, prev)


def main() -> None:
    console = Console(highlight=False)
    console.clear()
    console.print(render(console))
    wait_for_key()


if __name__ == "__main__":
    main()
