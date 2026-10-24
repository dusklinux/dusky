#!/usr/bin/env python3
"""
Dusky Windows KVM & Looking Glass Manager
Author: Antigravity Pair Programmer
Scope: Automated VM start/stop/kill/reboot/view/launch pipelines.
Philosophy: Zero hardcoding, dynamic VM selector, self-learning default cache, elite socket-level sync.
"""

import os
import sys
import json
import stat
import tempfile
import time
import socket
import shutil
import subprocess
from pathlib import Path
import xml.etree.ElementTree as ET

MIN_PY: tuple[int, int, int] = (3, 14, 7)
if sys.version_info[:3] < MIN_PY:
    sys.stderr.write(f"\n[FATAL] Python {MIN_PY[0]}.{MIN_PY[1]}.{MIN_PY[2]}+ required; running {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}.\n\n")
    raise SystemExit(1)

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.table import Table
except ImportError:
    print("\n[FATAL] 'python-rich' is missing. Please run: sudo pacman -S python-rich")
    sys.exit(1)

console = Console(force_terminal=True, force_interactive=True)

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


# ANSI Terminal Colors (Mapped to Rich Tags for Drop-in Compatibility)
C_BLUE = ""
C_GREEN = ""
C_YELLOW = ""
C_RED = ""
C_BOLD = "[bold]"
C_RESET = "[/bold]"


def print_info(msg: str):
    console.print(f"[bold blue][WIN][/bold blue] {msg}")


def print_success(msg: str):
    console.print(f"[bold green][SUCCESS][/bold green] {msg}")


def print_warn(msg: str):
    console.print(f"[bold yellow][WARN][/bold yellow] {msg}")


def print_err(msg: str):
    console.print(f"[bold red][ERROR][/bold red] {msg}")


def load_state() -> dict:
    """Loads the state dictionary from the state file."""
    state_file, _, _ = get_state_file_info()
    state = {"vm": "", "key": "KEY_F6"}  # Default escape key is KEY_F6
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
    """Atomically saves state via mkstemp+os.replace+fsync (durable, not truncated)."""
    try:
        state_file, uid, gid = get_state_file_info()
        safe_mkdir_and_chown(state_file.parent, uid, gid)
        payload = json.dumps(state, indent=2, sort_keys=True) + "\n"
        # Preserve existing mode if file exists, else 0644
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
            # fsync parent dir for durability
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
    return state.get("key", "KEY_F6")


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


def _virsh_list_names() -> list[str]:
    for prefix in ([], ["sudo"]):
        try:
            res = subprocess.run([*prefix, "virsh", "-c", "qemu:///system", "list", "--all", "--name"], capture_output=True, text=True, check=True)
            return [n.strip() for n in res.stdout.split() if n.strip()]
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    return []

def _virsh_domstate(name: str) -> str:
    for prefix in ([], ["sudo"]):
        try:
            res = subprocess.run([*prefix, "virsh", "-c", "qemu:///system", "domstate", name], capture_output=True, text=True, check=True)
            return res.stdout.strip() or "unknown"
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    return "unknown"

def get_all_vms() -> list[tuple[str, str]]:
    """Query libvirt via --name + domstate (robust, no column split)."""
    names = _virsh_list_names()
    if not names:
        # Fallback to legacy table parse if --name yields nothing (empty libvirt)
        try:
            res = subprocess.run(["virsh", "-c", "qemu:///system", "list", "--all"], capture_output=True, text=True, check=True)
            vms = []
            for line in res.stdout.strip().splitlines()[2:]:
                parts = line.split()
                if len(parts) >= 3:
                    vms.append((parts[1], " ".join(parts[2:])))
                elif len(parts) == 2:
                    vms.append((parts[0], parts[1]))
            return vms
        except Exception:
            return []
    return [(n, _virsh_domstate(n)) for n in names]


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
    console.print(f"\n[bold cyan]Select a Virtual Machine to manage:[/bold cyan]")
    for idx, (name, state) in enumerate(vms):
        console.print(f"  [[bold green]{idx + 1}[/bold green]] {name} [dim]({state})[/dim]")
    
    cancel_opt = str(len(vms) + 1)
    console.print(f"  [[bold green]{cancel_opt}[/bold green]] Cancel")

    choices = [str(i) for i in range(1, len(vms) + 2)]
    while True:
        try:
            choice = Prompt.ask("\nChoice", choices=choices, default="1").strip()
            val = int(choice)
            if val == len(vms) + 1:
                print_info("Operation cancelled.")
                sys.exit(0)
            vm_name = vms[val - 1][0]
            save_cached_vm(vm_name)
            print_info(f"Preferred VM set to: [bold]{vm_name}[/bold]")
            return vm_name
        except (ValueError, KeyboardInterrupt, EOFError):
            if isinstance(sys.exc_info()[0], KeyboardInterrupt):
                print("\n")
                sys.exit(1)


def resolve_shm_path() -> str:
    """Prefer state-recorded shm_path from pipeline state, fallback to canonical /dev/shm/looking-glass."""
    state_file = Path("/var/lib/arsonix/state.json")
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            cand = data.get("shm_path")
            if isinstance(cand, str) and cand.startswith("/dev/shm/"):
                return cand
    except Exception:
        pass
    return "/dev/shm/looking-glass"


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
    title_panel = Panel(
        "[bold cyan]Dusky Windows KVM & Looking Glass Manager[/bold cyan]",
        border_style="cyan",
        expand=False
    )
    console.print(title_panel)
    console.print("\n[bold]Usage:[/bold]")
    console.print(f"  {sys.argv[0]} <action> \\[options]\n")
    
    # Actions Table
    actions_table = Table(title="[bold green]Available Actions[/bold green]", title_justify="left", show_header=True, header_style="bold green")
    actions_table.add_column("Action", style="cyan", width=12)
    actions_table.add_column("Aliases / Shortcuts", style="dim", width=20)
    actions_table.add_column("Description")
    
    actions_table.add_row("start", "-", "Start the virtual machine")
    actions_table.add_row("stop", "shutdown", "Send a graceful shutdown signal")
    actions_table.add_row("kill", "destroy", "Forcefully power off/destroy the VM")
    actions_table.add_row("reboot", "-", "Reboot the VM")
    actions_table.add_row("status", "-", "Display current running state")
    actions_table.add_row("list", "-l, --list", "List all defined virtual machines")
    actions_table.add_row("view", "show, lg", "Launch looking-glass-client")
    actions_table.add_row("launch", "play", "Start VM and wait to launch Looking Glass")
    actions_table.add_row("rdp", "connect", "Connect to the VM via FreeRDP rescue bridge")
    actions_table.add_row("edit", "-", "Edit the VM XML topology configuration")
    actions_table.add_row("select", "-", "Change the default preferred VM")
    
    console.print(actions_table)
    console.print()
    
    # Options Table
    options_table = Table(title="[bold yellow]Options[/bold yellow]", title_justify="left", show_header=True, header_style="bold yellow")
    options_table.add_column("Option", style="cyan", width=20)
    options_table.add_column("Description")
    
    options_table.add_row("--vm <name>", "Override the default VM and run the action on <name>")
    options_table.add_row("--key, -k <key>", "Override and cache the Looking Glass escape key (e.g. F6, rightctrl)")
    options_table.add_row("--help, -h", "Show this help manual")
    
    console.print(options_table)
    console.print()


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
            console.print(f"\n[bold]Defined Virtual Machines:[/bold]")
            for idx, (name, state) in enumerate(vms):
                color = "green" if state == "running" else "red"
                console.print(f"  - [bold]{name}[/bold] ([{color}]{state}[/{color}])")
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
            
        shm_file = resolve_shm_path()
        spice_port = get_spice_port(vm_name)
        print_info(f"Launching Looking Glass Client (escape key: {active_key}, SPICE port: {spice_port})...")
        cmd = ["looking-glass-client", "-f", shm_file, "-m", active_key]
        if spice_port > 0:
            cmd.extend(["-p", str(spice_port)])
        subprocess.run(cmd)

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

        shm_file = resolve_shm_path()
        spice_port = get_spice_port(vm_name)
        print_info(f"Waiting for SPICE graphics pipe on port {spice_port}...")
        
        cmd = ["looking-glass-client", "-f", shm_file, "-m", active_key]
        if spice_port > 0:
            cmd.extend(["-p", str(spice_port)])

        if wait_for_spice_port(spice_port, timeout=20):
            # A tiny sleep gives the virtual display driver (VDD) in the guest 
            # time to complete its handshakes after SPICE wakes up
            time.sleep(1.0)
            print_success(f"Graphics server online. Launching Looking Glass Client (escape key: {active_key}, SPICE port: {spice_port})...")
            subprocess.run(cmd)
        else:
            print_err(f"Timed out waiting for graphics server. Launching fallback (escape key: {active_key})...")
            subprocess.run(cmd)

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
