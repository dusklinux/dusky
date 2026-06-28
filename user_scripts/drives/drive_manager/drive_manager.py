#!/usr/bin/env python3

"""
==============================================================================
 UNIVERSAL DRIVE MANAGER (PLATINUM HYBRID EDITION - ARCH OPTIMIZED)
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
  - Robust Lockfile Mechanics with User-Isolated Runtime Directing
  - Pre-emptive `sudo -v` credential priming to prevent stdin pipe collision
  - Interactive Busy Process Resolver (High-Performance Memory Parsing)
  - Triple-Tier Teardown (udisksctl -> cryptsetup -> deferred async closure)
  - Smart Password Retry Loop with Right-Aligned Memory History
  - Secure XDG_RUNTIME_DIR Session Persistence with Atomic Writes
  - Contextual Asynchronous SSD Maintenance (Orphaned fstrim Dispatcher)
  - HYBRID: Direct mapper fallback to bypass sluggish udev race conditions
  - MULTI-DRIVE: Native support for sequential multi-drive commands
  - HARDENED: Strict OS exit code propagation for safe shell chaining (&&)
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
import threading
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
    from rich.align import Align
    from rich.markup import escape
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
#  SECURITY & SYSTEM ISOLATION
# ------------------------------------------------------------------------------
def prevent_root_execution():
    """Ensures the script is run as a normal user to keep Keyring D-Bus access valid."""
    if os.geteuid() == 0:
        err("Do NOT run this script with `sudo`!")
        console.print("Running as root breaks access to your user's desktop keyring.")
        console.print("The script will securely request sudo permissions internally when needed.")
        sys.exit(1)

def get_runtime_dir() -> Path:
    """Returns a rigorously verified user-owned directory for temporary IPC and lockfiles."""
    uid = os.getuid()
    runtime_env = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    
    if runtime_env:
        path = Path(runtime_env) / "drive_manager"
    else:
        path = Path(f"/tmp/.drive_manager_{uid}")

    if not path.exists():
        try:
            path.mkdir(mode=0o700, parents=True)
        except FileExistsError:
            pass

    try:
        st = path.lstat()
    except FileNotFoundError:
        err(f"Security hazard: Directory {path} disappeared during creation.")
        sys.exit(1)

    if path.is_symlink():
        err(f"Security hazard: Directory {path} is a symlink. Possible hijack attempt.")
        sys.exit(1)

    if st.st_uid != uid or (st.st_mode & 0o077) != 0:
        err(f"Security hazard: Directory {path} is improperly permissioned or hijacked.")
        sys.exit(1)

    return path

def prime_sudo():
    """Primes the sudo credential cache cleanly before stdin operations."""
    try:
        subprocess.run(["sudo", "-v"], check=True)
    except subprocess.CalledProcessError:
        err("Sudo authentication failed. Cannot proceed.")
        sys.exit(1)

def acquire_lock():
    """Acquires a kernel-level exclusive file lock atomically within the user's isolated dir."""
    global lock_fd
    lock_path = get_runtime_dir() / "drive_manager.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        lock_fd = os.fdopen(fd, "r+")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        
        lock_fd.seek(0)
        lock_fd.truncate()
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

# ------------------------------------------------------------------------------
#  KERNEL INTERFACES
# ------------------------------------------------------------------------------
def resolve_device(uuid: str) -> Path | None:
    """Returns the fully resolved Path to a block device, resolving any symlinks."""
    if not uuid:
        return None
    dev_path = Path(f"/dev/disk/by-uuid/{uuid}")
    if dev_path.exists():
        return dev_path.resolve()
    return None

def is_device_readable(dev_path: Path) -> bool:
    """Verifies a block device is responsive by attempting to read its first block."""
    try:
        res = subprocess.run(
            ["sudo", "dd", f"if={dev_path}", "bs=4096", "count=1", "of=/dev/null"],
            capture_output=True, timeout=10
        )
        return res.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False

def wait_for_device(uuid: str, timeout: int) -> bool:
    """Waits strictly and safely for udev to populate the /dev/disk/by-uuid tree."""
    start = time.time()
    subprocess.run(["udevadm", "settle", f"--timeout={timeout}"], capture_output=True)
    
    while (time.time() - start) < timeout:
        if resolve_device(uuid):
            return True
        time.sleep(1)
        
    return resolve_device(uuid) is not None

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
            if devices and devices[0].get("fstype"):
                return devices[0].get("fstype")
        except json.JSONDecodeError:
            pass
    return None

def get_mount_info(target_dir: Path) -> dict[str, Any] | None:
    """Uses findmnt JSON output to safely detect if a directory is mounted."""
    cmd = ["findmnt", "--json", "-v", "--mountpoint", str(target_dir)]
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

def get_keyring_password_with_timeout(service: str, name: str, timeout: int = 60) -> str | None:
    """Attempts keyring lookup with a daemon thread timeout to prevent hanging on exit."""
    result = [None]
    
    def fetch():
        try:
            result[0] = keyring.get_password(service, name)
        except Exception:
            pass

    t = threading.Thread(target=fetch, daemon=True)
    t.start()
    t.join(timeout)

    if t.is_alive():
        log("Keyring lookup timed out. Falling through to manual password prompt.")
        return None

    return result[0]

def set_keyring_password_with_timeout(service: str, name: str, password: str, timeout: int = 60) -> bool:
    """Saves password to keyring with a daemon thread timeout to prevent hanging on locked keyring."""
    success_flag = [False]

    def store():
        try:
            keyring.set_password(service, name, password)
            success_flag[0] = True
        except keyring.errors.KeyringLocked:
            err("Keyring is locked. Password not saved to keyring.")
        except Exception as e:
            err(f"Unexpected keyring error: {e}")

    t = threading.Thread(target=store, daemon=True)
    t.start()
    t.join(timeout)

    if t.is_alive():
        err("Keyring is locked or unreachable. Password not saved to keyring.")
        return False

    return success_flag[0]

def run_sudo_cmd(cmd: list[str], stdin_data: str | None = None) -> bool:
    """Helper to run a sudo command securely. Dynamically applies capture_output to prevent hanging on sudo prompts."""
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
    """Finds processes keeping the drive busy parsing lsof directly (sudo bypasses hidepid natively)."""
    res = subprocess.run(["sudo", "lsof", "-F", "pcu", "+f", "--", str(mountpoint)], capture_output=True, text=True)
    if res.returncode != 0 or not res.stdout.strip():
        return False

    processes = []
    current_p = {}
    for line in res.stdout.strip().split("\n"):
        if not line:
            continue
        prefix = line[0]
        val = line[1:]
        if prefix == 'p':
            if current_p and 'pid' in current_p:
                processes.append(current_p)
            current_p = {'pid': val, 'cmd': 'Unknown', 'user': 'Unknown'}
        elif prefix == 'c' and current_p:
            current_p['cmd'] = val
        elif prefix == 'u' and current_p:
            current_p['user'] = val

    if current_p and 'pid' in current_p:
        processes.append(current_p)

    unique_processes = []
    seen_pids = set()
    for p in processes:
        if p['pid'] not in seen_pids:
            seen_pids.add(p['pid'])
            unique_processes.append(p)

    processes = unique_processes

    if not processes:
        return False

    console.print(Panel(
        "[bold red]⚠️  WARNING: FILESYSTEM IS BUSY ⚠️[/]\n\n"
        f"The following processes are currently locking [bold white]{mountpoint}[/]\n"
        "Attempting a graceful termination allows applications to save their data.",
        title="Filesystem Locked", border_style="red"
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
            f"Attempt graceful termination of [bold cyan]{escape(p['cmd'])}[/] (PID: [bold yellow]{p['pid']}[/])?", 
            choices=["y", "n"], 
            default="n"
        )
        if ans == "y":
            log(f"Sending SIGTERM (15) to {escape(p['cmd'])} (PID: {p['pid']})...")
            term_res = subprocess.run(["sudo", "kill", "-15", p['pid']], capture_output=True, text=True)
            
            if term_res.returncode == 0:
                time.sleep(2)
                if not is_process_alive(p['pid']):
                    success(f"Successfully terminated {escape(p['cmd'])} gracefully.")
                    action_taken = True
                else:
                    err(f"Process {p['pid']} refused to close gracefully. Engaging SIGKILL (9)...")
                    kill_res = subprocess.run(["sudo", "kill", "-9", p['pid']], capture_output=True, text=True)
                    if kill_res.returncode == 0:
                        success(f"Forcefully killed {escape(p['cmd'])} (PID: {p['pid']}).")
                        action_taken = True
                    else:
                        err(f"Failed to force kill PID {p['pid']}: {kill_res.stderr.strip()}")
            else:
                err(f"Failed to send SIGTERM to PID {p['pid']}: {term_res.stderr.strip()}")
    
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
#  PERSISTENT FAILED PASSWORD STORAGE
# ------------------------------------------------------------------------------
def get_temp_attempts_path(drive_name: str) -> Path:
    return get_runtime_dir() / f"attempts_{drive_name}.json"

def load_temp_attempts(drive_name: str) -> list[str]:
    path = get_temp_attempts_path(drive_name)
    if not path.exists():
        return []
    try:
        stat_info = path.stat()
        if stat_info.st_uid != os.getuid() or (stat_info.st_mode & 0o077) != 0:
            path.unlink(missing_ok=True)
            return []
            
        with open(path, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def save_temp_attempts(drive_name: str, attempts: list[str]):
    path = get_temp_attempts_path(drive_name)
    if len(attempts) > 50:
        attempts = attempts[-50:]
        
    temp_path = path.with_suffix(".tmp")
    try:
        fd = os.open(temp_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(attempts, f)
        temp_path.rename(path)
    except Exception:
        pass

def clear_temp_attempts(drive_name: str):
    path = get_temp_attempts_path(drive_name)
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass

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
        config_env = os.environ.get("XDG_CONFIG_HOME", "").strip()
        xdg_config = Path(config_env) if config_env else Path.home() / ".config"
        
        config_paths = [
            xdg_config / "drive_manager" / "drives.toml",
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

        fstype_str = get_fstype(target_uuid) or drive.fstype or "Unknown"

        if mount_info:
            source_str = mount_info.get("source")
            if source_str:
                actual_source = Path(source_str).resolve()
                expected_dev = resolve_device(target_uuid)
                
                # Check 1: Normal udev symlink match
                if expected_dev and expected_dev == actual_source:
                    is_mounted = True
                
                # Check 2: Direct fallback mapper match (Fixes Fallback Desync Regression)
                if not is_mounted and drive.type == "PROTECTED":
                    existing_mapper = get_crypt_mapper_name(drive.outer_uuid)
                    m_name = existing_mapper if existing_mapper else f"luks-{drive.outer_uuid}"
                    fallback_mapper = Path(f"/dev/mapper/{m_name}")
                    if fallback_mapper.exists() and fallback_mapper.resolve() == actual_source:
                        is_mounted = True

                # Check 3: Raw UUID inclusion
                if not is_mounted and target_uuid and target_uuid.lower() in source_str.lower():
                     is_mounted = True

        if is_mounted:
            table.add_row(f"[bold green]●[/] {name}", drive.type, fstype_str, "[bold green]Mounted[/]", str(drive.mountpoint))
        else:
            table.add_row(f"[bold red]○[/] {name}", drive.type, fstype_str, "[bold red]Unmounted[/]", str(drive.mountpoint))

    console.print()
    console.print(table)
    console.print()

def do_unlock(drive: Drive) -> bool:
    prime_sudo()
    log(f"Starting unlock sequence for '{drive.name}'...")

    target_uuid = drive.inner_uuid if drive.type == "PROTECTED" else drive.outer_uuid
    mount_info = get_mount_info(drive.mountpoint)
    mapper_name = None

    if mount_info:
        source_str = mount_info.get("source", "")
        actual_source = Path(source_str).resolve() if source_str else Path()
        expected_dev = resolve_device(target_uuid)
        
        is_mounted = False
        
        # Check 1: Normal udev symlink match
        if expected_dev and expected_dev == actual_source:
            is_mounted = True
            
        # Check 2: Direct fallback mapper match (Fixes Fallback Desync Regression)
        if not is_mounted and drive.type == "PROTECTED":
            existing_mapper = get_crypt_mapper_name(drive.outer_uuid)
            m_name = existing_mapper if existing_mapper else f"luks-{drive.outer_uuid}"
            fallback_mapper = Path(f"/dev/mapper/{m_name}")
            if fallback_mapper.exists() and fallback_mapper.resolve() == actual_source:
                is_mounted = True

        # Check 3: Raw UUID inclusion
        if not is_mounted and target_uuid and target_uuid.lower() in source_str.lower():
            is_mounted = True

        if is_mounted:
            success(f"'{drive.name}' is already successfully mounted at {drive.mountpoint}")
            return True
        else:
            err(f"Mountpoint {drive.mountpoint} is occupied by another device: {actual_source}")
            return False

    if drive.type == "PROTECTED":
        if not resolve_device(drive.outer_uuid):
            err(f"Physical drive not found (Outer UUID: {drive.outer_uuid}). Is it plugged in?")
            return False

        existing_mapper = get_crypt_mapper_name(drive.outer_uuid)
        mapper_name = existing_mapper if existing_mapper else f"luks-{drive.outer_uuid}"
        mapper_path = Path(f"/dev/mapper/{mapper_name}")
        
        inner_dev = resolve_device(drive.inner_uuid)
        
        # --- BULLETPROOF DEVICE CHECK ---
        # Checks both the direct mapper AND the udev symlink to prevent "Device already exists" 
        container_unlocked = False
        if mapper_path.exists() and is_device_readable(mapper_path):
            container_unlocked = True
        elif inner_dev and is_device_readable(inner_dev):
            container_unlocked = True

        if container_unlocked:
            log("Crypt container is already unlocked.")
        else:
            # If the node exists in any form but isn't readable, it's stale
            if mapper_path.exists() or inner_dev:
                err("Crypt device is unresponsive. Closing stale mapping...")
                if not run_sudo_cmd(["sudo", "cryptsetup", "close", mapper_name]):
                    err(f"Failed to close stale mapping for {mapper_name}. Manual intervention required.")
                    return False
                time.sleep(1)

            log("Unlocking encrypted container...")
            outer_dev_path = f"/dev/disk/by-uuid/{drive.outer_uuid}"
            
            # --- DYNAMIC CRYPTO PROBER ---
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

            base_cmd = ["sudo", "cryptsetup", "open", "--allow-discards"] + crypto_type_args + [outer_dev_path, mapper_name]
            pwd = get_keyring_password_with_timeout(KEYRING_SERVICE, drive.name, timeout=60)
            
            if pwd:
                log("Password found in secure keyring. Supplying to cryptsetup...")
                cmd = base_cmd + ["--tries", "1", "--key-file", "-"]
                if not run_sudo_cmd(cmd, stdin_data=pwd):
                    err("Decryption failed. Keyring password might be incorrect.")
                    return False
            else:
                log("No password in keyring. Falling back to manual terminal prompt.")
                if drive.hint:
                    hint_msg(drive.hint)
                
                tried_passwords = load_temp_attempts(drive.name)
                
                while True:
                    if tried_passwords:
                        max_display = 6
                        display_items = tried_passwords[-max_display:]
                        hidden_count = len(tried_passwords) - len(display_items)
                        
                        panel_lines = []
                        if hidden_count > 0:
                            panel_lines.append(f"[dim]... {hidden_count} older attempt{'s' if hidden_count > 1 else ''} hidden ...[/]")
                        
                        panel_lines.extend(f"[red]✗[/] {escape(p)}" for p in display_items)
                        
                        hist_panel = Panel(
                            "\n".join(panel_lines),
                            title="[yellow]Previously Tried[/]",
                            border_style="yellow",
                            expand=False
                        )
                        console.print(Align.right(hist_panel))
                        
                    try:
                        pwd_attempt = Prompt.ask(
                            f"Enter passphrase for /dev/disk/by-uuid/[bold cyan]{drive.outer_uuid}[/]", 
                            password=True
                        )
                    except (KeyboardInterrupt, EOFError):
                        console.print()
                        err("Cancelled by user.")
                        sys.exit(130)
                        
                    if not pwd_attempt:
                        continue
                        
                    # Fix: Use rstrip('\r\n') instead of strip() to preserve intentional spaces in passphrases
                    pwd_attempt = pwd_attempt.rstrip('\r\n')
                        
                    try:
                        subprocess.run(["sudo", "-n", "-v"], check=True, capture_output=True)
                    except subprocess.CalledProcessError:
                        log("Sudo credential expired during prompt. Refreshing...")
                        prime_sudo()
                        
                    cmd = base_cmd + ["--tries", "1", "--key-file", "-"]
                    
                    if run_sudo_cmd(cmd, stdin_data=pwd_attempt):
                        clear_temp_attempts(drive.name)
                        if set_keyring_password_with_timeout(KEYRING_SERVICE, drive.name, pwd_attempt):
                            success("Password saved to keyring for future use.")
                        break
                    else:
                        err("Decryption failed. Please try again.")
                        if pwd_attempt not in tried_passwords:
                            tried_passwords.append(pwd_attempt)
                            save_temp_attempts(drive.name, tried_passwords)

            log("Waiting for filesystem block device to populate...")
            if not wait_for_device(drive.inner_uuid, FILESYSTEM_TIMEOUT):
                # Ensure mapper_path actually exists before falling back
                if mapper_path.exists():
                    hint_msg("Inner filesystem UUID symlink not created by udev. Proceeding with direct mapper path...")
                else:
                    err("Timeout waiting for inner filesystem to appear.")
                    return False

    log(f"Mounting to {drive.mountpoint}...")
    
    detected_fstype = get_fstype(target_uuid)
    
    mount_source = f"UUID={target_uuid}"
    if drive.type == "PROTECTED" and not resolve_device(target_uuid):
        if mapper_name:
            fallback_mapper = Path(f"/dev/mapper/{mapper_name}")
            if fallback_mapper.exists():
                mount_source = str(fallback_mapper)

    fstype_to_check = (drive.fstype or detected_fstype or "").lower()

    mount_args = ["--mkdir"]
    
    # Under kernel 7.1+, we force the use of the new kernel 'ntfs' driver by bypassing
    # mount helpers (-i) and explicitly passing the filesystem type 'ntfs' if it is NTFS.
    if "ntfs" in fstype_to_check:
        mount_args.extend(["-i", "-t", "ntfs"])
    elif drive.fstype:
        mount_args.extend(["-t", drive.fstype])
        
    options = []
    if drive.mount_options:
        options.extend(drive.mount_options)
    else:
        if fstype_to_check in ["ntfs", "vfat", "fat32", "exfat", "msdos"]:
            uid = os.getuid()
            gid = os.getgid()
            options.append(f"uid={uid},gid={gid},dmask=022,fmask=133")
            log(f"Auto-configured kernel permissions for non-POSIX filesystem ({fstype_to_check.upper()}).")

    if options:
        mount_args.extend(["-o", ",".join(options)])
    
    cmd = [
        "sudo", "mount", 
        *mount_args,
        "--source", mount_source, 
        "--target", str(drive.mountpoint)
    ]
    
    if run_sudo_cmd(cmd):
        success(f"'{drive.name}' successfully mounted.")
        
        if fstype_to_check not in ["btrfs", "zfs"]:
            log("Dispatching asynchronous background TRIM operation to SSD firmware...")
            subprocess.Popen(
                ["sudo", "fstrim", str(drive.mountpoint)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True
            )
        return True
    else:
        err(f"Failed to mount {mount_source} to {drive.mountpoint}.")
        return False

def do_lock(drive: Drive) -> bool:
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
                    log("No userspace processes found. Waiting for kernel locks to clear...")
                    time.sleep(1)
                    continue
        
        if unmounted:
            log("Unmount successful.")
        else:
            err(f"Failed to unmount {drive.mountpoint}. A process is still locking the filesystem.")
            return False
    else:
        log(f"{drive.mountpoint} is already unmounted.")

    if drive.type == "PROTECTED":
        mapper_name = None
        physical_present = resolve_device(drive.outer_uuid)
        
        if physical_present:
            mapper_name = get_crypt_mapper_name(drive.outer_uuid)
            if not mapper_name:
                deterministic_name = f"luks-{drive.outer_uuid}"
                if Path(f"/dev/mapper/{deterministic_name}").exists():
                    mapper_name = deterministic_name
        else:
            deterministic_name = f"luks-{drive.outer_uuid}"
            if Path(f"/dev/mapper/{deterministic_name}").exists():
                hint_msg("Physical drive missing, but ghost mapper detected. Forcing cleanup.")
                mapper_name = deterministic_name
            elif resolve_device(drive.inner_uuid):
                err("Device is active under an unknown mapper name and physical drive is missing. Cannot securely lock.")
                return False
            else:
                success("Device removed physically, container is no longer active.")
                return True
        
        if mapper_name:
            time.sleep(1)
            subprocess.run(["udevadm", "settle", "--timeout=5"], capture_output=True)
            subprocess.run(["sudo", "blockdev", "--flushbufs", f"/dev/mapper/{mapper_name}"], capture_output=True)

            log(f"Locking crypt node: {mapper_name}...")
            
            cleartext_dev = f"/dev/mapper/{mapper_name}"
            if shutil.which("udisksctl") and Path(cleartext_dev).exists():
                res = subprocess.run(["udisksctl", "lock", "-b", cleartext_dev], capture_output=True, text=True)
                if res.returncode == 0:
                    success("Encrypted container successfully locked via udisks2 API.")
                    return True
            
            for attempt in range(LOCK_MAX_RETRIES):
                if run_sudo_cmd(["sudo", "cryptsetup", "close", mapper_name]):
                    success("Encrypted container successfully locked.")
                    return True
                log(f"Lock attempt {attempt+1}/{LOCK_MAX_RETRIES} failed. Retrying...")
                time.sleep(LOCK_RETRY_DELAY)
            
            log("Device is held by a kernel subsystem. Engaging deferred asynchronous lock...")
            if run_sudo_cmd(["sudo", "cryptsetup", "close", "--deferred", mapper_name]):
                success("Device marked for deferred closure (will lock automatically when kernel I/O finishes).")
                return True

            err(f"Failed to lock {mapper_name} after all strategies exhausted.")
            run_cryptsetup_forensics(mapper_name)
            return False
        else:
            success("Encrypted container is already locked.")
            return True
    else:
        success(f"Simple drive '{drive.name}' disconnected cleanly.")
        return True

def set_keyring_password(drives: dict[str, Drive], target: str) -> bool:
    if target not in drives:
        err(f"Drive '{target}' not recognized in config.")
        return False
    
    if drives[target].type != "PROTECTED":
        err(f"Drive '{target}' is a SIMPLE drive and does not require a password.")
        return False

    console.print(Panel(
        f"Setting secure keyring password for drive: [bold cyan]{escape(target)}[/]\n"
        "This eliminates the need for manual entry during unlock sequences.",
        title="Keyring Setup", border_style="cyan"
    ))

    try:
        pwd = getpass.getpass(f"Enter LUKS/BitLocker password for '{target}': ")
        pwd_confirm = getpass.getpass("Confirm password: ")
    except (KeyboardInterrupt, EOFError):
        console.print()
        err("Cancelled by user.")
        sys.exit(130)

    if pwd != pwd_confirm:
        err("Passwords do not match.")
        return False

    if set_keyring_password_with_timeout(KEYRING_SERVICE, target, pwd):
        success(f"Password stored securely in the system keyring for '{target}'.")
        clear_temp_attempts(target)
        return True
        
    return False

# ------------------------------------------------------------------------------
#  MAIN ENTRY
# ------------------------------------------------------------------------------
def main():
    prevent_root_execution()

    parser = argparse.ArgumentParser(
        description="Universal Drive Manager (Platinum Hybrid Edition / Multi-Drive Enabled)",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    parser.add_argument("-c", "--config", type=Path, help="Path to override drives.toml")
    subparsers = parser.add_subparsers(dest="action", required=True)

    subparsers.add_parser("status", help="Show status of all configured drives")
    
    unlock_p = subparsers.add_parser("unlock", help="Unlock and mount specified drive(s)")
    unlock_p.add_argument("targets", nargs="+", help="Drive name(s) to unlock (e.g., 'slow fast')")

    lock_p = subparsers.add_parser("lock", help="Unmount and lock specified drive(s)")
    lock_p.add_argument("targets", nargs="+", help="Drive name(s) to lock")

    setpass_p = subparsers.add_parser("set-password", help="Securely store a drive's password in the system keyring")
    setpass_p.add_argument("targets", nargs="+", help="Drive name(s)")

    args = parser.parse_args()

    check_dependencies()
    drives = load_config(args.config)

    match args.action:
        case "status":
            show_status(drives)
            
        case "set-password":
            overall_success = True
            for idx, target in enumerate(args.targets):
                if idx > 0:
                    console.print("\n[dim]" + "-" * 60 + "[/dim]\n")
                if not set_keyring_password(drives, target):
                    overall_success = False
                    if idx < len(args.targets) - 1:
                        hint_msg(f"Setup for '{target}' failed. Moving to next drive...")
                    else:
                        err(f"Setup for '{target}' failed.")
            
            # Regression Fix: Hard exit to OS on failure
            if not overall_success:
                sys.exit(1)
            
        case "unlock" | "lock":
            # First pass: Validate all requested targets exist in config to prevent partial executions
            for target in args.targets:
                if target not in drives:
                    err(f"Drive '{target}' not found in configuration.")
                    sys.exit(1)

            acquire_lock()
            
            overall_success = True
            
            # Second pass: Process them sequentially and robustly catch failures
            for idx, target in enumerate(args.targets):
                if idx > 0:
                    console.print("\n[dim]" + "-" * 60 + "[/dim]\n")
                
                drive = drives[target]
                if args.action == "unlock":
                    success_flag = do_unlock(drive)
                else:
                    success_flag = do_lock(drive)
                    
                if not success_flag:
                    overall_success = False
                    if idx < len(args.targets) - 1:
                        hint_msg(f"Operation on '{target}' failed. Moving to next drive...")
                    else:
                        err(f"Operation on '{target}' failed.")
            
            # Regression Fix: Hard exit to OS on failure to protect shell chaining
            if not overall_success:
                sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold red]\\[ERROR][/] Interrupted by user.")
        sys.exit(130)
