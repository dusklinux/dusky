#!/usr/bin/env python3
"""
Phase 4: VFIO Dynamic State Manager
Target: Arch Linux (Kernel 7.1.0+), Python 3.14.5+, systemd 260
Scope: Toggles GPU isolation state (VFIO <-> Host).
Features: JSON Bootctl tracking, UKI (Type #2) Awareness, Non-Destructive Config Toggling, Permission Inheritance.
Usage: ./gpu_manager.py --bind   (Isolate GPU for VM)
       ./gpu_manager.py --unbind (Return GPU to Host)
"""

import os
import sys
import re
import argparse
import subprocess
import json
import tempfile
import shlex
import shutil
from pathlib import Path
from typing import Never

# ==============================================================================
# BOOTSTRAP
# ==============================================================================
def require_root() -> None:
    """Enforce eUID 0. Auto-elevates via sudo if executed as standard user."""
    if os.geteuid() != 0:
        print("\n[INFO] Elevating to root...")
        try:
            os.execvp("sudo", ["sudo", sys.executable] + sys.argv)
        except OSError as e:
            print(f"\n[FATAL] Failed to elevate: {e}")
            sys.exit(1)

require_root()

try:
    from rich.console import Console
    from rich.panel import Panel
except ImportError:
    print("[FATAL] 'python-rich' is missing. Install it via pacman.")
    sys.exit(1)

console = Console()

# ==============================================================================
# CORE UTILITIES
# ==============================================================================
def bail(msg: str) -> Never:
    """Exit gracefully with a clear error panel."""
    console.print(Panel(f"[bold red]FATAL ERROR:[/bold red] {msg}", border_style="red"))
    sys.exit(1)

def atomic_write(target_path: Path, new_content: str) -> bool:
    """Safely writes data using a temporary file and atomic swap, inheriting permissions."""
    if target_path.exists() and target_path.read_text(encoding="utf-8") == new_content:
        return False
        
    orig_mode = 0o644
    if target_path.exists():
        orig_mode = target_path.stat().st_mode & 0o777

    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(dir=target_path.parent, prefix=f".{target_path.name}.tmp.")
    tmp_path = Path(tmp_path_str)
    
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(new_content)
        os.chmod(tmp_path, orig_mode)
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
    """Detects CPU architecture to set the correct IOMMU flag."""
    cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8")
    if "GenuineIntel" in cpuinfo:
        return "intel_iommu"
    elif "AuthenticAMD" in cpuinfo:
        return "amd_iommu"
    return "intel_iommu"

def get_vfio_ids() -> str:
    """Dynamically extracts hardware IDs from Phase 3 configs or active cmdlines."""
    paths = [Path("/etc/modprobe.d/vfio.conf"), Path("/etc/modprobe.d/vfio.conf.disabled")]
    for p in paths:
        if p.exists():
            content = p.read_text(encoding="utf-8")
            match = re.search(r'^options\s+vfio-pci\s+ids=([0-9a-fA-F:,]+)', content, re.MULTILINE)
            if match:
                return match.group(1)
                
    cmd_paths = [Path("/etc/kernel/cmdline"), Path("/proc/cmdline")]
    for p in cmd_paths:
        if p.exists():
            content = p.read_text(encoding="utf-8")
            match = re.search(r'vfio-pci\.ids=([0-9a-fA-F:,]+)', content)
            if match:
                return match.group(1)

    bail("Could not locate VFIO IDs in modprobe rules or cmdline. Please run Phase 3 Setup first.")

def resolve_boot_path() -> Path:
    """Resolves XBOOTLDR or ESP via systemd 260 bootctl flags."""
    try:
        res = subprocess.run(["bootctl", "-x"], capture_output=True, text=True, check=True)
        return Path(res.stdout.strip())
    except Exception:
        pass
    try:
        res = subprocess.run(["bootctl", "-p"], capture_output=True, text=True, check=True)
        return Path(res.stdout.strip())
    except Exception:
        return Path("/boot")

def get_systemd_boot_target() -> tuple[Path, str, str]:
    """Uses JSON output to locate the optimal default active entry (Type #1 or Type #2)."""
    try:
        res = subprocess.run(["bootctl", "list", "--json=short"], capture_output=True, text=True, check=True)
        entries = json.loads(res.stdout)
        
        default_entry = next((e for e in entries if e.get("is_default")), None)
        selected_entry = next((e for e in entries if e.get("is_selected")), None)
        target_entry = default_entry or selected_entry
        
        if target_entry:
            source_path = target_entry.get("source")
            entry_type = target_entry.get("type", "Type #1")
            options = target_entry.get("options", "")
            
            if source_path and Path(source_path).exists():
                return Path(source_path), entry_type, options
    except Exception:
        pass

    boot_dir = resolve_boot_path()
    entries_dir = boot_dir / "loader" / "entries"
    
    for name in ["arch-linux.conf", "arch.conf"]:
        candidate = entries_dir / name
        if candidate.exists():
            return candidate, "Type #1", ""

    bail("Could not dynamically resolve the target boot entry or configuration.")

# ==============================================================================
# STATE MANAGEMENT
# ==============================================================================
def generate_parameter_string(current_opts: list[str], state: str, vfio_ids: str) -> str:
    """Deduplicates and recalculates kernel parameters, safely merging state."""
    new_opts: list[str] = []
    
    merged_params: dict[str, set[str]] = {
        "intel_iommu": set(),
        "amd_iommu": set(),
        "iommu": set(),
        "vfio-pci.ids": set(),
        "module_blacklist": set(),
        "modprobe.blacklist": set()
    }
    
    # 1. Parse current cmdline
    for opt in current_opts:
        if "=" in opt:
            k, v = opt.split("=", 1)
            norm_k = k.replace("vfio_pci", "vfio-pci")
            if norm_k in merged_params:
                merged_params[norm_k].update(filter(None, v.split(",")))
            else:
                new_opts.append(opt)
        else:
            norm_opt = opt.replace("vfio_pci", "vfio-pci")
            if norm_opt in merged_params:
                continue
            new_opts.append(opt)
            
    cpu_flag = get_cpu_iommu_flag()
    target_ids = set(filter(None, vfio_ids.split(",")))
    vfio_targets = {"nouveau", "nvidia", "nvidia_drm", "nvidia_modeset", "nvidia_uvm"}

    if state == "bind":
        merged_params[cpu_flag].add("on")
        merged_params["iommu"].add("pt")
        merged_params["vfio-pci.ids"].update(target_ids)
        merged_params["module_blacklist"].update(vfio_targets)
    elif state == "unbind":
        # Remove ONLY the GPU IDs, preserving other VFIO devices
        merged_params["vfio-pci.ids"].difference_update(target_ids)
        merged_params["module_blacklist"].difference_update(vfio_targets)
        merged_params["modprobe.blacklist"].difference_update(vfio_targets)
        
    # Cross-vendor cleanup
    if cpu_flag == "intel_iommu":
        merged_params["amd_iommu"].clear()
    elif cpu_flag == "amd_iommu":
        merged_params["intel_iommu"].clear()

    # Reconstruct
    for k, vals in merged_params.items():
        if vals:
            new_opts.append(f"{k}={','.join(sorted(vals))}")

    return " ".join(new_opts)

def toggle_mkinitcpio(state: str) -> None:
    """Surgically injects or strips VFIO modules across all mkinitcpio configurations."""
    conf_paths = [Path("/etc/mkinitcpio.conf")]
    
    conf_d = Path("/etc/mkinitcpio.conf.d")
    if conf_d.exists() and conf_d.is_dir():
        conf_paths.extend(sorted(conf_d.glob("*.conf")))

    for mk_path in conf_paths:
        if not mk_path.exists():
            continue
            
        original_content = mk_path.read_text(encoding="utf-8")
        
        def patch_modules(match: re.Match) -> str:
            mods = shlex.split(match.group(1), comments=True, posix=True)
            vfio_reqs = ['vfio_pci', 'vfio', 'vfio_iommu_type1']
            
            if state == "bind":
                for req in vfio_reqs:
                    if req not in mods:
                        mods.append(req)
            elif state == "unbind":
                mods = [m for m in mods if m not in vfio_reqs]
                
            return f"MODULES=({' '.join(mods)})"
        
        content = re.sub(r'^MODULES=\(([^)]*)\)', patch_modules, original_content, flags=re.MULTILINE)
        
        if content != original_content:
            if atomic_write(mk_path, content):
                console.print(f"[bold green]  ✓ {state.capitalize()}ed VFIO initramfs modules in {mk_path.name}[/bold green]")
        else:
            console.print(f"  [dim]Initramfs configuration {mk_path.name} already optimized for {state} state.[/dim]")

def toggle_bootloader(state: str, vfio_ids: str) -> None:
    """Surgically alters kernel command lines in .conf or /etc/kernel/cmdline."""
    target_path, entry_type, baked_options = get_systemd_boot_target()
    
    # TYPE #1: Standard Conf
    if "Type #1" in entry_type or target_path.suffix == ".conf":
        content = target_path.read_text(encoding="utf-8")
        opt_match = re.search(r'^options\s+(.*)', content, re.MULTILINE)
        
        if not opt_match:
            bail(f"Could not locate the 'options' line in {target_path.name}.")
            
        current_opts = shlex.split(opt_match.group(1), posix=False)
        updated_opts_line = "options " + generate_parameter_string(current_opts, state, vfio_ids)
        new_content = content[:opt_match.start()] + updated_opts_line + content[opt_match.end():]
        
        if atomic_write(target_path, new_content):
            console.print(f"[bold green]  ✓ {state.capitalize()}ed VFIO parameters in {target_path.name}.[/bold green]")
        else:
            console.print(f"  [dim]Bootloader already properly configured for {state} state.[/dim]")

    # TYPE #2: UKI (/etc/kernel/cmdline)
    else:
        cmdline_path = Path("/etc/kernel/cmdline")
        current_opts = []
        
        if cmdline_path.exists():
            current_opts = shlex.split(cmdline_path.read_text(encoding="utf-8").strip(), posix=False)
        elif baked_options:
            current_opts = shlex.split(baked_options, posix=False)
        else:
            proc_content = Path("/proc/cmdline").read_text(encoding="utf-8").strip()
            volatile_flags = {"single", "1", "s", "S", "rescue", "emergency", "nomodeset", "systemd.unit=rescue.target", "systemd.unit=emergency.target"}
            raw_opts = shlex.split(proc_content, posix=False)
            
            for opt in raw_opts:
                if opt in volatile_flags or opt.startswith("BOOT_IMAGE=") or opt.startswith("initrd="):
                    continue
                current_opts.append(opt)
                
        updated_opts = generate_parameter_string(current_opts, state, vfio_ids)
        if atomic_write(cmdline_path, updated_opts + "\n"):
            console.print(f"[bold green]  ✓ {state.capitalize()}ed persistent parameters in /etc/kernel/cmdline (UKI).[/bold green]")
        else:
            console.print(f"  [dim]cmdline already properly configured for {state} state.[/dim]")

def toggle_modprobe(state: str) -> None:
    """Safely activates/deactivates modprobe rules via renaming to preserve IDs."""
    active = Path("/etc/modprobe.d/vfio.conf")
    disabled = Path("/etc/modprobe.d/vfio.conf.disabled")
    
    if state == "bind":
        if disabled.exists():
            shutil.move(disabled, active)
            console.print(f"[bold green]  ✓ Restored modprobe rules ({active.name}).[/bold green]")
        elif active.exists():
            console.print(f"  [dim]Modprobe rules already active.[/dim]")
        else:
            bail("No vfio configuration found. Please run Phase 3 Setup.")
            
    elif state == "unbind":
        if active.exists():
            shutil.move(active, disabled)
            console.print(f"[bold green]  ✓ Disabled modprobe rules (renamed to {disabled.name}).[/bold green]")
        elif disabled.exists():
            console.print(f"  [dim]Modprobe rules already disabled.[/dim]")
        else:
            console.print("  [dim]No active vfio configuration found to disable.[/dim]")

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
    
    vfio_ids = get_vfio_ids()
    
    if args.bind:
        console.print(Panel(f"[bold green]Engaging VFIO Mode[/bold green]\nTarget IDs: {vfio_ids}", expand=False))
        toggle_modprobe("bind")
        toggle_mkinitcpio("bind")
        toggle_bootloader("bind", vfio_ids)
        rebuild_initramfs()
        console.print("\n[bold green]=== SYSTEM READY FOR VM ===[/bold green]")
        
    elif args.unbind:
        console.print(Panel(f"[bold yellow]Engaging Host Mode[/bold yellow]\nReleasing IDs: {vfio_ids}", expand=False))
        toggle_modprobe("unbind")
        toggle_mkinitcpio("unbind")
        toggle_bootloader("unbind", vfio_ids)
        rebuild_initramfs()
        console.print("\n[bold green]=== SYSTEM READY FOR HOST GRAPHICS ===[/bold green]")

    prompt_reboot()

if __name__ == "__main__":
    main()
