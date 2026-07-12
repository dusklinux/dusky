#!/usr/bin/env python3
"""
Firefox Performance Optimization Script for Arch Linux & Hyprland
Optimizes memory usage, process models (Fission), hardware acceleration (VA-API),
Wayland native environments, profile caching in tmpfs, and database vacuuming.
Fully dynamic, scaling parameters to the system's total RAM.
Fully idempotent, self-healing, and user-independent (no hardcoded usernames).
"""

import argparse
import configparser
import getpass
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Final, Literal

# ==========================================
# Rich UI Auto-Installer & Setup
# ==========================================
try:
    from rich.console import Console
    from rich.logging import RichHandler
    from rich.panel import Panel
    from rich.prompt import Confirm
    from rich.text import Text
except ImportError:
    print("\033[94m[INFO]\033[0m Missing 'rich' library. Auto-installing via pacman...")
    try:
        subprocess.run(["sudo", "pacman", "-S", "--needed", "--noconfirm", "python-rich"], check=True)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        print(f"\033[91m[ERROR]\033[0m Failed to auto-install dependencies: {e}")
        sys.exit(1)

# Set up beautiful logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True, markup=True)]
)
logger = logging.getLogger("firefox_optimizer")
console = Console()

# ==========================================
# Constants
# ==========================================
USER_JS_BEGIN: Final[str] = "// === BEGIN FIREFOX OPTIMIZATION SUITE ==="
USER_JS_END: Final[str] = "// === END FIREFOX OPTIMIZATION SUITE ==="


MAX_BACKUPS_PER_PROFILE: Final[int] = 3

type CacheMode = Literal["tmpfs", "memory", "default"]


def get_clean_device_path(device_source: str) -> str:
    """Strips the subvolume brackets from btrfs mount sources."""
    if "[" in device_source:
        return device_source.split("[")[0].strip()
    return device_source.strip()


def is_luks_or_crypt_device(path: Path) -> bool:
    """Checks if the path is located on a LUKS or dm-crypt device using findmnt and lsblk."""
    try:
        res = subprocess.run(
            ["findmnt", "-n", "-o", "SOURCE", "-T", str(path)],
            capture_output=True,
            text=True,
            check=False
        )
        if res.returncode != 0 or not res.stdout.strip():
            return False
            
        source_dev = get_clean_device_path(res.stdout.strip())
        if not source_dev:
            return False
            
        if "/dev/mapper/" in source_dev or "/dev/dm-" in source_dev:
            lsblk_res = subprocess.run(
                ["lsblk", "-d", "-o", "TYPE", source_dev],
                capture_output=True,
                text=True,
                check=False
            )
            if lsblk_res.returncode == 0 and "crypt" in lsblk_res.stdout.lower():
                return True
    except Exception as e:
        logger.warning(f"Error checking if {path} is on crypt device: {e}")
    return False


def has_overlayfs_support() -> bool:
    """Checks if the kernel currently supports overlayfs by scanning /proc/filesystems."""
    try:
        proc_filesystems = Path("/proc/filesystems")
        if proc_filesystems.exists():
            if "overlay" in proc_filesystems.read_text():
                return True
    except Exception as e:
        logger.warning(f"Error reading /proc/filesystems: {e}")
        
    try:
        res = subprocess.run(["modprobe", "overlay"], capture_output=True, text=True)
        if res.returncode == 0:
            return True
    except Exception:
        pass
        
    return False


def backup_profile(
    profile_dir: Path,
    dry_run: bool,
    backup_dir_override: Path | None = None,
    force_backup_outside_luks: bool = False
) -> None:
    """Creates a tarball backup and strictly rotates old backups to prevent drive pollution."""
    import tarfile
    from datetime import datetime
    
    if not profile_dir.exists():
        return
        
    default_backup_dir = profile_dir.parent.parent
    backup_dir = backup_dir_override if backup_dir_override else default_backup_dir
    
    profile_on_luks = is_luks_or_crypt_device(profile_dir)
    backup_on_luks = is_luks_or_crypt_device(backup_dir)
    
    if profile_on_luks and not backup_on_luks:
        logger.warning(
            f"[bold red]SECURITY WARNING:[/] Backing up an encrypted profile to a non-encrypted location: [yellow]{backup_dir}[/]. "
            "This will expose passwords when the LUKS drive is locked."
        )
        if not force_backup_outside_luks:
            logger.error("Aborting backup for security reasons. Use [cyan]--force-backup-outside-luks[/] to override.")
            sys.exit(1)
            
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"firefox-backup-{profile_dir.name}-{timestamp}.tar.gz"
    backup_path = backup_dir / backup_filename
    
    if dry_run:
        logger.info(f"[dim][Dry Run] Would create profile backup at {backup_path}[/]")
        return
        
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Creating profile backup: [cyan]{backup_path.name}[/]...")
        with tarfile.open(backup_path, "w:gz") as tar:
            tar.add(profile_dir, arcname=profile_dir.name)
        logger.info("[bold green]Backup created successfully.[/]")
        
        # --- FIXED LOGIC: Backup Rotation / Drive Pollution Prevention ---
        existing_backups = sorted(
            backup_dir.glob(f"firefox-backup-{profile_dir.name}-*.tar.gz"),
            key=lambda p: p.stat().st_mtime
        )
        
        if len(existing_backups) > MAX_BACKUPS_PER_PROFILE:
            excess = len(existing_backups) - MAX_BACKUPS_PER_PROFILE
            logger.info(f"Pruning [yellow]{excess}[/] old backup(s) to maintain retention limit of {MAX_BACKUPS_PER_PROFILE}...")
            for old_backup in existing_backups[:excess]:
                try:
                    old_backup.unlink()
                    logger.info(f"[dim]Deleted old backup: {old_backup.name}[/]")
                except Exception as e:
                    logger.warning(f"Failed to delete old backup {old_backup.name}: {e}")
                    
    except Exception as e:
        logger.error(f"[bold red]Failed to create profile backup:[/] {e}")
        logger.error("Aborting optimization because backup failed.")
        sys.exit(1)


def disable_psd_service(dry_run: bool) -> None:
    """Stops and disables psd.service cleanly if it is active or enabled."""
    if dry_run:
        logger.info("[dim][Dry Run] Would stop and disable psd.service[/]")
        return

    logger.info("Stopping and disabling [bold yellow]profile-sync-daemon[/] service (LUKS device conflict)...")
    subprocess.run(["systemctl", "--user", "disable", "--now", "psd.service"], capture_output=True)


def get_total_ram_gb() -> float:
    """Dynamically detects total physical RAM in Gigabytes using os.sysconf."""
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        total_bytes = pages * page_size
        return total_bytes / (1024**3)
    except Exception as e:
        logger.warning(f"Failed to detect RAM size via sysconf: {e}. Defaulting to 8GB.")
        return 8.0


def is_firefox_running() -> bool:
    """Checks if a process named 'firefox' is currently running."""
    try:
        res = subprocess.run(["pgrep", "-x", "firefox"], capture_output=True)
        return res.returncode == 0
    except Exception:
        return False


def prompt_user_tty(message: str = "") -> str:
    """Prompts the user directly via /dev/tty using Rich for beautiful formatting."""
    if not sys.stdin.isatty():
        try:
            val = sys.stdin.readline()
            if not val:
                return "NON_INTERACTIVE"
            return val.strip()
        except Exception:
            return "NON_INTERACTIVE"

    try:
        tty_out = open("/dev/tty", "w")
        tty_in = open("/dev/tty", "r")
        
        console_tty = Console(file=tty_out, force_terminal=True)
        
        panel_content = (
            "[bold red]Firefox is currently running![/bold red]\n\n"
            "Firefox must be closed to prevent SQLite database locks or profile corruption.\n\n"
            "[bold cyan]Please select an option:[/bold cyan]\n"
            "  [bold yellow]1[/bold yellow]) Close Firefox yourself, then press Enter to re-check\n"
            "  [bold yellow]2[/bold yellow]) Let the script close Firefox for you now\n"
            "  [bold yellow]3[/bold yellow]) Skip Firefox optimization for now [dim](exits cleanly)[/dim]"
        )
        
        console_tty.print(Panel(panel_content, title="[bold white]Action Required[/bold white]", border_style="yellow"))
        tty_out.write("Selection [1/2/3]: ")
        tty_out.flush()
        
        choice = tty_in.readline().strip()
        
        tty_out.close()
        tty_in.close()
        return choice
    except Exception:
        print("\n[!] Firefox is running. Close it manually or abort.\nSelection [1/2/3]: ", end="", flush=True)
        try:
            return sys.stdin.readline().strip()
        except Exception:
            return "NON_INTERACTIVE"


def handle_firefox_open_check(dry_run: bool) -> None:
    """Checks if Firefox is running. Prompts the user to close it, autoclose it, or skip."""
    import time
    attempts = 0
    
    while is_firefox_running():
        attempts += 1
        if attempts > 10:
            logger.error("[bold red]Too many attempts. Skipping Firefox optimization to prevent corruption.[/]")
            sys.exit(0)
            
        choice = prompt_user_tty()
        
        if choice == "NON_INTERACTIVE":
            logger.warning("Non-interactive terminal detected and Firefox is running.")
            logger.warning("Skipping Firefox optimization to prevent profile corruption.")
            sys.exit(0)
            
        choice = choice.strip()
        
        if choice in ("", "1"):
            logger.info("Re-checking if Firefox is closed in 2 seconds...")
            time.sleep(2)
            continue
        elif choice == "2":
            logger.info("Closing Firefox automatically...")
            if dry_run:
                logger.info("[dim][Dry Run] Would send SIGTERM to firefox processes.[/]")
                return
            subprocess.run(["pkill", "-x", "firefox"])
            for _ in range(10):
                time.sleep(0.5)
                if not is_firefox_running():
                    logger.info("[bold green]Firefox closed successfully.[/]")
                    return
            logger.warning("Firefox did not close cleanly. Sending SIGKILL...")
            subprocess.run(["pkill", "-9", "-x", "firefox"])
            time.sleep(1)
            if not is_firefox_running():
                logger.info("[bold green]Firefox force-closed successfully.[/]")
                return
            else:
                logger.error("Failed to close Firefox. Please close it manually.")
        elif choice == "3":
            logger.info("Skipping Firefox optimization as requested. Exiting cleanly.")
            sys.exit(0)
        else:
            logger.warning("Invalid selection. Please choose 1, 2, or 3.")


def get_sudo_password(cmd_pass: str | None) -> str | None:
    if cmd_pass:
        return cmd_pass
    if sys.stdout.isatty():
        try:
            return getpass.getpass("Enter sudo password to install package dependencies: ")
        except Exception:
            return None
    return None


def run_sudo_cmd(cmd: list[str], password: str | None) -> subprocess.CompletedProcess[str]:
    if not password:
        return subprocess.run(["sudo"] + cmd, capture_output=True, text=True)
    return subprocess.run(["sudo", "-S"] + cmd, input=f"{password}\n", capture_output=True, text=True)


def install_package_dependencies(sudo_pass: str | None, dry_run: bool) -> bool:
    dependencies = ["profile-sync-daemon", "profile-cleaner"]
    missing = []

    for dep in dependencies:
        res = subprocess.run(["pacman", "-Qi", dep], capture_output=True)
        if res.returncode != 0:
            missing.append(dep)

    if not missing:
        logger.info("[green]All package dependencies are already installed.[/green]")
        return True

    logger.info(f"Missing dependencies to install: [yellow]{missing}[/yellow]")
    if dry_run:
        logger.info(f"[dim][Dry Run] Would install: {missing}[/dim]")
        return True

    verified_pass = get_sudo_password(sudo_pass)
    can_sudo_noninteractive = subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0

    if not verified_pass and not can_sudo_noninteractive:
        logger.error("[bold red]Sudo password is required to install system packages. Aborting installation.[/]")
        return False

    logger.info("Installing packages ([cyan]pacman -S[/])...")
    res = run_sudo_cmd(["pacman", "-S", "--noconfirm", "--needed"] + missing, verified_pass)
    if res.returncode == 0:
        logger.info("[bold green]Successfully installed packages.[/]")
        return True
    else:
        logger.error(f"[bold red]Failed to install packages:[/] {res.stderr}")
        return False


def find_firefox_profiles() -> list[Path]:
    search_paths = [
        Path.home() / ".config" / "mozilla" / "firefox",
        Path.home() / ".mozilla" / "firefox",
    ]

    profiles: list[Path] = []
    for base_dir in search_paths:
        if not base_dir.exists():
            continue

        ini_path = base_dir / "profiles.ini"
        if ini_path.exists():
            config = configparser.ConfigParser()
            try:
                config.read(ini_path)
                for section in config.sections():
                    if section.startswith("Profile"):
                        path_str = config.get(section, "Path", fallback=None)
                        is_relative = config.getint(section, "IsRelative", fallback=1)
                        if path_str:
                            prof_path = base_dir / path_str if is_relative else Path(path_str)
                            if prof_path.exists() and prof_path.is_dir():
                                profiles.append(prof_path)
            except Exception as e:
                logger.warning(f"Error parsing {ini_path}: {e}")

        for sub in base_dir.iterdir():
            if sub.is_dir() and (sub.suffix == ".default" or "default-release" in sub.name):
                if any(x in sub.name for x in ["-backup", "-back-ovfs"]):
                    continue
                if sub not in profiles:
                    profiles.append(sub)

    unique_profiles = []
    for p in profiles:
        resolved = p.resolve()
        if resolved not in unique_profiles:
            unique_profiles.append(resolved)

    return unique_profiles


def get_optimization_prefs(ram_gb: float, cache_mode: CacheMode) -> dict[str, str | int | bool]:
    uid = os.getuid()
    capacity_kb = 1048576 
    shared_ipc = 8
    isolated_ipc = 32
    ext_ipc = 1
    unload_low_mem = True

    if ram_gb > 30.0:
        logger.info(f"System has [bold cyan]{ram_gb:.1f} GB[/] RAM (> 30 GB). Enabling [bold green]Ultra high-performance[/] profile.")
        capacity_kb = 4194304  # 4 GB
        shared_ipc = 32
        isolated_ipc = 99
        unload_low_mem = False
    elif ram_gb >= 16.0:
        logger.info(f"System has [cyan]{ram_gb:.1f} GB[/] RAM. Enabling [green]High-performance[/] profile.")
        capacity_kb = 2097152  # 2 GB
        shared_ipc = 16
        isolated_ipc = 64
        unload_low_mem = False
    else:
        logger.info(f"System has [cyan]{ram_gb:.1f} GB[/] RAM (< 16 GB). Scaling to Moderate-performance profile.")

    prefs: dict[str, str | int | bool] = {
        "browser.cache.memory.enable": True,
        "browser.cache.memory.capacity": capacity_kb,
        "browser.cache.disk.smart_size.enabled": False,
        "browser.cache.disk_cache_ssl": False,
        "browser.cache.offline.enable": False,
        "dom.ipc.processCount": shared_ipc,
        "dom.ipc.processCount.webIsolated": isolated_ipc,
        "dom.ipc.processCount.extension": ext_ipc,
        "fission.autostart": True,
        "browser.tabs.unloadOnLowMemory": unload_low_mem,
        "gfx.webrender.all": True,
        "layers.acceleration.force-enabled": True,
        "media.ffmpeg.vaapi.enabled": True,
        "media.hardware-video-decoding.force-enabled": True,
        "widget.wayland-dmabuf-vaapi.enabled": True,
        "widget.wayland.opaque-region.enabled": False,
        "apz.gtk.kinetic_scroll.enabled": True,
        "toolkit.telemetry.enabled": False,
        "datareporting.healthreport.uploadEnabled": False,
        "app.normandy.enabled": False,
        "network.http.max-connections": 1800,
        "network.http.max-persistent-connections-per-server": 10,
        "network.trr.mode": 2,
        "network.trr.uri": "https://mozilla.cloudflare-dns.com/dns-query",
    }

    match cache_mode:
        case "tmpfs":
            prefs["browser.cache.disk.enable"] = True
            prefs["browser.cache.disk.parent_directory"] = f"/run/user/{uid}/firefox"
        case "memory":
            prefs["browser.cache.disk.enable"] = False
        case "default":
            pass

    return prefs


def format_pref_line(name: str, value: str | int | bool) -> str:
    if isinstance(value, bool):
        val_str = "true" if value else "false"
    elif isinstance(value, int):
        val_str = str(value)
    else:
        val_str = f'"{value}"'
    return f'user_pref("{name}", {val_str});'


def update_user_js(profile_dir: Path, prefs: dict[str, str | int | bool], dry_run: bool) -> None:
    user_js_path = profile_dir / "user.js"
    logger.info(f"Processing profile at [bold]{profile_dir.name}[/] (user.js)")

    lines = [USER_JS_BEGIN, f"// Auto-generated by Firefox System Optimizer"]
    for k, v in prefs.items():
        lines.append(format_pref_line(k, v))
    lines.append(USER_JS_END)
    block_content = "\n".join(lines) + "\n"

    content = ""
    if user_js_path.exists():
        try:
            content = user_js_path.read_text()
        except Exception as e:
            logger.error(f"Failed to read {user_js_path}: {e}")
            return

    pattern = re.compile(re.escape(USER_JS_BEGIN) + ".*?" + re.escape(USER_JS_END), re.DOTALL)
    content = pattern.sub("", content)

    lines_to_keep = []
    for line in content.splitlines():
        if USER_JS_BEGIN in line or USER_JS_END in line:
            continue
        is_managed_key = False
        for key in prefs.keys():
            if f'"{key}"' in line or f"'{key}'" in line:
                is_managed_key = True
                break
        if is_managed_key:
            continue
        lines_to_keep.append(line)

    cleaned_content = "\n".join(lines_to_keep).strip()
    new_content = cleaned_content + ("\n" if cleaned_content else "") + block_content

    if dry_run:
        logger.info(f"[dim][Dry Run] Would write to {user_js_path}[/dim]")
    else:
        try:
            user_js_path.write_text(new_content)
            logger.info(f"Successfully updated optimization settings in [cyan]{user_js_path}[/]")
        except Exception as e:
            logger.error(f"Failed to write to {user_js_path}: {e}")


def remove_user_js_optimizations(profile_dir: Path, dry_run: bool) -> None:
    # Key array remains identical for robust reversion
    managed_keys = [
        "browser.cache.memory.enable", "browser.cache.memory.capacity",
        "browser.cache.disk.smart_size.enabled", "browser.cache.disk_cache_ssl",
        "browser.cache.offline.enable", "dom.ipc.processCount",
        "dom.ipc.processCount.webIsolated", "dom.ipc.processCount.extension",
        "fission.autostart", "browser.tabs.unloadOnLowMemory",
        "gfx.webrender.all", "layers.acceleration.force-enabled",
        "media.ffmpeg.vaapi.enabled", "media.hardware-video-decoding.force-enabled",
        "widget.wayland-dmabuf-vaapi.enabled", "widget.wayland.opaque-region.enabled",
        "apz.gtk.kinetic_scroll.enabled", "toolkit.telemetry.enabled",
        "datareporting.healthreport.uploadEnabled", "app.normandy.enabled",
        "network.http.max-connections", "network.http.max-persistent-connections-per-server",
        "network.trr.mode", "network.trr.uri", "browser.cache.disk.enable",
        "browser.cache.disk.parent_directory",
    ]

    user_js_path = profile_dir / "user.js"
    if user_js_path.exists():
        logger.info(f"Removing configurations from [yellow]{user_js_path}[/]")
        try:
            content = user_js_path.read_text()
            pattern = re.compile(r"\n?" + re.escape(USER_JS_BEGIN) + ".*?" + re.escape(USER_JS_END) + r"\n?", re.DOTALL)
            content = pattern.sub("\n", content)

            lines_to_keep = []
            for line in content.splitlines():
                if USER_JS_BEGIN in line or USER_JS_END in line: continue
                if any(f'"{k}"' in line or f"'{k}'" in line for k in managed_keys): continue
                lines_to_keep.append(line)

            new_content = "\n".join(lines_to_keep).strip() + "\n"

            if dry_run:
                logger.info(f"[dim][Dry Run] Would clean optimization block from {user_js_path}[/]")
            else:
                if not new_content.strip():
                    user_js_path.unlink()
                    logger.info(f"Deleted empty {user_js_path}")
                else:
                    user_js_path.write_text(new_content)
                    logger.info(f"Cleaned optimization block from [cyan]{user_js_path}[/]")
        except Exception as e:
            logger.error(f"Failed to update/delete {user_js_path}: {e}")

    prefs_js_path = profile_dir / "prefs.js"
    if prefs_js_path.exists():
        try:
            prefs_content = prefs_js_path.read_text()
            lines_to_keep = []
            for line in prefs_content.splitlines():
                if any(f'"{k}"' in line for k in managed_keys): continue
                lines_to_keep.append(line)

            if dry_run:
                logger.info(f"[dim][Dry Run] Would scrub baked-in managed keys from {prefs_js_path}[/]")
            else:
                prefs_js_path.write_text("\n".join(lines_to_keep) + "\n")
                logger.info(f"Cleaned baked-in optimization preferences from [cyan]{prefs_js_path}[/]")
        except Exception as e:
            logger.error(f"Failed to clean {prefs_js_path}: {e}")


def configure_uwsm_env(dry_run: bool, remove: bool = False) -> None:
    legacy_env = Path.home() / ".config" / "uwsm" / "env.d" / "firefox"
    if legacy_env.exists():
        if dry_run:
            logger.info(f"[dim][Dry Run] Would remove legacy env file: {legacy_env}[/]")
        else:
            try:
                legacy_env.unlink()
                logger.info(f"Removed legacy env file: {legacy_env}")
            except Exception as e:
                logger.warning(f"Could not remove legacy env file {legacy_env}: {e}")
    elif remove:
        return

    env_file = Path.home() / ".config" / "hypr" / "source" / "environment_variables.lua"
    env_content = f"""-- Firefox environment variables
hl.env("MOZ_ENABLE_WAYLAND", "1")
hl.env("MOZ_USE_XINPUT2", "1")
"""

    if dry_run:
        logger.info(f"[dim][Dry Run] Would ensure Firefox env vars in {env_file}[/]")
    else:
        try:
            env_file.parent.mkdir(parents=True, exist_ok=True)
            current = env_file.read_text() if env_file.exists() else ""
            if "Firefox environment variables" not in current:
                with open(env_file, "a") as f:
                    f.write(env_content)
                logger.info(f"Appended Firefox environment variables to [cyan]{env_file}[/]")
            else:
                logger.info(f"Firefox environment variables already present in {env_file}")
        except Exception as e:
            logger.error(f"Failed to write Firefox environment variables: {e}")


def configure_psd_service(dry_run: bool) -> None:
    psd_conf_dir = Path.home() / ".config" / "psd"
    psd_conf_path = psd_conf_dir / "psd.conf"

    if not psd_conf_path.exists():
        logger.info("Initializing psd configuration...")
        if not dry_run:
            psd_conf_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(["psd", "p"], capture_output=True)

    if psd_conf_path.exists():
        logger.info(f"Modifying profile-sync-daemon config: [cyan]{psd_conf_path}[/]")
        try:
            content = psd_conf_path.read_text()
        except Exception as e:
            logger.error(f"Failed to read {psd_conf_path}: {e}")
            return

        use_overlayfs = "yes"
        if not has_overlayfs_support():
            logger.warning("[yellow]overlayfs kernel support is not available. Configuring PSD with USE_OVERLAYFS='no'.[/]")
            use_overlayfs = "no"

        directives = {
            "USE_OVERLAYFS": f'"{use_overlayfs}"',
            "BROWSERS": '"firefox"',
            "USE_BACKUPS": '"yes"',
            "BACKUP_LIMIT": '"5"',
        }

        lines = []
        for line in content.splitlines():
            if any(line.strip().startswith(f"{key}=") or line.strip().startswith(f"#{key}=") for key in directives):
                continue
            lines.append(line)

        for key, value in directives.items():
            lines.append(f"{key}={value}")

        content = "\n".join(lines) + "\n"

        if dry_run:
            logger.info(f"[dim][Dry Run] Would write modifications to {psd_conf_path}[/]")
        else:
            try:
                psd_conf_path.write_text(content)
                logger.info(f"Successfully updated [cyan]{psd_conf_path}[/]")
            except Exception as e:
                logger.error(f"Failed to write psd config: {e}")

    timer_dropin_dir = Path.home() / ".config" / "systemd" / "user" / "psd-resync.timer.d"
    timer_dropin_file = timer_dropin_dir / "frequency.conf"

    timer_content = """[Unit]
Description=Timer for Profile-sync-daemon - 10min

[Timer]
OnUnitActiveSec=
OnUnitActiveSec=10min
"""

    if dry_run:
        logger.info(f"[dim][Dry Run] Would create timer override at {timer_dropin_file}[/]")
    else:
        try:
            timer_dropin_dir.mkdir(parents=True, exist_ok=True)
            timer_dropin_file.write_text(timer_content)
            logger.info(f"Created systemd timer drop-in at [cyan]{timer_dropin_file}[/]")
        except Exception as e:
            logger.error(f"Failed to create timer override: {e}")

    if dry_run:
        logger.info("[dim][Dry Run] Would reload user systemd daemon and enable/start psd.service[/]")
    else:
        logger.info("Enabling and starting profile-sync-daemon services...")
        subprocess.run(["systemctl", "--user", "daemon-reload"])
        subprocess.run(["systemctl", "--user", "enable", "--now", "psd.service"])
        logger.info("[bold green]PSD services enabled.[/]")


def configure_profile_cleaner_weekly_timer(dry_run: bool) -> None:
    user_systemd_dir = Path.home() / ".config" / "systemd" / "user"
    service_path = user_systemd_dir / "profile-cleaner.service"
    timer_path = user_systemd_dir / "profile-cleaner.timer"

    service_content = """[Unit]
Description=Clean Firefox SQLite Databases
After=psd.service

[Service]
Type=oneshot
ExecStart=/usr/bin/profile-cleaner f
"""

    timer_content = """[Unit]
Description=Run Firefox SQLite database cleanup weekly

[Timer]
OnCalendar=weekly
Persistent=true

[Install]
WantedBy=timers.target
"""

    if dry_run:
        logger.info(f"[dim][Dry Run] Would write systemd user service & timer to {user_systemd_dir}[/]")
    else:
        try:
            user_systemd_dir.mkdir(parents=True, exist_ok=True)
            service_path.write_text(service_content)
            timer_path.write_text(timer_content)
            logger.info("Created weekly [yellow]profile-cleaner[/] service and timer files.")

            subprocess.run(["systemctl", "--user", "daemon-reload"])
            subprocess.run(["systemctl", "--user", "enable", "--now", "profile-cleaner.timer"])
            logger.info("[bold green]profile-cleaner timer started and enabled.[/]")
        except Exception as e:
            logger.error(f"Failed to set up weekly database cleaning: {e}")


def disable_optimizations(dry_run: bool) -> None:
    logger.warning("[bold yellow]Please ensure Firefox is completely closed before proceeding, or reversion changes may be overwritten.[/]")
    
    profiles = find_firefox_profiles()
    if not profiles:
        logger.warning("No Firefox profiles located during disable phase.")
    for profile in profiles:
        remove_user_js_optimizations(profile, dry_run)

    configure_uwsm_env(dry_run, remove=True)

    user_systemd_dir = Path.home() / ".config" / "systemd" / "user"
    pc_service = user_systemd_dir / "profile-cleaner.service"
    pc_timer = user_systemd_dir / "profile-cleaner.timer"
    psd_timer_dropin = user_systemd_dir / "psd-resync.timer.d" / "frequency.conf"

    if dry_run:
        logger.info("[dim][Dry Run] Would stop and disable systemd user services and delete config files.[/]")
    else:
        logger.info("Stopping and disabling systemd user services...")
        subprocess.run(["systemctl", "--user", "disable", "--now", "profile-cleaner.timer"], capture_output=True)
        subprocess.run(["systemctl", "--user", "disable", "--now", "psd.service"], capture_output=True)

        for path in [pc_service, pc_timer, psd_timer_dropin]:
            if path.exists():
                try:
                    path.unlink()
                    logger.info(f"Removed systemd configuration: [yellow]{path}[/]")
                except Exception as e:
                    logger.error(f"Failed to delete {path}: {e}")

        freq_dir = user_systemd_dir / "psd-resync.timer.d"
        if freq_dir.exists() and not any(freq_dir.iterdir()):
            freq_dir.rmdir()

        subprocess.run(["systemctl", "--user", "daemon-reload"])
        logger.info("[bold green]All systemd services cleaned up.[/]")


def print_banner():
    """Prints a beautiful Rich UI banner."""
    banner_text = (
        "[bold cyan]Firefox Performance Optimization Suite[/]\n"
        "[dim]Dynamic Process, Memory, and GPU Configuration[/]"
    )
    console.print(Panel(banner_text, border_style="blue", padding=(1, 2)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Dynamic Firefox Optimization script for Arch Linux + Hyprland + Wayland")
    parser.add_argument("--auto", action="store_true", help="Automatically detect system RAM and enable optimizations if RAM is > 30GB.")
    parser.add_argument("--force", action="store_true", help="Force enable the >30GB RAM performance optimizations on this machine.")
    parser.add_argument("--disable", action="store_true", help="Revert and remove all changes made by this script.")
    parser.add_argument("--cache-mode", choices=["tmpfs", "memory", "default"], default="tmpfs", help="Mechanism to use for caching. tmpfs (default), memory, or default.")
    parser.add_argument("--sudo-pass", type=str, help="Sudo password to install dependencies.")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without writing config files.")
    parser.add_argument("--verbose", action="store_true", help="Print verbose execution details.")
    parser.add_argument("--backup-dir", type=str, help="Custom backup directory path.")
    parser.add_argument("--force-backup-outside-luks", action="store_true", help="Force backing up profiles to a non-encrypted location.")

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    print_banner()

    if args.disable:
        logger.info("Disabling Firefox Optimization Suite...")
        disable_optimizations(args.dry_run)
        logger.info("[bold green]Optimizations successfully reverted.[/]")
        sys.exit(0)

    ram_gb = get_total_ram_gb()
    logger.info(f"Detected physical RAM: [bold cyan]{ram_gb:.2f} GB[/]")

    should_optimize = False
    if args.force:
        logger.info("Optimizations forced by user flag.")
        should_optimize = True
        ram_gb = 64.0
    elif args.auto:
        if ram_gb > 30.0:
            logger.info("Physical RAM > 30GB: Auto-enabling optimizations.")
            should_optimize = True
        else:
            logger.info("Physical RAM <= 30GB: Skipping auto-optimizations. Use --force to override.")
    else:
        if ram_gb > 30.0:
            logger.info("No run-mode argument supplied. Auto-enabled because RAM > 30GB.")
            should_optimize = True
        else:
            logger.info("No run-mode argument supplied. RAM is <= 30GB. No changes applied. Use [cyan]--force[/] to override.")

    if not should_optimize:
        sys.exit(0)

    handle_firefox_open_check(args.dry_run)

    success = install_package_dependencies(args.sudo_pass, args.dry_run)
    if not success:
        logger.error("Dependency installation failed. Optimization process aborted.")
        sys.exit(1)

    profiles = find_firefox_profiles()
    if not profiles:
        logger.info("No Firefox profiles located. Attempting to auto-initialize default profile...")
        try:
            subprocess.run(["firefox", "-headless", "-no-remote", "-CreateProfile", "default-release"], capture_output=True, text=True, check=False, timeout=15)
            profiles = find_firefox_profiles()
        except subprocess.TimeoutExpired:
            logger.warning("Firefox profile initialization timed out.")
        except FileNotFoundError:
            logger.error("Firefox executable not found. Please install Firefox first.")
            sys.exit(1)
        except Exception as e:
            logger.warning(f"Error during profile initialization: {e}")

    if not profiles:
        logger.error("[bold red]No Firefox profiles located. Make sure Firefox has been run at least once.[/]")
        sys.exit(1)
        
    logger.info(f"Located Firefox profiles: [yellow]{[p.name for p in profiles]}[/]")

    backup_dir_override = Path(args.backup_dir) if args.backup_dir else None
    for profile in profiles:
        backup_profile(profile, args.dry_run, backup_dir_override, args.force_backup_outside_luks)

    prefs = get_optimization_prefs(ram_gb, args.cache_mode)
    for profile in profiles:
        update_user_js(profile, prefs, args.dry_run)

    configure_uwsm_env(args.dry_run)

    is_luks = False
    for profile in profiles:
        if is_luks_or_crypt_device(profile):
            is_luks = True
            break

    if is_luks:
        logger.info("[bold yellow]LUKS/dm-crypt device detected for Firefox profiles. Skipping PSD setup to prevent profile corruption when locked.[/]")
        disable_psd_service(args.dry_run)
    else:
        configure_psd_service(args.dry_run)

    configure_profile_cleaner_weekly_timer(args.dry_run)

    console.print(Panel("[bold green]Firefox Optimization Suite applied successfully![/]", expand=False, border_style="green"))

if __name__ == "__main__":
    main()
