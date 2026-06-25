#!/usr/bin/env python3
"""
Phase 6: Automated KVM XML Architect & KVMFR Injection
Target: Arch Linux (Kernel 7.1.0+), Python 3.14+, systemd 260
Scope: virt-install baseline generation, Python XML DOM injection, Libvirt provisioning.
Philosophy: Complete automation, zero manual virsh edits, dynamic hardware scaling.
"""

import os
import sys
import re
import json
import shutil
import tempfile
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

def require_root() -> None:
    """Enforce eUID 0. Auto-elevates via sudo if executed as standard user."""
    if os.geteuid() != 0:
        print("\n[INFO] Elevating privileges via sudo...")
        os.execvp("sudo", ["sudo", sys.executable] + sys.argv)

require_root()

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
except ImportError:
    print("FATAL: python-rich is missing.")
    sys.exit(1)

console = Console()

# ==============================================================================
# CORE LOGIC
# ==============================================================================
def get_storage_target() -> Path:
    state_file = Path("/tmp/kvm_storage_state.json")
    default_path = Path("/var/lib/libvirt/images")
    
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text(encoding='utf-8'))
            return Path(data.get("KVM_TARGET_DIR", str(default_path)))
        except json.JSONDecodeError:
            pass
    return default_path

def discover_active_network() -> str:
    """Dynamically determine whether to use 'host-bridge' or 'default' NAT."""
    try:
        res = subprocess.run(["virsh", "net-list", "--name", "--state-active"], capture_output=True, text=True, check=True)
        networks = res.stdout.split()
        if "host-bridge" in networks:
            return "host-bridge"
        return "default"
    except subprocess.CalledProcessError:
        return "default"

def provision_qcow2(disk_path: Path, size_gib: int) -> None:
    """Provisions the disk image, handling overwrite confirmations safely."""
    console.print(f"\n[bold blue]==>[/bold blue] [bold]Provisioning Virtual Storage...[/bold]")
    if disk_path.exists():
        if not Confirm.ask(f"[yellow]Disk {disk_path.name} already exists. Overwrite?[/yellow]", default=False):
            console.print("[cyan]Re-using existing virtual disk.[/cyan]")
            return
        disk_path.unlink()
        
    with console.status(f"[cyan]Allocating {size_gib}GiB qcow2 backing store...", spinner="dots"):
        subprocess.run(["qemu-img", "create", "-f", "qcow2", str(disk_path), f"{size_gib}G"], check=True, stdout=subprocess.DEVNULL)
        
    # Enforce QEMU ownership (Failure is non-fatal if ACLs from Phase 1.5 are active)
    try:
        shutil.chown(disk_path, user="qemu", group="qemu")
    except LookupError:
        pass
    console.print(f"[bold green]  ✓ Virtual disk provisioned: {disk_path}[/bold green]")

def build_baseline_xml(vm_name: str, os_choice: str, gpu_choice: str, ram_mib: int, vcpu_count: int, disk_path: Path, network: str) -> str:
    """Generates a perfect hypervisor-compliant baseline XML using virt-install."""
    console.print("\n[bold blue]==>[/bold blue] [bold]Compiling Virt-Install Baseline...[/bold]")
    
    os_variant = "win11" if os_choice == "2" else "archlinux"
    
    cmd = [
        "virt-install",
        "--name", vm_name,
        "--memory", str(ram_mib),
        "--vcpus", str(vcpu_count),
        "--os-variant", os_variant,
        "--boot", "uefi",
        "--disk", f"path={disk_path},format=qcow2,bus=virtio,cache=none,discard=unmap",
        "--disk", "device=cdrom,bus=sata",
        "--network", f"network={network},model=virtio",
        "--channel", "spicevmc,target.type=virtio,target.name=com.redhat.spice.0",
        "--print-xml"
    ]
    
    if os_choice == "2": # Windows Specific Enhancements
        cmd.extend(["--disk", "device=cdrom,bus=sata"]) # Secondary for VirtIO Drivers
        cmd.extend(["--features", "hyperv_relaxed=on,hyperv_vapic=on,hyperv_spinlocks=on"])

    match gpu_choice:
        case "1":
            cmd.extend(["--graphics", "spice", "--video", "virtio"])
        case "2":
            cmd.extend(["--graphics", "spice,gl.enable=yes,listen=none", "--video", "virtio,accel3d=yes"])
        case "3":
            console.print("[cyan]Querying local PCIe topology for Passthrough...[/cyan]")
            subprocess.run("lspci -nn | grep -iE 'vga|3d|audio'", shell=True)
            console.print("\n[dim]Enter the Bus/Slot ID of the isolated GPU (e.g., 01:00). Functions 0 and 1 will be attached automatically.[/dim]")
            pci_prefix = Prompt.ask("[bold cyan]Target GPU PCIe ID[/bold cyan]")
            
            bus, slot = pci_prefix.replace('.', ':').split(':')[:2]
            cmd.extend(["--hostdev", f"pci_0000_{bus}_{slot}_0"])
            cmd.extend(["--hostdev", f"pci_0000_{bus}_{slot}_1"])
            cmd.extend(["--video", "none", "--graphics", "none"])

    with console.status("[cyan]Generating XML topology...", spinner="dots"):
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        console.print("[bold green]  ✓ Baseline XML generated natively.[/bold green]")
        return res.stdout

def inject_kvmfr_payload(xml_str: str, kvmfr_mib: int) -> str:
    """Programmatically intercepts and injects Looking Glass parameters into the XML DOM."""
    console.print("\n[bold blue]==>[/bold blue] [bold]Executing QOM JSON Payload Injection...[/bold]")
    
    # 1. Bulletproof Regex to secure the QEMU namespace on the root domain tag
    qemu_ns = "http://libvirt.org/schemas/domain/qemu/1.0"
    if "xmlns:qemu=" not in xml_str:
        xml_str = re.sub(r'<domain type=[\'"]kvm[\'"]>', f"<domain type='kvm' xmlns:qemu='{qemu_ns}'>", xml_str, count=1)

    # Register namespace to ensure elegant serialization
    ET.register_namespace('qemu', qemu_ns)
    root = ET.fromstring(xml_str)
    
    # 2. Obliterate memballoon to guarantee zero DMA latency
    for devices in root.findall('devices'):
        for balloon in devices.findall('memballoon'):
            balloon.set('model', 'none')
            console.print("[bold green]  ✓ Latency-inducing memballoon nullified.[/bold green]")

    # 3. Inject Phase 5 KVMFR `<qemu:commandline>` payload
    kvmfr_bytes = kvmfr_mib * 1024 * 1024
    qemu_cmd = ET.Element(f"{{{qemu_ns}}}commandline")
    
    # Maintain exact JSON string dict formats required by QEMU argument parsers
    ET.SubElement(qemu_cmd, f"{{{qemu_ns}}}arg", value="-device")
    ET.SubElement(qemu_cmd, f"{{{qemu_ns}}}arg", value="{'driver':'ivshmem-plain','id':'shmem0','memdev':'looking-glass'}")
    ET.SubElement(qemu_cmd, f"{{{qemu_ns}}}arg", value="-object")
    ET.SubElement(qemu_cmd, f"{{{qemu_ns}}}arg", value=f"{{'qom-type':'memory-backend-file','id':'looking-glass','mem-path':'/dev/kvmfr0','size':{kvmfr_bytes},'share':true}}")
    
    root.append(qemu_cmd)
    console.print(f"[bold green]  ✓ KVMFR payload ({kvmfr_mib} MiB) injected successfully.[/bold green]")
    
    # Pretty serialization
    if hasattr(ET, 'indent'):
        ET.indent(root, space="  ", level=0)
    return ET.tostring(root, encoding='unicode')

def main() -> None:
    console.clear()
    console.print(Panel("[bold green]Phase 6: Automated VM Deployment[/bold green]\nTarget: Arch Linux | Kernel 7.1.0+", expand=False))

    if not Confirm.ask("\nDo you want to deploy a new virtual machine?", default=False):
        console.print("[yellow]Skipping VM deployment phase.[/yellow]")
        return

    target_dir = get_storage_target()
    active_network = discover_active_network()
    
    vm_name = Prompt.ask("\nEnter Virtual Machine Name", default="archlinux")
    
    console.print("\n[bold cyan]Select Operating System Payload:[/bold cyan]")
    console.print("  [1] Arch Linux (Bleeding Edge)")
    console.print("  [2] Windows 10 / 11 (Hyper-V Enlightened)")
    os_choice = Prompt.ask("Choice", choices=["1", "2"], default="1")
    
    console.print("\n[bold cyan]Select Graphics Topology:[/bold cyan]")
    console.print("  [1] Basic QXL / Virtio 2D")
    console.print("  [2] 3D Accelerated (Virgil / OpenGL)")
    console.print("  [3] GPU Passthrough (VFIO + Looking Glass)")
    gpu_choice = Prompt.ask("Choice", choices=["1", "2", "3"], default="1")
    
    ram_gib = int(Prompt.ask("\nEnter RAM size in GiB", default="8"))
    vcpu_count = int(Prompt.ask("Enter vCPU Core Count", default="6"))
    disk_gib = int(Prompt.ask("Enter Disk Size in GiB", default="50"))
    
    disk_path = target_dir / f"{vm_name}.qcow2"
    provision_qcow2(disk_path, disk_gib)
    
    xml_payload = build_baseline_xml(vm_name, os_choice, gpu_choice, ram_gib * 1024, vcpu_count, disk_path, active_network)
    
    if gpu_choice == "3":
        kvmfr_mib = int(Prompt.ask("\n[bold yellow]Enter the KVMFR size defined in Phase 5 (MiB)[/bold yellow]", choices=["32", "64", "128"], default="64"))
        xml_payload = inject_kvmfr_payload(xml_payload, kvmfr_mib)

    # Atomic Libvirt Definition
    console.print("\n[bold blue]==>[/bold blue] [bold]Registering Virtual Machine...[/bold]")
    fd, tmp_path_str = tempfile.mkstemp(prefix=f"kvm-{vm_name}-", suffix=".xml")
    tmp_path = Path(tmp_path_str)
    
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(xml_payload)
            
        subprocess.run(["virsh", "-c", "qemu:///system", "define", str(tmp_path)], check=True, stdout=subprocess.DEVNULL)
        console.print(f"[bold green]  ✓ Virtual Machine '{vm_name}' compiled, verified, and defined in Libvirt![/bold green]")
        
        console.print("\n[bold yellow]NEXT STEPS:[/bold yellow]")
        if os_choice == "2":
            console.print("  [cyan]1.[/cyan] Open Virt-Manager.")
            console.print("  [cyan]2.[/cyan] Attach Windows ISO to SATA CDROM 1.")
            console.print("  [cyan]3.[/cyan] Attach virtio-win.iso to SATA CDROM 2 and boot.")
        else:
            console.print("  [cyan]1.[/cyan] Open Virt-Manager.")
            console.print("  [cyan]2.[/cyan] Attach Linux ISO to SATA CDROM and boot.")
            
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]FATAL: Libvirt rejected the XML payload. Exit Code: {e.returncode}[/bold red]")
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

if __name__ == "__main__":
    main()
