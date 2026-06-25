#!/usr/bin/env python3
"""
Dusky Windows KVM RDP Rescue Bridge
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

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
except ImportError:
    print("\n[FATAL] 'python-rich' is missing. Please run: sudo pacman -S python-rich")
    sys.exit(1)

# Force terminal characteristics for orchestrator tee compatibility 
console = Console(force_terminal=True, force_interactive=True)


def print_info(msg: str):
    console.print(f"[bold blue][RDP][/bold blue] {msg}")


def print_success(msg: str):
    console.print(f"[bold green][SUCCESS][/bold green] {msg}")


def print_warn(msg: str):
    console.print(f"[bold yellow][WARN][/bold yellow] {msg}")


def print_err(msg: str):
    console.print(f"[bold red][ERROR][/bold red] {msg}")


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

    console.print(f"\n[bold cyan]Select a Virtual Machine to connect via RDP:[/bold cyan]")
    for idx, (name, vm_state) in enumerate(vms):
        console.print(f"  [[bold green]{idx + 1}[/bold green]] {name} [dim]({vm_state})[/dim]")
    
    cancel_opt = str(len(vms) + 1)
    console.print(f"  [[bold green]{cancel_opt}[/bold green]] Cancel")

    choices = [str(i) for i in range(1, len(vms) + 2)]
    try:
        choice = Prompt.ask("\nChoice", choices=choices, default="1")
        val = int(choice)
        if val == len(vms) + 1:
            sys.exit(0)
        vm_name = vms[val - 1][0]
        state["vm"] = vm_name
        save_state(state)
        return vm_name
    except (KeyboardInterrupt, EOFError):
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
        print_success(f"Successfully resolved VM IP: [bold cyan]{ip}[/bold cyan]")
        state["rdp_ip"] = ip
        save_state(state)
        return ip
        
    if cached_ip:
        print_warn(f"Could not automatically resolve IP. Falling back to cached IP: [bold cyan]{cached_ip}[/bold cyan]")
        try:
            choice = Prompt.ask("Enter IP address to use", default=cached_ip).strip()
            ip = choice
        except (KeyboardInterrupt, EOFError):
            sys.exit(1)
    else:
        try:
            ip = Prompt.ask("Could not automatically resolve IP. Enter Windows VM IP").strip()
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
        
    print_info(f"Using RDP username: [bold cyan]{cached_user}[/bold cyan]")
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


def check_warp_status() -> str:
    """Check if Cloudflare WARP is active and connected."""
    if shutil.which("warp-cli"):
        try:
            res = subprocess.run(["warp-cli", "status"], capture_output=True, text=True, timeout=2)
            if "Status update: Connected" in res.stdout:
                return "Connected"
        except Exception:
            pass
    return "Disconnected"


def get_active_vpns() -> list[str]:
    """Check for active VPN interfaces in /sys/class/net/."""
    vpns = []
    vpn_prefixes = ("tun", "tap", "wg", "tailscale", "proton", "warp")
    try:
        for net_dir in Path("/sys/class/net").iterdir():
            name = net_dir.name
            if any(name.startswith(p) for p in vpn_prefixes):
                operstate_file = net_dir / "operstate"
                if operstate_file.exists() and operstate_file.read_text().strip() == "up":
                    vpns.append(name)
    except Exception:
        pass
    return vpns


def is_ufw_active() -> bool:
    """Check if UFW systemd service is active."""
    try:
        res = subprocess.run(["systemctl", "is-active", "ufw"], capture_output=True, text=True)
        return res.stdout.strip() == "active"
    except Exception:
        return False


def print_rdp_troubleshooting(ip_addr: str):
    warp_connected = check_warp_status() == "Connected"
    active_vpns = get_active_vpns()
    ufw_active = is_ufw_active()

    env_warnings = []
    if warp_connected:
        env_warnings.append("  [bold red]• Cloudflare WARP is active.[/bold red] Run [bold cyan]warp-cli disconnect[/bold cyan] to disable it.")
    if active_vpns:
        env_warnings.append(f"  [bold red]• Active VPN interface(s) detected: {', '.join(active_vpns)}.[/bold red] Disconnect your VPN client.")
    if ufw_active:
        env_warnings.append("  [bold yellow]• Host Firewall (UFW) is active.[/bold yellow] If routing is blocked, run:\n"
                            "    [bold cyan]sudo ufw allow in on virbr0 && sudo ufw route allow in on virbr0[/bold cyan]")

    trouble_text = f"The RDP connection to [bold cyan]{ip_addr}[/bold cyan] failed.\n\n"
    if env_warnings:
        trouble_text += "[bold yellow]Host Environment Warnings (Potential Blocks):[/bold yellow]\n"
        trouble_text += "\n".join(env_warnings) + "\n\n"

    trouble_text += (
        "[bold yellow]Option A: Quick Automatic Configuration (Recommended)[/bold yellow]\n"
        "To configure your Windows VM automatically, run this command [bold yellow]inside your Windows VM[/bold yellow]:\n"
        "  1. If not already viewing the VM, open Looking Glass on Linux: [bold cyan]win view[/bold cyan]\n"
        "  2. In the Windows VM GUI, click the Start menu, type [bold cyan]powershell[/bold cyan],\n"
        "     right-click [bold]Windows PowerShell[/bold], and select [bold]Run as Administrator[/bold].\n"
        "  3. Copy and paste this exact command block and press [bold]Enter[/bold]:\n\n"
        "  [bold green]Set-ItemProperty -Path 'HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server' -Name 'fDenyTSConnections' -Value 0; "
        "Set-ItemProperty -Path 'HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server\\WinStations\\RDP-Tcp' -Name 'UserAuthentication' -Value 0; "
        "Set-ItemProperty -Path 'HKLM:\\System\\CurrentControlSet\\Control\\Lsa' -Name 'LimitBlankPasswordUse' -Value 0; "
        "Get-NetConnectionProfile | Set-NetConnectionProfile -NetworkCategory Private; "
        "Enable-NetFirewallRule -DisplayGroup 'Remote Desktop'[/bold green]\n\n"
        "[bold yellow]Option B: Enable SSH for Remote Console Access[/bold yellow]\n"
        "To install and enable OpenSSH Server (allowing you to connect/debug from Linux terminal via SSH), run this command in the same Administrator PowerShell:\n\n"
        "  [bold green]Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0; "
        "Start-Service sshd; Set-Service -Name sshd -StartupType Automatic; "
        "(Get-Content C:\\ProgramData\\ssh\\sshd_config) -replace '#PermitEmptyPasswords no', 'PermitEmptyPasswords yes' -replace 'PermitEmptyPasswords no', 'PermitEmptyPasswords yes' | Set-Content C:\\ProgramData\\ssh\\sshd_config; "
        "Restart-Service sshd[/bold green]\n\n"
        "[bold yellow]Option C: Manual Configuration Steps[/bold yellow]\n"
        "  [bold green]1. Enable Remote Desktop[/bold green]\n"
        "     [bold white]Settings[/bold white] > [bold white]System[/bold white] > [bold white]Remote Desktop[/bold white] > Toggle [bold green]ON[/bold green].\n\n"
        "  [bold green]2. Disable Network Level Authentication (NLA) (required for blank passwords)[/bold green]\n"
        "     [bold white]Settings[/bold white] > [bold white]System[/bold white] > [bold white]Remote Desktop[/bold white] > [bold white]Advanced settings[/bold white] >\n"
        "     [bold red]Uncheck[/bold red] [bold cyan]'Require computers to use Network Level Authentication to connect'[/bold cyan]\n"
        "     and click '[bold green]Proceed anyway[/bold green]' on the warning prompt.\n\n"
        "  [bold green]3. Set Network Profile to Private[/bold green]\n"
        "     [bold white]Settings[/bold white] > [bold white]Network & Internet[/bold white] > [bold white]Properties[/bold white] > Select [bold green]'Private'[/bold green].\n"
        "     (Windows Firewall blocks incoming RDP on 'Public' profiles).\n\n"
        "  [bold green]4. Check Custom ISO Restrictions[/bold green]\n"
        "     If you are using a custom ISO with RDP stripped out, this method will not work. (Try a standard ISO).\n\n"
        "  [bold green]5. Blank Passwords Policy (for passwordless login)[/bold green]\n"
        "     If you have no password set on Windows, you must disable the blank password restriction:\n"
        "       a. Press [bold cyan]Super + R[/bold cyan] (or Win + R) to open the Run dialog.\n"
        "       b. Type [bold cyan]secpol.msc[/bold cyan] (or [bold cyan]gpedit.msc[/bold cyan]) and press Enter.\n"
        "       c. In the [italic white]left-hand navigation tree[/italic white], expand the [bold yellow]Local Policies[/bold yellow] folder\n"
        "          and click directly on the [bold yellow]Security Options[/bold yellow] folder. (Do not stay at the root!).\n"
        "       d. In the [italic white]right pane[/italic white], find and double-click the policy:\n"
        "          [bold cyan]'Accounts: Limit local account use of blank passwords to console logon only'[/bold cyan].\n"
        "       e. Select [bold green]Disabled[/bold green], click [bold magenta]Apply[/bold magenta], and click [bold magenta]OK[/bold magenta].\n"
        "       f. Note: If using Windows Home (which lacks secpol.msc), press [bold cyan]Super + R[/bold cyan],\n"
        "          run [bold cyan]regedit[/bold cyan], go to `HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Control\\Lsa`,\n"
        "          and set the DWORD [bold cyan]LimitBlankPasswordUse[/bold cyan] to [bold green]0[/bold green].\n\n"
        "  [bold green]6. Firewall Access[/bold green]\n"
        "     Ensure Windows Defender Firewall allows 'Remote Desktop' (TCP port 3389)."
    )
    console.print("\n")
    console.print(Panel(
        trouble_text,
        title="[bold red]RDP CONNECTION TROUBLESHOOTING[/bold red]",
        title_align="center",
        border_style="red"
    ))
    console.print("\n")


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
        print_warn(f"VM '{vm_name}' is currently [bold red]{vm_state}[/bold red].")
        try:
            action_verb = "start"
            if vm_state == "paused":
                action_verb = "resume"
            elif vm_state == "pmsuspended":
                action_verb = "dompmwakeup"
                
            choice = Prompt.ask(f"Do you want to {action_verb} the VM?", choices=["y", "n"], default="y").strip().lower()
            if choice == "y":
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

    print_info(f"Connecting to [bold cyan]{username}@{ip_addr}[/bold cyan] via FreeRDP v3...")
    try:
        res = subprocess.run(cmd)
        if res.returncode != 0:
            print_rdp_troubleshooting(ip_addr)
    except KeyboardInterrupt:
        print_info("\nRDP session terminated by operator.")


if __name__ == "__main__":
    main()
