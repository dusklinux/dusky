#!/usr/bin/env python3
"""
==============================================================================
 GPARTED WAYLAND AUTO-CONFIGURATOR (Enterprise Arch 7.0+ Edition)
 Description: Idempotent, zero-trust, zero-hardcoding system configurator.
              Installs gparted, configures native Wayland execution via
              wrapper scripts, and registers XDG desktop entries.
 Target:      Arch Linux (Kernel 7.0+ / Python 3.14+)
==============================================================================
"""

import os
import sys
import shutil
import getpass
import subprocess
from pathlib import Path

# ANSI color codes for premium terminal output
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_GREEN = "\033[32m"
C_BLUE = "\033[34m"
C_YELLOW = "\033[33m"
C_RED = "\033[31m"
C_CYAN = "\033[36m"

def is_package_installed(package_name: str) -> bool:
    """Checks if a pacman package is already installed on the system."""
    result = subprocess.run(["pacman", "-Qq", package_name], capture_output=True, text=True)
    return result.returncode == 0

def install_package(package_name: str) -> None:
    """Idempotently installs a pacman package using sudo if not already root."""
    if is_package_installed(package_name):
        print(f"  {C_GREEN}✔{C_RESET} Package '{package_name}' is already installed (idempotent skip).")
        return

    print(f"  {C_YELLOW}•{C_RESET} Package '{package_name}' not found. Installing...")
    
    # Determine privilege escalation tool
    cmd = []
    if os.getuid() != 0:
        if shutil.which("sudo"):
            cmd = ["sudo", "pacman", "-S", "--needed", "--noconfirm", package_name]
        elif shutil.which("pkexec"):
            cmd = ["pkexec", "pacman", "-S", "--needed", "--noconfirm", package_name]
        else:
            print(f"  {C_RED}✖ Error: Privilege escalation tool (sudo/pkexec) not found.{C_RESET}", file=sys.stderr)
            sys.exit(1)
    else:
        cmd = ["pacman", "-S", "--needed", "--noconfirm", package_name]

    try:
        subprocess.run(cmd, check=True)
        print(f"  {C_GREEN}✔{C_RESET} Successfully installed '{package_name}'.")
    except subprocess.CalledProcessError as e:
        print(f"  {C_RED}✖ Error installing package '{package_name}': {e}{C_RESET}", file=sys.stderr)
        sys.exit(1)

def main() -> None:
    print(f"{C_BOLD}{C_BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}")
    print(f"{C_BOLD}{C_BLUE}       GPARTED WAYLAND AUTO-CONFIGURATOR (Arch 7.0+){C_RESET}")
    print(f"{C_BOLD}{C_BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}")

    # 1. Package Installation Phase
    print(f"{C_BOLD}{C_CYAN}[Phase 1: Package Verification]{C_RESET}")
    install_package("gparted")
    print()

    # 2. Path & User Discovery
    print(f"{C_BOLD}{C_CYAN}[Phase 2: Environment Discovery]{C_RESET}")
    user = os.environ.get('USER') or os.environ.get('SUDO_USER') or getpass.getuser()
    if user == 'root' and os.environ.get('SUDO_USER'):
        user = os.environ.get('SUDO_USER')
        
    home = Path(f"/home/{user}") if user != 'root' else Path.home()
    
    print(f"  • Target User: {C_BOLD}{user}{C_RESET}")
    print(f"  • User Home:   {home}")
    
    # Path configuration
    bin_dir = home / ".local" / "bin"
    wrapper_path = bin_dir / "gparted-wayland"
    desktop_dir = home / ".config" / "desktop_entries" / "all"
    desktop_path = desktop_dir / "gparted-wayland.desktop"
    
    # Verify environment matches user PATH expectations
    user_path = os.environ.get("PATH", "")
    if str(bin_dir) not in user_path.split(":"):
        print(f"  {C_YELLOW}⚠ Warning: {bin_dir} is not currently in your PATH environment variable.{C_RESET}")
        
    print()

    # 3. Code & Config Generation
    print(f"{C_BOLD}{C_CYAN}[Phase 3: File Deployment]{C_RESET}")
    bin_dir.mkdir(parents=True, exist_ok=True)
    desktop_dir.mkdir(parents=True, exist_ok=True)

    # 3.1 Write the native Wayland wrapper script
    wrapper_content = f"""#!/bin/sh
# Native Wayland wrapper for GParted under UWSM/Wayland
# Generated dynamically by gparted_wayland_setup.py
exec pkexec env WAYLAND_DISPLAY="$WAYLAND_DISPLAY" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" /usr/bin/gparted "$@"
"""
    try:
        wrapper_path.write_text(wrapper_content, encoding='utf-8')
        wrapper_path.chmod(0o755)
        print(f"  {C_GREEN}✔{C_RESET} Native wrapper written to: {wrapper_path}")
    except OSError as e:
        print(f"  {C_RED}✖ Error writing wrapper script: {e}{C_RESET}", file=sys.stderr)
        sys.exit(1)

    # 3.2 Write the desktop entry dynamically
    desktop_content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name=GParted (Wayland)
GenericName=Partition Editor
Comment=Create, reorganize, and delete partitions natively on Wayland
Exec=uwsm-app -- {wrapper_path} %f
Icon=gparted
Terminal=false
Categories=GNOME;GTK;System;Filesystem;
StartupNotify=true
"""
    try:
        desktop_path.write_text(desktop_content, encoding='utf-8')
        print(f"  {C_GREEN}✔{C_RESET} Desktop entry template written to: {desktop_path}")
    except OSError as e:
        print(f"  {C_RED}✖ Error writing desktop entry: {e}{C_RESET}", file=sys.stderr)
        sys.exit(1)
        
    print()

    # 4. Synchronize and Clean Caches
    print(f"{C_BOLD}{C_CYAN}[Phase 4: System Integration & Sync]{C_RESET}")
    sync_script = home / "user_scripts" / "arch_setup_scripts" / "scripts" / "020_desktop_entries.py"
    if sync_script.is_file():
        try:
            # Run the synchronizer script
            result = subprocess.run([sys.executable, str(sync_script)], capture_output=True, text=True)
            if result.returncode == 0:
                print(f"  {C_GREEN}✔{C_RESET} Synchronization completed successfully.")
                print(result.stdout.strip())
            else:
                print(f"  {C_RED}✖ Synchronizer exited with status {result.returncode}{C_RESET}", file=sys.stderr)
                print(result.stderr, file=sys.stderr)
        except Exception as e:
            print(f"  {C_RED}✖ Failed to run synchronizer: {e}{C_RESET}", file=sys.stderr)
    else:
        print(f"  {C_YELLOW}⚠ Synchronizer script not found at {sync_script}.{C_RESET}")

    # Validate deployed desktop file
    deployed_desktop = home / ".local" / "share" / "applications" / "gparted-wayland.desktop"
    if deployed_desktop.is_file():
        if shutil.which("desktop-file-validate"):
            val_res = subprocess.run(["desktop-file-validate", str(deployed_desktop)], capture_output=True, text=True)
            if val_res.returncode == 0:
                print(f"  {C_GREEN}✔{C_RESET} Desktop entry validated successfully.")
            else:
                print(f"  {C_YELLOW}⚠ Desktop entry validation warning/error:{C_RESET}\n{val_res.stderr}")
        
        # Update desktop database
        if shutil.which("update-desktop-database"):
            subprocess.run(["update-desktop-database", str(deployed_desktop.parent)], capture_output=True)

    # Invalidate Rofi Cache
    rofi_cache = home / ".cache" / "rofi3.druncache"
    if rofi_cache.is_file():
        try:
            rofi_cache.unlink()
            print(f"  {C_GREEN}✔{C_RESET} Cleared stale Rofi cache for instant menu indexing.")
        except OSError:
            pass

    print(f"{C_BOLD}{C_BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C_RESET}\n")

if __name__ == "__main__":
    main()
