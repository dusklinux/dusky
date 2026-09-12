#!/usr/bin/env python3
"""
Dusky Proactive ZRAM Swap Modifier & Control Center Backend
Allows dynamic, runtime management of the MGLRU proactive idle memory skimmer:
- Timer state (Active / Disabled)
- Manual sweep trigger (Run Now)
- Per-app idle anon reclaim percentage (APP_IDLE_RECLAIM_RATIO)
- Max memory sweep budget ceiling (MAX_PER_RUN)
- Background sweep timer interval (OnUnitActiveSec)
- ZRAM capacity safety abort threshold (ZRAM_MAX_USAGE_RATIO)
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NoReturn

# --- ANSI Formatting ---
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
        for name in ("RED", "GRN", "YLW", "BLU", "CYN", "BOLD", "RST"):
            setattr(cls, name, "")

def info(msg: str) -> None: print(f"{C.BLU}[INFO]{C.RST} {msg}")
def ok(msg: str) -> None: print(f"{C.GRN}[ OK ]{C.RST} {msg}")
def warn(msg: str) -> None: print(f"{C.YLW}[WARN]{C.RST} {msg}")
def err(msg: str) -> None: print(f"{C.RED}[FAIL]{C.RST} {msg}", file=sys.stderr)
def die(msg: str, code: int = 1) -> NoReturn:
    err(msg)
    sys.exit(code)

# --- Configuration & Paths ---
CONF_DIR = Path("/etc/dusky")
CONF_FILE = CONF_DIR / "dusky_pro_active_zram_swap.conf"
TIMER_UNIT = Path("/etc/systemd/system/dusky_pro_active_zram_swap.timer")
SERVICE_UNIT = Path("/etc/systemd/system/dusky_pro_active_zram_swap.service")
BIN_PATH = Path("/usr/local/bin/dusky_pro_active_zram_swap")

def get_setup_script_path() -> Path:
    """Dynamically resolve the 217 setup script path without hardcoding any user or home directory."""
    try:
        # Relative to current script location: ../../arch_setup_scripts/scripts/217_pro_active_zram_swap.py
        cand = Path(__file__).resolve().parents[2] / "arch_setup_scripts" / "scripts" / "217_pro_active_zram_swap.py"
        if cand.exists():
            return cand
    except Exception:
        pass
    try:
        _, _, _, home_dir = get_real_user_info()
        cand = home_dir / "user_scripts" / "arch_setup_scripts" / "scripts" / "217_pro_active_zram_swap.py"
        if cand.exists():
            return cand
    except Exception:
        pass
    return Path.home() / "user_scripts" / "arch_setup_scripts" / "scripts" / "217_pro_active_zram_swap.py"

DEFAULT_RATIO = 0.40        # 40% per idle app
DEFAULT_BUDGET_MB = 256     # 256 MB per run
DEFAULT_CHUNK_MB = 32       # 32 MB write chunks per yield
DEFAULT_ZRAM_LIMIT = 0.95   # 95% full zram abort
DEFAULT_INTERVAL = "3min"   # Periodic sweep interval

def write_file_atomic(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

def get_real_user_info() -> tuple[str, int, int, Path]:
    sudo_user = os.environ.get("SUDO_USER")
    sudo_uid = os.environ.get("SUDO_UID")
    sudo_gid = os.environ.get("SUDO_GID")
    if sudo_user and sudo_uid and int(sudo_uid) != 0:
        try:
            import pwd
            pw = pwd.getpwnam(sudo_user)
            return sudo_user, int(sudo_uid), int(sudo_gid or pw.pw_gid), Path(pw.pw_dir)
        except Exception:
            pass
    try:
        import pwd
        loginuid_path = Path("/proc/self/loginuid")
        if loginuid_path.exists():
            l_uid = int(loginuid_path.read_text().strip())
            if 0 < l_uid < 65534:
                pw = pwd.getpwuid(l_uid)
                return pw.pw_name, pw.pw_uid, pw.pw_gid, Path(pw.pw_dir)
        for pw in pwd.getpwall():
            if 1000 <= pw.pw_uid < 60000 and pw.pw_name != "nobody":
                return pw.pw_name, pw.pw_uid, pw.pw_gid, Path(pw.pw_dir)
    except Exception:
        pass
    return "root", 0, 0, Path("/root")

def notify(title: str, message: str, urgency: str = "normal") -> None:
    if shutil.which("notify-send"):
        try:
            user, uid, _, _ = get_real_user_info()
            if user != "root" and os.geteuid() == 0:
                runtime_dir = f"/run/user/{uid}"
                env = os.environ.copy()
                env["XDG_RUNTIME_DIR"] = runtime_dir
                subprocess.run(
                    ["sudo", "-u", user, "notify-send", "-a", "Dusky Proactive Swap", "-u", urgency, title, message],
                    env=env,
                    check=False,
                    timeout=2
                )
            else:
                subprocess.run(["notify-send", "-a", "Dusky Proactive Swap", "-u", urgency, title, message], check=False, timeout=2)
        except Exception:
            pass

def escalate_root_if_needed() -> None:
    if os.geteuid() != 0:
        if shutil.which("sudo"):
            os.execvp("sudo", ["sudo", sys.executable, os.path.abspath(__file__)] + sys.argv[1:])
        elif shutil.which("pkexec"):
            os.execvp("pkexec", ["pkexec", sys.executable, os.path.abspath(__file__)] + sys.argv[1:])
        else:
            die("Root privileges required to modify proactive swap settings.")

def sync_binary_if_needed() -> None:
    """Ensures /usr/local/bin/dusky_pro_active_zram_swap is synchronized with the source setup script."""
    if os.geteuid() != 0:
        return
    try:
        setup_script = get_setup_script_path()
        if not setup_script.exists():
            return
        if not BIN_PATH.exists() or setup_script.read_bytes() != BIN_PATH.read_bytes():
            BIN_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(setup_script, BIN_PATH)
            os.chmod(BIN_PATH, 0o755)
            ok(f"Synchronized binary to {BIN_PATH}")
    except Exception as e:
        warn(f"Could not sync binary: {e}")

# --- Config Management ---
def read_config() -> dict[str, str]:
    config: dict[str, str] = {
        "APP_IDLE_RECLAIM_RATIO": str(DEFAULT_RATIO),
        "MAX_PER_RUN_MB": str(DEFAULT_BUDGET_MB),
        "CHUNK_SIZE_MB": str(DEFAULT_CHUNK_MB),
        "ZRAM_MAX_USAGE_RATIO": str(DEFAULT_ZRAM_LIMIT),
        "TIMER_INTERVAL": DEFAULT_INTERVAL,
    }
    if CONF_FILE.exists():
        try:
            with open(CONF_FILE, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    config[k.strip()] = v.strip().strip("\"'")
        except Exception:
            pass
    return config

def write_config(conf: dict[str, str]) -> None:
    CONF_DIR.mkdir(parents=True, exist_ok=True)
    content = f"""# Dusky Proactive ZRAM Swap Runtime Configuration
# Dynamically consumed by /usr/local/bin/dusky_pro_active_zram_swap
APP_IDLE_RECLAIM_RATIO={conf.get("APP_IDLE_RECLAIM_RATIO", str(DEFAULT_RATIO))}
MAX_PER_RUN_MB={conf.get("MAX_PER_RUN_MB", str(DEFAULT_BUDGET_MB))}
CHUNK_SIZE_MB={conf.get("CHUNK_SIZE_MB", str(DEFAULT_CHUNK_MB))}
ZRAM_MAX_USAGE_RATIO={conf.get("ZRAM_MAX_USAGE_RATIO", str(DEFAULT_ZRAM_LIMIT))}
TIMER_INTERVAL={conf.get("TIMER_INTERVAL", DEFAULT_INTERVAL)}
"""
    write_file_atomic(CONF_FILE, content, mode=0o644)

# --- Timer Interval Queries & Mutations ---
def get_timer_interval() -> str:
    if TIMER_UNIT.exists():
        try:
            content = TIMER_UNIT.read_text(encoding="utf-8")
            match = re.search(r"^\s*OnUnitActiveSec\s*=\s*(\S+)", content, re.MULTILINE)
            if match:
                return match.group(1).strip()
        except Exception:
            pass
    conf = read_config()
    return conf.get("TIMER_INTERVAL", DEFAULT_INTERVAL)

def set_timer_interval(interval: str) -> None:
    escalate_root_if_needed()
    sync_binary_if_needed()
    interval = interval.strip().lower().replace(" ", "")
    # Standardize format (e.g., 3m -> 3min, 3 -> 3min)
    if interval.endswith("m"):
        interval += "in"
    elif interval.isdigit():
        interval += "min"
    
    if not re.match(r"^\d+(s|sec|min|h|hr)$", interval):
        die(f"Invalid timer interval format: '{interval}'. Example formats: '3min', '5min', '30s'.")

    # Update config file
    conf = read_config()
    conf["TIMER_INTERVAL"] = interval
    write_config(conf)

    # Update timer unit
    if TIMER_UNIT.exists():
        try:
            lines = TIMER_UNIT.read_text(encoding="utf-8").splitlines()
            new_lines = []
            found = False
            for line in lines:
                if re.match(r"^\s*OnUnitActiveSec\s*=", line):
                    new_lines.append(f"OnUnitActiveSec={interval}")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                for idx, line in enumerate(new_lines):
                    if line.strip() == "[Timer]":
                        new_lines.insert(idx + 1, f"OnUnitActiveSec={interval}")
                        found = True
                        break
            write_file_atomic(TIMER_UNIT, "\n".join(new_lines) + "\n", mode=0o644)
            subprocess.run(["systemctl", "daemon-reload"], check=False)
            subprocess.run(["systemctl", "restart", "dusky_pro_active_zram_swap.timer"], check=False)
            ok(f"Timer interval updated to {interval} and reloaded.")
            notify("Proactive Swap Interval", f"Timer sweep interval updated to {interval}")
        except Exception as e:
            die(f"Failed to update timer file: {e}")
    else:
        warn(f"Timer file {TIMER_UNIT} not found. Stored setting in {CONF_FILE}.")

# --- Ratio Queries & Mutations ---
def get_ratio_str() -> str:
    conf = read_config()
    try:
        val = float(conf.get("APP_IDLE_RECLAIM_RATIO", str(DEFAULT_RATIO)))
        return f"{int(round(val * 100))}%"
    except ValueError:
        return f"{int(DEFAULT_RATIO * 100)}%"

def set_ratio(val_str: str) -> None:
    escalate_root_if_needed()
    sync_binary_if_needed()
    val_clean = val_str.strip().rstrip("%")
    try:
        num = float(val_clean)
        # If passed as decimal e.g. 0.40 -> convert to 0.40; if passed as 40 -> 0.40
        if num > 1.0:
            ratio = num / 100.0
        else:
            ratio = num
        ratio = max(0.05, min(1.0, ratio))
    except ValueError:
        die(f"Invalid ratio value '{val_str}'. Please provide a percentage like '40%' or '0.40'.")

    conf = read_config()
    conf["APP_IDLE_RECLAIM_RATIO"] = f"{ratio:.2f}"
    write_config(conf)
    pct = int(round(ratio * 100))
    ok(f"Per-app idle reclaim ratio set to {pct}% ({ratio:.2f}).")
    notify("Proactive Swap Ratio", f"Per-app reclaim ratio set to {pct}%")

# --- Max Budget Queries & Mutations ---
def get_max_budget_str() -> str:
    conf = read_config()
    try:
        mb = int(conf.get("MAX_PER_RUN_MB", str(DEFAULT_BUDGET_MB)))
        if mb >= 1024 and mb % 1024 == 0:
            return f"{mb // 1024} GB"
        return f"{mb} MB"
    except ValueError:
        return f"{DEFAULT_BUDGET_MB} MB"

def set_max_budget(val_str: str) -> None:
    escalate_root_if_needed()
    sync_binary_if_needed()
    val_clean = val_str.strip().upper().replace(" ", "")
    mb = DEFAULT_BUDGET_MB
    try:
        if val_clean.endswith("GB") or val_clean.endswith("G"):
            num = re.sub(r"[A-Z]+$", "", val_clean)
            mb = int(float(num) * 1024)
        elif val_clean.endswith("MB") or val_clean.endswith("M"):
            num = re.sub(r"[A-Z]+$", "", val_clean)
            mb = int(float(num))
        elif val_clean.isdigit():
            mb = int(val_clean)
        else:
            die(f"Invalid budget expression '{val_str}'. Examples: '256 MB', '512 MB', '1 GB'.")
    except ValueError:
        die(f"Invalid budget value '{val_str}'.")

    mb = max(32, min(8192, mb))
    conf = read_config()
    conf["MAX_PER_RUN_MB"] = str(mb)
    write_config(conf)
    display = f"{mb // 1024} GB" if (mb >= 1024 and mb % 1024 == 0) else f"{mb} MB"
    ok(f"Max memory sweep budget set to {display}.")
    notify("Proactive Swap Budget", f"Sweep budget capped at {display}")

# --- Reclaim Chunk Size Queries & Mutations ---
def get_chunk_size_str() -> str:
    conf = read_config()
    try:
        mb = int(conf.get("CHUNK_SIZE_MB", str(DEFAULT_CHUNK_MB)))
        return f"{mb} MB"
    except ValueError:
        return f"{DEFAULT_CHUNK_MB} MB"

def set_chunk_size(val_str: str) -> None:
    escalate_root_if_needed()
    sync_binary_if_needed()
    val_clean = val_str.strip().strip("'\"").upper().replace(" ", "")
    mb = DEFAULT_CHUNK_MB
    try:
        if val_clean.endswith("MB") or val_clean.endswith("M"):
            num = re.sub(r"[A-Z]+$", "", val_clean)
            mb = int(float(num))
        elif val_clean.isdigit():
            mb = int(val_clean)
        else:
            die(f"Invalid chunk size '{val_str}'. Examples: '16 MB', '32 MB', '64 MB'.")
    except ValueError:
        die(f"Invalid chunk size value '{val_str}'.")

    mb = max(4, min(512, mb))
    conf = read_config()
    conf["CHUNK_SIZE_MB"] = str(mb)
    write_config(conf)
    ok(f"Reclaim chunk write size set to {mb} MB.")
    notify("Proactive Swap Chunk Size", f"Kernel reclaim write chunk set to {mb} MB")

# --- ZRAM Safety Limit ---
def get_zram_limit_str() -> str:
    conf = read_config()
    try:
        val = float(conf.get("ZRAM_MAX_USAGE_RATIO", str(DEFAULT_ZRAM_LIMIT)))
        return f"{int(round(val * 100))}%"
    except ValueError:
        return f"{int(DEFAULT_ZRAM_LIMIT * 100)}%"

def set_zram_limit(val_str: str) -> None:
    escalate_root_if_needed()
    sync_binary_if_needed()
    val_clean = val_str.strip().rstrip("%")
    try:
        num = float(val_clean)
        if num > 1.0:
            limit = num / 100.0
        else:
            limit = num
        limit = max(0.50, min(1.0, limit))
    except ValueError:
        die(f"Invalid ZRAM limit '{val_str}'. Example: '95%' or '0.95'.")

    conf = read_config()
    conf["ZRAM_MAX_USAGE_RATIO"] = f"{limit:.2f}"
    write_config(conf)
    pct = int(round(limit * 100))
    ok(f"ZRAM safety limit set to {pct}%.")
    notify("Proactive Swap Safety Limit", f"ZRAM abort limit set to {pct}%")

# --- Service & Timer Status / Control ---
def is_timer_active() -> bool:
    res = subprocess.run(
        ["systemctl", "is-active", "--quiet", "dusky_pro_active_zram_swap.timer"],
        check=False
    )
    return res.returncode == 0

def enable_timer() -> None:
    escalate_root_if_needed()
    sync_binary_if_needed()
    subprocess.run(["systemctl", "enable", "--now", "dusky_pro_active_zram_swap.timer"], check=True)
    ok("dusky_pro_active_zram_swap.timer enabled and started.")
    notify("Proactive Swap", "Automatic background reclaim timer enabled.")

def disable_timer() -> None:
    escalate_root_if_needed()
    subprocess.run(["systemctl", "disable", "--now", "dusky_pro_active_zram_swap.timer"], check=True)
    ok("dusky_pro_active_zram_swap.timer stopped and disabled.")
    notify("Proactive Swap", "Automatic background reclaim timer disabled.")

def run_now() -> None:
    escalate_root_if_needed()
    sync_binary_if_needed()
    info("Triggering proactive memory sweep now...")
    res = subprocess.run(["systemctl", "start", "dusky_pro_active_zram_swap.service"], check=False)
    if res.returncode == 0:
        ok("Sweep initiated successfully via systemd service.")
        notify("Proactive Swap", "Memory sweep completed.")
    else:
        # Fallback to direct execution
        if BIN_PATH.exists():
            subprocess.run([sys.executable, str(BIN_PATH), "--run"], check=False)
        else:
            die("Neither systemd service nor binary could be executed.")

def get_last_sweep_summary() -> str:
    try:
        res = subprocess.run(
            ["journalctl", "-u", "dusky_pro_active_zram_swap.service", "-g", "Sweep finished", "-n", "1", "--no-pager", "-o", "cat"],
            capture_output=True,
            text=True,
            check=False
        )
        out = res.stdout.strip()
        if out:
            clean = re.sub(r"\x1b\[[0-9;]*[mGKF]", "", out)
            match = re.search(r"Stolen:\s*([0-9.]+)\s*MB", clean)
            dur_match = re.search(r"in\s*([0-9.]+)\s*ms", clean)
            if match:
                stolen_mb = float(match.group(1))
                stolen_str = f"{int(round(stolen_mb))} MB" if stolen_mb >= 10 else f"{stolen_mb:.1f} MB"
                if dur_match:
                    ms = float(dur_match.group(1))
                    dur_str = f"{ms/1000:.1f}s" if ms >= 1000 else f"{int(round(ms))}ms"
                    return f"{stolen_str} ({dur_str})"
                return stolen_str
    except Exception:
        pass
    return "None yet"

def get_zram_overview() -> str:
    try:
        res = subprocess.run(
            ["zramctl", "--output", "NAME,DATA,COMPR,TOTAL", "--noheadings"],
            capture_output=True,
            text=True,
            check=False
        )
        if res.returncode == 0 and res.stdout.strip():
            parts = res.stdout.strip().split()
            if len(parts) >= 4:
                return f"zRAM: {parts[2]}/{parts[3]}"
    except Exception:
        pass
    return "zRAM: Active"

def get_compact_status() -> str:
    active = is_timer_active()
    interval = get_timer_interval()
    if active:
        return f"Active ({interval})"
    return "Disabled"

def print_full_status() -> None:
    active = is_timer_active()
    interval = get_timer_interval()
    ratio = get_ratio_str()
    budget = get_max_budget_str()
    chunk = get_chunk_size_str()
    zram_limit = get_zram_limit_str()
    last = get_last_sweep_summary()
    
    print(f"\n{C.BOLD}═══════════════════════════════════════════════════════════════════{C.RST}")
    print(f"{C.BOLD}        DUSKY PROACTIVE ZRAM SWAP SUBSYSTEM (MGLRU ENGINE)          {C.RST}")
    print(f"{C.BOLD}═══════════════════════════════════════════════════════════════════{C.RST}\n")
    
    status_color = C.GRN if active else C.RED
    status_word = "ACTIVE (Running)" if active else "DISABLED (Stopped)"
    print(f"  {C.BOLD}Timer Subsystem:{C.RST}   {status_color}{status_word}{C.RST}")
    print(f"  {C.BOLD}Sweep Frequency:{C.RST}   {C.CYN}{interval}{C.RST}")
    print(f"  {C.BOLD}App Skim Limit:{C.RST}    {C.CYN}{ratio}{C.RST} anon memory per idle app")
    print(f"  {C.BOLD}Run Budget Cap:{C.RST}    {C.CYN}{budget}{C.RST} max per sweep")
    print(f"  {C.BOLD}Burst Chunk Size:{C.RST}  {C.CYN}{chunk}{C.RST} per kernel reclaim yield")
    print(f"  {C.BOLD}ZRAM Safety Cap:{C.RST}   {C.CYN}{zram_limit}{C.RST} (aborts sweep if exceeded)")
    print(f"  {C.BOLD}Last Execution:{C.RST}    {last}\n")

    # Timer schedule details
    try:
        res = subprocess.run(
            ["systemctl", "list-timers", "dusky_pro_active_zram_swap.timer", "--no-pager"],
            capture_output=True,
            text=True,
            check=False
        )
        if res.returncode == 0 and res.stdout.strip():
            print(f"  {C.BOLD}Systemd Timer Schedule:{C.RST}")
            for line in res.stdout.strip().splitlines()[:4]:
                print(f"    {line}")
            print()
    except Exception:
        pass

    # Swap devices
    try:
        res = subprocess.run(["swapon", "--show"], capture_output=True, text=True, check=False)
        if res.returncode == 0 and res.stdout.strip():
            print(f"  {C.BOLD}Active Swap Topology:{C.RST}")
            for line in res.stdout.strip().splitlines():
                print(f"    {line}")
            print()
    except Exception:
        pass

    # ZRAM Compression Stats
    try:
        res = subprocess.run(["zramctl"], capture_output=True, text=True, check=False)
        if res.returncode == 0 and res.stdout.strip():
            print(f"  {C.BOLD}ZRAM Hardware Compression:{C.RST}")
            for line in res.stdout.strip().splitlines():
                print(f"    {line}")
            print()
    except Exception:
        pass
    print(f"{C.BOLD}═══════════════════════════════════════════════════════════════════{C.RST}\n")

# =============================================================================
# Main CLI Entrypoint
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Dusky Proactive ZRAM Swap Modifier & CC Backend")

    # Status & Inspection Queries (Unprivileged)
    parser.add_argument("--status", action="store_true", help="Print compact one-line status")
    parser.add_argument("--status-full", action="store_true", help="Print comprehensive diagnostic report")
    parser.add_argument("--is-active", action="store_true", help="Output 'on' or 'off' for GUI toggle switch")
    parser.add_argument("--get-ratio", action="store_true", help="Print per-app idle reclaim ratio percentage")
    parser.add_argument("--get-max-budget", action="store_true", help="Print max memory sweep budget")
    parser.add_argument("--get-chunk-size", action="store_true", help="Print kernel memory reclaim write chunk size")
    parser.add_argument("--get-interval", action="store_true", help="Print periodic sweep timer interval")
    parser.add_argument("--get-zram-limit", action="store_true", help="Print ZRAM abort limit percentage")
    parser.add_argument("--last-sweep", action="store_true", help="Print last sweep summary")

    # Mutation Handlers (Root Escalated)
    parser.add_argument("--enable", action="store_true", help="Enable and start proactive swap timer")
    parser.add_argument("--disable", action="store_true", help="Disable and stop proactive swap timer")
    parser.add_argument("--run-now", action="store_true", help="Trigger an immediate memory sweep")
    parser.add_argument("--set-ratio", nargs="+", metavar="PCT", help="Set per-app idle anon memory ratio (e.g. '40%%', '25%%')")
    parser.add_argument("--set-max-budget", nargs="+", metavar="SIZE", help="Set max sweep budget ceiling (e.g. '256 MB', '512 MB', '1 GB')")
    parser.add_argument("--set-chunk-size", nargs="+", metavar="SIZE", help="Set kernel memory reclaim write chunk size (e.g. '16 MB', '32 MB', '64 MB')")
    parser.add_argument("--set-interval", nargs="+", metavar="INTERVAL", help="Set periodic timer interval (e.g. '3min', '5min')")
    parser.add_argument("--set-zram-limit", nargs="+", metavar="LIMIT", help="Set ZRAM abort fullness limit (e.g. '95%%')")

    args = parser.parse_args()

    # Query Handlers
    if args.is_active:
        print("on" if is_timer_active() else "off")
        return
    if args.status:
        print(get_compact_status())
        return
    if args.status_full:
        print_full_status()
        return
    if args.get_ratio:
        print(get_ratio_str())
        return
    if args.get_max_budget:
        print(get_max_budget_str())
        return
    if args.get_chunk_size:
        print(get_chunk_size_str())
        return
    if args.get_interval:
        print(get_timer_interval())
        return
    if args.get_zram_limit:
        print(get_zram_limit_str())
        return
    if args.last_sweep:
        print(get_last_sweep_summary())
        return

    # Mutation Handlers
    if args.enable:
        enable_timer()
        return
    if args.disable:
        disable_timer()
        return
    if args.run_now:
        run_now()
        return
    if args.set_ratio:
        set_ratio(" ".join(args.set_ratio) if isinstance(args.set_ratio, list) else str(args.set_ratio))
        return
    if args.set_max_budget:
        set_max_budget(" ".join(args.set_max_budget) if isinstance(args.set_max_budget, list) else str(args.set_max_budget))
        return
    if args.set_chunk_size:
        set_chunk_size(" ".join(args.set_chunk_size) if isinstance(args.set_chunk_size, list) else str(args.set_chunk_size))
        return
    if args.set_interval:
        set_timer_interval(" ".join(args.set_interval) if isinstance(args.set_interval, list) else str(args.set_interval))
        return
    if args.set_zram_limit:
        set_zram_limit(" ".join(args.set_zram_limit) if isinstance(args.set_zram_limit, list) else str(args.set_zram_limit))
        return

    # Default fallback
    print_full_status()

if __name__ == "__main__":
    main()
