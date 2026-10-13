#!/usr/bin/env python3
"""Install a matching ISO network meter, or build it for this CPU."""

import runpy
import sys
from pathlib import Path

sys.dont_write_bytecode = True

if __name__ == "__main__":
    project = Path(__file__).resolve().parents[2] / "waybar/network/rust"
    runpy.run_path(str(project / "setup.py"), run_name="__main__")
