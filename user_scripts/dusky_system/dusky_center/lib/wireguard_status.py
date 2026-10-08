#!/usr/bin/env python3
"""Read a wg-quick interface's runtime state without accepting invalid names."""

import re
import sys
from pathlib import Path


VALID_NAME = re.compile(r"[A-Za-z0-9_=+.-]{1,15}\Z")


def interface_state(name: str, root: Path = Path("/sys/class/net")) -> str:
    if not VALID_NAME.fullmatch(name) or name.startswith("-"):
        return "unavailable"
    return "yes" if (root / name).is_dir() else "no"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: wireguard_status.py INTERFACE")
    print(interface_state(sys.argv[1]))
