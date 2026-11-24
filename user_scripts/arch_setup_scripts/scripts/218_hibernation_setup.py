#!/usr/bin/env python3
"""
Production-grade Btrfs hibernation configurator for Arch Linux.

Target Path:
    /home/dusk/user_scripts/arch_setup_scripts/scripts/218_hibernation_setup.py

Target Environment:
    Arch Linux (rolling), Linux 7.3+, systemd 262+, Python 3.15+,
    Wayland/Hyprland, Btrfs root with dedicated subvolumes (@, @home, @swap),
    zram (pri=32767) + Btrfs swapfile (pri=0) memory hierarchy,
    dual bootloader support (systemd-boot & Limine).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import errno
import math
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Sequence

# ---------------------------------------------------------------------------
# Constants & System Paths
# ---------------------------------------------------------------------------

SWAP_SUBVOL = Path("/swap")
SWAPFILE_PATH = SWAP_SUBVOL / "swapfile"
FSTAB_PATH = Path("/etc/fstab")
FSTAB_COMMENT = "# Btrfs swapfile for system hibernation"
FSTAB_ENTRY = f"{SWAPFILE_PATH} none swap defaults,pri=0 0 0"

MKINITCPIO_DROPIN_DIR = Path("/etc/mkinitcpio.conf.d")
MKINITCPIO_HIBERNATION_CONF = MKINITCPIO_DROPIN_DIR / "15-hibernation.conf"
MKINITCPIO_OMARCHY_CONF = MKINITCPIO_DROPIN_DIR / "omarchy_resume.conf"

SYSTEMD_BOOT_ENTRIES_DIR = Path("/boot/loader/entries")
LIMINE_DROPIN_DIR = Path("/etc/limine-entry-tool.d")
LIMINE_RESUME_CONF = LIMINE_DROPIN_DIR / "resume.conf"
LIMINE_RTC_CONF = LIMINE_DROPIN_DIR / "rtc-alarm.conf"

SLEEP_HOOK_DIR = Path("/usr/lib/systemd/system-sleep")
SLEEP_HOOK_KBD = SLEEP_HOOK_DIR / "keyboard-backlight"
SLEEP_HOOK_FPRINTD = SLEEP_HOOK_DIR / "fprintd-resume"

DUSKY_REBUILD_MARKER = Path("/var/lib/dusky/hibernation-resume-rebuild")
OMARCHY_REBUILD_MARKER = Path("/var/lib/omarchy/hibernation-resume-rebuild")

SYS_POWER_STATE = Path("/sys/power/state")
SYS_POWER_IMAGE_SIZE = Path("/sys/power/image_size")
SYS_POWER_MEM_SLEEP = Path("/sys/power/mem_sleep")
SYS_KERNEL_LOCKDOWN = Path("/sys/kernel/security/lockdown")
PROC_SWAPS = Path("/proc/swaps")
PROC_MEMINFO = Path("/proc/meminfo")

SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([KMGTP]?)(?:i?B)?\s*$", re.IGNORECASE)
UNIT_MULTIPLIERS: dict[str, int] = {
    "": 1,
    "K": 1024,
    "M": 1024**2,
    "G": 1024**3,
    "T": 1024**4,
    "P": 1024**5,
}

KBD_BACKLIGHT_HOOK_CONTENT = """#!/usr/bin/env bash
# Preserve and restore keyboard backlight brightness across hibernation/sleep.
set -euo pipefail

STATE_DIR="/run/dusky-kbd-backlight"
ACTION="${1:-}"

case "$ACTION" in
    pre)
        mkdir -p "$STATE_DIR"
        for led in /sys/class/leds/*kbd_backlight*; do
            [[ -r "$led/brightness" ]] || continue
            name="$(basename "$led")"
            cat "$led/brightness" > "$STATE_DIR/$name" 2>/dev/null || true
        done
        ;;
    post)
        for led in /sys/class/leds/*kbd_backlight*; do
            [[ -w "$led/brightness" ]] || continue
            name="$(basename "$led")"
            if [[ -r "$STATE_DIR/$name" ]]; then
                cat "$STATE_DIR/$name" > "$led/brightness" 2>/dev/null || true
            fi
        done
        ;;
esac
"""

FPRINTD_RESUME_HOOK_CONTENT = """#!/usr/bin/env bash
# Reset fprintd after hibernation so USB/SPI fingerprint sensors recover cleanly.
set -euo pipefail

ACTION="${1:-}"
MODE="${2:-}"

if [[ "$ACTION" == "post" ]]; then
    case "$MODE" in
        hibernate|hybrid-sleep|suspend-then-hibernate)
            if command -v systemctl >/dev/null 2>&1; then
                if systemctl is-active --quiet fprintd.service 2>/dev/null; then
                    systemctl restart fprintd.service 2>/dev/null || true
                fi
            fi
            ;;
    esac
fi
"""


# ---------------------------------------------------------------------------
# Zero-Dependency ANSI Formatting (NO_COLOR & TTY Aware)
# ---------------------------------------------------------------------------


class Style:
    """Zero-dependency terminal styling honoring NO_COLOR and TTY detection."""

    def __init__(self) -> None:
        use_color = (
            sys.stdout.isatty()
            and os.environ.get("NO_COLOR") is None
            and os.environ.get("TERM", "").lower() != "dumb"
        )
        self.reset = "\033[0m" if use_color else ""
        self.bold = "\033[1m" if use_color else ""
        self.dim = "\033[2m" if use_color else ""
        self.red = "\033[31m" if use_color else ""
        self.green = "\033[32m" if use_color else ""
        self.yellow = "\033[33m" if use_color else ""
        self.blue = "\033[34m" if use_color else ""
        self.cyan = "\033[36m" if use_color else ""

    def header(self, text: str) -> None:
        print(f"\n{self.bold}{self.cyan}==> {text}{self.reset}")

    def info(self, text: str) -> None:
        print(f"  {self.blue}[INFO]{self.reset} {text}")

    def ok(self, text: str) -> None:
        print(f"  {self.green}[ OK ]{self.reset} {text}")

    def warn(self, text: str) -> None:
        print(f"  {self.yellow}[WARN]{self.reset} {text}")

    def err(self, text: str) -> None:
        print(f"  {self.red}[FAIL]{self.reset} {text}", file=sys.stderr)

    def dry(self, text: str) -> None:
        print(f"  {self.dim}[DRY ]{self.reset} {text}")


ui = Style()


class HibernationError(RuntimeError):
    """Domain-specific fatal error during hibernation setup or teardown."""


# ---------------------------------------------------------------------------
# Durable Atomic I/O & Subprocess Primitives
# ---------------------------------------------------------------------------


def _fsync_dir(directory: Path) -> None:
    """Durable fsync on a parent directory after atomic rename or unlink."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    dir_fd = os.open(directory, flags)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def atomic_write(
    path: Path,
    content: str | bytes,
    mode: int = 0o644,
    *,
    dry_run: bool = False,
) -> None:
    """
    Durable atomic file write:
    mkstemp -> fchmod -> write -> fsync(fd) -> close -> os.replace -> fsync(parent).
    """
    if dry_run:
        ui.dry(f"Would atomically write {path} (mode={oct(mode)})")
        return

    data = content.encode("utf-8") if isinstance(content, str) else content
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        dir=parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, mode)
        view = memoryview(data)
        total_written = 0
        while total_written < len(view):
            written = os.write(fd, view[total_written:])
            if written <= 0:
                raise OSError(errno.EIO, f"Short write to temporary file {tmp_path}")
            total_written += written
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(tmp_path, path)
        _fsync_dir(parent)
    except BaseException:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def durable_unlink(path: Path, *, dry_run: bool = False) -> bool:
    """Remove a file if present and fsync its parent directory."""
    if not path.exists() and not path.is_symlink():
        return False
    if dry_run:
        ui.dry(f"Would remove {path}")
        return True
    path.unlink()
    _fsync_dir(path.parent)
    return True


def run_cmd(
    cmd: Sequence[str],
    *,
    check: bool = True,
    capture: bool = True,
    dry_run: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Execute a system command cleanly with optional dry-run interception."""
    if dry_run:
        ui.dry(f"Would execute: {' '.join(cmd)}")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    result = subprocess.run(
        cmd,
        check=False,
        text=True,
        capture_output=capture,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise HibernationError(
            f"Command failed (exit {result.returncode}): {' '.join(cmd)}"
            + (f"\n  {detail}" if detail else "")
        )
    return result


# ---------------------------------------------------------------------------
# Privilege Escalation
# ---------------------------------------------------------------------------


def ensure_root(argv: Sequence[str]) -> None:
    """Self-escalate via sudo or pkexec while preserving all CLI arguments."""
    if os.geteuid() == 0:
        return

    script_path = str(Path(__file__).resolve())
    exec_cmd = [sys.executable, script_path, *argv]

    if sudo := shutil.which("sudo"):
        ui.info("Root privileges required; escalating via sudo...")
        os.execvp(sudo, [sudo, "--", *exec_cmd])
    if pkexec := shutil.which("pkexec"):
        ui.info("Root privileges required; escalating via pkexec...")
        os.execvp(pkexec, [pkexec, *exec_cmd])

    raise HibernationError(
        "Root privileges are required, but neither 'sudo' nor 'pkexec' was found."
    )


# ---------------------------------------------------------------------------
# Hardware, Kernel & Filesystem Discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KernelPreflight:
    is_apple_silicon: bool
    lockdown_mode: str
    disk_sleep_supported: bool
    image_size_bytes: int | None
    s2idle_active: bool
    mem_total_bytes: int
    root_fstype: str


def detect_apple_silicon() -> bool:
    """Detect Apple Silicon / Asahi Linux platforms where hibernation is unsupported."""
    if "asahi" in platform.release().lower():
        return True
    for compat_path in (
        Path("/proc/device-tree/compatible"),
        Path("/sys/firmware/devicetree/base/compatible"),
    ):
        try:
            if b"apple," in compat_path.read_bytes().lower():
                return True
        except OSError:
            continue
    return False


def detect_kernel_lockdown() -> str:
    """Return active kernel lockdown mode ('none', 'integrity', 'confidentiality', or 'unknown')."""
    try:
        raw = SYS_KERNEL_LOCKDOWN.read_text(encoding="utf-8").strip()
    except OSError:
        return "none"
    match = re.search(r"\[([^\]]+)\]", raw)
    return match.group(1).lower() if match else "unknown"


def detect_disk_sleep_supported() -> bool:
    """Check whether 'disk' hibernation state is exposed in /sys/power/state."""
    try:
        content = SYS_POWER_STATE.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return " disk " in f" {content} "


def read_image_size() -> int | None:
    """Read /sys/power/image_size in bytes if accessible."""
    try:
        return int(SYS_POWER_IMAGE_SIZE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def detect_s2idle_active() -> bool:
    """Return True if modern standby ([s2idle]) is the active mem_sleep mode."""
    try:
        content = SYS_POWER_MEM_SLEEP.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return "[s2idle]" in content


def read_mem_total_bytes() -> int:
    """Parse MemTotal from /proc/meminfo in bytes."""
    try:
        for line in PROC_MEMINFO.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError) as exc:
        raise HibernationError(f"Unable to read MemTotal from {PROC_MEMINFO}: {exc}") from exc
    raise HibernationError(f"MemTotal not found in {PROC_MEMINFO}")


def detect_mount_fstype(target: Path) -> str:
    """Determine filesystem type for a given path."""
    if findmnt := shutil.which("findmnt"):
        res = run_cmd([findmnt, "-n", "-o", "FSTYPE", "-T", str(target)], check=False)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip().splitlines()[0].strip().lower()

    best_mp = ""
    best_fs = "unknown"
    target_str = str(target.resolve())
    try:
        for line in Path("/proc/mounts").read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            mp, fstype = parts[1], parts[2]
            if target_str == mp or mp == "/" or target_str.startswith(mp.rstrip("/") + "/"):
                if len(mp) >= len(best_mp):
                    best_mp = mp
                    best_fs = fstype.lower()
    except OSError:
        pass
    return best_fs


def is_mountpoint(path: Path) -> bool:
    """
    Determine whether `path` is an independent mountpoint (e.g. subvol=/@swap
    mounted at /swap via /etc/fstab).
    Uses `findmnt -M <path>` as authoritative check with os.path.ismount fallback.
    """
    if not path.exists():
        return False
    if findmnt := shutil.which("findmnt"):
        res = run_cmd([findmnt, "-M", str(path)], check=False)
        if res.returncode == 0:
            return True
    try:
        return os.path.ismount(path)
    except OSError:
        return False


def run_preflight() -> KernelPreflight:
    """Gather all kernel, hardware, and filesystem telemetry."""
    return KernelPreflight(
        is_apple_silicon=detect_apple_silicon(),
        lockdown_mode=detect_kernel_lockdown(),
        disk_sleep_supported=detect_disk_sleep_supported(),
        image_size_bytes=read_image_size(),
        s2idle_active=detect_s2idle_active(),
        mem_total_bytes=read_mem_total_bytes(),
        root_fstype=detect_mount_fstype(Path("/")),
    )


def validate_preflight_for_enable(pf: KernelPreflight) -> None:
    """Enforce mandatory kernel and filesystem requirements before enabling."""
    if pf.is_apple_silicon:
        raise HibernationError(
            "Apple Silicon / Asahi Linux detected: S4 disk hibernation is not supported."
        )
    if pf.lockdown_mode in {"integrity", "confidentiality"}:
        raise HibernationError(
            f"Kernel lockdown is active ([{pf.lockdown_mode}]): "
            "unsigned swap hibernation images are blocked by the kernel."
        )
    if not pf.disk_sleep_supported:
        raise HibernationError(
            f"'disk' state not advertised in {SYS_POWER_STATE}: "
            "kernel or firmware does not support hibernation."
        )
    if pf.image_size_bytes is None:
        ui.warn(f"Could not read {SYS_POWER_IMAGE_SIZE}; continuing with kernel default.")
    if pf.root_fstype != "btrfs":
        raise HibernationError(
            f"Root filesystem is '{pf.root_fstype}', expected 'btrfs'."
        )


# ---------------------------------------------------------------------------
# Size Parsing & Swap Hierarchy Inspection
# ---------------------------------------------------------------------------


def parse_size_bytes(spec: str) -> int:
    """Parse human-readable size strings like '32G', '16GiB', '8192M' into bytes."""
    match = SIZE_RE.match(spec)
    if not match:
        raise HibernationError(f"Invalid size specification: '{spec}'")
    value = float(match.group(1))
    unit = match.group(2).upper()
    size_bytes = int(value * UNIT_MULTIPLIERS[unit])
    if size_bytes < 512 * 1024**2:
        raise HibernationError("Swapfile size must be at least 512M.")
    return (size_bytes // 4096) * 4096


def compute_recommended_swap_bytes(mem_total_bytes: int) -> int:
    """Compute RAM rounded up to whole GiB + 2 GiB safety headroom."""
    gib = 1024**3
    ram_gib = math.ceil(mem_total_bytes / gib)
    return (ram_gib + 2) * gib


def format_gib(size_bytes: int) -> str:
    return f"{size_bytes / (1024**3):.2f} GiB"


def read_active_swaps() -> list[dict[str, str]]:
    """Parse /proc/swaps into structured entries."""
    swaps: list[dict[str, str]] = []
    try:
        lines = PROC_SWAPS.read_text(encoding="utf-8").splitlines()
    except OSError:
        return swaps
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 5:
            swaps.append(
                {
                    "filename": parts[0],
                    "type": parts[1],
                    "size_kib": parts[2],
                    "used_kib": parts[3],
                    "priority": parts[4],
                }
            )
    return swaps


def find_active_swap(path: Path) -> dict[str, str] | None:
    target = str(path)
    for entry in read_active_swaps():
        if entry["filename"] == target:
            return entry
    return None


# ---------------------------------------------------------------------------
# Btrfs Subvolume, Swapfile Provisioning, Teardown & Offset Computation
# ---------------------------------------------------------------------------


def is_btrfs_subvolume(path: Path) -> bool:
    """Return True if path is an existing Btrfs subvolume (inode 256 on btrfs)."""
    if not path.is_dir():
        return False
    try:
        st = path.stat()
    except OSError:
        return False
    if st.st_ino == 256 and detect_mount_fstype(path) == "btrfs":
        return True
    if btrfs_bin := shutil.which("btrfs"):
        res = run_cmd([btrfs_bin, "subvolume", "show", str(path)], check=False)
        return res.returncode == 0
    return False


def ensure_swap_subvolume(*, dry_run: bool) -> None:
    """Ensure dedicated /swap Btrfs subvolume exists (mounted or local) with NOCOW + 0700."""
    if is_mountpoint(SWAP_SUBVOL):
        fstype = detect_mount_fstype(SWAP_SUBVOL)
        if fstype != "btrfs":
            raise HibernationError(
                f"{SWAP_SUBVOL} is mounted as '{fstype}', expected 'btrfs'."
            )
        ui.ok(f"Dedicated mounted Btrfs subvolume detected: {SWAP_SUBVOL}")
    elif is_btrfs_subvolume(SWAP_SUBVOL):
        ui.ok(f"Dedicated local Btrfs subvolume already exists: {SWAP_SUBVOL}")
    elif SWAP_SUBVOL.exists():
        if not SWAP_SUBVOL.is_dir():
            raise HibernationError(f"{SWAP_SUBVOL} exists and is not a directory.")
        if any(SWAP_SUBVOL.iterdir()):
            raise HibernationError(
                f"{SWAP_SUBVOL} is a non-empty regular directory; "
                "cannot convert safely to a Btrfs subvolume."
            )
        ui.info(f"Replacing empty regular directory {SWAP_SUBVOL} with Btrfs subvolume.")
        if not dry_run:
            SWAP_SUBVOL.rmdir()
        run_cmd(["btrfs", "subvolume", "create", str(SWAP_SUBVOL)], dry_run=dry_run)
        ui.ok(f"Created Btrfs subvolume: {SWAP_SUBVOL}")
    else:
        run_cmd(["btrfs", "subvolume", "create", str(SWAP_SUBVOL)], dry_run=dry_run)
        ui.ok(f"Created Btrfs subvolume: {SWAP_SUBVOL}")

    if dry_run:
        ui.dry(f"Would set permissions 0700 and NOCOW (+C) on {SWAP_SUBVOL}")
        return

    os.chmod(SWAP_SUBVOL, 0o700)
    if chattr := shutil.which("chattr"):
        run_cmd([chattr, "+C", str(SWAP_SUBVOL)], check=False)


def provision_btrfs_swapfile(size_bytes: int, *, dry_run: bool) -> None:
    """
    Allocate and format /swap/swapfile on Btrfs using `btrfs filesystem mkswapfile`
    with `chattr +C` + `fallocate` + `mkswap` fallback, then activate at pri=0.
    """
    active = find_active_swap(SWAPFILE_PATH)

    if SWAPFILE_PATH.exists():
        current_size = SWAPFILE_PATH.stat().st_size
        if abs(current_size - size_bytes) < 16 * 1024**2:
            ui.ok(
                f"Existing swapfile matches target size ({format_gib(current_size)}): {SWAPFILE_PATH}"
            )
            if active and active["priority"] == "0":
                ui.ok(f"Swapfile already active at priority 0: {SWAPFILE_PATH}")
                return
            if active:
                run_cmd(["swapoff", str(SWAPFILE_PATH)], dry_run=dry_run)
            run_cmd(["swapon", "-p", "0", str(SWAPFILE_PATH)], dry_run=dry_run)
            ui.ok(f"Activated swapfile with priority 0: {SWAPFILE_PATH}")
            return

        ui.info(
            f"Re-provisioning swapfile ({format_gib(current_size)} -> {format_gib(size_bytes)})."
        )
        if active:
            run_cmd(["swapoff", str(SWAPFILE_PATH)], dry_run=dry_run)
        durable_unlink(SWAPFILE_PATH, dry_run=dry_run)

    stat_target = SWAP_SUBVOL if SWAP_SUBVOL.exists() else Path("/")
    free_bytes = shutil.disk_usage(stat_target).free
    safety_margin = 2 * 1024**3
    if free_bytes < size_bytes + safety_margin:
        raise HibernationError(
            f"Insufficient free space on {stat_target}: "
            f"need {format_gib(size_bytes + safety_margin)}, have {format_gib(free_bytes)}."
        )

    size_mib = max(1, size_bytes // (1024**2))
    size_arg = f"{size_mib}m"

    ui.info(f"Provisioning Btrfs swapfile ({format_gib(size_bytes)}) at {SWAPFILE_PATH}...")
    if dry_run:
        ui.dry(
            f"Would run: btrfs filesystem mkswapfile --size {size_arg} --uuid clear {SWAPFILE_PATH}"
        )
        ui.dry(f"Would run: swapon -p 0 {SWAPFILE_PATH}")
        return

    btrfs_bin = shutil.which("btrfs")
    mkswapfile_ok = False
    if btrfs_bin:
        res = run_cmd(
            [
                btrfs_bin,
                "filesystem",
                "mkswapfile",
                "--size",
                size_arg,
                "--uuid",
                "clear",
                str(SWAPFILE_PATH),
            ],
            check=False,
        )
        if res.returncode == 0:
            mkswapfile_ok = True
        else:
            ui.warn(
                "btrfs filesystem mkswapfile failed; falling back to chattr +C + fallocate + mkswap."
            )
            SWAPFILE_PATH.unlink(missing_ok=True)

    if not mkswapfile_ok:
        fd = os.open(SWAPFILE_PATH, os.O_CREAT | os.O_RDWR | os.O_TRUNC, 0o600)
        os.close(fd)
        os.chmod(SWAPFILE_PATH, 0o600)
        if chattr := shutil.which("chattr"):
            run_cmd([chattr, "+C", str(SWAPFILE_PATH)], check=True)
        else:
            raise HibernationError("chattr is required for fallback Btrfs swapfile creation.")
        run_cmd(["fallocate", "-l", str(size_bytes), str(SWAPFILE_PATH)], check=True)
        run_cmd(["mkswap", str(SWAPFILE_PATH)], check=True)

    os.chmod(SWAPFILE_PATH, 0o600)
    run_cmd(["swapon", "-p", "0", str(SWAPFILE_PATH)], check=True)
    ui.ok(f"Provisioned and activated swapfile (pri=0): {SWAPFILE_PATH}")


def teardown_btrfs_swap(*, dry_run: bool) -> None:
    """
    Safely deactivate and remove /swap/swapfile.
    If /swap is an independent mountpoint (e.g. subvol=/@swap in /etc/fstab),
    do NOT run `btrfs subvolume delete /swap` (which fails with Invalid argument).
    Only delete /swap if it is an unmounted, empty local subvolume/directory inside
    the root mount, ignoring subvolume deletion errors gracefully.
    """
    if find_active_swap(SWAPFILE_PATH):
        run_cmd(["swapoff", str(SWAPFILE_PATH)], dry_run=dry_run)
        ui.ok(f"Deactivated swapfile: {SWAPFILE_PATH}")
    else:
        ui.info(f"Swapfile is not currently active: {SWAPFILE_PATH}")

    if durable_unlink(SWAPFILE_PATH, dry_run=dry_run):
        ui.ok(f"Deleted swapfile: {SWAPFILE_PATH}")

    if not SWAP_SUBVOL.exists():
        return

    # Critical guard: never attempt subvolume deletion on an active mountpoint
    if is_mountpoint(SWAP_SUBVOL):
        ui.info(
            f"{SWAP_SUBVOL} is an independent mountpoint (e.g. subvol=/@swap); "
            "preserving mountpoint and skipping subvolume deletion."
        )
        return

    remaining = [] if dry_run else [p for p in SWAP_SUBVOL.iterdir() if p != SWAPFILE_PATH]
    if remaining:
        ui.warn(f"{SWAP_SUBVOL} is not empty; skipping subvolume/directory removal.")
        return

    if is_btrfs_subvolume(SWAP_SUBVOL):
        if dry_run:
            ui.dry(f"Would attempt: btrfs subvolume delete {SWAP_SUBVOL}")
            return
        res = run_cmd(["btrfs", "subvolume", "delete", str(SWAP_SUBVOL)], check=False)
        if res.returncode == 0:
            ui.ok(f"Deleted empty local Btrfs subvolume: {SWAP_SUBVOL}")
        else:
            detail = (res.stderr or res.stdout or "").strip()
            ui.warn(
                f"Could not delete {SWAP_SUBVOL} (ignored safely)"
                + (f": {detail}" if detail else ".")
            )
    elif SWAP_SUBVOL.is_dir():
        if dry_run:
            ui.dry(f"Would remove empty directory {SWAP_SUBVOL}")
            return
        try:
            SWAP_SUBVOL.rmdir()
            ui.ok(f"Removed empty directory: {SWAP_SUBVOL}")
        except OSError as exc:
            ui.warn(f"Could not remove directory {SWAP_SUBVOL} (ignored safely): {exc}")


def resolve_swapfile_uuid() -> str:
    """Resolve the Btrfs filesystem UUID hosting /swap/swapfile."""
    target = SWAPFILE_PATH if SWAPFILE_PATH.exists() else (
        SWAP_SUBVOL if SWAP_SUBVOL.exists() else Path("/")
    )
    res = run_cmd(["findmnt", "-n", "-o", "UUID", "-T", str(target)], check=True)
    uuid = res.stdout.strip().splitlines()[0].strip()
    if not uuid:
        raise HibernationError(f"Unable to determine filesystem UUID for {target}.")
    return uuid


def resolve_resume_offset(*, dry_run: bool = False) -> str:
    """
    Compute the physical resume_offset for /swap/swapfile:
    Primary:  btrfs inspect-internal map-swapfile -r /swap/swapfile
    Fallback: filefrag -v /swap/swapfile
    """
    if dry_run and not SWAPFILE_PATH.exists():
        return "<SIMULATED_OFFSET>"

    if not SWAPFILE_PATH.exists():
        raise HibernationError(f"Swapfile does not exist: {SWAPFILE_PATH}")

    if btrfs_bin := shutil.which("btrfs"):
        res = run_cmd(
            [btrfs_bin, "inspect-internal", "map-swapfile", "-r", str(SWAPFILE_PATH)],
            check=False,
        )
        if res.returncode == 0:
            offset = res.stdout.strip().splitlines()[-1].strip()
            if offset.isdigit() and int(offset) > 0:
                return offset

    if filefrag_bin := shutil.which("filefrag"):
        res = run_cmd([filefrag_bin, "-v", str(SWAPFILE_PATH)], check=True)
        for line in res.stdout.splitlines():
            match = re.match(r"^\s*0:\s+\d+\.\.\s*\d+:\s+(\d+)\.\.", line)
            if match:
                offset = match.group(1)
                if offset.isdigit() and int(offset) > 0:
                    return offset

    raise HibernationError(
        f"Failed to resolve physical resume_offset for {SWAPFILE_PATH} via btrfs or filefrag."
    )


# ---------------------------------------------------------------------------
# /etc/fstab Management
# ---------------------------------------------------------------------------


def _backup_fstab(*, dry_run: bool) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = Path(f"/etc/fstab.bak.{stamp}")
    if dry_run:
        ui.dry(f"Would back up {FSTAB_PATH} -> {backup_path}")
        return backup_path
    content = FSTAB_PATH.read_bytes()
    mode = stat.S_IMODE(FSTAB_PATH.stat().st_mode)
    atomic_write(backup_path, content, mode=mode)
    ui.info(f"Created fstab backup: {backup_path}")
    return backup_path


def _clean_fstab_lines(lines: list[str]) -> list[str]:
    """
    Remove ONLY the hibernation comment and `/swap/swapfile` swap entries.
    Dedicated Btrfs subvolume mounts (e.g. `UUID=... /swap btrfs subvol=/@swap`)
    are strictly preserved.
    """
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == FSTAB_COMMENT:
            continue
        fields = stripped.split()
        if fields and fields[0] == str(SWAPFILE_PATH):
            continue
        cleaned.append(line)
    while cleaned and cleaned[-1].strip() == "":
        cleaned.pop()
    return cleaned


def update_fstab_enable(*, dry_run: bool) -> None:
    """Atomically ensure /etc/fstab contains the pri=0 Btrfs swapfile entry."""
    original = FSTAB_PATH.read_text(encoding="utf-8")
    cleaned = _clean_fstab_lines(original.splitlines())
    new_lines = [*cleaned, "", FSTAB_COMMENT, FSTAB_ENTRY]
    new_content = "\n".join(new_lines) + "\n"

    if new_content == original:
        ui.ok(f"{FSTAB_PATH} already contains the hibernation swapfile entry.")
        return

    _backup_fstab(dry_run=dry_run)
    mode = stat.S_IMODE(FSTAB_PATH.stat().st_mode)
    atomic_write(FSTAB_PATH, new_content, mode=mode, dry_run=dry_run)
    ui.ok(f"Updated {FSTAB_PATH} with pri=0 hibernation swapfile entry.")


def update_fstab_disable(*, dry_run: bool) -> None:
    """Atomically strip hibernation swapfile entries from /etc/fstab."""
    if not FSTAB_PATH.exists():
        return
    original = FSTAB_PATH.read_text(encoding="utf-8")
    cleaned = _clean_fstab_lines(original.splitlines())
    new_content = ("\n".join(cleaned) + "\n") if cleaned else ""

    if new_content == original:
        ui.ok(f"{FSTAB_PATH} has no hibernation swapfile entry to remove.")
        return

    _backup_fstab(dry_run=dry_run)
    mode = stat.S_IMODE(FSTAB_PATH.stat().st_mode)
    atomic_write(FSTAB_PATH, new_content, mode=mode, dry_run=dry_run)
    ui.ok(f"Removed hibernation swapfile entry from {FSTAB_PATH}.")


# ---------------------------------------------------------------------------
# Initramfs Hook & Rebuild Intelligence
# ---------------------------------------------------------------------------


def configure_mkinitcpio_enable(*, dry_run: bool) -> None:
    """Write /etc/mkinitcpio.conf.d/15-hibernation.conf with HOOKS+=(resume)."""
    content = (
        "# Managed by 218_hibernation_setup.py\n"
        "HOOKS+=(resume)\n"
    )
    if MKINITCPIO_HIBERNATION_CONF.exists():
        if MKINITCPIO_HIBERNATION_CONF.read_text(encoding="utf-8") == content:
            ui.ok(f"Initramfs drop-in already configured: {MKINITCPIO_HIBERNATION_CONF}")
            return
    atomic_write(MKINITCPIO_HIBERNATION_CONF, content, mode=0o644, dry_run=dry_run)
    ui.ok(f"Configured initramfs hook: {MKINITCPIO_HIBERNATION_CONF}")


def configure_mkinitcpio_disable(*, dry_run: bool) -> None:
    """Remove 15-hibernation.conf and legacy omarchy_resume.conf drop-ins."""
    for dropin in (MKINITCPIO_HIBERNATION_CONF, MKINITCPIO_OMARCHY_CONF):
        if durable_unlink(dropin, dry_run=dry_run):
            ui.ok(f"Removed initramfs drop-in: {dropin}")


def handle_initramfs_rebuild(*, action: str, no_rebuild: bool, dry_run: bool) -> None:
    """
    Rebuild initramfs immediately on standalone execution, or manage pending
    rebuild markers when `--no-rebuild` is supplied by an orchestrator.
    """
    if no_rebuild:
        if action == "enable":
            marker_payload = (
                "pending=1\n"
                f"timestamp={datetime.now().isoformat(timespec='seconds')}\n"
            )
            atomic_write(DUSKY_REBUILD_MARKER, marker_payload, mode=0o644, dry_run=dry_run)
            ui.info(
                f"Skipped initramfs rebuild (--no-rebuild); "
                f"created pending marker: {DUSKY_REBUILD_MARKER}"
            )
        else:
            for marker in (DUSKY_REBUILD_MARKER, OMARCHY_REBUILD_MARKER):
                if durable_unlink(marker, dry_run=dry_run):
                    ui.info(f"Removed pending rebuild marker: {marker}")
            ui.info("Skipped initramfs rebuild (--no-rebuild).")
        return

    if limine_mk := shutil.which("limine-mkinitcpio"):
        ui.info("Rebuilding initramfs and Limine entries via limine-mkinitcpio...")
        run_cmd([limine_mk], capture=False, dry_run=dry_run)
        ui.ok("Completed limine-mkinitcpio rebuild.")
    elif mkinitcpio := shutil.which("mkinitcpio"):
        ui.info("Rebuilding all initramfs presets via mkinitcpio -P...")
        run_cmd([mkinitcpio, "-P"], capture=False, dry_run=dry_run)
        ui.ok("Completed mkinitcpio -P rebuild.")
        if limine_update := shutil.which("limine-update"):
            if limine_detected():
                ui.info("Refreshing Limine boot entries via limine-update...")
                run_cmd([limine_update], capture=False, dry_run=dry_run)
                ui.ok("Completed limine-update.")
    else:
        raise HibernationError("Neither 'limine-mkinitcpio' nor 'mkinitcpio' was found.")

    for marker in (DUSKY_REBUILD_MARKER, OMARCHY_REBUILD_MARKER):
        if durable_unlink(marker, dry_run=dry_run):
            ui.ok(f"Cleared pending rebuild marker: {marker}")


# ---------------------------------------------------------------------------
# Bootloader Integration (systemd-boot & Limine)
# ---------------------------------------------------------------------------


def _strip_hibernation_tokens(tokens: list[str]) -> list[str]:
    """Remove resume=, resume_offset=, and rtc_cmos.use_acpi_alarm= kernel parameters."""
    prefixes = ("resume=", "resume_offset=", "rtc_cmos.use_acpi_alarm=")
    return [tok for tok in tokens if not tok.startswith(prefixes)]


def systemd_boot_entries() -> list[Path]:
    """Return sorted list of systemd-boot entry .conf files."""
    if not SYSTEMD_BOOT_ENTRIES_DIR.is_dir():
        return []
    return sorted(p for p in SYSTEMD_BOOT_ENTRIES_DIR.glob("*.conf") if p.is_file())


def limine_detected() -> bool:
    """Detect whether Limine bootloader / limine-entry-tool is present."""
    return (
        LIMINE_DROPIN_DIR.is_dir()
        or shutil.which("limine-entry-tool") is not None
        or shutil.which("limine-mkinitcpio") is not None
        or shutil.which("limine-update") is not None
        or Path("/boot/limine.conf").exists()
        or Path("/boot/EFI/limine").exists()
    )


def update_systemd_boot(
    *,
    enable: bool,
    uuid: str = "",
    offset: str = "",
    s2idle_active: bool = False,
    dry_run: bool = False,
) -> int:
    """Inject or strip hibernation kernel parameters in /boot/loader/entries/*.conf."""
    entries = systemd_boot_entries()
    if not entries:
        return 0

    modified_count = 0
    for entry_path in entries:
        original = entry_path.read_text(encoding="utf-8")
        lines = original.splitlines()
        new_lines: list[str] = []
        changed = False

        for line in lines:
            if match := re.match(r"^(\s*options\s+)(.*)$", line):
                prefix, rest = match.group(1), match.group(2)
                tokens = _strip_hibernation_tokens(rest.split())
                if enable:
                    tokens.extend([f"resume=UUID={uuid}", f"resume_offset={offset}"])
                    if s2idle_active:
                        tokens.append("rtc_cmos.use_acpi_alarm=1")
                rebuilt = f"{prefix}{' '.join(tokens)}"
                if rebuilt != line:
                    changed = True
                new_lines.append(rebuilt)
            else:
                new_lines.append(line)

        if changed:
            new_content = "\n".join(new_lines) + "\n"
            mode = stat.S_IMODE(entry_path.stat().st_mode)
            atomic_write(entry_path, new_content, mode=mode, dry_run=dry_run)
            modified_count += 1
            verb = "Updated" if enable else "Cleaned"
            ui.ok(f"{verb} systemd-boot entry: {entry_path}")

    if modified_count == 0:
        state_msg = "already configured" if enable else "already clean"
        ui.ok(f"systemd-boot entries ({len(entries)}) {state_msg}.")
    return len(entries)


def update_limine(
    *,
    enable: bool,
    uuid: str = "",
    offset: str = "",
    s2idle_active: bool = False,
    dry_run: bool = False,
) -> bool:
    """Manage /etc/limine-entry-tool.d/resume.conf and rtc-alarm.conf."""
    if not limine_detected():
        return False

    if enable:
        resume_content = (
            "# Managed by 218_hibernation_setup.py\n"
            f'KERNEL_CMDLINE[default]+=" resume=UUID={uuid} resume_offset={offset}"\n'
        )
        atomic_write(LIMINE_RESUME_CONF, resume_content, mode=0o644, dry_run=dry_run)
        ui.ok(f"Configured Limine drop-in: {LIMINE_RESUME_CONF}")

        if s2idle_active:
            rtc_content = (
                "# Managed by 218_hibernation_setup.py (s2idle modern standby fix)\n"
                'KERNEL_CMDLINE[default]+=" rtc_cmos.use_acpi_alarm=1"\n'
            )
            atomic_write(LIMINE_RTC_CONF, rtc_content, mode=0o644, dry_run=dry_run)
            ui.ok(f"Configured Limine s2idle drop-in: {LIMINE_RTC_CONF}")
        elif durable_unlink(LIMINE_RTC_CONF, dry_run=dry_run):
            ui.info(f"Removed unused Limine drop-in: {LIMINE_RTC_CONF}")
    else:
        for dropin in (LIMINE_RESUME_CONF, LIMINE_RTC_CONF):
            if durable_unlink(dropin, dry_run=dry_run):
                ui.ok(f"Removed Limine drop-in: {dropin}")

    return True


# ---------------------------------------------------------------------------
# Systemd Sleep Hooks
# ---------------------------------------------------------------------------


def install_sleep_hooks(*, dry_run: bool) -> None:
    """Install executable keyboard-backlight and fprintd-resume systemd sleep hooks."""
    hooks = (
        (SLEEP_HOOK_KBD, KBD_BACKLIGHT_HOOK_CONTENT),
        (SLEEP_HOOK_FPRINTD, FPRINTD_RESUME_HOOK_CONTENT),
    )
    for hook_path, content in hooks:
        if hook_path.exists():
            cur_mode = stat.S_IMODE(hook_path.stat().st_mode)
            if hook_path.read_text(encoding="utf-8") == content and cur_mode == 0o755:
                ui.ok(f"Sleep hook already installed: {hook_path}")
                continue
        atomic_write(hook_path, content, mode=0o755, dry_run=dry_run)
        ui.ok(f"Installed sleep hook: {hook_path}")


def remove_sleep_hooks(*, dry_run: bool) -> None:
    """Remove installed systemd sleep hooks."""
    for hook_path in (SLEEP_HOOK_KBD, SLEEP_HOOK_FPRINTD):
        if durable_unlink(hook_path, dry_run=dry_run):
            ui.ok(f"Removed sleep hook: {hook_path}")


# ---------------------------------------------------------------------------
# Core Engines: setup_hibernation, remove_hibernation, check_hibernation
# ---------------------------------------------------------------------------


def setup_hibernation(args: argparse.Namespace) -> int:
    """Enable and configure Btrfs swapfile hibernation end-to-end."""
    ui.header("Hibernation Enable Engine" + (" (DRY RUN)" if args.dry_run else ""))

    pf = run_preflight()
    validate_preflight_for_enable(pf)

    ui.ok(
        f"Preflight passed: Btrfs root, disk sleep supported, "
        f"lockdown={pf.lockdown_mode}, RAM={format_gib(pf.mem_total_bytes)}"
    )
    if pf.s2idle_active:
        ui.info("Modern standby [s2idle] detected; will append rtc_cmos.use_acpi_alarm=1.")

    target_bytes = (
        parse_size_bytes(args.size)
        if args.size
        else compute_recommended_swap_bytes(pf.mem_total_bytes)
    )
    ui.info(f"Target hibernation swapfile size: {format_gib(target_bytes)}")

    ui.header("1. Btrfs Swap Subvolume & Swapfile")
    ensure_swap_subvolume(dry_run=args.dry_run)
    provision_btrfs_swapfile(target_bytes, dry_run=args.dry_run)

    ui.header("2. /etc/fstab Persistence")
    update_fstab_enable(dry_run=args.dry_run)

    ui.header("3. Initramfs Resume Hook")
    configure_mkinitcpio_enable(dry_run=args.dry_run)

    ui.header("4. Bootloader Kernel Parameters")
    uuid = resolve_swapfile_uuid()
    offset = resolve_resume_offset(dry_run=args.dry_run)
    ui.info(f"Resolved resume=UUID={uuid} resume_offset={offset}")

    sd_count = update_systemd_boot(
        enable=True,
        uuid=uuid,
        offset=offset,
        s2idle_active=pf.s2idle_active,
        dry_run=args.dry_run,
    )
    limine_ok = update_limine(
        enable=True,
        uuid=uuid,
        offset=offset,
        s2idle_active=pf.s2idle_active,
        dry_run=args.dry_run,
    )
    if sd_count == 0 and not limine_ok:
        ui.warn(
            "Neither systemd-boot entries nor Limine drop-ins were detected. "
            f"Manually add: resume=UUID={uuid} resume_offset={offset}"
        )

    ui.header("5. Systemd Sleep Hooks")
    install_sleep_hooks(dry_run=args.dry_run)

    ui.header("6. Initramfs Generation")
    handle_initramfs_rebuild(
        action="enable",
        no_rebuild=args.no_rebuild,
        dry_run=args.dry_run,
    )

    ui.header("Hibernation Setup Complete")
    if args.no_rebuild:
        ui.info("Initramfs rebuild deferred; run mkinitcpio -P / limine-mkinitcpio before rebooting.")
    elif not args.dry_run:
        ui.ok("System is configured for hibernation. Reboot once so new kernel parameters take effect.")
    return 0


def remove_hibernation(args: argparse.Namespace) -> int:
    """Disable hibernation and cleanly tear down swapfile, hooks, and bootloader params."""
    ui.header("Hibernation Disable Engine" + (" (DRY RUN)" if args.dry_run else ""))

    ui.header("1. Swap Deactivation & Btrfs Teardown")
    teardown_btrfs_swap(dry_run=args.dry_run)

    ui.header("2. /etc/fstab Cleanup")
    update_fstab_disable(dry_run=args.dry_run)

    ui.header("3. Initramfs Drop-Ins & Sleep Hooks Cleanup")
    configure_mkinitcpio_disable(dry_run=args.dry_run)
    remove_sleep_hooks(dry_run=args.dry_run)

    ui.header("4. Bootloader Parameters Cleanup")
    update_systemd_boot(enable=False, dry_run=args.dry_run)
    update_limine(enable=False, dry_run=args.dry_run)

    ui.header("5. Initramfs Generation & Pending Markers")
    handle_initramfs_rebuild(
        action="disable",
        no_rebuild=args.no_rebuild,
        dry_run=args.dry_run,
    )
    for marker in (DUSKY_REBUILD_MARKER, OMARCHY_REBUILD_MARKER):
        if durable_unlink(marker, dry_run=args.dry_run):
            ui.ok(f"Cleaned marker: {marker}")

    ui.header("Hibernation Teardown Complete")
    return 0


def check_hibernation(_args: argparse.Namespace) -> int:
    """Perform a read-only audit of hibernation readiness and configuration."""
    ui.header("Hibernation Configuration Audit (Read-Only)")

    pf = run_preflight()
    ui.info(f"Root filesystem: {pf.root_fstype}")
    ui.info(f"Total RAM: {format_gib(pf.mem_total_bytes)}")
    ui.info(f"Apple Silicon / Asahi: {'yes (unsupported)' if pf.is_apple_silicon else 'no'}")
    ui.info(f"Kernel lockdown: {pf.lockdown_mode}")
    ui.info(f"Disk sleep (/sys/power/state): {'supported' if pf.disk_sleep_supported else 'unsupported'}")
    ui.info(
        f"Image size (/sys/power/image_size): "
        f"{format_gib(pf.image_size_bytes) if pf.image_size_bytes is not None else 'unreadable'}"
    )
    ui.info(f"Modern standby (s2idle): {'active' if pf.s2idle_active else 'inactive'}")
    ui.info(
        f"{SWAP_SUBVOL} mount status: "
        f"{'mounted subvolume' if is_mountpoint(SWAP_SUBVOL) else ('local subvolume' if is_btrfs_subvolume(SWAP_SUBVOL) else 'not a subvolume')}"
    )

    ui.header("Swap Hierarchy")
    swaps = read_active_swaps()
    if not swaps:
        ui.warn("No active swap devices found.")
    for entry in swaps:
        size_mib = int(entry["size_kib"]) / 1024
        ui.info(
            f"{entry['filename']} (type={entry['type']}, "
            f"size={size_mib:.0f} MiB, pri={entry['priority']})"
        )

    if SWAPFILE_PATH.exists():
        st = SWAPFILE_PATH.stat()
        ui.ok(
            f"Swapfile present: {SWAPFILE_PATH} "
            f"({format_gib(st.st_size)}, mode={oct(stat.S_IMODE(st.st_mode))})"
        )
        try:
            uuid = resolve_swapfile_uuid()
            offset = resolve_resume_offset()
            ui.ok(f"Resume coordinates: resume=UUID={uuid} resume_offset={offset}")
        except HibernationError as exc:
            ui.warn(f"Could not inspect swapfile offset (root may be required): {exc}")
    else:
        ui.warn(f"Swapfile not found: {SWAPFILE_PATH}")

    ui.header("Configuration Artifacts")
    if FSTAB_PATH.exists() and str(SWAPFILE_PATH) in FSTAB_PATH.read_text(encoding="utf-8"):
        ui.ok(f"{FSTAB_PATH}: hibernation swapfile entry present")
    else:
        ui.warn(f"{FSTAB_PATH}: hibernation swapfile entry absent")

    if MKINITCPIO_HIBERNATION_CONF.exists():
        ui.ok(f"Initramfs hook present: {MKINITCPIO_HIBERNATION_CONF}")
    else:
        ui.warn(f"Initramfs hook absent: {MKINITCPIO_HIBERNATION_CONF}")

    sd_entries = systemd_boot_entries()
    if sd_entries:
        configured = 0
        for entry in sd_entries:
            try:
                text = entry.read_text(encoding="utf-8")
                if "resume=UUID=" in text and "resume_offset=" in text:
                    configured += 1
            except PermissionError:
                ui.warn(f"Permission denied reading {entry}")
        ui.info(f"systemd-boot entries with resume params: {configured}/{len(sd_entries)}")

    if limine_detected():
        ui.info(f"Limine resume drop-in: {'present' if LIMINE_RESUME_CONF.exists() else 'absent'}")
        ui.info(f"Limine rtc-alarm drop-in: {'present' if LIMINE_RTC_CONF.exists() else 'absent'}")

    for hook in (SLEEP_HOOK_KBD, SLEEP_HOOK_FPRINTD):
        ui.info(f"Sleep hook {hook.name}: {'present' if hook.exists() else 'absent'}")

    for marker in (DUSKY_REBUILD_MARKER, OMARCHY_REBUILD_MARKER):
        if marker.exists():
            ui.warn(f"Pending initramfs rebuild marker present: {marker}")

    try:
        cmdline = Path("/proc/cmdline").read_text(encoding="utf-8").strip()
        if "resume=" in cmdline and "resume_offset=" in cmdline:
            ui.ok("Active kernel command line includes resume= and resume_offset=.")
        else:
            ui.warn("Active kernel command line does not yet include resume parameters.")
    except OSError:
        pass

    return 0


# ---------------------------------------------------------------------------
# CLI Argument Parser & Entrypoint
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="218_hibernation_setup.py",
        description="Configure, audit, or remove Btrfs swapfile hibernation on Arch Linux.",
    )
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument(
        "--enable",
        "--setup",
        dest="action",
        action="store_const",
        const="enable",
        help="Enable and configure Btrfs hibernation (default action).",
    )
    action_group.add_argument(
        "--disable",
        "--remove",
        dest="action",
        action="store_const",
        const="disable",
        help="Disable hibernation and cleanly tear down swapfile, hooks, and bootloader params.",
    )
    action_group.add_argument(
        "--check",
        "--status",
        dest="action",
        action="store_const",
        const="check",
        help="Run a read-only audit of hibernation readiness and configuration.",
    )
    parser.add_argument(
        "-s",
        "--size",
        type=str,
        default=None,
        help="Custom swapfile size (e.g., 32G, 16G, 24576M). Defaults to RAM + 2 GiB.",
    )
    parser.add_argument(
        "--no-rebuild",
        "--no-initramfs",
        "--no-init-gen",
        dest="no_rebuild",
        action="store_true",
        help="Skip mkinitcpio/limine-mkinitcpio rebuild and manage pending rebuild markers.",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Simulate operations without modifying disk or system configuration.",
    )
    parser.set_defaults(action="enable")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(raw_argv)

    if args.action in {"enable", "disable"} and not args.dry_run:
        ensure_root(raw_argv)

    if args.action == "enable":
        return setup_hibernation(args)
    if args.action == "disable":
        return remove_hibernation(args)
    if args.action == "check":
        return check_hibernation(args)

    parser.error(f"Unknown action: {args.action}")
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        raise SystemExit(130)
    except HibernationError as exc:
        ui.err(str(exc))
        raise SystemExit(1)
