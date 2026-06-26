#!/usr/bin/env python3
# =============================================================================
# Elite Arch Linux Global ZRAM Recompression Daemon
# Target: Arch Linux Cutting-Edge (Kernel 7.1+, Python 3.14+, systemd 260+)
# Scope: Platinum Grade. Autonomous idle page deep-compression scheduling.
# =============================================================================

import os
import subprocess
import sys
import tempfile
from pathlib import Path

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
def err(msg: str) -> None: print(f"{C.RED}[FAIL]{C.RST} {msg}", file=sys.stderr)
def die(msg: str, code: int = 1) -> "typing.NoReturn": # noqa: F821
    err(msg)
    sys.exit(code)

if not sys.stdout.isatty() or "NO_COLOR" in os.environ:
    C.strip()

# --- Privilege Escalation ---
def escalate_privileges() -> None:
    if os.geteuid() != 0:
        info("Root privileges required. Escalating...")
        if subprocess.call(["command", "-v", "sudo"], stdout=subprocess.DEVNULL, shell=True) != 0:
            die("sudo is required to run this script as root.")
        os.execvp("sudo", ["sudo", sys.executable, os.path.abspath(__file__)] + sys.argv[1:])

escalate_privileges()

# --- Utility Functions ---
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

# --- Core Deployment Logic ---
def deploy_universal_recompression_timer() -> None:
    info("Generating global ZRAM recompression systemd units...")

    # We use the explicit `-` ignore-fail syntax. If a user only runs zram0, 
    # systemd elegantly ignores the zram1 failure and processes zram0 anyway.
    recomp_service_path = Path("/etc/systemd/system/zram-recompress.service")
    recomp_service_content = """[Unit]
Description=Trigger ZRAM idle page deep recompression

[Service]
Type=oneshot
ExecStart=-/bin/sh -c 'echo 600 > /sys/block/zram0/idle && echo type=idle > /sys/block/zram0/recompress'
ExecStart=-/bin/sh -c 'echo 600 > /sys/block/zram1/idle && echo type=idle > /sys/block/zram1/recompress'
"""
    write_file_atomic(recomp_service_path, recomp_service_content)
    ok(f"Service payload written to {recomp_service_path}")

    recomp_timer_path = Path("/etc/systemd/system/zram-recompress.timer")
    recomp_timer_content = """[Unit]
Description=Global Timer for ZRAM idle page recompression

[Timer]
OnBootSec=15min
OnUnitActiveSec=15min


[Install]
WantedBy=timers.target
"""
    write_file_atomic(recomp_timer_path, recomp_timer_content)
    ok(f"Timer schedule written to {recomp_timer_path}")
    
    info("Reloading systemd daemon...")
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    
    info("Enabling and starting global timer...")
    subprocess.run(["systemctl", "enable", "--now", "zram-recompress.timer"], check=True)
    
    ok("Background recompression daemon fully active. Memory density optimized.")

def main() -> None:
    if sys.version_info < (3, 14):
        die(f"Python 3.14+ required, running {sys.version.split()[0]}")
    
    deploy_universal_recompression_timer()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.YLW}aborted — operation cancelled by user.{C.RST}")
        sys.exit(130)
