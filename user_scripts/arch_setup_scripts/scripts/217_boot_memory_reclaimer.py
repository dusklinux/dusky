#!/usr/bin/env python3
#d: Reclaim boot-time & periodic idle memory to ZRAM (MGLRU Engine)

from __future__ import annotations

import argparse
import errno
import os
import shutil
import sys
import subprocess
import tempfile
import time
from pathlib import Path
from typing import NoReturn

# --- Presentation (Zero-Dependency ANSI) ---
class C:
    BOLD = "\033[1m"
    RED = "\033[1;31m"
    GRN = "\033[1;32m"
    YLW = "\033[1;33m"
    BLU = "\033[1;34m"
    RST = "\033[0m"

    @classmethod
    def strip(cls) -> None:
        for name in ("BOLD", "RED", "GRN", "YLW", "BLU", "RST"):
            setattr(cls, name, "")

def info(msg: str) -> None: print(f"{C.BLU}[INFO]{C.RST} {msg}")
def ok(msg: str) -> None: print(f"{C.GRN}[ OK ]{C.RST} {msg}")
def warn(msg: str) -> None: print(f"{C.YLW}[WARN]{C.RST} {msg}")
def err(msg: str) -> None: print(f"{C.RED}[FAIL]{C.RST} {msg}", file=sys.stderr)
def die(msg: str, code: int = 1) -> NoReturn:
    err(msg)
    sys.exit(code)

# --- Configuration & Tuning ---
PAGE_SIZE: int = os.sysconf("SC_PAGESIZE") if hasattr(os, "sysconf") else 4096
CHUNK_SIZE: int = 32 * 1024 * 1024       # 32 MiB write chunks for ultra-low latency
PSI_SOME_THRESHOLD: float = 0.50         # Abort if some avg10 >= 0.50%

def get_total_ram_bytes() -> int:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    return int(parts[1]) * 1024
    except Exception:
        pass
    return 16 * 1024 * 1024 * 1024

TOTAL_RAM: int = get_total_ram_bytes()
# Dynamic run budget: capped at 1 GiB per sweep, or 10% of total system RAM on small systems
MAX_PER_RUN: int = min(1024 * 1024 * 1024, max(256 * 1024 * 1024, int(TOTAL_RAM * 0.10)))

# --- Argument Parsing (Executed BEFORE Privilege Escalation) ---
parser = argparse.ArgumentParser(description="Elite Arch Linux MGLRU Boot & Periodic Memory Skimmer")
group = parser.add_mutually_exclusive_group()
group.add_argument("--run", action="store_true", help="Directly trigger the memory reclaim task")
group.add_argument("--restore", action="store_true", help="Remove reclaimer binaries, systemd units and timer")
parser.add_argument("--no-color", action="store_true", help="Disable ANSI color output")

args = parser.parse_args()

if args.no_color or not sys.stdout.isatty() or "NO_COLOR" in os.environ:
    C.strip()

# --- Privilege Escalation ---
def escalate_privileges() -> None:
    if os.geteuid() == 0:
        return
    info("Root privileges required. Escalating...")
    if shutil.which("sudo") is None:
        die("sudo is required to run this script as root (not found in PATH).")
    script_path = str(Path(__file__).resolve())
    os.execvp("sudo", ["sudo", sys.executable, script_path] + sys.argv[1:])

def write_file_atomic(path: Path, content: str, mode: int = 0o644) -> None:
    path = Path(path)
    try:
        if path.exists() and path.read_text(encoding="utf-8") == content:
            try:
                if (path.stat().st_mode & 0o777) != mode:
                    os.chmod(path, mode)
            except FileNotFoundError:
                pass
            return
    except OSError:
        pass

    path.parent.mkdir(parents=True, exist_ok=True)
    dir_name = str(path.parent)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=f".{path.name}.", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
        try:
            dfd = os.open(dir_name, os.O_DIRECTORY | os.O_RDONLY if hasattr(os, "O_DIRECTORY") else os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise

def has_swap_or_zram() -> bool:
    try:
        swaps = Path("/proc/swaps").read_text(encoding="utf-8")
        lines = swaps.strip().splitlines()
        if len(lines) > 1:
            return True
    except OSError:
        pass
    try:
        for zram in Path("/sys/block").glob("zram*"):
            disksize = (zram / "disksize").read_text(encoding="utf-8").strip() if (zram / "disksize").exists() else "0"
            if int(disksize) > 0:
                return True
    except OSError:
        pass
    if Path("/dev/zram0").exists():
        return True
    return False

def is_cgroup2_mounted() -> bool:
    try:
        with open("/proc/mounts", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "/sys/fs/cgroup" and "cgroup2" in parts[2]:
                    return True
    except OSError:
        pass
    return Path("/sys/fs/cgroup/cgroup.controllers").exists()

def get_system_pressure() -> float:
    try:
        with open("/proc/pressure/memory", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("some "):
                    # some avg10=0.00 avg60=0.00 avg300=0.00 total=...
                    parts = line.split()
                    for p in parts:
                        if p.startswith("avg10="):
                            return float(p.split("=")[1])
    except Exception:
        pass
    return 0.0

def get_mem_stats(stat_path: Path) -> dict[str, int]:
    stats: dict[str, int] = {}
    if not stat_path.exists():
        return stats
    try:
        with stat_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) == 2:
                    try:
                        stats[parts[0]] = int(parts[1])
                    except ValueError:
                        pass
    except OSError:
        pass
    return stats

def get_cpu_usage_usec(cfile: Path) -> int | None:
    if not cfile.exists():
        return None
    try:
        with cfile.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("usage_usec "):
                    return int(line.split()[1])
    except Exception:
        pass
    return None

def find_app_slices() -> list[Path]:
    user_slice = Path("/sys/fs/cgroup/user.slice")
    targets: list[Path] = []
    if not user_slice.exists():
        return targets
    for user_sub in user_slice.glob("user-*.slice"):
        name = user_sub.name
        if name.startswith("user-") and name.endswith(".slice"):
            uid_str = name[5:-6]
            app_slice = user_sub / f"user@{uid_str}.service" / "app.slice"
            if app_slice.exists():
                targets.append(app_slice)
    return targets

def parse_proactive_reclaimed_bytes(stat_path: Path) -> int:
    try:
        with stat_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("pgsteal_proactive "):
                    try:
                        return int(line.split()[1]) * PAGE_SIZE
                    except (IndexError, ValueError):
                        return 0
    except OSError:
        return 0
    return 0

def reclaim_cgroup_chunked(cgroup_dir: Path, target_bytes: int, label: str) -> tuple[int, int]:
    reclaim_file = cgroup_dir / "memory.reclaim"
    stat_file = cgroup_dir / "memory.stat"
    if not reclaim_file.exists():
        return 0, 0

    before_steal = parse_proactive_reclaimed_bytes(stat_file) if stat_file.exists() else 0
    reclaimed_requested = 0

    while reclaimed_requested < target_bytes:
        # PSI pre-chunk check
        psi_sys = get_system_pressure()
        if psi_sys >= PSI_SOME_THRESHOLD:
            warn(f"System memory pressure elevated ({psi_sys:.2f}% >= {PSI_SOME_THRESHOLD}%). Halting sweep.")
            break

        chunk = min(CHUNK_SIZE, target_bytes - reclaimed_requested)
        try:
            with reclaim_file.open("w", encoding="utf-8") as fh:
                fh.write(f"{chunk} swappiness=max\n")
            reclaimed_requested += chunk
            time.sleep(0.005)  # Yield CPU briefly for foreground tasks
        except OSError as e:
            if e.errno == errno.EAGAIN:
                # Kernel processed all colder pages available
                reclaimed_requested += chunk
                break
            elif e.errno == errno.EINVAL:
                warn(f"swappiness=max unsupported or invalid parameter on {label}")
                break
            elif e.errno == errno.ENOENT:
                break
            else:
                warn(f"Reclaim error on {label}: {e}")
                break

    after_steal = parse_proactive_reclaimed_bytes(stat_file) if stat_file.exists() else 0
    actual_stolen = max(0, after_steal - before_steal)
    return reclaimed_requested, actual_stolen

def perform_reclaim() -> None:
    info("Initiating MGLRU proactive idle memory sweep...")

    if not is_cgroup2_mounted():
        die("cgroup v2 not mounted at /sys/fs/cgroup. Arch uses cgroup2 by default.")

    if not has_swap_or_zram():
        warn("No active swap or ZRAM detected. Kernel will reject anon reclaim.")

    # 1. Gate on system memory pressure
    psi_sys = get_system_pressure()
    if psi_sys >= PSI_SOME_THRESHOLD:
        info(f"System memory pressure active (some avg10={psi_sys:.2f}% >= {PSI_SOME_THRESHOLD}%). Skipping sweep.")
        return

    start_time = time.perf_counter()
    total_requested = 0
    total_stolen = 0

    # 2. Target user application scopes (app.slice)
    app_slices = find_app_slices()
    for app_slice in app_slices:
        if total_requested >= MAX_PER_RUN:
            break

        leaf_cgroups = [
            p for p in app_slice.iterdir()
            if p.is_dir() and (p.name.startswith("app-") or p.name.endswith(".scope") or p.name.endswith(".service"))
        ]

        # First pass: check leaf cgroups for genuinely idle apps
        for leaf in leaf_cgroups:
            if total_requested >= MAX_PER_RUN:
                break
            stats = get_mem_stats(leaf / "memory.stat")
            anon = stats.get("anon", 0)
            if anon < 16 * 1024 * 1024:
                continue

            # Detect CPU idleness over 50ms window
            cpu_f = leaf / "cpu.stat"
            u1 = get_cpu_usage_usec(cpu_f)
            time.sleep(0.05)
            u2 = get_cpu_usage_usec(cpu_f)
            delta_cpu = (u2 - u1) if (u1 is not None and u2 is not None) else 0

            # If CPU advanced less than 5ms over 50ms window, the app is idle
            if delta_cpu < 5000:
                target = min(int(anon * 0.40), MAX_PER_RUN - total_requested)
                if target > 0:
                    req, stl = reclaim_cgroup_chunked(leaf, target, leaf.name)
                    total_requested += req
                    total_stolen += stl
                    if stl > 0:
                        ok(f"Reclaimed {stl / (1024*1024):.1f} MB from idle app {leaf.name} (anon={anon/(1024*1024):.1f} MB)")

        # Second pass: reclaim remaining budget from app.slice general cold pool
        if total_requested < MAX_PER_RUN:
            remaining = MAX_PER_RUN - total_requested
            req, stl = reclaim_cgroup_chunked(app_slice, remaining, "app.slice")
            total_requested += req
            total_stolen += stl
            if stl > 0:
                ok(f"Reclaimed {stl / (1024*1024):.1f} MB from app.slice pool")

    # 3. Target system services cold pool (system.slice)
    if total_requested < MAX_PER_RUN:
        system_slice = Path("/sys/fs/cgroup/system.slice")
        if system_slice.exists():
            remaining = min(128 * 1024 * 1024, MAX_PER_RUN - total_requested)
            req, stl = reclaim_cgroup_chunked(system_slice, remaining, "system.slice")
            total_requested += req
            total_stolen += stl
            if stl > 0:
                ok(f"Reclaimed {stl / (1024*1024):.1f} MB from system.slice cold pool")

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    zram_info = ""
    try:
        res = subprocess.run(["zramctl", "--output", "NAME,DATA,COMPR,TOTAL", "--noheadings"],
                             capture_output=True, text=True, check=False)
        if res.returncode == 0 and res.stdout.strip():
            zram_info = f" | zRAM: {res.stdout.strip()}"
    except Exception:
        pass

    ok(f"Sweep finished in {elapsed_ms:.1f}ms. Stolen: {total_stolen / (1024*1024):.1f} MB to ZRAM{zram_info}")

def deploy_systemd_units() -> None:
    info("Deploying MGLRU boot & periodic idle memory reclaim units...")

    install_path = Path("/usr/local/bin/dusky_boot_mem_reclaim")
    current_script = Path(__file__).resolve()

    if current_script != install_path:
        install_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(current_script, install_path)
            os.chmod(install_path, 0o755)
            ok(f"Binary securely installed to {install_path}")
        except OSError as e:
            die(f"Failed to install to {install_path}: {e}")

    service_path = Path("/etc/systemd/system/dusky_boot_mem_reclaim.service")
    python_bin = "/usr/bin/python3"
    if not Path(python_bin).exists():
        python_bin = sys.executable

    service_content = f"""[Unit]
Description=MGLRU Cold Memory Reclaimer & Idle Skimmer (Kernel 7.2+ / systemd 261+)
Documentation=https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html
After=multi-user.target local-fs.target
ConditionPathExists=/sys/fs/cgroup
ConditionPathExists=/sys/fs/cgroup/system.slice

[Service]
Type=oneshot
TimeoutStartSec=30s
ExecStart={python_bin} {install_path} --run
RemainAfterExit=no
Nice=19
CPUSchedulingPolicy=idle
IOSchedulingClass=idle
CPUWeight=1
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=no
ProtectControlGroups=no
ReadWritePaths=/sys/fs/cgroup
LockPersonality=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
MemoryDenyWriteExecute=no
"""
    write_file_atomic(service_path, service_content, mode=0o644)
    ok(f"Service unit written to {service_path}")

    timer_path = Path("/etc/systemd/system/dusky_boot_mem_reclaim.timer")
    timer_content = """[Unit]
Description=Trigger MGLRU Cold Memory Reclaimer at 45s Boot & 5min Periodic
Documentation=https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html

[Timer]
OnBootSec=45s
OnUnitActiveSec=5min
AccuracySec=5s
RandomizedDelaySec=15s
Persistent=false
Unit=dusky_boot_mem_reclaim.service

[Install]
WantedBy=timers.target
"""
    write_file_atomic(timer_path, timer_content, mode=0o644)
    ok(f"Timer unit written to {timer_path}")

    info("Reloading systemd daemon...")
    try:
        subprocess.run(["systemctl", "daemon-reload"], check=True)
    except subprocess.CalledProcessError as e:
        die(f"systemctl daemon-reload failed: {e}")

    info("Enabling and starting dusky_boot_mem_reclaim.timer...")
    try:
        subprocess.run(["systemctl", "enable", "--now", "dusky_boot_mem_reclaim.timer"], check=True)
    except subprocess.CalledProcessError as e:
        die(f"Failed to enable timer: {e}")

    ok("MGLRU skimmer timer active: initial run at 45s after boot, recurring every 5min thereafter.")
    info("Verify with: systemctl status dusky_boot_mem_reclaim.timer && systemctl status dusky_boot_mem_reclaim.service && journalctl -u dusky_boot_mem_reclaim.service")

def main() -> None:
    if sys.version_info < (3, 14):
        die(f"Python 3.14+ required, running {sys.version.split()[0]}")

    escalate_privileges()

    if args.restore:
        info("Stopping and disabling systemd timer and service...")
        for unit in ("dusky_boot_mem_reclaim.timer", "dusky_boot_mem_reclaim.service"):
            try:
                subprocess.run(["systemctl", "disable", "--now", unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

        files_to_remove = [
            Path("/usr/local/bin/dusky_boot_mem_reclaim"),
            Path("/etc/systemd/system/dusky_boot_mem_reclaim.service"),
            Path("/etc/systemd/system/dusky_boot_mem_reclaim.timer"),
        ]
        for f in files_to_remove:
            if f.exists():
                try:
                    f.unlink()
                    ok(f"Removed {f}")
                except Exception as e:
                    warn(f"Failed to remove {f}: {e}")

        info("Reloading systemd daemon...")
        try:
            subprocess.run(["systemctl", "daemon-reload"], check=True)
        except Exception as e:
            warn(f"systemctl daemon-reload failed: {e}")

        ok("Restoration complete. Memory reclaimer uninstalled.")
        return

    if args.run:
        perform_reclaim()
    else:
        deploy_systemd_units()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.BOLD}{C.RED}aborted — operation cancelled by user.{C.RST}")
        sys.exit(130)
