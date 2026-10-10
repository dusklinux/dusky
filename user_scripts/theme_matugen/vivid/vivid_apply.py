#!/usr/bin/env python3
"""
===============================================================================
DUSKY VIVID THEME APPLY (CLI)
===============================================================================
Target: Arch Linux / Hyprland / Matugen / Python 3.15
Command-line interface to apply pre-baked vivid color packs instantly,
list available packs, check active state, or resume wallpaper theming.
===============================================================================
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Add local module directory to sys.path
_VIVID_DIR = Path(__file__).resolve().parent
if str(_VIVID_DIR) not in sys.path:
    sys.path.insert(0, str(_VIVID_DIR))

import vivid_data
import vivid_theme


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply pre-baked vivid color presets across the Dusky environment.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "preset_id",
        nargs="?",
        help="Preset ID to apply (e.g. toxic_lime, hot_magenta, cyber_cyan)",
    )
    parser.add_argument(
        "--apply",
        "-a",
        dest="apply_id",
        metavar="ID",
        help="Preset ID to apply",
    )
    parser.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="List all pre-baked vivid presets",
    )
    parser.add_argument(
        "--status",
        "-s",
        action="store_true",
        help="Show currently active vivid preset status",
    )
    parser.add_argument(
        "--resume-wallpaper",
        "-r",
        action="store_true",
        help="Return to dynamic wallpaper-derived theming",
    )

    args = parser.parse_args()

    if args.list:
        presets, diagnostics = vivid_data.load_presets_and_diagnostics()
        active_id, _, is_vivid = vivid_theme.get_active_vivid_info()
        print(f"Pre-Baked Vivid Presets ({len(presets)} available):")
        print("-" * 75)
        print(f"{'ID':<18} {'Accent':<9} {'Background':<12} {'Label'}")
        print("-" * 75)
        for p in presets:
            pid = p.get("id", "")
            marker = " *" if is_vivid and pid == active_id else ""
            print(f"{pid:<18} {p.get('hex', ''):<9} {p.get('bg_hex', ''):<12} {p.get('label', '')}{marker}")
        print("-" * 75)
        if diagnostics:
            print(f"\n[!] Definition Diagnostics ({len(diagnostics)}):")
            for d in diagnostics:
                print(f"    - {d}")
        if is_vivid:
            print(f"* Currently active: {active_id}")
        else:
            print("Currently active: dynamic wallpaper theme")
        return 0

    if args.status:
        active_id, active_hex, is_vivid = vivid_theme.get_active_vivid_info()
        if is_vivid:
            print(f"Active Theme: Vivid Preset '{active_id}' ({active_hex})")
        else:
            print(f"Active Theme: Wallpaper / Custom ({active_hex})")
        return 0

    if args.resume_wallpaper:
        print(":: Resuming dynamic wallpaper theming...")
        ok, msg, _ = vivid_theme.resume_wallpaper()
        if ok:
            print(f"[OK] {msg}")
            return 0
        else:
            print(f"[-] Error: {msg}", file=sys.stderr)
            return 1

    target_id = args.apply_id or args.preset_id
    if not target_id:
        parser.print_help()
        return 1

    print(f":: Applying vivid preset '{target_id}'...")
    ok, msg, _ = vivid_theme.apply_vivid(target_id)
    if ok:
        print(f"[OK] {msg}")
        return 0
    else:
        print(f"[-] Error: {msg}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
