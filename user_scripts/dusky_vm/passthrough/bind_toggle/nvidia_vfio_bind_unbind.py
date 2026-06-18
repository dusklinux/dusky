#!/usr/bin/env python3
"""
Phase 4: VFIO Dynamic State Manager
Target: Arch Linux, systemd-boot, mkinitcpio
Scope: Toggles GPU isolation state (VFIO <-> Host).
Features: JSON Bootctl tracking, Atomic file operations, Surgical parameter stripping.
Usage: ./gpu_manager.py --bind   (Isolate GPU for VM)
       ./gpu_manager.py --unbind (Return GPU to Host)
"""

import os
import sys
import argparse
import subprocess
import json
import tempfile
import shutil
from pathlib import Path
from typing import List, Set, Never

# ==============================================================================
# CONFIGURATION CONSTANTS
# ==============================================================================
GPU_IDS = "10de:25a0,10de:2291"
MODPROBE_CONF = Path("/etc/modprobe.d/vfio.conf")

VFIO_BLACKLIST_TARGETS = {"nouveau", "nvidia", "nvidia_drm", "nvidia_modeset", "nvidia_uvm"}

VFIO_MODPROBE_CONTENT = f"""options vfio-pci ids={GPU_IDS}
softdep nvidia pre: vfio-pci
softdep nvidia_drm pre: vfio-pci
softdep nvidia_modeset pre: vfio-pci
softdep nvidia_uvm pre: vfio-pci
softdep nouveau pre: vfio-pci
blacklist nvidia
blacklist nvidia_drm
blacklist nvidia_modeset
blacklist nvidia_uvm
blacklist nouveau
"""

# ==============================================================================
# BOOTSTRAP
# ==============================================================================
def require_root() -> None:
    if os.geteuid() != 0:
        print("\n[INFO] Elevating to root...")
        os.execvp("sudo", ["sudo", sys.executable] + sys.argv)

require_root()

try:
    from rich.console import Console
    from rich.panel import Panel
except ImportError:
    print("[FATAL] 'python-rich' is missing.")
    sys.exit(1)

console = Console()

# ==============================================================================
# CORE UTILITIES
# ==============================================================================
def bail(msg: str) -> Never:
    console.print(Panel(f"[bold red]FATAL ERROR:[/bold red] {msg}", border_style="red"))
    sys.exit(1)

def atomic_write(target_path: Path, new_content: str) -> bool:
    """Safely writes data using a temporary file and atomic swap."""
    if target_path.exists() and target_path.read_text(encoding="utf-8") == new_content:
        return False
        
    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(dir=target_path.parent, prefix=f".{target_path.name}.tmp.")
    tmp_path = Path(tmp_path_str)
    
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(new_content)
        os.chmod(tmp_path, 0o644)
        shutil.move(tmp_path, target_path)
        return True
    except Exception as e:
        if tmp_path.exists():
            tmp_path.unlink()
        bail(f"Atomic write failed on {target_path}: {e}")

# ==============================================================================
# SYSTEM INTELLIGENCE
# ==============================================================================
def get_cpu_iommu_flag() -> str:
    cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8")
    if "GenuineIntel" in cpuinfo:
        return "intel_iommu"
    elif "AuthenticAMD" in cpuinfo:
        return "amd_iommu"
    return "intel_iommu"

def get_systemd_boot_entry() -> Path:
    """Uses systemd native JSON output to flawlessly locate the active boot entry."""
    try:
        res = subprocess.run(["bootctl", "list", "--json=short"], capture_output=True, text=True, check=True)
        entries = json.loads(res.stdout)
        
        for entry in entries:
            if entry.get("is_default") or entry.get("is_selected"):
                source_path = entry.get("source")
                if source_path and Path(source_path).exists():
                    return Path(source_path)
    except Exception:
        pass # Fallback below

    # Fallback
    entries_dir = Path("/boot/loader/entries")
    for name in ["arch-linux.conf", "arch.conf"]:
        candidate = entries_dir / name
        if candidate.exists():
            return candidate

    bail("Could not dynamically resolve the target systemd-boot entry.")

# ==============================================================================
# STATE MANAGEMENT
# ==============================================================================
def toggle_bootloader(state: str) -> None:
    """Surgically injects or strips VFIO kernel parameters."""
    conf_path = get_systemd_boot_entry()
    cpu_flag = get_cpu_iommu_flag()
    
    content = conf_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    
    new_lines = []
    changed = False

    strip_keys = [f"{cpu_flag}=", "iommu=", "vfio-pci.ids=", "pcie_aspm="]

    for line in lines:
        if line.startswith("options "):
            current_opts = line[8:].split()
            clean_opts = []
            existing_bl: Set[str] = set()

            # Pass 1: Filter standard keys and extract existing blacklists
            for opt in current_opts:
                if opt.startswith("module_blacklist="):
                    existing_bl.update(opt.split("=", 1)[1].split(","))
                elif not any(opt.startswith(k) for k in strip_keys):
                    clean_opts.append(opt)

            # Pass 2: Apply State Logic
            if state == "bind":
                clean_opts.extend([f"{cpu_flag}=on", "iommu=pt", "pcie_aspm=force", f"vfio-pci.ids={GPU_IDS}"])
                merged_bl = existing_bl.union(VFIO_BLACKLIST_TARGETS)
                merged_bl.discard("")
                if merged_bl:
                    clean_opts.append(f"module_blacklist={','.join(sorted(merged_bl))}")
            
            elif state == "unbind":
                # Surgically remove ONLY VFIO targets from the blacklist
                remaining_bl = existing_bl - VFIO_BLACKLIST_TARGETS
                remaining_bl.discard("")
                if remaining_bl:
                    clean_opts.append(f"module_blacklist={','.join(sorted(remaining_bl))}")

            new_options_line = "options " + " ".join(clean_opts)
            
            if line != new_options_line:
                new_lines.append(new_options_line)
                changed = True
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    if changed:
        new_content = "\n".join(new_lines) + "\n"
        atomic_write(conf_path, new_content)
        action = "Injected" if state == "bind" else "Stripped"
        console.print(f"[bold green]  ✓ {action} VFIO parameters in {conf_path.name}.[/bold green]")
    else:
        console.print(f"  [dim]Bootloader already in {state} state. No changes required.[/dim]")

def toggle_modprobe(state: str) -> None:
    if state == "bind":
        if atomic_write(MODPROBE_CONF, VFIO_MODPROBE_CONTENT):
            console.print(f"[bold green]  ✓ Written strict VFIO rules to {MODPROBE_CONF.name}.[/bold green]")
        else:
            console.print("  [dim]Modprobe rules already active.[/dim]")
    elif state == "unbind":
        if MODPROBE_CONF.exists():
            MODPROBE_CONF.unlink()
            console.print(f"[bold green]  ✓ Obliterated {MODPROBE_CONF.name}.[/bold green]")
        else:
            console.print("  [dim]Modprobe rules already cleared.[/dim]")

def rebuild_initramfs() -> None:
    console.print("\n[bold blue]==>[/bold blue] [bold]Recompiling Initramfs (mkinitcpio -P)...[/bold]")
    try:
        with console.status("[cyan]Building images... this may take a moment.[/cyan]"):
            subprocess.run(["mkinitcpio", "-P"], check=True, capture_output=True, text=True)
        console.print("[bold green]  ✓ Initramfs regeneration successful.[/bold green]")
    except subprocess.CalledProcessError as e:
        console.print(Panel(f"[bold red]mkinitcpio failed![/bold red]\n{e.stderr}", border_style="red"))
        sys.exit(1)

def prompt_reboot() -> None:
    print()
    try:
        choice = input("Reboot system now to apply changes? [y/N]: ").strip().lower()
        if choice == 'y':
            console.print("[bold yellow]Initiating reboot...[/bold yellow]")
            subprocess.run(["reboot"])
    except KeyboardInterrupt:
        print()

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="VFIO GPU State Manager")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bind", action="store_true", help="Isolate GPU for VM (VFIO Mode)")
    group.add_argument("--unbind", action="store_true", help="Return GPU to Host (NVIDIA Mode)")
    
    args = parser.parse_args()
    console.clear()
    
    if args.bind:
        console.print(Panel("[bold green]Engaging VFIO Mode (GPU Isolation)[/bold green]", expand=False))
        toggle_bootloader("bind")
        toggle_modprobe("bind")
        rebuild_initramfs()
        console.print("\n[bold green]=== SYSTEM READY FOR VM ===[/bold green]")
        
    elif args.unbind:
        console.print(Panel("[bold yellow]Engaging Host Mode (GPU Restoration)[/bold yellow]", expand=False))
        toggle_bootloader("unbind")
        toggle_modprobe("unbind")
        rebuild_initramfs()
        console.print("\n[bold green]=== SYSTEM READY FOR HOST GRAPHICS ===[/bold green]")

    prompt_reboot()

if __name__ == "__main__":
    main()
