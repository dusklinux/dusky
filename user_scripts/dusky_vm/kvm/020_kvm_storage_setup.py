#!/usr/bin/env python3
# ==============================================================================
# 02_storage_setup.py
# Purpose: Interactive VM storage provisioner. Serializes state to JSON and 
#          enforces strict directory traversal ACLs for the QEMU daemon.
# Target: Python 3.14+ 
# ==============================================================================
import os
import sys
import json
import subprocess
import threading
import time
import readline
import glob
from pathlib import Path

CYAN, GREEN, YELLOW, RED, NC = '\033[1;36m', '\033[1;32m', '\033[1;33m', '\033[1;31m', '\033[0m'

def log_info(msg: str) -> None: print(f"{CYAN}[INFO]{NC} {msg}")
def log_warn(msg: str) -> None: print(f"{YELLOW}[WARN]{NC} {msg}")
def log_success(msg: str) -> None: print(f"{GREEN}[SUCCESS]{NC} {msg}")
def log_error(msg: str) -> None:
    print(f"{RED}[ERROR]{NC} {msg}")
    sys.exit(1)

def sudo_keep_alive() -> None:
    """Daemon thread to refresh sudo credentials invisibly."""
    while True:
        subprocess.run(["sudo", "-n", "true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        time.sleep(60)

def validate_environment() -> None:
    if os.geteuid() != 0:
        log_error("Root privileges required. Execute via sudo.")
    threading.Thread(target=sudo_keep_alive, daemon=True).start()

def setup_path_autocomplete() -> None:
    """Natively binds the 'readline' library to emulate Bash's 'read -e' path autocompletion."""
    def path_completer(text: str, state: int) -> str | None:
        expanded_text = os.path.expanduser(text)
        matches = glob.glob(expanded_text + '*')
        
        # Append a trailing slash to directories so the user can continue tab-completing naturally
        formatted_matches = [m + '/' if os.path.isdir(m) and not m.endswith('/') else m for m in matches]
        return (formatted_matches + [None])[state]

    # Prevent readline from breaking path strings at slashes or hyphens
    readline.set_completer_delims(' \t\n;')
    readline.parse_and_bind("tab: complete")
    readline.set_completer(path_completer)

def get_target_directory() -> Path:
    default_path = Path("/var/lib/libvirt/images")
    
    print(f"{CYAN}===================================================={NC}")
    print(f"{CYAN}       Virtual Machine Storage Configuration        {NC}")
    print(f"{CYAN}===================================================={NC}")
    print(f"  [1] Persistent Storage (Default: {default_path})")
    print("  [2] Ephemeral / RAM Disk (e.g., /mnt/zram1)")
    print("  [3] Custom Path\n")
    
    choice = input("Select an option [1-3] (Default: 1): ").strip() or "1"
    
    match choice:
        case "1":
            return default_path
        case "2":
            ephemeral = input("Enter ephemeral drive path (Default: /mnt/zram1): ").strip() or "/mnt/zram1"
            return Path(ephemeral)
        case "3":
            custom = input("Enter absolute custom directory path: ").strip()
            path = Path(custom)
            if not path.is_absolute():
                log_error("Path must be absolute (starting with '/').")
            return path
        case _:
            log_error("Invalid selection. Aborting.")

def apply_acls(target_dir: Path, default_dir: Path) -> None:
    if not target_dir.exists():
        log_warn(f"Directory {target_dir} missing. Provisioning...")
        target_dir.mkdir(parents=True, exist_ok=True)
    
    if target_dir != default_dir:
        log_info(f"Applying QEMU traversal ACLs to {target_dir}...")
        
        # Walk up the tree and ensure QEMU has execute rights, strictly ignoring the root directory
        for parent in target_dir.parents:
            if str(parent) != "/":
                subprocess.run(["setfacl", "-m", "u:qemu:x", str(parent)], check=False, stderr=subprocess.DEVNULL)
            
        # Grant full R/W/X to the target directory and set defaults for new files
        subprocess.run(["setfacl", "-m", "u:qemu:rwx", str(target_dir)], check=True)
        subprocess.run(["setfacl", "-d", "-m", "u:qemu:rwx", str(target_dir)], check=True)
        log_success("ACLs enforced.")

def serialize_state(target_dir: Path) -> None:
    state_file = Path("/tmp/kvm_storage_state.json")
    state_data = {"KVM_TARGET_DIR": str(target_dir)}
    
    with state_file.open('w', encoding='utf-8') as f:
        json.dump(state_data, f, indent=4)
        
    state_file.chmod(0o666)
    log_info(f"Storage state safely serialized to {state_file}")

def main() -> None:
    validate_environment()
    setup_path_autocomplete()
    
    target_dir = get_target_directory()
    default_dir = Path("/var/lib/libvirt/images")
    
    apply_acls(target_dir, default_dir)
    serialize_state(target_dir)
    
    print(f"\n{GREEN}=== Storage Provisioning Complete ==={NC}")

if __name__ == "__main__":
    main()
