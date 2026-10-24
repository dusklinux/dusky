#!/usr/bin/env python3
"""
Dusky Windows KVM RDP Rescue Bridge
Author: Antigravity Pair Programmer
Scope: Automatic IP resolution and FreeRDP v3 connection logic.
Philosophy: Zero-config RDP connection utilizing libvirt MAC-to-DHCP lease maps.
"""

import argparse
import ipaddress
import socket
import os
import sys
import json
import stat
import tempfile
import time
import shutil
import subprocess
from pathlib import Path
import xml.etree.ElementTree as ET

MIN_PY: tuple[int, int, int] = (3, 15, 0)
if sys.version_info[:3] < MIN_PY:
    sys.stderr.write(f"\n[FATAL] Python {MIN_PY[0]}.{MIN_PY[1]}.{MIN_PY[2]}+ required; running {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}.\n\n")
    raise SystemExit(1)

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.text import Text
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
        "key": "KEY_F6",
        "rdp_user": "",
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
    """Atomically saves state via mkstemp+os.replace+fsync."""
    try:
        state_file, uid, gid = get_state_file_info()
        safe_mkdir_and_chown(state_file.parent, uid, gid)
        payload = json.dumps(state, indent=2, sort_keys=True) + "\n"
        keep_mode = 0o644
        if state_file.exists():
            try:
                keep_mode = stat.S_IMODE(state_file.stat().st_mode)
            except OSError:
                pass
        state_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=state_file.parent, prefix=f".{state_file.name}.", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, keep_mode)
            if os.geteuid() == 0:
                try:
                    os.chown(tmp, uid, gid)
                except Exception as e:
                    print_warn(f"Failed to chown tmp {tmp}: {e}")
            os.replace(tmp, state_file)
            try:
                dir_fd = os.open(state_file.parent, os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
            if os.geteuid() == 0:
                try:
                    os.chown(state_file, uid, gid)
                except Exception as e:
                    print_warn(f"Failed to chown file {state_file}: {e}")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    except Exception as e:
        print_warn(f"Failed to write state file: {e}")


def query_virsh(args: list[str]) -> str:
    """Bound each query and keep libvirt's machine-readable output in English."""
    error = "libvirt query failed"
    for prefix in ([], ["sudo"]):
        try:
            res = subprocess.run(
                [*prefix, "virsh", "-c", "qemu:///system", *args],
                capture_output=True, text=True, timeout=10,
                env={**os.environ, "LC_ALL": "C"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            error = str(exc)
            continue
        if res.returncode == 0:
            return res.stdout.strip()
        error = res.stderr.strip() or res.stdout.strip() or error
    raise RuntimeError(error)


def _virsh_list_names() -> list[str]:
    return [name for name in query_virsh(["list", "--all", "--name"]).splitlines() if name]


def _virsh_domstate(name: str) -> str:
    return query_virsh(["domstate", name]) or "unknown"


def get_all_vms() -> list[tuple[str, str]]:
    """Query libvirt via --name + domstate (robust, no column split)."""
    names = _virsh_list_names()
    return [(n, _virsh_domstate(n)) for n in names]


def resolve_vm(specified_vm: str | None = None) -> str:
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
    root = ET.fromstring(query_virsh(["dumpxml", vm_name]))
    return [mac.get("address").lower() for mac in root.findall("./devices/interface/mac")
            if mac.get("address")]


def get_ip_from_leases(macs: list[str]) -> str:
    """Match IPv4 DHCP leases against the VM's actual MAC addresses."""
    networks = query_virsh(["net-list", "--name"]).splitlines()
    for net in networks:
        try:
            leases = query_virsh(["net-dhcp-leases", net])
        except RuntimeError:
            continue  # A network may stop while the guest starts.
        for line in leases.splitlines():
            fields = line.split()
            if not any(field.lower() in macs for field in fields):
                continue
            for field in fields:
                if "/" not in field:
                    continue
                try:
                    address = ipaddress.ip_interface(field).ip
                except ValueError:
                    continue
                if address.version == 4:
                    return str(address)
    return ""


def get_ip_from_arp(macs: list[str]) -> str:
    """Read IPv4 neighbors as JSON, skipping failed/incomplete entries."""
    try:
        res = subprocess.run(["ip", "-j", "-4", "neigh", "show"],
                             capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            for neighbor in json.loads(res.stdout):
                if neighbor.get("lladdr", "").lower() in macs and not (
                    {"FAILED", "INCOMPLETE"} & set(neighbor.get("state", []))
                ):
                    return str(ipaddress.IPv4Address(neighbor["dst"]))
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        pass
    return ""


def resolve_vm_ip(vm_name: str, *, wait: float = 0) -> str:
    """Resolve a VM's IPv4 address, allowing time for DHCP after a start."""
    print_info(f"Resolving IP address for VM '{vm_name}'...")
    macs = get_vm_mac_addresses(vm_name)
    deadline = time.monotonic() + wait
    while True:
        ip = (get_ip_from_leases(macs) or get_ip_from_arp(macs)) if macs else ""
        if ip:
            print_success(f"Successfully resolved VM IP: [bold cyan]{ip}[/bold cyan]")
            break
        if not macs or time.monotonic() >= deadline:
            state = load_state()
            cached_ip = state.get("rdp_ip", "") if state.get("rdp_vm") == vm_name else ""
            while True:
                candidate = Prompt.ask("Enter Windows VM IP address", default=cached_ip or None).strip()
                try:
                    ip = str(ipaddress.ip_address(candidate))
                    break
                except ValueError:
                    print_err("Enter a valid IPv4 or IPv6 address.")
            break
        time.sleep(1)
    state = load_state()
    state.update(rdp_ip=ip, rdp_vm=vm_name)
    save_state(state)
    return ip


def wait_for_rdp(ip_addr: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with socket.create_connection((ip_addr, 3389), timeout=1):
                return True
        except OSError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(1, remaining))


def resolve_rdp_user(specified_user: str | None = None) -> str:
    state = load_state()
    cached_user = state.get("rdp_user", "")
    
    if specified_user:
        state["rdp_user"] = specified_user
        save_state(state)
        return specified_user
        
    if not cached_user:
        # Determine fallback based on active host user
        host_user = "Administrator"
        try:
            import pwd
            uid = os.getuid()
            if os.geteuid() == 0:
                sudo_uid = os.environ.get("SUDO_UID")
                if sudo_uid:
                    uid = int(sudo_uid)
            host_user = pwd.getpwuid(uid).pw_name
        except Exception:
            pass
            
        if host_user == "root":
            sudo_user = os.environ.get("SUDO_USER")
            if sudo_user:
                host_user = sudo_user
                
        try:
            cached_user = Prompt.ask("[bold cyan]Enter Windows RDP username[/bold cyan]", default=host_user).strip()
            state["rdp_user"] = cached_user
            save_state(state)
        except (KeyboardInterrupt, EOFError):
            sys.exit(1)
    else:
        print_info(f"Using RDP username: [bold cyan]{cached_user}[/bold cyan]")
        
    return cached_user


def get_vm_state(vm_name: str) -> str:
    return _virsh_domstate(vm_name)


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


def print_rdp_troubleshooting(ip_addr: str):
    console.print(Panel(Text(
        f"RDP connection/session to {ip_addr} failed. Check the FreeRDP error above.\n\n"
        "Windows 11 Pro, Enterprise or Education is required for the built-in RDP server; "
        "Home cannot host RDP. Enable Settings > System > Remote Desktop.\n\n"
        "Check that TCP port 3389 is allowed by the guest firewall and that the VM address "
        "is current. A VPN can change routing; inspect the route before changing firewall rules.\n\n"
        "Use the Windows account password, rather than a Windows Hello PIN. For a local "
        "account with an intentionally blank password, pass --pass '' and configure Windows "
        "to allow blank-password remote logons with NLA disabled.\n\n"
        "For setup or recovery, use the VM's SPICE console or win view after configuring "
        "the matching Looking Glass IDD. Guest SSH setup is available in guest/setup_ssh.py."),
        title="RDP troubleshooting", border_style="red",
    ))


def find_freerdp_client() -> str:
    """Generic SDL executable names must actually contain the SDL3 build."""
    for name in ("sdl3-freerdp3", "sdl-freerdp3", "sdl3-freerdp", "sdl-freerdp"):
        path = shutil.which(name)
        if path is None:
            continue
        try:
            build = subprocess.run([path, "/buildconfig"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if build.returncode == 0 and "WITH_CLIENT_SDL3=ON" in build.stdout + build.stderr:
            return path
    raise RuntimeError("FreeRDP SDL3 client not found. Install freerdp with its SDL3 client enabled.")


# These outcomes are also treated as normal by SDL3's sdl_client_cleanup().
NORMAL_SESSION_EXITS = frozenset((0, 1, 2, 11, 145))


def main():
    parser = argparse.ArgumentParser(description="Windows KVM RDP rescue bridge (Wayland)", allow_abbrev=False)
    parser.add_argument("--vm", help="target VM name")
    parser.add_argument("-u", "--user", help="Windows RDP username")
    parser.add_argument("-p", "--pass", dest="password", help="Windows password; an empty value requests a blank password")
    parser.add_argument("--ip", type=ipaddress.ip_address, help="override VM address (IPv4 or IPv6)")
    args = parser.parse_args()
    specified_vm, specified_user, password = args.vm, args.user, args.password

    freerdp_bin = find_freerdp_client()
    if not os.environ.get("WAYLAND_DISPLAY"):
        print_err("Launch RDP from your Wayland session as the desktop user.")
        sys.exit(1)

    vm_name = resolve_vm(specified_vm)
    
    started = False
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
                elif vm_state == "shut off":
                    print_info(f"Starting VM '{vm_name}'...")
                    success = run_virsh_cmd(["start", vm_name])
                else:
                    print_err(f"Cannot start VM in state {vm_state!r}.")
                    sys.exit(1)

                if success:
                    print_success(f"VM '{vm_name}' state updated successfully. Waiting for network initialization...")
                    started = True
                else:
                    print_err(f"Failed to update state for VM '{vm_name}'. Exiting.")
                    sys.exit(1)
            else:
                print_info("Exiting RDP connection.")
                sys.exit(0)
        except (KeyboardInterrupt, EOFError):
            sys.exit(1)
            
    ip_addr = str(args.ip) if args.ip is not None else resolve_vm_ip(vm_name, wait=60 if started else 0)
    print_info(f"Waiting for RDP on {ip_addr}:3389...")
    if not wait_for_rdp(ip_addr, 60 if started else 10):
        print_rdp_troubleshooting(ip_addr)
        sys.exit(1)
    username = resolve_rdp_user(specified_user)

    # Build command
    cmd = [
        freerdp_bin,
        f"/v:[{ip_addr}]" if ":" in ip_addr else f"/v:{ip_addr}",
        f"/u:{username}",
        "/cert:ignore",
        "+dynamic-resolution",
        "/network:lan",
        "/clipboard",
        "/sound",
    ]
    if password is not None:
        cmd.append(f"/p:{password}" if password else "/p")

    print_info(f"Connecting to [bold cyan]{username}@{ip_addr}[/bold cyan] via FreeRDP v3 ({Path(freerdp_bin).name})...")
    try:
        env = os.environ.copy()
        env["SDL_VIDEO_DRIVER"] = "wayland"
        res = subprocess.run(cmd, env=env)
        if res.returncode not in NORMAL_SESSION_EXITS:
            print_rdp_troubleshooting(ip_addr)
        sys.exit(res.returncode)
    except KeyboardInterrupt:
        print_info("\nRDP session terminated by operator.")
        sys.exit(130)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        raise SystemExit(130) from None
    except (RuntimeError, OSError, ET.ParseError) as exc:
        print_err(str(exc))
        raise SystemExit(1) from None
