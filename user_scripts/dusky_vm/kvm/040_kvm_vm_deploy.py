#!/usr/bin/env python3
# ==============================================================================
# 04_vm_deploy.py
# Purpose: Dynamic KVM XML architect. Uses native ElementTree to build a perfectly 
#          optimized hardware topology based on OS type and GPU logic.
# Target: Python 3.14+ (Arch Linux Strict)
# ==============================================================================
import os
import sys
import json
import uuid
import secrets
import subprocess
import threading
import time
import pwd
import grp
import xml.etree.ElementTree as ET
from pathlib import Path

# ANSI Escape Sequences
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

def get_storage_target() -> Path:
    state_file = Path("/tmp/kvm_storage_state.json")
    default_path = Path("/var/lib/libvirt/images")
    
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text(encoding='utf-8'))
            target = Path(data.get("KVM_TARGET_DIR", str(default_path)))
            log_info(f"Loaded storage environment: {target}")
            return target
        except json.JSONDecodeError:
            log_warn("State file corrupted. Defaulting to standard path.")
    else:
        log_warn(f"Storage environment file not found. Defaulting to {default_path}")
    
    return default_path

def chown_qemu(target_path: Path) -> None:
    """Natively binds file ownership to the QEMU hypervisor daemon."""
    try:
        qemu_uid = pwd.getpwnam('qemu').pw_uid
        qemu_gid = grp.getgrnam('qemu').gr_gid
        os.chown(target_path, qemu_uid, qemu_gid)
    except KeyError:
        log_warn("User/Group 'qemu' not found in system registry. Skipping chown.")
    except PermissionError:
        log_warn(f"Kernel denied ownership transfer for {target_path}.")

def add_elem(parent: ET.Element, tag: str, text: str | None = None, **attrib) -> ET.Element:
    """Helper method to cleanly build XML sub-elements."""
    elem = ET.SubElement(parent, tag, **attrib)
    if text is not None:
        elem.text = str(text)
    return elem

def generate_mac_address() -> str:
    """Generates a KVM-compliant MAC address using cryptographic entropy."""
    return f"52:54:00:{secrets.randbelow(256):02x}:{secrets.randbelow(256):02x}:{secrets.randbelow(256):02x}"

def build_domain_xml(
    vm_name: str, os_choice: str, gpu_choice: str, 
    ram_kib: int, vcpu_count: int, disk_path: Path
) -> str:
    """Constructs the XML DOM programmatically for pristine formatting."""
    is_windows = (os_choice == "2")
    vm_uuid = str(uuid.uuid4())
    mac_addr = generate_mac_address()

    # Base Domain
    domain = ET.Element('domain', type='kvm')
    add_elem(domain, 'name', text=vm_name)
    add_elem(domain, 'uuid', text=vm_uuid)
    add_elem(domain, 'memory', text=str(ram_kib), unit='KiB')
    add_elem(domain, 'currentMemory', text=str(ram_kib), unit='KiB')
    
    mem_backing = add_elem(domain, 'memoryBacking')
    add_elem(mem_backing, 'source', type='memfd')
    add_elem(mem_backing, 'access', mode='shared')
    
    add_elem(domain, 'vcpu', text=str(vcpu_count), placement='static')
    
    # OS Firmware & Boot
    os_elem = add_elem(domain, 'os', firmware='efi')
    add_elem(os_elem, 'type', text='hvm', arch='x86_64', machine='q35')
    add_elem(os_elem, 'boot', dev='hd')
    add_elem(os_elem, 'boot', dev='cdrom')

    # CPU & Features
    features = add_elem(domain, 'features')
    add_elem(features, 'acpi')
    add_elem(features, 'apic')
    add_elem(features, 'vmport', state='off')
    
    if is_windows:
        hyperv = add_elem(features, 'hyperv')
        for hv_feat in ['relaxed', 'vapic', 'vpindex', 'runtime', 'synic', 'stimer', 'frequencies', 'tlbflush', 'ipi', 'evmcs', 'avic']:
            add_elem(hyperv, hv_feat, state='on')
        add_elem(hyperv, 'spinlocks', state='on', retries='8191')

    add_elem(domain, 'cpu', mode='host-passthrough', check='none', migratable='on')

    # Clock Config
    clock = add_elem(domain, 'clock', offset=('localtime' if is_windows else 'utc'))
    add_elem(clock, 'timer', name='rtc', tickpolicy='catchup')
    add_elem(clock, 'timer', name='pit', tickpolicy='delay')
    add_elem(clock, 'timer', name='hpet', present='no')
    if is_windows:
        add_elem(clock, 'timer', name='hypervclock', present='yes')

    # Power Management
    pm = add_elem(domain, 'pm')
    add_elem(pm, 'suspend-to-mem', enabled='no')
    add_elem(pm, 'suspend-to-disk', enabled='no')

    # Devices Block
    devices = add_elem(domain, 'devices')
    add_elem(devices, 'emulator', text='/usr/bin/qemu-system-x86_64')
    
    # Storage Devices
    disk = add_elem(devices, 'disk', type='file', device='disk')
    add_elem(disk, 'driver', name='qemu', type='qcow2', cache='none', discard='unmap')
    add_elem(disk, 'source', file=str(disk_path))
    add_elem(disk, 'target', dev='vda', bus='virtio')

    cd1 = add_elem(devices, 'disk', type='file', device='cdrom')
    add_elem(cd1, 'target', dev='sda', bus='sata')
    add_elem(cd1, 'readonly')
    
    if is_windows:
        cd2 = add_elem(devices, 'disk', type='file', device='cdrom')
        add_elem(cd2, 'target', dev='sdb', bus='sata')
        add_elem(cd2, 'readonly')

    # General Controllers & Network
    add_elem(devices, 'controller', type='usb', model='qemu-xhci', ports='15')
    add_elem(devices, 'controller', type='pci', model='pcie-root')
    
    iface = add_elem(devices, 'interface', type='bridge')
    add_elem(iface, 'mac', address=mac_addr)
    add_elem(iface, 'source', bridge='virbr0')
    add_elem(iface, 'model', type='virtio')

    # System Channels & Input
    ch1 = add_elem(devices, 'channel', type='unix')
    add_elem(ch1, 'target', type='virtio', name='org.qemu.guest_agent.0')
    ch2 = add_elem(devices, 'channel', type='spicevmc')
    add_elem(ch2, 'target', type='virtio', name='com.redhat.spice.0')
    
    add_elem(devices, 'input', type='tablet', bus='usb')
    add_elem(devices, 'input', type='mouse', bus='ps2')
    add_elem(devices, 'input', type='keyboard', bus='ps2')
    add_elem(devices, 'sound', model='ich9')
    
    rng = add_elem(devices, 'rng', model='virtio')
    add_elem(rng, 'backend', model='random', text='/dev/urandom')

    if is_windows:
        tpm = add_elem(devices, 'tpm', model='tpm-crb')
        add_elem(tpm, 'backend', type='emulator', version='2.0')

    # Graphics Topology
    match gpu_choice:
        case "1":
            gfx = add_elem(devices, 'graphics', type='spice', port='-1', autoport='yes')
            add_elem(gfx, 'image', compression='off')
            vid = add_elem(devices, 'video')
            add_elem(vid, 'model', type='virtio')
        case "2":
            gfx = add_elem(devices, 'graphics', type='spice')
            add_elem(gfx, 'listen', type='none')
            add_elem(gfx, 'image', compression='off')
            add_elem(gfx, 'gl', enable='yes', rendernode='/dev/dri/renderD128')
            vid = add_elem(devices, 'video')
            add_elem(vid, 'model', type='virtio', heads='1', primary='yes').append(ET.Element('acceleration', accel3d='yes'))
        case "3":
            vid = add_elem(devices, 'video')
            add_elem(vid, 'model', type='none')
            print(f"\n{YELLOW}GPU Passthrough Configuration{NC}")
            log_info("Run 'lspci' in another terminal to find bus IDs. Assuming func 0x0 (Video) and 0x1 (Audio).")
            pci_bus = input("Enter PCI Bus ID (e.g., '01'): ").strip()
            pci_slot = input("Enter PCI Slot ID (e.g., '00'): ").strip()
            
            for func in ['0x0', '0x1']:
                hostdev = add_elem(devices, 'hostdev', mode='subsystem', type='pci', managed='yes')
                add_elem(hostdev, 'source').append(
                    ET.Element('address', domain='0x0000', bus=f'0x{pci_bus}', slot=f'0x{pci_slot}', function=func)
                )
            
            shmem = add_elem(devices, 'shmem', name='looking-glass')
            add_elem(shmem, 'model', type='ivshmem-plain')
            add_elem(shmem, 'size', unit='M', text='32')

    # Native Python 3.9+ XML in-place indentation
    ET.indent(domain, space="  ", level=0)
    return ET.tostring(domain, encoding='unicode')

def main() -> None:
    validate_environment()
    
    print(f"{CYAN}===================================================={NC}")
    print(f"{CYAN}          Intelligent VM Provisioning Engine        {NC}")
    print(f"{CYAN}===================================================={NC}")

    target_dir = get_storage_target()
    
    vm_name = input("\nEnter Virtual Machine Name (Default: archlinux): ").strip() or "archlinux"
    
    print("\nSelect Operating System:")
    print("  [1] Arch Linux (Bleeding Edge)")
    print("  [2] Windows 10 / 11 (Includes TPM 2.0 & Hyper-V Enlightenments)")
    os_choice = input("Choice [1-2] (Default: 1): ").strip() or "1"
    
    print("\nSelect Graphics / GPU Topology:")
    print("  [1] Basic / Simple (Standard QXL or Virtio 2D)")
    print("  [2] GPU Acceleration (Virtio 3D with Virgil / OpenGL)")
    print("  [3] GPU Passthrough (VFIO Isolation + Looking Glass Shmem)")
    gpu_choice = input("Choice [1-3] (Default: 1): ").strip() or "1"
    
    try:
        ram_gib = int(input("Enter RAM size in GiB (Default: 8): ").strip() or 8)
        vcpu_count = int(input("Enter vCPU Core Count (Default: 6): ").strip() or 6)
        disk_gib = int(input("Enter Disk Size in GiB (Default: 50): ").strip() or 50)
    except ValueError:
        log_error("Invalid integer input provided.")
        
    ram_kib = ram_gib * 1024 * 1024
    disk_path = target_dir / f"{vm_name}.qcow2"
    
    # Provision Disk
    log_info(f"Provisioning virtual disk at: {disk_path}")
    subprocess.run(["qemu-img", "create", "-f", "qcow2", str(disk_path), f"{disk_gib}G"], check=True, stdout=subprocess.DEVNULL)
    
    # Enforce kernel-level permissions dynamically
    chown_qemu(disk_path)
    
    # Assemble XML
    log_info("Assembling dynamic XML DOM...")
    xml_payload = build_domain_xml(vm_name, os_choice, gpu_choice, ram_kib, vcpu_count, disk_path)
    
    xml_file = Path(f"/tmp/{vm_name}_deploy.xml")
    xml_file.write_text(xml_payload, encoding='utf-8')
    
    # Libvirt Definition
    log_info("Defining VM in libvirt from generated payload...")
    process = subprocess.run(["virsh", "-c", "qemu:///system", "define", str(xml_file)], capture_output=True, text=True)
    
    if process.returncode == 0:
        xml_file.unlink() # Cleanup only on success
        log_success(f"Virtual Machine '{vm_name}' successfully configured and imported!")
        if os_choice == "2":
            print(f"{YELLOW}Note: Open Virt-Manager, attach your Windows ISO to SATA CDROM 1, and the virtio-win.iso to SATA CDROM 2.{NC}")
        else:
            print(f"{YELLOW}Note: Open Virt-Manager, attach your Linux ISO to the SATA CDROM, and begin OS installation.{NC}")
    else:
        log_error(f"Failed to define VM.\nStderr: {process.stderr.strip()}")

if __name__ == "__main__":
    main()
