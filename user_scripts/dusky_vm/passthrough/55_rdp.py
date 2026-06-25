#!/usr/bin/env python3
"""
ASUS TUF KVM RDP Rescue Bridge
Author: Antigravity Pair Programmer
Scope: Automatic IP resolution and FreeRDP v3 connection logic.
Philosophy: Zero-config RDP connection utilizing libvirt MAC-to-DHCP lease maps.
"""

import os
import sys
import json
import time
import shutil
import subprocess
from pathlib import Path
import xml.etree.ElementTree as ET

# ANSI Terminal Colors
C_BLUE = "\033[34m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_RED = "\033[31m"
C_BOLD = "\033[1m"
C_RESET = "\033[0m"


def print_info(msg: str):
    print(f"{C_BLUE}[RDP]{C_RESET} {msg}")


def print_success(msg: str):
    print(f"{C_GREEN}[SUCCESS]{C_RESET} {msg}")


def print_warn(msg: str):
    print(f"{C_YELLOW}[WARN]{C_RESET} {msg}")


def print_err(msg: str):
    print(f"{C_RED}[ERROR]{C_RESET} {msg}")


def get_caller_identity():
    """
    Returns (home_path, uid, gid) for the actual caller.
    If run under sudo, resolves the invoking user's home directory and IDs.
    """
    uid = os.getuid()
    gid = os.getgid()
    
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
        home_dir = Path.home()
        
    return home_dir, uid, gid


def get_state_file_info():
    """Returns (state_file_path, uid, gid) for state operations."""
    home_dir, uid, gid = get_caller_identity()
    state_file = home_dir / ".config" / "dusky" / "settings" / "virt" / "win_state"
    return state_file, uid, gid


def safe_mkdir_and_chown(path: Path, uid: int, gid: int):
    """Recursively creates directories and ensures they are owned by uid/gid."""
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


def load_state() -> dict:
    state_file, _, _ = get_state_file_info()
    state = {
        "vm": "",
        "key": "KEY_RIGHTCTRL",
        "rdp_user": "dusk",
        "rdp_ip": ""
    }
    if state_file.exists():
        try:
            content = state_file.read_text(encoding="utf-8").strip()
            if content:
                if content.startswith("{") and content.endswith("}"):
                    data = json.loads(content)
                    if isinstance(data, dict):
                        state.update(data)
                else:
                    state["vm"] = content
        except Exception:
            pass
    return state


def save_state(state: dict):
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
    except Exception:
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


def resolve_vm(specified_vm: str = None) -> str:
    vms = get_all_vms()
    vm_names = [v[0] for v in vms]

    if not vms:
        print_err("No virtual machines detected in libvirt.")
        sys.exit(1)

    if specified_vm:
        if specified_vm not in vm_names:
            print_err(f"The specified VM '{specified_vm}' does not exist in libvirt.")
            sys.exit(1)
        state = load_state()
        state["vm"] = specified_vm
        save_state(state)
        return specified_vm

    state = load_state()
    cached_vm = state.get("vm", "")
    if cached_vm and cached_vm in vm_names:
        return cached_vm

    if len(vms) == 1:
        vm_name = vms[0][0]
        state["vm"] = vm_name
        save_state(state)
        return vm_name

    print(f"\n{C_BOLD}Select a Virtual Machine to connect via RDP:{C_RESET}")
    for idx, (name, vm_state) in enumerate(vms):
        print(f"  [{idx + 1}] {name} ({vm_state})")
    print(f"  [{len(vms) + 1}] Cancel")

    while True:
        try:
            choice = input(f"Choice (1-{len(vms) + 1}): ").strip()
            val = int(choice)
            if val == len(vms) + 1:
                sys.exit(0)
            if 1 <= val <= len(vms):
                vm_name = vms[val - 1][0]
                state["vm"] = vm_name
                save_state(state)
                return vm_name
        except (ValueError, KeyboardInterrupt, EOFError):
            if isinstance(sys.exc_info()[0], KeyboardInterrupt):
                sys.exit(1)


def get_vm_mac_addresses(vm_name: str) -> list[str]:
    """Parse VM XML to extract MAC addresses."""
    try:
        res = subprocess.run(["virsh", "-c", "qemu:///system", "dumpxml", vm_name], capture_output=True, text=True)
        if res.returncode != 0:
            res = subprocess.run(["sudo", "virsh", "-c", "qemu:///system", "dumpxml", vm_name], capture_output=True, text=True)
        if res.returncode == 0:
            root = ET.fromstring(res.stdout)
            macs = []
            for mac in root.findall(".//devices/interface/mac"):
                addr = mac.get("address")
                if addr:
                    macs.append(addr.lower())
            return macs
    except Exception:
        pass
    return []


def get_ip_from_leases(macs: list[str]) -> str:
    """Scan libvirt networks for a DHCP lease matching one of the MAC addresses."""
    try:
        res = subprocess.run(["virsh", "-c", "qemu:///system", "net-list", "--name"], capture_output=True, text=True)
        if res.returncode != 0:
            res = subprocess.run(["sudo", "virsh", "-c", "qemu:///system", "net-list", "--name"], capture_output=True, text=True)
        networks = []
        if res.returncode == 0:
            networks = [n.strip() for n in res.stdout.strip().splitlines() if n.strip()]
        
        if not networks:
            networks = ["default"]
            
        for net in networks:
            res_leases = subprocess.run(["virsh", "-c", "qemu:///system", "net-dhcp-leases", net], capture_output=True, text=True)
            if res_leases.returncode != 0:
                res_leases = subprocess.run(["sudo", "virsh", "-c", "qemu:///system", "net-dhcp-leases", net], capture_output=True, text=True)
            if res_leases.returncode == 0:
                for line in res_leases.stdout.splitlines():
                    parts = line.split()
                    for part in parts:
                        if ":" in part and len(part) == 17:
                            mac = part.lower()
                            if mac in macs:
                                for p in parts:
                                    if "/" in p and ("." in p or ":" in p):
                                        return p.split('/')[0]
    except Exception:
        pass
    return ""


def get_ip_from_arp(macs: list[str]) -> str:
    """Scan ARP table for MAC addresses."""
    try:
        res = subprocess.run(["ip", "neigh", "show"], capture_output=True, text=True)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 5:
                    ip = parts[0]
                    if "lladdr" in parts:
                        idx = parts.index("lladdr")
                        if idx + 1 < len(parts):
                            mac = parts[idx + 1].lower()
                            if mac in macs:
                                return ip
    except Exception:
        pass
    return ""


def resolve_vm_ip(vm_name: str) -> str:
    """Automatically resolve VM IP address or prompt as fallback."""
    print_info(f"Resolving IP address for VM '{vm_name}'...")
    macs = get_vm_mac_addresses(vm_name)
    ip = ""
    if macs:
        ip = get_ip_from_leases(macs)
        if not ip:
            ip = get_ip_from_arp(macs)
            
    state = load_state()
    cached_ip = state.get("rdp_ip", "")
    
    if ip:
        print_success(f"Successfully resolved VM IP: {C_BOLD}{ip}{C_RESET}")
        state["rdp_ip"] = ip
        save_state(state)
        return ip
        
    if cached_ip:
        print_warn(f"Could not automatically resolve IP. Falling back to cached IP: {C_BOLD}{cached_ip}{C_RESET}")
        try:
            choice = input("Press Enter to use cached IP, or type a new IP address: ").strip()
            if choice:
                ip = choice
            else:
                ip = cached_ip
        except (KeyboardInterrupt, EOFError):
            sys.exit(1)
    else:
        while not ip:
            try:
                ip = input("Could not automatically resolve IP. Enter Windows VM IP address: ").strip()
            except (KeyboardInterrupt, EOFError):
                sys.exit(1)
                
    state["rdp_ip"] = ip
    save_state(state)
    return ip


def resolve_rdp_user(specified_user: str = None) -> str:
    state = load_state()
    cached_user = state.get("rdp_user", "dusk")
    
    if specified_user:
        state["rdp_user"] = specified_user
        save_state(state)
        return specified_user
        
    print_info(f"Using RDP username: {C_BOLD}{cached_user}{C_RESET}")
    return cached_user


def get_vm_state(vm_name: str) -> str:
    res = subprocess.run(["virsh", "-c", "qemu:///system", "domstate", vm_name], capture_output=True, text=True)
    if res.returncode != 0:
        res = subprocess.run(["sudo", "virsh", "-c", "qemu:///system", "domstate", vm_name], capture_output=True, text=True)
    return res.stdout.strip() if res.returncode == 0 else "unknown"


def run_virsh_cmd(cmd_args: list) -> bool:
    base_cmd = ["virsh", "-c", "qemu:///system"] + cmd_args
    try:
        subprocess.run(base_cmd, check=True)
        return True
    except subprocess.CalledProcessError:
        try:
            sudo_cmd = ["sudo", "virsh", "-c", "qemu:///system"] + cmd_args
            subprocess.run(sudo_cmd, check=True)
            return True
        except subprocess.CalledProcessError:
            return False


def print_help():
    print(f"""{C_BOLD}Windows KVM RDP Rescue Bridge{C_RESET}

Usage:
  {sys.argv[0]} [options]

Options:
  --vm <name>       Override target VM
  -u, --user <name> Specify Windows RDP username (default: cached/dusk)
  -p, --pass <word> Specify Windows RDP password
  --help, -h        Show this help manual
""")


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print_help()
        sys.exit(0)

    # Parse options
    specified_vm = None
    if "--vm" in sys.argv:
        try:
            idx = sys.argv.index("--vm")
            specified_vm = sys.argv[idx + 1]
        except IndexError:
            print_err("Missing VM name after --vm option.")
            sys.exit(1)

    specified_user = None
    if "--user" in sys.argv or "-u" in sys.argv:
        flag = "--user" if "--user" in sys.argv else "-u"
        try:
            idx = sys.argv.index(flag)
            specified_user = sys.argv[idx + 1]
        except IndexError:
            print_err(f"Missing username after {flag} option.")
            sys.exit(1)

    password = None
    if "--pass" in sys.argv or "-p" in sys.argv:
        flag = "--pass" if "--pass" in sys.argv else "-p"
        try:
            idx = sys.argv.index(flag)
            password = sys.argv[idx + 1]
        except IndexError:
            print_err(f"Missing password after {flag} option.")
            sys.exit(1)

    # Run checks
    if not shutil.which("xfreerdp3"):
        print_err("xfreerdp3 binary not found. Please install it via: sudo pacman -S freerdp")
        sys.exit(1)

    vm_name = resolve_vm(specified_vm)
    
    vm_state = get_vm_state(vm_name)
    if vm_state != "running":
        print_warn(f"VM '{vm_name}' is currently {vm_state}.")
        try:
            action_verb = "start"
            if vm_state == "paused":
                action_verb = "resume"
            elif vm_state == "pmsuspended":
                action_verb = "dompmwakeup"
                
            choice = input(f"Do you want to {action_verb} the VM? [y/N]: ").strip().lower()
            if choice in ("y", "yes"):
                success = False
                if vm_state == "paused":
                    print_info(f"Resuming paused VM '{vm_name}'...")
                    success = run_virsh_cmd(["resume", vm_name])
                elif vm_state == "pmsuspended":
                    print_info(f"Waking up suspended VM '{vm_name}'...")
                    success = run_virsh_cmd(["dompmwakeup", vm_name])
                else:
                    print_info(f"Starting VM '{vm_name}'...")
                    success = run_virsh_cmd(["start", vm_name])
                    
                if success:
                    print_success(f"VM '{vm_name}' state updated successfully. Waiting for network initialization...")
                    time.sleep(5.0)
                else:
                    print_err(f"Failed to update state for VM '{vm_name}'. Exiting.")
                    sys.exit(1)
            else:
                print_info("Exiting RDP connection.")
                sys.exit(0)
        except (KeyboardInterrupt, EOFError):
            sys.exit(1)
            
    ip_addr = resolve_vm_ip(vm_name)
    username = resolve_rdp_user(specified_user)

    # Build command
    cmd = [
        "xfreerdp3",
        f"/v:{ip_addr}",
        f"/u:{username}",
        "/cert:ignore",
        "/dynamic-resolution"
    ]
    if password:
        cmd.append(f"/p:{password}")

    print_info(f"Connecting to {C_BOLD}{username}@{ip_addr}{C_RESET} via FreeRDP v3...")
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print_info("\nRDP session terminated by operator.")


if __name__ == "__main__":
    main()
