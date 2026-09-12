#!/usr/bin/env python3
"""
Deep ZRAM & Memory Architecture Diagnostics for modern Arch Linux (Kernel 7.2+, systemd 261+).
Non-destructively audits sysfs state, zswap, swap topology, and mount options.
Seamlessly supports both native tmpfs and ext4-on-zram1 RAM disks.
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

# --- Argument Parsing ---
parser = argparse.ArgumentParser(description="Deep ZRAM & Memory Architecture Diagnostics")
parser.add_argument("--strict", action="store_true", help="Exit with non-zero code on any warning or failure")
parser.add_argument("--no-color", action="store_true", help="Disable ANSI color output")
parser.add_argument("--json", action="store_true", help="Output machine-readable JSON report")
args = parser.parse_args()

class C:
    RED = "\033[1;31m"
    GRN = "\033[1;32m"
    YLW = "\033[1;33m"
    BLU = "\033[1;34m"
    CYN = "\033[1;36m"
    BOLD = "\033[1m"
    RST = "\033[0m"

    @classmethod
    def strip(cls) -> None:
        for attr in ("RED", "GRN", "YLW", "BLU", "CYN", "BOLD", "RST"):
            setattr(cls, attr, "")

if args.no_color or not sys.stdout.isatty() or "NO_COLOR" in os.environ:
    C.strip()

issues_detected: list[str] = []
warnings_detected: list[str] = []
audit_summary: dict = {
    "kernel": os.uname().release,
    "zswap": {},
    "zram0": {},
    "mnt_zram1": {},
    "sysfs_algorithms": {},
    "issues": [],
    "warnings": [],
    "status": "PASS"
}

def info(msg: str) -> None:
    if not args.json:
        print(f"{C.BLU}[INFO]{C.RST} {msg}")

def ok(msg: str) -> None:
    if not args.json:
        print(f"{C.GRN}[PASS]{C.RST} {msg}")

def warn(msg: str) -> None:
    warnings_detected.append(msg)
    audit_summary["warnings"].append(msg)
    if not args.json:
        print(f"{C.YLW}[WARN]{C.RST} {msg}")

def fail(msg: str) -> None:
    issues_detected.append(msg)
    audit_summary["issues"].append(msg)
    if not args.json:
        print(f"{C.RED}[FAIL]{C.RST} {msg}")

def run_cmd(cmd: list[str]) -> str:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return res.stdout.strip()
    except Exception:
        return ""

def main() -> int:
    if not args.json:
        print(f"\n{C.BOLD}=== Initiating Deep Architecture Diagnostics (Kernel 7.x+ Ready) ==={C.RST}\n")

    # 1. Bootloader / ZSWAP Check
    info("Checking ZSWAP state...")
    zswap_path = Path("/sys/module/zswap/parameters/enabled")
    if zswap_path.exists():
        try:
            val = zswap_path.read_text().strip()
            audit_summary["zswap"]["raw_value"] = val
            if val in ("Y", "1"):
                fail("ZSWAP is currently ACTIVE. Kernel parameter 'zswap.enabled=0' or /etc/tmpfiles.d/00-disable-zswap.conf is required with pure ZRAM.")
                audit_summary["zswap"]["disabled"] = False
            else:
                ok("ZSWAP is cleanly disabled at the kernel level.")
                audit_summary["zswap"]["disabled"] = True
        except Exception as e:
            warn(f"Could not read ZSWAP sysfs parameter: {e}")
            audit_summary["zswap"]["error"] = str(e)
    else:
        ok("ZSWAP module not loaded or not built-in (Clean).")
        audit_summary["zswap"]["disabled"] = True

    # 2. Comprehensive Configuration Discovery (zram-generator)
    info("Resolving zram-generator configuration hierarchy...")
    conf_search_dirs = [
        "/etc/systemd/zram-generator.conf.d",
        "/run/systemd/zram-generator.conf.d",
        "/usr/lib/systemd/zram-generator.conf.d",
    ]
    conf_files: list[Path] = []
    main_conf = Path("/etc/systemd/zram-generator.conf")
    if main_conf.is_file():
        conf_files.append(main_conf)

    for cdir in conf_search_dirs:
        p = Path(cdir)
        if p.is_dir():
            conf_files.extend(sorted(p.glob("*.conf")))

    zram_configs: dict[str, dict[str, str]] = {}
    for cf in conf_files:
        cp = configparser.ConfigParser(strict=False)
        try:
            cp.read(cf)
            for section in cp.sections():
                sec_clean = section.strip().lower()
                if sec_clean not in zram_configs:
                    zram_configs[sec_clean] = {}
                zram_configs[sec_clean].update(dict(cp.items(section)))
        except Exception as e:
            warn(f"Failed parsing drop-in {cf}: {e}")

    audit_summary["discovered_configs"] = {k: v for k, v in zram_configs.items()}

    # 3. Physical Memory Calculations
    info("Calculating total physical memory maps...")
    mem_total_bytes = 0
    try:
        meminfo = Path("/proc/meminfo").read_text()
        m = re.search(r"MemTotal:\s+(\d+)\s+kB", meminfo)
        if m:
            mem_total_bytes = int(m.group(1)) * 1024
            ok(f"Physical memory detected: {mem_total_bytes / (1024**3):.2f} GiB")
            audit_summary["mem_total_bytes"] = mem_total_bytes
    except Exception as e:
        fail(f"Could not parse /proc/meminfo: {e}")

    # 4. Auditing zram0 (Swap)
    info("Verifying main ZRAM swap topology (zram0)...")
    zramctl_out = run_cmd(["zramctl", "--output", "NAME", "--noheadings"])
    zram0_present = "zram0" in zramctl_out or "/dev/zram0" in zramctl_out

    if not zram0_present:
        fail("/dev/zram0 is not active in zramctl. (systemctl restart systemd-zram-setup@zram0.service may be required).")
        audit_summary["zram0"]["status"] = "missing"
    else:
        swapon_out = run_cmd(["swapon", "--show=NAME,PRIO", "--noheadings"])
        zram0_swapon = "zram0" in swapon_out or "/dev/zram0" in swapon_out

        if not zram0_swapon and Path("/proc/swaps").exists():
            swaps_content = Path("/proc/swaps").read_text()
            zram0_swapon = "zram0" in swaps_content

        if not zram0_swapon:
            fail("/dev/zram0 exists but is not currently active as swap in /proc/swaps.")
            audit_summary["zram0"]["status"] = "inactive"
        else:
            ok("/dev/zram0 swap is fully active.")
            audit_summary["zram0"]["status"] = "active"

            prio_found: int | None = None
            if swapon_out:
                for line in swapon_out.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and "zram0" in parts[0]:
                        try:
                            prio_found = int(parts[1])
                            break
                        except ValueError:
                            pass
            elif Path("/proc/swaps").exists():
                for line in Path("/proc/swaps").read_text().splitlines():
                    parts = line.split()
                    if len(parts) >= 5 and "zram0" in parts[0]:
                        try:
                            prio_found = int(parts[4])
                            break
                        except ValueError:
                            pass

            if prio_found is not None:
                audit_summary["zram0"]["priority"] = prio_found
                if prio_found >= 32767:
                    ok(f"/dev/zram0 swap priority confirmed at maximum ({prio_found}).")
                elif prio_found >= 100:
                    ok(f"/dev/zram0 swap priority confirmed positive ({prio_found}).")
                elif prio_found > 0:
                    info(f"/dev/zram0 swap priority is {prio_found} (Priority >= 100 recommended to override disk swap).")
                else:
                    warn(f"/dev/zram0 swap priority is low ({prio_found}). ZRAM should have higher priority than disk swap.")

        mm_stat_path = Path("/sys/block/zram0/mm_stat")
        if mm_stat_path.exists():
            try:
                stats = mm_stat_path.read_text().strip().split()
                if len(stats) >= 4:
                    orig_size = int(stats[0])
                    compr_size = int(stats[1])
                    mem_used = int(stats[2])
                    mem_limit = int(stats[3])

                    audit_summary["zram0"]["orig_data_size"] = orig_size
                    audit_summary["zram0"]["compr_data_size"] = compr_size
                    audit_summary["zram0"]["mem_used_total"] = mem_used
                    audit_summary["zram0"]["mem_limit"] = mem_limit

                    if mem_limit == 0:
                        ok("/dev/zram0 memory resident limit is uncapped (0 / unlimited).")
                    else:
                        limit_mib = mem_limit / (1024**2)
                        if mem_total_bytes > 0:
                            pct = (mem_limit / mem_total_bytes) * 100
                            ok(f"/dev/zram0 memory resident limit active: {limit_mib:.1f} MiB (~{pct:.0f}% of RAM).")
                        else:
                            ok(f"/dev/zram0 memory resident limit active: {limit_mib:.1f} MiB.")
                else:
                    fail(f"Invalid mm_stat format for zram0 (found {len(stats)} columns, expected >= 4).")
            except Exception as e:
                fail(f"Failed to read /sys/block/zram0/mm_stat: {e}")
        else:
            fail("Sysfs node /sys/block/zram0/mm_stat does not exist.")

    # 5. Auditing zram1 (/mnt/zram1): tmpfs OR ext4-on-zram1
    info("Interrogating /mnt and /mnt/zram1 filesystem & permissions...")

    mnt_path = Path("/mnt")
    if not mnt_path.exists():
        fail("/mnt mount directory does not exist.")
    else:
        try:
            st = mnt_path.stat()
            mode_oct = oct(stat.S_IMODE(st.st_mode))
            if st.st_mode & stat.S_IXOTH:
                ok(f"/mnt base traversal permissions intact ({mode_oct}).")
            else:
                warn(f"/mnt permissions restricted ({mode_oct}). Directory needs +x for user traversal.")
        except Exception as e:
            fail(f"Cannot stat /mnt: {e}")

        if shutil.which("getfacl"):
            facl_out = run_cmd(["getfacl", "-p", "/mnt"])
            custom_acls = [l for l in facl_out.splitlines() if l.startswith("user:") and not l.startswith("user::")]
            if custom_acls:
                info(f"Custom POSIX ACL detected on /mnt: {', '.join(custom_acls)}")

    mount_source = run_cmd(["findmnt", "-rn", "-o", "SOURCE", "--mountpoint", "/mnt/zram1"])
    mount_opts = run_cmd(["findmnt", "-rn", "-o", "OPTIONS", "--mountpoint", "/mnt/zram1"])
    mount_fstype = run_cmd(["findmnt", "-rn", "-o", "FSTYPE", "--mountpoint", "/mnt/zram1"])

    audit_summary["mnt_zram1"]["source"] = mount_source
    audit_summary["mnt_zram1"]["fstype"] = mount_fstype
    audit_summary["mnt_zram1"]["options"] = mount_opts

    if not mount_source:
        if "zram1" in zram_configs:
            info("Secondary /mnt/zram1 is declared in zram-generator config (Staged / pending mount).")
            audit_summary["mnt_zram1"]["status"] = "staged_zram"
        else:
            ok("Secondary RAM disk (/mnt/zram1) resolved as: Disabled / Inactive (Zero memory overhead).")
            audit_summary["mnt_zram1"]["status"] = "inactive"

    elif mount_fstype == "tmpfs" or mount_source == "tmpfs":
        ok("Backend dynamically resolved as: High-Performance tmpfs RAM disk.")
        audit_summary["mnt_zram1"]["status"] = "active_tmpfs"

        opts_list = [opt.strip() for opt in mount_opts.split(",") if opt.strip()]
        size_opt = [o for o in opts_list if o.startswith("size=")]
        if size_opt:
            ok(f"tmpfs allocation size limit confirmed: {size_opt[0]}.")
        else:
            info("tmpfs mounted with default kernel size (50% RAM).")

        for req_opt in ["noatime", "nosuid", "nodev", "rw"]:
            if req_opt in opts_list:
                ok(f"tmpfs mount option '{req_opt}' active.")
            else:
                warn(f"tmpfs mount option '{req_opt}' not active (recommended for ephemeral tmpfs).")

        # Audit transparent hugepages policy (huge=never is recommended per 206_zram_tmpfs_mounts.py)
        mount_unit_path = Path("/etc/systemd/system/mnt-zram1.mount")
        mount_unit_has_huge_never = False
        if mount_unit_path.exists():
            try:
                mount_unit_has_huge_never = "huge=never" in mount_unit_path.read_text(encoding="utf-8")
            except Exception:
                pass

        huge_opt = [o for o in opts_list if o.startswith("huge=")]
        if huge_opt and huge_opt[0] != "huge=never":
            warn(f"tmpfs transparent hugepages active: '{huge_opt[0]}' (huge=never recommended per 206_zram_tmpfs_mounts.py to prevent 2MB fragmentation/RAM bloat).")
            audit_summary["mnt_zram1"]["huge"] = huge_opt[0]
        elif mount_unit_has_huge_never or any(o == "huge=never" for o in opts_list):
            ok("tmpfs hugepage policy verified: huge=never (lowest idle RAM, zero internal fragmentation).")
            audit_summary["mnt_zram1"]["huge"] = "never"
        else:
            info("tmpfs mounted with kernel default hugepage policy.")
            audit_summary["mnt_zram1"]["huge"] = "default"

        zram1_dir = Path("/mnt/zram1")
        try:
            dst = zram1_dir.stat()
            dmode = stat.S_IMODE(dst.st_mode)
            audit_summary["mnt_zram1"]["mode"] = oct(dmode)
            if dmode == 0o1777 or (dmode & 0o777) == 0o777:
                ok(f"/mnt/zram1 permissions verified (Mode: {oct(dmode)} - Fully User Writable with Sticky Bit).")
            elif os.access(str(zram1_dir), os.W_OK):
                ok(f"/mnt/zram1 is directly writable by process UID {os.getuid()} (Mode: {oct(dmode)}).")
            else:
                warn(f"/mnt/zram1 mode is {oct(dmode)}; mode 1777 is recommended for shared ephemeral storage.")
        except Exception as e:
            fail(f"Could not stat /mnt/zram1: {e}")

    elif mount_source in ("/dev/zram1", "zram1") or Path(mount_source).name == "zram1":
        ok("Backend dynamically resolved as: Ext4 ZRAM Block Device (/dev/zram1).")
        audit_summary["mnt_zram1"]["status"] = "active_ext4_zram"

        mm_stat_zram1 = Path("/sys/block/zram1/mm_stat")
        if mm_stat_zram1.exists():
            try:
                stats = mm_stat_zram1.read_text().strip().split()
                if len(stats) >= 4:
                    limit_b = int(stats[3])
                    audit_summary["mnt_zram1"]["mem_limit"] = limit_b
                    if limit_b > 0 and mem_total_bytes > 0:
                        ratio = limit_b / mem_total_bytes
                        ok(f"zram1 resident limit active: {limit_b / (1024**3):.2f} GiB (~{ratio*100:.1f}% of RAM).")
                    elif limit_b > 0:
                        ok(f"zram1 resident limit active: {limit_b / (1024**3):.2f} GiB.")
                    else:
                        info("zram1 resident limit is uncapped (0 / unlimited).")
            except Exception as e:
                warn(f"Could not parse /sys/block/zram1/mm_stat: {e}")

        if os.geteuid() == 0:
            fs_info = ""
            if shutil.which("dumpe2fs"):
                fs_info = run_cmd(["dumpe2fs", "-h", "/dev/zram1"])
            elif shutil.which("tune2fs"):
                fs_info = run_cmd(["tune2fs", "-l", "/dev/zram1"])

            if fs_info:
                if "has_journal" in fs_info:
                    warn("Ext4 journal is enabled on zram1 (disabling journal recommended to save RAM write overhead).")
                    audit_summary["mnt_zram1"]["journal_disabled"] = False
                else:
                    ok("Ext4 filesystem confirmed as journal-less (Zero unnecessary RAM write overhead).")
                    audit_summary["mnt_zram1"]["journal_disabled"] = True
            else:
                info("dumpe2fs/tune2fs not available; skipping ext4 journal inspection.")
        else:
            info("Running as non-root; skipped raw block inspection for ext4 journal (run with sudo to verify).")

        opts_list = [opt.split("=")[0].strip() for opt in mount_opts.split(",") if opt.strip()]
        for req_opt in ["noatime", "discard", "rw", "lazytime"]:
            if req_opt in opts_list:
                ok(f"Ext4 mount option '{req_opt}' active.")
            else:
                warn(f"Ext4 mount option '{req_opt}' not active (recommended for zram block).")

        zram1_dir = Path("/mnt/zram1")
        try:
            dst = zram1_dir.stat()
            dmode = stat.S_IMODE(dst.st_mode)
            audit_summary["mnt_zram1"]["mode"] = oct(dmode)
            if dmode == 0o1777 or (dmode & 0o777) == 0o777:
                ok(f"/mnt/zram1 mount permissions verified (Mode: {oct(dmode)} - Fully User Writable).")
            elif os.access(str(zram1_dir), os.W_OK):
                ok(f"/mnt/zram1 is directly writable by process UID {os.getuid()} (Mode: {oct(dmode)}).")
            else:
                fail(f"/mnt/zram1 lacks user write permissions (Mode: {oct(dmode)}). Run `chmod 1777 /mnt/zram1`.")
        except Exception as e:
            fail(f"Could not stat /mnt/zram1: {e}")

    else:
        info(f"Custom mount source for /mnt/zram1: {mount_source} (FSType: {mount_fstype})")

    # 6. Algorithm Verification & Multi-Comp Diagnostics
    info("Testing compression algorithm setup...")
    for dev in ["zram0", "zram1"]:
        algo_path = Path(f"/sys/block/{dev}/comp_algorithm")
        recomp_path = Path(f"/sys/block/{dev}/recomp_algorithm")

        if algo_path.exists():
            algo_data = algo_path.read_text().strip()
            m = re.search(r"\[([^\]]+)\]", algo_data)
            active_algo = m.group(1) if m else algo_data
            ok(f"{dev} primary compressor: {active_algo}")
            audit_summary["sysfs_algorithms"][f"{dev}_primary"] = active_algo

            if recomp_path.exists():
                recomp_data = recomp_path.read_text().strip()
                if recomp_data:
                    info(f"{dev} secondary recompression algorithms: {recomp_data}")
                    audit_summary["sysfs_algorithms"][f"{dev}_secondary"] = recomp_data
        else:
            if dev == "zram0":
                info(f"{dev} sysfs comp_algorithm node not exposed.")

    # 7. Summary & Exit Code
    if args.json:
        audit_summary["status"] = "FAIL" if issues_detected or (warnings_detected and args.strict) else "PASS"
        print(json.dumps(audit_summary, indent=2))
        if issues_detected or (warnings_detected and args.strict):
            return 1
        return 0

    print("")
    if issues_detected:
        print(f"{C.RED}{C.BOLD}=== DIAGNOSTICS DETECTED {len(issues_detected)} CRITICAL FAILURE(S) ==={C.RST}")
        for item in issues_detected:
            print(f"  {C.RED}•{C.RST} {item}")
        return 1
    elif warnings_detected and args.strict:
        print(f"{C.YLW}{C.BOLD}=== DIAGNOSTICS DETECTED {len(warnings_detected)} WARNING(S) (STRICT MODE FAILED) ==={C.RST}")
        for item in warnings_detected:
            print(f"  {C.YLW}•{C.RST} {item}")
        return 1
    else:
        print(f"{C.GRN}{C.BOLD}=== DIAGNOSTICS COMPLETE. SYSTEM ARCHITECTURE VERIFIED CLEANLY. ==={C.RST}\n")
        return 0

if __name__ == "__main__":
    sys.exit(main())

