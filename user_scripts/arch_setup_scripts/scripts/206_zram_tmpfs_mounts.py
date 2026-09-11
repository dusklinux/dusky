#!/usr/bin/env python3
"""
Elite Arch Linux Hybrid Memory Mount Configurator (Kernel 7.2+, systemd 261+)
Supports:
  1) Native tmpfs (Pure RAM mapping - Recommended: zero double-buffering, lowest idle RAM)
  2) Ext2 on compressed ZRAM block device (/dev/zram1 on /mnt/zram1)
     - Native zero-journal filesystem (clean upstream default, lower metadata)
     - 0% root reserved blocks (-m 0)
     - Mode 1777 natively enforced via systemd X-mount.mode=1777
  3) Disable / clean up secondary RAM mounts
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

class C:
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[1;31m"
    GRN = "\033[1;32m"
    YLW = "\033[1;33m"
    BLU = "\033[1;34m"
    CYN = "\033[1;36m"
    RST = "\033[0m"

    @classmethod
    def strip(cls) -> None:
        for name in ("BOLD", "DIM", "RED", "GRN", "YLW", "BLU", "CYN", "RST"):
            setattr(cls, name, "")

def info(msg: str) -> None: print(f"{C.BLU}[INFO]{C.RST} {msg}")
def ok(msg: str) -> None: print(f"{C.GRN}[ OK ]{C.RST} {msg}")
def warn(msg: str) -> None: print(f"{C.YLW}[WARN]{C.RST} {msg}")
def err(msg: str) -> None: print(f"{C.RED}[FAIL]{C.RST} {msg}", file=sys.stderr)
def die(msg: str, code: int = 1) -> NoReturn:
    err(msg)
    sys.exit(code)

parser = argparse.ArgumentParser(description="Elite Arch Linux Autonomous Tmpfs Mount Configurator (/mnt/zram1)")
parser.add_argument("--disable", "--none", dest="disable", action="store_true", help="Disable RAM disk and clean up /mnt/zram1")
parser.add_argument("--size", "-s", type=str, default="", help="Size expression ceiling (e.g. '100%%', '50%%', '8G', 'ram')")
parser.add_argument("--no-color", action="store_true", help="Disable ANSI color output")

args = parser.parse_args()

if args.no_color or not sys.stdout.isatty() or "NO_COLOR" in os.environ:
    C.strip()

def escalate_privileges() -> None:
    if os.geteuid() != 0:
        info("Root privileges required. Escalating...")
        if shutil.which("sudo"):
            os.execvp("sudo", ["sudo", sys.executable, os.path.abspath(__file__)] + sys.argv[1:])
        elif shutil.which("pkexec"):
            os.execvp("pkexec", ["pkexec", sys.executable, os.path.abspath(__file__)] + sys.argv[1:])
        else:
            die("sudo or pkexec is required to run this script as root.")

escalate_privileges()

MOUNT_POINT = Path("/mnt/zram1")
BASE_MOUNT = Path("/mnt")
ZRAM_CONF_FILE = Path("/etc/systemd/zram-generator.conf.d/99-zram1.conf")
LEGACY_ZRAM_CONF_FILE = Path("/etc/systemd/zram-generator.conf.d/99-elite-zram1.conf")
TMPFILES_CONF = Path("/etc/tmpfiles.d/zram-mounts.conf")
TMPFS_MOUNT_UNIT_PATH = Path("/etc/systemd/system/mnt-zram1.mount")
SETUP_OVERRIDE_DIR = Path("/etc/systemd/system/systemd-zram-setup@zram1.service.d")
SETUP_OVERRIDE_CONF = SETUP_OVERRIDE_DIR / "override.conf"
LEGACY_MAKEFS_OVERRIDE_DIR = Path("/etc/systemd/system/systemd-makefs@dev-zram1.service.d")
PERMS_SERVICE_PATH = Path("/etc/systemd/system/mnt-zram1-permissions.service")
PERMS_WANTS_DIR = Path("/etc/systemd/system/mnt-zram1.mount.wants")
PERMS_WANTS_SYMLINK = PERMS_WANTS_DIR / "mnt-zram1-permissions.service"

COMPRESSION_ALGORITHM = "zstd(level=2)"
FS_OPTIONS = "rw,nosuid,nodev,discard,noatime,lazytime,X-mount.mode=1777"
CMD_TIMEOUT = 15

def run_cmd(cmd: list[str], ignore_errors: bool = False) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=not ignore_errors, timeout=CMD_TIMEOUT)
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        die(f"Command timed out after {CMD_TIMEOUT}s: {' '.join(cmd)}")
    except subprocess.CalledProcessError as e:
        if not ignore_errors:
            err(f"Command failed: {' '.join(cmd)}\n{e.stderr.strip()}")
            sys.exit(1)
        return ""

def write_file_atomic(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

def parse_tmpfs_size_expression(raw: str, default: str = "200%") -> str:
    s = raw.strip().lower().replace("x", "")
    if not s or s in ("auto", "default"):
        return default
    if s.endswith("%") or s.endswith("g") or s.endswith("m") or s.endswith("k"):
        return s
    if s == "ram":
        return "100%"
    elif s in ("ram * 2", "ram*2", "2"):
        return "200%"
    elif s in ("ram / 2", "ram/2", "0.5"):
        return "50%"
    elif s in ("ram / 4", "ram/4", "0.25"):
        return "25%"
    elif s.startswith("ram * ") or s.startswith("ram*"):
        try:
            val = float(s.replace("ram * ", "").replace("ram*", "").strip())
            return f"{int(round(val * 100))}%"
        except ValueError:
            pass
    elif s.startswith("ram / ") or s.startswith("ram/"):
        try:
            val = float(s.replace("ram / ", "").replace("ram/", "").strip())
            if val > 0:
                return f"{int(round((1.0 / val) * 100))}%"
        except ValueError:
            pass
    try:
        f = float(s)
        if 0.0 < f <= 1.0:
            return f"{int(round(f * 100))}%"
        elif 1.0 < f <= 10.0:
            return f"{int(round(f * 100))}%"
        elif 10.0 < f <= 1000.0 and f.is_integer():
            return f"{int(f)}%"
    except ValueError:
        pass
    return default

def parse_size_expression(raw: str, default: str = "ram / 2") -> str:
    s = raw.strip().lower()
    if not s or s in ("auto", "default"):
        return default
    if s.endswith("%"):
        try:
            pct = float(s[:-1])
            if pct == 100.0: return "ram"
            elif pct == 50.0: return "ram / 2"
            elif pct == 25.0: return "ram / 4"
            else: return f"ram * {pct / 100.0:.2f}".rstrip("0").rstrip(".")
        except ValueError:
            pass
    try:
        n = float(s)
        if 0.0 < n <= 2.0:
            return f"ram * {n:.2f}".rstrip("0").rstrip(".")
        elif 3.0 <= n <= 100.0 and n.is_integer():
            pct = n / 100.0
            return "ram" if pct == 1.0 else f"ram * {pct:.2f}".rstrip("0").rstrip(".")
    except ValueError:
        pass
    m = re.match(r"^([0-9.]+)\s*([gmk]b?)$", s)
    if m:
        val = float(m.group(1))
        unit = m.group(2)
        if unit.startswith("g"):
            return str(int(val * 1024))
        elif unit.startswith("m"):
            return str(int(val))
        elif unit.startswith("k"):
            return str(int(val / 1024))
    return re.sub(r"\s*([*/+-])\s*", r" \1 ", s)

def pre_flight_checks() -> None:
    if subprocess.run(["systemd-detect-virt", "--quiet", "--container"], capture_output=True).returncode == 0:
        die("Container detected — refusing to tune memory mounts inside a container.")
    
    cmdline = Path("/proc/cmdline").read_text() if Path("/proc/cmdline").exists() else ""
    if re.search(r"(^|\s)systemd\.zram=0(\s|$)", cmdline):
        die("Kernel cmdline carries systemd.zram=0 — zram device creation is disabled by boot policy.")

def get_mount_source() -> str:
    return run_cmd(["findmnt", "-rn", "-o", "SOURCE", "--mountpoint", str(MOUNT_POINT)], ignore_errors=True)

def fix_mount_permissions() -> None:
    if not BASE_MOUNT.exists():
        BASE_MOUNT.mkdir(parents=True, mode=0o755)
    try:
        os.chmod(BASE_MOUNT, 0o755)
    except Exception:
        pass

    if not MOUNT_POINT.exists():
        MOUNT_POINT.mkdir(parents=True, mode=0o1777)
    try:
        os.chmod(MOUNT_POINT, 0o1777)
    except Exception:
        pass

    tmpfiles_content = f"""# Managed by 206_zram_tmpfs_mounts.py
d {BASE_MOUNT} 0755 root root -
d {MOUNT_POINT} 1777 root root -
z {BASE_MOUNT} 0755 root root -
z {MOUNT_POINT} 1777 root root -
"""
    try:
        write_file_atomic(TMPFILES_CONF, tmpfiles_content)
        if shutil.which("systemd-tmpfiles"):
            subprocess.run(["systemd-tmpfiles", "--create", str(TMPFILES_CONF)], capture_output=True, check=False)
    except Exception:
        pass

def get_active_mount_pids(mount_point: Path) -> list[tuple[int, str]]:
    pids = []
    proc = Path("/proc")
    for pid_dir in proc.glob("[0-9]*"):
        try:
            pid = int(pid_dir.name)
            comm_file = pid_dir / "comm"
            comm = comm_file.read_text().strip() if comm_file.exists() else "unknown"
            
            matched = False
            fd_dir = pid_dir / "fd"
            if fd_dir.is_dir():
                for fd in fd_dir.iterdir():
                    try:
                        if str(mount_point) in os.readlink(fd):
                            pids.append((pid, comm))
                            matched = True
                            break
                    except (FileNotFoundError, PermissionError):
                        continue
            if matched:
                continue
            
            cwd = os.readlink(pid_dir / "cwd")
            if str(mount_point) in cwd:
                pids.append((pid, comm))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return sorted(pids, key=lambda x: x[0])

def safely_unmount_and_stage(mount_point: Path = MOUNT_POINT) -> Path | None:
    if not mount_point.exists() or not get_mount_source():
        return None

    active = get_active_mount_pids(mount_point)
    if active:
        pid_list_str = ", ".join(f"{p[0]} ({p})" for p in active[:8])
        if len(active) > 8:
            pid_list_str += f" and {len(active)-8} more"
        warn(f"Active process(es) holding open handles on {mount_point}: {pid_list_str}")
        
        if not sys.stdin.isatty():
            die(f"Mount point {mount_point} is in active use. Aborting in non-interactive shell.")
        
        ans = input(f"  > Send SIGTERM to {len(active)} holding process(es)? [y/N]: ").strip().lower()
        if ans != 'y':
            die("Aborted by user.")
        
        for pid, _ in active:
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pass
        
        for _ in range(10):
            time.sleep(0.5)
            if not get_active_mount_pids(mount_point):
                break
        
        remaining = get_active_mount_pids(mount_point)
        if remaining:
            ans_kill = input(f"  > {len(remaining)} process(es) still active. Send SIGKILL? [y/N]: ").strip().lower()
            if ans_kill == 'y':
                for pid, _ in remaining:
                    try:
                        os.kill(pid, 9)
                    except ProcessLookupError:
                        pass
                time.sleep(0.5)
            else:
                die("Aborted by user — open processes prevented clean unmount.")

    stage_dir: Path | None = None
    try:
        subprocess.run(["sync", "-f", str(mount_point)], capture_output=True, check=False)
        items = [p for p in mount_point.iterdir() if p.name not in ("lost+found", ".Trash-1000")]
        if items:
            info(f"Detected {len(items)} item(s) on {mount_point}. Staging for seamless migration...")
            
            staging_base = Path("/var/tmp")
            staging_fstype = run_cmd(["findmnt", "-n", "-o", "FSTYPE", "-T", str(staging_base)], ignore_errors=True)
            if staging_fstype in ("tmpfs", "ramfs", "zram"):
                warn(f"Staging path {staging_base} is on {staging_fstype} (RAM-backed). Skipping staging to avoid OOM.")
            else:
                du_out = run_cmd(["du", "-s", "-B1", "--exclude=lost+found", "--exclude=.Trash-1000", str(mount_point)], ignore_errors=True)
                allocated_bytes = int(du_out.split()[0]) if du_out and du_out.split()[0].isdigit() else 512 * 1024 * 1024
                
                st = os.statvfs(str(staging_base))
                free_bytes = st.f_bavail * st.f_frsize
                required_bytes = int(allocated_bytes * 1.2) + (100 * 1024 * 1024)
                if free_bytes > required_bytes:
                    s_dir = staging_base / f".zram1_migration_{os.getpid()}_{int(time.time())}"
                    s_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
                    
                    if shutil.which("rsync"):
                        res = subprocess.run(
                            ["rsync", "-aHAX", "--sparse", "--exclude=lost+found", "--exclude=.Trash-1000", f"{mount_point}/", f"{s_dir}/"],
                            capture_output=True, text=True, check=False
                        )
                        if res.returncode == 0:
                            stage_dir = s_dir
                    
                    if not stage_dir:
                        for it in items:
                            dest = s_dir / it.name
                            if it.is_dir():
                                shutil.copytree(it, dest, symlinks=True, dirs_exist_ok=True)
                            else:
                                subprocess.run(["cp", "-a", "--sparse=always", str(it), str(dest)], check=False)
                        stage_dir = s_dir
                    
                    if stage_dir:
                        ok(f"Successfully staged {len(items)} item(s) to persistent storage ({stage_dir}).")
                else:
                    warn(f"Insufficient free space on {staging_base} ({free_bytes // (1024*1024)}MB free vs {required_bytes // (1024*1024)}MB needed).")
                    if not sys.stdin.isatty():
                        die("Cannot stage files: insufficient free space on staging disk in non-interactive mode.")
                    ans = input("  > Proceed with unmount anyway? WARNING: unstaged files on /mnt/zram1 will be lost! [y/N]: ").strip().lower()
                    if ans != 'y':
                        die("Operation aborted by user to prevent data loss.")
    except Exception as e:
        warn(f"File inspection / staging encountered: {e}. Proceeding with unmount.")

    run_cmd(["systemctl", "stop", "mnt-zram1.mount"], ignore_errors=True)
    run_cmd(["systemctl", "stop", "systemd-zram-setup@zram1.service"], ignore_errors=True)
    run_cmd(["umount", "-q", str(mount_point)], ignore_errors=True)
    if get_mount_source():
        warn(f"{mount_point} busy. Performing lazy unmount fallback...")
        run_cmd(["umount", "-f", "-l", str(mount_point)], ignore_errors=True)
        time.sleep(0.3)

    return stage_dir

def restore_staged_files(stage_dir: Path | None, mount_point: Path = MOUNT_POINT) -> None:
    if not stage_dir or not stage_dir.exists():
        return
    try:
        info(f"Restoring staged data back to {mount_point}...")
        if shutil.which("rsync"):
            res = subprocess.run(
                ["rsync", "-aHAX", "--sparse", f"{stage_dir}/", f"{mount_point}/"],
                capture_output=True, text=True, check=False
            )
            if res.returncode == 0:
                shutil.rmtree(stage_dir, ignore_errors=True)
                ok("Restored staged data successfully.")
                return
        for it in stage_dir.iterdir():
            dest = mount_point / it.name
            if it.is_dir():
                shutil.copytree(it, dest, symlinks=True, dirs_exist_ok=True)
            else:
                subprocess.run(["cp", "-a", "--sparse=always", str(it), str(dest)], check=False)
        shutil.rmtree(stage_dir, ignore_errors=True)
        ok("Restored staged data successfully.")
    except Exception as e:
        warn(f"Failed to restore staged data: {e}")

def configure_tmpfs(size_override: str = "") -> None:
    size_expr = parse_tmpfs_size_expression(size_override) if size_override else "200%"
    info(f"Initializing Native tmpfs Mount for: {C.BOLD}{MOUNT_POINT}{C.RST} (Size: {size_expr})")
    
    stage_dir = safely_unmount_and_stage()

    if ZRAM_CONF_FILE.exists():
        ZRAM_CONF_FILE.unlink()
    if LEGACY_ZRAM_CONF_FILE.exists():
        LEGACY_ZRAM_CONF_FILE.unlink()
    if SETUP_OVERRIDE_DIR.exists():
        shutil.rmtree(SETUP_OVERRIDE_DIR, ignore_errors=True)
    if LEGACY_MAKEFS_OVERRIDE_DIR.exists():
        shutil.rmtree(LEGACY_MAKEFS_OVERRIDE_DIR, ignore_errors=True)
    if PERMS_SERVICE_PATH.exists():
        PERMS_SERVICE_PATH.unlink()
    if PERMS_WANTS_SYMLINK.exists() or PERMS_WANTS_SYMLINK.is_symlink():
        PERMS_WANTS_SYMLINK.unlink()
    if PERMS_WANTS_DIR.exists():
        shutil.rmtree(PERMS_WANTS_DIR, ignore_errors=True)

    run_cmd(["zramctl", "--reset", "/dev/zram1"], ignore_errors=True)
    run_cmd(["systemctl", "daemon-reload"])

    tmpfs_content = f"""# Managed by 206_zram_tmpfs_mounts.py
[Unit]
Description=High-Performance Native tmpfs on {MOUNT_POINT}
Before=local-fs.target

[Mount]
What=tmpfs
Where={MOUNT_POINT}
Type=tmpfs
Options=rw,nosuid,nodev,noatime,size={size_expr},mode=1777

[Install]
WantedBy=local-fs.target
"""
    write_file_atomic(TMPFS_MOUNT_UNIT_PATH, tmpfs_content)
    ok(f"Tmpfs mount unit written atomically to {TMPFS_MOUNT_UNIT_PATH}")

    run_cmd(["systemctl", "daemon-reload"])
    run_cmd(["systemctl", "enable", "--now", "mnt-zram1.mount"], ignore_errors=True)

    for _ in range(8):
        if get_mount_source() == "tmpfs": break
        time.sleep(0.3)

    if get_mount_source() != "tmpfs":
        run_cmd(["mount", "-t", "tmpfs", "-o", f"rw,nosuid,nodev,noatime,size={size_expr},mode=1777", "tmpfs", str(MOUNT_POINT)], ignore_errors=True)

    fix_mount_permissions()
    restore_staged_files(stage_dir)
    fix_mount_permissions()

    if get_mount_source() == "tmpfs":
        ok(f"Live memory: Native tmpfs attached to {MOUNT_POINT} (Mode: 1777, Size: {size_expr}).")
    else:
        die("Failed to mount tmpfs. Check `systemctl status mnt-zram1.mount`.")

def get_system_ram_kb() -> int:
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
        m = re.search(r"^MemTotal:\s+(\d+)\s+kB", text, re.M)
        return int(m.group(1)) if m else 0
    except Exception:
        return 0

def configure_none() -> None:
    info(f"Disabling secondary RAM disk for {C.BOLD}{MOUNT_POINT}{C.RST} (Minimal RAM mode)...")
    safely_unmount_and_stage()

    for p in [ZRAM_CONF_FILE, LEGACY_ZRAM_CONF_FILE, TMPFS_MOUNT_UNIT_PATH, PERMS_SERVICE_PATH, TMPFILES_CONF]:
        p.unlink(missing_ok=True)

    if PERMS_WANTS_SYMLINK.exists() or PERMS_WANTS_SYMLINK.is_symlink():
        PERMS_WANTS_SYMLINK.unlink(missing_ok=True)

    for d in [SETUP_OVERRIDE_DIR, LEGACY_MAKEFS_OVERRIDE_DIR, PERMS_WANTS_DIR]:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    run_cmd(["systemctl", "disable", "--now", "mnt-zram1.mount"], ignore_errors=True)
    run_cmd(["zramctl", "--reset", "/dev/zram1"], ignore_errors=True)
    run_cmd(["systemctl", "reset-failed", "mnt-zram1.mount", "systemd-zram-setup@zram1.service"], ignore_errors=True)
    run_cmd(["systemctl", "daemon-reload"])
    ok(f"Secondary RAM disk ({MOUNT_POINT}) disabled cleanly (zero memory overhead).")

def main() -> None:
    pre_flight_checks()

    for cmd in ["systemctl", "findmnt", "umount"]:
        if shutil.which(cmd) is None:
            die(f"'{cmd}' is required but missing from system PATH.")

    if args.disable:
        configure_none()
    else:
        configure_tmpfs(args.size)
            
    ok("Memory mount subsystem configured successfully.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.YLW}Operation cancelled by user.{C.RST}")
        sys.exit(130)

