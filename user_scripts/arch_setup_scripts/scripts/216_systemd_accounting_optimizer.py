#!/usr/bin/env python3
"""Master systemd default accounting optimizer for Arch Linux (systemd 260+, Python 3.14+)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

# Config targets
SYSTEMD_CONF_DIR = Path("/etc/systemd/system.conf.d")
DROPIN_FILE = SYSTEMD_CONF_DIR / "99-default-accounting.conf"


class C:
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[1;31m"
    GRN = "\033[1;32m"
    YLW = "\033[1;33m"
    BLU = "\033[1;34m"
    RST = "\033[0m"

    @classmethod
    def strip(cls) -> None:
        for name in ("BOLD", "DIM", "RED", "GRN", "YLW", "BLU", "RST"):
            setattr(cls, name, "")


QUIET = False


def info(msg: str) -> None:
    if not QUIET:
        print(f"{C.BLU}[INFO]{C.RST} {msg}")


def ok(msg: str) -> None:
    if not QUIET:
        print(f"{C.GRN}[ OK ]{C.RST} {msg}")


def warn(msg: str) -> None:
    print(f"{C.YLW}[WARN]{C.RST} {msg}")


def err(msg: str) -> None:
    print(f"{C.RED}[FAIL]{C.RST} {msg}", file=sys.stderr)


def die(msg: str, code: int = 1) -> NoReturn:
    err(msg)
    sys.exit(code)


def get_active_defaults() -> tuple[str, str]:
    try:
        r = subprocess.run(
            ["systemctl", "show", "-p", "DefaultMemoryAccounting", "-p", "DefaultTasksAccounting", "--value"],
            text=True,
            capture_output=True,
            check=True,
        )
        lines = r.stdout.strip().splitlines()
        if len(lines) >= 2:
            return lines[0].strip(), lines[1].strip()
        die(f"Unexpected systemctl output: {r.stdout}")
    except Exception as e:
        die(f"Failed to query systemctl defaults: {e}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="systemd_accounting_optimizer",
        description="Disable global systemd default accounting to reduce background overhead.",
    )
    ap.add_argument("-n", "--dry-run", action="store_true", help="Preview configurations without writing")
    ap.add_argument("--no-color", action="store_true", help="Disable colored output")
    args = ap.parse_args(argv)

    if args.no_color or not sys.stdout.isatty() or "NO_COLOR" in os.environ:
        C.strip()

    # 1. Root check
    if os.geteuid() != 0 and not args.dry_run:
        info("root privileges required — escalating via sudo")
        os.execvp("sudo", ["sudo", "--", sys.executable, str(Path(__file__).resolve()), *argv])

    # 2. Get current state
    curr_mem, curr_tasks = get_active_defaults()
    info(f"Current System Defaults: DefaultMemoryAccounting={C.BOLD}{curr_mem}{C.RST}, DefaultTasksAccounting={C.BOLD}{curr_tasks}{C.RST}")

    if curr_mem == "no" and curr_tasks == "no" and DROPIN_FILE.exists():
        ok("Systemd default accounting is already optimized.")
        return 0

    # 3. Drop-in Payload
    payload = """# Managed by 216_systemd_accounting_optimizer.py
# Scope: Disable global systemd process accounting defaults to reduce metadata overhead.

[Manager]
DefaultMemoryAccounting=no
DefaultTasksAccounting=no
"""

    if args.dry_run:
        print(f"\n{C.BOLD}[ DRY RUN: Would write to {DROPIN_FILE} ]{C.RST}")
        print(payload)
        return 0

    # Write configuration drop-in
    try:
        SYSTEMD_CONF_DIR.mkdir(parents=True, exist_ok=True)
        DROPIN_FILE.write_text(payload)
        os.chmod(DROPIN_FILE, 0o644)
        ok(f"Wrote configuration drop-in to {DROPIN_FILE}")
    except Exception as e:
        die(f"Failed to write configuration: {e}")

    # 4. Trigger Manager Reload / Re-exec to apply settings live
    info("Reloading systemd manager configuration...")
    try:
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        
        # Verify if changes are picked up, if not, perform daemon-reexec
        verify_mem, verify_tasks = get_active_defaults()
        if verify_mem != "no" or verify_tasks != "no":
            info("daemon-reload complete but settings not active. Re-executing system manager...")
            subprocess.run(["systemctl", "daemon-reexec"], check=True)
            verify_mem, verify_tasks = get_active_defaults()

        if verify_mem != "no" or verify_tasks != "no":
            die(f"Verification failed: Active defaults are still Memory={verify_mem}, Tasks={verify_tasks}")

        ok("Verified live manager values:")
        ok(f"  DefaultMemoryAccounting = {verify_mem}")
        ok(f"  DefaultTasksAccounting = {verify_tasks}")
    except Exception as e:
        die(f"Failed to apply or verify changes: {e}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print(f"\n{C.YLW}aborted — nothing further was written.{C.RST}")
        sys.exit(130)
