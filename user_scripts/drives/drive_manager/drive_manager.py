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
  - Dynamic LUKS/BitLocker auto-detection via `lsblk` probing
  - Intelligent NTFS/FAT32 Auto-Permission Configurator (uid/gid injection)
  - Zero-dependency TOML parsing (Python 3.11+ tomllib)
  - Arch Linux Auto-Bootstrapper for required UI/Sec dependencies
  - Kernel-level findmnt --evaluate tag resolution
  - Pre-emptive `sudo -v` credential priming to prevent stdin pipe collision
  - Interactive Busy Process Resolver (Intelligent PID Tracking + Forensics)
  - Triple-Tier Teardown (udisksctl -> cryptsetup -> deferred async closure)
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
    from rich.prompt import Prompt
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
    fstype: str | None = None
    mount_options: list[str] | None = None

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
    """Primes the sudo credential cache cleanly before stdin operations."""
    try:
        subprocess.run(["sudo", "-v"], check=True)
    except subprocess.CalledProcessError:
        err("Sudo authentication failed. Cannot proceed.")
        sys.exit(1)

def acquire_lock():
    """Acquires a kernel-level exclusive file lock."""
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
    deps = ["mount", "umount", "findmnt", "lsblk", "udevadm", "sudo", "cryptsetup", "lsof", "blockdev"]
    missing = [cmd for cmd in deps if shutil.which(cmd) is None]
    if missing:
        err(f"Missing required commands: {', '.join(missing)}")
        sys.exit(1)

def resolve_device(uuid: str) -> Path | None:
    """Returns the fully resolved Path to a block device, resolving any symlinks."""
    if not uuid:
        return None
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

def get_fstype(uuid: str) -> str | None:
    """Uses lsblk to dynamically probe the filesystem or crypto type of a UUID."""
    if not resolve_device(uuid):
        return None
    cmd = ["lsblk", f"/dev/disk/by-uuid/{uuid}", "--json", "-o", "FSTYPE"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        try:
            data = json.loads(res.stdout)
            devices = data.get("blockdevices", [])
            if devices:
                return devices[0].get("fstype")
        except json.JSONDecodeError:
            pass
    return None

def get_mount_info(target_dir: Path) -> dict[str, Any] | None:
    """Uses findmnt JSON output to safely detect if a directory is mounted."""
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
    """Uses lsblk to find the /dev/mapper/ NAME attached to the physical encrypted drive."""
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
            res = subprocess.run(cmd)
            return res.returncode == 0
    except Exception as e:
        err(f"Command execution failed: {e}")
        return False

def is_process_alive(pid: str) -> bool:
    """Checks if a process is still alive by sending signal 0 via the kernel."""
    try:
        res = subprocess.run(["sudo", "kill", "-0", pid], capture_output=True, text=True)
        return res.returncode == 0
    except Exception:
        return False

def resolve_busy_processes(mountpoint: Path) -> bool:
    """Finds processes keeping the drive busy and offers an interactive kill menu."""
    res = subprocess.run(["sudo", "lsof", "+f", "--", str(mountpoint)], capture_output=True, text=True)
    if res.returncode != 0 or not res.stdout.strip():
        return False

    lines = res.stdout.strip().split("\n")
    if len(lines) <= 1:
        return False

    processes = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 3:
            pid = parts[1]
            if not any(p["pid"] == pid for p in processes):
                processes.append({
                    "cmd": parts[0],
                    "pid": pid,
                    "user": parts[2]
                })

    if not processes:
        return False

    console.print(Panel(
        "[bold red]⚠️  WARNING: DATA CORRUPTION RISK ⚠️[/]\n\n"
        f"The following processes are currently locking [bold white]{mountpoint}[/]\n"
        "Force-killing them may result in unsaved work being lost or file corruption.",
        title="Filesystem Busy", border_style="red"
    ))

    table = Table(show_header=True, header_style="bold yellow", border_style="yellow")
    table.add_column("COMMAND", style="cyan")
    table.add_column("PID", justify="right", style="yellow")
    table.add_column("USER")

    for p in processes:
        table.add_row(p["cmd"], p["pid"], p["user"])

    console.print(table)
    console.print()

    action_taken = False
    for p in processes:
        if not is_process_alive(p["pid"]):
            console.print(f"[bold cyan][INFO][/] {p['cmd']} (PID: {p['pid']}) has already exited gracefully.")
            continue

        ans = Prompt.ask(
            f"Force kill [bold cyan]{p['cmd']}[/] (PID: [bold yellow]{p['pid']}[/])?", 
            choices=["y", "n"], 
            default="n"
        )
        if ans == "y":
            kill_res = subprocess.run(["sudo", "kill", "-9", p['pid']], capture_output=True, text=True)
            if kill_res.returncode == 0:
                success(f"Successfully killed {p['cmd']} (PID: {p['pid']}).")
                action_taken = True
            else:
                stderr_msg = kill_res.stderr.strip()
                err(f"Failed to kill PID {p['pid']}: {stderr_msg}")
    
    return action_taken

def run_cryptsetup_forensics(mapper_name: str):
    """Diagnoses exactly what is preventing a cryptsetup closure."""
    target = f"/dev/mapper/{mapper_name}"
    log(f"Running forensic block-device scan on {target}...")
    
    res = subprocess.run(["sudo", "lsof", target], capture_output=True, text=True)
    if res.stdout.strip():
        console.print(Panel(
            res.stdout.strip(), 
            title="Processes locking the underlying crypt node", 
            border_style="red"
        ))
    else:
        hint_msg("No userspace applications are holding the node. It is likely locked by a kernel subsystem (e.g., LVM, Btrfs async flusher) or udev daemon probing.")
        hint_msg(f"To lock it asynchronously once the kernel is finished, run: `sudo cryptsetup close --deferred {mapper_name}`")

# ------------------------------------------------------------------------------
#  CONFIG PARSING
# ------------------------------------------------------------------------------
def load_config(override_path: Path | None = None) -> dict[str, Drive]:
    """Loads and validates drives.toml into native dataclasses."""
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
                hint=data.get("hint"),
                fstype=data.get("fstype"),
                mount_options=data.get("mount_options")
            )
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
    table = Table(show_header=True, header_style="bold white", border_style="bright_black")
    table.add_column("DRIVE", width=14)
    table.add_column("TYPE", width=10)
    table.add_column("FS", width=10)
    table.add_column("STATUS", width=12)
    table.add_column("MOUNTPOINT")

    for name, drive in sorted(drives.items()):
        target_uuid = drive.inner_uuid if drive.type == "PROTECTED" else drive.outer_uuid
        mount_info = get_mount_info(drive.mountpoint)
        is_mounted = False

        # Attempt to identify FS type gracefully if locked
        fstype_str = get_fstype(target_uuid) or drive.fstype or "Unknown"

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
            table.add_row(f"[bold green]●[/] {name}", drive.type, fstype_str, "[bold green]Mounted[/]", str(drive.mountpoint))
        else:
            table.add_row(f"[bold red]○[/] {name}", drive.type, fstype_str, "[bold red]Unmounted[/]", str(drive.mountpoint))

    console.print()
    console.print(table)
    console.print()

def do_unlock(drive: Drive):
    prime_sudo()
    log(f"Starting unlock sequence for '{drive.name}'...")

    target_uuid = drive.inner_uuid if drive.type == "PROTECTED" else drive.outer_uuid
    mount_info = get_mount_info(drive.mountpoint)

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

    if drive.type == "PROTECTED":
        if not resolve_device(drive.outer_uuid):
            err(f"Physical drive not found (Outer UUID: {drive.outer_uuid}). Is it plugged in?")
            sys.exit(1)

        if resolve_device(drive.inner_uuid):
            log("Crypt container is already unlocked.")
        else:
            log("Unlocking encrypted container...")
            mapper_name = f"luks-{drive.outer_uuid}"
            outer_dev_path = f"/dev/disk/by-uuid/{drive.outer_uuid}"
            
            # --- DYNAMIC CRYPTO PROBER ---
            # cryptsetup natively auto-detects LUKS, but fails blindly on BitLocker without --type bitlk.
            outer_fstype = get_fstype(drive.outer_uuid)
            crypto_type_args = []
            
            if outer_fstype:
                fstype_lower = outer_fstype.lower()
                if "bitlocker" in fstype_lower:
                    log("Auto-detected BitLocker encryption. Adjusting kernel parameters...")
                    crypto_type_args = ["--type", "bitlk"]
                elif "luks" in fstype_lower:
                    log("Auto-detected LUKS encryption.")
                    crypto_type_args = ["--type", "luks"]
            else:
                log("Could not auto-detect encryption type. Relying on cryptsetup defaults.")

            base_cmd = ["sudo", "cryptsetup", "open"] + crypto_type_args + [outer_dev_path, mapper_name]
            pwd = keyring.get_password(KEYRING_SERVICE, drive.name)
            
            if pwd:
                log("Password found in secure keyring. Supplying to cryptsetup...")
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

    log(f"Mounting to {drive.mountpoint}...")
    
    # --- DYNAMIC FILESYSTEM PROBER & INJECTOR ---
    detected_fstype = get_fstype(target_uuid)
    
    mount_args = ["--mkdir"]
    
    # 1. Obey strict user override if provided in TOML
    if drive.fstype:
        mount_args.extend(["-t", drive.fstype])
        
    # 2. Compile mount options (Auto-inject NTFS/FAT permissions to avoid read-only root locking)
    options = []
    if drive.mount_options:
        options.extend(drive.mount_options)
    else:
        # Check either the explicitly set TOML fstype or the dynamically probed one.
        fstype_to_check = (drive.fstype or detected_fstype or "").lower()
        if fstype_to_check in ["ntfs", "ntfs3", "vfat", "fat32", "exfat", "msdos"]:
            uid = os.getuid()
            gid = os.getgid()
            options.append(f"uid={uid},gid={gid},dmask=022,fmask=133")
            log(f"Auto-configured kernel permissions for non-POSIX filesystem ({fstype_to_check.upper()}).")

    if options:
        mount_args.extend(["-o", ",".join(options)])
    
    cmd = [
        "sudo", "mount", 
        *mount_args,
        "--source", f"UUID={target_uuid}", 
        "--target", str(drive.mountpoint)
    ]
    
    if run_sudo_cmd(cmd):
        success(f"'{drive.name}' successfully mounted.")
    else:
        err(f"Failed to mount UUID={target_uuid} to {drive.mountpoint}.")
        sys.exit(1)

def do_lock(drive: Drive):
    prime_sudo()
    log(f"Starting lock sequence for '{drive.name}'...")

    mount_info = get_mount_info(drive.mountpoint)

    if mount_info:
        log(f"Unmounting {drive.mountpoint}...")
        unmounted = False
        
        for attempt in range(5):
            if run_sudo_cmd(["sudo", "umount", str(drive.mountpoint)]):
                unmounted = True
                break
            else:
                log("Filesystem is busy. Scanning for locking processes...")
                if resolve_busy_processes(drive.mountpoint):
                    log("Retrying unmount sequence...")
                    time.sleep(1)
                else:
                    break 
        
        if unmounted:
            log("Unmount successful.")
        else:
            err(f"Failed to unmount {drive.mountpoint}. A process is still locking the filesystem.")
            sys.exit(1)
    else:
        log(f"{drive.mountpoint} is already unmounted.")

    if drive.type == "PROTECTED":
        mapper_name = None
        physical_present = resolve_device(drive.outer_uuid)
        
        if physical_present:
            mapper_name = get_crypt_mapper_name(drive.outer_uuid)
        else:
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
            subprocess.run(["sudo", "blockdev", "--flushbufs", f"/dev/mapper/{mapper_name}"], capture_output=True)

            log(f"Locking crypt node: {mapper_name}...")
            
            # --- STRATEGY 1: Interoperable DBus Lock ---
            outer_dev = f"/dev/disk/by-uuid/{drive.outer_uuid}"
            if shutil.which("udisksctl") and Path(outer_dev).exists():
                res = subprocess.run(["udisksctl", "lock", "-b", outer_dev], capture_output=True, text=True)
                if res.returncode == 0:
                    success("Encrypted container successfully locked via udisks2 API.")
                    return
            
            # --- STRATEGY 2: Standard Cryptsetup Close ---
            for attempt in range(LOCK_MAX_RETRIES):
                if run_sudo_cmd(["sudo", "cryptsetup", "close", mapper_name]):
                    success("Encrypted container successfully locked.")
                    return
                log(f"Lock attempt {attempt+1}/{LOCK_MAX_RETRIES} failed. Retrying...")
                time.sleep(LOCK_RETRY_DELAY)
            
            # --- STRATEGY 3: Deferred Kernel Teardown ---
            log("Device is held by a kernel subsystem. Engaging deferred asynchronous lock...")
            if run_sudo_cmd(["sudo", "cryptsetup", "close", "--deferred", mapper_name]):
                success("Device marked for deferred closure (will lock automatically when kernel I/O finishes).")
                return

            err(f"Failed to lock {mapper_name} after all strategies exhausted.")
            run_cryptsetup_forensics(mapper_name)
            sys.exit(1)
        else:
            success("Encrypted container is already locked.")
    else:
        success(f"Simple drive '{drive.name}' disconnected cleanly.")

def set_keyring_password(drives: dict[str, Drive], target: str):
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

    pwd = getpass.getpass(f"Enter LUKS/BitLocker password for '{target}': ")
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

    subparsers.add_parser("status", help="Show status of all configured drives")
    
    unlock_p = subparsers.add_parser("unlock", help="Unlock and mount a specified drive")
    unlock_p.add_argument("target", help="Drive name to unlock")

    lock_p = subparsers.add_parser("lock", help="Unmount and lock a specified drive")
    lock_p.add_argument("target", help="Drive name to lock")

    setpass_p = subparsers.add_parser("set-password", help="Securely store a drive's password in the system keyring")
    setpass_p.add_argument("target", help="Drive name")

    args = parser.parse_args()

    check_dependencies()
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
