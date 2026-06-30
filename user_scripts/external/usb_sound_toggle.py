#!/usr/bin/env python3
"""Toggle USB sound notifications on/off via a user-owned flag file.

The flag file at ~/.config/dusky/settings/usb_udev_toggle is checked by
usb_sound.sh (both copies) before playing any USB connect/disconnect sounds.
No root privileges needed — the udev rule always stays active.
"""

from __future__ import annotations

import argparse
from pathlib import Path


FLAG_FILE = Path.home() / ".config" / "dusky" / "settings" / "usb_udev_toggle"


def cmd_status() -> str:
    return "yes" if FLAG_FILE.is_file() else "no"


def cmd_on() -> None:
    FLAG_FILE.parent.mkdir(parents=True, exist_ok=True)
    FLAG_FILE.touch()


def cmd_off() -> None:
    FLAG_FILE.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Toggle USB sound notifications on/off"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--on", action="store_true", help="Enable USB sounds")
    group.add_argument("--off", action="store_true", help="Disable USB sounds")
    group.add_argument("--status", action="store_true", help="Check state")
    args = parser.parse_args()

    if args.status:
        print(cmd_status())
    elif args.on:
        cmd_on()
    elif args.off:
        cmd_off()


if __name__ == "__main__":
    main()
