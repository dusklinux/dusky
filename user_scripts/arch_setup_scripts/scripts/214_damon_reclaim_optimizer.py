#!/usr/bin/env python3
"""Master DAMON Reclaim optimizer for Arch Linux (kernel 7.0+, systemd 260+, Python 3.14+)."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# Config targets
TMPFILES_FILE = Path("/etc/tmpfiles.d/99-damon-reclaim.conf")
DAMON_PARAMS_DIR = Path("/sys/module/damon_reclaim/parameters")

# =============================================================================
# USER CONFIGURATION AREA (Modify these parameters to tune behavior)
# =============================================================================

# Threshold in GB to switch between low-RAM and high-RAM profiles
RAM_DEMARCATION_GB = 30.0

# --- Profile 1: Low-RAM Configuration (< 30GB) ---
LOW_RAM_CONFIG = {
    "sample_interval": 500000,    # 500ms: How often the monitor checks what memory is used (wakes up 2 times/sec)
    "aggr_interval": 5000000,     # 5s: How often the monitor aggregates statistics to find cold memory
    "min_age": 120000000,         # 120s (2 min): Minimum time memory must sit untouched to be considered "cold"
    "wmarks_high": 500,           # 50%: Sleep the monitor if free RAM is above this percentage (500 parts per thousand)
    "wmarks_mid": 400,            # 40%: Activate the monitor if free RAM drops below this percentage (400 parts per thousand)
    "wmarks_low": 200,            # 20%: Pause the monitor if free RAM drops below this (to protect latency, 200 parts per thousand)
}

# --- Profile 2: High-RAM Configuration (>= 30GB) ---
HIGH_RAM_CONFIG = {
    "sample_interval": 2000000,   # 2s: How often the monitor checks what memory is used (wakes up once every 2 seconds)
    "aggr_interval": 10000000,    # 10s: How often the monitor aggregates statistics to find cold memory
    "min_age": 300000000,         # 300s (5 min): Minimum time memory must sit untouched to be considered "cold"
    "wmarks_high": 300,           # 30%: Sleep the monitor if free RAM is above this percentage (300 parts per thousand)
    "wmarks_mid": 200,            # 20%: Activate the monitor if free RAM drops below this percentage (200 parts per thousand)
    "wmarks_low": 100,            # 10%: Pause the monitor if free RAM drops below this (to protect latency, 100 parts per thousand)
}


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


def die(msg: str, code: int = 1) -> typing.NoReturn:  # type: ignore
    err(msg)
    sys.exit(code)


def detect_ram_gb() -> float:
    try:
        meminfo = Path("/proc/meminfo").read_text()
        m = re.search(r"^MemTotal:\s+(\d+)\s+kB", meminfo, re.M)
        if not m:
            die("Could not parse MemTotal from /proc/meminfo")
        return int(m.group(1)) / 1_048_576
    except Exception as e:
        die(f"Failed to read RAM capacity: {e}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="damon_reclaim_optimizer",
        description="Configure and enable DAMON Reclaim based on total memory capacity.",
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

    # 2. Compatibility check
    if not DAMON_PARAMS_DIR.is_dir():
        info("DAMON Reclaim is not supported or compiled in the current kernel. Skipping.")
        return 0

    # 3. RAM Detection and Profile Selection
    ram_gb = detect_ram_gb()
    info(f"Detected System RAM: {C.BOLD}{ram_gb:.2f} GB{C.RST}")

    if ram_gb < RAM_DEMARCATION_GB:
        label = "STRICT_RAM_SAVINGS (<30 GB)"
        blurb = "Aggressive low-power reclamation window, 2-minute page age threshold."
        cfg = LOW_RAM_CONFIG
    else:
        label = "PERFORMANCE_LEAN (≥30 GB)"
        blurb = "Emergency net: ultra-low overhead window, 5-minute page age, sleeps unless memory pressure occurs."
        cfg = HIGH_RAM_CONFIG

    sample_interval = cfg["sample_interval"]
    aggr_interval = cfg["aggr_interval"]
    min_age = cfg["min_age"]
    wmarks_high = cfg["wmarks_high"]
    wmarks_mid = cfg["wmarks_mid"]
    wmarks_low = cfg["wmarks_low"]

    info(f"Selected Profile: {C.BOLD}{label}{C.RST} — {C.DIM}{blurb}{C.RST}")

    # 4. Generate tmpfiles config
    config_content = f"""# Managed by 214_damon_reclaim_optimizer.py
# Scope: Enable DAMON Reclaim for {label} profile

# Polling and aggregation rates to protect battery life
w /sys/module/damon_reclaim/parameters/sample_interval - - - - {sample_interval}
w /sys/module/damon_reclaim/parameters/aggr_interval - - - - {aggr_interval}

# Minimum age for a memory page to be considered cold (seconds converted to microseconds)
w /sys/module/damon_reclaim/parameters/min_age - - - - {min_age}

# Watermarks (free memory rate per thousand): high, mid, low
w /sys/module/damon_reclaim/parameters/wmarks_high - - - - {wmarks_high}
w /sys/module/damon_reclaim/parameters/wmarks_mid - - - - {wmarks_mid}
w /sys/module/damon_reclaim/parameters/wmarks_low - - - - {wmarks_low}

# Start the daemon
w /sys/module/damon_reclaim/parameters/enabled - - - - Y
"""

    if args.dry_run:
        print(f"\n{C.BOLD}[ DRY RUN: Would write to {TMPFILES_FILE} ]{C.RST}")
        print(config_content)
        return 0

    # Write persistent config
    try:
        TMPFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
        TMPFILES_FILE.write_text(config_content)
        os.chmod(TMPFILES_FILE, 0o644)
        ok(f"Wrote configuration to {TMPFILES_FILE}")
    except Exception as e:
        die(f"Failed to write configuration: {e}")

    # 5. Apply live parameters in sysfs
    info("Applying parameters to live kernel...")
    try:
        (DAMON_PARAMS_DIR / "enabled").write_text("N")
        (DAMON_PARAMS_DIR / "sample_interval").write_text(str(sample_interval))
        (DAMON_PARAMS_DIR / "aggr_interval").write_text(str(aggr_interval))
        (DAMON_PARAMS_DIR / "min_age").write_text(str(min_age))
        (DAMON_PARAMS_DIR / "wmarks_high").write_text(str(wmarks_high))
        (DAMON_PARAMS_DIR / "wmarks_mid").write_text(str(wmarks_mid))
        (DAMON_PARAMS_DIR / "wmarks_low").write_text(str(wmarks_low))
        (DAMON_PARAMS_DIR / "enabled").write_text("Y")
        ok("Live parameters written successfully")
    except Exception as e:
        die(f"Failed to write live parameters: {e}")

    # 6. Verify live settings
    try:
        actual_enabled = (DAMON_PARAMS_DIR / "enabled").read_text().strip()
        actual_sample = (DAMON_PARAMS_DIR / "sample_interval").read_text().strip()
        actual_aggr = (DAMON_PARAMS_DIR / "aggr_interval").read_text().strip()
        actual_pid = (DAMON_PARAMS_DIR / "kdamond_pid").read_text().strip()

        if actual_enabled != "Y" or actual_sample != str(sample_interval) or actual_aggr != str(aggr_interval):
            die("Verification failed: values in sysfs do not match configured profile.")

        ok("Verified live kernel values:")
        ok(f"  enabled = {actual_enabled}")
        ok(f"  sample_interval = {actual_sample} µs")
        ok(f"  aggr_interval = {actual_aggr} µs")
        ok(f"  kdamond_pid = {actual_pid} (Active)")
    except Exception as e:
        die(f"Failed to verify running state: {e}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print(f"\n{C.YLW}aborted — nothing further was written.{C.RST}")
        sys.exit(130)
