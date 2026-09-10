#!/usr/bin/env python3
#d: Configure DAMON memory reclamation

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import tempfile
from pathlib import Path
from typing import NoReturn, Dict

TMPFILES_FILE = Path("/etc/tmpfiles.d/99-damon-reclaim.conf")
DAMON_PARAMS_DIR = Path("/sys/module/damon_reclaim/parameters")

# Unified 4-Tier Memory Demarcation (MemTotal in GiB)
TIER_S_CONFIG: Dict[str, int | str] = {
    "sample_interval": 500000,
    "aggr_interval": 5000000,
    "min_age": 60000000,         # 60s
    "wmarks_high": 800,
    "wmarks_mid": 700,
    "wmarks_low": 50,
    "wmarks_interval": 5000000,
    "quota_ms": 100,
    "quota_sz": 268435456,       # 256 MiB
    "quota_reset_interval_ms": 1000,
    "min_nr_regions": 10,
    "max_nr_regions": 1000,
    "skip_anon": "N",
    "addr_unit": 1,
    "quota_mem_pressure_us": 1000,
    "quota_autotune_feedback": 0,
}

TIER_M_CONFIG: Dict[str, int | str] = {
    **TIER_S_CONFIG,
    "min_age": 120000000,        # 120s (2 min)
}

TIER_L_CONFIG: Dict[str, int | str] = {
    **TIER_S_CONFIG,
    "min_age": 300000000,        # 300s (5 min)
    "wmarks_high": 500,
    "wmarks_mid": 400,
    "quota_ms": 50,
}

TIER_XL_CONFIG: Dict[str, int | str] = {
    **TIER_S_CONFIG,
    "sample_interval": 1000000,
    "min_age": 600000000,        # 600s (10 min)
    "wmarks_high": 400,
    "wmarks_mid": 300,
    "quota_ms": 50,
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
def info(m):
    if not QUIET: print(f"{C.BLU}[INFO]{C.RST} {m}")
def ok(m):
    if not QUIET: print(f"{C.GRN}[ OK ]{C.RST} {m}")
def warn(m): print(f"{C.YLW}[WARN]{C.RST} {m}")
def err(m): print(f"{C.RED}[FAIL]{C.RST} {m}", file=sys.stderr)
def die(m, code=1) -> NoReturn:
    err(m); sys.exit(code)

def detect_ram_gb() -> float:
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
        m = re.search(r"^MemTotal:\s+(\d+)\s+kB", text, re.M)
        if not m: die("Could not parse MemTotal from /proc/meminfo")
        return int(m.group(1)) / 1_048_576
    except FileNotFoundError:
        die("/proc/meminfo not found - not running on Linux?")
    except Exception as e:
        die(f"Failed to read RAM capacity: {e}")

def validate_profile(cfg, label):
    si=int(cfg["sample_interval"]); ai=int(cfg["aggr_interval"])
    if ai < si: die(f"{label}: aggr_interval ({ai}) must be >= sample_interval ({si})")
    if ai % si!= 0: warn(f"{label}: aggr {ai} not multiple of sample {si} - may be rejected")
    wh,wm,wl=int(cfg["wmarks_high"]),int(cfg["wmarks_mid"]),int(cfg["wmarks_low"])
    for n,v in (("wmarks_high",wh),("wmarks_mid",wm),("wmarks_low",wl)):
        if not 0 <= v <= 1000: die(f"{label}: {n}={v} out of 0-1000")
    if not (wh >= wm >= wl): die(f"{label}: watermark ordering high>=mid>=low violated {wh}>={wm}>={wl}")
    if int(cfg["min_nr_regions"]) < 3: die(f"{label}: min_nr_regions must be >=3")
    if int(cfg["max_nr_regions"]) < int(cfg["min_nr_regions"]): die("max_nr_regions < min_nr_regions")

def atomic_write_text(target: Path, content: str, mode: int = 0o644):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.tmp.")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content); f.flush(); os.fsync(f.fileno())
        if os.geteuid()==0:
            try: os.chown(tmp,0,0)
            except: pass
        os.rename(tmp, target)
    finally:
        try:
            if Path(tmp).exists(): Path(tmp).unlink()
        except: pass

def sysfs_write(p: Path, v: str):
    try: p.write_text(v, encoding="utf-8")
    except Exception as e: die(f"Failed to write {p}={v!r}: {e}")
def sysfs_read(p: Path) -> str:
    try: return p.read_text(encoding="utf-8").strip()
    except Exception as e: die(f"Failed to read {p}: {e}")
def wait_for(pred, timeout=5.0, interval=0.1, desc=""):
    dl=time.monotonic()+timeout
    while time.monotonic()<dl:
        if pred(): return True
        time.sleep(interval)
    if desc: warn(f"Timeout waiting for {desc}")
    return False

def apply_live_params(cfg):
    required=["sample_interval","aggr_interval","min_age","wmarks_high","wmarks_mid","wmarks_low"]
    for n in required:
        if not (DAMON_PARAMS_DIR/n).is_file(): die(f"Missing {DAMON_PARAMS_DIR/n}")

    # Ensure conflicting DAMON modules step aside first (exclusive in kernel)
    for mod in ["damon_stat", "damon_lru_sort"]:
        mod_param = Path(f"/sys/module/{mod}/parameters/enabled")
        if mod_param.is_file():
            try:
                if mod_param.read_text(encoding="utf-8").strip() == "Y":
                    info(f"Disabling conflicting {mod} module to ensure -EBUSY avoidance...")
                    mod_param.write_text("N", encoding="utf-8")
            except Exception as e:
                warn(f"Could not disable conflicting {mod}: {e}")

    has_commit=(DAMON_PARAMS_DIR/"commit_inputs").is_file()
    has_enabled=(DAMON_PARAMS_DIR/"enabled").is_file()
    has_pid=(DAMON_PARAMS_DIR/"kdamond_pid").is_file()
    enabled_path=DAMON_PARAMS_DIR/"enabled"; commit_path=DAMON_PARAMS_DIR/"commit_inputs"
    cur=sysfs_read(enabled_path) if has_enabled else "N"
    info(f"Current enabled state: {cur}")
    order=["sample_interval","aggr_interval","min_age","wmarks_high","wmarks_mid","wmarks_low","wmarks_interval","quota_ms","quota_sz","quota_reset_interval_ms","min_nr_regions","max_nr_regions","addr_unit","skip_anon","quota_mem_pressure_us","quota_autotune_feedback"]
    def write_all():
        for k in order:
            if k in cfg:
                p=DAMON_PARAMS_DIR/k
                if p.is_file(): sysfs_write(p,str(cfg[k])); ok(f" {k} = {cfg[k]}")
                else: warn(f" {k} not present, skipping")
    if cur=="Y" and has_commit:
        info("DAMON_RECLAIM running - using online tuning via commit_inputs")
        write_all(); sysfs_write(commit_path,"Y")
        info("Waiting for commit_inputs to return to N...")
        if not wait_for(lambda: sysfs_read(commit_path)=="N", timeout=15.0, desc="commit_inputs==N"): die("commit_inputs did not return to N - check dmesg")
        ok("Online commit completed")
    else:
        if has_enabled:
            info("Disabling for clean reconfiguration...")
            sysfs_write(enabled_path,"N")
            if has_pid: wait_for(lambda: sysfs_read(DAMON_PARAMS_DIR/"kdamond_pid")=="-1", timeout=3.0, desc="kdamond_pid==-1")
        write_all()
        if has_enabled:
            sysfs_write(enabled_path,"Y"); ok("Enabled DAMON_RECLAIM")
            if has_pid and not wait_for(lambda: sysfs_read(DAMON_PARAMS_DIR/"kdamond_pid") not in ("-1",""), timeout=3.0, desc="kdamond_pid active"):
                warn("kdamond_pid still -1 after enable - may be watermark inactive or conflicting DAMON module")

def verify_live(cfg):
    errs=[]
    for k in ["sample_interval","aggr_interval","min_age","wmarks_high","wmarks_mid","wmarks_low","wmarks_interval","quota_ms","quota_sz","quota_reset_interval_ms","min_nr_regions","max_nr_regions","quota_mem_pressure_us"]:
        if k in cfg:
            p=DAMON_PARAMS_DIR/k
            if p.is_file():
                a=sysfs_read(p); e=str(cfg[k])
                if a!=e: errs.append(f"{k}: expected {e}, got {a}")
    en=sysfs_read(DAMON_PARAMS_DIR/"enabled")
    pid=sysfs_read(DAMON_PARAMS_DIR/"kdamond_pid") if (DAMON_PARAMS_DIR/"kdamond_pid").is_file() else "unknown"
    if en!="Y": errs.append(f"enabled expected Y got {en}")
    if pid=="-1":
        for mod in ["damon_stat", "damon_lru_sort"]:
            mod_en = Path(f"/sys/module/{mod}/parameters/enabled")
            if mod_en.is_file() and mod_en.read_text(encoding="utf-8").strip() == "Y":
                errs.append(f"kdamond_pid is -1 due to module conflict: {mod} is running!")
        if not errs:
            warn("kdamond_pid is -1 even though enabled=Y - watermark inactive (free memory is above wmarks_mid)")
    if errs:
        for e in errs: err(e)
        die("Verification failed")
    ok(f"Verified: enabled={en}, kdamond_pid={pid}")
    for k in ["sample_interval","aggr_interval","min_age","wmarks_high","wmarks_mid","wmarks_low","quota_ms","quota_sz","quota_mem_pressure_us"]:
        if k in cfg: ok(f" {k} = {sysfs_read(DAMON_PARAMS_DIR/k)}")

def main(argv):
    ap=argparse.ArgumentParser(prog="damon_reclaim_optimizer", description="Configure DAMON Reclaim (kernel 7.1+, systemd 261+, Python 3.14+)")
    ap.add_argument("-n","--dry-run",action="store_true"); ap.add_argument("--no-color",action="store_true"); ap.add_argument("--force",action="store_true")
    args=ap.parse_args(argv)
    if args.no_color or not sys.stdout.isatty() or "NO_COLOR" in os.environ: C.strip()
    if os.geteuid()!=0 and not args.dry_run:
        info("root required — escalating via sudo")
        sudo="/usr/bin/sudo" if Path("/usr/bin/sudo").is_file() else "sudo"
        py=sys.executable
        if not py or not Path(py).is_absolute(): die("sys.executable not absolute")
        os.execvp(sudo,[sudo,"--",py,str(Path(__file__).resolve()),*argv])
    if not DAMON_PARAMS_DIR.is_dir():
        info("DAMON Reclaim not found at /sys/module/damon_reclaim/parameters. Skipping."); return 0
    ram=detect_ram_gb(); info(f"Detected RAM: {C.BOLD}{ram:.2f} GiB{C.RST}")
    if ram < 7.0:
        label="STRICT_RAM_SAVINGS (<8GB class)"; blurb="Aggressive: 500ms sample, 5s aggr, 60s cold age, reclaim when free <70% down to 5%"; cfg=TIER_S_CONFIG
    elif ram < 14.0:
        label="DYNAMIC_EFFICIENCY (8-12GB class)"; blurb="Dynamic: 500ms sample, 5s aggr, 120s cold age, reclaim when free <70% down to 5%"; cfg=TIER_M_CONFIG
    elif ram < 28.0:
        label="BALANCED_EFFICIENCY (16-24GB class)"; blurb="Balanced: 500ms sample, 5s aggr, 300s cold age, reclaim when free <40% down to 5%"; cfg=TIER_L_CONFIG
    else:
        label="PERFORMANCE_LEAN (>=32GB class)"; blurb="Conservative: 1s sample, 5s aggr, 600s cold age, reclaim when free <30% down to 5%"; cfg=TIER_XL_CONFIG
    validate_profile(cfg,label); info(f"Selected: {C.BOLD}{label}{C.RST} — {C.DIM}{blurb}{C.RST}")
    lines=[
        f"# Managed by {Path(__file__).name} - {label}",
        f"# Static configuration tuned for {ram:.2f} GiB system RAM",
        "#",
        "# Step aside conflicting DAMON modules first (exclusive in kernel)",
        "w- /sys/module/damon_stat/parameters/enabled - - - - N",
        "w- /sys/module/damon_lru_sort/parameters/enabled - - - - N",
        "",
        "# Monitoring intervals (us)",
        f"w- /sys/module/damon_reclaim/parameters/sample_interval - - - - {cfg['sample_interval']}",
        f"w- /sys/module/damon_reclaim/parameters/aggr_interval - - - - {cfg['aggr_interval']}",
        f"w- /sys/module/damon_reclaim/parameters/min_age - - - - {cfg['min_age']}",
        f"w- /sys/module/damon_reclaim/parameters/wmarks_interval - - - - {cfg['wmarks_interval']}",
        "",
        "# Watermarks per-thousand",
        f"w- /sys/module/damon_reclaim/parameters/wmarks_high - - - - {cfg['wmarks_high']}",
        f"w- /sys/module/damon_reclaim/parameters/wmarks_mid - - - - {cfg['wmarks_mid']}",
        f"w- /sys/module/damon_reclaim/parameters/wmarks_low - - - - {cfg['wmarks_low']}",
        "",
        "# Quotas & PSI Back-off feedback",
        f"w- /sys/module/damon_reclaim/parameters/quota_ms - - - - {cfg['quota_ms']}",
        f"w- /sys/module/damon_reclaim/parameters/quota_sz - - - - {cfg['quota_sz']}",
        f"w- /sys/module/damon_reclaim/parameters/quota_reset_interval_ms - - - - {cfg['quota_reset_interval_ms']}",
        f"w- /sys/module/damon_reclaim/parameters/quota_mem_pressure_us - - - - {cfg['quota_mem_pressure_us']}",
        f"w- /sys/module/damon_reclaim/parameters/quota_autotune_feedback - - - - {cfg['quota_autotune_feedback']}",
        "",
        "# Prevent monitoring interval drift on idle desktop",
        "w- /sys/module/damon_reclaim/parameters/autotune_monitoring_intervals - - - - N",
        "",
        "# Regions and behavior",
        f"w- /sys/module/damon_reclaim/parameters/min_nr_regions - - - - {cfg['min_nr_regions']}",
        f"w- /sys/module/damon_reclaim/parameters/max_nr_regions - - - - {cfg['max_nr_regions']}",
        f"w- /sys/module/damon_reclaim/parameters/addr_unit - - - - {cfg['addr_unit']}",
        f"w- /sys/module/damon_reclaim/parameters/skip_anon - - - - {cfg['skip_anon']}",
        "",
        "# Enable DAMON reclaim scheme",
        "w- /sys/module/damon_reclaim/parameters/enabled - - - - Y",
        ""
    ]
    content="\n".join(lines)+"\n"
    if args.dry_run:
        print(f"\n{C.BOLD}[ DRY RUN: Would write to {TMPFILES_FILE} ]{C.RST}"); print(content); return 0
    if TMPFILES_FILE.is_file() and not args.force:
        if TMPFILES_FILE.read_text(encoding="utf-8")==content: info("Existing config matches, skipping write")
        else: atomic_write_text(TMPFILES_FILE,content); ok(f"Wrote {TMPFILES_FILE}")
    else:
        atomic_write_text(TMPFILES_FILE,content); ok(f"Wrote {TMPFILES_FILE}")
    info("Applying live..."); apply_live_params(cfg); verify_live(cfg)
    ok("Completed successfully"); return 0

if __name__=="__main__":
    try: sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print(f"\n{C.YLW}aborted — nothing written.{C.RST}"); sys.exit(130)
