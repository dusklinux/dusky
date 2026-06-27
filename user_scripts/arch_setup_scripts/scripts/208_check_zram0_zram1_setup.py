#!/usr/bin/env python3
# =============================================================================
# Elite Arch Linux ZRAM & Mount Diagnostic Verifier (Revision 2)
# Scope: Exhaustive Post-Reboot Forensic Interrogation
# Fixes: Bypasses Kernel Write-Only (WO) restrictions on mem_limit nodes
# =============================================================================

import os
import sys
import subprocess
import re
from pathlib import Path

# --- Presentation ---
class C:
    RED = "\033[1;31m"
    GRN = "\033[1;32m"
    YLW = "\033[1;33m"
    BLU = "\033[1;34m"
    BOLD = "\033[1m"
    RST = "\033[0m"

def info(msg: str): print(f"{C.BLU}[INFO]{C.RST} {msg}")
def ok(msg: str): print(f"{C.GRN}[PASS]{C.RST} {msg}")
def warn(msg: str): print(f"{C.YLW}[WARN]{C.RST} {msg}")
def fail(msg: str): 
    print(f"{C.RED}[FAIL]{C.RST} {msg}")
    sys.exit(1)

# --- Privilege Check ---
if os.geteuid() != 0:
    print(f"{C.RED}[!] Root privileges required to interrogate /sys and block devices.{C.RST}")
    os.execvp("sudo", ["sudo", sys.executable, os.path.abspath(__file__)] + sys.argv[1:])

def run_cmd(cmd: list) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()
    except subprocess.CalledProcessError as e:
        return (e.stdout or "").strip() + (e.stderr or "").strip()

print(f"\n{C.BOLD}=== Initiating Deep Architecture Diagnostics ==={C.RST}\n")

# --- 1. Bootloader / ZSWAP Check ---
info("Checking ZSWAP state...")
zswap_path = Path("/sys/module/zswap/parameters/enabled")
if zswap_path.exists():
    if zswap_path.read_text().strip() in ("Y", "1"):
        fail("ZSWAP is currently ACTIVE. The bootloader cmdline modification failed or requires regeneration.")
    else:
        ok("ZSWAP is cleanly disabled at the kernel level.")
else:
    warn("ZSWAP module missing. Assuming it is disabled or not compiled into your kernel.")

# --- 2. Memory Calculations (Page-Aligned) ---
info("Calculating total physical memory maps...")
try:
    with open('/proc/meminfo', 'r') as f:
        meminfo = f.read()
    mem_total_kb = int(re.search(r"MemTotal:\s+(\d+)\s+kB", meminfo).group(1))
    mem_total_bytes = mem_total_kb * 1024
except Exception as e:
    fail(f"Could not parse /proc/meminfo: {e}")

# The kernel marks 'mem_limit' as Write-Only. We must read the 'mm_stat' matrix instead.
def verify_limit(device: str, expected_ratio: float):
    mm_stat_path = Path(f"/sys/block/{device}/mm_stat")
    if not mm_stat_path.exists():
        fail(f"Stats matrix for {device} does not exist. The device is dead.")
    
    try:
        stats = mm_stat_path.read_text().strip().split()
        if len(stats) < 4:
            fail(f"Invalid mm_stat matrix format for {device}.")
        actual_bytes = int(stats[3])  # 4th column is mem_limit
    except Exception as e:
        fail(f"Kernel rejected read on mm_stat for {device}: {e}")
        
    if actual_bytes == 0:
        fail(f"{device} memory limit is 0 (Unlimited). Systemd failed to cap the device.")

    expected_bytes = int(mem_total_bytes * expected_ratio)
    tolerance = expected_bytes * 0.05  # 5% tolerance for page alignment shifts
    
    if abs(actual_bytes - expected_bytes) <= tolerance:
        ok(f"{device} resident limit aligned correctly (~{actual_bytes / (1024**3):.2f} GB)")
    else:
        fail(f"{device} memory limit mismatch! Expected ~{expected_bytes}, found {actual_bytes}")

# --- 3. Base Swap Verification (zram0) ---
info("Verifying main ZRAM swap topology...")
zramctl_out = run_cmd(["zramctl", "--output", "NAME", "--noheadings"])
if "/dev/zram0" not in zramctl_out:
    fail("/dev/zram0 is missing. zram-generator failed to trigger for swap.")

swapon_out = run_cmd(["swapon", "--show=NAME,PRIO", "--noheadings"])
if "/dev/zram0" not in swapon_out:
    fail("/dev/zram0 is not mounted as swap.")
if "100" not in swapon_out:
    fail(f"zram0 swap priority is incorrect. Output:\n{swapon_out}")
ok("/dev/zram0 swap is fully active with Priority 100.")

verify_limit("zram0", 0.75)  # 3/4 Limit

# --- 4. Hybrid Mount Detection (zram1 / tmpfs) ---
info("Interrogating /mnt/zram1 mount backend...")
mount_source = run_cmd(["findmnt", "-rn", "-o", "SOURCE", "--mountpoint", "/mnt/zram1"])
mount_opts = run_cmd(["findmnt", "-rn", "-o", "OPTIONS", "--mountpoint", "/mnt/zram1"])

if not mount_source:
    fail("/mnt/zram1 is not currently mounted.")

if mount_source == "tmpfs":
    ok(f"Backend dynamically resolved as: Pure Tmpfs RAM disk.")
    if "uid=" in mount_opts and "gid=" in mount_opts:
         ok("Tmpfs user/group ownership mapping is intact.")
    else:
         fail("Tmpfs is missing user ownership mappings.")

elif mount_source in ("/dev/zram1", "zram1"):
    ok(f"Backend dynamically resolved as: Ext4 ZRAM Block.")
    verify_limit("zram1", 0.80)  # 4/5 Limit
    
    # Verify Ext4 Journal Annihilation
    dumpe2fs_out = run_cmd(["dumpe2fs", "-h", "/dev/zram1"])
    if "has_journal" in dumpe2fs_out:
        fail("Critical Failure: Ext4 journal was NOT stripped. Systemd ExecStartPost override failed.")
    ok("Ext4 filesystem confirmed as journal-less (Zero unnecessary write overhead).")
    
    # Verify Mount Options
    for opt in ["noatime", "lazytime", "discard", "rw"]:
        if opt not in mount_opts.split(","):
            fail(f"Missing critical mount option for zram block: {opt}")
    ok("Ext4 mount options strictly conform to zero-latency specifications.")
else:
    fail(f"Unknown mount source for /mnt/zram1: {mount_source}")

# --- 5. Algorithm Verification ---
info("Testing compression algorithm setup...")
for dev in ["zram0", "zram1"]:
    if dev == "zram1" and mount_source == "tmpfs":
        continue

    algo_path = Path(f"/sys/block/{dev}/comp_algorithm")
    if algo_path.exists():
        algo_data = algo_path.read_text().strip()
        if "[zstd]" in algo_data:
            ok(f"{dev} is running ZSTD natively.")
        else:
            fail(f"{dev} is running incorrect compression algorithm: {algo_data}")
    else:
        fail(f"{dev} does not expose compression algorithm control.")

print(f"\n{C.GRN}{C.BOLD}=== DIAGNOSTICS COMPLETE. ARCHITECTURE IS MATHEMATICALLY SOUND. ==={C.RST}\n")
