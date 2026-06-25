#!/usr/bin/env python3
"""
Phase 1: KVM & GPU Passthrough Staging Environment
Target: Arch Linux (Kernel 7.1.0+), Python 3.14+, systemd 260
Philosophy: Idempotent, Strict, 'Do One Thing Well' (Staging).
"""

import os
import sys
import pwd
import grp
import glob
import shutil
import readline
import importlib
import subprocess
import urllib.request
from urllib.error import URLError
from pathlib import Path
from typing import Never

# ==============================================================================
# PRE-FLIGHT (Strict Standard Library Only)
# ==============================================================================
def require_root_and_unlocked() -> None:
    """Hard enforcement of eUID 0. Auto-elevates via sudo if executed as standard user."""
    if os.geteuid() != 0:
        print("\n[INFO] Administrative privileges required. Elevating via sudo...")
        try:
            # Replace the current process with a sudo call, preserving exact binary and args
            os.execvp("sudo", ["sudo", sys.executable] + sys.argv)
        except OSError as e:
            print(f"\n[FATAL] Failed to elevate privileges dynamically: {e}")
            sys.exit(1)
            
    if Path("/var/lib/pacman/db.lck").exists():
        print("\n[FATAL] Pacman database is currently locked (/var/lib/pacman/db.lck).")
        print("        Ensure no other package managers are running and try again.\n")
        sys.exit(1)

require_root_and_unlocked()

# ==============================================================================
# BOOTSTRAP: Dynamic UI Dependency Resolution
# ==============================================================================
try:
    import rich
except ImportError:
    print("\n==> Bootstrapping 'python-rich' for advanced UI...")
    try:
        subprocess.run(
            ["pacman", "-S", "--needed", "--noconfirm", "python-rich"], 
            check=True, stdout=subprocess.DEVNULL
        )
        importlib.invalidate_caches()
        import rich
    except subprocess.CalledProcessError:
        print("[FATAL] Failed to install python-rich. Check your network or mirrors.")
        sys.exit(1)

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.progress import Progress, DownloadColumn, TransferSpeedColumn, TextColumn, TimeRemainingColumn

# Force terminal and interactive capabilities so the progress bar renders 
# correctly even when stdout is piped through 'tee' in the orchestrator.
console = Console(force_terminal=True, force_interactive=True)

# ==============================================================================
# CORE LOGIC
# ==============================================================================
def bail(msg: str) -> Never:
    """Exit gracefully with a clear error panel."""
    console.print(Panel(f"[bold red]FATAL ERROR:[/bold red] {msg}", border_style="red"))
    sys.exit(1)

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

def stage_packages() -> None:
    """Install core hypervisor and networking packages."""
    packages = [
        "qemu-full", "libvirt", "virt-install", "virt-manager", 
        "virt-viewer", "dnsmasq", "iproute2", "openbsd-netcat", 
        "edk2-ovmf", "swtpm", "nftables", "iptables", "libosinfo"
    ]
    
    console.print("\n[bold blue]==>[/bold blue] [bold]Synchronizing official hypervisor packages...[/bold]")
    
    try:
        # Added --noconfirm to prevent interactive provider prompts from hanging the script
        subprocess.run(["pacman", "-S", "--needed", "--noconfirm"] + packages, check=True)
        console.print("[bold green]  ✓ Core KVM packages staged.[/bold green]")
    except subprocess.CalledProcessError as e:
        bail(f"Pacman transaction failed with code {e.returncode}.")

def try_aur_virtio(user: str) -> bool:
    """Attempt to install virtio-win via paru. Fails safely if unavailable."""
    if not shutil.which("paru"):
        console.print("[yellow]  ⚠ 'paru' not found in PATH. Skipping AUR installation.[/yellow]")
        return False
        
    console.print(f"\n[bold blue]==>[/bold blue] [bold]Invoking AUR helper as '{user}' for virtio-win...[/bold]")
    
    # paru strictly blocks root execution. Drop privileges via sudo.
    # Added --noconfirm to bypass the PKGBUILD review pager which breaks inside log pipes
    cmd = ["sudo", "-u", user, "paru", "-S", "--needed", "--noconfirm", "--skipreview", "virtio-win"]
    try:
        subprocess.run(cmd, check=True)
        console.print("[bold green]  ✓ AUR package 'virtio-win' staged.[/bold green]")
        return True
    except subprocess.CalledProcessError:
        console.print("[bold yellow]  ⚠ AUR transaction failed or aborted. Falling back to direct download.[/bold yellow]")
        return False

def configure_access(user: str) -> None:
    """Idempotently append virtualization groups to the target user."""
    target_groups = ["libvirt", "kvm", "input"]
    console.print(f"\n[bold blue]==>[/bold blue] [bold]Configuring host access controls for '{user}'...[/bold]")
    
    existing_groups = [g.gr_name for g in grp.getgrall()]
    for g in target_groups:
        if g not in existing_groups:
            bail(f"Required system group '{g}' does not exist. Libvirt package installation may have failed.")

    try:
        subprocess.run(["usermod", "-aG", ",".join(target_groups), user], check=True)
        console.print(f"[bold green]  ✓ User '{user}' assigned to: {', '.join(target_groups)}[/bold green]")
        console.print(Panel(
            f"ACTION REQUIRED: '{user}' must fully [bold red]log out and log back in[/bold red] "
            "(or reboot) for group permissions to evaluate correctly.", 
            title="Permission Context", border_style="yellow"
        ))
    except subprocess.CalledProcessError as e:
        bail(f"Failed to assign groups. Usermod exited with {e.returncode}.")

def download_iso_stream(url: str, dest: Path) -> None:
    """Robust, chunked direct download using rich.progress."""
    req = urllib.request.Request(url, headers={'User-Agent': 'Arch-KVM-Deploy/1.0'})
    
    try:
        # Added timeout to prevent socket hangs causing a silent stall
        with urllib.request.urlopen(req, timeout=30) as response:
            total_size = int(response.headers.get("Content-Length", 0))
            
            with Progress(
                TextColumn("[bold cyan]{task.fields[filename]}", justify="right"),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
                console=console,
                transient=False
            ) as progress:
                task = progress.add_task("Downloading", filename="virtio-win.iso", total=total_size)
                
                with dest.open("wb") as out_file:
                    while chunk := response.read(16384): # 16KB chunks
                        out_file.write(chunk)
                        progress.update(task, advance=len(chunk))
                        
    except URLError as e:
        if dest.exists():
            dest.unlink()
        bail(f"Network error during ISO stream: {e}")
    except Exception as e:
        if dest.exists():
            dest.unlink()
        bail(f"Unexpected IO error: {e}")

def path_completer(text: str, state: int) -> str | None:
    """Tab completion for file paths, intelligently routing '~/' to the human user's home."""
    if text.startswith('~/'):
        sudo_user = os.environ.get("SUDO_USER", "")
        if sudo_user:
            try:
                home_dir = pwd.getpwnam(sudo_user).pw_dir
                expanded = os.path.join(home_dir, text[2:])
            except KeyError:
                expanded = os.path.expanduser(text)
        else:
            expanded = os.path.expanduser(text)
    else:
        expanded = os.path.expanduser(text)
        
    matches = glob.glob(expanded + '*')
    matches = [m + '/' if os.path.isdir(m) else m for m in matches]
    return matches[state] if state < len(matches) else None

def stage_iso(aur_success: bool) -> None:
    """Idempotent staging of the VirtIO ISO to the libvirt pool."""
    target_dir = Path("/var/lib/libvirt/images")
    target_iso = target_dir / "virtio-win.iso"
    aur_iso_path = Path("/usr/share/virtio/virtio-win.iso")
    
    console.print("\n[bold blue]==>[/bold blue] [bold]Staging VirtIO Driver ISO...[/bold]")
    
    target_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. State Check: perfectly staged already
    if target_iso.is_symlink() and target_iso.resolve() == aur_iso_path:
        console.print(f"[bold green]  ✓ ISO already symlinked to AUR path at {target_iso}[/bold green]")
        return
    elif target_iso.exists() and not target_iso.is_symlink():
        console.print(f"[bold green]  ✓ Standalone ISO already exists at {target_iso}[/bold green]")
        return

    # Cleanup orphaned symlinks
    if target_iso.is_symlink():
        target_iso.unlink()

    # 2. AUR Symlink execution
    if aur_success and aur_iso_path.exists():
        console.print("  [cyan]Creating logical link from AUR package to libvirt image pool...[/cyan]")
        target_iso.symlink_to(aur_iso_path)
        console.print(f"[bold green]  ✓ Symlink created at {target_iso}[/bold green]")
        return
        
    # 3. Interactive fallback / Direct Download
    console.print("[yellow]  ⚠ VirtIO ISO not found in standard paths.[/yellow]")
    
    # Stage the Readline Environment
    readline.set_completer_delims(' \t\n;')
    readline.parse_and_bind("tab: complete")
    readline.set_completer(path_completer)
    
    while True:
        console.print("\n[bold cyan]Provide absolute path to local ISO (Tab-completion enabled)[/bold cyan]")
        console.print("[dim]Or leave blank to stream directly from the Fedora Project network:[/dim]")
        
        try:
            choice = input("Path > ").strip(' "\'')
        except EOFError:
            choice = ""
        except KeyboardInterrupt:
            console.print("\n\n[bold red]⚠ Process interrupted by operator. Exiting cleanly.[/bold red]\n")
            sys.exit(130)
            
        # Resolve '~/' to the actual human user home for the final Path check
        if choice.startswith('~/'):
            sudo_user = os.environ.get("SUDO_USER", "")
            if sudo_user:
                try:
                    home_dir = pwd.getpwnam(sudo_user).pw_dir
                    choice = os.path.join(home_dir, choice[2:])
                except KeyError:
                    choice = os.path.expanduser(choice)
            else:
                choice = os.path.expanduser(choice)
        else:
            choice = os.path.expanduser(choice)
            
        match choice:
            case "":
                default_local_path = Path("/mnt/zram1/virtio-win-0.1.285.iso")
                if default_local_path.exists():
                    console.print(f"  [cyan]Cloning default local ISO from {default_local_path}...[/cyan]")
                    shutil.copy2(default_local_path, target_iso)
                    console.print(f"[bold green]  ✓ Default local ISO safely cloned to libvirt pool.[/bold green]")
                else:
                    url = "https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso"
                    console.print(f"  [cyan]Initiating direct stream from {url}...[/cyan]")
                    download_iso_stream(url, target_iso)
                    console.print(f"[bold green]  ✓ ISO provisioned directly to {target_iso}[/bold green]")
                break
            case path_str if Path(path_str).is_file():
                local_path = Path(path_str)
                console.print(f"  [cyan]Cloning ISO from {local_path}...[/cyan]")
                shutil.copy2(local_path, target_iso)
                console.print(f"[bold green]  ✓ Local ISO safely cloned to libvirt pool.[/bold green]")
                break
            case _:
                console.print("[bold red]  ✖ Invalid path or file does not exist. Try again.[/bold red]")
                
    # Unbind Readline Environment
    readline.set_completer(None)

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================
def main() -> None:
    console.clear()
    console.print(Panel("[bold green]KVM GPU Passthrough: Phase 1 Staging[/bold green]\nTarget: Arch Linux | Kernel 7.1.0+", expand=False))
    
    try:
        target_user = resolve_target_user()
        stage_packages()
        aur_success = try_aur_virtio(target_user)
        configure_access(target_user)
        stage_iso(aur_success)
        
        console.print("\n[bold green]=== PHASE 1 COMPLETE ===[/bold green]")
        console.print("The host file system and user access rights are fully prepared.")
        console.print("Ready to proceed to Phase 2 (systemd-boot initramfs injection & libvirt daemon configuration).\n")

    except KeyboardInterrupt:
        console.print("\n\n[bold red]⚠ Process interrupted by operator. Exiting cleanly.[/bold red]\n")
        sys.exit(130)

if __name__ == "__main__":
    main()
