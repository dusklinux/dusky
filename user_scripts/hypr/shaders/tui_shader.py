#!/usr/bin/env python3
"""
Dusky TUI Schema — Hyprland Screen Shaders (tui_shader.py)

Categorizes all 52 Hyprland GLSL screen shaders across intuitive tabs with
mutually exclusive native boolean toggles, instant live preview on scroll,
reset ('r') to disable active shaders, and automatic rollback of uncommitted
previews + rendering policies (damage_tracking & VFR) on exit.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _resolve_dusky_root() -> Path:
    """Locate the dusky_tui root directory reliably across symlinked or direct launches."""
    candidates: list[Path] = []
    env_root = os.environ.get("DUSKY_TUI_ROOT")
    if env_root:
        candidates.append(Path(env_root).expanduser())

    candidates.append(Path.home() / "user_scripts" / "dusky_tui")

    try:
        here = Path(__file__).resolve()
        for parent in here.parents:
            candidates.append(parent)
            candidates.append(parent / "dusky_tui")
    except OSError:
        pass

    for candidate in candidates:
        try:
            if (candidate / "python" / "main" / "main.py").is_file() or (
                candidate / "python" / "frontend" / "core_types.py"
            ).is_file():
                return candidate.resolve()
        except OSError:
            continue

    return Path.home() / "user_scripts" / "dusky_tui"


_DUSKY_TUI_ROOT = _resolve_dusky_root()
if str(_DUSKY_TUI_ROOT) not in sys.path:
    sys.path.insert(0, str(_DUSKY_TUI_ROOT))

from python.engines.hypr_shader import HyprShaderEngine
from python.frontend.core_types import ConfigItem

ENGINE_TYPE = "hypr_shader"
TARGET_FILE = "~/.config/hypr/shaders"
APP_TITLE = "Dusky Hyprland Shaders"
DEFAULT_MODE = "auto"
THEME_FILE = "~/.config/matugen/generated/dusky_tui.json"
ENABLE_USER_PRESETS = False

TABS: list[str] = [
    "Controls",
    "Color & Tone",
    "Retro & Optics",
    "Cursor & Click",
    "Ambient & Fluid",
    "Extreme & Surreal",
]

TAB_NOTICES: dict[int, dict[str, str]] = {
    0: {
        "level": "info",
        "message": (
            "Scroll across any shader tab to live-preview effects on screen. "
            "Toggle a shader ON (Enter / Space) to apply it, toggle it OFF (or press 'r') to disable it, "
            "or quit (q / Esc) to revert uncommitted previews."
        ),
        "position": "top",
    },
    3: {
        "level": "info",
        "message": (
            "Cursor & Click effects use Hyprland's native pointer_position and pointer_pressed_positions "
            "uniforms (damage_tracking=0, vfr=false while active)."
        ),
        "position": "top",
    },
    5: {
        "level": "warning",
        "message": (
            "Extreme & Surreal shaders (45-52) intentionally distort or mirror large areas of the desktop. "
            "Toggle OFF, press 'r', or quit without toggling ON to revert immediately."
        ),
        "position": "top",
    },
}

CONTROL_ITEMS: list[ConfigItem] = [
    ConfigItem(
        label="Live Preview on Scroll",
        key="live_preview",
        scope="control",
        type_="bool",
        default=True,
        group="Live Preview & Master Controls",
        extended_help=(
            "**Live Preview on Scroll**\n\n"
            "When enabled, highlighting any shader row with `Up`/`Down`, `j`/`k`, `PgUp`/`PgDn`, "
            "or the mouse wheel immediately previews that shader on your Hyprland desktop.\n\n"
            "- Toggling this **Off** while an uncommitted preview is active immediately restores "
            "your last committed shader.\n"
            "- Placed at Row 0 of Tab 0 so launching the TUI never overwrites your active shader "
            "before you begin navigating."
        ),
    ),
    ConfigItem(
        label="Disable Shader (Turn Off)",
        key="shader_off",
        scope="control",
        type_="bool",
        default=False,
        options=["trigger:Turn Off"],
        group="Live Preview & Master Controls",
        extended_help=(
            "**Disable Screen Shader (Turn Off)**\n\n"
            "Clears `decoration:screen_shader` (`\"\"`), untoggles any active shader across all tabs, "
            "restores your baseline `damage_tracking` and `vfr` settings, removes the runtime state "
            "override file, and records `\"\"` in `rofi_shader_memory`."
        ),
    ),
    ConfigItem(
        label="Rollback to Baseline",
        key="rollback_preview",
        scope="control",
        type_="bool",
        default=False,
        options=["trigger:Revert"],
        group="Live Preview & Master Controls",
        extended_help=(
            "**Rollback to Baseline**\n\n"
            "Immediately rolls back any uncommitted live preview (or restores the initial baseline "
            "shader, `damage_tracking`, and `vfr` captured when the TUI was launched)."
        ),
    ),
    ConfigItem(
        label="Committed Shader: Loading...",
        key="status_committed",
        scope="status",
        type_="action",
        default=":",
        group="Session Telemetry",
        read_only=True,
        extended_help=(
            "**Committed Shader**\n\n"
            "The shader currently toggled ON as your persistent session state. Quitting the TUI without "
            "toggling a new shader ON always restores this shader."
        ),
    ),
    ConfigItem(
        label="Live On-Screen: Loading...",
        key="status_live",
        scope="status",
        type_="action",
        default=":",
        group="Session Telemetry",
        read_only=True,
        extended_help=(
            "**Live On-Screen Shader**\n\n"
            "The shader currently active in Hyprland's OpenGL ES 3.20 renderer (`decoration:screen_shader`)."
        ),
    ),
    ConfigItem(
        label="Live Render Policy: Loading...",
        key="status_policy",
        scope="status",
        type_="action",
        default=":",
        group="Session Telemetry",
        read_only=True,
        extended_help=(
            "**Live Render Policy**\n\n"
            "Current `damage_tracking` and `vfr` values in Hyprland:\n"
            "- `// dusky: animated` -> `damage_tracking=0`, `vfr=false`\n"
            "- `// dusky: full-redraw` -> `damage_tracking=1` (unless baseline is `0`)\n"
            "- Static / Off -> restores baseline settings."
        ),
    ),
    ConfigItem(
        label="Saved Baseline: Loading...",
        key="status_baseline",
        scope="status",
        type_="action",
        default=":",
        group="Session Telemetry",
        read_only=True,
        extended_help=(
            "**Saved Baseline Policy**\n\n"
            "Your original `damage_tracking` and `vfr` settings from before any animated or "
            "full-redraw shader was applied (`$XDG_RUNTIME_DIR/dusky-shader-$HYPRLAND_INSTANCE_SIGNATURE.json`)."
        ),
    ),
    ConfigItem(
        label="Catalog: Scanning ~/.config/hypr/shaders...",
        key="status_catalog",
        scope="status",
        type_="action",
        default=":",
        group="Session Telemetry",
        read_only=True,
        extended_help=(
            "**Shader Catalog & Session Lock**\n\n"
            "Displays the number of discovered `.glsl`/`.frag` shaders in `~/.config/hypr/shaders` "
            "and the status of the exclusive session lock (`$XDG_RUNTIME_DIR/dusky-shader-menu.lock`)."
        ),
    ),
]

SCHEMA: dict[int, list[ConfigItem]] = {
    0: CONTROL_ITEMS,
    1: [],
    2: [],
    3: [],
    4: [],
    5: [],
}


def _deferred_load_shaders() -> tuple[list[int], dict[int, list[ConfigItem]], dict[str, str]]:
    """Populate Tabs 1..5 on background discovery and F5 refresh."""
    engine = HyprShaderEngine(TARGET_FILE)
    engine.register_control_items(CONTROL_ITEMS)
    categorized_tabs = engine.build_shader_tabs()
    state = engine.load_state()
    return [1, 2, 3, 4, 5], categorized_tabs, state


DEFERRED_LOAD = _deferred_load_shaders


if __name__ == "__main__":
    script_path = Path(__file__).resolve()
    main_router = _DUSKY_TUI_ROOT / "python" / "main" / "main.py"
    cmd = [sys.executable, str(main_router), str(script_path), *sys.argv[1:]]
    try:
        os.execv(sys.executable, cmd)
    except OSError:
        sys.exit(subprocess.run(cmd, check=False).returncode)
