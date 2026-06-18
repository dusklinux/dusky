#!/usr/bin/env python3
"""
Phase 3: VFIO Kernel Isolation & Bootloader Configuration
Target: Arch Linux (Kernel 7.1.0+), Python 3.14.5+, systemd 260
Scope: Dynamic hardware probing, UKI-aware bootctl JSON parsing, mkinitcpio structural hook enforcement.
Philosophy: Zero-Clutter Idempotency, Atomic Writes, Sysfs Topography.
"""

import os
import sys
import re
import json
import shlex
import shutil
import tempfile
import subprocess
import dataclasses
from pathlib import Path
from typing import Never

# ==============================================================================
# BOOTSTRAP: Strict Privilege & Auto-Elevation
# ==============================================================================
def require_root() -> None:
    """Enforce eUID 0. Auto-elevates via sudo if executed as standard user."""
    if os.geteuid() != 0:
        print("\n[INFO] Administrative privileges required. Elevating via sudo...")
        try:
            os.execvp("sudo", ["sudo", sys.executable] + sys.argv)
        except OSError as e:
            print(f"\n[FATAL] Failed to elevate privileges dynamically: {e}")
            sys.exit(1)

require_root()

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.prompt import IntPrompt
except ImportError:
    print("\n[FATAL] 'python-rich' is missing. Please ensure Phase 1 completed successfully.")
    sys.exit(1)

console = Console()

# ==============================================================================
# DATA STRUCTURES
# ==============================================================================
@dataclasses.dataclass
class VFIODevice:
    pci_bus: str
    video_id: str
    video_desc: str
    audio_id: str | None = None
    audio_desc: str = "No companion audio detected"
    iommu_group: str = "Unknown"

# ==============================================================================
# CORE UTILITIES
# ==============================================================================
def bail(msg: str) -> Never:
    """Exit gracefully with a clear error panel."""
    console.print(Panel(f"[bold red]FATAL ERROR:[/bold red] {msg}", border_style="red"))
    sys.exit(1)

def check_deps() -> None:
    """Ensures pciutils is installed before executing system hardware scans."""
    if shutil.which("lspci"):
        return
    console.print("[yellow]⚠ Missing dependency detected: pciutils[/yellow]")
    console.print("[cyan]  Attempting to install via pacman...[/cyan]")
    try:
        subprocess.run(['pacman', '-S', '--needed', '--noconfirm', 'pciutils'], check=True, stdout=subprocess.DEVNULL)
        console.print("[green]  ✓ Dependencies installed.[/green]")
    except subprocess.CalledProcessError:
        bail("Failed to install required dependencies. Aborting.")

def atomic_write(target_path: Path, new_content: str) -> bool:
    """
    Safely writes data using a temporary file and atomic swap.
    Returns True if changes were made, False if the file is already optimal.
    """
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
# HARDWARE DISCOVERY & IOMMU TOPOLOGY
# ==============================================================================
def get_cpu_iommu_flag() -> str:
    """Detects CPU architecture via /proc/cpuinfo to set the correct IOMMU flag."""
    cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8")
    if "GenuineIntel" in cpuinfo:
        return "intel_iommu"
    elif "AuthenticAMD" in cpuinfo:
        return "amd_iommu"
    
    console.print("[yellow]⚠ Could not strictly determine CPU vendor. Defaulting to Intel VT-d flags.[/yellow]")
    return "intel_iommu"

def probe_gpus() -> list[VFIODevice]:
    """Dynamically probes PCI tree for GPUs, companion audio, and IOMMU groups."""
    console.print("\n[bold blue]==>[/bold blue] [bold]Probing system PCI & IOMMU topography...[/bold]")
    check_deps()
    
    try:
        res = subprocess.run(["lspci", "-Dnn"], capture_output=True, text=True, check=True)
        lspci_out = res.stdout
    except subprocess.CalledProcessError:
        bail("Failed to execute lspci.")

    gpu_map: dict[str, VFIODevice] = {}
    
    for line in lspci_out.splitlines():
        if "[0300]" in line or "[0302]" in line:
            bus_match = re.match(r'^([0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.\d)', line)
            id_match = re.search(r'\[([0-9a-fA-F]{4}:[0-9a-fA-F]{4})\]', line)
            
            if bus_match and id_match:
                bus = bus_match.group(1)
                iommu_group = "Unknown"
                iommu_path = Path(f"/sys/bus/pci/devices/{bus}/iommu_group")
                if iommu_path.is_symlink():
                    iommu_group = iommu_path.resolve().name
                
                gpu_map[bus] = VFIODevice(
                    pci_bus=bus,
                    video_id=id_match.group(1),
                    video_desc=line[len(bus):].strip(),
                    iommu_group=iommu_group
                )

    for line in lspci_out.splitlines():
        if "Audio device" in line or "[0403]" in line:
            bus_match = re.match(r'^([0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2})\.(\d)', line)
            if bus_match:
                base_bus = bus_match.group(1)
                gpu_bus = f"{base_bus}.0" 
                
                if gpu_bus in gpu_map:
                    id_match = re.search(r'\[([0-9a-fA-F]{4}:[0-9a-fA-F]{4})\]', line)
                    if id_match:
                        gpu_map[gpu_bus].audio_id = id_match.group(1)
                        gpu_map[gpu_bus].audio_desc = line[len(bus_match.group(0)):].strip()

    return sorted(list(gpu_map.values()), key=lambda x: x.pci_bus)

def select_target_gpu(devices: list[VFIODevice]) -> list[str]:
    """Provides an interactive UI for the administrator to isolate a specific GPU."""
    if not devices:
        bail("No VGA/3D controllers detected on this system.")

    table = Table(title="Available Graphics Processing Units", show_header=True, header_style="bold magenta")
    table.add_column("Opt", justify="center", style="cyan")
    table.add_column("PCI Bus", style="dim")
    table.add_column("IOMMU", justify="center", style="bold red")
    table.add_column("Video Controller & ID", style="green")
    table.add_column("Companion Audio & ID", style="yellow")

    for idx, dev in enumerate(devices):
        v_str = f"{dev.video_desc} [bold]({dev.video_id})[/bold]"
        a_str = f"{dev.audio_desc} [bold]({dev.audio_id})[/bold]" if dev.audio_id else "None"
        table.add_row(str(idx + 1), dev.pci_bus, dev.iommu_group, v_str, a_str)

    console.print(table)
    choice = IntPrompt.ask("\n[bold cyan]Select the discrete GPU to isolate for VFIO[/bold cyan]", choices=[str(i+1) for i in range(len(devices))])
    
    selected = devices[choice - 1]
    ids = [selected.video_id]
    if selected.audio_id:
        ids.append(selected.audio_id)
        
    console.print(f"[bold green]  ✓ Selected isolation IDs: {','.join(ids)} (IOMMU Group {selected.iommu_group})[/bold green]")
    return ids

# ==============================================================================
# BOOTLOADER INJECTION: SYSTEMD-BOOT (JSON NATIVE + UKI AWARE)
# ==============================================================================
def resolve_boot_path() -> Path:
    """Dynamically resolves the XBOOTLDR or ESP path via systemd 260 bootctl."""
    try:
        # Attempt 1: -x prints XBOOTLDR if it exists, or ESP otherwise.
        res = subprocess.run(["bootctl", "-x"], capture_output=True, text=True, check=True)
        return Path(res.stdout.strip())
    except Exception:
        pass

    try:
        # Attempt 2: Explicit fallback to -p (ESP only) if -x inexplicably fails.
        res = subprocess.run(["bootctl", "-p"], capture_output=True, text=True, check=True)
        return Path(res.stdout.strip())
    except Exception:
        console.print("[yellow]  ⚠ bootctl path resolution failed entirely. Falling back to legacy /boot.[/yellow]")
        return Path("/boot")

def get_systemd_boot_target() -> tuple[Path, str, str]:
    """
    Uses JSON output to locate the active entry.
    Returns: (File Path, Boot Loader Specification Type, Baked Options String)
    """
    console.print("  [cyan]Querying systemd-boot EFI payload data...[/cyan]")
    
    try:
        res = subprocess.run(["bootctl", "list", "--json=short"], capture_output=True, text=True, check=True)
        entries = json.loads(res.stdout)
        
        for entry in entries:
            if entry.get("is_default") or entry.get("is_selected"):
                source_path = entry.get("source")
                entry_type = entry.get("type", "Type #1")
                options = entry.get("options", "")
                
                if source_path and Path(source_path).exists():
                    return Path(source_path), entry_type, options
                    
    except Exception as e:
        console.print(f"[yellow]  ⚠ bootctl JSON query failed: {e}. Attempting dynamic fallback path resolution.[/yellow]")

    boot_dir = resolve_boot_path()
    entries_dir = boot_dir / "loader" / "entries"
    
    for name in ["arch-linux.conf", "arch.conf"]:
        candidate = entries_dir / name
        if candidate.exists():
            return candidate, "Type #1", ""

    bail("Could not dynamically resolve the target boot entry or configuration.")

def generate_parameter_string(current_opts: list[str], targets: dict[str, str], blacklist_set: set[str]) -> str:
    """Deduplicates and merges kernel parameters cleanly."""
    new_opts: list[str] = []
    existing_bl: set[str] = set()

    for opt in current_opts:
        if "=" in opt:
            k, v = opt.split("=", 1)
            if k in ["intel_iommu", "amd_iommu", "iommu", "vfio-pci.ids"]:
                continue
            elif k == "module_blacklist":
                existing_bl.update(v.split(","))
            else:
                new_opts.append(opt)
        else:
            new_opts.append(opt)
            
    for k, v in targets.items():
        new_opts.append(f"{k}={v}")
        
    merged_bl = existing_bl.union(blacklist_set)
    merged_bl.discard("")
    new_opts.append(f"module_blacklist={','.join(sorted(merged_bl))}")
    
    return " ".join(new_opts)

def inject_bootloader_parameters(vfio_ids: list[str]) -> None:
    """Safely injects kernel parameters, strictly preventing volatile pollution."""
    target_path, entry_type, baked_options = get_systemd_boot_target()
    cpu_flag = get_cpu_iommu_flag()
    id_str = ",".join(vfio_ids)
    
    targets = {cpu_flag: "on", "iommu": "pt", "vfio-pci.ids": id_str}
    blacklist_set = {"nouveau", "nvidia", "nvidia_drm", "nvidia_modeset", "nvidia_uvm"}

    # BRANCH A: Standard Boot Loader Spec Type #1 (.conf)
    if "Type #1" in entry_type or target_path.suffix == ".conf":
        console.print(f"\n[bold blue]==>[/bold blue] [bold]Targeting Standard Config:[/bold] {target_path.name}")
        content = target_path.read_text(encoding="utf-8")
        opt_match = re.search(r'^options\s+(.*)', content, re.MULTILINE)
        
        if not opt_match:
            bail(f"Could not locate the 'options' line in {target_path.name}.")
            
        current_opts = shlex.split(opt_match.group(1), posix=False)
        updated_opts_line = "options " + generate_parameter_string(current_opts, targets, blacklist_set)
        new_content = content[:opt_match.start()] + updated_opts_line + content[opt_match.end():]
        
        if atomic_write(target_path, new_content):
            console.print(f"[bold green]  ✓ Injected parameters into {target_path.name}.[/bold green]")
        else:
            console.print("[bold green]  ✓ Kernel parameters already optimal in config.[/bold green]")

    # BRANCH B: Boot Loader Spec Type #2 (Unified Kernel Image)
    else:
        console.print(f"\n[bold blue]==>[/bold blue] [bold]UKI Detected (Type #2):[/bold] {target_path.name}")
        console.print("  [cyan]Pivoting parameter injection to /etc/kernel/cmdline for mkinitcpio compilation...[/cyan]")
        
        cmdline_path = Path("/etc/kernel/cmdline")
        current_opts = []
        
        # 1. Respect explicit static configuration if it exists
        if cmdline_path.exists():
            content = cmdline_path.read_text(encoding="utf-8").strip()
            current_opts = shlex.split(content, posix=False)
            
        # 2. Inherit safely from the compiled `.efi` payload binary
        elif baked_options:
            console.print("  [cyan]No static cmdline found. Extracting baseline parameters directly from active UKI binary payload...[/cyan]")
            current_opts = shlex.split(baked_options, posix=False)
            
        # 3. Last resort fallback to /proc/cmdline (with explicit filtering of dynamic bootloader flags)
        else:
            console.print("  [yellow]⚠ No static cmdline or UKI payload options found. Attempting safe fallback via /proc/cmdline...[/yellow]")
            proc_content = Path("/proc/cmdline").read_text(encoding="utf-8").strip()
            
            volatile_flags = {"single", "1", "s", "S", "rescue", "emergency", "nomodeset", "systemd.unit=rescue.target", "systemd.unit=emergency.target"}
            raw_opts = shlex.split(proc_content, posix=False)
            
            # Prevent double-initrd loading or invalid BOOT_IMAGE mappings in generated UKIs
            for opt in raw_opts:
                if opt in volatile_flags:
                    continue
                if opt.startswith("BOOT_IMAGE=") or opt.startswith("initrd="):
                    continue
                current_opts.append(opt)
            
        updated_opts = generate_parameter_string(current_opts, targets, blacklist_set)
        if atomic_write(cmdline_path, updated_opts + "\n"):
            console.print(f"[bold green]  ✓ Injected persistent parameters safely into /etc/kernel/cmdline.[/bold green]")
        else:
            console.print("[bold green]  ✓ cmdline already perfectly optimized for UKI generation.[/bold green]")

# ==============================================================================
# INITRAMFS MANIPULATION 
# ==============================================================================
def process_mkinitcpio_file(mk_path: Path) -> None:
    """Parses, normalizes, and injects VFIO requirements into an mkinitcpio config file."""
    if not mk_path.exists():
        return

    original_content = mk_path.read_text(encoding="utf-8")
    
    def patch_modules(match: re.Match) -> str:
        # comments=True safely drops inline bash comments so they don't break the flattened array
        mods = shlex.split(match.group(1), comments=True, posix=True)
        for req in ['vfio_pci', 'vfio', 'vfio_iommu_type1']:
            if req not in mods:
                mods.append(req)
        return f"MODULES=({' '.join(mods)})"

    def patch_hooks(match: re.Match) -> str:
        hooks = shlex.split(match.group(1), comments=True, posix=True)
        if 'modconf' not in hooks:
            if 'kms' in hooks:
                hooks.insert(hooks.index('kms'), 'modconf')
            else:
                hooks.append('modconf')
        else:
            if 'kms' in hooks and hooks.index('modconf') > hooks.index('kms'):
                hooks.remove('modconf')
                hooks.insert(hooks.index('kms'), 'modconf')
        return f"HOOKS=({' '.join(hooks)})"

    # [^)]* slurps everything up to the closing parenthesis, catching multi-line arrays cleanly.
    content = re.sub(r'^MODULES=\(([^)]*)\)', patch_modules, original_content, flags=re.MULTILINE)
    content = re.sub(r'^HOOKS=\(([^)]*)\)', patch_hooks, content, flags=re.MULTILINE)

    if content != original_content:
        if atomic_write(mk_path, content):
            console.print(f"[bold green]  ✓ Enforced VFIO constraints in {mk_path.name}[/bold green]")
    else:
        console.print(f"[dim green]  ✓ Config {mk_path.name} is already optimal[/dim green]")


def configure_mkinitcpio() -> None:
    """Coordinates initramfs hardening across the primary config and all active drop-ins."""
    console.print("\n[bold blue]==>[/bold blue] [bold]Hardening initramfs via mkinitcpio.conf & drop-ins...[/bold]")
    
    main_conf = Path("/etc/mkinitcpio.conf")
    if not main_conf.exists():
        bail(f"{main_conf} does not exist. Ensure mkinitcpio is installed.")

    # 1. Process the primary configuration file
    process_mkinitcpio_file(main_conf)

    # 2. Dynamically scan and process all drop-in configurations
    conf_d = Path("/etc/mkinitcpio.conf.d")
    if conf_d.exists() and conf_d.is_dir():
        # Process sequentially and alphabetically, exactly mirroring mkinitcpio's parse priority
        for drop_in in sorted(conf_d.glob("*.conf")):
            process_mkinitcpio_file(drop_in)

# ==============================================================================
# MODPROBE KERNEL RULES
# ==============================================================================
def write_modprobe_rules(vfio_ids: list[str]) -> None:
    """Generates the absolute Ring 0 isolation rules for the VFIO framework."""
    vfio_conf = Path("/etc/modprobe.d/vfio.conf")
    console.print("\n[bold blue]==>[/bold blue] [bold]Generating static kernel driver rules...[/bold]")
    
    id_str = ",".join(vfio_ids)
    content = vfio_conf.read_text(encoding="utf-8") if vfio_conf.exists() else ""
    
    if re.search(r'^options vfio-pci ids=.*', content, re.MULTILINE):
        content = re.sub(r'^options vfio-pci ids=.*', f'options vfio-pci ids={id_str}', content, flags=re.MULTILINE)
    else:
        content += f"\noptions vfio-pci ids={id_str}\n"

    targets = ["nvidia", "nvidia_drm", "nvidia_modeset", "nvidia_uvm", "nouveau"]
    for sd in targets:
        sd_line = f"softdep {sd} pre: vfio-pci"
        if not re.search(rf'^softdep\s+{sd}\s+pre:\s+vfio-pci', content, re.MULTILINE):
            content += f"{sd_line}\n"

        bl_line = f"blacklist {sd}"
        if not re.search(rf'^blacklist\s+{sd}', content, re.MULTILINE):
            content += f"{bl_line}\n"

    content = re.sub(r'\n{3,}', '\n\n', content).strip() + "\n"
    
    if atomic_write(vfio_conf, content):
        console.print("[bold green]  ✓ Modprobe isolation dependencies bound securely.[/bold green]")
    else:
        console.print("[bold green]  ✓ VFIO modprobe isolation rules are already perfect.[/bold green]")

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================
def main() -> None:
    console.clear()
    console.print(Panel("[bold green]KVM GPU Passthrough: Phase 3[/bold green]\nTarget: VFIO Isolation & Host Kernel Configuration", expand=False))
    
    try:
        devices = probe_gpus()
        target_ids = select_target_gpu(devices)
        
        inject_bootloader_parameters(target_ids)
        configure_mkinitcpio()
        write_modprobe_rules(target_ids)
        
        console.print("\n[bold blue]==>[/bold blue] [bold]Compiling Initramfs Environment (mkinitcpio -P)...[/bold]")
        subprocess.run(["mkinitcpio", "-P"], check=True)
        
        console.print("\n[bold green]=== PHASE 3 COMPLETE ===[/bold green]")
        console.print("The host kernel is now structurally programmed to drop the GPU at boot.")
        console.print(Panel("ACTION REQUIRED: Reboot your system now. The isolation takes effect at Ring 0 during startup.", border_style="yellow"))

    except KeyboardInterrupt:
        console.print("\n\n[bold red]⚠ Process interrupted by operator. Exiting cleanly.[/bold red]\n")
        sys.exit(130)

if __name__ == "__main__":
    main()
