#!/usr/bin/env python3
import subprocess
import sys
import os
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.text import Text

console = Console()

def run_command(command: str, description: str, critical: bool = True):
    """Executes a shell command with a Rich status spinner."""
    console.print(f"\n[bold cyan]Target:[/bold cyan] {description}")
    console.print(f"[bold black on white] {command} [/bold black on white]")
    
    if not Confirm.ask("[bold yellow]Execute this step?[/bold yellow]", default=True):
        console.print("[dim]Skipped by user.[/dim]")
        return True

    with console.status(f"[bold green]Running: {command}...[/bold green]", spinner="dots"):
        try:
            # Use shell=True for complex commands, pipe stdout/stderr
            process = subprocess.run(
                command, 
                shell=True, 
                check=True, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                text=True
            )
            console.print("[bold green]✔ Success![/bold green]")
            return True
        except subprocess.CalledProcessError as e:
            console.print(f"[bold red]✘ Failed with exit code {e.returncode}[/bold red]")
            console.print(Panel(e.stderr.strip(), title="Error Output", border_style="red"))
            if critical:
                console.print("[bold red]Critical step failed. Aborting script to prevent system instability.[/bold red]")
                sys.exit(1)
            return False

def check_root():
    """Ensure the user has sudo privileges before starting."""
    if os.geteuid() == 0:
        console.print("[bold red]Please run this script as your normal user, not directly as root. Sudo will be invoked when needed.[/bold red]")
        sys.exit(1)

def main():
    console.clear()
    console.print(Panel.fit(
        "[bold magenta]Arch Linux Golden Gaming Setup[/bold magenta]\n"
        "[white]Comprehensive automated installer for Drivers, Steam, Bottles, & Flatpaks.[/white]",
        border_style="magenta"
    ))

    check_root()

    # Step 1: System Sync
    run_command(
        "sudo pacman -Syu --noconfirm",
        "Synchronize package databases and update system to prevent dependency breakage."
    )

    # Step 2: Multilib Repository
    console.print(Panel(
        "[bold]The [multilib] repository is MANDATORY for Steam and 32-bit Windows games.[/bold]\n"
        "If you have already enabled this in /etc/pacman.conf, you can skip this step.\n"
        "Otherwise, this command will safely uncomment the multilib lines.",
        style="yellow"
    ))
    run_command(
        "sudo sed -i '/^#\\[multilib\\]/{s/^#//;n;s/^#//}' /etc/pacman.conf && sudo pacman -Sy",
        "Enable 32-bit multilib repository in pacman.conf"
    )

    # Step 3: GPU Drivers
    console.print("\n[bold cyan]Select your GPU Vendor for Vulkan Drivers:[/bold cyan]")
    console.print("1. AMD (Radeon)")
    console.print("2. NVIDIA")
    console.print("3. Skip (Already installed)")
    gpu_choice = Prompt.ask("Enter choice", choices=["1", "2", "3"], default="3")
    
    if gpu_choice == "1":
        run_command(
            "sudo pacman -S --needed vulkan-radeon lib32-vulkan-radeon mesa lib32-mesa",
            "Install AMD native and 32-bit Vulkan/Mesa drivers."
        )
    elif gpu_choice == "2":
        run_command(
            "sudo pacman -S --needed nvidia-utils lib32-nvidia-utils",
            "Install NVIDIA proprietary utilities and 32-bit Vulkan drivers."
        )

    # Step 4: Core Native Gaming Tools
    run_command(
        "sudo pacman -S --needed steam flatpak gamemode lib32-gamemode mangohud lib32-mangohud",
        "Install Steam, Flatpak daemon, Feral GameMode (CPU optimizer), and MangoHud (Performance overlay)."
    )

    # Step 5: Flatpak Repository
    run_command(
        "flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo",
        "Ensure the Flathub remote is configured for software installation."
    )

    # Step 6: Flatpak Gaming Applications
    flatpak_apps = {
        "Bottles": "com.usebottles.bottles",
        "Flatseal": "com.github.tchx84.Flatseal",
        "ProtonPlus": "com.vysp3r.ProtonPlus"
    }

    for app_name, app_id in flatpak_apps.items():
        run_command(
            f"flatpak install flathub {app_id} -y",
            f"Install {app_name} via Flatpak sandbox.",
            critical=False
        )

    # Step 7: Final Polish
    console.print(Panel.fit(
        "[bold green]✔ Setup Complete![/bold green]\n"
        "Your Arch system is now a fully optimized gaming environment.\n\n"
        "[bold]Next Steps for Forza Horizon 6:[/bold]\n"
        "1. Open [cyan]Flatseal[/cyan] and grant [cyan]Bottles[/cyan] permission to your separate partition.\n"
        "2. Open [cyan]Bottles[/cyan], create a 'Gaming' environment, and run the FitGirl setup.exe.\n"
        "3. Remember to check the [bold red]'Limit installer to 2GB'[/bold red] box during installation!",
        border_style="green"
    ))

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold red]Installation aborted by user.[/bold red]")
        sys.exit(0)
