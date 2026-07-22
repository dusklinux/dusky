#!/usr/bin/env python3.14
"""
==============================================================================
Description: Dusky Keybinds Cheatsheet
Language:    Python 3.14.6+ (Native Generics, Type Aliases, Match Statements)
Design:      Catppuccin Palette, Keycap Pill Formatting, Inline Context Badges
==============================================================================
"""

import sys
from dataclasses import dataclass
from typing import Final, Literal

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.box import ROUNDED, HEAVY

# Ensure Python 3.14+ requirement
if sys.version_info < (3, 14):
    print(f"[FATAL] Python 3.14+ required. Running: {sys.version}")
    sys.exit(1)


# Modern Python 3.14 Type Alias
type KeyTag = Literal["primary", "accent", "danger", "warning", "info", "muted", "success"]

@dataclass(frozen=True, slots=True)
class KeyEntry:
    keys: list[str]
    description: str
    tag: KeyTag = "primary"
    note: str = ""


type CategoryDict = dict[str, tuple[str, str, list[KeyEntry]]]

# ──────────────────────────────────────────────────────────────────────────────
# Perfectly Balanced & Symmetrical Hyprland Keybindings (9 Items Per Box)
# ──────────────────────────────────────────────────────────────────────────────
KEYBIND_GROUPS: Final[CategoryDict] = {
    "apps": (
        "🚀 ESSENTIAL APP LAUNCHERS & SEARCH",
        "#89b4fa",  # Catppuccin Blue
        [
            KeyEntry(["SUPER", "Q"], "Launch Terminal", "primary", "kitty"),
            KeyEntry(["SUPER", "W"], "Launch Web Browser", "primary", "Zen / Firefox"),
            KeyEntry(["SUPER", "E"], "Launch File Manager", "primary", "Thunar / Yazi"),
            KeyEntry(["SUPER", "R"], "Open Text Editor", "primary", "Neovim / VSCode"),
            KeyEntry(["ALT", "SPACE"], "App Launcher / Search", "accent", "Rofi Dmenu"),
            KeyEntry(["SUPER", "SPACE"], "System Quick Menu", "accent", "Rofi System"),
            KeyEntry(["SUPER", "V"], "Clipboard Manager", "info", "History & Copy"),
            KeyEntry(["SUPER", "G"], "Image Search & Lens", "info", "Select & Search"),
            KeyEntry(["SUPER", "CTRL", "SPACE"], "Emoji Picker & Insert", "accent", "Rofi Emoji"),
        ],
    ),
    "window": (
        "🪟 WINDOW TILING & POSITIONING",
        "#a6e3a1",  # Catppuccin Green
        [
            KeyEntry(["SUPER", "C"], "Close Focused Window", "danger", "Kill active"),
            KeyEntry(["SUPER", "A"], "Toggle Fullscreen Mode", "warning", "Monocle mode"),
            KeyEntry(["SUPER", "D"], "Toggle Smart Float", "info", "Floating window"),
            KeyEntry(["SUPER", "Y"], "Toggle Window Split", "info", "Vert / Horiz"),
            KeyEntry(["SUPER", "X"], "Pin Window (Sticky)", "info", "All Workspaces"),
            KeyEntry(["SUPER", "SHIFT", "A"], "Maximize Window Mode", "warning", "Fill screen"),
            KeyEntry(["SUPER", "H / J / K / L"], "Focus Directionally", "muted", "Vim keys"),
            KeyEntry(["SUPER", "SHIFT", "H/J/K/L"], "Move Window Position", "muted", "Swap tile"),
            KeyEntry(["SUPER", "Mouse Drag"], "Move or Resize Window", "muted", "Left / Right Drag"),
        ],
    ),
    "workspaces": (
        "📌 WORKSPACES & NAVIGATION",
        "#cba6f7",  # Catppuccin Mauve
        [
            KeyEntry(["SUPER", "1 .. 9"], "Switch Workspace 1 .. 9", "primary", "Direct jump"),
            KeyEntry(["SUPER", "SHIFT", "1..9"], "Move Window to WS 1..9", "warning", "Shift tile"),
            KeyEntry(["SUPER", "ALT", "1..9"], "Silent Move Window to WS", "muted", "Background"),
            KeyEntry(["SUPER", "TAB"], "Switch to Last Active WS", "accent", "Quick toggle"),
            KeyEntry(["SUPER", "Z"], "Toggle Scratchpad", "accent", "Drop-down term"),
            KeyEntry(["SUPER", "SHIFT", "Z"], "Move Window to Scratchpad", "warning", "Hide window"),
            KeyEntry(["SUPER", "SHIFT", "M"], "Special Spotify Workspace", "info", "Music WS"),
            KeyEntry(["SUPER", "Mouse Scroll"], "Cycle Workspaces", "muted", "Wheel up/down"),
            KeyEntry(["SUPER", "SHIFT", "TAB"], "Cycle Next Workspace", "accent", "WS Sequence"),
        ],
    ),
    "system": (
        "⚙️ SYSTEM, TOOLS & CONTROLS",
        "#f9e2af",  # Catppuccin Yellow
        [
            KeyEntry(["SUPER", "M"], "Lock Screen Immediately", "danger", "Hyprlock"),
            KeyEntry(["ALT", "F4"], "Power & Logout Menu", "danger", "Session menu"),
            KeyEntry(["CTRL", "SHIFT", "ESC"], "System Activity Monitor", "info", "btop TUI"),
            KeyEntry(["SUPER", "S / Print"], "Quick Screenshot", "accent", "Grim / Slurp"),
            KeyEntry(["SUPER", "SHIFT", "S"], "Screenshot & Annotate", "accent", "Swappy / Area"),
            KeyEntry(["SUPER", "B"], "Color Picker Tool", "info", "Hyprpicker"),
            KeyEntry(["SUPER", "ALT", "O"], "AI LLM Ollama Chat TUI", "primary", "Terminal AI"),
            KeyEntry(["ALT", "1 / 2 / 3"], "Wi-Fi / BT / Audio TUI", "muted", "System TUI"),
            KeyEntry(["SUPER", "SHIFT", "R"], "Reload Hyprland Config", "danger", "Hot reload"),
        ],
    ),
}


def format_keycaps(keys: list[str]) -> Text:
    """Format key combinations into visual keycap pills."""
    res = Text()
    for idx, k in enumerate(keys):
        if idx > 0:
            res.append(" + ", style="bold #6c7086")
        
        if k in ("SUPER", "ALT", "CTRL", "SHIFT"):
            res.append(f" {k} ", style="bold white on #313244")
        elif ".." in k or "/" in k:
            res.append(f" {k} ", style="bold #f9e2af on #1e1e2e")
        else:
            res.append(f" {k} ", style="bold #89b4fa on #313244")
    return res


def get_tag_style(tag: KeyTag) -> str:
    """Python 3.14 Pattern Matching for Tag Colors."""
    match tag:
        case "primary": return "bold #89b4fa"
        case "accent": return "bold #cba6f7"
        case "danger": return "bold #f38ba8"
        case "warning": return "bold #fab387"
        case "info": return "bold #89dceb"
        case "success": return "bold #a6e3a1"
        case "muted" | _: return "#a6adc8"


def create_category_table(title: str, border_color: str, entries: list[KeyEntry]) -> Table:
    """Create a balanced 2-column table with inline context badges for maximum space efficiency."""
    tbl = Table(
        show_header=True,
        header_style="bold underline #cdd6f4",
        box=ROUNDED,
        expand=True,
        title=f"[bold {border_color}]{title}[/bold {border_color}]",
        border_style=border_color,
    )
    
    tbl.add_column("Shortcut Keycap", justify="left", width=25, no_wrap=True)
    tbl.add_column("Action & Description", justify="left")

    for entry in entries:
        k_formatted = format_keycaps(entry.keys)
        desc_style = get_tag_style(entry.tag)
        d_text = Text(entry.description, style=desc_style)
        if entry.note:
            d_text.append(f"  ({entry.note})", style="italic #7f849c")
        
        tbl.add_row(k_formatted, d_text)

    return tbl


def render_cheatsheet_dashboard() -> Panel:
    console = Console()
    
    tables: list[Table] = []

    for group_key, (title, color, entries) in KEYBIND_GROUPS.items():
        tbl = create_category_table(title, color, entries)
        tables.append(tbl)

    # Responsive Grid Layout: 2 columns if width >= 115, else stack in 1 column
    if console.width >= 115:
        grid = Table.grid(expand=True, padding=(1, 2))
        grid.add_column(ratio=1)
        grid.add_column(ratio=1)
        grid.add_row(tables[0], tables[1])
        grid.add_row(tables[2], tables[3])
    else:
        grid = Table.grid(expand=True, padding=(1, 0))
        grid.add_column(ratio=1)
        for tbl in tables:
            grid.add_row(tbl)

    header_title = Text.assemble(
        (" 󰌌  DUSKY KEYBINDS CHEATSHEET ", "bold white on #89b4fa"),
        ("  [ SUPER = Win Key | ALT = Alt | CTRL = Ctrl | SHIFT = Shift ] ", "bold #cdd6f4 on #313244"),
    )

    footer_hint = Text(
        "Press 'q' or 'Esc' or Ctrl+C to close this glance window",
        style="bold #a6adc8",
        justify="center"
    )

    main_panel = Panel(
        grid,
        title=header_title,
        subtitle=footer_hint,
        border_style="bold #89b4fa",
        box=HEAVY,
        padding=(1, 1),
    )

    return main_panel


def main() -> None:
    console = Console()
    console.clear()
    panel = render_cheatsheet_dashboard()
    console.print(panel)

    if sys.stdin.isatty():
        try:
            import tty
            import termios
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(sys.stdin.fileno())
                sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except Exception:
            pass


if __name__ == "__main__":
    main()
