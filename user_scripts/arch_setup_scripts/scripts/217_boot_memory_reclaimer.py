#!/usr/bin/env python3
#d: Reclaim boot-time memory to ZRAM/swap

from __future__ import annotations

import argparse
import errno
import os
import shutil
import sys
import subprocess
import tempfile
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

# --- Dynamic Page Size Resolution ---
PAGE_SIZE = os.sysconf("SC_PAGESIZE") if hasattr(os, "sysconf") else 4096

# --- Argument Parsing (Executed BEFORE Privilege Escalation) ---
parser = argparse.ArgumentParser(description="Elite Arch Linux Boot-Time Memory Reclaimer")
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

def parse_anon_bytes(stat_path: Path) -> int | None:
    try:
        with stat_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("anon "):
                    try:
                        return int(line.split()[1])
                    except (IndexError, ValueError):
                        return None
    except OSError:
        return None
    return None

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

def perform_reclaim() -> None:
    info("Initiating targeted boot-time cold memory sweep...")

    if not is_cgroup2_mounted():
        die("cgroup v2 not mounted at /sys/fs/cgroup. Arch uses cgroup2 by default.")

    if not has_swap_or_zram():
        warn("No active swap or ZRAM detected. swappiness=max requires swap; kernel will return EAGAIN.")

    slices = ["user.slice", "system.slice"]
    reclaimed_total_bytes = 0

    for slice_name in slices:
        cgroup_dir = Path("/sys/fs/cgroup") / slice_name
        current_file = cgroup_dir / "memory.current"
        reclaim_file = cgroup_dir / "memory.reclaim"
        stat_file = cgroup_dir / "memory.stat"

        if not current_file.exists() or not reclaim_file.exists() or not stat_file.exists():
            info(f"Cgroup slice {slice_name} does not support memory reclaim. Skipping.")
            continue

        try:
            anon_bytes = parse_anon_bytes(stat_file)
            if anon_bytes is None:
                info(f"Could not parse anonymous memory stats for {slice_name}. Skipping.")
                continue

            before_reclaimed = parse_proactive_reclaimed_bytes(stat_file)

            target_reclaim = anon_bytes
            if target_reclaim < 1024 * 1024:
                if anon_bytes >= 1024 * 1024:
                    target_reclaim = anon_bytes
                else:
                    info(f"No cold anonymous pages (anon={anon_bytes} B) in {slice_name}.")
                    continue

            reclaim_command = f"{target_reclaim} swappiness=max"

            try:
                reclaim_file.write_text(reclaim_command, encoding="utf-8")
            except OSError as e:
                if e.errno == errno.EAGAIN:
                    info(f"Kernel processed reclaim for {slice_name} (partial reclaim - EAGAIN returned).")
                elif e.errno == errno.EINVAL:
                    try:
                        reclaim_file.write_text(str(target_reclaim), encoding="utf-8")
                    except OSError as e2:
                        if e2.errno == errno.EAGAIN:
                            info(f"Kernel processed reclaim for {slice_name} (partial reclaim - EAGAIN returned).")
                        else:
                            err(f"Invalid reclaim command for {slice_name}: {target_reclaim} ({e2})")
                            continue
                elif e.errno == errno.ENOENT:
                    info(f"Reclaim interface missing for {slice_name} (controller unmounted). Skipping.")
                    continue
                else:
                    raise

            after_reclaimed = parse_proactive_reclaimed_bytes(stat_file)
            actual_reclaimed = after_reclaimed - before_reclaimed

            if actual_reclaimed > 0:
                reclaimed_total_bytes += actual_reclaimed
                ok(f"Reclaimed {actual_reclaimed / (1024*1024):.1f} MB of cold memory from {slice_name} (anon={anon_bytes/(1024*1024):.1f} MB, requested={target_reclaim/(1024*1024):.1f} MB)")
            else:
                info(f"Kernel processed reclaim for {slice_name} (requested {target_reclaim/(1024*1024):.1f} MB). pgsteal_proactive delta {actual_reclaimed} B.")

        except Exception as e:
            info(f"Failed to reclaim memory from {slice_name}: {e}")

    ok(f"Targeted cold memory sweep completed. Reclaimed ~{reclaimed_total_bytes / (1024*1024):.1f} MB of cold pages to swap/ZRAM.")

def deploy_systemd_units() -> None:
    info("Deploying boot-time memory reclaim systemd units...")

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
Description=Boot-time Cold Memory Reclaimer (Kernel 7.1+ / systemd 261.1)
Documentation=https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html
After=multi-user.target local-fs.target
ConditionPathExists=/sys/fs/cgroup
ConditionPathExists=/sys/fs/cgroup/system.slice

[Service]
Type=oneshot
ExecStart={python_bin} {install_path} --run
RemainAfterExit=no
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
Description=Trigger Boot-time Cold Memory Reclaimer 45 seconds after boot
Documentation=https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html

[Timer]
OnBootSec=45s
AccuracySec=1s
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

    ok("Boot-time reclaimer timer is active. Cold memory will be purged 45 seconds after boot (AccuracySec=1s).")
    info("Verify with: systemctl status dusky_boot_mem_reclaim.timer && systemctl status dusky_boot_mem_reclaim.service && journalctl -u dusky_boot_mem_reclaim.service")

def main() -> None:
    if sys.version_info < (3, 14):
        die(f"Python 3.14+ required, running {sys.version.split()[0]}")

    escalate_privileges()

    if args.restore:
        # Stop and disable systemd units
        info("Stopping and disabling systemd timer and service...")
        for unit in ("dusky_boot_mem_reclaim.timer", "dusky_boot_mem_reclaim.service"):
            try:
                subprocess.run(["systemctl", "disable", "--now", unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
        
        # Remove files
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
