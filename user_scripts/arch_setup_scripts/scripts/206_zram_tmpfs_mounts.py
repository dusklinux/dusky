#!/usr/bin/env python3
# =============================================================================
# Elite Arch Linux Hybrid Memory Mount Configurator
# Target: Arch Linux Cutting-Edge (Kernel 7.1+, Python 3.14+, systemd 260+)
# Scope: Platinum Grade. High-Performance RAM Disks via Tmpfs or ZRAM block.
# Updates: Decoupled. Strictly handles mount states. Recompression timer 
#          delegated to standalone global daemon script.
# =============================================================================

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NoReturn

# --- Presentation (Zero-Dependency ANSI) ---
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

# --- Argument Parsing (Executed BEFORE Privilege Escalation) ---
parser = argparse.ArgumentParser(description="Elite Arch Linux Hybrid Memory Mount Configurator")
group = parser.add_mutually_exclusive_group()
group.add_argument("--tmpfs", action="store_true", help="Autonomously deploy pure Tmpfs mapping")
group.add_argument("--zram", action="store_true", help="Autonomously deploy Ext4 ZRAM block mapping")
parser.add_argument("--no-color", action="store_true", help="Disable ANSI color output")

args = parser.parse_args()

if args.no_color or not sys.stdout.isatty() or "NO_COLOR" in os.environ:
    C.strip()

# --- Privilege Escalation ---
def escalate_privileges() -> None:
    if os.geteuid() != 0:
        info("Root privileges required. Escalating...")
        if subprocess.call(["command", "-v", "sudo"], stdout=subprocess.DEVNULL, shell=True) != 0:
            die("sudo is required to run this script as root.")
        os.execvp("sudo", ["sudo", sys.executable, os.path.abspath(__file__)] + sys.argv[1:])

escalate_privileges()

# --- Core Constants ---
MOUNT_POINT = "/mnt/zram1"
ZRAM_SIZE_EXPR = "ram"
ZRAM_RESIDENT_LIMIT_EXPR = "ram * 4 / 5"
COMPRESSION_ALGORITHM = "zstd(level=2)"

FS_OPTIONS = "rw,nosuid,nodev,discard,noatime,lazytime,X-mount.mode=1777"
CMD_TIMEOUT = 15

# --- Target User Identification (Wayland/Hyprland Safety) ---
TARGET_UID = int(os.environ.get("SUDO_UID", str(os.getuid())))
TARGET_GID = int(os.environ.get("SUDO_GID", str(os.getgid())))

if TARGET_UID == 0:
    err("Executed as raw root (UID 0).")
    print("Wayland/Hyprland requires user-space ownership of temporary filesystems.")
    die("Please execute this script using 'sudo ./206_memory_mounts.py' from your normal user account.")

# --- Utility Functions ---
def run_cmd(cmd: list[str], ignore_errors: bool = False) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=not ignore_errors, timeout=CMD_TIMEOUT)
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        die(f"Command timed out after {CMD_TIMEOUT}s: {' '.join(cmd)}")
    except subprocess.CalledProcessError as e:
        if not ignore_errors:
            err(f"Command failed: {' '.join(cmd)}")
            print(e.stderr)
            sys.exit(1)
        return ""

def write_file_atomic(path: Path, content: str, mode: int = 0o644) -> None:
    if path.exists() and path.read_text() == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

def pre_flight_checks() -> None:
    if subprocess.run(["systemd-detect-virt", "--quiet", "--container"], capture_output=True).returncode == 0:
        die("Container detected — refusing to tune memory mounts inside a container.")
    
    cmdline = Path("/proc/cmdline").read_text() if Path("/proc/cmdline").exists() else ""
    if re.search(r"(^|\s)systemd\.zram=0(\s|$)", cmdline):
        die("Kernel cmdline carries systemd.zram=0 — zram device creation is disabled by boot policy.")

def unit_is_loaded(unit: str) -> bool:
    stdout = run_cmd(["systemctl", "show", "-p", "LoadState", "--value", unit], ignore_errors=True)
    return stdout == "loaded"

def assert_unit_loaded(unit: str) -> None:
    if not unit_is_loaded(unit):
        die(f"Systemd failed to ingest the generated unit: {unit}")

def get_mount_source() -> str:
    return run_cmd(["findmnt", "-rn", "-o", "SOURCE", "--mountpoint", MOUNT_POINT], ignore_errors=True)

def prepare_mount_directory() -> None:
    mp_path = Path(MOUNT_POINT)
    if mp_path.exists():
        if not mp_path.is_dir():
            die(f"{MOUNT_POINT} exists but is not a directory.")
    else:
        mp_path.mkdir(mode=0o755, parents=True)
    
    os.chown(MOUNT_POINT, TARGET_UID, TARGET_GID)
    ok(f"Directory prepared with strict UID {TARGET_UID} / GID {TARGET_GID} ownership.")

def resolve_live_conflicts(target_backend: str, mount_unit_name: str) -> None:
    current = get_mount_source()
    if not current:
        return

    if target_backend == "tmpfs" and current in ("/dev/zram1", "zram1"):
        warn("Tearing down legacy ZRAM block device to free memory...")
        run_cmd(["systemctl", "stop", "systemd-zram-setup@zram1.service"], ignore_errors=True)
        run_cmd(["umount", "-q", MOUNT_POINT], ignore_errors=True)
        
    elif target_backend == "zram" and current == "tmpfs":
        warn("Unmounting live Tmpfs to prepare for ZRAM block allocation...")
        run_cmd(["systemctl", "stop", mount_unit_name], ignore_errors=True)
        run_cmd(["umount", "-q", MOUNT_POINT], ignore_errors=True)

# --- Backend Configurators ---
def configure_tmpfs(mount_unit_name: str, mount_unit_path: Path) -> None:
    if get_mount_source() == "tmpfs" and mount_unit_path.exists():
        ok(f"Tmpfs architecture is already active and perfectly configured at {MOUNT_POINT}. No action required.")
        return

    info(f"Initializing Pure Tmpfs Mount for: {C.BOLD}{MOUNT_POINT}{C.RST}")
    resolve_live_conflicts("tmpfs", mount_unit_name)

    zram_conf = Path("/etc/systemd/zram-generator.conf.d/99-elite-zram1.conf")
    if zram_conf.exists():
        zram_conf.unlink()
        run_cmd(["systemctl", "daemon-reload"])

    tmpfs_content = f"""# Managed by Elite Arch Linux Configurator
[Unit]
Description=High-Performance tmpfs for {MOUNT_POINT}
Before=local-fs.target
ConditionPathExists={MOUNT_POINT}

[Mount]
What=tmpfs
Where={MOUNT_POINT}
Type=tmpfs
Options=rw,nosuid,nodev,relatime,size=100%,mode=0755,uid={TARGET_UID},gid={TARGET_GID}

[Install]
WantedBy=local-fs.target
"""
    write_file_atomic(mount_unit_path, tmpfs_content)
    ok(f"Tmpfs mount unit written atomically to {mount_unit_path}")

    info("Reloading systemd daemon...")
    run_cmd(["systemctl", "daemon-reload"])
    assert_unit_loaded(mount_unit_name)

    info("Enabling systemd mount unit...")
    run_cmd(["systemctl", "enable", mount_unit_name], ignore_errors=True)
    run_cmd(["systemctl", "start", mount_unit_name], ignore_errors=True)
    
    for _ in range(6):
        if get_mount_source() == "tmpfs": break
        time.sleep(0.5)
    
    if get_mount_source() == "tmpfs":
        ok(f"Live memory: Pure tmpfs successfully attached to {MOUNT_POINT}.")
    else:
        die(f"Failed to mount pure tmpfs. Check 'systemctl status {mount_unit_name}'.")

def configure_zram(mount_unit_name: str, mount_unit_path: Path) -> None:
    config_dir = Path("/etc/systemd/zram-generator.conf.d")
    zram_conf = config_dir / "99-elite-zram1.conf"

    zram_content = f"""# Managed by Elite Arch Linux Configurator.
[zram1]
zram-size = {ZRAM_SIZE_EXPR}
zram-resident-limit = {ZRAM_RESIDENT_LIMIT_EXPR}
fs-type = ext4
mount-point = {MOUNT_POINT}
compression-algorithm = {COMPRESSION_ALGORITHM}
options = {FS_OPTIONS}
"""

    if get_mount_source() in ("/dev/zram1", "zram1") and zram_conf.exists() and zram_conf.read_text() == zram_content:
        ok(f"Ext4 ZRAM architecture is already active and perfectly configured at {MOUNT_POINT}. No action required.")
        return

    info(f"Initializing Ext4 ZRAM Block Mount for: {C.BOLD}{MOUNT_POINT}{C.RST}")
    resolve_live_conflicts("zram", mount_unit_name)

    if mount_unit_path.exists():
        mount_unit_path.unlink()
        run_cmd(["systemctl", "daemon-reload"])
    write_file_atomic(zram_conf, zram_content)
    ok(f"ZRAM pool configuration written atomically to {zram_conf}")

    override_dir = Path("/etc/systemd/system/systemd-zram-setup@zram1.service.d")
    override_dir.mkdir(parents=True, exist_ok=True)
    override_conf = override_dir / "override.conf"
    
    # Strip the failed ExecStartPre hook but retain the essential tune2fs hook
    override_content = """[Service]
ExecStartPost=/usr/sbin/tune2fs -O ^has_journal /dev/%i
"""
    write_file_atomic(override_conf, override_content)
    ok(f"Journal-less Ext4 systemd override deployed to {override_conf}")

    info("Reloading systemd generators...")
    run_cmd(["systemctl", "daemon-reload"])
    assert_unit_loaded("systemd-zram-setup@zram1.service")
    
    info("Engaging ZRAM generator pipeline...")
    run_cmd(["systemctl", "restart", "systemd-zram-setup@zram1.service"], ignore_errors=True)

    for _ in range(6):
        if get_mount_source() in ("/dev/zram1", "zram1"): break
        time.sleep(0.5)

    if get_mount_source() in ("/dev/zram1", "zram1"):
        ok(f"Live memory: Ext4 ZRAM successfully attached to {MOUNT_POINT}.")
    else:
        warn("Live ZRAM configuration delayed. Reboot the system to finalize the architecture.")

def ask_backend() -> str:
    current = get_mount_source()
    zram_conf = Path("/etc/systemd/zram-generator.conf.d/99-elite-zram1.conf")
    
    tmpfs_tag = f"{C.GRN} [LIVE & ACTIVE]{C.RST}" if current == "tmpfs" else ""
    
    if current in ("/dev/zram1", "zram1"):
        zram_tag = f"{C.GRN} [LIVE & ACTIVE]{C.RST}"
    elif zram_conf.exists() and current != "tmpfs":
        zram_tag = f"{C.YLW} [STAGED - PENDING REBOOT]{C.RST}"
    else:
        zram_tag = ""

    print(f"\n  {C.CYN}[ Select backend for {MOUNT_POINT} ]{C.RST}")
    print(f"   {C.BOLD}1{C.RST}) tmpfs (Pure RAM Mapping){tmpfs_tag}")
    print(f"   {C.BOLD}2{C.RST}) zram  (Ext4 Compressed Block){zram_tag}")
    while True:
        raw = input("  > ").strip().lower()
        if raw in ("1", "tmpfs"): return "tmpfs"
        if raw in ("2", "zram"): return "zram"
        if raw in ("q", "quit"): sys.exit(0)
        print(f"  {C.RED}Invalid choice.{C.RST} Select 1 or 2.")

# --- Entry Point ---
def main() -> None:
    if sys.version_info < (3, 14):
        die(f"Python 3.14+ required, running {sys.version.split()[0]}")

    pre_flight_checks()

    match (args.tmpfs, args.zram):
        case (True, False): backend = "tmpfs"
        case (False, True): backend = "zram"
        case _: backend = ask_backend()

    for cmd in ["systemctl", "systemd-escape", "findmnt", "umount"]:
        if subprocess.call(["command", "-v", cmd], stdout=subprocess.DEVNULL, shell=True) != 0:
            die(f"'{cmd}' is required but missing from system PATH.")

    mount_unit_name = run_cmd(["systemd-escape", "--path", f"--suffix=mount", MOUNT_POINT])
    mount_unit_path = Path("/etc/systemd/system") / mount_unit_name

    prepare_mount_directory()

    match backend:
        case "tmpfs": configure_tmpfs(mount_unit_name, mount_unit_path)
        case "zram": configure_zram(mount_unit_name, mount_unit_path)
            
    ok(f"Subsystem configured. Target is ready for high-I/O workloads.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.YLW}aborted — operation cancelled by user.{C.RST}")
        sys.exit(130)
