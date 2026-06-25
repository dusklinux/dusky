#!/usr/bin/env python3
"""
ASUS TUF KVM & Looking Glass Manager
Author: Antigravity Pair Programmer
Scope: Automated VM start/stop/kill/reboot/view/launch pipelines.
Philosophy: Zero hardcoding, dynamic VM selector, self-learning default cache, elite socket-level sync.
"""

import os
import sys
import json
import time
import socket
import shutil
import subprocess
from pathlib import Path
import xml.etree.ElementTree as ET

def get_caller_identity():
    """
    Returns (home_path, uid, gid) for the actual caller.
    If run under sudo, resolves the invoking user's home directory and IDs.
    """
    uid = os.getuid()
    gid = os.getgid()
    
    # If we are root (euid == 0), check if we were called via sudo
    if os.geteuid() == 0:
        sudo_uid = os.environ.get("SUDO_UID")
        sudo_gid = os.environ.get("SUDO_GID")
        if sudo_uid and sudo_gid:
            try:
                uid = int(sudo_uid)
                gid = int(sudo_gid)
            except ValueError:
                pass
                
    try:
        import pwd
        pw = pwd.getpwuid(uid)
        home_dir = Path(pw.pw_dir)
    except Exception:
        # Fallback to standard Path.home()
        home_dir = Path.home()
        
    return home_dir, uid, gid


def get_state_file_info():
    """Returns (state_file_path, uid, gid) for state operations."""
    home_dir, uid, gid = get_caller_identity()
    state_file = home_dir / ".config" / "dusky" / "settings" / "virt" / "win_state"
    return state_file, uid, gid


def safe_mkdir_and_chown(path: Path, uid: int, gid: int):
    """
    Recursively creates directories and ensures they are owned by the specified uid/gid
    if running as root.
    """
    parts_to_create = []
    curr = path
    while curr != curr.parent:
        if curr.exists():
            break
        parts_to_create.append(curr)
        curr = curr.parent
    
    for p in reversed(parts_to_create):
        p.mkdir(exist_ok=True)
        if os.geteuid() == 0:
            try:
                os.chown(p, uid, gid)
            except Exception as e:
                print_warn(f"Failed to chown directory {p}: {e}")


# ANSI Terminal Colors
C_BLUE = "\033[34m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_RED = "\033[31m"
C_BOLD = "\033[1m"
C_RESET = "\033[0m"


def print_info(msg: str):
    print(f"{C_BLUE}[WIN]{C_RESET} {msg}")


def print_success(msg: str):
    print(f"{C_GREEN}[SUCCESS]{C_RESET} {msg}")


def print_warn(msg: str):
    print(f"{C_YELLOW}[WARN]{C_RESET} {msg}")


def print_err(msg: str):
    print(f"{C_RED}[ERROR]{C_RESET} {msg}")


def load_state() -> dict:
    """Loads the state dictionary from the state file."""
    state_file, _, _ = get_state_file_info()
    state = {"vm": "", "key": "KEY_RIGHTCTRL"}  # Default escape key is KEY_RIGHTCTRL
    if state_file.exists():
        try:
            content = state_file.read_text(encoding="utf-8").strip()
            if content:
                if content.startswith("{") and content.endswith("}"):
                    data = json.loads(content)
                    if isinstance(data, dict):
                        state.update(data)
                else:
                    # Backward compatibility for old raw-text VM name
                    state["vm"] = content
        except Exception:
            pass
    return state


def save_state(state: dict):
    """Saves the state dictionary to the state file."""
    try:
        state_file, uid, gid = get_state_file_info()
        safe_mkdir_and_chown(state_file.parent, uid, gid)
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        if os.geteuid() == 0:
            try:
                os.chown(state_file, uid, gid)
            except Exception as e:
                print_warn(f"Failed to chown file {state_file}: {e}")
    except Exception as e:
        print_warn(f"Failed to write state file: {e}")


def load_cached_vm() -> str:
    """Loads the cached VM name from the state file."""
    state = load_state()
    return state.get("vm", "")


def save_cached_vm(vm_name: str):
    """Saves the VM name to the state file."""
    state = load_state()
    state["vm"] = vm_name
    save_state(state)


def load_cached_key() -> str:
    """Loads the cached escape key from the state file."""
    state = load_state()
    return state.get("key", "KEY_RIGHTCTRL")


def save_cached_key(key: str):
    """Saves the escape key to the state file."""
    state = load_state()
    state["key"] = key
    save_state(state)


def clear_cached_vm():
    """Clears the cached VM but preserves other settings like key."""
    try:
        state = load_state()
        state["vm"] = ""
        save_state(state)
    except Exception as e:
        print_warn(f"Failed to clear VM state: {e}")


def normalize_key(key: str) -> str:
    """Normalizes key names (e.g. f6 -> KEY_F6, rightctrl -> KEY_RIGHTCTRL)."""
    k = key.strip().upper()
    if not k.startswith("KEY_"):
        # Special common aliases
        if k in ("F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12"):
            k = f"KEY_{k}"
        elif k in ("RIGHTCTRL", "RCTRL", "RIGHT_CTRL"):
            k = "KEY_RIGHTCTRL"
        elif k in ("LEFTCTRL", "LCTRL", "LEFT_CTRL"):
            k = "KEY_LEFTCTRL"
        elif k in ("RIGHTALT", "RALT", "RIGHT_ALT"):
            k = "KEY_RIGHTALT"
        elif k in ("LEFTALT", "LALT", "LEFT_ALT"):
            k = "KEY_LEFTALT"
        elif k in ("SCROLLLOCK", "SCROLL_LOCK"):
            k = "KEY_SCROLLLOCK"
        else:
            k = f"KEY_{k}"
    return k


def get_all_vms() -> list[tuple[str, str]]:
    """Query libvirt dynamically for all configured VMs and their states."""
    try:
        res = subprocess.run(
            ["virsh", "-c", "qemu:///system", "list", "--all"],
            capture_output=True, text=True, check=True
        )
        vms = []
        for line in res.stdout.strip().splitlines()[2:]:
            parts = line.split()
            if len(parts) >= 3:
                vms.append((parts[1], " ".join(parts[2:])))
            elif len(parts) == 2:
                vms.append((parts[0], parts[1]))
        return vms
    except subprocess.CalledProcessError:
        # Fall back to running with sudo if standard user hasn't refreshed group memberships yet
        try:
            res = subprocess.run(
                ["sudo", "virsh", "-c", "qemu:///system", "list", "--all"],
                capture_output=True, text=True, check=True
            )
            vms = []
            for line in res.stdout.strip().splitlines()[2:]:
                parts = line.split()
                if len(parts) >= 3:
                    vms.append((parts[1], " ".join(parts[2:])))
                elif len(parts) == 2:
                    vms.append((parts[0], parts[1]))
            return vms
        except Exception as e:
            print_err(f"Failed to query libvirt VMs: {e}")
            sys.exit(1)
    except Exception as e:
        print_err(f"Failed to query libvirt VMs: {e}")
        sys.exit(1)


def run_virsh_cmd(cmd_args: list) -> bool:
    """Runs a virsh command, elevating to sudo if direct access is rejected."""
    base_cmd = ["virsh", "-c", "qemu:///system"] + cmd_args
    try:
        subprocess.run(base_cmd, check=True)
        return True
    except subprocess.CalledProcessError:
        try:
            sudo_cmd = ["sudo", "virsh", "-c", "qemu:///system"] + cmd_args
            subprocess.run(sudo_cmd, check=True)
            return True
        except subprocess.CalledProcessError as e:
            print_err(f"Command execution failed with exit code: {e.returncode}")
            return False


def resolve_vm(specified_vm: str = None) -> str:
    """
    Returns the target VM name.
    If specified_vm is passed, validates it and stores it as default.
    Otherwise, returns the cached default or prompts the user.
    """
    vms = get_all_vms()
    vm_names = [v[0] for v in vms]

    if not vms:
        print_err("No virtual machines detected in libvirt.")
        sys.exit(1)

    # 1. Handle command-line override (--vm option)
    if specified_vm:
        if specified_vm not in vm_names:
            print_err(f"The specified VM '{specified_vm}' does not exist in libvirt.")
            print_info("Available VMs: " + ", ".join(vm_names))
            sys.exit(1)
        save_cached_vm(specified_vm)
        return specified_vm

    # 2. Check and validate cached default VM
    cached_vm = load_cached_vm()
    if cached_vm:
        if cached_vm in vm_names:
            return cached_vm
        else:
            print_warn(f"Cached default VM '{cached_vm}' no longer exists. Resetting default.")
            clear_cached_vm()

    # 3. Handle single VM scenario
    if len(vms) == 1:
        vm_name = vms[0][0]
        print_info(f"Automatically selected the only configured VM: {C_BOLD}{vm_name}{C_RESET}")
        save_cached_vm(vm_name)
        return vm_name

    # 4. Handle multiple VMs (Prompt user)
    print(f"\n{C_BOLD}Select a Virtual Machine to manage:{C_RESET}")
    for idx, (name, state) in enumerate(vms):
        print(f"  [{idx + 1}] {name} ({state})")
    print(f"  [{len(vms) + 1}] Cancel")

    while True:
        try:
            choice = input(f"Choice (1-{len(vms) + 1}): ").strip()
            val = int(choice)
            if val == len(vms) + 1:
                print_info("Operation cancelled.")
                sys.exit(0)
            if 1 <= val <= len(vms):
                vm_name = vms[val - 1][0]
                save_cached_vm(vm_name)
                print_info(f"Preferred VM set to: {C_BOLD}{vm_name}{C_RESET}")
                return vm_name
        except (ValueError, KeyboardInterrupt, EOFError):
            if isinstance(sys.exc_info()[0], KeyboardInterrupt):
                print("\n")
                sys.exit(1)
        print(f"Please enter a valid choice between 1 and {len(vms) + 1}")


def get_spice_port(vm_name: str) -> int:
    """Parse the VM XML to extract the active SPICE port (handling autoport)."""
    try:
        res = subprocess.run(["virsh", "-c", "qemu:///system", "dumpxml", vm_name], capture_output=True, text=True)
        if res.returncode != 0:
            res = subprocess.run(["sudo", "virsh", "-c", "qemu:///system", "dumpxml", vm_name], capture_output=True, text=True)
        
        if res.returncode == 0:
            root = ET.fromstring(res.stdout)
            graphics = root.find(".//devices/graphics[@type='spice']")
            if graphics is not None:
                port_str = graphics.get("port")
                if port_str and port_str.isdigit():
                    return int(port_str)
    except Exception:
        pass
    return 5900  # Default fallback port


def wait_for_spice_port(port: int, timeout: int = 15) -> bool:
    """Blocks until the SPICE socket opens, ensuring zero-delay launch alignment."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except (socket.timeout, ConnectionRefusedError):
            time.sleep(0.5)
    return False


def get_vm_state(vm_name: str) -> str:
    """Returns the precise state of the VM (e.g. running, shut off)."""
    res = subprocess.run(["virsh", "-c", "qemu:///system", "domstate", vm_name], capture_output=True, text=True)
    if res.returncode != 0:
        res = subprocess.run(["sudo", "virsh", "-c", "qemu:///system", "domstate", vm_name], capture_output=True, text=True)
    return res.stdout.strip() if res.returncode == 0 else "unknown"


def print_help():
    print(f"""{C_BOLD}Windows KVM & Looking Glass Manager{C_RESET}

Usage:
  {sys.argv[0]} <action> [options]

Actions:
  {C_GREEN}start{C_RESET}        Start the virtual machine
  {C_GREEN}stop{C_RESET}         Send a graceful shutdown signal (alias: shutdown)
  {C_GREEN}kill{C_RESET}         Forcefully power off/destroy the VM (alias: destroy)
  {C_GREEN}reboot{C_RESET}       Reboot the VM
  {C_GREEN}status{C_RESET}       Display current running state
  {C_GREEN}list{C_RESET}         List all defined virtual machines (alias: --list, -l)
  {C_GREEN}view{C_RESET}         Launch looking-glass-client (alias: show, lg)
  {C_GREEN}launch{C_RESET}       Start VM and automatically wait to launch Looking Glass (alias: play)
  {C_GREEN}rdp{C_RESET}          Connect to the VM via FreeRDP rescue bridge (alias: connect)
  {C_GREEN}edit{C_RESET}         Edit the VM XML topology configuration
  {C_GREEN}select{C_RESET}       Change the default preferred VM

Options:
  --vm <name>    Override the default VM and run the action on <name>
  --key, -k <key> Override and cache the Looking Glass escape key (e.g. F6, rightctrl)
  --help, -h     Show this help manual
""")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("--help", "-h", "help"):
        print_help()
        sys.exit(0)

    action = sys.argv[1].lower()
    
    # Parse options
    specified_vm = None
    if "--vm" in sys.argv:
        try:
            idx = sys.argv.index("--vm")
            specified_vm = sys.argv[idx + 1]
        except IndexError:
            print_err("Missing VM name after --vm option.")
            sys.exit(1)

    # Parse key option
    specified_key = None
    if "--key" in sys.argv or "-k" in sys.argv:
        key_flag = "--key" if "--key" in sys.argv else "-k"
        try:
            idx = sys.argv.index(key_flag)
            specified_key = sys.argv[idx + 1]
        except IndexError:
            print_err(f"Missing key name after {key_flag} option.")
            sys.exit(1)

    # Check for list option/action before VM resolution (so list command doesn't trigger prompts)
    if action in ("list", "--list", "-l") or "--list" in sys.argv or "-l" in sys.argv:
        vms = get_all_vms()
        if not vms:
            print_info("No virtual machines found in libvirt.")
        else:
            print(f"\n{C_BOLD}Defined Virtual Machines:{C_RESET}")
            for idx, (name, state) in enumerate(vms):
                color = C_GREEN if state == "running" else C_RED
                print(f"  - {C_BOLD}{name}{C_RESET} ({color}{state}{C_RESET})")
        sys.exit(0)

    vm_name = resolve_vm(specified_vm)

    # Resolve escape key
    if specified_key:
        active_key = normalize_key(specified_key)
        save_cached_key(active_key)
        print_info(f"Escape key set to: {C_BOLD}{active_key}{C_RESET}")
    else:
        active_key = load_cached_key()

    if action == "select":
        # Clear default VM and trigger selection prompt
        clear_cached_vm()
        resolve_vm()
        sys.exit(0)

    elif action == "start":
        state = get_vm_state(vm_name)
        if state == "paused":
            print_info(f"Resuming paused VM '{vm_name}'...")
            if run_virsh_cmd(["resume", vm_name]):
                print_success(f"VM '{vm_name}' resumed.")
        elif state == "pmsuspended":
            print_info(f"Waking up suspended VM '{vm_name}'...")
            if run_virsh_cmd(["dompmwakeup", vm_name]):
                print_success(f"VM '{vm_name}' awoken.")
        else:
            print_info(f"Starting VM '{vm_name}'...")
            if run_virsh_cmd(["start", vm_name]):
                print_success(f"VM '{vm_name}' booted.")

    elif action in ("stop", "shutdown"):
        print_info(f"Sending graceful shutdown signal to VM '{vm_name}'...")
        if run_virsh_cmd(["shutdown", vm_name]):
            print_success("Shutdown signal dispatched.")

    elif action in ("kill", "destroy"):
        print_info(f"Forcefully shutting down (destroying) VM '{vm_name}'...")
        if run_virsh_cmd(["destroy", vm_name]):
            print_success("VM forcefully terminated.")

    elif action == "reboot":
        print_info(f"Rebooting VM '{vm_name}'...")
        if run_virsh_cmd(["reboot", vm_name]):
            print_success("Reboot signal dispatched.")

    elif action == "status":
        state = get_vm_state(vm_name)
        color = C_GREEN if state == "running" else C_RED
        print_info(f"VM '{vm_name}' state: {color}{state}{C_RESET}")

    elif action == "edit":
        env = os.environ.copy()
        if "EDITOR" not in env and "VISUAL" not in env:
            env["EDITOR"] = "nano"
        
        print_info(f"Opening XML editor for VM '{vm_name}'...")
        try:
            subprocess.run(["virsh", "-c", "qemu:///system", "edit", vm_name], env=env, check=True)
        except subprocess.CalledProcessError:
            subprocess.run(["sudo", "virsh", "-c", "qemu:///system", "edit", vm_name], env=env, check=True)

    elif action in ("view", "lg", "show"):
        if not shutil.which("looking-glass-client"):
            print_err("looking-glass-client binary not found in PATH.")
            sys.exit(1)
        
        state = get_vm_state(vm_name)
        if state != "running":
            print_warn(f"VM '{vm_name}' is currently {state}. Connection might fail.")
            
        print_info(f"Launching Looking Glass Client (escape key: {active_key})...")
        subprocess.run(["looking-glass-client", "-m", active_key])

    elif action in ("launch", "play"):
        if not shutil.which("looking-glass-client"):
            print_err("looking-glass-client binary not found in PATH.")
            sys.exit(1)

        state = get_vm_state(vm_name)
        if state != "running":
            if state == "paused":
                print_info(f"Resuming paused VM '{vm_name}'...")
                run_virsh_cmd(["resume", vm_name])
            elif state == "pmsuspended":
                print_info(f"Waking up suspended VM '{vm_name}'...")
                run_virsh_cmd(["dompmwakeup", vm_name])
            else:
                print_info(f"Starting VM '{vm_name}'...")
                run_virsh_cmd(["start", vm_name])
        else:
            print_info(f"VM '{vm_name}' is already running.")

        spice_port = get_spice_port(vm_name)
        print_info(f"Waiting for SPICE graphics pipe on port {spice_port}...")
        
        if wait_for_spice_port(spice_port, timeout=20):
            # A tiny sleep gives the virtual display driver (VDD) in the guest 
            # time to complete its handshakes after SPICE wakes up
            time.sleep(1.0)
            print_success(f"Graphics server online. Launching Looking Glass Client (escape key: {active_key})...")
            subprocess.run(["looking-glass-client", "-m", active_key])
        else:
            print_err(f"Timed out waiting for graphics server. Launching fallback (escape key: {active_key})...")
            subprocess.run(["looking-glass-client", "-m", active_key])

    elif action in ("rdp", "connect"):
        rdp_script = Path(__file__).parent / "55_rdp.py"
        cmd = [sys.executable, str(rdp_script)] + sys.argv[2:]
        subprocess.run(cmd)

    else:
        print_err(f"Unknown action: {action}")
        print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
