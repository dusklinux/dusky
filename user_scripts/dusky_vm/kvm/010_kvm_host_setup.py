#!/usr/bin/env python3
# ==============================================================================
# 01_host_setup.py
# Purpose: Prepares an Arch Linux host for KVM/QEMU virtualization using modern
#          modular daemons and bleeding-edge dependencies.
# Target: Python 3.14+ 
# ==============================================================================
import os
import sys
import subprocess
import threading
import time
from pathlib import Path

# ANSI Escape Sequences
CYAN, GREEN, YELLOW, RED, NC = '\033[1;36m', '\033[1;32m', '\033[1;33m', '\033[1;31m', '\033[0m'

def log_info(msg: str) -> None: print(f"{CYAN}[INFO]{NC} {msg}")
def log_success(msg: str) -> None: print(f"{GREEN}[SUCCESS]{NC} {msg}")
def log_warn(msg: str) -> None: print(f"{YELLOW}[WARN]{NC} {msg}")
def log_error(msg: str) -> None:
    print(f"{RED}[ERROR]{NC} {msg}")
    sys.exit(1)

def run_cmd(cmd: list[str], ignore_errors: bool = False) -> subprocess.CompletedProcess[str] | None:
    """Executes a system command with strict standard stream handling."""
    try:
        return subprocess.run(cmd, check=not ignore_errors, text=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        if not ignore_errors:
            log_error(f"Command failed: {' '.join(cmd)}\nStderr: {e.stderr.strip()}")
        return None

def sudo_keep_alive() -> None:
    """Daemon thread to refresh sudo credentials invisibly."""
    while True:
        subprocess.run(["sudo", "-n", "true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        time.sleep(60)

def validate_environment() -> None:
    if os.geteuid() != 0:
        log_error("Root privileges required. Execute via sudo.")
    threading.Thread(target=sudo_keep_alive, daemon=True).start()

def verify_hardware() -> None:
    log_info("Verifying hardware virtualization support...")
    try:
        cpuinfo = Path('/proc/cpuinfo').read_text()
        if not any(flag in cpuinfo for flag in ('vmx', 'svm')):
            log_error("Hardware virtualization is disabled in BIOS/UEFI or unsupported.")
        log_success("Hardware virtualization enabled at CPU level.")
    except FileNotFoundError:
        log_error("Kernel state unreadable. /proc/cpuinfo missing.")

def install_packages() -> None:
    log_info("Synchronizing virtualization dependencies via pacman...")
    packages = [
        "qemu-full", "virt-manager", "libvirt", "dnsmasq", "iptables-nft", 
        "edk2-ovmf", "virt-viewer", "swtpm", "openbsd-netcat", "libosinfo"
    ]
    run_cmd(["pacman", "-Syu", "--needed", "--noconfirm", *packages])
    log_success("Virtualization dependencies synchronized.")

def configure_daemons() -> None:
    log_info("Enforcing modular libvirt daemons...")
    services_to_mask = [
        "libvirtd.socket", "libvirtd-ro.socket", "libvirtd-admin.socket", 
        "libvirtd-tls.socket", "libvirtd-tcp.socket", "libvirtd.service"
    ]
    run_cmd(["systemctl", "mask", *services_to_mask], ignore_errors=True)
    
    sockets_to_enable = [
        "virtqemud.socket", "virtnetworkd.socket", "virtstoraged.socket", 
        "virtnodedevd.socket", "virtproxyd.socket", "virtsecretd.socket", "virtnwfilterd.socket"
    ]
    run_cmd(["systemctl", "enable", "--now", *sockets_to_enable])
    log_success("Modular libvirt daemons activated.")

def configure_network() -> None:
    log_info("Configuring NAT bridging (virbr0)...")
    run_cmd(["virsh", "-c", "qemu:///system", "net-autostart", "default"], ignore_errors=True)
    run_cmd(["virsh", "-c", "qemu:///system", "net-start", "default"], ignore_errors=True)
    log_success("Network topology configured.")

def configure_permissions() -> None:
    target_user = os.environ.get("SUDO_USER")
    if not target_user:
        log_warn("SUDO_USER unreadable (running as raw root). Skipping standard user group injection.")
        return
    
    log_info(f"Injecting standard user ({target_user}) into hypervisor groups...")
    run_cmd(["usermod", "-aG", "libvirt,kvm,input,disk,render", target_user])
    log_success(f"Permissions granted. (Re-login required for user '{target_user}').")

def main() -> None:
    validate_environment()
    verify_hardware()
    install_packages()
    configure_daemons()
    configure_network()
    configure_permissions()
    print(f"\n{GREEN}=== Host Virtualization Setup Complete ==={NC}")

if __name__ == "__main__":
    main()
