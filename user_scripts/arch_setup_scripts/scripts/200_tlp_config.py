#!/usr/bin/env python3
#d: Configure TLP power management for ASUS TUF F15

import sys
import os
import shutil
import subprocess
import pwd
from pathlib import Path

# Try to import Rich modules for beautiful styling
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.status import Status
    from rich.prompt import Confirm
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# --- CONFIGURATION AREA ---
TLP_CONFIG_CONTENT = """# tlp 1.11
# Do not use, this is custom configured for dusk's FX507ZE asus tuf f15 laptop

TLP_ENABLE=1
TLP_AUTO_SWITCH=1
TLP_PROFILE_AC=BAL
TLP_PROFILE_BAT=SAV
DISK_IDLE_SECS_ON_PRF=0
DISK_IDLE_SECS_ON_BAL=2
MAX_LOST_WORK_SECS_ON_PRF=30
MAX_LOST_WORK_SECS_ON_BAL=300
MAX_LOST_WORK_SECS_ON_SAV=600
CPU_SCALING_GOVERNOR_ON_PRF=performance
CPU_SCALING_GOVERNOR_ON_BAL=powersave
CPU_SCALING_GOVERNOR_ON_SAV=powersave
CPU_ENERGY_PERF_POLICY_ON_PRF=performance
CPU_ENERGY_PERF_POLICY_ON_BAL=balance_power
CPU_ENERGY_PERF_POLICY_ON_SAV=power
CPU_MIN_PERF_ON_PRF=0
CPU_MAX_PERF_ON_PRF=100
CPU_MIN_PERF_ON_BAL=0
CPU_MAX_PERF_ON_BAL=40
CPU_MIN_PERF_ON_SAV=0
CPU_MAX_PERF_ON_SAV=20
CPU_BOOST_ON_PRF=1
CPU_BOOST_ON_BAL=0
CPU_BOOST_ON_SAV=0
CPU_HWP_DYN_BOOST_ON_PRF=1
CPU_HWP_DYN_BOOST_ON_BAL=0
CPU_HWP_DYN_BOOST_ON_SAV=0
PLATFORM_PROFILE_ON_PRF=performance
PLATFORM_PROFILE_ON_BAL=balanced
PLATFORM_PROFILE_ON_SAV=quiet
MEM_SLEEP_ON_PRF=s2idle
MEM_SLEEP_ON_BAL=s2idle
MEM_SLEEP_ON_SAV=s2idle
DISK_DEVICES="nvme-INTEL_SSDPEKNU512GZ_BTKA151410KY512A nvme-Samsung_SSD_980_1TB_S649NL0T857112D"
DISK_IOSCHED="none none"
AHCI_RUNTIME_PM_ON_PRF=auto
AHCI_RUNTIME_PM_ON_BAL=auto
AHCI_RUNTIME_PM_ON_SAV=auto
AHCI_RUNTIME_PM_TIMEOUT=10
INTEL_GPU_MIN_FREQ_ON_PRF=100
INTEL_GPU_MIN_FREQ_ON_BAL=100
INTEL_GPU_MIN_FREQ_ON_SAV=100
INTEL_GPU_MAX_FREQ_ON_PRF=1200
INTEL_GPU_MAX_FREQ_ON_BAL=200
INTEL_GPU_MAX_FREQ_ON_SAV=200
INTEL_GPU_BOOST_FREQ_ON_PRF=1400
INTEL_GPU_BOOST_FREQ_ON_BAL=400
INTEL_GPU_BOOST_FREQ_ON_SAV=300
WIFI_PWR_ON_PRF=off
WIFI_PWR_ON_BAL=on
WIFI_PWR_ON_SAV=on
SOUND_POWER_SAVE_ON_PRF=1
SOUND_POWER_SAVE_ON_BAL=1
SOUND_POWER_SAVE_ON_SAV=1
SOUND_POWER_SAVE_CONTROLLER=Y
PCIE_ASPM_ON_PRF=performance
PCIE_ASPM_ON_BAL=powersupersave
PCIE_ASPM_ON_SAV=powersupersave
RUNTIME_PM_ON_PRF=auto
RUNTIME_PM_ON_BAL=auto
RUNTIME_PM_ON_SAV=auto
USB_AUTOSUSPEND=1
DEVICES_TO_ENABLE_ON_PRF=""
DEVICES_TO_ENABLE_ON_BAL=""
DEVICES_TO_ENABLE_ON_SAV=""
DEVICES_TO_DISABLE_ON_PRF=""
DEVICES_TO_DISABLE_ON_BAL=""
DEVICES_TO_DISABLE_ON_SAV="bluetooth"
DEVICES_TO_DISABLE_ON_PRF_NOT_IN_USE=""
DEVICES_TO_DISABLE_ON_BAL_NOT_IN_USE=""
DEVICES_TO_DISABLE_ON_SAV_NOT_IN_USE="bluetooth wifi"
START_CHARGE_THRESH_BAT1=70
STOP_CHARGE_THRESH_BAT1=80
"""

TARGET_FILE = Path("/etc/tlp.conf")

if HAS_RICH:
    console = Console()
    error_console = Console(stderr=True)
else:
    console = None
    error_console = None

def log_info(msg: str):
    if HAS_RICH:
        console.print(Text.assemble(("[INFO]", "bold blue"), f" {msg}"))
    else:
        print(f"\033[1;34m[INFO]\033[0m {msg}")

def log_success(msg: str):
    if HAS_RICH:
        console.print(Text.assemble(("[OK]", "bold green"), f" {msg}"))
    else:
        print(f"\033[1;32m[OK]\033[0m {msg}")

def log_warn(msg: str):
    if HAS_RICH:
        error_console.print(Text.assemble(("[WARN]", "bold yellow"), f" {msg}"))
    else:
        print(f"\033[1;33m[WARN]\033[0m {msg}", file=sys.stderr)

def log_error(msg: str):
    if HAS_RICH:
        error_console.print(Text.assemble(("[ERROR]", "bold red"), f" {msg}"))
    else:
        print(f"\033[1;31m[ERROR]\033[0m {msg}", file=sys.stderr)

def elevate_privileges():
    """Reruns the script under sudo if not root."""
    if os.geteuid() != 0:
        log_info("Root privileges required. Elevating...")
        try:
            subprocess.run(["sudo", sys.executable] + sys.argv, check=True)
            sys.exit(0)
        except subprocess.CalledProcessError as e:
            log_error(f"Failed to elevate privileges: {e}")
            sys.exit(1)

def confirm_target_machine():
    """Asks the user for confirmation if they are running interactively."""
    sys_vendor = "Unknown"
    sys_product = "Unknown"

    vendor_path = Path("/sys/class/dmi/id/sys_vendor")
    product_path = Path("/sys/class/dmi/id/product_name")

    if vendor_path.exists():
        sys_vendor = vendor_path.read_text().strip()
    if product_path.exists():
        sys_product = product_path.read_text().strip()

    if HAS_RICH:
        title_text = Text("Dusky Personal TLP Configurator", style="bold white")
        console.print(Panel(
            Text.assemble(
                ("Target System: ", "bold"), f"{sys_vendor} {sys_product}\n",
                ("Intended For:  ", "bold"), "Dusk's Personal ASUS Laptop FX507\n",
                ("Action:        ", "bold"), "Configures system power management variables in /etc/tlp.conf"
            ),
            title=title_text,
            border_style="cyan"
        ))
    else:
        print(f"\nSystem identifies as: {sys_vendor} {sys_product}")
        print("This script is configured for: Dusk's Personal ASUS Laptop")
        print(f"It will modify {TARGET_FILE}.")

    # Only ask for input if stdin is a terminal
    if sys.stdin.isatty():
        if HAS_RICH:
            try:
                if not Confirm.ask("[bold yellow]Do you want to proceed with applying this TLP configuration?[/bold yellow]"):
                    log_info("Operation cancelled by user.")
                    sys.exit(0)
            except (KeyboardInterrupt, EOFError):
                log_error("Input interrupted. Aborting.")
                sys.exit(1)
        else:
            try:
                response = input("\nDo you want to proceed with applying this TLP configuration? [y/N] ").strip().lower()
                if response not in ("y", "yes"):
                    log_info("Operation cancelled by user.")
                    sys.exit(0)
            except (KeyboardInterrupt, EOFError):
                log_error("Input interrupted. Aborting.")
                sys.exit(1)
    else:
        log_info("Non-interactive run detected. Proceeding automatically.")

def main():
    elevate_privileges()
    confirm_target_machine()

    # Install dependencies (pacman -S --needed --noconfirm tlp tlp-rdw)
    pkgs = ["tlp", "tlp-rdw"]
    log_info(f"Ensuring required packages are installed: {', '.join(pkgs)}")
    
    if HAS_RICH:
        with console.status("[bold green]Running pacman dependency synchronization...[/bold green]"):
            res = subprocess.run(["pacman", "-S", "--needed", "--noconfirm"] + pkgs, capture_output=True)
    else:
        res = subprocess.run(["pacman", "-S", "--needed", "--noconfirm"] + pkgs, capture_output=True)

    if res.returncode == 0:
        log_success("Packages are installed and up-to-date.")
    else:
        log_error("Failed to install required packages via pacman.")
        sys.exit(1)

    # Backup logic
    real_user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "dusk"
    try:
        pw = pwd.getpwnam(real_user)
        real_home = Path(pw.pw_dir)
        real_uid = pw.pw_uid
        real_gid = pw.pw_gid
    except KeyError:
        real_home = Path("/home/dusk")
        real_uid = 1000
        real_gid = 1000

    backup_dir = real_home / "Documents"
    backup_file = backup_dir / "tlp_backup.conf"
    file_existed = TARGET_FILE.exists()

    if file_existed:
        if not backup_dir.exists():
            log_info(f"Creating directory {backup_dir}...")
            backup_dir.mkdir(parents=True, exist_ok=True)
            os.chown(str(backup_dir), real_uid, real_gid)

        log_info(f"Backing up current config to {backup_file}...")
        try:
            shutil.copy2(TARGET_FILE, backup_file)
            os.chown(str(backup_file), real_uid, real_gid)
            log_success("Backup verified.")
        except OSError as e:
            log_warn(f"Failed to create backup: {e}")

    # Write configuration file
    if file_existed:
        log_info(f"Overwriting existing file at {TARGET_FILE}...")
    else:
        log_info(f"File did not exist. Creating new file at {TARGET_FILE}...")

    try:
        TARGET_FILE.write_text(TLP_CONFIG_CONTENT, encoding="utf-8")
        log_success("Configuration written successfully.")
    except OSError as e:
        log_error(f"Failed to write to {TARGET_FILE}: {e}")
        sys.exit(1)

    # Service management (systemctl enable --now, restart tlp.service)
    log_info("Enabling and starting systemd services...")
    
    if HAS_RICH:
        with console.status("[bold green]Enabling tlp.service...[/bold green]"):
            res = subprocess.run(["systemctl", "enable", "--now", "tlp.service"], capture_output=True)
    else:
        res = subprocess.run(["systemctl", "enable", "--now", "tlp.service"], capture_output=True)

    if res.returncode == 0:
        log_success("Service (tlp.service) enabled and active.")
    else:
        log_error("Failed to enable or start TLP services.")
        sys.exit(1)

    log_info("Restarting TLP service to apply configurations...")
    if HAS_RICH:
        with console.status("[bold green]Restarting tlp.service...[/bold green]"):
            res = subprocess.run(["systemctl", "restart", "tlp.service"], capture_output=True)
    else:
        res = subprocess.run(["systemctl", "restart", "tlp.service"], capture_output=True)

    if res.returncode == 0:
        log_success("TLP service restarted successfully.")
    else:
        log_error("Failed to restart TLP service.")
        sys.exit(1)

    log_success("TLP configuration pipeline completed successfully.")

if __name__ == "__main__":
    main()
