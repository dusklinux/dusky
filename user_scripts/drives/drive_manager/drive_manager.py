#!/usr/bin/env python3

"""
==============================================================================
 UNIVERSAL DRIVE MANAGER (PLATINUM EDITION)
 ------------------------------------------------------------------------------
 Architecture updated to strict, cutting-edge standards based on the latest 
 util-linux (2.42+) and cryptsetup (2.8+) man pages.
 
 Features:
  - Native UUID= tagging for cryptsetup and mount mechanisms
  - Atomic directory creation via mount --mkdir
  - Deprecated luksOpen syntax replaced with `open --type luks`
  - Zero-dependency TOML parsing (Python 3.11+ tomllib)
  - Arch Linux Auto-Bootstrapper for required UI/Sec dependencies
  - Kernel-level findmnt --evaluate tag resolution
  - Pre-emptive `sudo -v` credential priming to prevent stdin pipe collision
  - Hybrid Ghost-Mapper resolution (maintains 3rd-party interoperability)
==============================================================================
"""

import os
import sys
import time
import fcntl
import json
import getpass
import argparse
import tomllib
import subprocess
import shutil
from pathlib import Path
from typing import Any
from dataclasses import dataclass

# ------------------------------------------------------------------------------
#  ARCH LINUX AUTO-BOOTSTRAPPER
# ------------------------------------------------------------------------------
try:
    import keyring
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
except ImportError:
    print("\n[INFO] Missing required Python libraries: 'keyring' and/or 'rich'.")
    print("[INFO] Attempting to auto-install via pacman...")
    try:
        subprocess.run(
            ["sudo", "pacman", "-S", "--needed", "--noconfirm", "python-keyring", "python-rich"],
            check=True
        )
        print("[SUCCESS] Dependencies installed. Seamlessly restarting script...\n")
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except subprocess.CalledProcessError:
        print("\n[ERROR] Failed to install dependencies automatically.")
        sys.exit(1)
    except FileNotFoundError:
        print("\n[ERROR] 'pacman' command not found. Are you on Arch Linux?")
        sys.exit(1)

# ------------------------------------------------------------------------------
#  CONSTANTS & GLOBALS
# ------------------------------------------------------------------------------
FILESYSTEM_TIMEOUT = 15
LOCK_RETRY_DELAY = 1
LOCK_MAX_RETRIES = 5
LOCK_FILE = Path("/tmp/.drive_manager.lock")
KEYRING_SERVICE = "drive_manager"

console = Console()
lock_fd = None

# ------------------------------------------------------------------------------
#  DATA STRUCTURES
# ------------------------------------------------------------------------------
@dataclass
class Drive:
    name: str
    type: str  # "PROTECTED" | "SIMPLE"
    mountpoint: Path
    outer_uuid: str
    inner_uuid: str | None = None
    hint: str | None = None

# ------------------------------------------------------------------------------
#  LOGGING & UI
# ------------------------------------------------------------------------------
def log(msg: str):
    console.print(f"[bold blue]\\[DRIVE][/] {msg}")

def success(msg: str):
    console.print(f"[bold green]\\[SUCCESS][/] {msg}")

def err(msg: str):
    console.print(f"[bold red]\\[ERROR][/] {msg}")

def hint_msg(msg: str):
    console.print(f"[bold yellow]\\[HINT][/] {msg}")

# ------------------------------------------------------------------------------
#  SYSTEM HELPERS & KERNEL INTERFACES
# ------------------------------------------------------------------------------
def prevent_root_execution():
    """Ensures the script is run as a normal user to keep Keyring D-Bus access valid."""
    if os.geteuid() == 0:
        err("Do NOT run this script with `sudo`!")
        console.print("Running as root breaks access to your user's desktop keyring.")
        console.print("The script will securely request sudo permissions internally when needed.")
        sys.exit(1)

def prime_sudo():
    """
    Primes the sudo credential cache. 
    CRITICAL: If sudo prompts for a password via stdin while we are simultaneously piping 
    a LUKS password to `subprocess.run`, sudo will swallow the LUKS password, fail, and hang.
    This guarantees the TTY prompt occurs cleanly before piped operations begin.
    """
    try:
        subprocess.run(["sudo", "-v"], check=True)
    except subprocess.CalledProcessError:
        err("Sudo authentication failed. Cannot proceed.")
        sys.exit(1)

def acquire_lock():
    """Acquires a kernel-level exclusive file lock to prevent concurrent executions."""
    global lock_fd
    try:
        lock_fd = open(LOCK_FILE, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
    except BlockingIOError:
        err("Another instance of drive_manager is currently running.")
        sys.exit(1)
    except Exception as e:
        err(f"Could not open lock file: {e}")
        sys.exit(1)

def check_dependencies():
    """Ensures necessary OS binaries exist."""
    deps = ["mount", "umount", "findmnt", "lsblk", "udevadm", "sudo", "cryptsetup"]
    missing = [cmd for cmd in deps if shutil.which(cmd) is None]
    if missing:
        err(f"Missing required commands: {', '.join(missing)}")
        sys.exit(1)

def resolve_device(uuid: str) -> Path | None:
    """Returns the fully resolved Path to a block device, resolving any symlinks."""
    dev_path = Path(f"/dev/disk/by-uuid/{uuid}")
    if dev_path.exists():
        return dev_path.resolve()
    return None

def wait_for_device(uuid: str, timeout: int) -> bool:
    """Waits for udev to populate the /dev/disk/by-uuid tree."""
    subprocess.run(["udevadm", "settle", f"--timeout={timeout}"], capture_output=True)
    start = time.time()
    while (time.time() - start) < timeout:
        if resolve_device(uuid):
            return True
        time.sleep(1)
    return False

def get_mount_info(target_dir: Path) -> dict[str, Any] | None:
    """
    Uses findmnt JSON output to safely detect if a directory is mounted.
    - `--mountpoint`: strictly evaluates the directory node itself to prevent parent bleed.
    - `--evaluate`: converts all kernel UUID=/LABEL= tags back to /dev/ paths for true 1:1 validation.
    """
    cmd = ["findmnt", "--json", "--evaluate", "--mountpoint", str(target_dir)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    
    if res.returncode == 0:
        try:
            data = json.loads(res.stdout)
            if "filesystems" in data and data["filesystems"]:
                return data["filesystems"][0]
        except json.JSONDecodeError:
            pass
    return None

def get_crypt_mapper_name(outer_uuid: str) -> str | None:
    """
    Uses lsblk to find the /dev/mapper/ NAME attached to the physical encrypted drive.
    - `--tree`: Enforced per lsblk.md to guarantee `children[]` population.
    Returns just the basename (e.g., 'luks-xxx') per modern `cryptsetup close <name>` spec.
    """
    cmd = ["lsblk", f"/dev/disk/by-uuid/{outer_uuid}", "--json", "--tree", "-o", "NAME,TYPE"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    
    if res.returncode == 0:
        try:
            data = json.loads(res.stdout)
            for device in data.get("blockdevices", []):
                for child in device.get("children", []):
                    if child.get("type") == "crypt":
                        return child.get("name")
        except json.JSONDecodeError:
            pass
    return None

def run_sudo_cmd(cmd: list[str], stdin_data: str | None = None) -> bool:
    """Helper to run a sudo command securely, surfacing internal stderr logs if it fails."""
    try:
        if stdin_data is not None:
            res = subprocess.run(cmd, input=stdin_data, text=True, capture_output=True)
            if res.returncode != 0:
                if res.stderr:
                    err(f"Subprocess kernel error: {res.stderr.strip()}")
                return False
            return True
        else:
            # Without stdin_data, let standard streams flow naturally (stdout/stderr to console)
            res = subprocess.run(cmd)
            return res.returncode == 0
    except Exception as e:
        err(f"Command execution failed: {e}")
        return False

# ------------------------------------------------------------------------------
#  CONFIG PARSING
# ------------------------------------------------------------------------------
def load_config(override_path: Path | None = None) -> dict[str, Drive]:
    """Loads and validates drives.toml into native dataclasses."""
    
    # Handle explicit CLI override natively
    if override_path:
        if not override_path.exists():
            err(f"Explicit config file '{override_path}' not found.")
            sys.exit(1)
        target_config = override_path
    else:
        config_paths = [
            Path.home() / ".config" / "drive_manager" / "drives.toml",
            Path(__file__).parent / "drives.toml"
        ]
        target_config = next((p for p in config_paths if p.exists()), None)

    if not target_config:
        err("Configuration file 'drives.toml' not found.")
        console.print("Please place it in `~/.config/drive_manager/drives.toml` or the script directory.")
        sys.exit(1)

    try:
        with open(target_config, "rb") as f:
            raw_data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        err(f"Failed to parse TOML config: {e}")
        sys.exit(1)

    drives: dict[str, Drive] = {}
    drive_entries = raw_data.get("drives", {})

    for name, data in drive_entries.items():
        try:
            drives[name] = Drive(
                name=name,
                type=data["type"].upper(),
                mountpoint=Path(data["mountpoint"]),
                outer_uuid=data["outer_uuid"],
                inner_uuid=data.get("inner_uuid"),
                hint=data.get("hint")
            )
            # Validation
            if drives[name].type not in ["PROTECTED", "SIMPLE"]:
                raise ValueError(f"Invalid type '{drives[name].type}'")
            if drives[name].type == "PROTECTED" and not drives[name].inner_uuid:
                raise ValueError("PROTECTED drives require an inner_uuid")
        except KeyError as e:
            err(f"Config error in drive '{name}': Missing required key {e}")
            sys.exit(1)
        except ValueError as e:
            err(f"Config error in drive '{name}': {e}")
            sys.exit(1)

    return drives

# ------------------------------------------------------------------------------
#  CORE ENGINE
# ------------------------------------------------------------------------------
def show_status(drives: dict[str, Drive]):
    """Renders a visually appealing status table mapping system reality to config."""
    table = Table(show_header=True, header_style="bold white", border_style="bright_black")
    table.add_column("DRIVE", width=14)
    table.add_column("TYPE", width=10)
    table.add_column("STATUS", width=12)
    table.add_column("MOUNTPOINT")

    for name, drive in sorted(drives.items()):
        target_uuid = drive.inner_uuid if drive.type == "PROTECTED" else drive.outer_uuid
        mount_info = get_mount_info(drive.mountpoint)
        is_mounted = False

        if mount_info:
            source_str = mount_info.get("source")
            if source_str:
                actual_source = Path(source_str).resolve()
                expected_dev = resolve_device(target_uuid)
                
                if expected_dev and expected_dev == actual_source:
                    is_mounted = True
                elif target_uuid and target_uuid.lower() in source_str.lower():
                     is_mounted = True

        if is_mounted:
            table.add_row(f"[bold green]●[/] {name}", drive.type, "[bold green]Mounted[/]", str(drive.mountpoint))
        else:
            table.add_row(f"[bold red]○[/] {name}", drive.type, "[bold red]Unmounted[/]", str(drive.mountpoint))

    console.print()
    console.print(table)
    console.print()

def do_unlock(drive: Drive):
    """Unlocks and mounts the drive using latest kernel semantics."""
    prime_sudo()
    log(f"Starting unlock sequence for '{drive.name}'...")

    target_uuid = drive.inner_uuid if drive.type == "PROTECTED" else drive.outer_uuid
    mount_info = get_mount_info(drive.mountpoint)

    # 1. Check if occupied
    if mount_info:
        source_str = mount_info.get("source", "")
        actual_source = Path(source_str).resolve() if source_str else Path()
        expected_dev = resolve_device(target_uuid)

        if expected_dev and expected_dev == actual_source:
            success(f"'{drive.name}' is already successfully mounted at {drive.mountpoint}")
            return
        else:
            err(f"Mountpoint {drive.mountpoint} is occupied by another device: {actual_source}")
            sys.exit(1)

    # 2. Decrypt if PROTECTED
    if drive.type == "PROTECTED":
        if not resolve_device(drive.outer_uuid):
            err(f"Physical drive not found (Outer UUID: {drive.outer_uuid}). Is it plugged in?")
            sys.exit(1)

        if resolve_device(drive.inner_uuid):
            log("Crypt container is already unlocked.")
        else:
            log("Unlocking encrypted container...")
            mapper_name = f"luks-{drive.outer_uuid}"
            
            # As per cryptsetup.md: use `open --type luks UUID=<uuid>` (replaces legacy `luksOpen`)
            base_cmd = ["sudo", "cryptsetup", "open", "--type", "luks", f"UUID={drive.outer_uuid}", mapper_name]

            pwd = keyring.get_password(KEYRING_SERVICE, drive.name)
            
            if pwd:
                log("Password found in secure keyring. Supplying to cryptsetup...")
                # "--key-file -" explicitly maps to stdin reading
                cmd = base_cmd + ["--key-file", "-"]
                if not run_sudo_cmd(cmd, stdin_data=pwd):
                    err("Decryption failed. Keyring password might be incorrect.")
                    sys.exit(1)
            else:
                log("No password in keyring. Falling back to manual terminal prompt.")
                if drive.hint:
                    hint_msg(drive.hint)
                if not run_sudo_cmd(base_cmd):
                    err("Decryption failed or was cancelled.")
                    sys.exit(1)

            log("Waiting for filesystem block device to populate...")
            if not wait_for_device(drive.inner_uuid, FILESYSTEM_TIMEOUT):
                err("Timeout waiting for inner filesystem to appear.")
                sys.exit(1)

    # 3. Mount Filesystem
    log(f"Mounting to {drive.mountpoint}...")
    
    # As per mount.md: utilize native `UUID=` tags, explicit `--source`/`--target`, and atomic `--mkdir`
    cmd = [
        "sudo", "mount", 
        "--mkdir", 
        "--source", f"UUID={target_uuid}", 
        "--target", str(drive.mountpoint)
    ]
    
    if run_sudo_cmd(cmd):
        success(f"'{drive.name}' successfully mounted.")
    else:
        err(f"Failed to mount UUID={target_uuid} to {drive.mountpoint}.")
        sys.exit(1)

def do_lock(drive: Drive):
    """Unmounts and locks the crypt device securely."""
    prime_sudo()
    log(f"Starting lock sequence for '{drive.name}'...")

    mount_info = get_mount_info(drive.mountpoint)

    # 1. Unmount safely
    if mount_info:
        log(f"Unmounting {drive.mountpoint}...")
        # As per umount.md: umount inherently flushes buffers for the targeted filesystem.
        # Calling global os.sync() here is an archaic anti-pattern and has been removed.
        if not run_sudo_cmd(["sudo", "umount", str(drive.mountpoint)]):
            err(f"Failed to unmount. A process might be locking the filesystem (check 'lsof +f -- {drive.mountpoint}').")
            sys.exit(1)
        log("Unmount successful.")
    else:
        log(f"{drive.mountpoint} is already unmounted.")

    # 2. Lock Cryptsetup
    if drive.type == "PROTECTED":
        mapper_name = None
        physical_present = resolve_device(drive.outer_uuid)
        
        if physical_present:
            # Drive is physically present. Ask kernel for exact mapper name to ensure interoperability 
            # (handles drives unlocked by OS/udisks2 instead of our script).
            mapper_name = get_crypt_mapper_name(drive.outer_uuid)
        else:
            # Drive is physically missing (surprise removal). lsblk will fail, so we check for a ghost
            # node matching our script's deterministic naming scheme.
            deterministic_name = f"luks-{drive.outer_uuid}"
            if Path(f"/dev/mapper/{deterministic_name}").exists():
                hint_msg("Physical drive missing, but ghost mapper detected. Forcing cleanup.")
                mapper_name = deterministic_name
            elif resolve_device(drive.inner_uuid):
                err("Device is active under an unknown mapper name and physical drive is missing. Cannot securely lock.")
                sys.exit(1)
            else:
                success("Device removed physically, container is no longer active.")
                return
        
        if mapper_name:
            time.sleep(1)
            subprocess.run(["udevadm", "settle", "--timeout=5"], capture_output=True)

            log(f"Locking crypt node: {mapper_name}...")
            
            for attempt in range(LOCK_MAX_RETRIES):
                if run_sudo_cmd(["sudo", "cryptsetup", "close", mapper_name]):
                    success("Encrypted container successfully locked.")
                    return
                log(f"Lock attempt {attempt+1}/{LOCK_MAX_RETRIES} failed. Retrying...")
                time.sleep(LOCK_RETRY_DELAY)
            
            err(f"Failed to lock {mapper_name} after multiple attempts. Ensure no rogue process holds a reference.")
            sys.exit(1)
        else:
            success("Encrypted container is already locked.")
    else:
        success(f"Simple drive '{drive.name}' disconnected cleanly.")

def set_keyring_password(drives: dict[str, Drive], target: str):
    """Securely store a LUKS password in the system keyring."""
    if target not in drives:
        err(f"Drive '{target}' not recognized in config.")
        sys.exit(1)
    
    if drives[target].type != "PROTECTED":
        err(f"Drive '{target}' is a SIMPLE drive and does not require a password.")
        sys.exit(1)

    console.print(Panel(
        f"Setting secure keyring password for drive: [bold cyan]{target}[/]\n"
        "This eliminates the need for manual entry during unlock sequences.",
        title="Keyring Setup", border_style="cyan"
    ))

    pwd = getpass.getpass(f"Enter LUKS password for '{target}': ")
    pwd_confirm = getpass.getpass("Confirm password: ")

    if pwd != pwd_confirm:
        err("Passwords do not match.")
        sys.exit(1)

    keyring.set_password(KEYRING_SERVICE, target, pwd)
    success(f"Password stored securely in the system keyring for '{target}'.")

# ------------------------------------------------------------------------------
#  MAIN ENTRY
# ------------------------------------------------------------------------------
def main():
    prevent_root_execution()

    parser = argparse.ArgumentParser(
        description="Universal Drive Manager (Platinum / TOML Native)",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    parser.add_argument("-c", "--config", type=Path, help="Path to override drives.toml")
    subparsers = parser.add_subparsers(dest="action", required=True)

    # Subcommands
    subparsers.add_parser("status", help="Show status of all configured drives")
    
    unlock_p = subparsers.add_parser("unlock", help="Unlock and mount a specified drive")
    unlock_p.add_argument("target", help="Drive name to unlock")

    lock_p = subparsers.add_parser("lock", help="Unmount and lock a specified drive")
    lock_p.add_argument("target", help="Drive name to lock")

    setpass_p = subparsers.add_parser("set-password", help="Securely store a drive's password in the system keyring")
    setpass_p.add_argument("target", help="Drive name")

    args = parser.parse_args()

    # Early dependency check before touching kernel / disk states
    check_dependencies()

    # Load Configuration from TOML
    drives = load_config(args.config)

    match args.action:
        case "status":
            show_status(drives)
            
        case "set-password":
            set_keyring_password(drives, args.target)
            
        case "unlock" | "lock":
            if args.target not in drives:
                err(f"Drive '{args.target}' not found in configuration.")
                sys.exit(1)

            acquire_lock()
            drive = drives[args.target]

            if args.action == "unlock":
                do_unlock(drive)
            else:
                do_lock(drive)

if __name__ == "__main__":
    main()
