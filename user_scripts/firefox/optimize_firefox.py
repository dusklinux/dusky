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

# Set up logging format
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("firefox_optimizer")

# Constant Markers for Safe Modification
USER_JS_BEGIN: Final[str] = "// === BEGIN FIREFOX OPTIMIZATION SUITE ==="
USER_JS_END: Final[str] = "// === END FIREFOX OPTIMIZATION SUITE ==="

UWSM_ENV_BEGIN: Final[str] = "# === BEGIN FIREFOX WAYLAND EXPORTS ==="
UWSM_ENV_END: Final[str] = "# === END FIREFOX WAYLAND EXPORTS ==="

type CacheMode = Literal["tmpfs", "memory", "default"]


def get_clean_device_path(device_source: str) -> str:
    """Strips the subvolume brackets from btrfs mount sources (e.g. /dev/nvme0n1p3[/@home] -> /dev/nvme0n1p3)."""
    if "[" in device_source:
        return device_source.split("[")[0].strip()
    return device_source.strip()


def is_luks_or_crypt_device(path: Path) -> bool:
    """Checks if the path is located on a LUKS or dm-crypt device using findmnt and lsblk."""
    try:
        # Find the source block device for the path (using -T/--target to handle child paths)
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
            # Check type with lsblk
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
    """Creates a tarball backup of the profile directory stored on the same partition (or override) for safety."""
    import tarfile
    from datetime import datetime
    
    if not profile_dir.exists():
        return
        
    # By default, save backup in the mozilla folder to inherit LUKS encryption if mounted
    default_backup_dir = profile_dir.parent.parent
    backup_dir = backup_dir_override if backup_dir_override else default_backup_dir
    
    profile_on_luks = is_luks_or_crypt_device(profile_dir)
    backup_on_luks = is_luks_or_crypt_device(backup_dir)
    
    if profile_on_luks and not backup_on_luks:
        logger.warning(
            f"SECURITY WARNING: You are backing up an encrypted profile to a non-encrypted location: {backup_dir}. "
            "This will expose passwords, cookies, and history when the LUKS drive is locked."
        )
        if not force_backup_outside_luks:
            logger.error("Aborting backup for security reasons. Use --force-backup-outside-luks to override.")
            sys.exit(1)
            
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"firefox-backup-{profile_dir.name}-{timestamp}.tar.gz"
    backup_path = backup_dir / backup_filename
    
    if dry_run:
        logger.info(f"[Dry Run] Would create profile backup at {backup_path}")
        return
        
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Creating profile backup: {backup_path}...")
        with tarfile.open(backup_path, "w:gz") as tar:
            tar.add(profile_dir, arcname=profile_dir.name)
        logger.info("Backup created successfully.")
    except Exception as e:
        logger.error(f"Failed to create profile backup: {e}")
        logger.error("Aborting optimization because backup failed.")
        sys.exit(1)


def disable_psd_service(dry_run: bool) -> None:
    """Stops and disables psd.service cleanly if it is active or enabled."""
    if dry_run:
        logger.info("[Dry Run] Would stop and disable psd.service")
        return

    logger.info("Stopping and disabling profile-sync-daemon service (LUKS device conflict)...")
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
    """Attempts to prompt the user directly via /dev/tty using Rich for beautiful formatting,
    with fallback logic for automated piping and non-interactive scripts."""
    # If stdin is not a TTY, we might be in an automated pipe (e.g. echo "2" | script)
    if not sys.stdin.isatty():
        try:
            val = sys.stdin.readline()
            if not val:
                return "NON_INTERACTIVE"
            return val.strip()
        except Exception:
            return "NON_INTERACTIVE"

    # Otherwise, stdin is a TTY. We can try to use Rich and /dev/tty to prompt beautifully.
    try:
        from rich.console import Console
        from rich.panel import Panel
        
        # Open /dev/tty for direct writing/reading to bypass stdout pipe redirection
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
        
        console_tty.print(Panel(panel_content, title="[bold white]Firefox Open Check[/bold white]", border_style="yellow"))
        tty_out.write("Selection [1/2/3]: ")
        tty_out.flush()
        
        choice = tty_in.readline().strip()
        
        tty_out.close()
        tty_in.close()
        return choice
    except Exception:
        # Fallback to standard input/output if /dev/tty or Rich is not available
        fallback_prompt = (
            "\n[Firefox Open Check] Firefox is currently running!\n"
            "Firefox must be closed to prevent database locks or profile corruption.\n"
            "Please select an option:\n"
            "  1) Close Firefox yourself, then press Enter to re-check\n"
            "  2) Let the script close Firefox for you now\n"
            "  3) Skip Firefox optimization for now (exits cleanly without error)\n"
            "Selection [1/2/3]: "
        )
        print(fallback_prompt, end="", flush=True)
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
            logger.error("Too many attempts. Skipping Firefox optimization to prevent corruption.")
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
                logger.info("[Dry Run] Would send SIGTERM to firefox processes.")
                return
            # Try terminating cleanly first (SIGTERM)
            subprocess.run(["pkill", "-x", "firefox"])
            # Wait up to 5 seconds for it to close
            for _ in range(10):
                time.sleep(0.5)
                if not is_firefox_running():
                    logger.info("Firefox closed successfully.")
                    return
            # Force kill if still running (SIGKILL)
            logger.warning("Firefox did not close cleanly. Sending SIGKILL...")
            subprocess.run(["pkill", "-9", "-x", "firefox"])
            time.sleep(1)
            if not is_firefox_running():
                logger.info("Firefox force-closed successfully.")
                return
            else:
                logger.error("Failed to close Firefox. Please close it manually.")
        elif choice == "3":
            logger.info("Skipping Firefox optimization as requested. Exiting cleanly.")
            sys.exit(0)
        else:
            logger.warning("Invalid selection. Please choose 1, 2, or 3.")



def get_sudo_password(cmd_pass: str | None) -> str | None:
    """Prompts for sudo password securely to run privileged pacman installations."""
    if cmd_pass:
        return cmd_pass

    # Prompt if interactive, otherwise return None
    if sys.stdout.isatty():
        try:
            return getpass.getpass("Enter sudo password to install package dependencies: ")
        except Exception:
            return None
    return None


def run_sudo_cmd(cmd: list[str], password: str | None) -> subprocess.CompletedProcess[str]:
    """Runs a command as sudo, passing the password via stdin if required."""
    if not password:
        # Try running normally, might be passwordless
        return subprocess.run(
            ["sudo"] + cmd,
            capture_output=True,
            text=True,
        )
    return subprocess.run(
        ["sudo", "-S"] + cmd,
        input=f"{password}\n",
        capture_output=True,
        text=True,
    )


def install_package_dependencies(sudo_pass: str | None, dry_run: bool) -> bool:
    """Checks and installs profile-sync-daemon and profile-cleaner packages."""
    dependencies = ["profile-sync-daemon", "profile-cleaner"]
    missing = []

    for dep in dependencies:
        res = subprocess.run(["pacman", "-Qi", dep], capture_output=True)
        if res.returncode != 0:
            missing.append(dep)

    if not missing:
        logger.info("All package dependencies are already installed.")
        return True

    logger.info(f"Missing dependencies to install: {missing}")
    if dry_run:
        logger.info(f"[Dry Run] Would install: {missing}")
        return True

    verified_pass = get_sudo_password(sudo_pass)
    # Check if we can run sudo non-interactively (e.g. from orchestra which cached sudo credentials)
    can_sudo_noninteractive = subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0

    if not verified_pass and not can_sudo_noninteractive:
        logger.error("Sudo password is required to install system packages. Aborting installation.")
        return False

    logger.info("Installing packages (pacman -S)...")
    res = run_sudo_cmd(["pacman", "-S", "--noconfirm", "--needed"] + missing, verified_pass)
    if res.returncode == 0:
        logger.info("Successfully installed packages.")
        return True
    else:
        logger.error(f"Failed to install packages: {res.stderr}")
        logger.error("Note: A 404 error means your local pacman database is outdated.")
        logger.error("Please run 'sudo pacman -Syu' manually to sync and update your system, then rerun this script.")
        return False


def find_firefox_profiles() -> list[Path]:
    """Finds all Firefox profile directories under common paths or via profiles.ini."""
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

        # Fallback: scan folders directly in case profiles.ini is corrupt/missing
        for sub in base_dir.iterdir():
            if sub.is_dir() and (sub.suffix == ".default" or "default-release" in sub.name):
                # Ignore profile-sync-daemon (PSD) backup and overlayfs mount folders
                if any(x in sub.name for x in ["-backup", "-back-ovfs"]):
                    continue
                if sub not in profiles:
                    profiles.append(sub)

    # De-duplicate while preserving order
    unique_profiles = []
    for p in profiles:
        resolved = p.resolve()
        if resolved not in unique_profiles:
            unique_profiles.append(resolved)

    return unique_profiles


def get_optimization_prefs(ram_gb: float, cache_mode: CacheMode) -> dict[str, str | int | bool]:
    """Computes the optimized firefox preferences map dynamically based on RAM size."""
    uid = os.getuid()

    # Base profile values
    capacity_kb = 1048576  # 1 GB default
    shared_ipc = 8
    isolated_ipc = 32
    # Keep ext_ipc as 1 at all times. Setting this preference to > 1 is unsupported
    # and breaks WebExtensions (causing them to fail loading / run in about:addons).
    ext_ipc = 1
    unload_low_mem = "true"

    if ram_gb > 30.0:
        logger.info(f"System has {ram_gb:.1f} GB RAM (> 30 GB). Enabling Ultra high-performance profile.")
        capacity_kb = 4194304  # 4 GB
        shared_ipc = 32
        isolated_ipc = 99
        unload_low_mem = "false"
    elif ram_gb >= 16.0:
        logger.info(f"System has {ram_gb:.1f} GB RAM. Enabling High-performance profile.")
        capacity_kb = 2097152  # 2 GB
        shared_ipc = 16
        isolated_ipc = 64
        unload_low_mem = "false"
    else:
        logger.info(f"System has {ram_gb:.1f} GB RAM (< 16 GB). Scaling to Moderate-performance profile.")

    prefs: dict[str, str | int | bool] = {
        # 1. Native Memory Cache Control
        "browser.cache.memory.enable": True,
        "browser.cache.memory.capacity": capacity_kb,
        "browser.cache.disk.smart_size.enabled": False,
        "browser.cache.disk_cache_ssl": False,
        "browser.cache.offline.enable": False,
        # 2. Site Isolation / Process Count settings (Fission)
        "dom.ipc.processCount": shared_ipc,
        "dom.ipc.processCount.webIsolated": isolated_ipc,
        "dom.ipc.processCount.extension": ext_ipc,
        "fission.autostart": True,
        # 3. Tab Memory Management
        "browser.tabs.unloadOnLowMemory": unload_low_mem,
        # 4. Rendering and GPU Hardware Acceleration
        "gfx.webrender.all": True,
        "layers.acceleration.force-enabled": True,
        "media.ffmpeg.vaapi.enabled": True,
        "media.hardware-video-decoding.force-enabled": True,
        "widget.wayland-dmabuf-vaapi.enabled": True,
        "widget.wayland.opaque-region.enabled": False,
        "apz.gtk.kinetic_scroll.enabled": True,
        # 5. Telemetry Purging
        "toolkit.telemetry.enabled": False,
        "datareporting.healthreport.uploadEnabled": False,
        "app.normandy.enabled": False,
        # 6. Network Connection Optimizations
        "network.http.max-connections": 1800,
        "network.http.max-persistent-connections-per-server": 10,
        "network.trr.mode": 2,
        "network.trr.uri": "https://mozilla.cloudflare-dns.com/dns-query",
    }

    # Apply caching target depending on cache_mode
    match cache_mode:
        case "tmpfs":
            prefs["browser.cache.disk.enable"] = True
            prefs["browser.cache.disk.parent_directory"] = f"/run/user/{uid}/firefox"
        case "memory":
            prefs["browser.cache.disk.enable"] = False
        case "default":
            # Leave browser disk cache preferences untouched
            pass

    return prefs


def format_pref_line(name: str, value: str | int | bool) -> str:
    """Formats Python preference values to javascript user.js style."""
    if isinstance(value, bool):
        val_str = "true" if value else "false"
    elif isinstance(value, int):
        val_str = str(value)
    else:
        val_str = f'"{value}"'
    return f'user_pref("{name}", {val_str});'


def update_user_js(profile_dir: Path, prefs: dict[str, str | int | bool], dry_run: bool) -> None:
    """Injects or updates the optimization preferences in the profile's user.js file."""
    user_js_path = profile_dir / "user.js"
    logger.info(f"Processing profile at {profile_dir.name} ({user_js_path.name})")

    # Generate javascript block
    lines = [USER_JS_BEGIN, f"// Auto-generated by Firefox System Optimizer"]
    for k, v in prefs.items():
        lines.append(format_pref_line(k, v))
    lines.append(USER_JS_END)
    block_content = "\n".join(lines) + "\n"

    # Read existing content if file exists
    content = ""
    if user_js_path.exists():
        try:
            content = user_js_path.read_text()
        except Exception as e:
            logger.error(f"Failed to read {user_js_path}: {e}")
            return

    # Self-healing: Strip existing complete blocks
    pattern = re.compile(
        re.escape(USER_JS_BEGIN) + ".*?" + re.escape(USER_JS_END),
        re.DOTALL,
    )
    content = pattern.sub("", content)

    # Self-healing / Recovery: Strip orphaned markers or duplicate keys from previous corrupt/manual runs
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
        logger.info(f"[Dry Run] Would write to {user_js_path}:\n{block_content}")
    else:
        try:
            user_js_path.write_text(new_content)
            logger.info(f"Successfully updated optimization settings in {user_js_path}")
        except Exception as e:
            logger.error(f"Failed to write to {user_js_path}: {e}")


def remove_user_js_optimizations(profile_dir: Path, dry_run: bool) -> None:
    """Removes the optimization preferences block from user.js and scrubs baked-in settings from prefs.js."""
    managed_keys = [
        "browser.cache.memory.enable",
        "browser.cache.memory.capacity",
        "browser.cache.disk.smart_size.enabled",
        "browser.cache.disk_cache_ssl",
        "browser.cache.offline.enable",
        "dom.ipc.processCount",
        "dom.ipc.processCount.webIsolated",
        "dom.ipc.processCount.extension",
        "fission.autostart",
        "browser.tabs.unloadOnLowMemory",
        "gfx.webrender.all",
        "layers.acceleration.force-enabled",
        "media.ffmpeg.vaapi.enabled",
        "media.hardware-video-decoding.force-enabled",
        "widget.wayland-dmabuf-vaapi.enabled",
        "widget.wayland.opaque-region.enabled",
        "apz.gtk.kinetic_scroll.enabled",
        "toolkit.telemetry.enabled",
        "datareporting.healthreport.uploadEnabled",
        "app.normandy.enabled",
        "network.http.max-connections",
        "network.http.max-persistent-connections-per-server",
        "network.trr.mode",
        "network.trr.uri",
        "browser.cache.disk.enable",
        "browser.cache.disk.parent_directory",
    ]

    # 1. Clean user.js
    user_js_path = profile_dir / "user.js"
    if user_js_path.exists():
        logger.info(f"Removing optimization configurations from {user_js_path}")
        try:
            content = user_js_path.read_text()
            pattern = re.compile(
                r"\n?" + re.escape(USER_JS_BEGIN) + ".*?" + re.escape(USER_JS_END) + r"\n?",
                re.DOTALL,
            )
            content = pattern.sub("\n", content)

            lines_to_keep = []
            for line in content.splitlines():
                if USER_JS_BEGIN in line or USER_JS_END in line:
                    continue
                if any(f'"{k}"' in line or f"'{k}'" in line for k in managed_keys):
                    continue
                lines_to_keep.append(line)

            new_content = "\n".join(lines_to_keep).strip() + "\n"

            if dry_run:
                logger.info(f"[Dry Run] Would remove optimization block from {user_js_path}")
            else:
                if not new_content.strip():
                    user_js_path.unlink()
                    logger.info(f"Deleted empty {user_js_path}")
                else:
                    user_js_path.write_text(new_content)
                    logger.info(f"Cleaned optimization block from {user_js_path}")
        except Exception as e:
            logger.error(f"Failed to update/delete {user_js_path}: {e}")

    # 2. Clean prefs.js natively to revert baked-in states
    prefs_js_path = profile_dir / "prefs.js"
    if prefs_js_path.exists():
        try:
            prefs_content = prefs_js_path.read_text()
            lines_to_keep = []
            for line in prefs_content.splitlines():
                if any(f'"{k}"' in line for k in managed_keys):
                    continue
                lines_to_keep.append(line)

            if dry_run:
                logger.info(f"[Dry Run] Would scrub baked-in managed keys from {prefs_js_path}")
            else:
                prefs_js_path.write_text("\n".join(lines_to_keep) + "\n")
                logger.info(f"Cleaned baked-in optimization preferences from {prefs_js_path}")
        except Exception as e:
            logger.error(f"Failed to clean {prefs_js_path}: {e}")


def configure_uwsm_env(dry_run: bool, remove: bool = False) -> None:
    """Updates environment variables by writing to ~/.config/uwsm/env.d/firefox and cleaning legacy config."""
    # 1. Self-healing: Clean legacy main env file if present
    main_env_file = Path.home() / ".config" / "uwsm" / "env"
    if main_env_file.exists():
        try:
            content = main_env_file.read_text()
            pattern = re.compile(
                r"\n?" + re.escape(UWSM_ENV_BEGIN) + ".*?" + re.escape(UWSM_ENV_END) + r"\n?",
                re.DOTALL,
            )
            if pattern.search(content):
                new_content = pattern.sub("\n", content).strip() + "\n"
                if not dry_run:
                    main_env_file.write_text(new_content)
                    logger.info(f"Cleaned legacy environment configuration from main file: {main_env_file}")
        except Exception as e:
            logger.warning(f"Could not clean legacy main environment file: {e}")

    # 2. Manage drop-in env.d config
    dropin_dir = Path.home() / ".config" / "uwsm" / "env.d"
    dropin_file = dropin_dir / "firefox"

    if remove:
        if dropin_file.exists():
            if dry_run:
                logger.info(f"[Dry Run] Would delete drop-in environment file: {dropin_file}")
            else:
                try:
                    dropin_file.unlink()
                    logger.info(f"Successfully removed drop-in environment file: {dropin_file}")
                except Exception as e:
                    logger.error(f"Failed to delete {dropin_file}: {e}")
        return

    # Add environment block
    env_content = """# Enable native Wayland in Firefox
export MOZ_ENABLE_WAYLAND=1
# Enable GTK Kinetic scrolling support
export MOZ_USE_XINPUT2=1
"""

    if dry_run:
        logger.info(f"[Dry Run] Would create/overwrite drop-in environment file at {dropin_file} with:\n{env_content}")
    else:
        try:
            dropin_dir.mkdir(parents=True, exist_ok=True)
            dropin_file.write_text(env_content)
            logger.info(f"Successfully wrote drop-in environment variables to {dropin_file}")
        except Exception as e:
            logger.error(f"Failed to write drop-in environment variables: {e}")


def configure_psd_service(dry_run: bool) -> None:
    """Configures profile-sync-daemon config file and systemd service overlay."""
    psd_conf_dir = Path.home() / ".config" / "psd"
    psd_conf_path = psd_conf_dir / "psd.conf"

    if not psd_conf_path.exists():
        logger.info("Initializing psd configuration...")
        if not dry_run:
            psd_conf_dir.mkdir(parents=True, exist_ok=True)
            # Run psd parse to bootstrap default configuration file
            subprocess.run(["psd", "p"], capture_output=True)

    if psd_conf_path.exists():
        logger.info(f"Modifying profile-sync-daemon config: {psd_conf_path}")
        try:
            content = psd_conf_path.read_text()
        except Exception as e:
            logger.error(f"Failed to read {psd_conf_path}: {e}")
            return

        # Check overlayfs support dynamically
        use_overlayfs = "yes"
        if not has_overlayfs_support():
            logger.warning("overlayfs kernel support is not available. Configuring PSD with USE_OVERLAYFS='no'.")
            use_overlayfs = "no"

        # Directives to ensure are correct
        directives = {
            "USE_OVERLAYFS": f'"{use_overlayfs}"',
            "BROWSERS": '"firefox"',
            "USE_BACKUPS": '"yes"',
            "BACKUP_LIMIT": '"5"',
        }

        # Self-healing: scrub existing directives first to avoid duplicate configurations
        lines = []
        for line in content.splitlines():
            if any(line.strip().startswith(f"{key}=") or line.strip().startswith(f"#{key}=") for key in directives):
                continue
            lines.append(line)

        # Append clean directives at the end
        for key, value in directives.items():
            lines.append(f"{key}={value}")

        content = "\n".join(lines) + "\n"

        if dry_run:
            logger.info(f"[Dry Run] Would write modifications to {psd_conf_path}")
        else:
            try:
                psd_conf_path.write_text(content)
                logger.info(f"Successfully updated {psd_conf_path}")
            except Exception as e:
                logger.error(f"Failed to write psd config: {e}")

    # Set up resync timer granularity (10 minutes)
    timer_dropin_dir = Path.home() / ".config" / "systemd" / "user" / "psd-resync.timer.d"
    timer_dropin_file = timer_dropin_dir / "frequency.conf"

    timer_content = """[Unit]
Description=Timer for Profile-sync-daemon - 10min

[Timer]
OnUnitActiveSec=
OnUnitActiveSec=10min
"""

    if dry_run:
        logger.info(f"[Dry Run] Would create timer override at {timer_dropin_file}")
    else:
        try:
            timer_dropin_dir.mkdir(parents=True, exist_ok=True)
            timer_dropin_file.write_text(timer_content)
            logger.info(f"Created systemd timer drop-in at {timer_dropin_file}")
        except Exception as e:
            logger.error(f"Failed to create timer override: {e}")

    # Start and enable systemd user services
    if dry_run:
        logger.info("[Dry Run] Would reload user systemd daemon and enable/start psd.service")
    else:
        logger.info("Enabling and starting profile-sync-daemon services...")
        subprocess.run(["systemctl", "--user", "daemon-reload"])
        subprocess.run(["systemctl", "--user", "enable", "--now", "psd.service"])
        logger.info("PSD services enabled.")


def configure_profile_cleaner_weekly_timer(dry_run: bool) -> None:
    """Creates a systemd user service and timer to automate weekly SQLite vacuuming."""
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
        logger.info(f"[Dry Run] Would write systemd user service to {service_path}")
        logger.info(f"[Dry Run] Would write systemd user timer to {timer_path}")
        logger.info("[Dry Run] Would enable and start profile-cleaner.timer")
    else:
        try:
            user_systemd_dir.mkdir(parents=True, exist_ok=True)
            service_path.write_text(service_content)
            timer_path.write_text(timer_content)
            logger.info("Created weekly profile-cleaner service and timer files.")

            subprocess.run(["systemctl", "--user", "daemon-reload"])
            subprocess.run(["systemctl", "--user", "enable", "--now", "profile-cleaner.timer"])
            logger.info("profile-cleaner timer started and enabled.")
        except Exception as e:
            logger.error(f"Failed to set up weekly database cleaning: {e}")


def disable_optimizations(dry_run: bool) -> None:
    """Disables all optimizations, deleting generated files and cleaning user configs."""
    logger.warning("Please ensure Firefox is completely closed before proceeding, or reversion changes may be overwritten.")
    
    # 1. Clean user.js and prefs.js configs
    profiles = find_firefox_profiles()
    if not profiles:
        logger.warning("No Firefox profiles located during disable phase.")
    for profile in profiles:
        remove_user_js_optimizations(profile, dry_run)

    # 2. Clean environment variables (both drop-in and legacy main)
    configure_uwsm_env(dry_run, remove=True)

    # 3. Disable and stop systemd user units
    user_systemd_dir = Path.home() / ".config" / "systemd" / "user"
    pc_service = user_systemd_dir / "profile-cleaner.service"
    pc_timer = user_systemd_dir / "profile-cleaner.timer"
    psd_timer_dropin = user_systemd_dir / "psd-resync.timer.d" / "frequency.conf"

    if dry_run:
        logger.info("[Dry Run] Would stop and disable psd.service and profile-cleaner.timer")
        logger.info(f"[Dry Run] Would delete {pc_service}, {pc_timer}, and {psd_timer_dropin}")
    else:
        logger.info("Stopping and disabling systemd user services...")
        subprocess.run(["systemctl", "--user", "disable", "--now", "profile-cleaner.timer"], capture_output=True)
        subprocess.run(["systemctl", "--user", "disable", "--now", "psd.service"], capture_output=True)

        # Remove files
        for path in [pc_service, pc_timer, psd_timer_dropin]:
            if path.exists():
                try:
                    path.unlink()
                    logger.info(f"Removed systemd configuration: {path}")
                except Exception as e:
                    logger.error(f"Failed to delete {path}: {e}")

        # Remove frequency.conf parent directory if empty
        freq_dir = user_systemd_dir / "psd-resync.timer.d"
        if freq_dir.exists() and not any(freq_dir.iterdir()):
            freq_dir.rmdir()

        subprocess.run(["systemctl", "--user", "daemon-reload"])
        logger.info("All systemd services cleaned up.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dynamic Firefox Optimization script for Arch Linux + Hyprland + Wayland"
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Automatically detect system RAM and enable optimizations if RAM is > 30GB.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force enable the >30GB RAM performance optimizations on this machine.",
    )
    parser.add_argument(
        "--disable",
        action="store_true",
        help="Revert and remove all changes made by this script.",
    )
    parser.add_argument(
        "--cache-mode",
        choices=["tmpfs", "memory", "default"],
        default="tmpfs",
        help="Mechanism to use for caching. tmpfs (default), memory (native override), or default.",
    )
    parser.add_argument(
        "--sudo-pass",
        type=str,
        help="Sudo password to install dependencies (if required). Fallbacks to defaults or interactive prompt.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions to be performed without writing config files or modifying system.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print verbose execution details.",
    )
    parser.add_argument(
        "--backup-dir",
        type=str,
        help="Custom backup directory path. Default is the mozilla base folder (e.g. ~/.config/mozilla).",
    )
    parser.add_argument(
        "--force-backup-outside-luks",
        action="store_true",
        help="Force backing up profiles to a non-encrypted location even if profiles are on LUKS.",
    )

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # 1. Action: Revert/Disable
    if args.disable:
        logger.info("Disabling Firefox Optimization Suite...")
        disable_optimizations(args.dry_run)
        logger.info("Optimizations successfully reverted.")
        sys.exit(0)

    # 2. Detect RAM
    ram_gb = get_total_ram_gb()
    logger.info(f"Detected physical RAM: {ram_gb:.2f} GB")

    should_optimize = False
    if args.force:
        logger.info("Optimizations forced by user flag.")
        should_optimize = True
        # Force high memory settings by passing fake large RAM
        ram_gb = 64.0
    elif args.auto:
        if ram_gb > 30.0:
            logger.info("Physical RAM > 30GB: Auto-enabling optimizations.")
            should_optimize = True
        else:
            logger.info("Physical RAM <= 30GB: Skipping auto-optimizations. Use --force to override.")
    else:
        # Default behavior: run auto-detection check
        if ram_gb > 30.0:
            logger.info("No run-mode argument supplied. Auto-enabled because RAM > 30GB.")
            should_optimize = True
        else:
            logger.info("No run-mode argument supplied. Checking system specs: RAM is <= 30GB. No changes applied. Use --force to override.")

    if not should_optimize:
        sys.exit(0)

    # Check if Firefox is running and handle it
    handle_firefox_open_check(args.dry_run)

    # 3. Install packages (psd and profile-cleaner)
    success = install_package_dependencies(args.sudo_pass, args.dry_run)
    if not success:
        logger.error("Dependency installation failed. Optimization process aborted.")
        sys.exit(1)

    # 4. Find Firefox Profiles
    profiles = find_firefox_profiles()
    if not profiles:
        logger.info("No Firefox profiles located. Attempting to auto-initialize default profile...")
        try:
            # Run Firefox in headless mode with -no-remote to safely create the default profile
            subprocess.run(
                ["firefox", "-headless", "-no-remote", "-CreateProfile", "default-release"],
                capture_output=True,
                text=True,
                check=False,
                timeout=15
            )
            # Re-try finding profiles
            profiles = find_firefox_profiles()
        except subprocess.TimeoutExpired:
            logger.warning("Firefox profile initialization timed out.")
        except FileNotFoundError:
            logger.error("Firefox executable not found. Please install Firefox first.")
            sys.exit(1)
        except Exception as e:
            logger.warning(f"Error during profile initialization: {e}")

    if not profiles:
        logger.error("No Firefox profiles located. Make sure Firefox has been run at least once.")
        sys.exit(1)
    logger.info(f"Located Firefox profiles: {[p.name for p in profiles]}")

    # 5. Backup Profiles before modifying them
    backup_dir_override = Path(args.backup_dir) if args.backup_dir else None
    for profile in profiles:
        backup_profile(
            profile,
            args.dry_run,
            backup_dir_override,
            args.force_backup_outside_luks
        )

    # 6. Build and inject preferences map
    prefs = get_optimization_prefs(ram_gb, args.cache_mode)
    for profile in profiles:
        update_user_js(profile, prefs, args.dry_run)

    # 7. Setup Wayland Environment Variables in UWSM env.d drop-in
    configure_uwsm_env(args.dry_run)

    # 8. Configure Profile Sync Daemon (PSD) (skip/disable if LUKS/dm-crypt detected)
    is_luks = False
    for profile in profiles:
        if is_luks_or_crypt_device(profile):
            is_luks = True
            break

    if is_luks:
        logger.info("LUKS/dm-crypt device detected for Firefox profiles. Skipping Profile Sync Daemon (PSD) setup to prevent profile corruption when locked.")
        disable_psd_service(args.dry_run)
    else:
        configure_psd_service(args.dry_run)

    # 9. Configure weekly profile cleaner database optimizer
    configure_profile_cleaner_weekly_timer(args.dry_run)

    logger.info("Firefox Optimization Suite applied successfully!")


if __name__ == "__main__":
    main()
