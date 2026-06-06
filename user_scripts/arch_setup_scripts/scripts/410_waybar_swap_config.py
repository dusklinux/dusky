#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# --- Prevent Local __pycache__ Generation ---
# Route all compiled .pyc files to ~/.cache/dusky_tui to keep source folders perfectly clean.
# Setting this in the environment guarantees the underlying tui_waybars.py script inherits it.
_cache_home = Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser().resolve()
os.environ["PYTHONPYCACHEPREFIX"] = str(_cache_home / "dusky_tui")
sys.pycache_prefix = str(_cache_home / "dusky_tui")

# We only need the target script. tui_waybars.py acts as its own 
# standalone CLI router when arguments are passed to it.
DEFAULT_TARGET_SCRIPT = Path.home() / "user_scripts" / "waybar" / "tui_waybars.py"


def normalize_set_value(value: str) -> str:
    """
    Intelligently extracts just the value so it can be passed to --apply.
    Converts: 'active_theme_name=5' -> '5'
    Converts: 'waybar=chaos_h' -> 'chaos_h'
    """
    value = value.strip()
    if not value:
        raise ValueError("empty value passed to --set")
    if "=" in value:
        return value.split("=", 1)[1]
    return value


def build_command(target_script: Path, ns: argparse.Namespace, passthrough: list[str]) -> list[str]:
    # Bypass main.py and hit the schema script directly for headless execution
    base = [sys.executable, str(target_script)]

    if ns.set_value is not None:
        base += ["--apply", normalize_set_value(ns.set_value)]
    elif ns.next:
        base += ["--toggle"]
    elif ns.prev:
        base += ["--back_toggle"]
    elif ns.pos:
        base += ["--toggle-pos"]
    elif ns.heal:
        base += ["--heal"]
    else:
        base += passthrough

    return base


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="waybar_swap_config",
        description="Smart wrapper around the Dusky Waybar configuration ecosystem.",
        allow_abbrev=False,
    )

    parser.add_argument(
        "--target-script",
        type=Path,
        default=DEFAULT_TARGET_SCRIPT,
        help="path to the inner Python script (tui_waybars.py)",
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--set", "-s",
        dest="set_value",
        metavar="VALUE",
        help="set a specific Waybar value, e.g. 5, chaos_h, or active_theme_name=5",
    )
    group.add_argument(
        "--next", "-n",
        action="store_true",
        help="move to the next waybar theme",
    )
    group.add_argument(
        "--prev", "--previous", "-p",
        dest="prev",
        action="store_true",
        help="move to the previous waybar theme",
    )
    group.add_argument(
        "--pos",
        action="store_true",
        help="toggle the waybar screen position (top/bottom/left/right)",
    )
    group.add_argument(
        "--heal", "--reapply", "--state", "-r",
        dest="heal",
        action="store_true",
        help="reapply the current waybar theme (also heals broken symlinks)",
    )

    ns, passthrough = parser.parse_known_args(argv)

    target_script = ns.target_script.expanduser().resolve()

    if not target_script.is_file():
        print(f"[-] Error: missing target schema script: {target_script}", file=sys.stderr)
        return 1

    cmd = build_command(target_script, ns, passthrough)

    try:
        completed = subprocess.run(cmd, check=True)
        return completed.returncode
    except subprocess.CalledProcessError as e:
        # Silently pass back the exit code without dumping Python stack traces to the terminal
        return e.returncode
    except OSError as e:
        print(f"[-] Error: failed to start command: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
