#!/usr/bin/env python3
"""
Phase 5: Looking Glass KVMFR Host Configuration
Target: Arch Linux (Kernel 7.1.0+), Python 3.14.5+, systemd 260
Scope: KVMFR Modprobe, udev rules, cgroup whitelisting, dynamic IVSHMEM calculation.
Philosophy: Zero-Clutter Idempotency, Atomic Writes, Strict Cgroup Regex Parsing, Ring 0 Safety.
"""

import os
import sys
import re
import pwd
import stat
import shutil
import tempfile
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Never, Tuple

# ==============================================================================
# BOOTSTRAP: Strict Privilege & Auto-Elevation
# ==============================================================================
def require_root() -> None:
    """Enforce eUID 0. Auto-elevate via sudo if executed as a standard user."""
    if os.geteuid() != 0:
        print("\n[INFO] Administrative privileges required. Elevating via sudo...")
        try:
            # Replace the current process with a sudo call, preserving exact binary and args
            os.execvp("sudo", ["sudo", sys.executable] + sys.argv)
        except OSError as e:
            print(f"\n[FATAL] Failed to elevate privileges dynamically: {e}")
            sys.exit(1)

require_root()

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich.table import Table
except ImportError:
    print("\n[FATAL] 'python-rich' is missing. Please run: sudo pacman -S python-rich")
    sys.exit(1)

# Force terminal characteristics for orchestrator tee compatibility 
console = Console(force_terminal=True, force_interactive=True)

# ==============================================================================
# CORE UTILITIES
# ==============================================================================
def bail(msg: str) -> Never:
    """Exit gracefully with a clear error panel."""
    console.print(Panel(f"[bold red]FATAL ERROR:[/bold red] {msg}", border_style="red"))
    sys.exit(1)

def atomic_write(target_path: Path, new_content: str) -> bool:
    """
    Safely writes data using a temporary file and an atomic swap.
    Inherits exact file permissions (st_mode) to prevent security regressions.
    Zero-clutter: NO .bak files are ever created.
    """
    if target_path.exists():
        if target_path.read_text(encoding="utf-8") == new_content:
            return False
        mode = target_path.stat().st_mode
    else:
        mode = 0o644 # Default standard file permissions
        
    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(dir=target_path.parent, prefix=f".{target_path.name}.tmp.")
    tmp_path = Path(tmp_path_str)
    
    try:
        with os.fdopen(fd, 'w', encoding="utf-8") as f:
            f.write(new_content)
        os.chmod(tmp_path, stat.S_IMODE(mode))
        shutil.move(tmp_path, target_path)
        return True
    except Exception as e:
        if tmp_path.exists():
            tmp_path.unlink()
        bail(f"Atomic write failed on {target_path}: {e}")

def run_cmd(cmd: list, check: bool = True) -> int:
    """
    Execute shell commands silently. 
    Raises fatal error if check=True and command fails. Returns the exit code.
    """
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if check and result.returncode != 0:
        bail(f"Command execution failed: {' '.join(cmd)}\nExit Code: {result.returncode}")
    return result.returncode

# ==============================================================================
# USER RESOLUTION & PACKAGE INSTALLATION
# ==============================================================================
def resolve_target_user() -> str:
    """Forensically determine the real human user interacting with the system."""
    user = os.environ.get("SUDO_USER") or os.environ.get("DOAS_USER")
    
    if not user or user == "root":
        try:
            user = os.getlogin()
        except OSError:
            pass # TTY might not be attached properly
            
    if not user or user == "root":
        console.print("[yellow]⚠ Could not automatically determine standard user from environment.[/yellow]")
        user = Prompt.ask("[bold cyan]Enter your non-root Arch username[/bold cyan]").strip()
    
    try:
        pwd.getpwnam(user)
    except KeyError:
        bail(f"The user '{user}' does not exist in the local passwd database.")
        
    return user

def install_looking_glass_packages(user: str) -> None:
    """Install required packages via AUR using the standard user."""
    packages = ["looking-glass-module-dkms-git", "looking-glass-git", "freerdp", "dkms"]
    console.print("\n[bold blue]==>[/bold blue] [bold]Synchronizing Looking Glass packages...[/bold]")
    
    if not shutil.which("paru"):
        bail("'paru' not found in PATH. Cannot install AUR packages.")
        
    # Drop privileges to standard user to run paru; bypass pagers with --noconfirm
    cmd = ["sudo", "-u", user, "paru", "-S", "--needed", "--noconfirm", "--skipreview"] + packages
    
    try:
        subprocess.run(cmd, check=True)
        console.print("[bold green]  ✓ Looking Glass & DKMS packages staged successfully.[/bold green]")
    except subprocess.CalledProcessError as e:
        bail(f"Package installation failed with code {e.returncode}.")

# ==============================================================================
# DYNAMIC IVSHMEM CALCULATION
# ==============================================================================
def calculate_kvmfr_size() -> Tuple[int, int]:
    """Interactively map SDR resolution targets to strict KVMFR sizing."""
    console.print("\n[bold blue]==>[/bold blue] [bold]SDR Resolution & IVSHMEM Memory Calculation[/bold]")
    
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Option", style="cyan", justify="center")
    table.add_column("SDR Resolution Target", style="green")
    table.add_column("Base Calculation", style="dim")
    table.add_column("Required KVMFR (MiB)", style="bold yellow")

    table.add_row("1", "1080p / 1200p", "16-18 MB + 10 MB Overhead", "32 MiB")
    table.add_row("2", "1440p (Recommended)", "29 MB + 10 MB Overhead", "64 MiB")
    table.add_row("3", "4K", "66 MB + 10 MB Overhead", "128 MiB")
    
    console.print(table)
    
    choice = Prompt.ask(
        "[bold cyan]Select your target SDR resolution[/bold cyan]", 
        choices=["1", "2", "3"], 
        default="2"
    )

    size_map = {"1": 32, "2": 64, "3": 128}
    mib_size = size_map[choice]
    byte_size = mib_size * 1024 * 1024
    
    console.print(f"[bold green]  ✓ Locked KVMFR size to {mib_size} MiB ({byte_size} bytes).[/bold green]")
    return mib_size, byte_size

# ==============================================================================
# HOST CONFIGURATION & RACE CONDITION PREVENTION
# ==============================================================================
def configure_host_modules(mib_size: int) -> None:
    """Idempotently configure modprobe, modules-load, and udev rules."""
    console.print("\n[bold blue]==>[/bold blue] [bold]Staging KVMFR Kernel Module & Udev Permissions...[/bold]")

    # 1. Modprobe configuration
    modprobe_path = Path("/etc/modprobe.d/kvmfr.conf")
    modprobe_content = f"# KVMFR Looking Glass — static IVSHMEM device size\noptions kvmfr static_size_mb={mib_size}\n"
    if atomic_write(modprobe_path, modprobe_content):
        console.print(f"[bold green]  ✓ Modprobe options enforced: {modprobe_path}[/bold green]")
    else:
        console.print(f"[bold green]  ✓ Modprobe options already optimal: {modprobe_path}[/bold green]")

    # 2. Modules-load configuration
    load_path = Path("/etc/modules-load.d/kvmfr.conf")
    load_content = "# Load KVMFR before any VM that uses it\nkvmfr\n"
    if atomic_write(load_path, load_content):
        console.print(f"[bold green]  ✓ Systemd module load enforced: {load_path}[/bold green]")
    else:
        console.print(f"[bold green]  ✓ Systemd module load already optimal: {load_path}[/bold green]")

    # 3. Udev Rules (Must sort before 73-seat-late.rules per systemd 260 spec)
    udev_path = Path("/etc/udev/rules.d/70-kvmfr.rules")
    udev_content = 'SUBSYSTEM=="kvmfr", GROUP="kvm", MODE="0660", TAG+="uaccess"\n'
    if atomic_write(udev_path, udev_content):
        console.print(f"[bold green]  ✓ Udev access controls enforced: {udev_path}[/bold green]")
    else:
        console.print(f"[bold green]  ✓ Udev access controls already optimal: {udev_path}[/bold green]")

    with console.status("[cyan]Triggering surgical udev rule reload...", spinner="dots"):
        run_cmd(["udevadm", "control", "--reload"])
        # Surgical trigger: only target the kvmfr subsystem to prevent micro-stutters
        run_cmd(["udevadm", "trigger", "--action=add", "--subsystem-match=kvmfr"])

def enforce_device_integrity() -> None:
    """Detects and mitigates the QEMU regular file creation race condition."""
    dev_path = Path("/dev/kvmfr0")
    console.print("\n[bold blue]==>[/bold blue] [bold]Verifying KVMFR DMA Integrity...[/bold]")
    
    if dev_path.exists():
        mode = dev_path.stat().st_mode
        if not stat.S_ISCHR(mode):
            console.print("[bold yellow]  ⚠ FATAL RACE DETECTED: /dev/kvmfr0 is a regular file, not a char device![/bold yellow]")
            console.print("[cyan]    Purging corrupted file...[/cyan]")
            dev_path.unlink()
    
    with console.status("[cyan]Injecting KVMFR into Ring 0...", spinner="dots"):
        # We pass check=False here to bypass the strict gatekeeper check in run_cmd.
        # This allows us to catch the missing module without assassinating the script.
        if run_cmd(["modprobe", "kvmfr"], check=False) == 0:
            console.print("[bold green]  ✓ KVMFR char device dynamically loaded and secured.[/bold green]")
        else:
            console.print("[bold yellow]  ⚠ KVMFR module failed to load. (DKMS build might be pending or requires a reboot).[/bold yellow]")

# ==============================================================================
# LIBVIRT CGROUP INJECTION
# ==============================================================================
def configure_qemu_cgroups() -> None:
    """
    Bulletproof Regex parsing to cleanly uncomment and inject /dev/kvmfr0.
    Utilizes [^\\\\]* to prevent multiline runaway regex crashes.
    """
    conf_path = Path("/etc/libvirt/qemu.conf")
    console.print("\n[bold blue]==>[/bold blue] [bold]Securing QEMU Cgroup Device ACLs...[/bold]")

    if not conf_path.exists():
        bail(f"Configuration file {conf_path} does not exist. Ensure libvirt is installed.")

    content = conf_path.read_text(encoding="utf-8")
    target_device = '"/dev/kvmfr0"'

    # Regex constraints explicitly bound inside the array braces
    pattern_active = re.compile(r'^\s*cgroup_device_acl\s*=\s*\[([^\]]*)\]', re.MULTILINE)
    pattern_commented = re.compile(r'^\s*#\s*cgroup_device_acl\s*=\s*\[([^\]]*)\]', re.MULTILINE)

    if match := pattern_active.search(content):
        inner = match.group(1)
        if target_device not in inner:
            clean_inner = inner.rstrip(" \n\r\t,")
            new_block = f"cgroup_device_acl = [{clean_inner},\n    {target_device}\n]"
            content = content[:match.start()] + new_block + content[match.end():]
            
    elif match := pattern_commented.search(content):
        inner = match.group(1)
        # Strip comments line-by-line to preserve structure perfectly
        uncommented_inner = "\n".join([line.lstrip(' \t#') for line in inner.splitlines()])
        clean_inner = uncommented_inner.rstrip(" \n\r\t,")
        new_block = f"cgroup_device_acl = [{clean_inner},\n    {target_device}\n]"
        content = content[:match.start()] + new_block + content[match.end():]
        
    else:
        # Failsafe fallback appended to EOF
        fallback_block = (
            '\ncgroup_device_acl = [\n'
            '    "/dev/null", "/dev/full", "/dev/zero",\n'
            '    "/dev/random", "/dev/urandom",\n'
            '    "/dev/ptmx", "/dev/kvm", "/dev/kqemu",\n'
            '    "/dev/rtc","/dev/hpet", "/dev/sev",\n'
            f'    {target_device}\n]\n'
        )
        content += fallback_block

    if atomic_write(conf_path, content):
        console.print("[bold green]  ✓ qemu.conf strictly parsed and injected with KVMFR ACL.[/bold green]")
        with console.status("[cyan]Restarting Libvirt modular daemons...", spinner="dots"):
            # Target modular daemons dynamically based on Phase 2 architecture
            run_cmd(["systemctl", "restart", "virtqemud.socket", "virtqemud.service"])
        console.print("[bold green]  ✓ virtqemud service/socket restarted successfully.[/bold green]")
    else:
        console.print("[bold green]  ✓ qemu.conf already whitelists /dev/kvmfr0 perfectly. No changes made.[/bold green]")

# ==============================================================================
# MAIN EXECUTION & XML OUTPUT
# ==============================================================================
# ==============================================================================
# VM XML AUTOMATION
# ==============================================================================
def get_all_vms() -> list[Tuple[str, str]]:
    """Query libvirt for all defined virtual machines and their states."""
    try:
        res = subprocess.run(
            ["virsh", "-c", "qemu:///system", "list", "--all"],
            capture_output=True, text=True, check=True
        )
        vms = []
        for line in res.stdout.strip().splitlines()[2:]:
            parts = line.split()
            if len(parts) >= 3:
                name = parts[1]
                state = " ".join(parts[2:])
                vms.append((name, state))
            elif len(parts) == 2:
                name = parts[0]
                state = parts[1]
                vms.append((name, state))
        return vms
    except Exception as e:
        console.print(f"[yellow]⚠ Failed to query libvirt VMs: {e}[/yellow]")
        return []

def inject_kvmfr_into_xml(xml_str: str, byte_size: int) -> str:
    """Safely and programmatically injects Looking Glass KVMFR configuration and optimizes CPU topology into VM XML."""
    qemu_ns = "http://libvirt.org/schemas/domain/qemu/1.0"
    ET.register_namespace('qemu', qemu_ns)
    root = ET.fromstring(xml_str)
    
    # 1. Optimize CPU topology to resolve hyperthreading warning & socket limits
    vcpu_elem = root.find('vcpu')
    vcpu_count = 1
    if vcpu_elem is not None and vcpu_elem.text:
        try:
            vcpu_count = int(vcpu_elem.text.strip())
        except ValueError:
            pass
            
    if vcpu_count % 2 == 0:
        sockets = 1
        cores = vcpu_count // 2
        threads = 2
    else:
        sockets = 1
        cores = vcpu_count
        threads = 1
        
    cpu_elem = root.find('cpu')
    if cpu_elem is None:
        cpu_elem = ET.SubElement(root, 'cpu', mode='host-passthrough', check='none', migratable='on')
        
    topology = cpu_elem.find('topology')
    if topology is None:
        topology = ET.SubElement(cpu_elem, 'topology')
        
    topology.set('sockets', str(sockets))
    topology.set('dies', '1')
    topology.set('cores', str(cores))
    topology.set('threads', str(threads))
    console.print(f"[bold green]  ✓ CPU Topology optimized: {sockets} socket(s), {cores} core(s), {threads} thread(s) (matches {vcpu_count} vCPUs).[/bold green]")
    
    # 2. Nullify memballoon to guarantee zero DMA latency
    devices = root.find('devices')
    if devices is not None:
        balloon = devices.find('memballoon')
        if balloon is not None:
            balloon.set('model', 'none')
            console.print("[bold green]  ✓ Latency-inducing memballoon nullified.[/bold green]")
        else:
            balloon = ET.SubElement(devices, 'memballoon', model='none')
            console.print("[bold green]  ✓ Latency-inducing memballoon nullified (created none).[/bold green]")

        # Check and inject SPICE agent channel for clipboard sharing
        has_spice_channel = False
        for channel in devices.findall('channel'):
            if channel.get('type') == 'spicevmc':
                target = channel.find('target')
                if target is not None and target.get('name') == 'com.redhat.spice.0':
                    has_spice_channel = True
                    break
        if not has_spice_channel:
            spice_channel = ET.SubElement(devices, 'channel', type='spicevmc')
            ET.SubElement(spice_channel, 'target', type='virtio', name='com.redhat.spice.0')
            console.print("[bold green]  ✓ SPICE guest agent channel injected for clipboard synchronization.[/bold green]")

    # 3. Add or update <qemu:commandline>
    qemu_cmd = root.find(f"{{{qemu_ns}}}commandline")
    target_args = [
        ("-device", "{'driver':'ivshmem-plain','id':'shmem0','memdev':'looking-glass'}"),
        ("-object", f"{{'qom-type':'memory-backend-file','id':'looking-glass','mem-path':'/dev/kvmfr0','size':{byte_size},'share':true}}")
    ]
    
    if qemu_cmd is None:
        qemu_cmd = ET.Element(f"{{{qemu_ns}}}commandline")
        root.append(qemu_cmd)
        
    args = qemu_cmd.findall(f"{{{qemu_ns}}}arg")
    new_args = []
    skip_next = False
    for i, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        val = arg.get('value', '')
        if val in ('-device', '-object'):
            if i + 1 < len(args):
                next_val = args[i+1].get('value', '')
                if 'looking-glass' in next_val or 'kvmfr' in next_val:
                    skip_next = True
                    continue
        if 'looking-glass' in val or 'kvmfr' in val:
            continue
        new_args.append(arg)
        
    # Clear old args
    for arg in list(qemu_cmd):
        qemu_cmd.remove(arg)
        
    # Put back filtered args
    for arg in new_args:
        qemu_cmd.append(arg)
        
    # Append the new looking-glass args
    for arg_type, arg_val in target_args:
        ET.SubElement(qemu_cmd, f"{{{qemu_ns}}}arg", value=arg_type)
        ET.SubElement(qemu_cmd, f"{{{qemu_ns}}}arg", value=arg_val)
        
    console.print(f"[bold green]  ✓ KVMFR payload injected/updated successfully.[/bold green]")
    
    if hasattr(ET, 'indent'):
        ET.indent(root, space="  ", level=0)
    return ET.tostring(root, encoding='unicode')

def configure_vm_xml(vm_name: str, byte_size: int) -> bool:
    """Retrieve VM XML, apply edits, and redefine VM in libvirt."""
    console.print(f"\n[bold blue]==>[/bold blue] [bold]Configuring VM '{vm_name}' XML...[/bold]")
    try:
        res = subprocess.run(
            ["virsh", "-c", "qemu:///system", "dumpxml", "--inactive", vm_name],
            capture_output=True, text=True, check=True
        )
        xml_old = res.stdout
        xml_new = inject_kvmfr_into_xml(xml_old, byte_size)
        
        fd, tmp_path_str = tempfile.mkstemp(prefix=f"kvm-{vm_name}-", suffix=".xml")
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(xml_new)
            
            subprocess.run(
                ["virsh", "-c", "qemu:///system", "define", str(tmp_path)],
                check=True, stdout=subprocess.DEVNULL
            )
            console.print(f"[bold green]  ✓ VM '{vm_name}' configuration updated in libvirt.[/bold green]")
            return True
        finally:
            if tmp_path.exists():
                tmp_path.unlink()
    except Exception as e:
        console.print(f"[bold red]  ✖ Failed to edit/redefine VM XML: {e}[/bold red]")
        return False

def interactively_configure_vm(byte_size: int) -> None:
    """Detect VMs, prompt user, and apply edits."""
    vms = get_all_vms()
    if not vms:
        console.print("\n[yellow]⚠ No existing KVM VMs detected on the system.[/yellow]")
        return
        
    console.print("\n[bold cyan]Select an existing VM to automatically inject Looking Glass settings:[/bold cyan]")
    
    choices = []
    for idx, (name, state) in enumerate(vms):
        opt = str(idx + 1)
        console.print(f"  [{opt}] {name} [dim]({state})[/dim]")
        choices.append(opt)
        
    skip_opt = str(len(vms) + 1)
    console.print(f"  [{skip_opt}] Skip automatic XML editing (Show manual instructions instead)")
    choices.append(skip_opt)
    
    custom_opt = str(len(vms) + 2)
    console.print(f"  [{custom_opt}] Enter a custom VM name manually")
    choices.append(custom_opt)
    
    choice = Prompt.ask("\nChoice", choices=choices, default="1")
    
    if choice == skip_opt:
        console.print("[yellow]Skipping automatic VM editing.[/yellow]")
        return
    elif choice == custom_opt:
        vm_name = Prompt.ask("Enter custom VM name").strip()
        if not vm_name:
            console.print("[red]Invalid name. Skipping VM editing.[/red]")
            return
        state = "unknown"
    else:
        idx = int(choice) - 1
        vm_name, state = vms[idx]
        
    success = configure_vm_xml(vm_name, byte_size)
    if success and state == "running":
        console.print(Panel(
            f"[bold yellow]WARNING:[/bold yellow] VM '{vm_name}' is currently running.\n"
            "You must completely shutdown and power cycle the VM for the new XML settings to take effect.",
            border_style="yellow"
        ))

def main() -> None:
    console.clear()
    console.print(Panel("[bold green]Phase 5: KVMFR Host Configuration[/bold green]\nTarget: Arch Linux | Kernel 7.1.0+ | systemd 260", expand=False))
    
    try:
        target_user = resolve_target_user()
        install_looking_glass_packages(target_user)
        
        mib_size, byte_size = calculate_kvmfr_size()
        configure_host_modules(mib_size)
        enforce_device_integrity()
        configure_qemu_cgroups()
        
        # Interactively configure VM XML
        interactively_configure_vm(byte_size)
        
        # Absolute correct QOM JSON formatting for QEMU commandline mapping
        xml_payload = f"""  <qemu:commandline>
    <qemu:arg value="-device"/>
    <qemu:arg value="{{'driver':'ivshmem-plain','id':'shmem0','memdev':'looking-glass'}}"/>
    <qemu:arg value="-object"/>
    <qemu:arg value="{{'qom-type':'memory-backend-file','id':'looking-glass','mem-path':'/dev/kvmfr0','size':{byte_size},'share':true}}"/>
  </qemu:commandline>"""

        console.print("\n[bold green]=== PHASE 5 COMPLETE ===[/bold green]")
        console.print("The host kernel environment, udev rules, and QEMU cgroups are fully staged.")
        
        console.print("\n[bold yellow]MANUAL XML FALLBACK REFERENCE (if needed):[/bold yellow]")
        console.print("  [cyan]1.[/cyan] Change the first line of VM XML (virsh edit <vm>) to: [bold]<domain type='kvm' xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>[/bold]")
        console.print("  [cyan]2.[/cyan] Find your memory balloon and disable it to prevent DMA latency: [bold]<memballoon model='none'/>[/bold]")
        console.print("  [cyan]3.[/cyan] Paste the following block at the absolute bottom of the file, just before [bold]</domain>[/bold]:\n")
        
        # SURGICAL FIX: Bypass 'rich' entirely for the payload.
        # Printing directly to standard output prevents the library from injecting
        # artificial line breaks or box-drawing characters that complicate copy-pasting.
        console.print("[cyan]━━━━━━━━━━━━━━━━━━━━━━━━━ libvirt QOM JSON Payload ━━━━━━━━━━━━━━━━━━━━━━━━━[/cyan]")
        print(xml_payload)
        console.print("[cyan]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/cyan]\n")

    except KeyboardInterrupt:
        console.print("\n\n[bold red]⚠ Process interrupted by operator. Exiting cleanly.[/bold red]\n")
        sys.exit(130)

if __name__ == "__main__":
    main()
