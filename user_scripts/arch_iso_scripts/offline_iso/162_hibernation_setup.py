#!/usr/bin/env python3
"""Dusky Universal Hibernation Setup & Validator (Btrfs-Dedicated).

Strictly targets Btrfs topologies as engineered by Dusky and Omarchy.
Supports:
  1. Live ISO installer environment (targeting /mnt outside chroot)
  2. Installation chroot environment (Phase 2)
  3. Live user space environment (running installed system)
"""

from __future__ import annotations
lazy import datetime
lazy import json
lazy import os
lazy import re
lazy import shutil
lazy import subprocess
lazy import sys
lazy import tempfile
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console(highlight=False)

# Standard Btrfs Paths (relative to target root)
SWAP_SUBVOLUME_REL = "swap"
SWAP_FILE_REL = "swap/swapfile"
MKINITCPIO_DROPIN_REL = "etc/mkinitcpio.conf.d/15-hibernation.conf"
REBUILD_MARKER_REL = "var/lib/dusky/hibernation-resume-rebuild"
KBD_BACKLIGHT_HOOK_REL = "usr/lib/systemd/system-sleep/keyboard-backlight"
FPRINTD_RESUME_HOOK_REL = "usr/lib/systemd/system-sleep/fprintd-resume"


def run_cmd(
    *cmd: str | Path,
    check: bool = True,
    capture: bool = True,
    timeout: float = 300,
    text: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Execute command safely with LC_ALL=C."""
    argv = [os.fspath(c) for c in cmd]
    try:
        return subprocess.run(
            argv,
            check=check,
            capture_output=capture,
            timeout=timeout,
            text=text,
            env=os.environ | {"LC_ALL": "C"},
        )
    except subprocess.CalledProcessError as e:
        if check:
            console.print(f"[bold red]Command failed:[/bold red] {' '.join(argv)}")
            if e.stderr:
                console.print(f"[dim red]{e.stderr.strip()}[/dim red]")
        raise


def get_mem_total_kb() -> int:
    """Return total system memory in KiB from /proc/meminfo."""
    try:
        meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
        for line in meminfo.splitlines():
            if line.startswith("MemTotal:"):
                parts = line.split()
                return int(parts[1])
    except Exception as e:
        console.print(f"[yellow]Warning: Could not read /proc/meminfo: {e}[/yellow]")
    return 16 * 1024 * 1024  # 16 GiB fallback in KiB


def format_bytes_human(num_bytes: int) -> str:
    """Format bytes into human-readable string (GiB/MiB)."""
    gib = num_bytes / (1024**3)
    if gib >= 1.0:
        return f"{gib:.1f} GiB"
    mib = num_bytes / (1024**2)
    return f"{mib:.1f} MiB"


def parse_size_to_kb(size_str: str) -> int:
    """Parse size like '64G', '16384M', '4096' into integer KiB."""
    size_str = size_str.strip().upper()
    m = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([KMGTPE]?I?B?)$", size_str)
    if not m:
        raise ValueError(f"Invalid size string: '{size_str}'")
    val = float(m.group(1))
    unit = m.group(2)
    multipliers_to_kb = {
        "": 1 / 1024,
        "B": 1 / 1024,
        "K": 1,
        "KB": 1,
        "KIB": 1,
        "M": 1024,
        "MB": 1024,
        "MIB": 1024,
        "G": 1024**2,
        "GB": 1024**2,
        "GIB": 1024**2,
        "T": 1024**3,
        "TB": 1024**3,
        "TIB": 1024**3,
    }
    return int(val * multipliers_to_kb.get(unit, 1))


def detect_environment(explicit_target: str | None = None) -> tuple[Path, str]:
    """Detect whether running in ISO outside chroot, chroot, or user space."""
    if explicit_target:
        target = Path(explicit_target).resolve()
        if not target.is_dir():
            raise FileNotFoundError(f"Specified target '{explicit_target}' does not exist.")
        return target, "explicit"

    # Detect ISO environment targeting /mnt
    mnt = Path("/mnt")
    is_live_iso = False
    try:
        root_fstype = run_cmd("findmnt", "-no", "FSTYPE", "/", check=False).stdout.strip()
        if root_fstype in ("overlay", "overlayfs", "squashfs", "iso9660") or Path("/run/archiso").exists():
            is_live_iso = True
    except Exception:
        pass

    if is_live_iso and mnt.is_mount():
        if (mnt / "etc").is_dir() or (mnt / "boot").is_dir():
            return mnt, "iso_target"

    # Detect chroot
    try:
        r = run_cmd("systemd-detect-virt", "--chroot", check=False)
        if r.returncode == 0:
            return Path("/"), "chroot"
    except Exception:
        pass

    try:
        root_stat = os.stat("/")
        if root_stat.st_ino != 2:
            return Path("/"), "chroot"
    except Exception:
        pass

    return Path("/"), "userspace"


def check_kernel_hibernation_support() -> tuple[bool, str]:
    """Inspect sysfs and hardware platform to check if hibernation is supported."""
    # 1. Apple Silicon check (borrowed from Omarchy: Asahi hibernation is unsupported)
    try:
        arch = run_cmd("uname", "-m", check=False).stdout.strip()
        if arch == "aarch64":
            for dt_path in (Path("/proc/device-tree/compatible"), Path("/sys/firmware/devicetree/base/compatible")):
                if dt_path.is_file():
                    content = dt_path.read_text(encoding="utf-8", errors="ignore").lower()
                    if "apple" in content:
                        return False, "Hibernation is not supported on Apple Silicon (Asahi Linux)"
    except Exception:
        pass

    # 2. Check if sleep is disabled via systemd sleep.conf.d
    sleep_conf_dirs = [Path("/etc/systemd/sleep.conf.d"), Path("/run/systemd/sleep.conf.d")]
    for scd in sleep_conf_dirs:
        if scd.is_dir():
            for scf in scd.glob("*.conf"):
                try:
                    c = scf.read_text(encoding="utf-8", errors="ignore")
                    if re.search(r"^\s*AllowHibernation\s*=\s*(no|false|0)\b", c, re.MULTILINE):
                        return False, f"Hibernation is disabled in {scf}"
                    if "omarchy-sleep-disabled" in scf.name or "sleep-disabled" in scf.name:
                        return False, f"Sleep is turned off on this machine ({scf.name})"
                except Exception:
                    pass

    # 3. Check /sys/power/state
    sys_power = Path("/sys/power")
    if not sys_power.is_dir():
        return False, "/sys/power sysfs directory does not exist"

    state_file = sys_power / "state"
    if not state_file.is_file():
        return False, "/sys/power/state not readable"

    try:
        # Match exact word "disk" (borrowed from Omarchy's test: SLEEP_STATES=" $(<state>) ", *" disk "*)
        states_raw = state_file.read_text(encoding="utf-8").strip()
        states = f" {states_raw} "
        if " disk " not in states:
            lockdown_file = Path("/sys/kernel/security/lockdown")
            if lockdown_file.is_file():
                lockdown = lockdown_file.read_text(encoding="utf-8").strip()
                if "confidentiality" in lockdown or "integrity" in lockdown:
                    return False, f"Kernel Lockdown is active ({lockdown}), which disallows hibernation"
            return False, f"Kernel does not offer 'disk' sleep state (available: {states_raw})"
    except Exception as e:
        return False, f"Failed to read /sys/power/state: {e}"

    # 4. Check /sys/power/image_size
    img_size_file = sys_power / "image_size"
    if not img_size_file.is_file():
        return False, "/sys/power/image_size does not exist"

    return True, "Kernel hibernation supported"


def is_s2idle_system() -> bool:
    """Return True if s2idle (modern standby) is active in /sys/power/mem_sleep."""
    mem_sleep = Path("/sys/power/mem_sleep")
    if mem_sleep.is_file():
        try:
            content = mem_sleep.read_text(encoding="utf-8")
            return "[s2idle]" in content
        except Exception:
            pass
    return False


def verify_btrfs_filesystem(path: Path) -> None:
    """Enforce that target filesystem is Btrfs. Fails fast otherwise."""
    r = run_cmd("findmnt", "-no", "FSTYPE", "-T", str(path), check=False)
    fstype = r.stdout.strip().lower()
    if fstype != "btrfs":
        raise RuntimeError(f"Hibernation swap must reside on a Btrfs filesystem, but {path} is '{fstype}'.")


def atomic_write(path: Path, content: str, mode: int = 0o644) -> None:
    """Write file atomically using a temporary file in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        tmp_path.chmod(mode)
        tmp_path.replace(path)
    finally:
        tmp_path.unlink(missing_ok=True)


def calculate_resume_device(swapfile_path: Path) -> str:
    """Calculate the resume device name (borrowed from Omarchy's resume_device).

    A filesystem UUID names the same disk on every boot (avoiding NVMe probe reordering).
    A device-mapper name (/dev/mapper/*) is set by dm-crypt on an encrypted install,
    so it stays.
    """
    # findmnt -no SOURCE -T "$SWAP_FILE" | sed 's/\[.*\]//'
    r_src = run_cmd("findmnt", "-no", "SOURCE", "-T", str(swapfile_path), check=True)
    source = r_src.stdout.strip().split("[", 1)[0].strip()

    if not source.startswith("/dev/mapper/"):
        r_uuid = run_cmd("findmnt", "-no", "UUID", "-T", str(swapfile_path), check=False)
        uuid = r_uuid.stdout.strip()
        if uuid:
            return f"UUID={uuid}"
    return source


def calculate_btrfs_resume_offset(swapfile_path: Path) -> str:
    """Calculate btrfs physical page resume offset via btrfs inspect-internal map-swapfile -r."""
    r = run_cmd("btrfs", "inspect-internal", "map-swapfile", "-r", str(swapfile_path), check=True)
    offset = r.stdout.strip()
    if not offset.isdigit():
        raise RuntimeError(f"Invalid resume offset returned by btrfs map-swapfile: '{offset}'")
    return offset


def install_system_sleep_hooks(target_root: Path, dry_run: bool = False) -> None:
    """Install Omarchy-engineered system-sleep hooks for hardware reliability."""
    sleep_dir = target_root / "usr" / "lib" / "systemd" / "system-sleep"

    # 1. Keyboard Backlight Hook
    # Prevents ASUS/laptop keyboard controllers from blocking S4 shutdown if LEDs are active.
    kbd_hook = sleep_dir / "keyboard-backlight"
    kbd_content = """#!/bin/bash
# Turn off keyboard backlight before hibernate to prevent hang on power-off.
# The ASUS keyboard controller can block S4 shutdown if LEDs are active.

sleep_action=${SYSTEMD_SLEEP_ACTION:-$2}

if [[ $1 == "pre" && $sleep_action == "hibernate" ]]; then
  device=""
  for candidate in /sys/class/leds/*kbd_backlight*; do
    if [[ -e "$candidate" ]]; then
      device="$(basename "$candidate")"
      break
    fi
  done

  if [[ -n "$device" ]] && command -v brightnessctl >/dev/null 2>&1; then
    brightnessctl -d "$device" set 0 >/dev/null 2>&1
  fi
fi
"""

    # 2. Fingerprint Resume Hook
    # Prevents fprintd sensor claim from wedging across suspend/hibernate resume.
    fprintd_hook = sleep_dir / "fprintd-resume"
    fprintd_content = """#!/bin/bash
# Suspend/hibernate can wedge fprintd's claim; restart it after resume to clear it.
# Upstream: https://gitlab.freedesktop.org/libfprint/fprintd/-/issues/173

if [[ $1 == "post" ]]; then
  # user.slice stays frozen until this hook returns; enqueue rather than wait.
  systemctl --no-block try-restart fprintd.service 2>/dev/null || true
fi
"""

    if not dry_run:
        sleep_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(kbd_hook, kbd_content, mode=0o755)
        console.print(f"[green]✔ Installed keyboard-backlight sleep hook:[/green] {kbd_hook}")

        # Install fprintd hook if fprintd is present or in ISO build
        if shutil.which("fprintd-verify") or (target_root / "usr" / "bin" / "fprintd-verify").exists():
            atomic_write(fprintd_hook, fprintd_content, mode=0o755)
            console.print(f"[green]✔ Installed fprintd-resume sleep hook:[/green] {fprintd_hook}")
    else:
        console.print(f"[dim][Dry-run] Would install sleep hooks in {sleep_dir}[/dim]")


def update_mkinitcpio_config(target_root: Path, dry_run: bool = False) -> None:
    """Ensure mkinitcpio has the resume hook configured."""
    dropin_file = target_root / MKINITCPIO_DROPIN_REL
    content = (
        "# Btrfs Hibernation Configuration Drop-in\n"
        "# Generated by Dusky 162_hibernation_setup.py\n"
        "HOOKS+=(resume)\n"
    )
    if not dry_run:
        dropin_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(dropin_file, content)
        console.print(f"[green]✔ Configured initramfs resume hook drop-in:[/green] {dropin_file}")
    else:
        console.print(f"[dim][Dry-run] Would write {dropin_file}[/dim]")


def update_systemd_boot_entries(
    target_root: Path,
    resume_device: str,
    resume_offset: str,
    s2idle: bool,
    dry_run: bool = False,
) -> tuple[list[str], bool]:
    """Inject or rename resume parameters in systemd-boot loader entries.

    Returns:
        (modified_entries, rename_occurred)
    """
    entries_dir = target_root / "boot" / "loader" / "entries"
    if not entries_dir.is_dir():
        alt = target_root / "efi" / "loader" / "entries"
        if alt.is_dir():
            entries_dir = alt
        else:
            return [], False

    modified_entries = []
    rename_occurred = False

    for conf_file in entries_dir.glob("*.conf"):
        try:
            lines = conf_file.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue

        changed = False
        new_lines = []
        for line in lines:
            if line.startswith("options"):
                parts = line.split(maxsplit=1)
                opts = parts[1] if len(parts) > 1 else ""

                # Check if old raw kernel name was used (e.g. resume=/dev/nvme0n1p2)
                m_old = re.search(r"\bresume=(/dev/[^ \t\n\r\f\v\"]+)", opts)
                if m_old and not m_old.group(1).startswith("/dev/mapper/") and resume_device.startswith("UUID="):
                    rename_occurred = True

                # Strip existing resume parameters
                opts = re.sub(r"\bresume=\S+", "", opts)
                opts = re.sub(r"\bresume_offset=\S*", "", opts)
                opts = re.sub(r"\brtc_cmos\.use_acpi_alarm=\S+", "", opts)
                opts = " ".join(opts.split())

                # Inject fresh resume parameters
                opts += f" resume={resume_device} resume_offset={resume_offset}"
                if s2idle:
                    opts += " rtc_cmos.use_acpi_alarm=1"

                new_line = f"options {opts.strip()}"
                if new_line != line:
                    changed = True
                new_lines.append(new_line)
            else:
                new_lines.append(line)

        if changed:
            modified_entries.append(conf_file.name)
            if not dry_run:
                atomic_write(conf_file, "\n".join(new_lines) + "\n")

    return modified_entries, rename_occurred


def update_limine_dropins(
    target_root: Path,
    resume_device: str,
    resume_offset: str,
    s2idle: bool,
    dry_run: bool = False,
) -> None:
    """If Limine bootloader is present, deploy entry drop-ins matching Omarchy."""
    limine_dir = target_root / "etc" / "limine-entry-tool.d"
    if limine_dir.is_dir() or shutil.which("limine-entry-tool"):
        resume_dropin = limine_dir / "resume.conf"
        content = f'KERNEL_CMDLINE[default]+=" resume={resume_device} resume_offset={resume_offset}"\n'
        if not dry_run:
            limine_dir.mkdir(parents=True, exist_ok=True)
            atomic_write(resume_dropin, content)
            console.print(f"[green]✔ Configured Limine drop-in:[/green] {resume_dropin}")

        if s2idle:
            rtc_dropin = limine_dir / "rtc-alarm.conf"
            rtc_content = 'KERNEL_CMDLINE[default]+=" rtc_cmos.use_acpi_alarm=1"\n'
            if not dry_run:
                atomic_write(rtc_dropin, rtc_content)
                console.print(f"[green]✔ Configured Limine s2idle RTC drop-in:[/green] {rtc_dropin}")


def update_fstab(target_root: Path, swap_fstab_path: str, dry_run: bool = False) -> None:
    """Append swapfile to /etc/fstab with timestamped backup and pri=0."""
    fstab_path = target_root / "etc" / "fstab"
    if not fstab_path.is_file():
        return

    content = fstab_path.read_text(encoding="utf-8")
    for line in content.splitlines():
        line_clean = line.strip()
        if not line_clean.startswith("#") and swap_fstab_path in line_clean and "swap" in line_clean:
            return  # Already configured

    if not dry_run:
        # Timestamped backup matching Omarchy: /etc/fstab.<timestamp>.back
        stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        backup_path = target_root / "etc" / f"fstab.{stamp}.back"
        shutil.copy2(fstab_path, backup_path)
        console.print(f"[dim]Backed up fstab to {backup_path}[/dim]")

        # Uncomment if commented out, otherwise append
        new_lines = []
        found_commented = False
        for line in content.splitlines():
            if re.search(rf"^#\s*{re.escape(swap_fstab_path)}\s+none\s+swap", line.strip()):
                new_lines.append(f"{swap_fstab_path} none swap defaults,pri=0 0 0")
                found_commented = True
            else:
                new_lines.append(line)

        if not found_commented:
            new_lines.append(f"\n# Btrfs swapfile for system hibernation\n{swap_fstab_path} none swap defaults,pri=0 0 0\n")

        atomic_write(fstab_path, "\n".join(new_lines).strip() + "\n")
        console.print(f"[green]✔ Updated {fstab_path} with pri=0 swap.[/green]")
    else:
        console.print(f"[dim][Dry-run] Would append swap to {fstab_path} (pri=0)[/dim]")


def check_hibernation_status(target_root: Path) -> dict[str, Any]:
    """Audit current hibernation status matching omarchy-hibernation-available."""
    results: dict[str, Any] = {}

    # 1. Kernel state check
    supported, reason = check_kernel_hibernation_support()
    results["kernel_supported"] = supported
    results["kernel_reason"] = reason

    # 2. Swap size vs hibernation image_size
    img_size_file = Path("/sys/power/image_size")
    image_size_bytes = 0
    if img_size_file.is_file():
        try:
            image_size_bytes = int(img_size_file.read_text(encoding="utf-8").strip())
        except Exception:
            pass
    results["image_size"] = image_size_bytes

    # Sum all non-zram swaps in KiB (from /proc/swaps matching Omarchy logic)
    non_zram_swap_bytes = 0
    try:
        swaps_content = Path("/proc/swaps").read_text(encoding="utf-8")
        for line in swaps_content.splitlines():
            if line.startswith("Filename") or "zram" in line:
                continue
            parts = line.split()
            if len(parts) >= 3 and parts[2].isdigit():
                non_zram_swap_bytes += int(parts[2]) * 1024
    except Exception:
        pass
    results["active_swap_bytes"] = non_zram_swap_bytes

    # Check swapfile on disk
    swap_file = target_root / SWAP_FILE_REL
    results["swapfile_exists"] = swap_file.is_file()
    results["swapfile_size"] = swap_file.stat().st_size if swap_file.is_file() else 0

    mem_total_bytes = get_mem_total_kb() * 1024
    results["mem_total"] = mem_total_bytes

    # Omarchy condition: SWAPSIZE > HIBERNATION_IMAGE_SIZE and resume conf exists
    effective_swap = max(non_zram_swap_bytes, results["swapfile_size"])
    results["swap_sufficient"] = effective_swap > image_size_bytes and effective_swap >= (mem_total_bytes * 0.9)

    # 3. mkinitcpio check
    dropin = target_root / MKINITCPIO_DROPIN_REL
    omarchy_dropin = target_root / "etc" / "mkinitcpio.conf.d" / "omarchy_resume.conf"
    results["mkinitcpio_hook"] = (dropin.is_file() and "resume" in dropin.read_text(encoding="utf-8")) or \
                                 (omarchy_dropin.is_file() and "resume" in omarchy_dropin.read_text(encoding="utf-8"))

    # 4. Bootloader resume options
    entries_dir = target_root / "boot" / "loader" / "entries"
    has_boot_resume = False
    has_boot_offset = False
    if entries_dir.is_dir():
        for conf in entries_dir.glob("*.conf"):
            try:
                c = conf.read_text(encoding="utf-8")
                if "resume=" in c:
                    has_boot_resume = True
                if "resume_offset=" in c and not re.search(r'resume_offset=["\s]*$', c):
                    has_boot_offset = True
            except Exception:
                pass
    results["boot_resume"] = has_boot_resume
    results["boot_offset"] = has_boot_offset

    results["ready"] = supported and results["swap_sufficient"] and results["mkinitcpio_hook"] and has_boot_resume and has_boot_offset
    return results


def setup_hibernation(
    target_root: Path,
    env_mode: str,
    desired_kb: int | None = None,
    dry_run: bool = False,
    auto_mode: bool = False,
    no_rebuild: bool = False,
) -> bool:
    """Execute complete Btrfs hibernation setup."""
    console.print(Panel(
        f"[bold cyan]Dusky Hibernation Setup Engine (Btrfs-Dedicated)[/bold cyan]\n"
        f"[dim]Target: {target_root} | Environment: {env_mode}[/dim]",
        box=box.ROUNDED,
    ))

    # 1. Enforce Btrfs
    verify_btrfs_filesystem(target_root)

    # 2. Pre-flight checks
    supported, reason = check_kernel_hibernation_support()
    if not supported:
        console.print(f"[bold red]Cannot configure hibernation:[/bold red] {reason}")
        return False
    console.print(f"[green]✔[/green] {reason}")

    mem_total_kb = get_mem_total_kb()
    if desired_kb is None:
        desired_kb = mem_total_kb
    desired_bytes = desired_kb * 1024

    console.print(f"[cyan]Target swap capacity:[/cyan] {format_bytes_human(desired_bytes)} (System RAM: {format_bytes_human(mem_total_kb * 1024)})")

    swap_subvol = target_root / SWAP_SUBVOLUME_REL
    swap_file = target_root / SWAP_FILE_REL
    swap_fstab_path = f"/{SWAP_FILE_REL}"

    # 3. Create Btrfs Subvolume if missing (matching Omarchy)
    if not swap_subvol.is_dir():
        if not dry_run:
            console.print("[yellow]Creating Btrfs subvolume for swap...[/yellow]")
            run_cmd("btrfs", "subvolume", "create", str(swap_subvol))
            run_cmd("chattr", "+C", str(swap_subvol), check=False)
            console.print(f"[green]✔ Created Btrfs subvolume:[/green] {swap_subvol} (nodatacow)")
        else:
            console.print(f"[dim][Dry-run] Would create Btrfs subvolume {swap_subvol} with chattr +C[/dim]")

    # 4. Check existing swapfile
    existing_swap = swap_file if swap_file.is_file() else None
    if existing_swap:
        cur_size = existing_swap.stat().st_size
        console.print(f"[yellow]Existing swapfile found:[/yellow] {swap_file} ({format_bytes_human(cur_size)})")
        if cur_size >= desired_bytes * 0.95:
            console.print("[green]✔ Existing swapfile capacity is sufficient. Reusing file without disk writes.[/green]")
        else:
            console.print(f"[yellow]Existing swapfile is smaller than RAM ({format_bytes_human(cur_size)} < {format_bytes_human(desired_bytes)}).[/yellow]")
            if dry_run:
                console.print(f"[dim][Dry-run] Would resize swapfile to {format_bytes_human(desired_bytes)}[/dim]")
            elif not auto_mode:
                console.print(f"[yellow]Resizing swapfile to match physical RAM ({format_bytes_human(desired_bytes)})...[/yellow]")
                run_cmd("swapoff", str(swap_file), check=False)
                swap_file.unlink(missing_ok=True)
                existing_swap = None
            else:
                run_cmd("swapoff", str(swap_file), check=False)
                swap_file.unlink(missing_ok=True)
                existing_swap = None

    # 5. Create Btrfs swapfile via mkswapfile (matching Omarchy)
    if not existing_swap and not dry_run:
        console.print(f"[yellow]Allocating Btrfs swapfile via btrfs filesystem mkswapfile -s {desired_kb}k...[/yellow]")
        run_cmd("btrfs", "filesystem", "mkswapfile", "-s", f"{desired_kb}k", str(swap_file))
        console.print(f"[green]✔ Created Btrfs swapfile:[/green] {swap_file}")

    # 6. Add to fstab with timestamped backup (matching Omarchy)
    update_fstab(target_root, swap_fstab_path, dry_run=dry_run)

    # 7. Enable swap if in userspace
    if env_mode == "userspace" and not dry_run:
        # swapon -p 0
        run_cmd("swapon", "-p", "0", str(swap_file), check=False)
        console.print(f"[green]✔ Enabled swap on {swap_file} (priority 0).[/green]")

    # 8. Install system-sleep hooks (keyboard backlight & fprintd)
    install_system_sleep_hooks(target_root, dry_run=dry_run)

    # 9. Configure mkinitcpio drop-in
    update_mkinitcpio_config(target_root, dry_run=dry_run)

    # 10. Calculate resume device and btrfs resume offset
    if swap_file.is_file():
        resume_dev = calculate_resume_device(swap_file)
        resume_off = calculate_btrfs_resume_offset(swap_file)
        console.print(f"[cyan]Calculated resume parameters:[/cyan] [bold]resume={resume_dev} resume_offset={resume_off}[/bold]")
    else:
        resume_dev = "UUID=<target-uuid>"
        resume_off = "4242"
        console.print(f"[dim][Dry-run] Simulated resume parameters: resume={resume_dev} resume_offset={resume_off}[/dim]")

    s2idle = is_s2idle_system()
    if s2idle:
        console.print("[cyan]s2idle mode detected:[/cyan] Configuring rtc_cmos.use_acpi_alarm=1 for suspend-then-hibernate.")

    # 11. Configure bootloaders (systemd-boot & Limine)
    mod_entries, renamed = update_systemd_boot_entries(
        target_root,
        resume_dev,
        resume_off,
        s2idle=s2idle,
        dry_run=dry_run,
    )
    if mod_entries:
        console.print(f"[green]✔ Updated systemd-boot entries:[/green] {', '.join(mod_entries)}")
    update_limine_dropins(target_root, resume_dev, resume_off, s2idle=s2idle, dry_run=dry_run)

    # 12. Handle pending rebuild marker (matching Omarchy)
    marker_file = target_root / REBUILD_MARKER_REL
    if not no_rebuild and not dry_run and env_mode in ("userspace", "chroot"):
        console.print("[yellow]Regenerating initramfs (mkinitcpio -P)...[/yellow]")
        r_mk = run_cmd("mkinitcpio", "-P", check=False)
        if r_mk.returncode == 0:
            marker_file.unlink(missing_ok=True)
            console.print("[green]✔ Initramfs generated successfully.[/green]")
        else:
            marker_file.parent.mkdir(parents=True, exist_ok=True)
            marker_file.touch()
            console.print("[bold red]Initramfs generation failed; recorded pending rebuild marker.[/bold red]")
            return False

    console.print(Panel(
        "[bold green]Hibernation Setup Completed Successfully![/bold green]\n"
        "Disk swap priority is set to 0, ensuring daily memory compression\n"
        "stays on zram without causing SSD wear until hibernation is triggered.",
        box=box.ROUNDED,
    ))
    return True


def remove_hibernation(target_root: Path, dry_run: bool = False) -> bool:
    """Cleanly dismantle hibernation configuration (matching omarchy-hibernation-remove)."""
    console.print(Panel(
        f"[bold yellow]Dusky Hibernation Removal (Btrfs)[/bold yellow]\n"
        f"[dim]Target: {target_root}[/dim]",
        box=box.ROUNDED,
    ))

    swap_subvol = target_root / SWAP_SUBVOLUME_REL
    swap_file = target_root / SWAP_FILE_REL

    # 1. Disable swap if active
    if swap_file.is_file():
        if not dry_run:
            console.print(f"[yellow]Disabling swap on {swap_file}...[/yellow]")
            run_cmd("swapoff", str(swap_file), check=False)
            swap_file.unlink(missing_ok=True)
            console.print(f"[green]✔ Removed swapfile:[/green] {swap_file}")
        else:
            console.print(f"[dim][Dry-run] Would disable and delete {swap_file}[/dim]")

    # 2. Remove Btrfs subvolume if empty
    if swap_subvol.is_dir() and not dry_run:
        try:
            r = run_cmd("btrfs", "subvolume", "delete", str(swap_subvol), check=False)
            if r.returncode == 0:
                console.print(f"[green]✔ Removed Btrfs subvolume:[/green] {swap_subvol}")
        except Exception:
            pass

    # 3. Clean fstab with timestamped backup
    fstab = target_root / "etc" / "fstab"
    if fstab.is_file() and not dry_run:
        stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        backup_path = target_root / "etc" / f"fstab.{stamp}.back"
        shutil.copy2(fstab, backup_path)
        content = fstab.read_text(encoding="utf-8")
        new_lines = []
        for line in content.splitlines():
            if "swapfile" in line and "swap" in line:
                continue
            if line.strip() == "# Btrfs swapfile for system hibernation":
                continue
            new_lines.append(line)
        atomic_write(fstab, "\n".join(new_lines).strip() + "\n")
        console.print(f"[green]✔ Cleaned {fstab}[/green]")

    # 4. Remove mkinitcpio resume drop-ins
    for dropin_name in ("15-hibernation.conf", "omarchy_resume.conf"):
        dropin = target_root / "etc" / "mkinitcpio.conf.d" / dropin_name
        if dropin.is_file() and not dry_run:
            dropin.unlink(missing_ok=True)
            console.print(f"[green]✔ Removed initramfs drop-in:[/green] {dropin}")

    # 5. Clean system-sleep hooks
    for hook_name in ("keyboard-backlight", "fprintd-resume"):
        hook = target_root / "usr" / "lib" / "systemd" / "system-sleep" / hook_name
        if hook.is_file() and not dry_run:
            hook.unlink(missing_ok=True)
            console.print(f"[green]✔ Removed sleep hook:[/green] {hook}")

    # 6. Clean bootloader options
    entries_dir = target_root / "boot" / "loader" / "entries"
    if entries_dir.is_dir() and not dry_run:
        for conf_file in entries_dir.glob("*.conf"):
            try:
                lines = conf_file.read_text(encoding="utf-8").splitlines()
                new_lines = []
                changed = False
                for line in lines:
                    if line.startswith("options"):
                        parts = line.split(maxsplit=1)
                        opts = parts[1] if len(parts) > 1 else ""
                        new_opts = re.sub(r"\bresume=\S+", "", opts)
                        new_opts = re.sub(r"\bresume_offset=\S+", "", new_opts)
                        new_opts = re.sub(r"\brtc_cmos\.use_acpi_alarm=\S+", "", new_opts)
                        new_opts = " ".join(new_opts.split())
                        if new_opts != opts:
                            changed = True
                        new_lines.append(f"options {new_opts}")
                    else:
                        new_lines.append(line)
                if changed:
                    atomic_write(conf_file, "\n".join(new_lines) + "\n")
                    console.print(f"[green]✔ Cleaned boot parameters from:[/green] {conf_file.name}")
            except Exception:
                pass

    # 7. Clean Limine drop-ins
    for lconf in ("resume.conf", "rtc-alarm.conf"):
        p = target_root / "etc" / "limine-entry-tool.d" / lconf
        if p.is_file() and not dry_run:
            p.unlink(missing_ok=True)

    console.print(Panel("[bold green]Hibernation dismantled successfully.[/bold green]", box=box.ROUNDED))
    return True


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Dusky Universal Hibernation Setup & Validator (Btrfs-Dedicated)")
    parser.add_argument("--target", "-t", type=str, default=None, help="Target root directory (e.g. /mnt or /)")
    parser.add_argument("--check", "-c", action="store_true", help="Audit hibernation status (omarchy-hibernation-available style)")
    parser.add_argument("--remove", action="store_true", help="Remove hibernation setup (omarchy-hibernation-remove style)")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Simulate actions without touching disk")
    parser.add_argument("--size", "-s", type=str, default=None, help="Custom swap size (e.g. 64G, 16G)")
    parser.add_argument("--no-rebuild", action="store_true", help="Skip rebuilding initramfs with mkinitcpio")
    parser.add_argument("--auto", "-y", action="store_true", help="Non-interactive automatic mode")
    args = parser.parse_args()

    if os.geteuid() != 0 and not args.check:
        console.print("[bold red]Error: Root privileges required for hibernation configuration.[/bold red]")
        return 1

    target_root, env_mode = detect_environment(args.target)

    if args.check:
        status = check_hibernation_status(target_root)
        table = Table(title="Dusky Btrfs Hibernation Status Report", box=box.ROUNDED)
        table.add_column("Component", style="cyan")
        table.add_column("Status", style="bold")
        table.add_column("Details")

        table.add_row(
            "Kernel Sleep Support",
            "[green]Supported[/green]" if status["kernel_supported"] else "[red]Not Supported[/red]",
            status["kernel_reason"],
        )
        table.add_row(
            "Physical Memory",
            format_bytes_human(status["mem_total"]),
            f"Image target: {format_bytes_human(status['image_size'])}",
        )
        table.add_row(
            "Btrfs Swapfile",
            "[green]Detected[/green]" if status["swapfile_exists"] else "[yellow]None[/yellow]",
            f"/swap/swapfile ({format_bytes_human(status['swapfile_size'])})" if status["swapfile_exists"] else "No /swap/swapfile found",
        )
        table.add_row(
            "Swap Capacity",
            "[green]Sufficient[/green]" if status["swap_sufficient"] else "[red]Insufficient[/red]",
            f"Active: {format_bytes_human(status['active_swap_bytes'])} (Requires > {format_bytes_human(status['image_size'])})",
        )
        table.add_row(
            "Initramfs Resume Hook",
            "[green]Configured[/green]" if status["mkinitcpio_hook"] else "[yellow]Missing[/yellow]",
            "HOOKS+=(resume) drop-in",
        )
        table.add_row(
            "Bootloader Resume Device",
            "[green]Configured[/green]" if status["boot_resume"] else "[yellow]Missing[/yellow]",
            "resume= parameter in loader entries",
        )
        table.add_row(
            "Bootloader Resume Offset",
            "[green]Configured[/green]" if status["boot_offset"] else "[yellow]Missing[/yellow]",
            "resume_offset= parameter in loader entries",
        )

        console.print(table)
        if status["ready"]:
            console.print("[bold green]✔ System is ready for hibernation.[/bold green]")
            return 0
        else:
            console.print("[bold yellow]Hibernation is not yet fully configured.[/bold yellow]")
            return 1

    if args.remove:
        ok = remove_hibernation(target_root, dry_run=args.dry_run)
        return 0 if ok else 1

    desired_kb = parse_size_to_kb(args.size) if args.size else None
    ok = setup_hibernation(
        target_root=target_root,
        env_mode=env_mode,
        desired_kb=desired_kb,
        dry_run=args.dry_run,
        auto_mode=args.auto,
        no_rebuild=args.no_rebuild,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
