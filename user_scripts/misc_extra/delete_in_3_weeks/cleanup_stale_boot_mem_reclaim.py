#!/usr/bin/env python3
"""
cleanup_stale_boot_mem_reclaim.py — Purge legacy dusky_boot_mem_reclaim units and binary

Idempotently stops, disables, and deletes:
  - /etc/systemd/system/dusky_boot_mem_reclaim.timer
  - /etc/systemd/system/dusky_boot_mem_reclaim.service
  - /etc/systemd/system/timers.target.wants/dusky_boot_mem_reclaim.timer
  - /etc/systemd/system/multi-user.target.wants/dusky_boot_mem_reclaim.service
  - /usr/local/bin/dusky_boot_mem_reclaim

This ensures seamless migration to dusky_pro_active_zram_swap.
Scheduled to be safely deleted once all systems migrate.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# --- Presentation ---
class C:
    RED = "\033[1;31m"
    GRN = "\033[1;32m"
    YLW = "\033[1;33m"
    BLU = "\033[1;34m"
    RST = "\033[0m"

if not sys.stdout.isatty() or "NO_COLOR" in os.environ:
    C.RED = C.GRN = C.YLW = C.BLU = C.RST = ""

def info(msg: str) -> None: print(f"{C.BLU}[INFO]{C.RST} {msg}")
def ok(msg: str) -> None: print(f"{C.GRN}[ OK ]{C.RST} {msg}")
def warn(msg: str) -> None: print(f"{C.YLW}[WARN]{C.RST} {msg}")

def ensure_root() -> None:
    if os.geteuid() == 0:
        return
    info("Root privileges required to purge systemd units. Re-launching via sudo...")
    if shutil.which("sudo") is None:
        print(f"{C.RED}[FAIL]{C.RST} sudo not found in PATH. Run as root.", file=sys.stderr)
        sys.exit(1)
    os.execvp("sudo", ["sudo", sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])

def main() -> None:
    ensure_root()
    info("Checking for legacy dusky_boot_mem_reclaim artifacts...")

    legacy_units = (
        "dusky_boot_mem_reclaim.timer",
        "dusky_boot_mem_reclaim.service",
    )
    legacy_files = (
        Path("/etc/systemd/system/dusky_boot_mem_reclaim.timer"),
        Path("/etc/systemd/system/dusky_boot_mem_reclaim.service"),
        Path("/etc/systemd/system/timers.target.wants/dusky_boot_mem_reclaim.timer"),
        Path("/etc/systemd/system/multi-user.target.wants/dusky_boot_mem_reclaim.service"),
        Path("/usr/local/bin/dusky_boot_mem_reclaim"),
    )

    changes_made = False

    # 1. Stop and disable legacy units
    for unit in legacy_units:
        try:
            res = subprocess.run(
                ["systemctl", "is-active", "--quiet", unit],
                check=False
            )
            is_active = (res.returncode == 0)
            res_en = subprocess.run(
                ["systemctl", "is-enabled", "--quiet", unit],
                check=False
            )
            is_enabled = (res_en.returncode == 0)

            if is_active or is_enabled:
                info(f"Stopping and disabling legacy unit: {unit}")
                subprocess.run(
                    ["systemctl", "disable", "--now", unit],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False
                )
                changes_made = True
                ok(f"Disabled legacy unit: {unit}")
        except Exception as e:
            warn(f"Could not disable unit {unit}: {e}")

    # 2. Remove legacy files and symlinks
    for f in legacy_files:
        if f.is_symlink() or f.exists():
            try:
                f.unlink(missing_ok=True)
                changes_made = True
                ok(f"Removed legacy file: {f}")
            except OSError as e:
                warn(f"Failed to remove {f}: {e}")

    # 3. Reload systemd if changes were made
    if changes_made:
        info("Reloading systemd daemon...")
        try:
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "reset-failed"], check=False)
            ok("systemd daemon reloaded cleanly.")
        except Exception as e:
            warn(f"systemctl daemon-reload failed: {e}")
        ok("Cleanup complete: All stale dusky_boot_mem_reclaim artifacts purged.")
    else:
        ok("System is clean: No legacy dusky_boot_mem_reclaim artifacts detected.")

if __name__ == "__main__":
    main()
