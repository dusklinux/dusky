#!/usr/bin/env python3
"""Report whether every installed radio of a type is unblocked."""

import sys
from pathlib import Path


def radio_state(kind: str, root: Path = Path("/sys/class/rfkill")) -> str:
    if kind not in {"bluetooth", "wlan"}:
        raise ValueError(f"Unsupported radio type: {kind}")
    states = []
    for device in root.glob("rfkill*"):
        if (device / "type").read_text().strip() == kind:
            soft = (device / "soft").read_text().strip()
            hard = (device / "hard").read_text().strip()
            if soft not in {"0", "1"} or hard not in {"0", "1"}:
                raise ValueError(f"Invalid block state for {device}")
            states.append(soft == "0" and hard == "0")
    if not states:
        return "unavailable"
    return "yes" if any(states) else "no"


if __name__ == "__main__":
    try:
        print(radio_state(sys.argv[1]))
    except (IndexError, OSError, ValueError) as error:
        print(f"radio status: {error}", file=sys.stderr)
        sys.exit(1)
