#!/usr/bin/env python3
"""Universal deployment entry point: Linux by default, --windows for Windows."""

import os
import sys
from pathlib import Path


def main() -> int:
    args = sys.argv[1:]
    if "--windows" in args:
        args.remove("--windows")
        target = Path(__file__).resolve().parent / "windows/deploy_vm.py"
        # A distinct Windows entry point preserves the OS choice through sudo.
        os.execv(sys.executable, [sys.executable, str(target), *args])
    from linux.deploy_vm import main as linux_main
    return linux_main()


if __name__ == "__main__":
    raise SystemExit(main())
