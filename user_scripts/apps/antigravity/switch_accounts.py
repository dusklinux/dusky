#!/usr/bin/env python3

import sys
import subprocess
import shutil
import importlib.util
import os
import re
import argparse
from pathlib import Path

# ==========================================
# 1. AUTONOMOUS FAIL-SAFE DEPENDENCY RESOLVER
# ==========================================
def resolve_dependencies() -> None:
    """Bulletproof, iterative dependency resolver with PIP/AUR fallbacks."""
    requirements = {
        "rich": {"pac": "python-rich", "pip": "rich"},
        "keyring": {"pac": "python-keyring", "pip": "keyring"},
        "secretstorage": {"pac": "python-secretstorage", "pip": "SecretStorage"},
        "dbus": {"pac": "python-dbus", "pip": "dbus-python"},
        "questionary": {"pac": "python-questionary", "pip": "questionary"},
        "psutil": {"pac": "python-psutil", "pip": "psutil"}
    }

    # Execute at C-speed to dynamically map missing modules
    missing = [mod for mod in requirements if importlib.util.find_spec(mod) is None]
    
    if not missing:
        return

    print(f"\n[*] Missing dependencies detected: {', '.join(missing)}")
    print("[*] Engaging autonomous fail-safe resolver...\n")

    # Force sudo cache refresh to prevent hidden hangs in subprocesses
    subprocess.run(["sudo", "-v"], check=False)

    # Auto-detect common Arch AUR helpers
    aur_helper = next((h for h in ["paru", "yay"] if shutil.which(h)), None)

    for mod in missing:
        pkg_pac = requirements[mod]["pac"]
        pkg_pip = requirements[mod]["pip"]
        print(f" -> Resolving '{mod}'...")
        
        success = False

        # Try 1: AUR Helper (Best for Arch ecosystem)
        if aur_helper:
            res = subprocess.run([aur_helper, "-S", "--needed", "--noconfirm", pkg_pac], 
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0:
                success = True

        # Try 2: Standard Pacman
        if not success:
            res = subprocess.run(["sudo", "pacman", "-S", "--needed", "--noconfirm", pkg_pac], 
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0:
                success = True

        # Try 3: PEP-668 Pip Injection (Bleeding Edge Fallback)
        if not success:
            print(f"    [!] '{pkg_pac}' absent from repos. Injecting via pip bypass...")
            res = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--user", "--break-system-packages", pkg_pip],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            if res.returncode == 0:
                success = True

        if not success:
            print(f"\n[✖] FATAL: Absolute failure resolving '{mod}'.")
            sys.exit(1)

    print("\n[✔] Matrix dependencies successfully satisfied. Rebooting manager...\n")
    os.execv(sys.executable, [sys.executable] + sys.argv)

resolve_dependencies()

# ==========================================
# 2. HYPRLAND / D-BUS PRE-FLIGHT CHECK
# ==========================================
if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
    print("\n[!] FATAL: DBUS_SESSION_BUS_ADDRESS is not exposed in this environment.")
    print("[!] Keyring requires a valid D-Bus session under Wayland/Hyprland.")
    print("[!] Ensure you are executing this within a proper terminal session.\n")
    sys.exit(1)

from rich.console import Console
from rich.theme import Theme
from rich.panel import Panel
from rich.table import Table
from rich.align import Align
from rich.text import Text
from rich.rule import Rule
import keyring
import keyring.errors
import questionary
import psutil

# ==========================================
# 3. UI THEMING
# ==========================================
custom_theme = Theme({
    "info": "dim cyan",
    "warning": "bold yellow",
    "error": "bold red",
    "success": "bold green",
    "highlight": "bold magenta",
    "muted": "dim white"
})

console = Console(theme=custom_theme)

# ==========================================
# 4. MODERN TYPE ALIASES (Python 3.12+)
# ==========================================
type ProcList = list[psutil.Process]
type ProfileList = list[str]

# ==========================================
# 5. CORE MANAGER CLASS
# ==========================================
class ProfileManager:
    def __init__(self) -> None:
        # The exclusive, XDG-compliant storage directory for the profile manager
        self.storage_dir = Path.home() / ".config" / "dusky" / "settings" / "apps" / "antigravity"
        self.profiles_dir = self.storage_dir / "profiles"
        self.active_profile_file = self.profiles_dir / "active_profile.txt"
        
        try:
            self.profiles_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            console.print(f"[error]✖ Fatal: Insufficient permissions to create directory in {self.storage_dir}[/error]")
            sys.exit(1)

    @staticmethod
    def is_valid_name(name: str) -> bool:
        """Strict alphanumeric, dash, and underscore validation."""
        return bool(re.match(r"^[a-zA-Z0-9_-]+$", name))

    def get_active(self) -> str | None:
        """Retrieve the verified active profile name using strict pathlib logic."""
        if self.active_profile_file.is_file():
            try:
                name = self.active_profile_file.read_text(encoding="utf-8").strip()
                if name and (self.profiles_dir / name).is_dir():
                    return name
            except IOError as e:
                console.print(f"[warning]⚠ State read error: {e}[/warning]")
        return None

    def get_all(self) -> ProfileList:
        """Return a sorted array of all valid profile directories."""
        try:
            return sorted(p.name for p in self.profiles_dir.iterdir() if p.is_dir())
        except IOError:
            return []

    def check_running_processes(self) -> ProcList:
        """Kernel-level mapping of running Antigravity processes."""
        procs: ProcList = []
        current_pid, parent_pid = os.getpid(), os.getppid()
        
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if proc.info['pid'] in (current_pid, parent_pid):
                    continue
                    
                name = proc.info['name'] or ""
                cmdline = " ".join(proc.info['cmdline'] or []).lower()
                
                if "antigravity" in name.lower() or "agy" in name.lower() or "antigravity" in cmdline:
                    procs.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
        return procs

    def kill_processes(self, processes: ProcList) -> None:
        """Forcibly terminate blocking processes."""
        for proc in processes:
            try:
                proc.terminate()
            except psutil.NoSuchProcess:
                continue
        
        # Await graceful SIGTERM exit, escalate to SIGKILL if blocked
        gone, alive = psutil.wait_procs(processes, timeout=3.0)
        for proc in alive:
            try:
                proc.kill() 
            except psutil.NoSuchProcess:
                pass
                
        console.print("[success]✔ Conflicting processes successfully eradicated.[/success]")

    def stash_keyring(self, profile_name: str) -> None:
        """Extract and securely stash the auth token with 0o600 permissions."""
        try:
            token = keyring.get_password("gemini", "antigravity")
            token_file = self.profiles_dir / profile_name / "keyring_token.json"
            if token:
                # Security Override: Ensure file is exclusively readable/writable by the owner
                token_file.touch(mode=0o600, exist_ok=True)
                token_file.write_text(token, encoding="utf-8")
                console.print(f"[info]ℹ Secured auth token to '{profile_name}'.[/info]")
        except keyring.errors.KeyringError as e:
            console.print(f"[warning]⚠ Keyring subsystem failure during stash: {e}[/warning]")
        except IOError as e:
            console.print(f"[warning]⚠ Filesystem IO error during credential save: {e}[/warning]")

    def restore_keyring(self, profile_name: str) -> None:
        """Restore stashed keyring token or securely purge the global state."""
        token_file = self.profiles_dir / profile_name / "keyring_token.json"
        if token_file.is_file():
            try:
                token = token_file.read_text(encoding="utf-8").strip()
                if token:
                    keyring.set_password("gemini", "antigravity", token)
                    console.print(f"[info]ℹ Restored auth credentials from '{profile_name}'.[/info]")
            except keyring.errors.KeyringError as e:
                console.print(f"[error]✖ Keyring subsystem failure during restore: {e}[/error]")
            except IOError as e:
                console.print(f"[error]✖ Filesystem IO error during credential read: {e}[/error]")
        else:
            try:
                keyring.delete_password("gemini", "antigravity")
                console.print("[info]ℹ Purged global auth state (profile initialized fresh).[/info]")
            except keyring.errors.PasswordDeleteError:
                pass 

    def switch(self, target_profile: str) -> None:
        """Execute atomic context switch."""
        if not self.is_valid_name(target_profile):
            console.print(f"[error]✖ Fatal: Invalid profile syntax '{target_profile}'.[/error]")
            sys.exit(1)

        running_procs = self.check_running_processes()
        if running_procs:
            console.print(f"\n[warning]⚠ {len(running_procs)} Active Antigravity process(es) detected![/warning]")
            action = questionary.select(
                "Resolve collision:",
                choices=[
                    questionary.Choice("Abort (Safe)", value="cancel"),
                    questionary.Choice("SIGKILL & Proceed", value="kill"),
                    questionary.Choice("Ignore & Proceed (Risky)", value="ignore")
                ],
                style=questionary.Style([('pointer', 'fg:ansiyellow bold')])
            ).ask()
            
            match action:
                case "cancel" | None:
                    console.print("[error]Operation aborted.[/error]")
                    return
                case "kill":
                    self.kill_processes(running_procs)
                case "ignore":
                    console.print("[warning]Proceeding with collision risk...[/warning]")

        current_profile = self.get_active()
        if current_profile == target_profile:
            console.print(f"[info]ℹ State unchanged. Already on '{target_profile}'.[/info]")
            return

        if current_profile:
            self.stash_keyring(current_profile)

        target_path = self.profiles_dir / target_profile
        try:
            target_path.mkdir(parents=True, exist_ok=True)
            self.restore_keyring(target_profile)
            self.active_profile_file.write_text(target_profile, encoding="utf-8")
        except IOError as e:
            console.print(f"[error]✖ Fatal IO fault during state switch: {e}[/error]")
            sys.exit(1)
            
        console.print(f"\n[success]✔ Switched to isolated profile: '{target_profile}'.[/success]")

    def cycle_next(self) -> None:
        """Math-based array indexing for sequential cycling."""
        profiles = self.get_all()
        if not profiles:
            console.print("[error]✖ Error: Array is empty. No profiles to cycle.[/error]")
            return
            
        active = self.get_active()
        next_profile = profiles[0] if active not in profiles else profiles[(profiles.index(active) + 1) % len(profiles)]
            
        console.print(f"\n[info]⟳ Iterating to next sequential profile...[/info]")
        self.switch(next_profile)

    def create(self, name: str) -> None:
        """Mint a new profile structure."""
        if not self.is_valid_name(name):
            console.print("[error]✖ Syntax Error: Alphanumeric, dash, and underscores exclusively.[/error]")
            return
            
        profile_path = self.profiles_dir / name
        if profile_path.is_dir():
            console.print(f"[error]✖ Collision: Profile '{name}' already exists.[/error]")
            return
            
        try:
            profile_path.mkdir(parents=True)
            console.print(f"[success]✔ Initialized isolated context: '{name}'.[/success]")
            if questionary.confirm("Execute context switch to new profile now?").ask():
                self.switch(name)
        except IOError as e:
            console.print(f"[error]✖ IO Error during initialization: {e}[/error]")

    def delete(self, name: str) -> None:
        """Recursively eradicate a profile and its local state."""
        if name == self.get_active():
            console.print("[error]✖ State lock: Cannot wipe the active profile. Cycle first.[/error]")
            return
            
        profile_path = self.profiles_dir / name
        if not profile_path.is_dir():
            console.print(f"[error]✖ Missing Reference: '{name}' does not exist.[/error]")
            return
            
        if questionary.confirm(f"Permanently wipe '{name}' and all isolated data?").ask():
            try:
                shutil.rmtree(profile_path)
                console.print(f"[success]✔ Profile '{name}' successfully eradicated.[/success]")
            except IOError as e:
                console.print(f"[error]✖ IO Fault during deletion: {e}[/error]")

    def render_dashboard(self) -> None:
        """Render the primary matrix via Rich."""
        active = self.get_active()
        profiles = self.get_all()
        
        table = Table(title="Local Isolation Matrix", title_style="highlight", border_style="magenta", expand=True)
        table.add_column("Index", justify="right", style="cyan", no_wrap=True)
        table.add_column("State", justify="center", no_wrap=True)
        table.add_column("Identity Matrix", style="success")
        table.add_column("Keyring Cache", justify="center", no_wrap=True)
        
        for idx, p in enumerate(profiles, start=1):
            is_active = p == active
            status_text = Text("● ACTIVE", style="bold green") if is_active else Text("○ STANDBY", style="dim white")
            
            token_file = self.profiles_dir / p / "keyring_token.json"
            auth_state = Text("Secured", style="bold cyan") if (token_file.is_file() and token_file.stat().st_size > 0) else Text("Void", style="dim yellow")
            
            table.add_row(str(idx), status_text, p, auth_state)
            
        console.print(Rule(style="dim magenta"))
        if not profiles:
            console.print(Align.center("[muted]Matrix empty. Mint an identity to begin.[/muted]"))
        else:
            console.print(table)
        console.print(Rule(style="dim magenta"))


# ==========================================
# 6. ROUTER & EVENT LOOP
# ==========================================
def build_profile_choices(profiles: ProfileList) -> list[questionary.Choice]:
    """Helper to generate questionary choices with a dedicated back button."""
    choices = [questionary.Choice(p, value=p) for p in profiles]
    choices.append(questionary.Choice("↩ Cancel / Go Back", value=None))
    return choices

def interactive_tui(manager: ProfileManager) -> None:
    """Primary asynchronous-style event loop for the TUI."""
    while True:
        console.clear()
        
        title = Text("🚀 Antigravity Architecture", style="bold magenta")
        subtitle = Text("State isolation and identity multiplexer", style="italic cyan")
        console.print(Panel(Align.center(Text.assemble(title, "\n", subtitle)), border_style="magenta"))
        
        manager.render_dashboard()
        
        profiles = manager.get_all()
        main_choices = []
        
        if profiles:
            main_choices.append(questionary.Choice("Switch Identity", value="switch"))
            main_choices.append(questionary.Choice("Iterate Sequentially", value="cycle"))
        
        main_choices.extend([
            questionary.Choice("Mint Identity", value="create"),
            questionary.Choice("Eradicate Identity", value="delete", disabled="Matrix empty" if not profiles else None),
            questionary.Choice("Force Auth Stash", value="stash", disabled="No active state" if not manager.get_active() else None),
            questionary.Choice("Terminate Session", value="quit")
        ])

        try:
            console.print("")
            action = questionary.select(
                "Execute directive:",
                choices=main_choices,
                use_indicator=True,
                pointer="❯",
                style=questionary.Style([('pointer', 'fg:ansimagenta bold')])
            ).ask()
        except KeyboardInterrupt:
            break

        console.print("")
        
        match action:
            case "quit" | None:
                console.print("[info]Session terminated.[/info]")
                break
            case "switch":
                target = questionary.select(
                    "Select target index:", 
                    choices=build_profile_choices(profiles),
                    style=questionary.Style([('pointer', 'fg:ansimagenta bold')])
                ).ask()
                
                if target: 
                    manager.switch(target)
                    questionary.press_any_key_to_continue("\nPress any key to return...").ask()
            case "cycle":
                manager.cycle_next()
                questionary.press_any_key_to_continue("\nPress any key to return...").ask()
            case "create":
                name = questionary.text("Designation for new identity (leave blank to cancel):").ask()
                
                if name and name.strip(): 
                    manager.create(name.strip())
                    questionary.press_any_key_to_continue("\nPress any key to return...").ask()
            case "delete":
                target = questionary.select(
                    "Select index for eradication:", 
                    choices=build_profile_choices(profiles),
                    style=questionary.Style([('pointer', 'fg:ansimagenta bold')])
                ).ask()
                
                if target: 
                    manager.delete(target)
                    questionary.press_any_key_to_continue("\nPress any key to return...").ask()
            case "stash":
                active_profile = manager.get_active()
                if active_profile:
                    manager.stash_keyring(active_profile)
                    questionary.press_any_key_to_continue("\nPress any key to return...").ask()

def main() -> None:
    manager = ProfileManager()
    
    parser = argparse.ArgumentParser(description="Antigravity Architecture Multiplexer")
    parser.add_argument("profile", nargs="?", help="Direct index override")
    parser.add_argument("-l", "--list", action="store_true", help="Dump matrix and exit")
    parser.add_argument("-n", "--next", action="store_true", help="Iterate matrix and exit")
    
    args = parser.parse_args()

    if args.list:
        manager.render_dashboard()
    elif args.next:
        manager.cycle_next()
    elif args.profile:
        manager.switch(args.profile)
    else:
        interactive_tui(manager)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[error]Process killed via SIGINT.[/error]")
        sys.exit(130)
